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
import threading

import pandas as pd
import numpy as np
import traceback
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from data_manager import LetterboxdScraper
from analyzer import full_analysis, clean_dataset
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
    """Background scrape task."""
    global _scrape_status
    _scrape_status["running"] = True
    _scrape_status["username"] = username
    _scrape_status["progress"] = []
    _scrape_status["done"] = 0

    def on_progress(msg: str):
        _scrape_status["progress"].append(msg)
        _scrape_status["done"] += 1

    try:
        scraper = LetterboxdScraper(username, use_cache=True)
        scraper.set_progress_callback(on_progress)
        dataset = scraper.scrape_full()
        dataset.to_csv(CSV_PATH, index=False, encoding="utf-8")
        _scrape_status["total"] = len(dataset)
    except Exception as e:
        _scrape_status["progress"].append(f"ERROR: {e}")
    finally:
        _scrape_status["running"] = False


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
    """Start a background scrape for the given Letterboxd username."""
    if _scrape_status["running"]:
        return JSONResponse(
            status_code=409,
            content={"error": "A scrape is already running", "username": _scrape_status["username"]},
        )
    background_tasks.add_task(_run_scrape, username)
    return {"message": f"Scrape started for '{username}'", "username": username}


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
async def dashboard_index():
    return FileResponse(os.path.join(DASHBOARD_DIR, "index.html"))


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
