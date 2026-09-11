"""Workout session history and aggregate stats - /api/sessions/*."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.deps import get_current_user
from app.engines.fusion_engine import detect_imu_reps, evaluate, fuse
from app.engines.pose_engine import list_exercises
from app.models import User, WorkoutSession
from app.schemas import SessionDetail, SessionOut, SessionPatch
from app.services import mongo

router = APIRouter(prefix="/api", tags=["sessions"])


@router.get("/exercises")
def exercises() -> list[dict[str, Any]]:
    """Exercise catalogue with the FSM thresholds the client draws as a gauge."""
    return list_exercises()


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SessionOut]:
    rows = db.scalars(
        select(WorkoutSession)
        .where(WorkoutSession.user_id == user.id)
        .order_by(WorkoutSession.started_at.desc())
        .limit(limit)
    ).all()
    return [SessionOut.model_validate(r) for r in rows]


@router.get("/sessions/summary")
def summary(
    days: int = Query(default=30, ge=1, le=365),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Everything the dashboard needs, in one request."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.scalars(
        select(WorkoutSession)
        .where(WorkoutSession.user_id == user.id, WorkoutSession.started_at >= since)
        .order_by(WorkoutSession.started_at.asc())
    ).all()

    by_day: dict[str, dict[str, float]] = defaultdict(
        lambda: {"reps": 0, "sessions": 0, "form": 0.0}
    )
    by_exercise: dict[str, int] = defaultdict(int)
    agreement_points: list[dict[str, Any]] = []

    for s in rows:
        day = s.started_at.date().isoformat()
        by_day[day]["reps"] += s.fused_reps
        by_day[day]["sessions"] += 1
        by_day[day]["form"] += s.avg_form_score
        by_exercise[s.exercise] += s.fused_reps
        if s.imu_reps or s.camera_reps:
            agreement_points.append({
                "session_id": s.id,
                "date": day,
                "camera": s.camera_reps,
                "imu": s.imu_reps,
                "fused": s.fused_reps,
                "ground_truth": s.ground_truth_reps,
            })

    daily = [
        {
            "date": d,
            "reps": int(v["reps"]),
            "sessions": int(v["sessions"]),
            "avg_form": round(v["form"] / v["sessions"], 3) if v["sessions"] else 0.0,
        }
        for d, v in sorted(by_day.items())
    ]

    forms = [s.avg_form_score for s in rows if s.avg_form_score > 0]
    labelled = [s for s in rows if s.ground_truth_reps is not None]
    accuracy = None
    if labelled:
        accuracy = {
            "n_labelled_sessions": len(labelled),
            "camera_mae": round(
                sum(abs(s.camera_reps - s.ground_truth_reps) for s in labelled) / len(labelled), 3),
            "imu_mae": round(
                sum(abs(s.imu_reps - s.ground_truth_reps) for s in labelled) / len(labelled), 3),
            "fused_mae": round(
                sum(abs(s.fused_reps - s.ground_truth_reps) for s in labelled) / len(labelled), 3),
        }

    return {
        "window_days": days,
        "total_sessions": len(rows),
        "total_reps": sum(s.fused_reps for s in rows),
        "mean_form_score": round(sum(forms) / len(forms), 3) if forms else 0.0,
        "daily": daily,
        "by_exercise": [{"exercise": k, "reps": v} for k, v in sorted(by_exercise.items())],
        "agreement": agreement_points[-20:],
        "accuracy": accuracy,
    }


@router.get("/sessions/{session_id}", response_model=SessionDetail)
def get_session(
    session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionDetail:
    s = db.scalar(
        select(WorkoutSession)
        .options(selectinload(WorkoutSession.reps))
        .where(WorkoutSession.id == session_id, WorkoutSession.user_id == user.id)
    )
    if s is None:
        raise HTTPException(status_code=404, detail="Session not found")
    detail = SessionDetail.model_validate(s)
    detail.reps = sorted(detail.reps, key=lambda r: (r.source, r.rep_index))
    return detail


@router.get("/sessions/{session_id}/trace")
def session_trace(
    session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Angle trace + IMU trace for the session detail chart (needs MongoDB)."""
    s = db.scalar(
        select(WorkoutSession).where(
            WorkoutSession.id == session_id, WorkoutSession.user_id == user.id
        )
    )
    if s is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session_id,
        "mongo": mongo.status(),
        "frames": mongo.load_frames(session_id),
        "imu": mongo.load_imu(session_id)[:4000],
    }


