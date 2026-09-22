"""
Letterboxd Analysis – FastAPI Server
=====================================
Serves the dashboard and exposes API endpoints for uploads, statistics,
and recommendations.

Every upload gets its own session id, so visitors never share a dataset.
Uploads return immediately with the stats derivable from the ZIP alone; the
film metadata scrape runs in the background and its progress is streamed.

Usage:
    py -3.12 server.py
"""

import asyncio
import io
import json
import mimetypes
import os
import random
import re
import threading
import time
import traceback
import urllib.parse
import zipfile

import numpy as np
import pandas as pd
import requests
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (FileResponse, JSONResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles

# Fix Windows registry MIME type issues
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('application/javascript', '.js')

import sessions
import taste
from analyzer import (films_matching, full_analysis, instant_summary,
                      most_watched_people, taste_facts, wrapped_summary)
from data_manager import CACHE_FILE
from data_manager import (load_cache as load_film_cache, person_record,
                          scrape_films, scrape_people)
from quiz import build_full_quiz, build_instant_quiz

# ---------------------------------------------------------------------------
# Paths & app
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_DIR = os.path.join(BASE_DIR, "dashboard")

app = FastAPI(title="Letterboxd Profile Analysis")

# In production set ALLOWED_ORIGINS to a comma-separated list of your own
# origins; unset (local development) keeps the permissive default.
_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["*"], allow_headers=["*"])

# Upload guards. A Letterboxd export is well under a megabyte, so anything
# near these limits is either a mistake or an attack — a zip bomb expands to
# gigabytes from a few hundred kilobytes.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024          # 10 MB compressed
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024   # 200 MB expanded

# Uploads are the only expensive anonymous action: each one parses a ZIP,
# writes a session file and queues a scrape. One process (WEB_CONCURRENCY=1)
# means an in-process table is the whole story — there is nothing to
# coordinate across workers, so no Redis.
UPLOADS_PER_HOUR = int(os.getenv("UPLOADS_PER_HOUR", "12"))

# Taste profiles are the one request that leaves for a paid-for-by-quota
# service, so they get a global ceiling as well: the free tier allows ~1,500
# a day, and one viral afternoon must degrade to "no paragraph", not to a
# blocked key. Counted per process, like the upload limit.
PROFILES_PER_HOUR = int(os.getenv("PROFILES_PER_HOUR", "60"))


def _failed(what: str, status: int = 500) -> JSONResponse:
    """Log the real cause, hand the caller a sentence they can act on.

    The traceback goes to the host's log where it is useful; `str(e)` in the
    response body only ever showed a visitor a Python exception.
    """
    traceback.print_exc()
    return JSONResponse(status_code=status,
                        content={"error": f"Could not {what}. Try reloading the page."})


