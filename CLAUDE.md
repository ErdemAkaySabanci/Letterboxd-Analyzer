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

# Regenerate dashboard/og.png (the social preview card) — dev-only,
# needs playwright + pillow, which are deliberately absent from requirements.txt
py -3.12 make_og.py
```

`py -3.12` is the interpreter that has the dependencies — bare `python` on this
machine does not. There is no test suite, linter, or frontend build step; the
frontend is plain HTML/CSS/JS served as static files.

[PRODUCT.md](PRODUCT.md) has the product spec (positioning, principles, what's
undecided) if a change touches product shape rather than just code.

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
- `ALLOWED_ORIGINS`, `PORT`, `SCRAPE_WORKERS`, `SCRAPE_JOBS`,
  `UPLOADS_PER_HOUR`, `GEMINI_API_KEY`, `GEMINI_MODEL` and `PROFILES_PER_HOUR`
  come from the environment. **Set `ALLOWED_ORIGINS`** — it defaults to `*`.
  Without `GEMINI_API_KEY` the taste profile does not exist (see below); the
  rest of the app is unaffected.
- **Free tier sleeps after 15 minutes**; the measured cold start is ~33s. Paid
  Starter ($7/mo) removes the sleep and gives 0.5 vCPU instead of 0.1.

Things that bound the single worker, all in-process because there is only one:

- `cached_film_cache()` in [server.py](server.py) memoises the shared cache on
  `(mtime, size)`. Without it every analysis request re-parsed the whole JSON,
  so N concurrent requests held N copies — the real path to exhausting 512 MB.
  **The returned dict is shared: read it, never mutate it.** Scrapers keep
  using `load_cache()` for a private copy.
- `/api/stats` memoises `full_analysis()` per session against the same stamp.
  Measured: 0.82s cold, 0.245s warm on a 603-film library.
- `MAX_SCRAPE_JOBS` (data_manager) caps how many sessions scrape at once. Each
  opens its own pool, so without it twenty uploads meant several hundred live
  threads. Queued sessions lose nothing visible — phase-1 questions need no
  scrape.
- `_jobs` entries are dropped once the SSE stream sees a terminal state;
  `_job_state()` reports "done" for anything it no longer holds.
- Uploads are rate-limited per IP (`UPLOADS_PER_HOUR`, default 12) and
  `sessions.purge_expired()` now also runs opportunistically on upload, not
  only at boot.

## Architecture

**Backend**: a FastAPI app in [server.py](server.py) serving the frontend from `dashboard/` plus a JSON API. There is no database.

The product is **Close-Up** ("for Letterboxd"), English-only. It deliberately
does not lead with Letterboxd's trademark: their terms license the logo for
linking, not the name for a product.

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
- **[personas.py](personas.py)** — the viewer persona behind chapter 07 and
  the share card's "Viewer type" row. It is segmentation *without a
  population*: there is no user pool to fit k-means on (no database, sessions
  expire), so five persona centroids are written by hand over eleven [0, 1]
  feature shares (four genre groups, non-English, pre-1990, obscure, generous,
  contrarian, new-release, long) and only the assignment step runs — nearest
  centroid, with `exp(-d / TEMPERATURE)` turned into a closeness percentage
  for all five. `_selfcheck()` (run `py -3.12 personas.py`) asserts every
  centroid wins its own persona; run it after touching a centroid. Needs
  `MIN_FILMS` (20) films with metadata, otherwise `None` and the chapter
  stays hidden. Do not call it "clustering" in copy — the page says
  "closeness, not a verdict".
- **[taste.py](taste.py)** — the written "taste profile" under the result
  score, the one analysis step that runs on somebody else's model. It is a
  single `requests.post` to the Gemini REST API (no SDK: `requests` is already
  pinned and a one-call feature does not earn a dependency on a 512 MB box)
  from `taste_facts()` in analyzer.py — ~600 tokens of names, counts and
  averages, no watch dates, no film list, nothing identifying. Off unless
  `GEMINI_API_KEY` is set; `GEMINI_MODEL` defaults to `gemini-2.5-flash`
  (the free tier: ~1,500 requests/day, no card). `/api/profile` answers with
  a status (`disabled | pending | ready | unavailable`), never a 5xx, and the
  page hides the block for anything but `ready` — there is no spinner because
  the paragraph may legitimately never come. Every profile is written once
  and kept with the session as `sessions/{id}.profile.json`; the demo's is
  `demo_session.profile.json`, committed so the example spends no quota.
  `PROFILES_PER_HOUR` (default 60) caps generations per process, and a
  session that failed twice stops asking. The 2.5 models spend "thinking"
  tokens out of `maxOutputTokens` — `taste.py` turns thinking off for them or
  a capped reply can come back empty.
- **[quiz.py](quiz.py)** — builds "how well do you know yourself" questions from the user's own library, with distractors drawn from their real data. `build_instant_quiz()` works the moment the ZIP lands; `build_full_quiz()` needs the scrape. Questions ship ready to render; the client never derives facts.

### Frontend ([dashboard/](dashboard/))

No framework, no build step. Chart.js and html2canvas via CDN.

- `quiz.js` — the quiz engine. Questions can be appended mid-run, which is how phase-2 questions join once the scrape finishes.
- `app.js` — orchestration: upload → quiz → result → analysis, plus all chapter rendering.
- **The three screens are real addresses**: `/`, `/quiz`, `/result`. `show()`
  pushes one per transition and a `popstate` handler restores it, so the back
  button works — on a phone it is the main way out of a screen, and without
  this the only thing it could do from the result was leave the site.
  [server.py](server.py) serves `index.html` on all three so a reload or a
  pasted link is not a 404; a deep link with nothing in `localStorage` to
  restore rewrites itself back to `/`.
  Adding a screen means adding it to `PATHS` **and** to the server's routes.
- **`/example` is the landing page's "see an example result"**: the `wrapped`
  screen showing the committed `demo_session.csv` (id `DEMO_SESSION_ID` in
  [sessions.py](sessions.py), served by `/api/demo`). It is a server route but
  deliberately *not* in `PATHS` — it is a second way into an existing screen —
  and it never touches `localStorage`. Demo mode is `isDemo()`, derived from
  `session`, never a separate flag. No scrape is ever started for it, so every
  film in that CSV must already be in `film_cache.seed.json` or the demo
  silently renders half-empty (`enrich_with_cache` substitutes defaults, it
  does not throw). Anything that re-renders the analysis for a different
  library must go through `resetAnalysis()`, which clears all three one-shot
  guards; clearing only `loadAnalysis.done` leaves the previous library's
  faces and posters under the new numbers.
- The result summary and the 7 analysis chapters live on **one continuous page** (`#wrapped`); there is no separate dashboard page. Chapter 07 (`#ch7`) starts `hidden` and `chapterType()` unhides it only when `/api/stats` carries a persona.

