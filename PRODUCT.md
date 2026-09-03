# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Anyone with a Letterboxd account who wants insight into their own film taste.
The interface is Turkish-only today, but that is a starting point, not a
scope limit — the intent is to serve any Letterboxd user, with additional
languages as a plausible (not yet started) future direction. Users arrive
with no account on this app itself: they export their data from Letterboxd
and upload the ZIP.

## Product Purpose

Turns a Letterboxd data export into two things: (1) a "how well do you know
yourself" quiz built entirely from the user's own viewing history — correct
answers and distractors both drawn from their real data — answerable within
a second of upload; and (2) a six-chapter visual deep-dive into their taste
(directors, actors, genres, ratings vs. the crowd, decades, countries,
languages) that fills in as background scraping completes. Success is a user
who uploads, plays the quiz, and leaves with a result (shared PNG or the full
scroll) that reflects something true and specific about their own taste.

## Positioning

Not a generic "year in review" stats page: the core mechanic is a
personality quiz built from the user's own data, not category trivia.
Letterboxd exports carry only five columns (date, name, year, URI, rating)
— no director, genre, runtime, cast, or community rating — so a neighboring
"stats from your export" tool would have to choose between staring at a
scrape spinner or a database it doesn't have. This product answers instantly
from the ZIP alone via `instant_summary()`, then streams the richer,
scrape-dependent quiz phase and analysis in over SSE as a background scrape
fills a cache shared across all users — so the product gets faster for
everyone as more people use it, with no login and no database.

## Operating Context

A user exports their data at `letterboxd.com/settings/data/` and drops the
ZIP on the landing page — no signup on this app. It runs as a single Render
web service (`WEB_CONCURRENCY=1`) on the free tier, so disk is not
persistent: session files and the shared film cache reset on every deploy,
which is why a seeded cache (`film_cache.seed.json`) ships in the repo so a
fresh container starts warm. Session data expires after 30 days regardless.

## Capabilities and Constraints

- The Letterboxd export ZIP is the only source of the user's own film
  list/ratings/watch dates — everything else (director, genre, runtime,
  cast, community rating, poster) is scraped live per film from that film's
  Letterboxd page.
- Letterboxd profile pages beyond page 1 are Cloudflare-protected and cannot
  be scraped — the ZIP export is not optional, there is no fallback source.
- One worker only: scrape job state lives in an in-process dict and the
  shared film cache is a single lock-guarded file. Any new feature must not
  assume multiple processes or a database.
- No persistent disk on the current hosting tier — `sessions/` and
  `film_cache.json` are wiped on every deploy; only what's committed to the
  repo (the seed cache) survives.
- Outbound scraping is capped globally at 16 concurrent requests across all
  sessions combined, to avoid the host IP getting banned by Letterboxd.
- No user accounts. A session is tied to one uploaded ZIP, not to a
  person — nothing persists a user's identity across visits.
- Rankings (favorite director/actor) use Bayesian averaging
  (`MIN_DIRECTOR_FILMS` / `MIN_ACTOR_FILMS`, currently 4) as both a minimum
  sample size and prior weight, so a two-film director can't outrank one
  followed for years on one great rating.
- Undecided: whether/when to support languages beyond Turkish. Undecided:
  monetization (none exists today; the stated goal is organic growth via
  shareable results, not revenue).

## Brand Commitments

"Letterboxd Wrapped" is a **working title, not a locked name** — it
deliberately borrows Letterboxd's brand and the Spotify "Wrapped" format as
a placeholder, and the user expects the name to change before any wider
release. Future work should not treat this name, or design decisions
anchored to it, as durable.

## Evidence on Hand

Real personal Letterboxd export data exists locally (a full export folder,
`test_export.zip`, `my_movie_dataset.csv`) — this is the user's own data
used for development/testing, not a curated demo or case study for
marketing. There are no testimonials, press mentions, or case studies;
future work must not fabricate any.

## Product Principles

1. Time-to-first-value beats completeness — the instant summary and quiz
   must work the moment the ZIP lands, before any scrape finishes.
2. The data is the content — quiz questions, distractors, rankings, and
   analysis all come from the user's own real history, never generic trivia
   or placeholder examples.
3. Shareability is the growth lever — the result screen (score, share card,
   PNG export) is the artifact meant to travel outside the app and bring in
   the next user.
4. Every session warms the shared cache — architecture should keep favoring
   "more users make it faster for everyone" over per-user isolation.
5. Single-worker, ephemeral-disk hosting is a real, ongoing constraint —
   design within it (in-memory job state, a lock-guarded shared cache file,
   a seeded cache committed to the repo) rather than assuming it away.

## Accessibility & Inclusion

No accessibility requirement has been established yet.