def clean_nans(obj):
    """Replace NaN/Inf with None so the result is valid JSON."""
    if isinstance(obj, dict):
        return {k: clean_nans(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_nans(v) for v in obj]
    if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
        return None
    return obj


# ---------------------------------------------------------------------------
# Background scrape jobs, keyed by session id
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Memoised reads
# ---------------------------------------------------------------------------

# The film cache used to be re-parsed from disk on every analysis request. At
# a few hundred films that is invisible; at tens of thousands it is a full
# JSON parse per request, and N concurrent requests each hold their own copy
# of the result. Keyed on (mtime, size), so a scrape checkpoint invalidates it
# and the next reader picks the new file up.
_cache_memo: tuple[tuple, dict] | None = None
_cache_memo_lock = threading.Lock()

# Per-session analysis, keyed on the same cache stamp so it refreshes exactly
# when the underlying metadata does.
_stats_memo: dict[str, tuple[tuple, dict]] = {}
_stats_memo_lock = threading.Lock()
_STATS_MEMO_MAX = 32


def _cache_stamp() -> tuple:
    try:
        st = os.stat(CACHE_FILE)
        return (st.st_mtime, st.st_size)
    except OSError:
        return (0.0, 0)


def cached_film_cache() -> dict:
    """The shared film cache, re-parsed only when the file changes.

    The returned dict is shared between requests — read it, never mutate it.
    Scrapers keep using `load_film_cache()` for their own private copy.
    """
    global _cache_memo
    stamp = _cache_stamp()
    with _cache_memo_lock:
        if _cache_memo and _cache_memo[0] == stamp:
            return _cache_memo[1]

    # Parsed outside the lock: a slow read must not block every other reader,
    # and two threads racing here costs one duplicated parse, nothing worse.
    fresh = load_film_cache()
    with _cache_memo_lock:
        _cache_memo = (stamp, fresh)
    return fresh


_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

# One entry per upload used to live here for the lifetime of the process.
# _job_state() reports "done" for anything it no longer holds, so dropping a
# finished job is safe: a late poll simply learns the scrape is over.
_JOBS_MAX = 200


def _forget_job(session_id: str) -> None:
    with _jobs_lock:
        _jobs.pop(session_id, None)


def _start_scrape(session_id: str, links: list[str]) -> None:
    """Kick off a background metadata scrape for one session's films."""
    cache = cached_film_cache()
    pending = [link for link in dict.fromkeys(links) if link and link not in cache]

    with _jobs_lock:
        while len(_jobs) >= _JOBS_MAX:
            _jobs.pop(next(iter(_jobs)))
        _jobs[session_id] = {
            "status": "done" if not pending else "running",
            "done": 0,
            "total": len(pending),
            "title": None,
        }
    if not pending:
        return

    def progress(done, total, title):
        with _jobs_lock:
            job = _jobs.get(session_id)
            if job:
                job.update(done=done, total=total, title=title)

    def run():
        try:
            scrape_films(pending, progress_cb=progress)
            status = "done"
        except Exception:
            traceback.print_exc()
            status = "error"
        with _jobs_lock:
            job = _jobs.get(session_id)
            if job:
                job["status"] = status

    threading.Thread(target=run, daemon=True).start()


def _job_state(session_id: str) -> dict:
    with _jobs_lock:
        job = _jobs.get(session_id)
        return dict(job) if job else {"status": "done", "done": 0, "total": 0, "title": None}


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------

def enrich_with_cache(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fill metadata columns from the scraped film cache.

    Matching is on the film's Letterboxd URI, which comes straight from the
    export ZIP — an exact key, unlike guessing a slug from the title (which
    misses every film whose slug is disambiguated, e.g. doctor-strange-2016).
    """
    fields = {
        "Director": ("director", None),
        "Actors": ("actors", []),
        "Country": ("countries", []),
        "Language": ("languages", []),
        "Runtime_minutes": ("runtime_minutes", None),
        "genre_of_movie": ("genres", []),
        "average_rating": ("average_rating", None),
        "Watched_number": ("rating_count", None),
        "poster": ("poster", None),
    }

    cache = cached_film_cache()
    records = [cache.get(link) or {} for link in df["link_of_movie"]] if cache else [{}] * len(df)
    for col, (key, default) in fields.items():
        df[col] = [rec.get(key, default) if rec else default for rec in records]

    return df


def load_dataset(session_id: str) -> pd.DataFrame | None:
    """Load a session's films, enriched with whatever the cache holds now."""
    df = sessions.load(session_id)
    if df is None:
        return None
    return enrich_with_cache(df)


def _require(session_id: str):
    """Return (df, None) or (None, error response)."""
    if not session_id:
        return None, JSONResponse(status_code=400, content={"error": "No session. Upload your export again."})
    df = load_dataset(session_id)
    if df is None:
        return None, JSONResponse(
            status_code=404,
            content={"error": "That session has expired. Upload your export again."})
    return df, None


# ---------------------------------------------------------------------------
# ZIP parsing
# ---------------------------------------------------------------------------

def _parse_letterboxd_zip(zip_bytes: bytes) -> pd.DataFrame:
    """Parse a Letterboxd export ZIP into a unified DataFrame."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        expanded = sum(info.file_size for info in zf.infolist())
        if expanded > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("That ZIP unpacks to far more than a Letterboxd export should.")

        names = zf.namelist()

        watched_path = next((n for n in names if n.endswith('watched.csv')), None)
        ratings_path = next((n for n in names if n.endswith('ratings.csv')), None)

        if not watched_path:
            raise ValueError(
                "No watched.csv inside that ZIP. Upload the export exactly as "
                "Letterboxd gave it to you, without unzipping it first.")

        watched = pd.read_csv(zf.open(watched_path))

        # Ratings are optional — not everyone rates what they watch.
        if ratings_path:
            ratings = pd.read_csv(zf.open(ratings_path))
            df = watched.merge(
                ratings[['Letterboxd URI', 'Rating']], on='Letterboxd URI', how='left'
            )
        else:
            df = watched.copy()
            df['Rating'] = None

        df = df.rename(columns={
            'Name': 'title_of_movie',
            'Rating': 'my_rating',
            'Letterboxd URI': 'link_of_movie',
            'Date': 'Watch_Date',
            'Year': 'Release_Year',
        })

        df['movie_id'] = df['link_of_movie'].apply(
            lambda x: x.split('/')[-1] if isinstance(x, str) and '/' in x else None
        )

        return df


# ---------------------------------------------------------------------------
# Upload & progress
# ---------------------------------------------------------------------------

_uploads: dict[str, list[float]] = {}
_uploads_lock = threading.Lock()
_last_purge = 0.0


def _client_ip(request: Request) -> str:
    """The visitor's address, as seen from behind the host's proxy."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limited(ip: str) -> bool:
    """True once an address has had its share of uploads for the hour."""
    now = time.time()
    cutoff = now - 3600
    with _uploads_lock:
        if len(_uploads) > 5000:
            for addr in [a for a, seen in _uploads.items() if not seen or seen[-1] < cutoff]:
                _uploads.pop(addr, None)
        recent = [t for t in _uploads.get(ip, ()) if t > cutoff]
        if len(recent) >= UPLOADS_PER_HOUR:
            _uploads[ip] = recent
            return True
        recent.append(now)
        _uploads[ip] = recent
        return False


_profiles: list[float] = []
_profile_inflight: set[str] = set()
_profile_attempts: dict[str, int] = {}
_profiles_lock = threading.Lock()

# A session that failed twice stops asking. The first miss is usually the
# per-minute quota and the retry on the next refresh lands; a second miss
# means the key or the model is wrong, and hammering will not fix that.
_PROFILE_MAX_ATTEMPTS = 2


def _profile_slot() -> bool:
    """Claim one of this hour's profile generations, or False if none is left.

    Called with _profiles_lock held.
    """
    now = time.time()
    _profiles[:] = [t for t in _profiles if t > now - 3600]
    if len(_profiles) >= PROFILES_PER_HOUR:
        return False
    _profiles.append(now)
    return True


def _purge_occasionally() -> None:
    """Expire old session files from inside the request path.

    purge_expired() used to run only at startup, so on a long-lived process
    nothing ever aged out. Uploads are the only thing that creates session
    files, which makes them the natural place to clean them up.
    """
    global _last_purge
    now = time.time()
    if now - _last_purge < 3600:
        return
    _last_purge = now
    try:
        removed = sessions.purge_expired()
        if removed:
            print(f"Purged {removed} expired session(s)")
    except Exception:
        traceback.print_exc()


async def _read_limited(file: UploadFile) -> bytes:
    """Read an upload into memory, refusing anything over MAX_UPLOAD_BYTES."""
    chunks, total = [], 0
    while chunk := await file.read(1 << 20):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise ValueError(
                f"That file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB. "
                "A Letterboxd export is well under one.")
        chunks.append(chunk)
    return b"".join(chunks)


@app.post("/api/upload-zip")
async def upload_zip(request: Request, file: UploadFile = File(...)):
    """
    Parse an export ZIP, open a session, and return the stats available with
    no scraping at all. The metadata scrape starts in the background.
    """
    if _rate_limited(_client_ip(request)):
        return JSONResponse(
            status_code=429,
            content={"error": "That is a lot of uploads from one place. Try again in an hour."})

    _purge_occasionally()

    try:
        df = _parse_letterboxd_zip(await _read_limited(file))
        if df.empty:
            return JSONResponse(
                status_code=400,
                content={"error": "That export has no films in it yet."})

        session_id = sessions.create(df)
        _start_scrape(session_id, df["link_of_movie"].dropna().tolist())

        return clean_nans({
            "status": "success",
            "session_id": session_id,
            "instant": instant_summary(df),
            "scrape": _job_state(session_id),
        })
    except ValueError as e:
        # Raised deliberately above, already carrying a written message.
        return JSONResponse(status_code=400, content={"error": str(e)})
    except zipfile.BadZipFile:
        return JSONResponse(
            status_code=400,
            content={"error": "That file is not a ZIP. Upload the export exactly as "
                              "Letterboxd gave it to you."})
    except (KeyError, pd.errors.ParserError, pd.errors.EmptyDataError):
        # The ZIP opened and held a watched.csv, but not one shaped like an
        # export — a hand-edited or unrelated CSV lands here.
        traceback.print_exc()
        return JSONResponse(
            status_code=400,
            content={"error": "That does not look like a Letterboxd export. Download a "
                              "fresh one from Settings, Import & Export."})
    except Exception:
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"error": "Something went wrong reading that file. Try again."})


