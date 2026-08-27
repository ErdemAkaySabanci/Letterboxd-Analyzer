"""
Letterboxd Data Analyzer 2.0 (Advanced Data Science Features)
=============================================================
Statistical analysis, Bayesian averages, controversial movies, 
temporal trends, network analysis, and more.
"""

import ast
import pandas as pd
import numpy as np
from scipy.stats import pearsonr, chi2_contingency
from collections import Counter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean_dataset(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure proper types and handle missing data."""
    df = df.copy()
    
    numeric_cols = ["my_rating", "average_rating", "Runtime_minutes", "Watched_number", "Release_Year"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            
    # Parse list columns if they are strings
    list_cols = ["genre_of_movie", "Actors", "Country", "Language"]
    for col in list_cols:
        if col in df.columns:
            df[col] = df[col].apply(
                lambda x: ast.literal_eval(x) if isinstance(x, str) and x.startswith("[") else (x if isinstance(x, list) else [])
            )
            
    # Watch date handling
    if "Watch_Date" in df.columns:
        df["Watch_Date"] = pd.to_datetime(df["Watch_Date"], errors="coerce")
        
    return df

def bayesian_average(ratings, C, m):
    """
    Calculate Bayesian average: (v / (v + m)) * R + (m / (v + m)) * C
    Where:
    - R: average rating for the item
    - v: number of ratings for the item
    - m: minimum ratings required to be listed
    - C: mean rating across the whole report
    """
    v = len(ratings)
    if v == 0:
        return None
    R = sum(ratings) / v
    return (v / (v + m)) * R + (m / (v + m)) * C

# ---------------------------------------------------------------------------
# 1. Advanced Statistical Depth
# ---------------------------------------------------------------------------

def bayesian_director_analysis(df: pd.DataFrame, m: int = 1) -> dict:
    """Calculate Bayesian averages for directors."""
    sub = df.dropna(subset=["Director", "my_rating"]).copy()
    if sub.empty:
        return {"directors": []}
        
    global_mean = sub["my_rating"].mean()
    
    directors = {}
    for _, row in sub.iterrows():
        d = row["Director"]
        if d not in directors:
            directors[d] = {"ratings": [], "people_ratings": [], "total_watched": 0}
        directors[d]["ratings"].append(row["my_rating"])
        if pd.notna(row["average_rating"]):
            directors[d]["people_ratings"].append(row["average_rating"])
        if pd.notna(row["Watched_number"]):
            directors[d]["total_watched"] += row["Watched_number"]
            
    result = []
    for d, data in directors.items():
        v = len(data["ratings"])
        if v >= m:
            bayesian_avg = bayesian_average(data["ratings"], global_mean, m)
            my_avg = sum(data["ratings"]) / v
            people_avg = sum(data["people_ratings"]) / len(data["people_ratings"]) if data["people_ratings"] else None
            
            result.append({
                "director": d,
                "movie_count": v,
                "bayesian_avg": round(bayesian_avg, 2),
                "my_avg": round(my_avg, 2),
                "people_avg": round(people_avg, 2) if people_avg else None,
                "popularity_millions": round(data["total_watched"] / v / 1_000_000, 3)
            })
            
    # Sort by Bayesian average
    result = sorted(result, key=lambda x: x["bayesian_avg"], reverse=True)
    return {"directors": result, "global_mean": round(global_mean, 2)}

def bayesian_actor_analysis(df: pd.DataFrame, m: int = 1) -> dict:
    """Calculate Bayesian averages for actors."""
    sub = df.dropna(subset=["my_rating"]).copy()
    if "Actors" not in sub.columns or sub.empty:
        return {"actors": []}
        
    global_mean = sub["my_rating"].mean()
    
    actors = {}
    for _, row in sub.iterrows():
        for a in row["Actors"]:
            if not isinstance(a, str): continue
            if a not in actors:
                actors[a] = {"ratings": []}
            actors[a]["ratings"].append(row["my_rating"])
            
    result = []
    for a, data in actors.items():
        v = len(data["ratings"])
        if v >= m:
            bayesian_avg = bayesian_average(data["ratings"], global_mean, m)
            my_avg = sum(data["ratings"]) / v
            
            result.append({
                "actor": a,
                "movie_count": v,
                "bayesian_avg": round(bayesian_avg, 2),
                "my_avg": round(my_avg, 2),
            })
            
    result = sorted(result, key=lambda x: x["bayesian_avg"], reverse=True)
    return {"actors": result}

def controversial_movies(df: pd.DataFrame, top_n: int = 10) -> dict:
    """Movies with largest difference between my_rating and average_rating."""
    sub = df.dropna(subset=["my_rating", "average_rating"]).copy()
    sub["diff"] = sub["my_rating"] - sub["average_rating"]
    sub["abs_diff"] = sub["diff"].abs()
    
    sub = sub.sort_values("abs_diff", ascending=False)
    
    result = []
    for _, row in sub.head(top_n).iterrows():
        result.append({
            "title": row["title_of_movie"],
            "my_rating": float(row["my_rating"]),
            "average_rating": float(row["average_rating"]),
            "diff": round(row["diff"], 2),
            "abs_diff": round(row["abs_diff"], 2)
        })
    return {"controversial": result}


# ---------------------------------------------------------------------------
# 2. Temporal Analysis
# ---------------------------------------------------------------------------

def temporal_evolution(df: pd.DataFrame) -> dict:
    """Average rating and count per year/month of watching."""
    if "Watch_Date" not in df.columns:
        return {"error": "No Watch_Date column"}
        
    sub = df.dropna(subset=["Watch_Date", "my_rating"]).copy()
    sub["watch_year"] = sub["Watch_Date"].dt.year
    sub["watch_month"] = sub["Watch_Date"].dt.month
    
    yearly = sub.groupby("watch_year").agg(
        count=("my_rating", "size"),
        avg_rating=("my_rating", "mean")
    ).reset_index()
    
    return {
        "years": yearly["watch_year"].tolist(),
        "counts": yearly["count"].tolist(),
        "avg_ratings": [round(x, 2) for x in yearly["avg_rating"].tolist()]
    }

def binge_habits(df: pd.DataFrame) -> dict:
    """Count of movies watched by month (1-12)."""
    if "Watch_Date" not in df.columns:
        return {"error": "No Watch_Date column"}
        
    sub = df.dropna(subset=["Watch_Date"]).copy()
    sub["watch_month"] = sub["Watch_Date"].dt.month
    
    counts = sub["watch_month"].value_counts().sort_index()
    
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    
    return {
        "months": months,
        "counts": [int(counts.get(i, 0)) for i in range(1, 13)]
    }

def backlog_analysis(df: pd.DataFrame) -> dict:
    """Release Year vs Watch Year."""
    if "Watch_Date" not in df.columns or "Release_Year" not in df.columns:
        return {"error": "Missing columns"}
        
    sub = df.dropna(subset=["Watch_Date", "Release_Year"]).copy()
    sub["watch_year"] = sub["Watch_Date"].dt.year
    sub["age_when_watched"] = sub["watch_year"] - sub["Release_Year"]
    
    # Categorize
    bins = [-999, 0, 2, 10, 30, 999]
    labels = ["Release Year", "Recent (1-2y)", "Decade (3-10y)", "Classic (11-30y)", "Old (31y+)"]
    sub["age_category"] = pd.cut(sub["age_when_watched"], bins=bins, labels=labels)
    
    counts = sub["age_category"].value_counts().reindex(labels)
    return {
        "categories": labels,
        "counts": counts.values.tolist(),
        "avg_age": round(sub["age_when_watched"].mean(), 1)
    }

# ---------------------------------------------------------------------------
# 3. Network & Diversity
# ---------------------------------------------------------------------------

def diversity_analysis(df: pd.DataFrame) -> dict:
    """Analyze countries and languages."""
    countries = []
    languages = []
    
    for _, row in df.iterrows():
        if isinstance(row.get("Country"), list):
            countries.extend(row["Country"])
        if isinstance(row.get("Language"), list):
            languages.extend(row["Language"])
            
    country_counts = Counter(countries).most_common(10)
    language_counts = Counter(languages).most_common(10)
    
    return {
        "top_countries": [{"name": k, "count": v} for k, v in country_counts],
        "top_languages": [{"name": k, "count": v} for k, v in language_counts]
    }

def actor_director_network(df: pd.DataFrame) -> dict:
    """Find most frequent actor-director collaborations in watched list."""
    sub = df.dropna(subset=["Director"]).copy()
    collabs = []
    
    for _, row in sub.iterrows():
        d = row["Director"]
        acts = row.get("Actors", [])
        if isinstance(acts, list):
            for a in acts:
                if isinstance(a, str):
                    collabs.append(f"{a} & {d}")
                    
    counts = Counter(collabs).most_common(10)
    return {
        "top_collaborations": [{"pair": k, "count": v} for k, v in counts]
    }

# ---------------------------------------------------------------------------
# 4. Restored Basic Stats
# ---------------------------------------------------------------------------

def rating_distribution(df: pd.DataFrame) -> dict:
    counts = df["my_rating"].value_counts().sort_index()
    return {"ratings": [float(r) for r in counts.index], "counts": counts.values.tolist()}

def add_runtime_intervals(df: pd.DataFrame) -> pd.DataFrame:
    bins = [0, 89, 119, 150, 9999]
    labels = ["0–89 min", "90–119 min", "120–150 min", "151+ min"]
    df = df.copy()
    df["Runtime_interval"] = pd.cut(df["Runtime_minutes"], bins=bins, labels=labels, right=True)
    return df

def runtime_interval_counts(df: pd.DataFrame) -> dict:
    df = add_runtime_intervals(df)
    counts = df["Runtime_interval"].value_counts().sort_index()
    return {"labels": [str(x) for x in counts.index.tolist()], "values": counts.values.tolist()}

def runtime_interval_avg_rating(df: pd.DataFrame) -> dict:
    df = add_runtime_intervals(df)
    avg = df.groupby("Runtime_interval", observed=True)["my_rating"].mean()
    return {"labels": [str(x) for x in avg.index.tolist()], "values": [round(v, 2) if pd.notna(v) else None for v in avg.values.tolist()]}

def chi_square_runtime_rating(df: pd.DataFrame) -> dict:
    df = df.dropna(subset=["Runtime_minutes", "my_rating"]).copy()
    if df.empty: return {"chi2": None, "p_value": None, "dof": None, "significant": False}
    df["rating_bin"] = pd.cut(df["my_rating"], bins=[0, 1, 2, 3, 4, 5])
    df = add_runtime_intervals(df)
    ct = pd.crosstab(df["Runtime_interval"], df["rating_bin"])
    try:
        chi2, p, dof, expected = chi2_contingency(ct)
        return {"chi2": round(chi2, 4), "p_value": round(p, 6), "dof": int(dof), "significant": bool(p < 0.05)}
    except:
        return {"chi2": None, "p_value": None, "dof": None, "significant": False}

def correlation_my_vs_avg(df: pd.DataFrame) -> dict:
    sub = df[["my_rating", "average_rating"]].dropna()
    if len(sub) < 3: return {"r": None, "p": None, "n": len(sub)}
    r, p = pearsonr(sub["my_rating"], sub["average_rating"])
    return {"r": round(r, 4), "p": round(p, 6), "n": len(sub), "significant": bool(p < 0.05)}

def correlation_matrix(df: pd.DataFrame) -> dict:
    cols = ["my_rating", "average_rating", "Runtime_minutes", "Watched_number"]
    cols = [c for c in cols if c in df.columns]
    sub = df[cols].apply(pd.to_numeric, errors="coerce").dropna()
    corr = sub.corr()
    return {"columns": corr.columns.tolist(), "matrix": corr.round(3).values.tolist()}

def genre_distribution(df: pd.DataFrame) -> dict:
    if "genre_of_movie" not in df.columns: return {"genres": []}
    exploded = df.explode("genre_of_movie").dropna(subset=["genre_of_movie"])
    counts = exploded.groupby("genre_of_movie").agg(
        count=("my_rating", "size"),
        avg_my_rating=("my_rating", "mean"),
        avg_user_rating=("average_rating", lambda x: pd.to_numeric(x, errors="coerce").mean()),
    ).sort_values("count", ascending=False)
    result = []
    for genre, row in counts.iterrows():
        result.append({
            "genre": genre,
            "count": int(row["count"]),
            "avg_my_rating": round(row["avg_my_rating"], 2),
            "avg_user_rating": round(row["avg_user_rating"], 2) if not pd.isna(row["avg_user_rating"]) else None,
        })
    return {"genres": result}

def director_analysis(df: pd.DataFrame, min_movies: int = 3) -> dict:
    if "Director" not in df.columns: return {"directors": [], "min_movies": min_movies}
    sub = df.dropna(subset=["Director", "my_rating"]).copy()
    grouped = sub.groupby("Director").agg(
        movie_count=("my_rating", "size"),
        my_avg=("my_rating", "mean"),
        people_avg=("average_rating", "mean"),
    )
    filtered = grouped[grouped["movie_count"] >= min_movies].copy()
    result = []
    for director, row in filtered.iterrows():
        result.append({
            "director": director,
            "movie_count": int(row["movie_count"]),
            "my_avg": round(row["my_avg"], 2),
            "people_avg": round(row["people_avg"], 2) if not pd.isna(row["people_avg"]) else None,
        })
    return {"directors": result, "min_movies": min_movies}

def director_correlation(df: pd.DataFrame, min_movies: int = 3) -> dict:
    analysis = director_analysis(df, min_movies)
    directors = analysis["directors"]
    if len(directors) < 3: return {"r": None, "p": None, "n": len(directors)}
    my_avgs = [d["my_avg"] for d in directors if d["people_avg"] is not None]
    people_avgs = [d["people_avg"] for d in directors if d["people_avg"] is not None]
    if len(my_avgs) < 3: return {"r": None, "p": None, "n": len(my_avgs)}
    r, p = pearsonr(my_avgs, people_avgs)
    return {"r": round(r, 4), "p": round(p, 6), "n": len(my_avgs), "significant": bool(p < 0.05)}

# ---------------------------------------------------------------------------
# Basic Summary
# ---------------------------------------------------------------------------

def summary_stats(df: pd.DataFrame) -> dict:
    """Quick overview numbers for the dashboard."""
    total = len(df)
    rated = df["my_rating"].notna().sum()
    avg_my = round(df["my_rating"].mean(), 2) if rated > 0 else None
    
    genres_count = len(set(g for sublist in df.get("genre_of_movie", []) if isinstance(sublist, list) for g in sublist))
    directors_count = df["Director"].nunique() if "Director" in df.columns else 0
    
    return {
        "total_movies": int(total),
        "rated_movies": int(rated),
        "avg_my_rating": avg_my,
        "unique_genres": int(genres_count),
        "unique_directors": int(directors_count),
    }

def wrapped_summary(df: pd.DataFrame) -> dict:
    """Pre-compute all data needed for the Wrapped storytelling cards."""
    df = clean_dataset(df)

    total_movies = len(df)
    rated_movies = int(df["my_rating"].notna().sum())
    total_minutes = pd.to_numeric(df.get("Runtime_minutes"), errors="coerce").sum()
    total_hours = round(total_minutes / 60, 1) if pd.notna(total_minutes) else 0
    total_days = round(total_hours / 24, 1)
    avg_my = round(df["my_rating"].mean(), 2) if rated_movies > 0 else None

    # --- Top director (Bayesian, min 2 films) ---
    # Require at least 2 films to call someone a "favorite director".
    # Fall back to min=1 only if the user has no directors with 2+ films.
    bay_dir_2 = bayesian_director_analysis(df, m=2)
    bay_dir_1 = bayesian_director_analysis(df, m=1)
    top_director = None
    dirs_to_use = bay_dir_2["directors"] if bay_dir_2["directors"] else bay_dir_1["directors"]
    if dirs_to_use:
        d = dirs_to_use[0]
        top_director = {
            "name": d["director"],
            "movie_count": d["movie_count"],
            "bayesian_avg": d["bayesian_avg"],
            "my_avg": d["my_avg"],
            "people_avg": d.get("people_avg"),
            "is_estimate": len(bay_dir_2["directors"]) == 0,  # flag if only 1-film dirs available
        }

    # --- Top 5 genres ---
    genre_data = genre_distribution(df)
    top_genres = genre_data["genres"][:5] if genre_data["genres"] else []

    # --- Controversial movies (separate loved vs hated) ---
    cont_df = df.dropna(subset=["my_rating", "average_rating"]).copy()
    cont_df["diff"] = cont_df["my_rating"] - cont_df["average_rating"]

    loved_df = cont_df[cont_df["diff"] > 0].nlargest(3, "diff")
    hated_df = cont_df[cont_df["diff"] < 0].nsmallest(3, "diff")

    loved_by_you = [
        {"title": r["title_of_movie"], "my_rating": float(r["my_rating"]),
         "average_rating": float(r["average_rating"]), "diff": round(r["diff"], 2)}
        for _, r in loved_df.iterrows()
    ]
    hated_by_you = [
        {"title": r["title_of_movie"], "my_rating": float(r["my_rating"]),
         "average_rating": float(r["average_rating"]), "diff": round(r["diff"], 2)}
        for _, r in hated_df.iterrows()
    ]

    # --- Busiest month ---
    binge = binge_habits(df)
    busiest_month = None
    if binge.get("counts"):
        max_idx = binge["counts"].index(max(binge["counts"]))
        busiest_month = {
            "month": binge["months"][max_idx],
            "count": binge["counts"][max_idx],
        }

    # --- Taste evolution ---
    temporal = temporal_evolution(df)

    # --- Watch date range ---
    if "Watch_Date" in df.columns:
        dates = df["Watch_Date"].dropna()
        first_watch = str(dates.min().date()) if len(dates) > 0 else None
        last_watch = str(dates.max().date()) if len(dates) > 0 else None
    else:
        first_watch = last_watch = None

    # --- Unique directors count ---
    unique_directors = df["Director"].nunique() if "Director" in df.columns else 0

    return {
        "total_movies": total_movies,
        "rated_movies": rated_movies,
        "total_hours": total_hours,
        "total_days": total_days,
        "avg_rating": avg_my,
        "unique_directors": unique_directors,
        "top_director": top_director,
        "top_genres": top_genres,
        "loved_by_you": loved_by_you,
        "hated_by_you": hated_by_you,
        "busiest_month": busiest_month,
        "taste_evolution": temporal,
        "binge_months": binge,
        "first_watch": first_watch,
        "last_watch": last_watch,
    }


def full_analysis(df: pd.DataFrame) -> dict:
    """Run every analysis and return a combined dict."""
    df = clean_dataset(df)
    return {
        "summary": summary_stats(df),
        "rating_distribution": rating_distribution(df),
        "runtime_counts": runtime_interval_counts(df),
        "runtime_avg_rating": runtime_interval_avg_rating(df),
        "correlation_matrix": correlation_matrix(df),
        "genre_distribution": genre_distribution(df),
        "director_analysis": director_analysis(df),
        "chi_square": chi_square_runtime_rating(df),
        "correlation_my_vs_avg": correlation_my_vs_avg(df),
        "director_correlation": director_correlation(df),
        "bayesian_directors": bayesian_director_analysis(df),
        "bayesian_actors": bayesian_actor_analysis(df),
        "controversial": controversial_movies(df),
        "temporal_evolution": temporal_evolution(df),
        "binge_habits": binge_habits(df),
        "backlog": backlog_analysis(df),
        "diversity": diversity_analysis(df),
        "network": actor_director_network(df)
    }