@router.patch("/sessions/{session_id}", response_model=SessionOut)
def patch_session(
    session_id: int,
    payload: SessionPatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionOut:
    """
    Record the human-counted rep total for a session.

    This is the single most important button in the whole app for your report:
    without a ground-truth label a session contributes nothing to the
    evaluation table. Label every session you record.
    """
    s = db.scalar(
        select(WorkoutSession).where(
            WorkoutSession.id == session_id, WorkoutSession.user_id == user.id
        )
    )
    if s is None:
        raise HTTPException(status_code=404, detail="Session not found")
    data = payload.model_dump(exclude_unset=True)
    if "ground_truth_reps" in data:
        s.ground_truth_reps = data["ground_truth_reps"]
    if "notes" in data and data["notes"] is not None:
        s.notes = data["notes"]
    db.commit()
    db.refresh(s)
    return SessionOut.model_validate(s)


@router.post("/sessions/{session_id}/refuse")
def recompute_fusion(
    session_id: int,
    match_window_s: float = Query(default=0.60, ge=0.05, le=3.0),
    low_vis_threshold: float = Query(default=0.55, ge=0.0, le=1.0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Re-run fusion on a stored session with different thresholds.

    This is how you sweep parameters on REAL recorded data without re-recording
    anything - it reads the archived camera reps from Postgres and the archived
    raw IMU samples from MongoDB.
    """
    s = db.scalar(
        select(WorkoutSession)
        .options(selectinload(WorkoutSession.reps))
        .where(WorkoutSession.id == session_id, WorkoutSession.user_id == user.id)
    )
    if s is None:
        raise HTTPException(status_code=404, detail="Session not found")

    cam = [
        {
            "end_ts": r.end_ts,
            "form_score": r.form_score,
            "rom_deg": r.rom_deg,
            "tempo_s": r.tempo_s,
            "mean_visibility": r.confidence,
            "flags": r.flags or [],
        }
        for r in s.reps if r.source == "camera"
    ]
    raw_imu = mongo.load_imu(session_id)
    if raw_imu:
        imu = detect_imu_reps(raw_imu)
    else:
        imu = [{"index": r.rep_index, "t": r.end_ts, "peak_amp": 0.0, "duration_s": 0.0}
               for r in s.reps if r.source == "imu"]

    frames = mongo.load_frames(session_id)
    vis_timeline = [(f["t"], f.get("vis", 1.0)) for f in frames] if frames else []

    result = fuse(
        cam, imu,
        visibility_timeline=vis_timeline,
        match_window_s=match_window_s,
        low_vis_threshold=low_vis_threshold,
    )
    out = result.to_dict()
    if s.ground_truth_reps is not None:
        gt_times = []   # per-rep GT timing is not labelled in the UI, count only
        out["vs_ground_truth"] = {
            "ground_truth_reps": s.ground_truth_reps,
            "camera_abs_error": abs(len(cam) - s.ground_truth_reps),
            "imu_abs_error": abs(len(imu) - s.ground_truth_reps),
            "fused_abs_error": abs(result.fused_count - s.ground_truth_reps),
        }
        if gt_times:
            out["detection_metrics"] = evaluate(
                [r.t for r in result.reps if r.accepted], gt_times
            )
    return out


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(
    session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    s = db.scalar(
        select(WorkoutSession).where(
            WorkoutSession.id == session_id, WorkoutSession.user_id == user.id
        )
    )
    if s is None:
        raise HTTPException(status_code=404, detail="Session not found")
    db.delete(s)
    db.commit()
