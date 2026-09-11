"""
In-process fan-out from the IMU ingest paths to the live WebSocket sessions.

Two producers write here:
  * `mqtt_subscriber`  - runs in paho's own background thread
  * `POST /api/imu/ingest` - runs in the asyncio event loop

One consumer reads: the `/ws/workout` handler for the session whose user is
paired with that device_id.

Because one producer is on a foreign thread, every publish goes through
`loop.call_soon_threadsafe`. Calling `asyncio.Queue.put_nowait` directly from
the MQTT thread would corrupt the loop's internal state - it fails silently
and intermittently, which is the worst kind of bug to debug at 2 a.m.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from typing import Any

from app.engines.fusion_engine import StreamingImuDetector

log = logging.getLogger(__name__)

MAX_QUEUE = 256
RAW_BUFFER = 4000          # ~3 minutes at 20 Hz, per device


class ImuBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._detectors: dict[str, StreamingImuDetector] = {}
        self._raw: dict[str, deque] = defaultdict(lambda: deque(maxlen=RAW_BUFFER))
        self._loop: asyncio.AbstractEventLoop | None = None
        self.samples_seen = 0
        self.reps_detected = 0
        self.last_sample_at: float = 0.0
        self.devices_seen: set[str] = set()

    # -------------------------------------------------------------- setup --
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # ---------------------------------------------------------- subscribe --
    def subscribe(self, device_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE)
        self._subs[device_id].add(q)
        self._detectors.setdefault(device_id, StreamingImuDetector())
        return q

    def unsubscribe(self, device_id: str, q: asyncio.Queue) -> None:
        self._subs[device_id].discard(q)
        if not self._subs[device_id]:
            self._subs.pop(device_id, None)

    def raw_window(self, device_id: str) -> list[dict[str, Any]]:
        return list(self._raw.get(device_id, ()))

    # ------------------------------------------------------------ publish --
    def publish_sample(self, device_id: str, sample: dict[str, Any]) -> None:
        """Thread-safe. Safe to call from the MQTT thread or the event loop."""
        # setdefault() does not help when the key exists but holds None.
        if sample.get("t") is None:
            sample["t"] = time.time()
        self.samples_seen += 1
        self.last_sample_at = time.time()
        self.devices_seen.add(device_id)
        self._raw[device_id].append(sample)

        det = self._detectors.setdefault(device_id, StreamingImuDetector())
        rep = det.push(sample)

        events: list[dict[str, Any]] = [{"type": "imu_sample", "device_id": device_id,
                                         "t": sample["t"]}]
        if rep is not None:
            self.reps_detected += 1
            events.append({
                "type": "imu_rep",
                "device_id": device_id,
                "t": rep.t,
                "index": rep.index,
                "peak_amp": rep.peak_amp,
            })

        for ev in events:
            self._dispatch(device_id, ev)

    def publish_rep(self, device_id: str, t: float | None = None) -> None:
        """For firmware that does its own counting and publishes rep events."""
        self.reps_detected += 1
        self._dispatch(device_id, {
            "type": "imu_rep",
            "device_id": device_id,
            "t": float(t if t is not None else time.time()),
            "index": self.reps_detected,
            "peak_amp": 0.0,
        })

    # ------------------------------------------------------------ internal --
    def _dispatch(self, device_id: str, event: dict[str, Any]) -> None:
        queues = list(self._subs.get(device_id, ()))
        if not queues:
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._deliver, queues, event)
        except RuntimeError:
            # Loop shut down between the check and the call - nothing to do.
            pass

    @staticmethod
    def _deliver(queues: list[asyncio.Queue], event: dict[str, Any]) -> None:
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest and retry once: a stalled browser tab must
                # not block the sensor stream for everybody else.
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:  # noqa: BLE001
                    pass

    # -------------------------------------------------------------- stats --
    def stats(self) -> dict[str, Any]:
        return {
            "samples_seen": self.samples_seen,
            "reps_detected": self.reps_detected,
            "devices_seen": sorted(self.devices_seen),
            "active_subscriptions": {k: len(v) for k, v in self._subs.items()},
            "seconds_since_last_sample": (
                round(time.time() - self.last_sample_at, 1) if self.last_sample_at else None
            ),
        }


bus = ImuBus()
