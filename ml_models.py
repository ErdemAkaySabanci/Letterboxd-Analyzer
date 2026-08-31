"""
Letterboxd ML Models & Recommendation Engine
=============================================
Feature engineering, multi-model training, comparison, and movie recommendations.
"""

import warnings
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore", category=FutureWarning)


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------

def _prepare_features(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """
    Build a feature matrix X and target vector y from the movie dataset.

    Features used:
        - average_rating (float)
        - Runtime_minutes (float)
        - Watched_number (float, log-scaled)
        - director_avg_my_rating (float, per-director mean of my_rating)
        - director_movie_count (int, how many films of that director we watched)
        - Release_Year (int, optional)
        - One-hot encoded top genres
        - One-hot encoded top actors
    """
    df = df.copy()
    # Ensure numeric
    for col in ["average_rating", "Runtime_minutes", "Watched_number", "my_rating", "Release_Year"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Drop rows missing the target or key features
    required = ["my_rating", "average_rating", "Runtime_minutes"]
    df = df.dropna(subset=required).reset_index(drop=True)

    # --- Director-level features ---
    if "Director" in df.columns:
        dir_stats = df.groupby("Director")["my_rating"].agg(["mean", "count"])
        dir_stats.columns = ["director_avg_my_rating", "director_movie_count"]
        df = df.merge(dir_stats, left_on="Director", right_index=True, how="left")
    else:
        df["director_avg_my_rating"] = df["my_rating"].mean()
        df["director_movie_count"] = 1

    # --- Log-scaled popularity ---
    if "Watched_number" in df.columns:
        df["log_watched"] = np.log1p(df["Watched_number"].fillna(0))
    else:
        df["log_watched"] = 0.0
        
    # --- Release Year ---
    if "Release_Year" not in df.columns:
        df["Release_Year"] = 2000 # default fallback

    # --- One-hot encode top genres ---
    genre_dummies = pd.DataFrame()
    if "genre_of_movie" in df.columns:
        exploded = df[["genre_of_movie"]].explode("genre_of_movie")
        top_genres = exploded["genre_of_movie"].value_counts().head(12).index.tolist()
        for g in top_genres:
            col_name = f"genre_{g.lower().replace(' ', '_')}"
            df[col_name] = df["genre_of_movie"].apply(
                lambda genres: 1 if isinstance(genres, list) and g in genres else 0
            )
            genre_dummies[col_name] = df[col_name]

    # --- One-hot encode top actors ---
    actor_dummies = pd.DataFrame()
    if "Actors" in df.columns:
        # Convert string representations to lists if necessary
        import ast
        df["Actors"] = df["Actors"].apply(
            lambda x: ast.literal_eval(x) if isinstance(x, str) and x.startswith("[") else (x if isinstance(x, list) else [])
        )
        exploded_actors = df[["Actors"]].explode("Actors")
        top_actors = exploded_actors["Actors"].value_counts().head(20).index.tolist()
        for a in top_actors:
            if not isinstance(a, str): continue
            col_name = f"actor_{a.lower().replace(' ', '_')}"
            df[col_name] = df["Actors"].apply(
                lambda actors: 1 if isinstance(actors, list) and a in actors else 0
            )
            actor_dummies[col_name] = df[col_name]

    # --- Assemble feature matrix ---
    feature_cols = [
        "average_rating", "Runtime_minutes", "log_watched", "Release_Year",
        "director_avg_my_rating", "director_movie_count",
    ] + list(genre_dummies.columns) + list(actor_dummies.columns)

    X = df[feature_cols].fillna(0)
    y = df["my_rating"]
    return X, y


# ---------------------------------------------------------------------------
# Model training & comparison
# ---------------------------------------------------------------------------

def train_models(df: pd.DataFrame, test_size: float = 0.2, random_state: int = 42) -> dict:
    """
    Train Linear Regression, Ridge, and Random Forest models.
    Returns a dict with model comparison metrics and predictions.
    """
    X, y = _prepare_features(df)

    if len(X) < 10:
        return {"error": "Not enough data to train models", "n_samples": len(X)}

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=random_state
    )

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    models = {
        "Linear Regression": LinearRegression(),
        "Ridge (a=1.0)": Ridge(alpha=1.0),
        "Random Forest": RandomForestRegressor(
            n_estimators=100, max_depth=8, random_state=random_state
        ),
    }

    results = []
    best_model = None
    best_r2 = -float("inf")
    best_name = ""

    for name, model in models.items():
        if "Forest" in name:
            model.fit(X_train, y_train)
            y_pred = model.predict(X_test)
        else:
            model.fit(X_train_scaled, y_train)
            y_pred = model.predict(X_test_scaled)

        mse = mean_squared_error(y_test, y_pred)
        r2 = r2_score(y_test, y_pred)
        results.append({
            "model": name,
            "mse": round(mse, 4),
            "r2": round(r2, 4),
        })
        if r2 > best_r2:
            best_r2 = r2
            best_model = model
            best_name = name

    # Feature importance from Random Forest
    rf_model = models["Random Forest"]
    feature_importance = []
    for fname, imp in zip(X.columns, rf_model.feature_importances_):
        feature_importance.append({"feature": fname, "importance": round(float(imp), 4)})
    feature_importance.sort(key=lambda x: x["importance"], reverse=True)

    return {
        "models": results,
        "best_model": best_name,
        "best_r2": round(best_r2, 4),
        "feature_importance": feature_importance,
        "n_train": len(X_train),
        "n_test": len(X_test),
        "features_used": X.columns.tolist(),
        "_best_model_obj": best_model,
        "_scaler": scaler,
        "_feature_cols": X.columns.tolist(),
    }


# ---------------------------------------------------------------------------
# Human-readable explanation
# ---------------------------------------------------------------------------

_FEATURE_LABELS = {
    "director_avg_my_rating": "Yönetmene verdiğin geçmiş puanlar",
    "director_movie_count": "O yönetmenden kaç film izlediğin",
    "average_rating": "Letterboxd kitlesinin puanı",
    "Runtime_minutes": "Filmin uzunluğu",
    "log_watched": "Filmin ne kadar bilindiği",
    "Release_Year": "Filmin yılı",
}


def explain_predictions(df: pd.DataFrame) -> dict:
    """
    Turn feature importances into something a person can read.

    Genre and actor dummies are rolled up into single lines — thirty-odd
    one-hot columns each carrying 1% is a story about genre mattering, not
    thirty separate findings.
    """
    result = train_models(df)
    if "error" in result:
        return {"error": result["error"], "drivers": []}

    grouped: dict[str, float] = {}
    for item in result["feature_importance"]:
        name, weight = item["feature"], item["importance"]
        if name.startswith("genre_"):
            label = "Filmin türü"
        elif name.startswith("actor_"):
            label = "Oyuncu kadrosu"
        else:
            label = _FEATURE_LABELS.get(name, name)
        grouped[label] = grouped.get(label, 0.0) + weight

    total = sum(grouped.values()) or 1.0
    drivers = sorted(
        ({"label": label, "share": round(weight / total * 100, 1)}
         for label, weight in grouped.items()),
        key=lambda d: d["share"], reverse=True,
    )

    top = drivers[0] if drivers else None
    return {
        "drivers": drivers,
        "headline": (
            f"Puanını en çok belirleyen şey: {top['label'].lower()}." if top else ""
        ),
        # Kept out of the UI, but useful when judging whether to trust the above.
        "fit": result["best_r2"],
        "n_samples": result["n_train"] + result["n_test"],
    }


# ---------------------------------------------------------------------------
# Recommendation engine
# ---------------------------------------------------------------------------

def _build_recommendation_candidates(df: pd.DataFrame) -> list[dict]:
    """
    Generate recommendation candidates from top directors' filmographies.
    Uses the directors the user likes most (high my_avg_rating, >= 3 movies).
    Returns a list of candidate dicts with estimated features.
    """
    if "Director" not in df.columns:
        return []

    df["average_rating"] = pd.to_numeric(df["average_rating"], errors="coerce")
    df["Watched_number"] = pd.to_numeric(df["Watched_number"], errors="coerce")
    df["Runtime_minutes"] = pd.to_numeric(df["Runtime_minutes"], errors="coerce")

    dir_stats = df.groupby("Director").agg(
        count=("my_rating", "size"),
        my_avg=("my_rating", "mean"),
        people_avg=("average_rating", "mean"),
        avg_runtime=("Runtime_minutes", "mean"),
        avg_watched=("Watched_number", "mean"),
    )
    top_dirs = dir_stats[dir_stats["count"] >= 2].nlargest(10, "my_avg")

    candidates = []
    for director, row in top_dirs.iterrows():
        candidates.append({
            "director": director,
            "estimated_avg_rating": round(row["people_avg"], 2) if not pd.isna(row["people_avg"]) else 3.5,
            "estimated_runtime": round(row["avg_runtime"]) if not pd.isna(row["avg_runtime"]) else 120,
            "estimated_watched": round(row["avg_watched"]) if not pd.isna(row["avg_watched"]) else 500000,
            "director_avg_my_rating": round(row["my_avg"], 2),
            "director_movie_count": int(row["count"]),
            "reason": f"You rated {director}'s {int(row['count'])} films avg {row['my_avg']:.1f}★",
        })
    return candidates


def generate_recommendations(df: pd.DataFrame, n: int = 10) -> dict:
    """
    Use the best trained model to score recommendation candidates
    and return the top-N suggestions.
    """
    training_result = train_models(df)
    if "error" in training_result:
        return {"error": training_result["error"], "recommendations": []}

    candidates = _build_recommendation_candidates(df)
    if not candidates:
        return {"error": "Not enough director data for recommendations", "recommendations": []}

    model = training_result["_best_model_obj"]
    scaler = training_result["_scaler"]
    feature_cols = training_result["_feature_cols"]
    model_name = training_result["best_model"]

    # Build feature rows for candidates
    rows = []
    for c in candidates:
        row = {
            "average_rating": c["estimated_avg_rating"],
            "Runtime_minutes": c["estimated_runtime"],
            "log_watched": np.log1p(c["estimated_watched"]),
            "director_avg_my_rating": c["director_avg_my_rating"],
            "director_movie_count": c["director_movie_count"],
            "Release_Year": 2024,
        }
        # Zero out any features we don't know (genres, actors, etc)
        for col in feature_cols:
            if col not in row:
                row[col] = 0
        rows.append(row)

    X_candidates = pd.DataFrame(rows)[feature_cols].fillna(0)

    if "Forest" in model_name:
        predictions = model.predict(X_candidates)
    else:
        predictions = model.predict(scaler.transform(X_candidates))

    # Attach predictions
    for i, c in enumerate(candidates):
        c["predicted_rating"] = round(float(np.clip(predictions[i], 0.5, 5.0)), 2)

    # Sort by predicted rating
    candidates.sort(key=lambda x: x["predicted_rating"], reverse=True)

    # Clean up internal fields
    recs = []
    for c in candidates[:n]:
        recs.append({
            "director": c["director"],
            "predicted_rating": c["predicted_rating"],
            "reason": c["reason"],
            "director_avg_my_rating": c["director_avg_my_rating"],
            "director_movie_count": c["director_movie_count"],
        })

    return {
        "recommendations": recs,
        "model_used": model_name,
        "model_metrics": [m for m in training_result["models"]],
        "feature_importance": training_result["feature_importance"],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os, sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    csv_path = os.path.join(os.path.dirname(__file__), "my_movie_dataset.csv")
    if not os.path.exists(csv_path):
        print(f"Dataset not found at {csv_path}. Run data_manager.py first.")
        raise SystemExit(1)

    df = pd.read_csv(csv_path)
    # Convert genre string representation back to list
    if "genre_of_movie" in df.columns:
        import ast
        df["genre_of_movie"] = df["genre_of_movie"].apply(
            lambda x: ast.literal_eval(x) if isinstance(x, str) and x.startswith("[") else []
        )

    print("=" * 60)
    print("MODEL TRAINING RESULTS")
    print("=" * 60)
    result = train_models(df)
    if "error" not in result:
        for m in result["models"]:
            print(f"  {m['model']:25s}  MSE={m['mse']:.4f}  R²={m['r2']:.4f}")
        print(f"\n  Best model: {result['best_model']} (R²={result['best_r2']:.4f})")
        print(f"\n  Feature importance (Random Forest):")
        for fi in result["feature_importance"][:8]:
            print(f"    {fi['feature']:30s}  {fi['importance']:.4f}")
    else:
        print(f"  Error: {result['error']}")

    print("\n" + "=" * 60)
    print("RECOMMENDATIONS")
    print("=" * 60)
    recs = generate_recommendations(df)
    if recs.get("recommendations"):
        for r in recs["recommendations"]:
            print(f"  ★ {r['predicted_rating']:.1f}  {r['director']:25s}  ({r['reason']})")
    else:
        print(f"  {recs.get('error', 'No recommendations')}")
