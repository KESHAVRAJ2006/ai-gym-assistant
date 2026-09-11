"""
MQTT subscriber: ESP32 -> broker -> this process -> imu_bus -> WebSocket.

MQTT is a publish/subscribe protocol designed for devices with kilobytes of
RAM. The ESP32 publishes to `gym/<device_id>/imu` and never needs to know
where the backend lives, which means the sensor keeps working when the backend
restarts - a property plain HTTP POSTs do not give you.

Expected payload (JSON, one object per message):
    {"device_id":"esp32-a1","t":1730000000.12,
     "ax":0.1,"ay":-0.2,"az":9.7,"gx":12.4,"gy":-3.1,"gz":0.8}

Or, if the firmware counts reps itself:
    {"device_id":"esp32-a1","t":1730000000.12,"event":"rep"}

MQTT is OPTIONAL. With MQTT_HOST empty this module does nothing and the HTTP
endpoint /api/imu/ingest carries the same data.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from app.config import settings
from app.services.imu_bus import bus

log = logging.getLogger(__name__)

_client: Any = None
_status: str = "disabled"
_lock = threading.Lock()


def status() -> str:
    return _status


def _on_connect(client: Any, userdata: Any, flags: Any, reason_code: Any,
                properties: Any = None) -> None:
    global _status
    ok = (getattr(reason_code, "is_failure", None) is False) or reason_code == 0
    if ok:
        client.subscribe(settings.MQTT_TOPIC, qos=0)
        _status = f"connected ({settings.MQTT_HOST}:{settings.MQTT_PORT} {settings.MQTT_TOPIC})"
        log.info("MQTT %s", _status)
    else:
        _status = f"connect failed: {reason_code}"
        log.warning("MQTT %s", _status)


def _on_disconnect(client: Any, userdata: Any, flags: Any = None,
                   reason_code: Any = None, properties: Any = None) -> None:
    global _status
    _status = "disconnected (auto-retrying)"
    log.warning("MQTT disconnected: %s", reason_code)


def _on_message(client: Any, userdata: Any, msg: Any) -> None:
    """Runs on paho's network thread - keep it short and never raise."""
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        log.debug("MQTT: non-JSON payload on %s", msg.topic)
        return

    # Topic wins over payload so one device cannot impersonate another.
    parts = msg.topic.split("/")
    device_id = parts[1] if len(parts) >= 2 else str(payload.get("device_id", "unknown"))

    try:
        if payload.get("event") == "rep":
            bus.publish_rep(device_id, payload.get("t"))
            return
        t_raw = payload.get("t")
        bus.publish_sample(device_id, {
            "t": float(t_raw) if t_raw is not None else time.time(),
            "ax": float(payload.get("ax", 0.0)),
            "ay": float(payload.get("ay", 0.0)),
            "az": float(payload.get("az", 0.0)),
            "gx": float(payload.get("gx", 0.0)),
            "gy": float(payload.get("gy", 0.0)),
            "gz": float(payload.get("gz", 0.0)),
        })
    except (TypeError, ValueError) as exc:
        log.debug("MQTT: bad sample %s", exc)


def start() -> str:
    """Start the background MQTT client. Safe to call when MQTT is disabled."""
    global _client, _status
    if not settings.mqtt_enabled:
        _status = "disabled (MQTT_HOST not set)"
        return _status

    with _lock:
        if _client is not None:
            return _status
        try:
            import paho.mqtt.client as mqtt

            # CallbackAPIVersion.VERSION2 is required by paho-mqtt 2.x. Omitting
            # it raises: "Unsupported callback API version".
            _client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=f"aigym-backend-{id(bus)}",
                clean_session=True,
            )
            if settings.MQTT_USERNAME:
                _client.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)
            if settings.MQTT_TLS:
                _client.tls_set()

            _client.on_connect = _on_connect
            _client.on_disconnect = _on_disconnect
            _client.on_message = _on_message
            _client.reconnect_delay_set(min_delay=1, max_delay=30)
            _client.connect_async(settings.MQTT_HOST, settings.MQTT_PORT, keepalive=45)
            _client.loop_start()
            _status = "connecting..."
        except Exception as exc:  # noqa: BLE001 - optional subsystem
            _client = None
            _status = f"unavailable: {type(exc).__name__}: {exc}"
            log.warning("MQTT start failed: %s", exc)
    return _status


def stop() -> None:
    global _client, _status
    with _lock:
        if _client is not None:
            try:
                _client.loop_stop()
                _client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        _client = None
        _status = "stopped"
