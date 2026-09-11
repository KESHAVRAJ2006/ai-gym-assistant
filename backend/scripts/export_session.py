"""
Export a recorded session into the format run_evaluation.py reads.

This is the bridge between "I recorded a workout in the app" and "my report
has a table of real results". Without it the real-data table in results.md
stays empty.

Usage
-----
    cd backend
    .venv\\Scripts\\python.exe scripts/export_session.py --list
    .venv\\Scripts\\python.exe scripts/export_session.py 12 --gt 15
    .venv\\Scripts\\python.exe scripts/export_session.py --all

Requirements
------------
Full replay (per-rep precision/recall) needs the raw landmark archive, which
lives in MongoDB. With no MONGO_URL configured the script still exports a
count-only record: you get count error but not detection metrics. Say which
you have in the report.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Rep, WorkoutSession  # noqa: E402
from app.services import mongo  # noqa: E402

OUT_DIR = BACKEND / "data" / "sessions"


def list_sessions() -> None:
    db = SessionLocal()
    try:
        rows = db.scalars(
            select(WorkoutSession).order_by(WorkoutSession.started_at.desc()).limit(60)
        ).all()
        if not rows:
            print("No sessions in the database yet.")
            return
        print(f"{'id':>4}  {'date':<17} {'exercise':<16} "
              f"{'cam':>4} {'imu':>4} {'fused':>5} {'truth':>6}")
        print("-" * 66)
        for s in rows:
            print(f"{s.id:>4}  {s.started_at:%Y-%m-%d %H:%M}  {s.exercise:<16} "
                  f"{s.camera_reps:>4} {s.imu_reps:>4} {s.fused_reps:>5} "
                  f"{'-' if s.ground_truth_reps is None else s.ground_truth_reps:>6}")
    finally:
        db.close()


def export_one(session_id: int, gt: int | None) -> Path | None:
    db = SessionLocal()
    try:
        s = db.get(WorkoutSession, session_id)
        if s is None:
            print(f"Session {session_id} not found.")
            return None

        if gt is not None:
            s.ground_truth_reps = gt
            db.commit()

        if s.ground_truth_reps is None:
            print(f"Session {session_id}: no ground-truth label. "
                  f"Re-run with --gt N, or label it in the web app. Skipped.")
            return None

        reps = db.scalars(select(Rep).where(Rep.session_id == session_id)).all()
        raw = mongo.load_raw_landmarks(session_id)
        imu = mongo.load_imu(session_id)

        payload: dict = {
            "name": f"s{session_id:03d}_{s.exercise}",
            "exercise": s.exercise,
            "recorded_at": s.started_at.isoformat(),
            "ground_truth_count": s.ground_truth_reps,
            "device_id": s.device_id,
            "stored_counts": {
                "camera": s.camera_reps,
                "imu": s.imu_reps,
                "fused": s.fused_reps,
            },
            "imu_samples": imu,
            "cam_frames": [],
        }

        # Prefer the raw 33-landmark archive: only that allows PoseEngine to
        # be re-run, which is what per-rep precision and recall require.
        if raw and "lm" in raw[0]:
            payload["cam_frames"] = raw
            payload["replayable"] = True
        else:
            payload["replayable"] = False
            payload["camera_rep_times"] = [
                r.end_ts for r in reps if r.source == "camera"
            ]
            payload["note"] = (
                "Angle-trace archive only: PoseEngine cannot be re-run on this "
                "session, so precision/recall are unavailable and only count "
                "error is meaningful."
            )

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUT_DIR / f"{payload['name']}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        kb = path.stat().st_size / 1024
        print(f"Wrote {path.relative_to(BACKEND)}  ({kb:.0f} kB, "
              f"{len(imu)} IMU samples, replayable={payload['replayable']})")
        return path
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Export sessions for evaluation")
    ap.add_argument("session_id", nargs="?", type=int)
    ap.add_argument("--gt", type=int, help="record the true rep count while exporting")
    ap.add_argument("--list", action="store_true", help="list sessions and exit")
    ap.add_argument("--all", action="store_true", help="export every labelled session")
    args = ap.parse_args()

    print("MongoDB:", mongo.connect())

    if args.list or (args.session_id is None and not args.all):
        list_sessions()
        if args.session_id is None and not args.all:
            print("\nPass a session id to export it, or --all for every labelled one.")
        return 0

    if args.all:
        db = SessionLocal()
        try:
            ids = [
                s.id for s in db.scalars(
                    select(WorkoutSession).where(
                        WorkoutSession.ground_truth_reps.is_not(None)
                    )
                ).all()
            ]
        finally:
            db.close()
        if not ids:
            print("No labelled sessions to export. Label them in the web app first.")
            return 1
        for sid in ids:
            export_one(sid, None)
        print(f"\nExported {len(ids)} session(s) to {OUT_DIR}")
        print("Now run: .venv\\Scripts\\python.exe scripts/run_evaluation.py")
        return 0

    export_one(args.session_id, args.gt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
