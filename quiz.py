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

# Turkish possessive suffix depends on how the number is *pronounced*, so it
# comes off the last spoken part: 36 -> "otuz altısı", 40 -> "kırkı".
_SUFFIX_ONES = {1: "i", 2: "si", 3: "ü", 4: "ü", 5: "i",
                6: "sı", 7: "si", 8: "i", 9: "u"}
_SUFFIX_TENS = {10: "u", 20: "si", 30: "u", 40: "ı", 50: "si",
                60: "ı", 70: "i", 80: "i", 90: "ı", 100: "ü"}


def pct(n) -> str:
    """Format a percentage with the right Turkish possessive suffix."""
    n = int(round(n))
    ones = n % 10
    if ones:
        suffix = _SUFFIX_ONES[ones]
    else:
        suffix = _SUFFIX_TENS.get(n if n <= 100 else n % 100 or 100, "ı")
    return f"%{n}'{suffix}"


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
    return [f"%{v}" if as_pct else str(v) for v in out]


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
        options, answer = _shuffled(f"%{generous}", _spread(generous, rng, lo=5, hi=99, as_pct=True), rng)
        questions.append(_question(
            "generosity", "Cömertlik testi",
            "Puanladığın filmlerin yüzde kaçına 3.5 ve üstü verdin?",
            options, answer,
            f"{len(rated)} filmden {int((rated >= 3.5).sum())} tanesi geçer not aldı. "
            + ("Sen eleştirmen değilsin, hayransın." if generous >= 65 else
               "Kolay beğenmiyorsun." if generous <= 45 else "Dengeli bir izleyicisin."),
            "amber", kind="number",
        ))

        # --- Signature rating: the score you hand out on autopilot ---
        mode = float(rated.mode()[0])
        share = round(float((rated == mode).mean() * 100))
        pool = [f"{v:.1f}" for v in (2.5, 3.0, 3.5, 4.0, 4.5) if v != mode]
        rng.shuffle(pool)
        options, answer = _shuffled(f"{mode:.1f}", pool[:3], rng)
        questions.append(_question(
            "signature", "İmza puanın",
            "En sık verdiğin puan hangisi?",
            options, answer,
            f"{int((rated == mode).sum())} film — tüm puanlarının {pct(share)} tek bir değerde toplanmış.",
            "violet", kind="rating",
        ))

        # --- Five stars: scarcity as self-portrait ---
        fives = int((rated == 5.0).sum())
        options, answer = _shuffled(str(fives), _spread(fives, rng, lo=0), rng)
        questions.append(_question(
            "fivestars", "Beş yıldız cimriliği",
            "Kaç filme 5 yıldız verdin?",
            options, answer,
            f"Puanladıklarının {pct(fives / len(rated) * 100)}. "
            f"Beş yıldızı hak eden {fives} film.",
            "crimson", kind="number",
        ))

    # --- Decade: which era raised you ---
    years = df["Release_Year"].dropna()
    if len(years) >= 20:
        decades = (years // 10 * 10).astype(int).value_counts()
        top = int(decades.index[0])
        others = [f"{int(d)}'ler" for d in decades.index[1:4]]
        options, answer = _shuffled(f"{top}'ler", others, rng)
        questions.append(_question(
            "decade", "Hangi on yılın çocuğusun?",
            "Kütüphanenin çoğunluğu hangi on yıldan?",
            options, answer,
            f"{int(decades.iloc[0])} film. "
            + " · ".join(f"{int(d)}'ler {int(c)}" for d, c in decades.head(4).items()),
            "sky", kind="number",
        ))

    # --- Time capsule: all four are films they actually watched ---
    oldest = df.dropna(subset=["Release_Year"]).nsmallest(4, "Release_Year")
    if len(oldest) == 4:
        titles = [f"{r['title_of_movie']} ({int(r['Release_Year'])})" for _, r in oldest.iterrows()]
        correct = titles[0]
        options, answer = _shuffled(correct, titles[1:], rng)
        questions.append(_question(
            "oldest", "Zaman kapsülü",
            "İzlediğin en eski film hangisi?",
            options, answer,
            f"{oldest.iloc[0]['title_of_movie']} — {int(oldest.iloc[0]['Release_Year'])}. "
            "Dördü de senin izlediğin filmler.",
            "amber", kind="title",
        ))

    # --- Unrated: the ones you never had an opinion about ---
    unrated = int(df["my_rating"].isna().sum())
    if unrated >= 5:
        options, answer = _shuffled(str(unrated), _spread(unrated, rng, lo=1), rng)
        questions.append(_question(
            "unrated", "Yarım kalanlar",
            f"{len(df)} filmden kaçını puanlamadan bıraktın?",
            options, answer,
            f"Her {round(len(df) / unrated)} filmden biri sessiz kaldı.",
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
            "most_watched_dir", "Sadakat",
            "En çok filmini izlediğin yönetmen kim?",
            options, answer,
            f"{most} — {int(counts.iloc[0])} film. " + " · ".join(
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
            "favourite_dir", "Ama gerçek favorin",
            "Peki en yüksek ortalamayı verdiğin yönetmen?",
            options, answer,
            f"{best} — {int(eligible.iloc[0]['size'])} film, ortalama "
            f"{eligible.iloc[0]['mean']:.2f}. En çok izlediğin {counts.index[0]} idi.",
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
                "toxic", "İlişki durumu: karmaşık",
                f"{worst} adlı yönetmenin {worst_n} filmini izlemişsin. Ortalama kaç verdin?",
                options, answer,
                f"{worst_n} film. Ortalama {worst_avg:.2f}. Bu bir ilişki mi, bağımlılık mı?",
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
            "confession", "İtiraf vakti",
            f"Letterboxd kitlesi «{worst['title_of_movie']}» filmine "
            f"{worst['average_rating']:.2f} verdi. Sen kaç verdin?",
            options, answer,
            f"Dünya bayıldı, sen {worst['my_rating']:.1f} verdin. "
            f"Aradaki fark {abs(worst['diff']):.2f} puan.",
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
            "passport", "Sinema pasaportun",
            "İzlediğin filmler kaç farklı ülkeden?",
            options, answer,
            f"{total} ülke. Ama {pct(share)} sadece üçünden: "
            + " · ".join(f"{n} {c}" for n, c in top3),
            "mint", kind="number",
        ))

    # --- Mirror: the humbling one ---
    if len(contrast) >= 20:
        crowd = round(float(contrast["average_rating"].mean()), 2)
        mine = round(float(contrast["my_rating"].mean()), 2)
        gap = abs(mine - crowd)
        verdict = ("Kendini özel sanıyordun. Sen tam olarak ortalamasın." if gap < 0.15
                   else "Kitleden belirgin şekilde cömertsin." if mine > crowd
                   else "Kitleden belirgin şekilde sertsin.")
        pool = [f"{v:.2f}" for v in (crowd - 0.5, crowd + 0.45, crowd + 0.9) if v != mine][:3]
        options, answer = _shuffled(f"{mine:.2f}", pool, rng)
        questions.append(_question(
            "mirror", "Ayna",
            f"Letterboxd kitlesi bu filmlere ortalama {crowd:.2f} verdi. Ya sen?",
            options, answer,
            f"Sen {mine:.2f}, kitle {crowd:.2f}. {verdict}",
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
            "cast", "Kadro",
            "Bu üç isim hangi filmde bir arada?",
            options, answer,
            f"{pick['title_of_movie']} — {pick.get('Director') or 'bilinmeyen yönetmen'}",
            "violet", kind="cast",
        ))
        questions[-1]["hint"] = pick["Actors"][:3]

    return questions
