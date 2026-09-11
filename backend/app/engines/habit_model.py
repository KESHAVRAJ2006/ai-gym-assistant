"""
habit_model - predicts the probability that the user skips today's session.

Why a gradient-boosted tree and not a neural net: the inputs are nine
tabular features with strong non-linear interactions (soreness matters much
more when sleep is short), the dataset is tiny, and GBDTs dominate that
regime. XGBoost is the default; if the wheel is unavailable on the host we
fall back to scikit-learn's HistGradientBoostingClassifier so the API never
fails to start because of an optional dependency.

Cold start: a new user has no history, so the model is bootstrapped on a
synthetic population generated from a documented latent rule (see
`synthesize`). The report must state this clearly - the AUC below is measured
on held-out synthetic users and is a sanity check on the pipeline, not a
claim about real human behaviour. Replace it with your own logged data before
the viva.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np

from app.config import ARTIFACT_DIR

MODEL_PATH = ARTIFACT_DIR / "habit_model.joblib"
METRICS_PATH = ARTIFACT_DIR / "habit_model_metrics.json"

FEATURES = [
    "dow",                  # 0 = Monday
    "days_since_last",
    "streak",
    "sessions_last_7",
    "sleep_h",
    "soreness",
    "mood",
    "planned_hour",
    "completion_rate_30d",
]

FEATURE_LABELS = {
    "dow": "day of week",
    "days_since_last": "days since last workout",
    "streak": "current streak",
    "sessions_last_7": "sessions in the last 7 days",
    "sleep_h": "sleep hours",
    "soreness": "muscle soreness",
    "mood": "mood",
    "planned_hour": "planned start hour",
    "completion_rate_30d": "30-day completion rate",
}

MEDIANS = {
    "dow": 3.0, "days_since_last": 1.0, "streak": 3.0, "sessions_last_7": 3.0,
    "sleep_h": 7.0, "soreness": 2.0, "mood": 3.0, "planned_hour": 18.0,
    "completion_rate_30d": 0.6,
}

_model: Any = None
_model_kind: str = "untrained"


# ==========================================================================
# Synthetic population
# ==========================================================================
def _latent_skip_logit(f: dict[str, float]) -> float:
    """
    The documented ground-truth rule the synthetic data is drawn from.

    Written as an explicit logit so the report can state exactly what
    relationship the model is being asked to recover:
      - short sleep and high soreness raise skip risk, and interact
      - a long streak protects strongly (habit formation)
      - very early and very late sessions are skipped more
      - weekends are skipped more
    """
    z = -0.55
    z += 0.42 * max(0.0, 7.0 - f["sleep_h"])
    z += 0.30 * f["soreness"]
    z += 0.16 * f["soreness"] * max(0.0, 7.0 - f["sleep_h"])   # interaction
    z -= 0.24 * min(f["streak"], 12.0)
    z -= 0.55 * f["mood"]
    z += 0.28 * min(f["days_since_last"], 7.0)
    z -= 1.9 * f["completion_rate_30d"]
    z += 0.9 if f["dow"] >= 5 else 0.0
    z += 0.10 * abs(f["planned_hour"] - 18.0)
    z -= 0.11 * f["sessions_last_7"]
    return z


def synthesize(n_users: int = 260, days: int = 70, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """Generate (X, y) for a synthetic population of users over `days` days."""
    rng = np.random.default_rng(seed)
    rows: list[list[float]] = []
    labels: list[int] = []

    for _ in range(n_users):
        discipline = float(rng.beta(2.4, 2.0))        # per-user trait
        chrono = float(rng.choice([6, 7, 17, 18, 19, 20, 21]))
        streak = 0.0
        days_since = float(rng.integers(0, 3))
        hist: list[int] = []

        for d in range(days):
            dow = float(d % 7)
            sleep = float(np.clip(rng.normal(7.1 - (0.5 if dow >= 5 else 0.0), 1.1), 3.5, 10.0))
            soreness = float(np.clip(rng.normal(2.0 + 0.35 * min(streak, 6), 1.0), 0, 5))
            mood = float(np.clip(rng.normal(3.2 + 0.25 * discipline * 2, 0.9), 1, 5))
            sessions7 = float(sum(hist[-7:]))
            rate30 = float(np.mean(hist[-30:])) if hist else discipline

            f = {
                "dow": dow, "days_since_last": days_since, "streak": streak,
                "sessions_last_7": sessions7, "sleep_h": sleep, "soreness": soreness,
                "mood": mood, "planned_hour": chrono, "completion_rate_30d": rate30,
            }
            z = _latent_skip_logit(f) + (1.0 - discipline) * 1.4
            p_skip = 1.0 / (1.0 + math.exp(-z))
            skipped = int(rng.random() < p_skip)

            rows.append([f[k] for k in FEATURES])
            labels.append(skipped)

            if skipped:
                streak = 0.0
                days_since += 1.0
                hist.append(0)
            else:
                streak += 1.0
                days_since = 0.0
                hist.append(1)

    return np.asarray(rows, dtype=float), np.asarray(labels, dtype=int)


# ==========================================================================
# Training
# ==========================================================================
def _new_estimator() -> tuple[Any, str]:
    try:
        from xgboost import XGBClassifier

        return (
            XGBClassifier(
                n_estimators=320,
                max_depth=4,
                learning_rate=0.06,
                subsample=0.9,
                colsample_bytree=0.9,
                reg_lambda=1.2,
                eval_metric="logloss",
                tree_method="hist",
                n_jobs=2,
            ),
            "xgboost",
        )
    except Exception:  # noqa: BLE001 - missing wheel must not break the API
        from sklearn.ensemble import HistGradientBoostingClassifier

        return (
            HistGradientBoostingClassifier(
                max_iter=300, max_depth=4, learning_rate=0.06, random_state=7
            ),
            "sklearn_hgb",
        )


def train(save: bool = True, seed: int = 7) -> dict[str, Any]:
    """Train on the synthetic population and report held-out metrics."""
    from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score
    from sklearn.model_selection import train_test_split

    X, y = synthesize(seed=seed)
    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.25, random_state=seed, stratify=y
    )
    model, kind = _new_estimator()
    model.fit(X_tr, y_tr)

    proba = model.predict_proba(X_te)[:, 1]
    metrics = {
        "model_kind": kind,
        "n_train": int(len(X_tr)),
        "n_test": int(len(X_te)),
        "positive_rate": round(float(y.mean()), 4),
        "roc_auc": round(float(roc_auc_score(y_te, proba)), 4),
        "accuracy": round(float(accuracy_score(y_te, (proba >= 0.5).astype(int))), 4),
        "brier": round(float(brier_score_loss(y_te, proba)), 4),
        "data_source": "synthetic population (see habit_model.synthesize)",
    }

    if save:
        import joblib

        joblib.dump({"model": model, "kind": kind, "features": FEATURES}, MODEL_PATH)
        METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    global _model, _model_kind
    _model, _model_kind = model, kind
    return metrics


def load_model() -> tuple[Any, str]:
    """Lazy-load, training once on first use if no artifact exists."""
    global _model, _model_kind
    if _model is not None:
        return _model, _model_kind
    if MODEL_PATH.exists():
        try:
            import joblib

            blob = joblib.load(MODEL_PATH)
            _model, _model_kind = blob["model"], blob.get("kind", "unknown")
            return _model, _model_kind
        except Exception:  # noqa: BLE001 - corrupt or version-mismatched artifact
            pass
    train(save=True)
    return _model, _model_kind


def model_metrics() -> dict[str, Any]:
    if METRICS_PATH.exists():
        try:
            return json.loads(METRICS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return train(save=True)


# ==========================================================================
# Inference
# ==========================================================================
@dataclass
class SkipRisk:
    probability: float
    band: str
    drivers: list[str]
    recommendation: str
    model_kind: str


def _vector(features: dict[str, float]) -> np.ndarray:
    return np.asarray([[float(features.get(k, MEDIANS[k])) for k in FEATURES]], dtype=float)


def predict(features: dict[str, float]) -> SkipRisk:
    """
    Probability the user skips, plus an ablation-based explanation.

    Explanation method: for each feature, re-score the same example with that
    one feature replaced by its population median. The drop in predicted risk
    is that feature's contribution. This is a one-feature-at-a-time
    counterfactual - cheap, model-agnostic, and easy to defend, unlike quoting
    global feature_importances_ for a single prediction.
    """
    model, kind = load_model()
    x = _vector(features)
    p = float(model.predict_proba(x)[0, 1])

    contributions: list[tuple[float, str]] = []
    for i, name in enumerate(FEATURES):
        x2 = x.copy()
        x2[0, i] = MEDIANS[name]
        p2 = float(model.predict_proba(x2)[0, 1])
        contributions.append((p - p2, name))
    contributions.sort(reverse=True)
    drivers = [
        f"{FEATURE_LABELS[n]} (+{d * 100:.0f}% risk)"
        for d, n in contributions[:3] if d > 0.01
    ] or ["nothing stands out - risk is at your baseline"]

    if p < 0.30:
        band, rec = "low", "You are on track. Keep the same slot and warm up properly."
    elif p < 0.60:
        band, rec = "medium", (
            "Shrink the session, do not skip it. Commit to the first two sets; "
            "finishing a short session protects the streak."
        )
    else:
        band, rec = "high", (
            "High skip risk. Move the session earlier, cut it to 20 minutes, "
            "and lay your kit out now - reduce the activation energy."
        )

    if features.get("sleep_h", 7) < 6:
        rec += " Sleep was short, so drop the top set and keep intensity moderate."
    if features.get("soreness", 2) >= 4:
        rec += " Soreness is high - train a different muscle group today."

    return SkipRisk(round(p, 4), band, drivers, rec, kind)


# ==========================================================================
# Feature extraction from real logs
# ==========================================================================
def features_from_logs(
    logs: list[Any],
    target: date | None = None,
    planned_hour: int = 18,
    sleep_h: float = 7.0,
    soreness: int = 2,
    mood: int = 3,
) -> dict[str, float]:
    """
    Build the feature vector for `target` from HabitLog ORM rows.

    Only rows strictly BEFORE the target date are used. Leaking the target
    day's own outcome into its features is the classic way to accidentally
    report 0.99 AUC in a project report.
    """
    target = target or date.today()
    past = sorted(
        (log for log in logs if getattr(log, "log_date", None) and log.log_date < target),
        key=lambda log: log.log_date,
    )

    completed_dates = [log.log_date for log in past if log.completed]
    days_since = float((target - completed_dates[-1]).days) if completed_dates else 14.0

    streak = 0.0
    d = target - timedelta(days=1)
    done = set(completed_dates)
    while d in done:
        streak += 1
        d -= timedelta(days=1)

    last7 = [log for log in past if (target - log.log_date).days <= 7]
    last30 = [log for log in past if (target - log.log_date).days <= 30]

    recent = past[-1] if past else None
    return {
        "dow": float(target.weekday()),
        "days_since_last": min(days_since, 14.0),
        "streak": streak,
        "sessions_last_7": float(sum(1 for log in last7 if log.completed)),
        "sleep_h": float(getattr(recent, "sleep_h", sleep_h) if recent else sleep_h),
        "soreness": float(getattr(recent, "soreness", soreness) if recent else soreness),
        "mood": float(getattr(recent, "mood", mood) if recent else mood),
        "planned_hour": float(planned_hour),
        "completion_rate_30d": (
            float(sum(1 for log in last30 if log.completed) / len(last30)) if last30 else 0.5
        ),
    }
