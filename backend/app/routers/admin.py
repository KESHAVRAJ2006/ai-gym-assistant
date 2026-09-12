"""
Admin dashboard and analytics - /api/admin/*.

Everything here is aggregate and system-wide, in contrast to /api/sessions
which is always scoped to the calling user. Access is gated by
`get_current_admin`, which reads ADMIN_EMAILS from configuration rather than
a database column, so there is no row a user could write to in order to
promote themselves.

The headline number on this dashboard is the fusion accuracy table computed
across EVERY labelled session from EVERY user - that is the system-level
evidence for the project's central claim, as opposed to the per-user view.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db, engine
from app.deps import get_current_admin
from app.engines import habit_model
from app.models import DietPlan, HabitLog, Rep, User, WorkoutSession
from app.services import mongo, mqtt_subscriber
from app.services.imu_bus import bus

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _mae(rows: list[WorkoutSession], attr: str) -> float | None:
    vals = [abs(getattr(s, attr) - s.ground_truth_reps) for s in rows]
    return round(sum(vals) / len(vals), 3) if vals else None


@router.get("/overview")
def overview(
    days: int = Query(default=30, ge=1, le=365),
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Everything the admin dashboard renders, in a single round trip."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    total_users = db.scalar(select(func.count(User.id))) or 0
    total_sessions = db.scalar(select(func.count(WorkoutSession.id))) or 0
    total_reps = db.scalar(select(func.sum(WorkoutSession.fused_reps))) or 0
    total_plans = db.scalar(select(func.count(DietPlan.id))) or 0
    total_habit_logs = db.scalar(select(func.count(HabitLog.id))) or 0

    sessions = db.scalars(
        select(WorkoutSession).where(WorkoutSession.started_at >= since)
    ).all()

    # ---- system-wide fusion accuracy (the project's central claim) ----
    labelled = [s for s in db.scalars(select(WorkoutSession)).all()
                if s.ground_truth_reps is not None]
    accuracy = None
    if labelled:
        gt_total = sum(s.ground_truth_reps for s in labelled)
        accuracy = {
            "labelled_sessions": len(labelled),
            "ground_truth_reps": gt_total,
            "camera": {
                "mae": _mae(labelled, "camera_reps"),
                "total": sum(s.camera_reps for s in labelled),
            },
            "imu": {
                "mae": _mae(labelled, "imu_reps"),
                "total": sum(s.imu_reps for s in labelled),
            },
            "fused": {
                "mae": _mae(labelled, "fused_reps"),
                "total": sum(s.fused_reps for s in labelled),
            },
        }

    # ---- activity over time ----
    by_day: dict[str, dict[str, float]] = defaultdict(
        lambda: {"sessions": 0, "reps": 0}
    )
    by_exercise: dict[str, int] = defaultdict(int)
    for s in sessions:
        day = s.started_at.date().isoformat()
        by_day[day]["sessions"] += 1
        by_day[day]["reps"] += s.fused_reps
        by_exercise[s.exercise] += s.fused_reps

    # ---- per-user leaderboard ----
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    counts: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"sessions": 0, "reps": 0, "form": 0.0}
    )
    for s in db.scalars(select(WorkoutSession)).all():
        c = counts[s.user_id]
        c["sessions"] += 1
        c["reps"] += s.fused_reps
        c["form"] += s.avg_form_score

    user_rows = []
    for u in users[:50]:
        c = counts.get(u.id, {"sessions": 0, "reps": 0, "form": 0.0})
        user_rows.append({
            "id": u.id,
            "email": u.email,
            "full_name": u.full_name,
            "goal": u.goal,
            "joined": u.created_at.date().isoformat() if u.created_at else None,
            "sessions": c["sessions"],
            "reps": c["reps"],
            "avg_form": round(c["form"] / c["sessions"], 3) if c["sessions"] else 0.0,
        })

    # ---- form-fault distribution across every stored rep ----
    flag_counts: dict[str, int] = defaultdict(int)
    for rep in db.scalars(select(Rep).where(Rep.source == "camera")).all():
        for f in (rep.flags or []):
            flag_counts[f] += 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "totals": {
            "users": total_users,
            "sessions": total_sessions,
            "reps": int(total_reps),
            "diet_plans": total_plans,
            "habit_logs": total_habit_logs,
        },
        "accuracy": accuracy,
        "daily": [
            {"date": d, "sessions": int(v["sessions"]), "reps": int(v["reps"])}
            for d, v in sorted(by_day.items())
        ],
        "by_exercise": [
            {"exercise": k, "reps": v}
            for k, v in sorted(by_exercise.items(), key=lambda kv: -kv[1])
        ],
        "users": user_rows,
        "form_faults": [
            {"flag": k, "count": v}
            for k, v in sorted(flag_counts.items(), key=lambda kv: -kv[1])
        ],
    }


@router.get("/system")
def system(admin: User = Depends(get_current_admin)) -> dict[str, Any]:
    """Live service health, for the operations panel of the dashboard."""
    db_ok, db_err = True, None
    try:
        from sqlalchemy import text

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        db_ok, db_err = False, f"{type(exc).__name__}: {exc}"

    return {
        "environment": settings.ENV,
        "database": {
            "ok": db_ok,
            "dialect": engine.url.get_backend_name(),
            "error": db_err,
        },
        "mongo": mongo.status(),
        "mqtt": mqtt_subscriber.status(),
        "llm": "enabled" if settings.llm_enabled else "rule-based coach",
        "imu_bus": bus.stats(),
        "fusion_defaults": {
            "match_window_s": settings.FUSION_MATCH_WINDOW_S,
            "max_offset_s": settings.FUSION_MAX_OFFSET_S,
            "min_camera_visibility": settings.FUSION_MIN_CAMERA_VIS,
        },
    }


@router.get("/model-card")
def model_card(admin: User = Depends(get_current_admin)) -> dict[str, Any]:
    """
    The habit model's held-out metrics, surfaced to the operator.

    Deliberately repeats the 'synthetic' warning: an admin panel is exactly
    where someone would screenshot a number and paste it into a report
    without reading where it came from.
    """
    m = habit_model.model_metrics()
    return {
        **m,
        "warning": (
            "Measured on held-out SYNTHETIC users generated from a documented "
            "rule. This validates the training pipeline, not real human "
            "adherence. Retrain on logged data before citing it."
        ),
        "features": [
            {"name": f, "label": habit_model.FEATURE_LABELS[f]}
            for f in habit_model.FEATURES
        ],
    }
