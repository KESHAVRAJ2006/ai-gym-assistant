"""
IMU ingest and diagnostics - /api/imu/*.

Three ways data gets in:
  1. MQTT      - the real path, handled by services/mqtt_subscriber.
  2. HTTP POST - the fallback path for firmware on a network that blocks 1883.
  3. /simulate - a synthetic ESP32, so the camera+IMU demo works when the
                 hardware is on a bench in another building. It publishes into
                 exactly the same bus as the real sensor, so nothing else in
                 the system can tell the difference.
"""
from __future__ import annotations

import asyncio
import math
import random
import time
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import ImuBatch, ImuIngest
from app.services import mqtt_subscriber
from app.services.imu_bus import bus

router = APIRouter(prefix="/api/imu", tags=["imu"])


@router.post("/ingest")
def ingest(sample: ImuIngest) -> dict[str, Any]:
    """
    Unauthenticated on purpose: an ESP32 cannot hold a JWT comfortably, and
    the only thing an attacker gains is the ability to inject rep events for a
    device id they would have to guess. Treat device ids as low-value secrets
    and do not reuse them across users.
    """
    # `sample.t or time.time()` would discard a legitimate t == 0.0, which is
    # exactly what a freshly booted ESP32 sends.
    bus.publish_sample(sample.device_id, {
        "t": sample.t,
        "ax": sample.ax, "ay": sample.ay, "az": sample.az,
        "gx": sample.gx, "gy": sample.gy, "gz": sample.gz,
    })
    return {"ok": True, "samples_seen": bus.samples_seen}


@router.post("/ingest/batch")
def ingest_batch(batch: ImuBatch) -> dict[str, Any]:
    """Batched ingest - one HTTP round trip per ~20 samples saves ESP32 power."""
    for s in batch.samples:
        bus.publish_sample(s.device_id, {
            "t": s.t,
            "ax": s.ax, "ay": s.ay, "az": s.az,
            "gx": s.gx, "gy": s.gy, "gz": s.gz,
        })
    return {"ok": True, "accepted": len(batch.samples), "samples_seen": bus.samples_seen}


@router.get("/status")
def imu_status() -> dict[str, Any]:
    return {
        "mqtt": {
            "enabled": settings.mqtt_enabled,
            "status": mqtt_subscriber.status(),
            "host": settings.MQTT_HOST or None,
            "topic": settings.MQTT_TOPIC,
        },
        "bus": bus.stats(),
    }


# --------------------------------------------------------------------------
# Synthetic sensor
# --------------------------------------------------------------------------
async def _simulate_task(
    device_id: str,
    reps: int,
    period_s: float,
    rate_hz: float,
    noise: float,
    miss_prob: float,
    bumps: int,
) -> None:
    """
    Emit a physically plausible MPU6050 stream for `reps` repetitions.

    Model: each rep is one sinusoidal flexion-extension cycle. Angular
    velocity is the derivative of that cycle, so the gyroscope trace peaks
    twice per rep (up, then down) - exactly what a real curl looks like and
    exactly the case `_merge_half_cycles` exists to handle.
    """
    dt = 1.0 / rate_hz
    t0 = time.time()
    amp = 120.0                      # deg/s peak angular velocity
    total = reps * period_s
    skipped = {random.randrange(reps) for _ in range(int(reps * miss_prob))}
    bump_times = sorted(random.uniform(0, total) for _ in range(bumps))

    n = int(total * rate_hz)
    for i in range(n):
        t_rel = i * dt
        rep_idx = int(t_rel // period_s)
        phase = (t_rel % period_s) / period_s

        gy = 0.0 if rep_idx in skipped else amp * math.sin(2 * math.pi * phase)
        # A bump is a short, sharp spike with no matching camera motion.
        for bt in bump_times:
            if 0 <= t_rel - bt < 0.12:
                gy += 260.0 * math.sin(math.pi * (t_rel - bt) / 0.12)

        bus.publish_sample(device_id, {
            "t": t0 + t_rel,
            "ax": random.gauss(0, noise),
            "ay": random.gauss(0, noise),
            "az": 9.81 + random.gauss(0, noise),
            "gx": random.gauss(0, noise * 2),
            "gy": gy + random.gauss(0, noise * 3),
            "gz": random.gauss(0, noise * 2),
        })
        await asyncio.sleep(dt)


@router.post("/simulate")
async def simulate(
    reps: int = Query(default=10, ge=1, le=100),
    period_s: float = Query(default=2.5, ge=0.8, le=8.0),
    rate_hz: float = Query(default=50.0, ge=10.0, le=200.0),
    noise: float = Query(default=0.25, ge=0.0, le=3.0),
    miss_prob: float = Query(default=0.0, ge=0.0, le=0.9),
    bumps: int = Query(default=0, ge=0, le=20),
    device_id: str = Query(default=""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Start a synthetic IMU stream for this user's device.

    `miss_prob` makes the IMU miss reps (a loose strap), `bumps` injects
    events the camera will not see (someone knocking the dumbbell). Those two
    knobs are how you demonstrate, live, what the fusion layer is for.
    """
    dev = device_id or user.device_id or f"sim-{user.id}"
    if not user.device_id:
        user.device_id = dev
        db.commit()

    asyncio.create_task(
        _simulate_task(dev, reps, period_s, rate_hz, noise, miss_prob, bumps)
    )
    return {
        "started": True,
        "device_id": dev,
        "reps": reps,
        "duration_s": round(reps * period_s, 1),
        "note": "synthetic stream - identical code path to the real ESP32",
    }
