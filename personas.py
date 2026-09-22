"""
Viewer personas
===============
Assigns a library to one of a handful of viewer types.

This is segmentation without a population. Real k-means clusters *users*, and
this app never holds more than one at a time: no database, sessions that
expire in thirty days. So the centroids are written down here by hand and
only the assignment step of k-means runs. Describe the library as a point in
feature space, find the nearest persona, report how near. That makes the
answer deterministic and explainable, which a model fitted on a few hundred
anonymous vectors would not be.

Every feature is a share in [0, 1] so the distance is not dominated by
whichever axis happens to have the biggest numbers.
"""

import numpy as np
import pandas as pd

# Fewer than this many films with metadata and a persona is a guess.
MIN_FILMS = 20

# Feature order is the axis order everywhere: the radar chart, the centroids,
# the distance. Change it here and nowhere else.
FEATURES = [
    "blockbuster", "drama", "dark", "light", "foreign", "vintage",
    "obscure", "generous", "contrarian", "fresh", "long",
]

# Plain-English axis labels for the chart.
LABELS = {
    "blockbuster": "Blockbuster",
    "drama": "Drama",
    "dark": "Dark genres",
    "light": "Light genres",
    "foreign": "Non-English",
    "vintage": "Pre-1990",
    "obscure": "Obscure",
    "generous": "Generous",
    "contrarian": "Contrarian",
    "fresh": "New releases",
    "long": "Long films",
}

GENRE_GROUPS = {
    "blockbuster": {"Action", "Adventure", "Science Fiction", "Fantasy"},
    "drama": {"Drama", "Romance", "History", "War"},
    "dark": {"Horror", "Thriller", "Crime", "Mystery"},
    "light": {"Comedy", "Animation", "Family", "Music"},
}

VINTAGE_BEFORE = 1990
OBSCURE_UNDER = 100_000      # Letterboxd ratings
LONG_OVER = 130              # minutes
FRESH_WITHIN_YEARS = 2

# How sharply closeness falls off with distance. Smaller = the winner takes
# more of the percentage; 0.15 leaves a visible runner-up on real libraries.
TEMPERATURE = 0.15

PERSONAS = [
    {
        "name": "The Arthouse Cinephile",
        "tagline": "Subtitles on, crowd ignored.",
        "centroid": dict(blockbuster=.15, drama=.65, dark=.30, light=.20, foreign=.55,
                         vintage=.30, obscure=.45, generous=.55, contrarian=.55, fresh=.15, long=.40),
    },
    {
        "name": "The Multiplex Regular",
        "tagline": "Opening weekend is a plan.",
        "centroid": dict(blockbuster=.55, drama=.40, dark=.35, light=.40, foreign=.08,
                         vintage=.05, obscure=.08, generous=.75, contrarian=.35, fresh=.40, long=.30),
    },
    {
        "name": "The Genre Devotee",
        "tagline": "The darker the better.",
        "centroid": dict(blockbuster=.30, drama=.35, dark=.70, light=.20, foreign=.15,
                         vintage=.15, obscure=.30, generous=.55, contrarian=.45, fresh=.25, long=.20),
    },
    {
        "name": "The Archivist",
        "tagline": "Nothing before 1990 is old to you.",
        "centroid": dict(blockbuster=.20, drama=.60, dark=.35, light=.35, foreign=.30,
                         vintage=.55, obscure=.30, generous=.70, contrarian=.35, fresh=.05, long=.35),
    },
    {
        "name": "The Comfort Watcher",
        "tagline": "Films are for feeling good.",
        "centroid": dict(blockbuster=.40, drama=.35, dark=.15, light=.65, foreign=.08,
                         vintage=.10, obscure=.10, generous=.80, contrarian=.30, fresh=.30, long=.15),
    },
]