@app.get("/api/demo")
async def get_demo():
    """
    The session id behind the landing page's "example result".

    Only the id: the client then walks the ordinary session endpoints, so the
    demo exercises the same code every upload does. Handing it out here rather
    than hardcoding it in app.js keeps one source of truth, and means a build
    shipped without the CSV fails as a 404 the button can report instead of a
    blank result page.
    """
    if not os.path.exists(sessions.DEMO_FILE):
        return JSONResponse(status_code=404, content={"error": "No example is available."})
    return {"session_id": sessions.DEMO_SESSION_ID}


@app.get("/api/progress")
async def progress_stream(session: str = ""):
    """Server-sent events reporting background scrape progress."""
    if not sessions.exists(session):
        return JSONResponse(status_code=404, content={"error": "No such session."})

    async def events():
        try:
            while True:
                state = _job_state(session)
                yield f"data: {json.dumps(state)}\n\n"
                if state["status"] in ("done", "error"):
                    break
                await asyncio.sleep(0.5)
        finally:
            # Only once the scrape has actually finished. A reader who closes
            # the tab mid-scrape must still find their job on the next poll.
            if _job_state(session)["status"] in ("done", "error"):
                _forget_job(session)

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/api/status")
async def get_status(session: str = ""):
    """Session existence plus scrape progress, for clients that prefer polling."""
    df = load_dataset(session) if session else None
    return {
        "session": session or None,
        "exists": df is not None,
        "rows": len(df) if df is not None else 0,
        "scrape": _job_state(session) if session else None,
    }


