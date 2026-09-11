"""Habit tracking and skip-risk prediction - /api/habits/*."""
from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.engines import habit_model
from app.models import HabitLog, User
from app.schemas import HabitLogIn, HabitLogOut, SkipRiskOut

router = APIRouter(prefix="/api/habits", tags=["habits"])


@router.get("", response_model=list[HabitLogOut])
def list_logs(
    days: int = Query(default=60, ge=1, le=365),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[HabitLogOut]:
    since = date.today() - timedelta(days=days)
    rows = db.scalars(
        select(HabitLog)
        .where(HabitLog.user_id == user.id, HabitLog.log_date >= since)
        .order_by(HabitLog.log_date.asc())
    ).all()
    return [HabitLogOut.model_validate(r) for r in rows]


@router.post("/log", response_model=HabitLogOut)
def upsert_log(
    payload: HabitLogIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HabitLogOut:
    """One row per user per day - posting the same date twice updates it."""
    day = payload.log_date or date.today()
    row = db.scalar(
        select(HabitLog).where(HabitLog.user_id == user.id, HabitLog.log_date == day)
    )
    if row is None:
        row = HabitLog(user_id=user.id, log_date=day)
        db.add(row)

    row.planned = payload.planned
    row.completed = payload.completed
    row.duration_min = payload.duration_min
    row.sleep_h = payload.sleep_h
    row.soreness = payload.soreness
    row.mood = payload.mood
    row.planned_hour = payload.planned_hour
    db.commit()
    db.refresh(row)
    return HabitLogOut.model_validate(row)


@router.get("/skip-risk", response_model=SkipRiskOut)
def skip_risk(
    planned_hour: int = Query(default=18, ge=0, le=23),
    sleep_h: float = Query(default=7.0, ge=0, le=16),
    soreness: int = Query(default=2, ge=0, le=5),
    mood: int = Query(default=3, ge=1, le=5),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkipRiskOut:
    logs = db.scalars(select(HabitLog).where(HabitLog.user_id == user.id)).all()
    feats = habit_model.features_from_logs(
        list(logs), target=date.today(), planned_hour=planned_hour,
        sleep_h=sleep_h, soreness=soreness, mood=mood,
    )
    # Today's self-reported state overrides whatever yesterday's row said.
    feats.update({"sleep_h": sleep_h, "soreness": float(soreness), "mood": float(mood)})
    risk = habit_model.predict(feats)
    return SkipRiskOut(
        skip_probability=risk.probability,
        risk_band=risk.band,
        top_drivers=risk.drivers,
        recommendation=risk.recommendation,
        model_kind=risk.model_kind,
    )


@router.get("/streak")
def streak(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    logs = db.scalars(
        select(HabitLog)
        .where(HabitLog.user_id == user.id)
        .order_by(HabitLog.log_date.asc())
    ).all()
    done = {log.log_date for log in logs if log.completed}

    current = 0
    d = date.today()
    if d not in done:
        d -= timedelta(days=1)          # today is not over yet; do not break it
    while d in done:
        current += 1
        d -= timedelta(days=1)

    best = run = 0
    prev: date | None = None
    for day in sorted(done):
        run = run + 1 if (prev and (day - prev).days == 1) else 1
        best = max(best, run)
        prev = day

    last30 = [log for log in logs if (date.today() - log.log_date).days <= 30]
    return {
        "current_streak": current,
        "best_streak": best,
        "days_logged": len(logs),
        "completion_rate_30d": (
            round(sum(1 for log in last30 if log.completed) / len(last30), 3)
            if last30 else 0.0
        ),
        "calendar": [
            {"date": log.log_date.isoformat(), "completed": log.completed,
             "duration_min": log.duration_min}
            for log in logs
        ],
    }


@router.get("/model-metrics")
def model_metrics() -> dict[str, Any]:
    """
    Held-out metrics for the skip-risk model.

    Report these honestly: the model ships trained on a synthetic population,
    so this is pipeline validation, not a behavioural finding.
    """
    return habit_model.model_metrics()


@router.post("/seed-demo")
def seed_demo(
    days: int = Query(default=45, ge=7, le=120),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Fill the habit calendar with plausible history so the dashboard and the
    model have something to show on a brand-new account. Clearly labelled as
    demo data - delete it before you collect real logs for the report.
    """
    rng = random.Random(user.id)
    created = 0
    for i in range(days, 0, -1):
        day = date.today() - timedelta(days=i)
        exists = db.scalar(
            select(HabitLog).where(HabitLog.user_id == user.id, HabitLog.log_date == day)
        )
        if exists:
            continue
        weekend = day.weekday() >= 5
        completed = rng.random() < (0.45 if weekend else 0.78)
        db.add(HabitLog(
            user_id=user.id,
            log_date=day,
            planned=True,
            completed=completed,
            duration_min=round(rng.uniform(25, 65), 1) if completed else 0.0,
            sleep_h=round(rng.uniform(5.2, 8.6), 1),
            soreness=rng.randint(0, 4),
            mood=rng.randint(2, 5),
            planned_hour=rng.choice([7, 18, 19, 20]),
        ))
        created += 1
    db.commit()
    return {"created": created, "note": "demo data - delete before collecting real logs"}
