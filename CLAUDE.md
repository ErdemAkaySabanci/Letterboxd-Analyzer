# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (pinned — the deploy image installs the same file)
pip install -r requirements.txt

# Run the server (FastAPI + static frontend) at http://localhost:8000
py -3.12 server.py

# Backfill film metadata for a dataset CSV (rated films only; --all includes unrated)
py -3.12 data_manager.py
py -3.12 data_manager.py --all

# Syntax-check the frontend before loading it in a browser
node --check dashboard/app.js && node --check dashboard/quiz.js
```

`py -3.12` is the interpreter that has the dependencies — bare `python` on this
machine does not. There is no test suite, linter, or frontend build step; the
frontend is plain HTML/CSS/JS served as static files.

## Branches and deployment

**Push to `dev`, never to `main`.** The app is live at
https://letterboxd-analyzer.onrender.com and Render deploys from `main`, so a
push there goes straight to the public site. Day-to-day work — every commit,
every experiment — belongs on `dev`.

`main` moves only when a change has been checked and is meant to ship:

```bash
git checkout main && git merge dev && git push origin main
git checkout dev
```

Deploy notes that constrain what can safely change:

- **One worker only.** Scrape job state lives in a module-level dict and the
  film cache is a single lock-guarded file. Render sets `WEB_CONCURRENCY=1`;
  anything that assumes multiple processes will lose job state.
- **No persistent disk on the free tier.** `sessions/` and `film_cache.json`
  reset on every deploy, which is why `film_cache.seed.json` is committed and
  copied into place by the Dockerfile — a fresh container starts warm instead
  of re-scraping hundreds of films on 0.1 vCPU.
- `ALLOWED_ORIGINS`, `PORT`, and `SCRAPE_WORKERS` come from the environment.

## Architecture

**Backend**: a FastAPI app in [server.py](server.py) serving the frontend from `dashboard/` plus a JSON API. There is no database.

### Data flow

The user exports their data from `letterboxd.com/settings/data/` and uploads the ZIP. That ZIP contains only `Date, Name, Year, Letterboxd URI` (plus `Rating`) — **no director, genre, runtime, cast, or community rating**. Everything else is scraped per film.

1. `POST /api/upload-zip` parses the ZIP, opens a session, and returns immediately with `instant_summary()` — the stats derivable from the ZIP alone. A background thread starts scraping metadata for films not yet cached.
2. `GET /api/progress` streams scrape progress over SSE.
3. Later requests re-derive metadata from the shared cache, so a session's data fills in as the scrape proceeds — no extra machinery needed.

### Two-layer storage

- **[sessions.py](sessions.py)** — one file per upload (`sessions/{32-hex}.csv`), holding *only* the columns unique to that user (title, year, rating, watch date, link). Sessions are ~37 KB and expire after 30 days.
- **`film_cache.json`** — shared across all users, keyed by Letterboxd URI, holding scraped metadata and poster URLs. The more users, the warmer it gets.

`enrich_with_cache()` joins them on `link_of_movie`. Match on that URI, never on a slug guessed from the title — titles with disambiguated slugs (`doctor-strange-2016`) silently miss.

### Scraping

[data_manager.py](data_manager.py) fetches film pages and reads the schema.org LD+JSON block, which **Letterboxd wraps in a CDATA comment that must be stripped before `json.loads`**. Profile pages are Cloudflare-protected past page 1 and are never scraped — the film list comes from the ZIP. 16 workers, ~0.3s per film, so ~2 min for a 600-film cold library.

[playwright_scraper.py](playwright_scraper.py) is a leftover from the original scrape-the-profile approach and is not used by the server.

### Analysis

- **[analyzer.py](analyzer.py)** — pure `DataFrame -> dict` functions. `instant_summary()` (ZIP-only) and `full_analysis()` (needs metadata) are the aggregate entry points. `MIN_DIRECTOR_FILMS` / `MIN_ACTOR_FILMS` (both 4) act as *both* the minimum film count and the Bayesian prior weight — lower values let a two-film director outrank one you've followed for ten.
- **[quiz.py](quiz.py)** — builds "how well do you know yourself" questions from the user's own library, with distractors drawn from their real data. `build_instant_quiz()` works the moment the ZIP lands; `build_full_quiz()` needs the scrape. Questions ship ready to render; the client never derives facts.
- **[ml_models.py](ml_models.py)** — `explain_predictions()` trains the models and reports feature importance in plain language (genre/actor one-hots rolled up). The raw metrics and the director-based recommender are no longer surfaced.

### Frontend ([dashboard/](dashboard/))

No framework, no build step. Chart.js and html2canvas via CDN.

- `quiz.js` — the quiz engine. Questions can be appended mid-run, which is how phase-2 questions join once the scrape finishes.
- `app.js` — orchestration: upload → quiz → result → analysis, plus all chapter rendering.
- The result summary and the 7 analysis chapters live on **one continuous page** (`#wrapped`); there is no separate dashboard page.

Design language is dark, poster-forward, with a per-section accent (`data-accent` on an ancestor sets `--accent`).

## Gotchas that have already bitten

- Setting `display` in an id rule (`#landing { display: grid }`) outranks `.page { display: none }` and leaves every screen stacked. Put `display` only on `.active`.
- `[hidden]` loses to any rule that sets `display`; the global `[hidden] { display: none !important }` in `style.css` is load-bearing.
- `a ?? b || c` is a JavaScript syntax error — parenthesise it. It takes down the whole file silently, so run `node --check`.
- In `chart()`, the merged `plugins`/`scales` must come *after* `...config.options`, or a caller that sets either one drops the defaults.
- Ratings cluster in a narrow band (~2.8–4.0); a 0–5 bar makes every genre look identical. Scale to the observed range.
- Watch dates are *log* dates. A bulk import on signup day skews any "busiest month/day" stat, and `diary.csv` (the only source of real watch dates) is usually near-empty.
- The page is `lang="tr"`, so CSS `text-transform: uppercase` turns "Fiennes" into
  "FİENNES". Never uppercase a person's name.