# ---------------------------------------------------------------------------
# Analysis endpoints
# ---------------------------------------------------------------------------

@app.get("/api/instant")
async def get_instant(session: str = ""):
    """Stats that need no scraped metadata — available the moment ZIP lands."""
    df, error = _require(session)
    if error:
        return error
    return clean_nans(instant_summary(df))


@app.get("/api/posters")
async def get_posters(n: int = 40, session: str = ""):
    """
    Poster URLs for the decorative walls.

    With a session these are the visitor's own films: the quiz plays its reel
    behind the library being asked about, not a stranger's. The shared cache
    tops the list up when their own scrape has not caught up yet, and is the
    whole answer for the landing wall, which runs before any upload exists.
    """
    urls: list[str] = []
    if session:
        df = load_dataset(session)
        if df is not None and "poster" in df.columns:
            urls = [u for u in df["poster"].tolist() if isinstance(u, str) and u]
            random.shuffle(urls)

    if len(urls) < n:
        cache = cached_film_cache()
        shared = [film["poster"] for film in cache.values()
                  if isinstance(film, dict) and film.get("poster")]
        random.shuffle(shared)
        seen = set(urls)
        urls += [u for u in shared if u not in seen]

    return {"posters": urls[:max(0, min(n, 120))]}


@app.get("/api/people")
async def get_people(session: str = "", n: int = 8):
    """
    Portraits and biographies for the people this library watches most.

    Kept out of /api/stats because it is the one analysis call that leaves
    the machine: the chapter renders from the stats straight away and the
    faces arrive when they arrive. Scraping runs in a worker thread so a cold
    cache does not block the event loop for everyone else.
    """
    df, error = _require(session)
    if error:
        return error
    try:
        n = max(1, min(n, 12))
        watched = most_watched_people(df)
        directors = (watched.get("directors") or [])[:n]
        actors = (watched.get("actors") or [])[:n]

        wanted = [(d["name"], "director") for d in directors if d.get("name")]
        wanted += [(a["name"], "actor") for a in actors if a.get("name")]

        cache = await asyncio.to_thread(scrape_people, wanted)

        def dress(rows, kind):
            out = []
            for row in rows:
                found = person_record(cache, row.get("name") or "", kind) or {}
                out.append({
                    **row,
                    "portrait": found.get("portrait"),
                    "bio": found.get("bio"),
                    "url": found.get("url"),
                })
            return out

        return clean_nans({
            "directors": dress(directors, "director"),
            "actors": dress(actors, "actor"),
        })
    except Exception:
        return _failed("load those portraits")


