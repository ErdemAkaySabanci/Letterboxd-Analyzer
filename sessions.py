"""
Per-upload session storage
==========================
Each ZIP upload gets its own session so two visitors never share a dataset.

A session file holds only what came out of the user's ZIP — title, year,
rating, watch date, link. Everything else (director, cast, runtime, genres,
community rating, poster) is re-derived from the shared film cache on every
load. That keeps session files tiny, and means a session automatically picks
up richer metadata as the background scrape fills the cache.
"""

import os
import time
import uuid

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")

# Columns that come straight from the export ZIP and belong to this user alone.
OWN_COLUMNS = [
    "movie_id",
    "title_of_movie",
    "my_rating",
    "link_of_movie",
    "Watch_Date",
    "Release_Year",
]

SESSION_TTL_DAYS = 30


def _path(session_id: str) -> str:
    return os.path.join(SESSIONS_DIR, f"{session_id}.csv")


def is_valid_id(session_id: str) -> bool:
    """Guard against path traversal — ids are always 32 hex characters."""
    return (
        isinstance(session_id, str)
        and len(session_id) == 32
        and all(c in "0123456789abcdef" for c in session_id)
    )


def create(df: pd.DataFrame) -> str:
    """Persist a freshly parsed upload and return its session id."""
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    session_id = uuid.uuid4().hex
    columns = [c for c in OWN_COLUMNS if c in df.columns]
    df[columns].to_csv(_path(session_id), index=False, encoding="utf-8")
    return session_id


def load(session_id: str) -> pd.DataFrame | None:
    """Load a session's own data, or None if the id is unknown or expired."""
    if not is_valid_id(session_id):
        return None
    path = _path(session_id)
    if not os.path.exists(path):
        return None
    return pd.read_csv(path)


def exists(session_id: str) -> bool:
    return is_valid_id(session_id) and os.path.exists(_path(session_id))


def purge_expired(ttl_days: int = SESSION_TTL_DAYS) -> int:
    """Delete sessions untouched for longer than the TTL. Returns the count."""
    if not os.path.isdir(SESSIONS_DIR):
        return 0
    cutoff = time.time() - ttl_days * 86400
    removed = 0
    for name in os.listdir(SESSIONS_DIR):
        if not name.endswith(".csv"):
            continue
        path = os.path.join(SESSIONS_DIR, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            pass
    return removed
