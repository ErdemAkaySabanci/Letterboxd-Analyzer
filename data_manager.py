"""
Letterboxd Film Metadata Scraper
=================================
Fetches per-film metadata (director, cast, genres, runtime, community rating,
countries, languages, poster) from Letterboxd film pages.

The film list itself comes from the user's export ZIP — we never scrape the
profile pages, which are Cloudflare-protected past page 1.

Everything is read from the page's schema.org LD+JSON block, which Letterboxd
wraps in a CDATA comment that must be stripped before parsing.

Usage:
    py -3.12 data_manager.py              # backfill my_movie_dataset.csv
    py -3.12 data_manager.py --all        # include unrated films too
"""

import json
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DOMAIN = "https://letterboxd.com"
CACHE_FILE = os.path.join(os.path.dirname(__file__), "film_cache.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

MAX_WORKERS = int(os.getenv("SCRAPE_WORKERS", "16"))  # measured locally: no
# throttling at 24, gains flatten after 16. On a CPU-limited host (e.g. a
# free-tier container with a fraction of a core) that many threads mostly
# fight each other for the GIL — set SCRAPE_WORKERS lower there.
MAX_ACTORS = 20       # LD+JSON lists ~50 in billing order; leads are enough
RETRIES = 3

_thread_local = threading.local()
_cache_lock = threading.Lock()

# Caps concurrent outbound requests across *all* sessions. Without it, five
# simultaneous uploads mean 80 parallel hits on Letterboxd and an IP ban that
# breaks the app for everyone. Per-session pools still spawn their threads;
# they just queue here.
_request_slots = threading.BoundedSemaphore(MAX_WORKERS)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def load_cache() -> dict:
    """Load the film cache, keyed by the film's Letterboxd URI."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_cache(cache: dict) -> None:
    """Persist the cache atomically so an interrupted run can't corrupt it."""
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CACHE_FILE)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _session() -> requests.Session:
    """One Session per worker thread, for connection pooling."""
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers.update(HEADERS)
        _thread_local.session = s
    return s


# ---------------------------------------------------------------------------
# LD+JSON extraction
# ---------------------------------------------------------------------------

# Letterboxd emits: /* <![CDATA[ */ {...} /* ]]> */
_CDATA_OPEN = re.compile(r"^\s*/\*\s*<!\[CDATA\[\s*\*/")
_CDATA_CLOSE = re.compile(r"/\*\s*\]\]>\s*\*/\s*$")


def _parse_ld_json(soup: BeautifulSoup) -> dict:
    """Return the film's schema.org payload, or {} if absent/unparsable."""
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        if not raw:
            continue
        raw = _CDATA_CLOSE.sub("", _CDATA_OPEN.sub("", raw.strip())).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") in ("Movie", "TVSeries", None):
            return data
    return {}


def _names(value) -> list[str]:
    """Pull 'name' fields out of a schema.org person/place list."""
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    return [v["name"] for v in value if isinstance(v, dict) and v.get("name")]


def _runtime_minutes(ld: dict, soup: BeautifulSoup) -> int | None:
    """Runtime from ISO-8601 duration, falling back to the '123 mins' text."""
    duration = ld.get("duration")
    if isinstance(duration, str):
        m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", duration)
        if m and (m.group(1) or m.group(2)):
            return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)

    m = re.search(r"(\d+)\s*mins", soup.get_text(" ", strip=True))
    return int(m.group(1)) if m else None


def _detail_tab_links(soup: BeautifulSoup, kind: str) -> list[str]:
    """Read the film's details tab (used as a fallback for country/language)."""
    tab = soup.find("div", id="tab-panel-details") or soup.find("div", id="tab-details")
    if not tab:
        return []
    links = tab.find_all("a", href=re.compile(rf"/films/{kind}/"))
    return sorted({a.get_text(strip=True) for a in links if a.get_text(strip=True)})


# ---------------------------------------------------------------------------
# Single-film scrape
# ---------------------------------------------------------------------------

