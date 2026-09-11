"""
Live workout WebSocket - /ws/workout?token=<JWT>

Wire protocol
-------------
client -> server
    {"type":"start","exercise":"bicep_curl","device_id":"esp32-a1"}
    {"type":"frame","t":1730000000.12,
     "lm":[[x,y,z,visibility], ... 33],
     "wlm":[[x,y,z], ... 33]}          # optional but strongly preferred
    {"type":"stop"}
    {"type":"ping"}

server -> client
    {"type":"ready","exercises":[...]}
    {"type":"started","session_id":12,"exercise":"bicep_curl"}
    {"type":"angles","t":...,"angles":{...},"state":"peak","reps":3,...}
    {"type":"rep","rep":{...},"fusion":{...}}
    {"type":"imu_rep","t":...,"fusion":{...}}
    {"type":"summary","session_id":12,"camera_reps":10,...}
    {"type":"error","message":"..."}

Why landmarks and not video: 33 points at 20 Hz is about 8 kB/s, which a free
Render dyno serves comfortably; 720p video is roughly 400x that and would also
mean shipping the user's gym footage to a server.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app.db import SessionLocal
from app.deps import user_from_token_string
from app.engines.fusion_engine import OnlineFusion
from app.engines.pose_engine import PoseEngine, list_exercises
from app.models import Rep, User, WorkoutSession
from app.services import mongo
from app.services.imu_bus import bus

log = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])

MAX_FRAME_BYTES = 64_000          # a 33-landmark frame is ~2 kB; 64 kB is generous
IDLE_TIMEOUT_S = 300.0


class LiveSession:
    """Everything one connected browser tab owns."""

    def __init__(self, user_id: int, exercise: str, device_id: str) -> None:
        self.user_id = user_id
        self.exercise = exercise
        self.device_id = device_id
        self.pose = PoseEngine(exercise)
        self.fusion = OnlineFusion()
        self.session_id: int | None = None
        self.imu_samples: list[dict[str, Any]] = []
        self.started_at = time.time()
        self.finalised = False


# --------------------------------------------------------------------------
# Database helpers (sync SQLAlchemy, run off the event loop)
# --------------------------------------------------------------------------
def _create_session_row(user_id: int, exercise: str, device_id: str) -> int:
    db = SessionLocal()
    try:
        row = WorkoutSession(user_id=user_id, exercise=exercise, device_id=device_id)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


def _finalise_row(live: LiveSession, summary: dict[str, Any],
                  fusion_dict: dict[str, Any]) -> dict[str, Any]:
    from datetime import datetime, timezone

    db = SessionLocal()
    try:
        row = db.get(WorkoutSession, live.session_id)
        if row is None:
            return {}
        row.ended_at = datetime.now(timezone.utc)
        row.camera_reps = summary["reps"]
        row.imu_reps = fusion_dict.get("imu_count", 0)
        row.fused_reps = fusion_dict.get("fused_count", summary["reps"])
        row.avg_form_score = summary["avg_form_score"]
        row.mean_visibility = summary["mean_visibility"]
        row.clock_offset_s = fusion_dict.get("offset_s", 0.0)

        for r in live.pose.reps:
            db.add(Rep(
                session_id=row.id, rep_index=r.index, source="camera",
                start_ts=r.start_ts, end_ts=r.end_ts, tempo_s=r.tempo_s,
                rom_deg=r.rom_deg, peak_angle=r.peak_angle, min_angle=r.min_angle,
                form_score=r.form_score, confidence=r.mean_visibility,
                matched=False, flags=r.flags,
            ))
        for ir in live.fusion.imu_reps:
            db.add(Rep(
                session_id=row.id, rep_index=ir.index, source="imu",
                start_ts=ir.t, end_ts=ir.t, tempo_s=ir.duration_s,
                rom_deg=0.0, form_score=1.0, confidence=1.0, matched=False, flags=[],
            ))
        for fr in fusion_dict.get("reps", []):
            if not fr.get("accepted"):
                continue
            db.add(Rep(
                session_id=row.id, rep_index=fr["index"], source="fused",
                start_ts=fr["t"], end_ts=fr["t"], tempo_s=fr.get("tempo_s", 0.0),
                rom_deg=fr.get("rom_deg", 0.0), form_score=fr.get("form_score", 1.0),
                confidence=fr.get("confidence", 1.0), matched=fr.get("origin") == "both",
                flags=fr.get("flags", []),
            ))
        db.commit()
        return {
            "session_id": row.id,
            "camera_reps": row.camera_reps,
            "imu_reps": row.imu_reps,
            "fused_reps": row.fused_reps,
        }
    finally:
        db.close()


def _load_user(token: str) -> User | None:
    db = SessionLocal()
    try:
        user = user_from_token_string(token, db)
        if user is None:
            return None
        db.expunge(user)      # detach so it is safe to read after the session closes
        return user
    finally:
        db.close()


# --------------------------------------------------------------------------
# The endpoint
# --------------------------------------------------------------------------
@router.websocket("/ws/workout")
async def workout_ws(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token", "")
    user = await run_in_threadpool(_load_user, token)
    if user is None:
        # 1008 = policy violation. Accept-then-close gives the browser a
        # readable reason; rejecting outright surfaces only "connection failed".
        await websocket.accept()
        await websocket.send_json({"type": "error", "message": "Invalid or missing token"})
        await websocket.close(code=1008)
        return

    await websocket.accept()
    send_lock = asyncio.Lock()

    async def send(payload: dict[str, Any]) -> None:
        async with send_lock:
            try:
                await websocket.send_text(json.dumps(payload))
            except (RuntimeError, WebSocketDisconnect):
                pass

    await send({
        "type": "ready",
        "user": {"id": user.id, "email": user.email, "device_id": user.device_id},
        "exercises": list_exercises(),
    })

    live: LiveSession | None = None
    imu_task: asyncio.Task | None = None
    imu_queue: asyncio.Queue | None = None

    async def drain_imu(q: asyncio.Queue, session: LiveSession) -> None:
        """Push IMU events to the browser as they arrive from the bus."""
        while True:
            ev = await q.get()
            if ev.get("type") == "imu_sample":
                session.imu_samples.append(ev)
                continue
            result = session.fusion.add_imu_rep(ev["t"], ev.get("peak_amp", 0.0))
            await send({
                "type": "imu_rep",
                "t": ev["t"],
                "imu_count": len(session.fusion.imu_reps),
                "fusion": _fusion_brief(result),
            })

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=IDLE_TIMEOUT_S)
            except asyncio.TimeoutError:
                await send({"type": "error", "message": "Idle timeout - reconnect to continue"})
                break

            if len(raw) > MAX_FRAME_BYTES:
                await send({"type": "error", "message": "Frame too large"})
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await send({"type": "error", "message": "Malformed JSON"})
                continue

            kind = msg.get("type")

            # ---------------------------------------------------------- ping
            if kind == "ping":
                await send({"type": "pong", "t": time.time()})
                continue

            # --------------------------------------------------------- start
            if kind == "start":
                if live is not None:
                    await send({"type": "error", "message": "Session already running"})
                    continue
                exercise = str(msg.get("exercise", "bicep_curl"))
                device_id = str(msg.get("device_id") or user.device_id or f"sim-{user.id}")
                live = LiveSession(user.id, exercise, device_id)
                live.session_id = await run_in_threadpool(
                    _create_session_row, user.id, exercise, device_id
                )
                imu_queue = bus.subscribe(device_id)
                imu_task = asyncio.create_task(drain_imu(imu_queue, live))
                await send({
                    "type": "started",
                    "session_id": live.session_id,
                    "exercise": live.pose.cfg.key,
                    "exercise_name": live.pose.cfg.name,
                    "device_id": device_id,
                    "thresholds": {
                        "start": live.pose.cfg.start_thresh,
                        "peak": live.pose.cfg.peak_thresh,
                        "direction": live.pose.cfg.direction,
                    },
                })
                continue

            # --------------------------------------------------------- frame
            if kind == "frame":
                if live is None:
                    await send({"type": "error", "message": "Send a start message first"})
                    continue
                # `msg.get("t") or time.time()` is WRONG here: a timestamp of
                # 0.0 is falsy, so the first frame of a device that counts from
                # boot (every ESP32, and our own simulator) would silently get
                # wall-clock time while every later frame got 0.05, 0.10, ...
                # The resulting negative tempo made the engine discard rep 1.
                t_raw = msg.get("t")
                t = float(t_raw) if t_raw is not None else time.time()
                lm = msg.get("lm") or []
                wlm = msg.get("wlm")
                result = live.pose.update(t, lm, wlm)
                live.fusion.note_visibility(t, result.visibility)

                out = result.to_dict()
                out["type"] = "angles"
                await send(out)

                if result.rep_event is not None:
                    fr = live.fusion.add_camera_rep(result.rep_event.to_dict())
                    await send({
                        "type": "rep",
                        "rep": result.rep_event.to_dict(),
                        "fusion": _fusion_brief(fr),
                    })
                continue

            # ---------------------------------------------------------- stop
            if kind == "stop":
                if live is None:
                    await send({"type": "error", "message": "No session running"})
                    continue
                payload = await _finalise(live, imu_task, imu_queue)
                await send(payload)
                live, imu_task, imu_queue = None, None, None
                continue

            await send({"type": "error", "message": f"Unknown message type: {kind}"})

    except WebSocketDisconnect:
        log.info("WebSocket closed by client (user %s)", user.id)
    except Exception as exc:  # noqa: BLE001 - a crash here must not 500 silently
        log.exception("WebSocket error: %s", exc)
        await send({"type": "error", "message": f"Server error: {type(exc).__name__}"})
    finally:
        if live is not None:
            await _finalise(live, imu_task, imu_queue)
        try:
            await websocket.close()
        except RuntimeError:
            pass


def _fusion_brief(result: Any) -> dict[str, Any]:
    return {
        "camera_count": result.camera_count,
        "imu_count": result.imu_count,
        "fused_count": result.fused_count,
        "matched": result.matched,
        "camera_only": result.camera_only,
        "imu_only": result.imu_only,
        "offset_s": result.offset_s,
        "mean_confidence": result.mean_confidence,
    }


async def _finalise(
    live: LiveSession,
    imu_task: asyncio.Task | None,
    imu_queue: asyncio.Queue | None,
) -> dict[str, Any]:
    """Stop the IMU drain, persist everything, and build the summary payload."""
    if live.finalised:
        return {"type": "summary", "note": "already finalised"}
    live.finalised = True

    if imu_task is not None:
        imu_task.cancel()
        try:
            await imu_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    if imu_queue is not None:
        bus.unsubscribe(live.device_id, imu_queue)

    summary = live.pose.summary()
    fusion_dict = live.fusion.recompute().to_dict()
    saved = await run_in_threadpool(_finalise_row, live, summary, fusion_dict)

    if live.session_id is not None:
        await run_in_threadpool(
            mongo.save_frames, live.session_id, live.user_id,
            live.exercise, live.pose.frames,
        )
        await run_in_threadpool(
            mongo.save_raw_landmarks, live.session_id, live.user_id,
            live.exercise, live.pose.raw_frames,
        )
        raw = bus.raw_window(live.device_id)
        if raw:
            await run_in_threadpool(mongo.save_imu, live.session_id, live.device_id, raw)

    return {
        "type": "summary",
        "session_id": live.session_id,
        **summary,
        "fusion": fusion_dict,
        "saved": saved,
        "mongo": mongo.status(),
    }