@app.get("/api/quiz")
async def get_quiz(session: str = "", phase: str = "instant", seed: int | None = None):
    """
    Quiz questions built from the user's own library.

    phase=instant works the moment the ZIP lands; phase=full needs the
    metadata scrape and returns fewer questions until it finishes.
    """
    df, error = _require(session)
    if error:
        return error
    if phase not in ("instant", "full"):
        return JSONResponse(status_code=400, content={"error": "phase must be 'instant' or 'full'."})
    try:
        build = build_instant_quiz if phase == "instant" else build_full_quiz
        return clean_nans({"phase": phase, "questions": build(df, seed=seed)})
    except Exception:
        return _failed("build your questions")


@app.get("/api/wrapped")
async def get_wrapped(session: str = ""):
    """Full Wrapped summary, including everything the scrape has filled in."""
    df, error = _require(session)
    if error:
        return error
    try:
        result = await enrich_wrapped_with_posters(wrapped_summary(df))
        return clean_nans(result)
    except Exception:
        return _failed("build your summary")


@app.get("/api/stats")
async def get_stats(session: str = ""):
    """Full statistical analysis of the session's dataset.

    The result is memoised against the film cache's stamp: the chapters are
    re-fetched whenever the scrape lands, and on a large library this is
    seconds of pandas work that would otherwise repeat on every request.
    """
    df, error = _require(session)
    if error:
        return error

    stamp = _cache_stamp()
    with _stats_memo_lock:
        hit = _stats_memo.get(session)
        if hit and hit[0] == stamp:
            return hit[1]

    try:
        result = clean_nans(full_analysis(df))
    except Exception:
        return _failed("build your analysis")

    with _stats_memo_lock:
        while len(_stats_memo) >= _STATS_MEMO_MAX:
            _stats_memo.pop(next(iter(_stats_memo)))
        _stats_memo[session] = (stamp, result)
    return result


@app.get("/api/profile")
async def get_profile(session: str = ""):
    """
    The written taste profile, generated once per session and then kept.

    Always answers with a status the page can act on without a spinner:
    `disabled` (no key configured), `pending` (the scrape is still running or
    another request is already writing it), `ready` with the text, or
    `unavailable` (quota, network, or a reply not worth showing). None of
    these is an error: the block is simply hidden until there is a paragraph.
    """
    df, error = _require(session)
    if error:
        return error
    if not taste.ENABLED:
        return {"status": "disabled"}

    saved = sessions.load_profile(session)
    if saved and saved.get("text"):
        return {"status": "ready", "text": saved["text"]}

    # Written from the whole library or not at all: a half-scraped one would
    # name the wrong most-watched director, and the answer is kept for good.
    if _job_state(session)["status"] == "running":
        return {"status": "pending"}

    with _profiles_lock:
        if session in _profile_inflight:
            return {"status": "pending"}
        if _profile_attempts.get(session, 0) >= _PROFILE_MAX_ATTEMPTS:
            return {"status": "unavailable"}
        # A full hour is not this session's fault: leave its attempts alone
        # so the next visit, once the window has moved on, still gets a try.
        if not _profile_slot():
            return {"status": "unavailable"}
        if len(_profile_attempts) > 1000:
            _profile_attempts.clear()
        _profile_attempts[session] = _profile_attempts.get(session, 0) + 1
        _profile_inflight.add(session)

    try:
        facts = clean_nans(taste_facts(df))
        text = await asyncio.to_thread(taste.write_profile, facts)
    except taste.ProfileUnavailable:
        return {"status": "unavailable"}
    except Exception:
        traceback.print_exc()
        return {"status": "unavailable"}
    finally:
        with _profiles_lock:
            _profile_inflight.discard(session)

    try:
        sessions.save_profile(session, {"text": text, "model": taste.MODEL, "written": time.time()})
    except OSError:
        traceback.print_exc()          # still worth showing this once
    return {"status": "ready", "text": text}


