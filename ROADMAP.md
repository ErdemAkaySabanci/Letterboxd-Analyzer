# Roadmap

Working notes for what comes next. Written so a fresh Claude Code session can
pick up any single track without reading the others.

**Read [CLAUDE.md](CLAUDE.md) first** — it carries the architecture, the
deployment constraints, and the gotchas that have already cost time.

## How to work through this

Push to `dev`, never to `main` (`main` is what Render deploys to the live site).

If you are running **several chats at once**, give each track its own branch off
`dev` so they do not fight over the same files:

```bash
git checkout dev && git pull
git checkout -b feat/<track-name>
# ...work...
git checkout dev && git merge feat/<track-name> && git push origin dev
```

Which tracks can safely run in parallel:

| Together | Why |
| --- | --- |
| A + E + F | Different files entirely, zero overlap |
| C + D | **Avoid.** Both live in `dashboard/`, they will conflict |

---

## Track A — Ship what is already built

Small, no code, do it first. Everything else is more pleasant once the live
site actually reflects the repo.

1. **Deploy the current `main`.** The seed-cache fix (`6b20ec5`) is committed
   but the last Render deploy ran from `9fdf87d`, so the live site is still
   scraping from cold. Render dashboard → Manual Deploy → Deploy latest commit.
2. **Turn on auto-deploy** from `main` in Render → Settings, so `main` and the
   live site stop drifting apart.
3. **Add a cold-start line to the README**: the free instance sleeps and the
   first request can take ~50 seconds. Saying so is better than a visitor
   assuming the app is broken.

**Done when** the live site loads a 600-film export with no scraping wait.

---

## Track C — Stop the quiz stalling

The bug actually seen in the browser: the first 6 questions answer fine, then
the quiz sits on "Film bilgilerin geliyor..." for a long time.

Cause: [`app.js:137-150`](dashboard/app.js) only calls `loadFullPhase()` once
the scrape reports `done` or `error`. Phase-2 questions therefore wait for
**every** film to finish, even though they only need enough metadata to build a
handful of questions. On a 0.1 vCPU host that wait is long enough to look
broken.

1. **Load phase 2 on a threshold, not on completion** — once enough films carry
   metadata for `build_full_quiz()` to produce questions, add them. The scrape
   can keep running underneath; the two-layer cache design already means later
   requests see richer data.
2. **Decide what `build_full_quiz()` needs** to produce a usable question set,
   and have the server say when that bar is met rather than making the client
   guess.
3. **Give the wait a real end.** If the threshold is never reached, the quiz
   should finish gracefully rather than spin forever.

**Files**: `dashboard/app.js`, `quiz.py`, `server.py` (`/api/quiz`)
**Done when** a cold library reaches the end of the quiz without a stall.

---

## Track D — Frontend work in flight

There are uncommitted changes in `dashboard/index.html`, `dashboard/quiz.js`,
and `dashboard/style.css` from an earlier session. Decide whether they are
finished, then commit or discard them — leaving them uncommitted makes every
future branch messier.

Run `node --check dashboard/app.js && node --check dashboard/quiz.js` before
committing; a syntax error there takes down the whole file silently.

**Conflicts with Track C** — do not run both at once.

---

## Track E — Handle libraries that are not yours

Everything has been tested against one 603-film export. A stranger's export
will differ in ways worth handling before anyone else is handed the link.

1. **No ratings at all.** `_parse_letterboxd_zip()` already tolerates a missing
   `ratings.csv`, but the analysis and quiz paths downstream assume ratings
   exist in places. Worth tracing end to end with a ratings-free export.
2. **A very large library.** 5000 films on 0.1 vCPU is a different problem than
   600. At minimum, fail in a way that explains itself.
3. **Real watch dates.** Watch dates in `watched.csv` are *log* dates — a bulk
   import on signup day skews every "busiest month" statistic. `diary.csv`
   holds real dates and is usually near-empty, so any temporal claim should
   either use it when present or say plainly what it is measuring.

**Files**: `analyzer.py`, `server.py`
**Done when** a ratings-free and an oversized export both produce something
sensible instead of an error.

---

## Track F — A small test suite

There are no tests. `analyzer.py` is written as pure `DataFrame -> dict`
functions, which is the easy case — a handful of tests over a small synthetic
DataFrame would cover a lot and cost little.

Worth covering: the Bayesian ranking (a two-film director must not outrank a
ten-film one), `instant_summary()` on an export with no ratings, and the
CDATA-stripping JSON parse in `data_manager.py`.

**Files**: new `tests/` directory only — conflicts with nothing.
**Done when** `pytest` runs green and the README says how to run it.

---

## Not now

Deliberately parked, with the reason:

- **A custom domain.** Pure cosmetics; it changes no deployment behaviour.
- **Paid hosting.** Only worth it if the free tier's sleep and CPU limits
  actually get in the way. Fix Track A and C first and see whether they still
  do.
- **Postgres, multiple workers, monitoring.** All of it is scaling work, and
  there is no traffic to scale for yet.
- **Mini-games and new features.** Nothing new until the existing flow runs
  clean end to end on someone else's library.
