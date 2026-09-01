# Letterboxd Wrapped

Upload your Letterboxd data export and get a "how well do you know yourself"
quiz built from your own viewing history, followed by a seven-chapter visual
analysis of your taste.

The interface is in Turkish; this README is in English.

## The constraint that shapes everything

A Letterboxd export ZIP contains five columns: `Date, Name, Year, Letterboxd
URI, Rating`. No director, genre, runtime, cast, or community rating. Profile
pages are Cloudflare-protected past page 1, so they cannot be crawled either.

Everything interesting therefore has to be scraped per film, and scraping 600
films takes about two minutes — far too long to make a user stare at a spinner.
The architecture is the answer to that problem:

1. **`POST /api/upload-zip`** parses the ZIP, opens a session, and returns
   immediately with the statistics derivable from the ZIP alone. The user is
   answering quiz questions within a second of uploading.
2. A background thread scrapes film metadata while they play.
   **`GET /api/progress`** streams progress over server-sent events.
3. Later requests re-derive metadata from a shared cache, so the session's data
   fills in as the scrape proceeds. Phase-two quiz questions are appended to a
   run already in flight.

## Two-layer storage

There is no database.

- **`sessions/{32-hex}.csv`** holds *only* the columns unique to one user —
  title, year, rating, watch date, link. About 37 KB per upload; expires after
  30 days.
- **`film_cache.json`** is shared across all users, keyed by Letterboxd URI,
  holding scraped metadata and poster URLs.

The two are joined on the film's URI at request time. The consequence is that
the cache gets warmer with every user: the hundredth visitor's scrape is mostly
cache hits, and their results appear almost instantly.

## Analysis

`analyzer.py` is a set of pure `DataFrame -> dict` functions. The rankings use
**Bayesian averages** rather than raw means: `MIN_DIRECTOR_FILMS` and
`MIN_ACTOR_FILMS` act as both a minimum sample size and the prior weight, so a
director you have seen twice cannot outrank one you have followed for ten films
on the strength of a single five-star rating.

`ml_models.py` trains regressors (linear, ridge, random forest) on genre and
cast one-hot features, runtime, release year, and director habits, then reports
feature importance in plain language with the one-hot columns rolled back up
into human-readable groups. The output the user sees is the explanation, not
the prediction — "you rate crime films 0.4 stars above your own average" is
more interesting than a predicted score.

## Stack

FastAPI, pandas, scikit-learn, BeautifulSoup. The frontend is plain HTML, CSS
and JavaScript with no framework and no build step; Chart.js and html2canvas
come from a CDN.

## Running it

```bash
pip install -r requirements.txt
python server.py            # http://localhost:8000
```

Or with Docker:

```bash
docker build -t letterboxd-wrapped .
docker run -p 8000:8000 -v "$PWD/data:/app/sessions" letterboxd-wrapped
```

Get your export from `letterboxd.com/settings/data/` and drop the ZIP on the
landing page.

### Deployment notes

- Run **one worker**. Scrape job state lives in memory and the film cache is a
  single file guarded by an in-process lock; multiple workers would lose job
  state and let the last writer win on the cache.
- `sessions/` and `film_cache.json` need a persistent volume. Without one they
  survive until the container restarts, at which point every library has to be
  re-scraped from cold.
- Set `ALLOWED_ORIGINS` to your own origin in production. Unset, CORS defaults
  to permissive for local development.
- `PORT` is read from the environment and defaults to 8000.
- Outbound scraping is capped globally at 16 concurrent requests across all
  sessions, not per session — the limit exists to avoid getting the host's IP
  banned.

## Repository map

| File | Role |
| --- | --- |
| `server.py` | FastAPI app: upload, SSE progress, analysis endpoints, static files |
| `sessions.py` | Per-upload storage and expiry |
| `data_manager.py` | Film scraper (schema.org LD+JSON) and shared cache |
| `analyzer.py` | Pure statistics functions |
| `quiz.py` | Question generation, with distractors drawn from real user data |
| `ml_models.py` | Model training and plain-language feature importance |
| `dashboard/` | Frontend — `app.js` orchestrates, `quiz.js` is the quiz engine |

`playwright_scraper.py` is a leftover from the original scrape-the-profile
approach, kept for reference and unused by the server.