@app.get("/api/films")
async def get_films(session: str = "", director: str = "", actor: str = "",
                    genre: str = "", country: str = "", language: str = "",
                    decade: int | None = None, rating: float | None = None,
                    limit: int = 60):
    """
    The films behind a chapter row — the drill-down a click opens.

    Filters are the labels the chapters already render (a director's name, a
    genre, a decade's first year), so the client passes back what it displayed.
    They combine with AND; with no filter this is the whole library.
    """
    df, error = _require(session)
    if error:
        return error
    try:
        return clean_nans(films_matching(
            df,
            director=director or None,
            actor=actor or None,
            genre=genre or None,
            country=country or None,
            language=language or None,
            decade=decade,
            rating=rating,
            limit=max(1, min(limit, 300)),
        ))
    except Exception:
        return _failed("load those films")


# ---------------------------------------------------------------------------
# Posters
# ---------------------------------------------------------------------------

async def fetch_poster(url: str) -> str:
    """Fetch the og:image poster for a Letterboxd film page."""
    try:
        response = await asyncio.to_thread(
            lambda: requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        )
        if response.status_code == 200:
            match = re.search(r'property="og:image" content="(.*?)"', response.text)
            if match:
                return match.group(1)
    except Exception:
        pass
    return ""


async def enrich_wrapped_with_posters(w: dict) -> dict:
    """
    Backfill posters for any card the film cache didn't already cover.

    Scraped films carry their poster through from the cache, so this normally
    has nothing to do; it only fetches for films the scrape hasn't reached yet.
    """
    missing = [m for m in w.get("loved_by_you", []) + w.get("hated_by_you", [])
               if not m.get("poster")]
    if not missing:
        return w

    urls = [f"https://letterboxd.com/film/"
            f"{re.sub(r'[^a-z0-9]+', '-', m['title'].lower()).strip('-')}/"
            for m in missing]
    posters = await asyncio.gather(*(fetch_poster(u) for u in urls))

    for movie, poster in zip(missing, posters):
        movie["poster"] = poster

    return w


# The Letterboxd CDN sends no Access-Control-Allow-Origin header, so a canvas
# that has drawn one of its images is tainted and html2canvas silently drops
# the poster — the share card exported as a row of empty frames. Re-serving
# those bytes from this origin is what lets the download keep its posters.
#
# Host-locked on purpose: an image proxy that fetches whatever it is handed is
# an open relay, useful for hiding the origin of a request that is not ours.
POSTER_HOSTS = ("a.ltrbxd.com", "s.ltrbxd.com")


@app.get("/api/poster-img")
async def poster_img(u: str = ""):
    """Re-serve one Letterboxd poster from this origin, for canvas export."""
    host = urllib.parse.urlparse(u).netloc.lower()
    if not u.startswith("https://") or host not in POSTER_HOSTS:
        return JSONResponse(status_code=400, content={"error": "Not a poster URL."})

    try:
        upstream = await asyncio.to_thread(
            lambda: requests.get(u, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        )
    except Exception:
        return JSONResponse(status_code=502, content={"error": "Could not fetch that poster."})

    if upstream.status_code != 200:
        return JSONResponse(status_code=502, content={"error": "Could not fetch that poster."})

    media = upstream.headers.get("Content-Type", "image/jpeg")
    if not media.startswith("image/"):
        return JSONResponse(status_code=415, content={"error": "That is not an image."})

    return Response(
        content=upstream.content,
        media_type=media,
        headers={
            "Cache-Control": "public, max-age=604800",
            "Access-Control-Allow-Origin": "*",
        },
    )


# ---------------------------------------------------------------------------
# Dashboard static files
# ---------------------------------------------------------------------------

# app.js pushes a path per screen, so a reload or a pasted link has to find
# the app on all of them rather than a 404. There is nothing to route on the
# server: the client reads its own screen back out of the path.
@app.get("/")
@app.get("/quiz")
@app.get("/result")
@app.get("/example")
def read_root():
    response = FileResponse(os.path.join(DASHBOARD_DIR, "index.html"))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# Mounted after the explicit route so "/" doesn't conflict
app.mount("/dashboard", StaticFiles(directory=DASHBOARD_DIR), name="dashboard")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    removed = sessions.purge_expired()
    if removed:
        print(f"Purged {removed} expired session(s)")
    port = int(os.getenv("PORT", "8000"))
    print(f"Starting Close-Up at http://localhost:{port}")
    # One worker only: scrape job state lives in memory and the film cache is a
    # single file guarded by an in-process lock. See README before scaling out.
    uvicorn.run(app, host="0.0.0.0", port=port)