# One sentence per axis, given the user's share as a whole percentage. These
# are the "why" lines under the verdict.
EVIDENCE = {
    "blockbuster": "{pct}% of your films are action, adventure, sci-fi or fantasy",
    "drama": "{pct}% of your films are dramas, romances, histories or war films",
    "dark": "{pct}% of your films are horror, thrillers, crime or mysteries",
    "light": "{pct}% of your films are comedies, animation, family films or musicals",
    "foreign": "{pct}% of your films are not in English",
    "vintage": "{pct}% of your films were released before " + str(VINTAGE_BEFORE),
    "obscure": "{pct}% of your films have under " + str(OBSCURE_UNDER // 1000) + "k ratings on Letterboxd",
    "generous": "{pct}% of your ratings are three and a half stars or more",
    "contrarian": "you sit {gap} stars from the crowd on an average film",
    "fresh": "{pct}% of your films were logged within " + str(FRESH_WITHIN_YEARS) + " years of release",
    "long": "{pct}% of your films run over " + str(LONG_OVER) + " minutes",
}

# When the persona is defined by having *little* of an axis, the same number
# needs an "only" in front of it, and the crowd line is a different sentence.
EVIDENCE_LOW = {
    key: "only " + text for key, text in EVIDENCE.items() if key != "contrarian"
}
EVIDENCE_LOW["contrarian"] = "you stay within {gap} stars of the crowd on an average film"


def user_features(df: pd.DataFrame) -> dict | None:
    """The library as a point in feature space, or None if too little is known.

    `df` is expected cleaned (analyzer.clean_dataset): list columns parsed,
    numerics numeric, Watch_Date a datetime.
    """
    # Imported here rather than at the top: analyzer imports this module.
    from analyzer import drop_bulk_import_days

    genres = df["genre_of_movie"] if "genre_of_movie" in df.columns else pd.Series([[]] * len(df), index=df.index)
    known = df[genres.apply(lambda g: isinstance(g, list) and len(g) > 0)]
    if len(known) < MIN_FILMS:
        return None

    def share(mask) -> float:
        return round(float(mask.mean()), 3) if len(mask) else 0.0

    f = {}
    for key, group in GENRE_GROUPS.items():
        f[key] = share(known["genre_of_movie"].apply(lambda g: bool(group & set(g))))

    langs = known["Language"] if "Language" in known.columns else pd.Series([[]] * len(known), index=known.index)
    with_lang = langs[langs.apply(lambda l: isinstance(l, list) and len(l) > 0)]
    f["foreign"] = share(with_lang.apply(lambda l: l[0] != "English"))

    years = pd.to_numeric(known.get("Release_Year"), errors="coerce").dropna()
    f["vintage"] = share(years < VINTAGE_BEFORE)

    counts = pd.to_numeric(known.get("Watched_number"), errors="coerce").dropna()
    f["obscure"] = share(counts < OBSCURE_UNDER)

    mine = pd.to_numeric(known.get("my_rating"), errors="coerce").dropna()
    f["generous"] = share(mine >= 3.5)

    both = known.dropna(subset=["my_rating", "average_rating"])
    gap = float((both["my_rating"] - both["average_rating"]).abs().mean()) if len(both) else 0.0
    f["contrarian"] = round(min(gap, 1.0), 3)

    # Age at logging, on the same import-day-free basis as the backlog chart.
    sub, _, _ = drop_bulk_import_days(known)
    sub = sub.dropna(subset=["Watch_Date", "Release_Year"])
    if len(sub):
        age = sub["Watch_Date"].dt.year - sub["Release_Year"]
        f["fresh"] = share(age <= FRESH_WITHIN_YEARS)
    else:
        f["fresh"] = 0.0

    runtime = pd.to_numeric(known.get("Runtime_minutes"), errors="coerce").dropna()
    f["long"] = share(runtime > LONG_OVER)

    f["_films"] = int(len(known))
    f["_gap"] = round(gap, 2)
    return f


def _vec(d: dict) -> np.ndarray:
    return np.array([d[k] for k in FEATURES], dtype=float)


def assign(features: dict) -> dict:
    """Nearest persona to a feature point, with closeness to every persona."""
    x = _vec(features)
    centroids = np.stack([_vec(p["centroid"]) for p in PERSONAS])
    dist = np.linalg.norm(centroids - x, axis=1)

    weights = np.exp(-dist / TEMPERATURE)
    pct = weights / weights.sum() * 100
    order = np.argsort(dist)
    win, second = order[0], order[1]
    winner = PERSONAS[win]

    # The axes that make this persona this persona (where its centroid stands
    # apart from the others) on which the library leans the same way.
    others = np.delete(centroids, win, axis=0).mean(axis=0)
    pull = (centroids[win] - others) * (x - others)
    evidence = []
    for i in np.argsort(-pull):
        if pull[i] <= 0 or len(evidence) == 3:
            break
        key = FEATURES[i]
        template = (EVIDENCE if centroids[win][i] >= others[i] else EVIDENCE_LOW)[key]
        evidence.append(template.format(pct=round(x[i] * 100), gap=features.get("_gap", 0)))

    return {
        "name": winner["name"],
        "tagline": winner["tagline"],
        "match_pct": int(round(pct[win])),
        "runner_up": {"name": PERSONAS[second]["name"], "pct": int(round(pct[second]))},
        "ranking": [{"name": PERSONAS[i]["name"], "pct": int(round(pct[i]))} for i in order],
        "axes": [LABELS[k] for k in FEATURES],
        "features": [round(float(v), 3) for v in x],
        "centroid": [float(v) for v in centroids[win]],
        "evidence": evidence,
        "films_used": features.get("_films", 0),
    }


def viewer_persona(df: pd.DataFrame) -> dict | None:
    features = user_features(df)
    return assign(features) if features else None


def _selfcheck() -> None:
    """Every centroid must win its own persona, or the set overlaps too much."""
    for p in PERSONAS:
        got = assign({**p["centroid"], "_films": 100, "_gap": 0.4})
        assert got["name"] == p["name"], f"{p['name']} lost to {got['name']}"
    print("ok:", ", ".join(p["name"] for p in PERSONAS))


if __name__ == "__main__":
    _selfcheck()
