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
              poster=None, slider=None, items=None, order=None):
    """
    `kind` picks the card layout on the client. It describes what the *answer*
    is, so a run of questions varies in shape instead of repeating one template:

        number  a count or percentage — set as large figures
        rating  a star value — drawn as stars
        person  a director or actor name
        title   a film title
        poster  the film's poster is the subject, shown large
        cast    a list of actors is the prompt material
        slider  a magnitude guessed by dragging; `answer` is the real value
                itself (not an option index) and `slider` describes the widget
        rank    a small set ordered by dragging/tapping; `items` is the
                shuffled display order and `order` holds the indices into
                `items` that put it back in the correct order, best first
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
        "slider": slider,
        "items": items,
        "order": order,
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
        questions.append(_question(
            "generosity", "The generosity test",
            "What share of your ratings did you let off easy with 3.5 stars or more? Drag to guess.",
            [], generous,
            f"{int((rated >= 3.5).sum())} of {len(rated)} rated films cleared the bar. "
            + ("You're not a critic. You're a hype man." if generous >= 65 else
               "You'd make a film cry." if generous <= 45 else "You grade on a real curve. Suspicious."),
            "amber", kind="slider",
            slider={"min": 0, "max": 100, "step": 1, "format": "percent", "tolerance": 10},
        ))

        # --- Signature rating: the score you hand out on autopilot ---
        mode = float(rated.mode()[0])
        share = round(float((rated == mode).mean() * 100))
        pool = [f"{v:.1f}" for v in (2.5, 3.0, 3.5, 4.0, 4.5) if v != mode]
        rng.shuffle(pool)
        options, answer = _shuffled(f"{mode:.1f}", pool[:3], rng)
        questions.append(_question(
            "signature", "Your signature score",
            "If your ratings had a favorite number, what would it be?",
            options, answer,
            f"{int((rated == mode).sum())} films, {pct(share)} of your whole library, parked on one number. "
            "Efficient. Lazy. Possibly both.",
            "violet", kind="rating",
        ))

        # --- Five stars: scarcity as self-portrait ---
        fives = int((rated == 5.0).sum())
        options, answer = _shuffled(str(fives), _spread(fives, rng, lo=0), rng)
        questions.append(_question(
            "fivestars", "Five-star scarcity",
            "How many films have you crowned with a full five stars?",
            options, answer,
            f"{fives} films made the cut — {pct(fives / len(rated) * 100)} of your rated library. "
            "A very exclusive club.",
            "crimson", kind="number",
        ))

    # --- Decade: which era raised you, in order ---
    years = df["Release_Year"].dropna()
    if len(years) >= 20:
        decades = (years // 10 * 10).astype(int).value_counts()
        if len(decades) >= 4:
            top4 = decades.head(4)
            labels = [f"{int(d)}s" for d in top4.index]  # correct order, most -> least
            shuffle_map = list(range(4))
            rng.shuffle(shuffle_map)                      # shuffle_map[pos] = true rank shown at pos
            items = [labels[r] for r in shuffle_map]
            order = sorted(range(4), key=lambda pos: shuffle_map[pos])
            questions.append(_question(
                "decade", "Which decade raised you?",
                "Rank these four decades by how much of your library they make up — most to least.",
                [], None,
                f"{int(top4.iloc[0])} films. Your taste has a birth year. "
                + " · ".join(f"{l} {int(c)}" for l, c in zip(labels, top4)),
                "sky", kind="rank", items=items, order=order,
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
            "Of these four films you've actually seen, which one is the oldest?",
            options, answer,
            f"{oldest.iloc[0]['title_of_movie']} — {int(oldest.iloc[0]['Release_Year'])}. "
            "Yes, you really watched all four of these.",
            "amber", kind="title",
        ))

    # --- Unrated: the ones you never had an opinion about ---
    unrated = int(df["my_rating"].isna().sum())
    if unrated >= 5:
        options, answer = _shuffled(str(unrated), _spread(unrated, rng, lo=1), rng)
        questions.append(_question(
            "unrated", "The ones you never scored",
            f"Of the {len(df)} films you logged, how many couldn't be bothered to get a rating?",
            options, answer,
            f"One in every {round(len(df) / unrated)} films got total silence from you. Cold.",
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
            "Which director have you basically adopted?",
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
            "Plot twist: which director do you actually rate the highest?",
            options, answer,
            f"{best} — {int(eligible.iloc[0]['size'])} films, averaging "
            f"{eligible.iloc[0]['mean']:.2f}. The one you watch most was {counts.index[0]}. "
            "Turns out loyalty and taste aren't the same thing.",
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
                f"You've sat through {worst_n} films by {worst} anyway. What do you even give them?",
                options, answer,
                f"{worst_n} films. Averaging {worst_avg:.2f}. That's not a habit. That's a situationship.",
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
            f"The internet gave “{worst['title_of_movie']}” a "
            f"{worst['average_rating']:.2f}, crying tears of joy. What did you give it?",
            options, answer,
            f"Everyone else was sobbing with happiness. You gave it {worst['my_rating']:.1f} — "
            f"a {abs(worst['diff']):.2f}-star gap of pure contrarianism.",
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
            "How many countries has your watchlist actually visited?",
            options, answer,
            f"{total} countries on the passport. {pct(share)} of the actual trips were to "
            "just three, though: " + " · ".join(f"{n} {c}" for n, c in top3),
            "mint", kind="number",
        ))

    # --- Mirror: the humbling one ---
    if len(contrast) >= 20:
        crowd = round(float(contrast["average_rating"].mean()), 2)
        mine = round(float(contrast["my_rating"].mean()), 2)
        gap = abs(mine - crowd)
        verdict = ("You thought your taste was special. It's extremely average." if gap < 0.15
                   else "You rate kinder than the crowd. A soft touch." if mine > crowd
                   else "You rate harsher than the crowd. Nobody's safe.")
        pool = [f"{v:.2f}" for v in (crowd - 0.5, crowd + 0.45, crowd + 0.9) if v != mine][:3]
        options, answer = _shuffled(f"{mine:.2f}", pool, rng)
        questions.append(_question(
            "mirror", "The mirror",
            f"The crowd rates these films {crowd:.2f} on average. Where do you land?",
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
            "These three actors all showed up in the same film. Which one?",
            options, answer,
            f"{pick['title_of_movie']}, directed by {pick.get('Director') or 'director unknown'}. "
            "You knew that instantly, right?",
            "violet", kind="cast",
        ))
        questions[-1]["hint"] = pick["Actors"][:3]

    return questions