Design language is dark, poster-forward, with a per-section accent (`data-accent` on an ancestor sets `--accent`).

## Analytics

Cloudflare Web Analytics, via the JS beacon in `index.html` — free, and it
needs no DNS change because the site is not proxied through Cloudflare. It sets
no cookie and touches no `localStorage`, so there is no consent banner to owe
anyone and the landing page's privacy line stays true.

**It has no custom-event API.** The funnel is read instead from the routes
above: `/` → `/quiz` → `/result` is landing → started the test → finished one,
and the drop-off between them is the number worth watching. The example
result lives at `/example` for exactly this reason: had it pushed `/result`,
every visitor who browsed the demo would count as a finished test. Anything
that needs a real event would need a different tool.

The beacon is registered against one hostname, so it rejects beacons from
`localhost` with a CORS error in the console. That is expected locally and not
a bug; change the hostname in the Cloudflare dashboard if the domain moves.

## Gotchas that have already bitten

- Setting `display` in an id rule (`#landing { display: grid }`) outranks `.page { display: none }` and leaves every screen stacked. Put `display` only on `.active`.
- `[hidden]` loses to any rule that sets `display`; the global `[hidden] { display: none !important }` in `style.css` is load-bearing.
- `a ?? b || c` is a JavaScript syntax error — parenthesise it. It takes down the whole file silently, so run `node --check`.
- In `chart()`, the merged `plugins`/`scales` must come *after* `...config.options`, or a caller that sets either one drops the defaults.
- Ratings cluster in a narrow band (~2.8–4.0); a 0–5 bar makes every genre look identical. Scale to the observed range.
- Watch dates are *log* dates. A bulk import on signup day skews any "busiest month/day" stat, and `diary.csv` (the only source of real watch dates) is usually near-empty.
- Never uppercase a person's name. (The page was `lang="tr"` once, where
  `text-transform: uppercase` turned "Fiennes" into "FİENNES"; the interface is
  English now, but a real name is still set the way its owner spells it.)
- **Two CSS rules of equal specificity: source order wins.** A `@media` block
  placed *above* the base rule it means to override does nothing. The phone
  rules for `.btn-skip` are scoped `#play .btn-skip` for exactly this reason.
- The landing scrim (`#landing::after`) and the poster wall's mask are ellipses
  sized for a wide viewport, where the copy sits inside a hollowed-out middle.
  On a phone the copy *is* the middle, so both need their own `max-width: 620px`
  treatment or posters run straight through the words.
- **html2canvas will not draw an image it cannot read.** The Letterboxd CDN
  sends no `Access-Control-Allow-Origin`, so a poster loaded straight from it
  taints the canvas and vanishes from the exported share PNG. The share strip
  routes through `/api/poster-img` (same origin, host-locked) so the download
  keeps its posters; the decorative reels stay on the CDN.