def scrape_film(url: str) -> dict | None:
    """
    Scrape one film page. Returns a metadata dict, or None if the page could
    not be fetched (so callers can tell 'failed' from 'genuinely has no data').
    """
    if not url.startswith("http"):
        url = f"{DOMAIN}{url}"

    response = None
    for attempt in range(RETRIES):
        try:
            response = _session().get(url, timeout=20)
            if response.status_code == 200:
                break
            # Backed-off retry on rate limiting / transient blocks
            if response.status_code in (403, 429, 500, 502, 503):
                time.sleep(2 * (attempt + 1))
                continue
            return None
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
    if response is None or response.status_code != 200:
        return None

    soup = BeautifulSoup(response.content, "html.parser")
    ld = _parse_ld_json(soup)
    rating = ld.get("aggregateRating") or {}

    directors = _names(ld.get("director"))
    countries = _names(ld.get("countryOfOrigin")) or _detail_tab_links(soup, "country")
    languages = _names(ld.get("inLanguage")) or _detail_tab_links(soup, "language")

    genres = ld.get("genre") or []
    if isinstance(genres, str):
        genres = [genres]

    release_year = None
    created = ld.get("dateCreated") or ""
    if isinstance(created, str) and len(created) >= 4 and created[:4].isdigit():
        release_year = int(created[:4])

    poster = ""
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        poster = og["content"]

    return {
        "slug": (ld.get("url") or response.url).replace(DOMAIN, "").strip("/"),
        "title": ld.get("name"),
        "director": directors[0] if directors else None,
        "directors": directors,
        "actors": _names(ld.get("actor"))[:MAX_ACTORS],
        "genres": genres,
        "countries": countries,
        "languages": languages,
        "runtime_minutes": _runtime_minutes(ld, soup),
        "average_rating": rating.get("ratingValue"),
        "rating_count": rating.get("ratingCount"),
        "release_year": release_year,
        "poster": poster,
    }


# ---------------------------------------------------------------------------
# Bulk scrape
# ---------------------------------------------------------------------------

def scrape_films(urls, progress_cb=None, max_workers: int = MAX_WORKERS) -> dict:
    """
    Scrape every URL not already cached, in parallel, and return the full
    cache dict (keyed by URL). The cache is saved as results arrive, so an
    interrupted run keeps its progress.

    progress_cb(done, total, title) is called after each film.
    """
    cache = load_cache()
    todo = [u for u in dict.fromkeys(urls) if u and u not in cache]

    total = len(todo)
    if total == 0:
        return cache

    done = 0
    failures = []

    def work(url):
        nonlocal done
        with _request_slots:
            data = scrape_film(url)
        with _cache_lock:
            if data is None:
                failures.append(url)
            else:
                cache[url] = data
            done += 1
            # Checkpoint periodically rather than on every film
            if done % 25 == 0:
                save_cache(cache)
        if progress_cb:
            progress_cb(done, total, (data or {}).get("title") or url)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        list(pool.map(work, todo))

    save_cache(cache)

    if failures:
        cache["__failures__"] = failures
    return cache


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------
#
# Letterboxd keeps a portrait and a biography on every director and actor
# page. Both are useful and neither is in the export, so they are scraped and
# cached the same way films are: one shared file, lock-guarded, seeded into
# the deploy image.

PEOPLE_CACHE_FILE = os.path.join(os.path.dirname(__file__), "people_cache.json")
_people_lock = threading.Lock()


def load_people_cache() -> dict:
    """Load the people cache, keyed by "<kind>:<slug>"."""
    if os.path.exists(PEOPLE_CACHE_FILE):
        try:
            with open(PEOPLE_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_people_cache(cache: dict) -> None:
    """Persist atomically, like the film cache."""
    tmp = PEOPLE_CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PEOPLE_CACHE_FILE)


def person_slug(name: str) -> str:
    """Letterboxd's person slug: unaccented, lowercased, punctuation dropped."""
    plain = unicodedata.normalize("NFKD", name or "")
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


