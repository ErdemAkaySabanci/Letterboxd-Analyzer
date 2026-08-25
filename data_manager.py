"""
Letterboxd Profile Scraper & Data Manager
==========================================
Robust scraper with caching, rate-limiting, and user-agent spoofing.
Adapted for the 2025/2026 Letterboxd HTML structure and Cloudflare protection.
"""

import json
import os
import re
import time
from typing import Optional

import requests
import pandas as pd
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DOMAIN = "https://letterboxd.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
CACHE_FILE = os.path.join(os.path.dirname(__file__), "movies_cache.json")
REQUEST_DELAY = 1.5  # seconds between HTTP requests


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _load_cache() -> dict:
    """Load the local JSON cache, or return an empty dict."""
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cache(cache: dict) -> None:
    """Persist the cache dict to disk."""
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(url: str, session: Optional[requests.Session] = None) -> tuple[BeautifulSoup, int]:
    """
    Fetch a page with rate-limiting. Returns (soup, status_code).
    Does NOT raise on non-200 so callers can handle 403 gracefully.
    """
    s = session or requests.Session()
    for attempt in range(3):
        time.sleep(REQUEST_DELAY)
        try:
            resp = s.get(url, headers=HEADERS, timeout=15)
            return BeautifulSoup(resp.content, "html.parser"), resp.status_code
        except Exception as e:
            print(f"  [!] HTTP Error for {url} (Attempt {attempt+1}/3): {e}")
            if attempt == 2:
                return BeautifulSoup("", "html.parser"), 500
            time.sleep(2)
    return BeautifulSoup("", "html.parser"), 500


# ---------------------------------------------------------------------------
# Star-rating converter
# ---------------------------------------------------------------------------

_STAR_MAP = {
    "½": 0.5,
    "★": 1.0, "★½": 1.5,
    "★★": 2.0, "★★½": 2.5,
    "★★★": 3.0, "★★★½": 3.5,
    "★★★★": 4.0, "★★★★½": 4.5,
    "★★★★★": 5.0,
}


def _convert_star_rating(text: str):
    """Convert Letterboxd star-character string to a float, or None."""
    text = text.strip()
    return _STAR_MAP.get(text)


# ---------------------------------------------------------------------------
# Main scraper class
# ---------------------------------------------------------------------------

