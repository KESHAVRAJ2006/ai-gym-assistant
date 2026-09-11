"""
MongoDB archive for raw time-series.

Two collections:
  landmark_frames : one document per session holding the down-sampled angle
                    trace, so a session can be replayed or re-analysed with a
                    different pose_engine threshold without re-recording it.
  imu_raw         : one document per session holding the raw IMU samples,
                    which is what the evaluation script re-runs offline.

Mongo is OPTIONAL. If MONGO_URL is empty, or the cluster is unreachable, every
function here becomes a no-op and the API keeps working. That is deliberate:
losing the archive must never cost you a demo.
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)

_client: Any = None
_db: Any = None
_status: str = "disabled"


def connect() -> str:
    """Called once at startup. Returns a human-readable status string."""
    global _client, _db, _status
    if not settings.mongo_enabled:
        _status = "disabled (MONGO_URL not set)"
        return _status
    try:
        from pymongo import MongoClient

        _client = MongoClient(settings.MONGO_URL, serverSelectionTimeoutMS=4000)
        _client.admin.command("ping")
        _db = _client[settings.MONGO_DB]
        _db["landmark_frames"].create_index("session_id")
        _db["landmark_raw"].create_index("session_id")
        _db["imu_raw"].create_index("session_id")
        _status = f"connected ({settings.MONGO_DB})"
    except Exception as exc:  # noqa: BLE001 - optional dependency
        _client = _db = None
        _status = f"unavailable: {type(exc).__name__}"
        log.warning("MongoDB unavailable, archiving disabled: %s", exc)
    return _status


def close() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
    _client = _db = None


def status() -> str:
    return _status


def available() -> bool:
    return _db is not None


def save_frames(session_id: int, user_id: int, exercise: str,
                frames: list[dict[str, Any]]) -> bool:
    if not available() or not frames:
        return False
    try:
        _db["landmark_frames"].insert_one({
            "session_id": session_id,
            "user_id": user_id,
            "exercise": exercise,
            "n_frames": len(frames),
            "frames": frames,
        })
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("save_frames failed: %s", exc)
        return False


def save_imu(session_id: int, device_id: str, samples: list[dict[str, Any]]) -> bool:
    if not available() or not samples:
        return False
    try:
        _db["imu_raw"].insert_one({
            "session_id": session_id,
            "device_id": device_id,
            "n_samples": len(samples),
            "samples": samples,
        })
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("save_imu failed: %s", exc)
        return False


def save_raw_landmarks(session_id: int, user_id: int, exercise: str,
                       frames: list[dict[str, Any]]) -> bool:
    """
    Archive the full 33-landmark stream so the session can be replayed
    through PoseEngine later with different thresholds.

    A 16 MB BSON document limit applies, which is roughly 3 minutes at 20 Hz.
    Longer sessions are stored truncated rather than rejected outright - the
    return value tells the caller nothing was lost silently.
    """
    if not available() or not frames:
        return False
    try:
        _db["landmark_raw"].delete_many({"session_id": session_id})
        _db["landmark_raw"].insert_one({
            "session_id": session_id,
            "user_id": user_id,
            "exercise": exercise,
            "n_frames": len(frames),
            "frames": frames,
        })
        return True
    except Exception as exc:  # noqa: BLE001 - oversized document, network, ...
        log.warning("save_raw_landmarks failed (%s); trying a truncated copy", exc)
        try:
            half = frames[: max(1, len(frames) // 2)]
            _db["landmark_raw"].insert_one({
                "session_id": session_id, "user_id": user_id,
                "exercise": exercise, "n_frames": len(half),
                "truncated": True, "frames": half,
            })
            return True
        except Exception:  # noqa: BLE001
            return False


def load_raw_landmarks(session_id: int) -> list[dict[str, Any]]:
    if not available():
        return []
    doc = _db["landmark_raw"].find_one({"session_id": session_id})
    return list(doc.get("frames", [])) if doc else []


def load_frames(session_id: int) -> list[dict[str, Any]]:
    if not available():
        return []
    doc = _db["landmark_frames"].find_one({"session_id": session_id})
    return list(doc.get("frames", [])) if doc else []


def load_imu(session_id: int) -> list[dict[str, Any]]:
    if not available():
        return []
    doc = _db["imu_raw"].find_one({"session_id": session_id})
    return list(doc.get("samples", [])) if doc else []
