"""
Quiz question generation
========================
Builds "how well do you know yourself" questions out of a user's own library.

Questions come in two phases:

  instant — answerable from the export ZIP alone (ratings, years, titles), so
            they can be played while the film metadata scrape is still running
  full    — need scraped metadata (directors, cast, countries, runtimes)

Every question ships its own distractors, drawn from the user's real data
wherever possible: a wrong answer should be plausible, not obviously absurd.
The client is a pure renderer and never derives facts of its own.
"""

import random
from collections import Counter

import pandas as pd

from analyzer import clean_dataset

# Accent keys the front-end maps to card themes.
ACCENTS = ["amber", "crimson", "violet", "mint", "sky"]


def pct(n) -> str:
    """Format a number as a whole percentage."""
    return f"{int(round(n))}%"


def _question(qid, eyebrow, prompt, options, answer, reveal, accent, kind="plain",
              poster=None):
    """
    `kind` picks the card layout on the client. It describes what the *answer*
    is, so a run of questions varies in shape instead of repeating one template:

        number  a count or percentage — set as large figures
        rating  a star value — drawn as stars
        person  a director or actor name
        title   a film title
        poster  the film's poster is the subject, shown large
        cast    a list of actors is the prompt material
    """
    return {
        "id": qid,
        "kind": kind,
        "eyebrow": eyebrow,
        "prompt": prompt,
        "options": options,
        "answer": answer,
        "reveal": reveal,
        "accent": accent,
        "poster": poster,
    }


def _shuffled(correct, distractors, rng):
    """Place the correct answer randomly among its distractors."""
    options = list(dict.fromkeys([correct, *distractors]))[:4]
    while len(options) < 4:
        options.append(f"—{len(options)}")
    rng.shuffle(options)
    return options, options.index(correct)


def _spread(value, rng, lo=None, hi=None, step=1, as_pct=False):
    """Three plausible wrong numbers around a correct one."""
    offsets = [-3, -2, -1, 1, 2, 3]
    rng.shuffle(offsets)
    out = []
    for off in offsets:
        candidate = value + off * step * max(1, round(abs(value) * 0.18 / max(step, 1)))
        if lo is not None:
            candidate = max(lo, candidate)
        if hi is not None:
            candidate = min(hi, candidate)
        candidate = round(candidate)
        if candidate != value and candidate not in out:
            out.append(candidate)
        if len(out) == 3:
            break
    while len(out) < 3:
        out.append(value + len(out) + 1)
    return [f"{v}%" if as_pct else str(v) for v in out]


# ---------------------------------------------------------------------------
# Phase 1 — playable immediately, from ZIP data only
# ---------------------------------------------------------------------------

