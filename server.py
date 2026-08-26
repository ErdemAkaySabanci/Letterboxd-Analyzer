"""
Letterboxd Analysis – FastAPI Server
=====================================
Serves the dashboard and exposes API endpoints for scraping,
statistics, and recommendations.

Usage:
    py -3.12 server.py
"""

import ast
import os
import io
import zipfile
import threading

import pandas as pd
import numpy as np
import traceback
from fastapi import FastAPI, BackgroundTasks, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import json
import mimetypes

# Fix Windows registry MIME type issues
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('application/javascript', '.js')

from analyzer import full_analysis, clean_dataset, wrapped_summary
from ml_models import train_models, generate_recommendations

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_DIR = os.path.join(BASE_DIR, "dashboard")
CSV_PATH = os.path.join(BASE_DIR, "my_movie_dataset.csv")

# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------

def clean_nans(obj):
    if isinstance(obj, dict):
        return {k: clean_nans(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_nans(v) for v in obj]
    elif isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
        return None
    return obj

app = FastAPI(title="Letterboxd Profile Analysis")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_scrape_status = {
    "running": False,
    "username": None,
    "progress": [],
    "total": 0,
    "done": 0,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_dataset() -> pd.DataFrame | None:
    """Load the cached CSV dataset, returning None if not found."""
    if not os.path.exists(CSV_PATH):
        return None
    df = pd.read_csv(CSV_PATH)
    # Parse genre lists stored as strings
    if "genre_of_movie" in df.columns:
        df["genre_of_movie"] = df["genre_of_movie"].apply(
            lambda x: ast.literal_eval(x) if isinstance(x, str) and x.startswith("[") else []
        )
    return df


def _run_scrape(username: str):
    """Background scrape task (legacy, kept for compatibility)."""
    pass


def enrich_with_cache(df: pd.DataFrame) -> pd.DataFrame:
    cache_path = os.path.join(os.path.dirname(__file__), "movies_cache.json")
    if not os.path.exists(cache_path):
        return df
        
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        return df
        
    directors, actors, countries, languages = [], [], [], []
    runtimes, genres, avg_ratings = [], [], []
    
    import re
    
    # Helper to convert title and year to slug
    def make_slug(title, year):
        if not isinstance(title, str): return ""
        # Remove special chars, spaces to hyphens, lowercase
        slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
        return f"film/{slug}"
        
    for idx, row in df.iterrows():
        # Try exact slug match
        slug = make_slug(row.get('title_of_movie'), row.get('Release_Year'))
        data = cache.get(slug)
        
        # If not found, try slug with year (some Letterboxd URLs append year)
        if not data and pd.notna(row.get('Release_Year')):
            year_slug = f"{slug}-{int(row['Release_Year'])}"
            data = cache.get(year_slug)
            
        data = data or {}
        
        directors.append(data.get('director'))
        actors.append([])
        countries.append([])
        languages.append([])
        runtimes.append(data.get('runtime_minutes'))
        genres.append(data.get('genres', []))
        avg_ratings.append(data.get('average_rating'))
        
    df['Director'] = directors
    df['Actors'] = actors
    df['Country'] = countries
    df['Language'] = languages
    df['Runtime_minutes'] = runtimes
    df['genre_of_movie'] = genres
    df['average_rating'] = avg_ratings
    
    return df


def _parse_letterboxd_zip(zip_bytes: bytes) -> pd.DataFrame:
    """Parse a Letterboxd export ZIP into a unified DataFrame."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()

        # Find watched.csv and ratings.csv (may be in a subfolder)
        watched_path = next((n for n in names if n.endswith('watched.csv')), None)
        ratings_path = next((n for n in names if n.endswith('ratings.csv')), None)
        diary_path = next((n for n in names if n.endswith('diary.csv')), None)

        if not watched_path:
            raise ValueError("ZIP dosyasında watched.csv bulunamadı")

        watched = pd.read_csv(zf.open(watched_path))

        # Ratings (optional — some users may not have rated everything)
        if ratings_path:
            ratings = pd.read_csv(zf.open(ratings_path))
            # Merge on Letterboxd URI to get ratings
            df = watched.merge(
                ratings[['Letterboxd URI', 'Rating']],
                on='Letterboxd URI', how='left'
            )
        else:
            df = watched.copy()
            df['Rating'] = None

        # Rename to match our schema
        df = df.rename(columns={
            'Name': 'title_of_movie',
            'Rating': 'my_rating',
            'Letterboxd URI': 'link_of_movie',
            'Date': 'Watch_Date',
            'Year': 'Release_Year',
        })

        # Add columns that we'll enrich later (or leave empty)
        for col in ['movie_id', 'average_rating', 'genre_of_movie', 'Director',
                    'Actors', 'Country', 'Language', 'Runtime_minutes', 'Watched_number']:
            if col not in df.columns:
                df[col] = None

        # Generate movie_id from link
        df['movie_id'] = df['link_of_movie'].apply(
            lambda x: x.split('/')[-2] if isinstance(x, str) and '/' in x else None
        )

        df = enrich_with_cache(df)

        return df


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/status")
async def get_status():
    """Return scrape progress and dataset size."""
    df = _load_dataset()
    return {
        "scrape": {
            "running": _scrape_status["running"],
            "username": _scrape_status["username"],
            "progress_count": len(_scrape_status["progress"]),
            "last_message": _scrape_status["progress"][-1] if _scrape_status["progress"] else None,
            "movies_done": _scrape_status["done"],
        },
        "dataset": {
            "exists": df is not None,
            "rows": len(df) if df is not None else 0,
        },
    }


@app.post("/api/scrape")
async def start_scrape(username: str = "Erdemstein", background_tasks: BackgroundTasks = None):
    """Legacy scrape endpoint."""
    return {"message": "Use /api/upload-zip instead", "username": username}


@app.post("/api/upload-zip")
async def upload_zip(file: UploadFile = File(...)):
    """Upload a Letterboxd export ZIP and parse it into a dataset."""
    try:
        zip_bytes = await file.read()
        df = _parse_letterboxd_zip(zip_bytes)
        df.to_csv(CSV_PATH, index=False, encoding="utf-8")

        # Immediately compute wrapped summary
        result = wrapped_summary(df)
        return clean_nans({
            "status": "success",
            "total_movies": len(df),
            "rated_movies": int(df["my_rating"].notna().sum()),
            "wrapped": result,
        })
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=400, content={"error": str(e)})


@app.get("/api/scrape-stream")
async def scrape_stream(username: str = "Erdemstein"):
    """SSE endpoint: stream analysis progress (uses existing CSV data)."""
    progress_messages = [
        "📋 Film verileri okunuyor...",
        "⭐ Puanlar hesaplanıyor...",
        "🎯 Yönetmen analizi yapılıyor...",
        "🔥 En tartışmalı filmler bulunuyor...",
        "📊 Wrapped kartların hazırlanıyor...",
    ]

    async def event_generator():
        df = _load_dataset()
        if df is not None and len(df) > 0:
            for i, msg in enumerate(progress_messages):
                data = json.dumps({"type": "progress", "message": msg, "step": i + 1, "total": len(progress_messages)})
                yield f"data: {data}\n\n"
                await asyncio.sleep(0.5)
            result = wrapped_summary(df)
            data = json.dumps({"type": "complete", "wrapped": clean_nans(result)})
            yield f"data: {data}\n\n"
        else:
            yield f'data: {{"type": "error", "message": "Önce ZIP dosyası yükleyin"}}\n\n'

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/wrapped")
async def get_wrapped():
    """Return pre-computed Wrapped summary data."""
    df = _load_dataset()
    if df is None:
        return JSONResponse(status_code=404, content={"error": "No dataset found."})
    try:
        result = wrapped_summary(df)
        return clean_nans(result)
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/stats")
async def get_stats():
    """Return full statistical analysis of the dataset."""
    df = _load_dataset()
    if df is None:
        return JSONResponse(status_code=404, content={"error": "No dataset found. Run /api/scrape first."})
    try:
        result = full_analysis(df)
        return clean_nans(result)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/models")
async def get_models():
    """Return ML model training results."""
    df = _load_dataset()
    if df is None:
        return JSONResponse(status_code=404, content={"error": "No dataset found."})
    try:
        result = train_models(df)
        # Remove non-serializable objects
        result.pop("_best_model_obj", None)
        result.pop("_scaler", None)
        result.pop("_feature_cols", None)
        return clean_nans(result)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/recommendations")
async def get_recommendations(n: int = 10):
    """Return movie recommendations based on trained models."""
    df = _load_dataset()
    if df is None:
        return JSONResponse(status_code=404, content={"error": "No dataset found."})
    try:
        result = generate_recommendations(df, n=n)
        return clean_nans(result)
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


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


# Mount static files AFTER the explicit route so "/" doesn't conflict
app.mount("/dashboard", StaticFiles(directory=DASHBOARD_DIR), name="dashboard")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    print(f"Starting Letterboxd Analysis server at http://localhost:8000")
    print(f"Dashboard dir: {DASHBOARD_DIR}")
    uvicorn.run(app, host="0.0.0.0", port=8000)