def scrape_person(name: str, kind: str = "director") -> dict | None:
    """
    One person page: the portrait and biography Letterboxd holds for them.

    Nothing in the export names a person's page, so this is the one place in
    this codebase that builds a slug from a name. It is proved rather than
    trusted: a bad guess 404s, and a slug that happens to resolve to somebody
    else fails the og:title check below. A mismatch therefore yields no card
    rather than the wrong face.

    None means the page could not be fetched at all. A confirmed "no such
    person" comes back as a record with found=False, so it is cached once
    instead of being retried on every request.
    """
    slug = person_slug(name)
    if not slug:
        return {"name": name, "kind": kind, "found": False}

    url = f"{DOMAIN}/{kind}/{slug}/"
    response = None
    for attempt in range(RETRIES):
        try:
            response = _session().get(url, timeout=20)
            if response.status_code == 200:
                break
            if response.status_code == 404:
                return {"name": name, "kind": kind, "slug": slug, "found": False}
            if response.status_code in (403, 429, 500, 502, 503):
                time.sleep(2 * (attempt + 1))
                continue
            return None
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
    if response is None or response.status_code != 200:
        return None

    soup = BeautifulSoup(response.content, "html.parser")

    # "Films directed by Christopher Nolan" / "Films starring Brad Pitt".
    og = soup.find("meta", property="og:title")
    heading = (og.get("content") if og else "") or ""
    if name.lower() not in heading.lower():
        return {"name": name, "kind": kind, "slug": slug, "found": False}

    # The portrait is lazy-loaded, so the URL lives in data-image, not src.
    img = soup.select_one("img.js-tmdb-person[data-image]")
    bio = soup.select_one(".js-tmdb-bio p")

    return {
        "name": name,
        "kind": kind,
        "slug": slug,
        "url": url,
        "portrait": img["data-image"] if img else None,
        "tmdb_id": img.get("data-tmdb-id") if img else None,
        "bio": bio.get_text(" ", strip=True) if bio else None,
        "found": True,
    }


def scrape_people(people, max_workers: int = 8) -> dict:
    """
    Fetch every (name, kind) pair not already cached, and return the cache.

    Sixteen people is a couple of seconds of work, so there is no progress
    stream here the way there is for films; the caller just waits. The shared
    request semaphore still applies, so this cannot flood Letterboxd even
    when several sessions ask at once.
    """
    cache = load_people_cache()
    todo = [(n, k) for n, k in dict.fromkeys(people)
            if n and f"{k}:{person_slug(n)}" not in cache]
    if not todo:
        return cache

    def work(item):
        name, kind = item
        with _request_slots:
            data = scrape_person(name, kind)
        if data is None:
            return                    # transient failure: retry next request
        with _people_lock:
            cache[f"{kind}:{person_slug(name)}"] = data

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        list(pool.map(work, todo))

    save_people_cache(cache)
    return cache


def person_record(cache: dict, name: str, kind: str) -> dict | None:
    """The cached page for one person, or None if they were never confirmed."""
    entry = cache.get(f"{kind}:{person_slug(name)}")
    return entry if entry and entry.get("found") else None


# ---------------------------------------------------------------------------
# CLI backfill
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
    import pandas as pd

    include_unrated = "--all" in sys.argv
    csv_path = os.path.join(os.path.dirname(__file__), "my_movie_dataset.csv")
    if not os.path.exists(csv_path):
        print(f"Dataset not found at {csv_path}")
        raise SystemExit(1)

    df = pd.read_csv(csv_path)
    if not include_unrated:
        df = df[df["my_rating"].notna()]
        print(f"Rated films only: {len(df)} (pass --all to include unrated)")

    urls = df["link_of_movie"].dropna().tolist()
    cached = load_cache()
    pending = len([u for u in set(urls) if u not in cached])
    print(f"{len(set(urls))} unique films | {pending} to scrape | {len(set(urls)) - pending} cached")

    if pending == 0:
        print("Nothing to do.")
        raise SystemExit(0)

    started = time.time()

    def report(done, total, title):
        elapsed = time.time() - started
        rate = done / elapsed if elapsed else 0
        eta = (total - done) / rate if rate else 0
        print(f"  [{done:>4}/{total}] {str(title)[:48]:50s} ETA {eta:5.0f}s", flush=True)

    result = scrape_films(urls, progress_cb=report)
    failures = result.pop("__failures__", [])

    elapsed = time.time() - started
    print(f"\nDone in {elapsed:.0f}s ({elapsed / max(pending, 1):.2f}s per film)")
    print(f"Cached films: {len(result)}")
    if failures:
        print(f"Failed: {len(failures)}")
        for url in failures[:10]:
            print(f"  {url}")