def build_instant_quiz(df: pd.DataFrame, seed: int | None = None) -> list[dict]:
    rng = random.Random(seed)
    df = clean_dataset(df)
    rated = df["my_rating"].dropna()
    questions = []

    if len(rated) >= 20:
        # --- Generosity: nobody knows how kind they actually are ---
        generous = round(float((rated >= 3.5).mean() * 100))
        options, answer = _shuffled(f"{generous}%", _spread(generous, rng, lo=5, hi=99, as_pct=True), rng)
        questions.append(_question(
            "generosity", "The generosity test",
            "What share of the films you rated did you give 3.5 or higher?",
            options, answer,
            f"{int((rated >= 3.5).sum())} of {len(rated)} rated films cleared the bar. "
            + ("You are not a critic, you are a fan." if generous >= 65 else
               "You are hard to please." if generous <= 45 else "You are an even-handed viewer."),
            "amber", kind="number",
        ))

        # --- Signature rating: the score you hand out on autopilot ---
        mode = float(rated.mode()[0])
        share = round(float((rated == mode).mean() * 100))
        pool = [f"{v:.1f}" for v in (2.5, 3.0, 3.5, 4.0, 4.5) if v != mode]
        rng.shuffle(pool)
        options, answer = _shuffled(f"{mode:.1f}", pool[:3], rng)
        questions.append(_question(
            "signature", "Your signature score",
            "Which rating do you hand out most often?",
            options, answer,
            f"{int((rated == mode).sum())} films — {pct(share)} of everything you rated lands on one value.",
            "violet", kind="rating",
        ))

        # --- Five stars: scarcity as self-portrait ---
        fives = int((rated == 5.0).sum())
        options, answer = _shuffled(str(fives), _spread(fives, rng, lo=0), rng)
        questions.append(_question(
            "fivestars", "Five-star scarcity",
            "How many films did you give a full five stars?",
            options, answer,
            f"{pct(fives / len(rated) * 100)} of everything you rated. "
            f"{fives} films earned it.",
            "crimson", kind="number",
        ))

    # --- Decade: which era raised you ---
    years = df["Release_Year"].dropna()
    if len(years) >= 20:
        decades = (years // 10 * 10).astype(int).value_counts()
        top = int(decades.index[0])
        others = [f"{int(d)}s" for d in decades.index[1:4]]
        options, answer = _shuffled(f"{top}s", others, rng)
        questions.append(_question(
            "decade", "Which decade raised you?",
            "Most of your library comes from which decade?",
            options, answer,
            f"{int(decades.iloc[0])} films. "
            + " · ".join(f"{int(d)}s {int(c)}" for d, c in decades.head(4).items()),
            "sky", kind="number",
        ))

    # --- Time capsule: all four are films they actually watched ---
    # The year deliberately doesn't appear in the options — printing it would
    # answer the question outright rather than testing whether they remember.
    oldest = df.dropna(subset=["Release_Year"]).nsmallest(4, "Release_Year")
    if len(oldest) == 4:
        titles = [r["title_of_movie"] for _, r in oldest.iterrows()]
        correct = titles[0]
        options, answer = _shuffled(correct, titles[1:], rng)
        questions.append(_question(
            "oldest", "Time capsule",
            "Which is the oldest film you have watched?",
            options, answer,
            f"{oldest.iloc[0]['title_of_movie']} — {int(oldest.iloc[0]['Release_Year'])}. "
            "All four are films you have actually seen.",
            "amber", kind="title",
        ))

    # --- Unrated: the ones you never had an opinion about ---
    unrated = int(df["my_rating"].isna().sum())
    if unrated >= 5:
        options, answer = _shuffled(str(unrated), _spread(unrated, rng, lo=1), rng)
        questions.append(_question(
            "unrated", "The ones you never scored",
            f"Of {len(df)} films, how many did you leave unrated?",
            options, answer,
            f"One in every {round(len(df) / unrated)} films went by without a word.",
            "mint", kind="number",
        ))

    return questions


# ---------------------------------------------------------------------------
# Phase 2 — needs scraped metadata
# ---------------------------------------------------------------------------

def build_full_quiz(df: pd.DataFrame, seed: int | None = None) -> list[dict]:
    rng = random.Random(seed)
    df = clean_dataset(df)
    questions = []

    rated_dirs = df.dropna(subset=["Director", "my_rating"])
    counts = df["Director"].dropna().value_counts()

    # --- Trap: the director you watch most is rarely the one you rate best ---
    if len(counts) >= 4:
        most = counts.index[0]
        options, answer = _shuffled(most, list(counts.index[1:4]), rng)
        questions.append(_question(
            "most_watched_dir", "Loyalty",
            "Whose films have you watched the most of?",
            options, answer,
            f"{most} — {int(counts.iloc[0])} films. " + " · ".join(
                f"{d} {int(c)}" for d, c in counts.head(4).items()),
            "sky", kind="person",
        ))

    stats = rated_dirs.groupby("Director")["my_rating"].agg(["size", "mean"])
    eligible = stats[stats["size"] >= 4].sort_values("mean", ascending=False)
    if len(eligible) >= 4:
        best = eligible.index[0]
        # Distractors deliberately include the most-watched director, so the
        # previous question primes the wrong answer.
        pool = [d for d in [counts.index[0], *eligible.index[1:4]] if d != best]
        options, answer = _shuffled(best, pool[:3], rng)
        questions.append(_question(
            "favourite_dir", "But your actual favorite",
            "And which director do you rate highest?",
            options, answer,
            f"{best} — {int(eligible.iloc[0]['size'])} films, averaging "
            f"{eligible.iloc[0]['mean']:.2f}. The one you watch most was {counts.index[0]}.",
            "violet", kind="person",
        ))

        # --- Toxic relationship: many films, mediocre scores ---
        watched_a_lot = stats[stats["size"] >= 4].sort_values("mean")
        worst = watched_a_lot.index[0]
        worst_avg = round(float(watched_a_lot.iloc[0]["mean"]), 2)
        worst_n = int(watched_a_lot.iloc[0]["size"])
        if worst_avg < 3.8:
            pool = [f"{v:.2f}" for v in (worst_avg + 0.8, worst_avg - 0.7, worst_avg + 1.4)]
            options, answer = _shuffled(f"{worst_avg:.2f}", pool, rng)
            questions.append(_question(
                "toxic", "Relationship status: complicated",
                f"You have sat through {worst_n} films by {worst}. What do you average on them?",
                options, answer,
                f"{worst_n} films. Averaging {worst_avg:.2f}. Is that a relationship or a habit?",
                "crimson", kind="rating",
            ))

    # --- Confession: the beloved film you couldn't stand ---
    contrast = df.dropna(subset=["my_rating", "average_rating"]).copy()
    if len(contrast) >= 10:
        contrast["diff"] = contrast["my_rating"] - contrast["average_rating"]
        worst = contrast.nsmallest(1, "diff").iloc[0]
        correct = f"{worst['my_rating']:.1f}"
        pool = [f"{v:.1f}" for v in (worst["my_rating"] + 1.0, worst["my_rating"] + 2.0,
                                     worst["my_rating"] + 1.5) if v <= 5.0][:3]
        options, answer = _shuffled(correct, pool, rng)
        questions.append(_question(
            "confession", "Confession time",
            f"Letterboxd gave “{worst['title_of_movie']}” a "
            f"{worst['average_rating']:.2f}. What did you give it?",
            options, answer,
            f"Everyone else loved it. You gave it {worst['my_rating']:.1f} — "
            f"a gap of {abs(worst['diff']):.2f}.",
            "crimson", kind="poster",
            poster=worst.get("poster") or None,
        ))

    # --- Passport: pride, then the twist ---
    countries = Counter(c for lst in df["Country"] if isinstance(lst, list) for c in lst)
    if len(countries) >= 4:
        total = len(countries)
        options, answer = _shuffled(str(total), _spread(total, rng, lo=2), rng)
        top3 = countries.most_common(3)
        share = round(sum(c for _, c in top3) / sum(countries.values()) * 100)
        questions.append(_question(
            "passport", "Your cinema passport",
            "How many different countries have you watched films from?",
            options, answer,
            f"{total} countries. But {pct(share)} of them come from just three: "
            + " · ".join(f"{n} {c}" for n, c in top3),
            "mint", kind="number",
        ))

    # --- Mirror: the humbling one ---
    if len(contrast) >= 20:
        crowd = round(float(contrast["average_rating"].mean()), 2)
        mine = round(float(contrast["my_rating"].mean()), 2)
        gap = abs(mine - crowd)
        verdict = ("You thought you were unusual. You are exactly average." if gap < 0.15
                   else "You are noticeably kinder than the crowd." if mine > crowd
                   else "You are noticeably harsher than the crowd.")
        pool = [f"{v:.2f}" for v in (crowd - 0.5, crowd + 0.45, crowd + 0.9) if v != mine][:3]
        options, answer = _shuffled(f"{mine:.2f}", pool, rng)
        questions.append(_question(
            "mirror", "The mirror",
            f"Letterboxd averages {crowd:.2f} across these films. And you?",
            options, answer,
            f"You {mine:.2f}, the crowd {crowd:.2f}. {verdict}",
            "amber", kind="rating",
        ))

    # --- Cast: three names, one film ---
    cast_rows = df[df["Actors"].apply(lambda a: isinstance(a, list) and len(a) >= 3)]
    if len(cast_rows) >= 4:
        pick = cast_rows.sample(1, random_state=seed or 0).iloc[0]
        others = cast_rows[cast_rows["title_of_movie"] != pick["title_of_movie"]]
        distractors = others.sample(min(3, len(others)), random_state=seed or 0)["title_of_movie"].tolist()
        options, answer = _shuffled(pick["title_of_movie"], distractors, rng)
        questions.append(_question(
            "cast", "The cast list",
            "These three names share which film?",
            options, answer,
            f"{pick['title_of_movie']} — {pick.get('Director') or 'director unknown'}",
            "violet", kind="cast",
        ))
        questions[-1]["hint"] = pick["Actors"][:3]

    return questions