class LetterboxdScraper:
    """Scrape and cache Letterboxd profile data."""

    def __init__(self, username: str, use_cache: bool = True):
        self.username = username
        self.use_cache = use_cache
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.cache = _load_cache() if use_cache else {}
        self._progress_callback = None

    # ------------------------------------------------------------------
    # Progress reporting
    # ------------------------------------------------------------------

    def set_progress_callback(self, fn):
        """Set a callable(message: str) to receive progress updates."""
        self._progress_callback = fn

    def _report(self, msg: str):
        if self._progress_callback:
            self._progress_callback(msg)
        else:
            print(msg)

    # ------------------------------------------------------------------
    # 1. Scrape basic film list (title, rating, link)
    # ------------------------------------------------------------------

    def scrape_films_list(self) -> pd.DataFrame:
        """
        Return a DataFrame of all rated films on the user's profile.

        Letterboxd now uses Cloudflare protection on paginated URLs
        (/films/page/N), so we attempt all pages but gracefully fall
        back to just the first page if pagination is blocked.
        """
        base_url = f"{DOMAIN}/{self.username}/films/"
        self._report(f"Fetching films list for {self.username}...")

        # First page (usually unprotected)
        soup, status = _get(base_url, self.session)
        if status != 200:
            raise RuntimeError(f"Cannot access {base_url} (HTTP {status})")

        # Determine total pages
        page_links = soup.find_all("li", class_="paginate-page")
        num_pages = int(page_links[-1].find("a").get_text().strip()) if page_links else 1
        self._report(f"Found {num_pages} page(s) of films")

        all_records = []
        # Parse first page
        all_records.extend(self._parse_films_page(soup))

        # Attempt remaining pages
        for page_num in range(2, num_pages + 1):
            self._report(f"Scraping films page {page_num}/{num_pages}")
            page_soup, page_status = _get(f"{base_url}page/{page_num}/", self.session)
            if page_status != 200:
                self._report(f"  Page {page_num} blocked (HTTP {page_status}), skipping remaining pages")
                break
            all_records.extend(self._parse_films_page(page_soup))

        df = pd.DataFrame(all_records)
        # Drop unrated movies
        df = df.dropna(subset=["my_rating"]).reset_index(drop=True)
        self._report(f"Total rated movies scraped: {len(df)}")
        return df

    def _parse_films_page(self, soup: BeautifulSoup) -> list[dict]:
        """Extract movie records from a single films page."""
        records = []
        # Current (2025+) Letterboxd uses <ul class="grid -p70">
        grid = soup.find("ul", class_="grid")
        if grid is None:
            # Fallback: try legacy poster-list
            grid = soup.find("ul", class_="poster-list")
        if grid is None:
            return records

        for li in grid.find_all("li"):
            div = li.find("div")
            if not div:
                continue
            img = li.find("img")
            title = img.get("alt", "Unknown") if img else "Unknown"

            # Get link: prefer data-target-link, then data-item-link
            link = div.get("data-target-link") or div.get("data-item-link", "")
            if not link:
                continue

            # Movie ID: try data-film-id (legacy) then data-postered-identifier
            movie_id = (div.get("data-film-id")
                        or div.get("data-postered-identifier")
                        or div.get("data-item-slug", ""))

            # Rating
            rating_p = li.find("p", class_="poster-viewingdata")
            rating_text = rating_p.get_text().strip() if rating_p else ""
            rating = _convert_star_rating(rating_text)

            records.append({
                "movie_id": movie_id,
                "title_of_movie": title,
                "my_rating": rating,
                "link_of_movie": link,
            })
        return records

    # ------------------------------------------------------------------
    # 2. Scrape movie details (avg rating, genres, director, runtime)
    # ------------------------------------------------------------------

    def _scrape_movie_detail(self, link: str) -> dict:
        """Scrape or return cached details for a single movie link."""
        cache_key = link.strip("/").replace("https://", "").replace("http://", "")
        
        # Check cache, but force refetch if it's an old cache without 'actors'
        if self.use_cache and cache_key in self.cache:
            if "actors" in self.cache[cache_key]:
                return self.cache[cache_key]

        url = link if link.startswith("http") else f"{DOMAIN}{link}"
        soup, status = _get(url, self.session)

        detail = {
            "average_rating": None,
            "genres": [],
            "director": None,
            "actors": [],
            "release_year": None,
            "country": [],
            "language": [],
            "runtime_minutes": None,
            "watched_number": None,
        }

        if status != 200:
            self._report(f"  Detail page blocked (HTTP {status})")
            return detail

        # --- Average rating from LD+JSON or inline script ---
        for sc in soup.find_all("script"):
            text = sc.string or ""
            if "ratingValue" in text:
                m = re.search(r'"ratingValue":\s*([\d.]+)', text)
                if not m:
                    # Legacy format: ratingValue":3.5,
                    m = re.search(r'ratingValue["\s:]+(\d+\.?\d*)', text)
                if m:
                    try:
                        detail["average_rating"] = float(m.group(1))
                    except ValueError:
                        pass
                break

        # --- Genres (new: tab-panel-genres, legacy: tab-genres) ---
        genre_div = soup.find("div", id="tab-panel-genres") or soup.find("div", id="tab-genres")
        if genre_div:
            # Only take links that go to /films/genre/ (real genres, not tags)
            genre_links = genre_div.find_all("a", href=re.compile(r"/films/genre/"))
            if genre_links:
                detail["genres"] = [a.get_text().strip() for a in genre_links]
            else:
                # Fallback: take first div's links
                inner = genre_div.find("div")
                if inner:
                    detail["genres"] = [a.get_text().strip() for a in inner.find_all("a")]

        # --- Director (new: tab-panel-crew, legacy: tab-crew) ---
        crew_div = soup.find("div", id="tab-panel-crew") or soup.find("div", id="tab-crew")
        if crew_div:
            # Director links have href matching /director/
            director_links = crew_div.find_all("a", href=re.compile(r"/director/"))
            if director_links:
                detail["director"] = director_links[0].get_text().strip()
            else:
                inner = crew_div.find("div")
                if inner:
                    a = inner.find("a")
                    if a:
                        detail["director"] = a.get_text().strip()

        # Also try LD+JSON for director if not found
        if not detail["director"]:
            for sc in soup.find_all("script", type="application/ld+json"):
                if sc.string:
                    try:
                        ld = json.loads(sc.string)
                        if isinstance(ld, dict):
                            # Director
                            if "director" in ld:
                                dirs = ld["director"]
                                if isinstance(dirs, list) and dirs:
                                    detail["director"] = dirs[0].get("name")
                                elif isinstance(dirs, dict):
                                    detail["director"] = dirs.get("name")
                                    
                            # Actors
                            if "actor" in ld:
                                acts = ld["actor"]
                                if isinstance(acts, list):
                                    detail["actors"] = [a.get("name") for a in acts if isinstance(a, dict) and "name" in a]
                                    
                            # Release Year
                            if "dateCreated" in ld:
                                try:
                                    detail["release_year"] = int(str(ld["dateCreated"])[:4])
                                except (ValueError, TypeError):
                                    pass
                                    
                            # Country
                            if "countryOfOrigin" in ld:
                                countries = ld["countryOfOrigin"]
                                if isinstance(countries, list):
                                    detail["country"] = [c.get("name") for c in countries if isinstance(c, dict) and "name" in c]
                                    
                            # Language
                            if "inLanguage" in ld:
                                langs = ld["inLanguage"]
                                if isinstance(langs, list):
                                    detail["language"] = [l.get("name") for l in langs if isinstance(l, dict) and "name" in l]
                                elif isinstance(langs, str):
                                    detail["language"] = [langs]
                                    
                    except (json.JSONDecodeError, KeyError):
                        pass

        # --- Runtime ---
        # Try runTime in script (legacy)
        runtime_match = re.search(r"runTime:\s*(\d+)", str(soup))
        if runtime_match:
            detail["runtime_minutes"] = int(runtime_match.group(1))
        else:
            # Try ISO duration in LD+JSON
            dur_match = re.search(r'"duration":\s*"PT(\d+)M"', str(soup))
            if dur_match:
                detail["runtime_minutes"] = int(dur_match.group(1))
            else:
                # Try text like "145 mins"
                mins_match = re.search(r'(\d+)\s*min', str(soup))
                if mins_match:
                    detail["runtime_minutes"] = int(mins_match.group(1))

        # --- Watched number (popularity) ---
        # Members page is often blocked by Cloudflare, try but don't fail
        try:
            members_soup, m_status = _get(f"{url}members/", self.session)
            if m_status == 200:
                li_el = members_soup.find("li", class_="js-route-watches")
                if li_el:
                    a_tag = li_el.find("a")
                    if a_tag and a_tag.get("title"):
                        detail["watched_number"] = int("".join(filter(str.isdigit, a_tag["title"])))
        except Exception:
            pass

        # If members page blocked, try to get watched count from LD+JSON or main page
        if detail["watched_number"] is None:
            for sc in soup.find_all("script"):
                text = sc.string or ""
                m = re.search(r'"ratingCount":\s*(\d+)', text)
                if m:
                    # ratingCount is not exactly watched count but a reasonable proxy
                    detail["watched_number"] = int(m.group(1))
                    break

        # Cache the result only if we didn't fail
        if self.use_cache and status == 200:
            self.cache[cache_key] = detail
            _save_cache(self.cache)

        return detail

    def enrich_dataset(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add average_rating, genre, director, runtime, watched_number columns."""
        details = []
        total = len(df)
        for idx, row in df.iterrows():
            self._report(f"[{idx + 1}/{total}] Enriching: {row['title_of_movie']}")
            d = self._scrape_movie_detail(row["link_of_movie"])
            details.append(d)

        detail_df = pd.DataFrame(details)
        # Rename to match column conventions
        detail_df.rename(columns={
            "genres": "genre_of_movie",
            "director": "Director",
            "actors": "Actors",
            "release_year": "Release_Year",
            "country": "Country",
            "language": "Language",
            "runtime_minutes": "Runtime_minutes",
            "watched_number": "Watched_number",
        }, inplace=True)

        enriched = pd.concat([df.reset_index(drop=True), detail_df.reset_index(drop=True)], axis=1)
        return enriched

    # ------------------------------------------------------------------
    # 3. Full pipeline convenience method
    # ------------------------------------------------------------------

    def scrape_full(self) -> pd.DataFrame:
        """Scrape films list then enrich every movie. Returns full DataFrame."""
        df = self.scrape_films_list()
        df = self.enrich_dataset(df)
        return df


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    username = sys.argv[1] if len(sys.argv) > 1 else "Erdemstein"
    scraper = LetterboxdScraper(username)
    dataset = scraper.scrape_full()
    out_path = os.path.join(os.path.dirname(__file__), "my_movie_dataset.csv")
    dataset.to_csv(out_path, index=False, encoding="utf-8")
    print(f"\nDone! {len(dataset)} movies saved to {out_path}")
    print(dataset.head(10).to_string())
