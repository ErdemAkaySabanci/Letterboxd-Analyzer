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

import json
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

# The landing page's "example result" reads this one committed library. Its id
# is 32 hex characters so it passes is_valid_id like any other, but the file
# sits outside SESSIONS_DIR, which is what makes it permanent: purge_expired()
# only walks that directory, and create() only ever writes a uuid4 into it.
DEMO_SESSION_ID = "deadbeefdeadbeefdeadbeefdeadbeef"
DEMO_FILE = os.path.join(BASE_DIR, "demo_session.csv")


# The written taste profile lives next to the CSV under this suffix. It is
# the one thing a session holds that is not re-derived from the cache: the
# model is asked once, and the answer is kept for the session's lifetime.
PROFILE_SUFFIX = ".profile.json"


def _path(session_id: str, suffix: str = ".csv") -> str:
    if session_id == DEMO_SESSION_ID:
        return os.path.splitext(DEMO_FILE)[0] + suffix
    return os.path.join(SESSIONS_DIR, session_id + suffix)


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


def load_profile(session_id: str) -> dict | None:
    """The saved taste profile for a session, or None if none was written yet."""
    if not is_valid_id(session_id):
        return None
    path = _path(session_id, PROFILE_SUFFIX)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_profile(session_id: str, data: dict) -> None:
    if not is_valid_id(session_id):
        return
    with open(_path(session_id, PROFILE_SUFFIX), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def purge_expired(ttl_days: int = SESSION_TTL_DAYS) -> int:
    """Delete sessions untouched for longer than the TTL. Returns the count."""
    if not os.path.isdir(SESSIONS_DIR):
        return 0
    cutoff = time.time() - ttl_days * 86400
    removed = 0
    for name in os.listdir(SESSIONS_DIR):
        if not (name.endswith(".csv") or name.endswith(PROFILE_SUFFIX)):
            continue
        path = os.path.join(SESSIONS_DIR, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            pass
    return removed
