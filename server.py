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
import traceback
import zipfile

import numpy as np
import pandas as pd
import requests
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# Fix Windows registry MIME type issues
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('application/javascript', '.js')

import sessions
from analyzer import (films_matching, full_analysis, instant_summary,
                      most_watched_people, wrapped_summary)
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

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _start_scrape(session_id: str, links: list[str]) -> None:
    """Kick off a background metadata scrape for one session's films."""
    cache = load_film_cache()
    pending = [link for link in dict.fromkeys(links) if link and link not in cache]

    with _jobs_lock:
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

    cache = load_film_cache()
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
        return None, JSONResponse(status_code=400, content={"error": "session parametresi gerekli"})
    df = load_dataset(session_id)
    if df is None:
        return None, JSONResponse(status_code=404, content={"error": "Oturum bulunamadı. ZIP'i tekrar yükleyin."})
    return df, None


# ---------------------------------------------------------------------------
# ZIP parsing
# ---------------------------------------------------------------------------

def _parse_letterboxd_zip(zip_bytes: bytes) -> pd.DataFrame:
    """Parse a Letterboxd export ZIP into a unified DataFrame."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        expanded = sum(info.file_size for info in zf.infolist())
        if expanded > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("ZIP içeriği beklenenden çok büyük")

        names = zf.namelist()

        watched_path = next((n for n in names if n.endswith('watched.csv')), None)
        ratings_path = next((n for n in names if n.endswith('ratings.csv')), None)

        if not watched_path:
            raise ValueError("ZIP dosyasında watched.csv bulunamadı")

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

async def _read_limited(file: UploadFile) -> bytes:
    """Read an upload into memory, refusing anything over MAX_UPLOAD_BYTES."""
    chunks, total = [], 0
    while chunk := await file.read(1 << 20):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise ValueError(f"Dosya çok büyük (en fazla {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")
        chunks.append(chunk)
    return b"".join(chunks)


@app.post("/api/upload-zip")
async def upload_zip(file: UploadFile = File(...)):
    """
    Parse an export ZIP, open a session, and return the stats available with
    no scraping at all. The metadata scrape starts in the background.
    """
    try:
        df = _parse_letterboxd_zip(await _read_limited(file))
        if df.empty:
            return JSONResponse(status_code=400, content={"error": "ZIP boş görünüyor"})

        session_id = sessions.create(df)
        _start_scrape(session_id, df["link_of_movie"].dropna().tolist())

        return clean_nans({
            "status": "success",
            "session_id": session_id,
            "instant": instant_summary(df),
            "scrape": _job_state(session_id),
        })
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=400, content={"error": str(e)})


@app.get("/api/progress")
async def progress_stream(session: str = ""):
    """Server-sent events reporting background scrape progress."""
    if not sessions.exists(session):
        return JSONResponse(status_code=404, content={"error": "Oturum bulunamadı"})

    async def events():
        while True:
            state = _job_state(session)
            yield f"data: {json.dumps(state)}\n\n"
            if state["status"] in ("done", "error"):
                break
            await asyncio.sleep(0.5)

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
        cache = load_film_cache()
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
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


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
        return JSONResponse(status_code=400, content={"error": "phase 'instant' veya 'full' olmalı"})
    try:
        build = build_instant_quiz if phase == "instant" else build_full_quiz
        return clean_nans({"phase": phase, "questions": build(df, seed=seed)})
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/wrapped")
async def get_wrapped(session: str = ""):
    """Full Wrapped summary, including everything the scrape has filled in."""
    df, error = _require(session)
    if error:
        return error
    try:
        result = await enrich_wrapped_with_posters(wrapped_summary(df))
        return clean_nans(result)
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/stats")
async def get_stats(session: str = ""):
    """Full statistical analysis of the session's dataset."""
    df, error = _require(session)
    if error:
        return error
    try:
        return clean_nans(full_analysis(df))
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


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
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


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


# ---------------------------------------------------------------------------
# Dashboard static files
# ---------------------------------------------------------------------------

@app.get("/")
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
    print(f"Starting Letterboxd Analysis server at http://localhost:{port}")
    # One worker only: scrape job state lives in memory and the film cache is a
    # single file guarded by an in-process lock. See README before scaling out.
    uvicorn.run(app, host="0.0.0.0", port=port)
