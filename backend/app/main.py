"""
FastAPI application entry point.

Run locally:
    uvicorn app.main:app --reload --port 8000
Interactive API docs:
    http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.config import settings
from app.db import Base, engine
from app.routers import admin, auth, chat, diet, habits, imu, sessions, ws
from app.services import mongo, mqtt_subscriber
from app.services.imu_bus import bus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("aigym")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start optional subsystems. None of them may block startup."""
    log.info("Starting %s v%s (env=%s)", settings.APP_NAME, __version__, settings.ENV)

    # Tables: harmless if Alembic already created them.
    try:
        from app import models  # noqa: F401

        Base.metadata.create_all(bind=engine)
        log.info("Database ready: %s", engine.url.render_as_string(hide_password=True))
    except Exception as exc:  # noqa: BLE001
        log.error("Database init failed: %s", exc)

    bus.bind_loop(asyncio.get_running_loop())
    log.info("MongoDB: %s", mongo.connect())
    log.info("MQTT: %s", mqtt_subscriber.start())

    yield

    mqtt_subscriber.stop()
    mongo.close()
    log.info("Shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    version=__version__,
    description=(
        "Camera + IMU sensor fusion for repetition counting and form validation, "
        "with diet planning and habit prediction."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_origin_regex=r"https://.*\.onrender\.com",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(sessions.router)
app.include_router(diet.router)
app.include_router(habits.router)
app.include_router(chat.router)
app.include_router(imu.router)
app.include_router(ws.router)


@app.get("/", tags=["meta"])
def root() -> dict[str, Any]:
    return {
        "name": settings.APP_NAME,
        "version": __version__,
        "docs": "/docs",
        "health": "/health",
        "websocket": "/ws/workout?token=<JWT>",
    }


@app.get("/health", tags=["meta"])
def health() -> dict[str, Any]:
    """
    Liveness + subsystem report.

    Render pings this to decide whether the service is up, so it must never
    depend on Mongo or MQTT being reachable - it reports their state instead.
    """
    db_ok, db_err = True, None
    try:
        from sqlalchemy import text

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        db_ok, db_err = False, f"{type(exc).__name__}: {exc}"

    return {
        "status": "ok" if db_ok else "degraded",
        "version": __version__,
        "database": {"ok": db_ok, "error": db_err,
                     "dialect": engine.url.get_backend_name()},
        "mongo": mongo.status(),
        "mqtt": mqtt_subscriber.status(),
        "llm": "enabled" if settings.llm_enabled else "rule-based coach",
        "imu_bus": bus.stats(),
    }


@app.exception_handler(Exception)
async def unhandled(request: Any, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error on %s", getattr(request, "url", "?"))
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal error: {type(exc).__name__}"},
    )
