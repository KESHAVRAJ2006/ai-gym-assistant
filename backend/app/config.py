"""
Central configuration.

Every tunable value in the system is read from environment variables here and
nowhere else. Import `settings` anywhere you need a value.

Design rule for this project: the API must boot even when the OPTIONAL
services (MongoDB, MQTT, LLM) are missing. Only DATABASE_URL and JWT_SECRET
are load-bearing, and both have safe local defaults.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = BACKEND_DIR / "artifacts"
DATA_DIR = BACKEND_DIR / "data"
ARTIFACT_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- core ----
    APP_NAME: str = "AI Gym & Fitness Assistant"
    ENV: str = "development"
    DATABASE_URL: str = ""
    JWT_SECRET: str = "dev-only-insecure-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 7
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---- optional: mongo ----
    MONGO_URL: str = ""
    MONGO_DB: str = "aigym"

    # ---- optional: mqtt ----
    MQTT_HOST: str = ""
    MQTT_PORT: int = 1883
    MQTT_USERNAME: str = ""
    MQTT_PASSWORD: str = ""
    MQTT_TLS: int = 0
    MQTT_TOPIC: str = "gym/+/imu"

    # ---- optional: llm ----
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-haiku-4-5-20251001"

    # ---- fusion tuning (these are the knobs the report sweeps) ----
    FUSION_MATCH_WINDOW_S: float = 0.60
    FUSION_MAX_OFFSET_S: float = 2.50
    FUSION_MIN_CAMERA_VIS: float = 0.55

    @property
    def sqlalchemy_url(self) -> str:
        """
        Normalise whatever the host gave us into a SQLAlchemy 2.x URL.

        Render hands out `postgres://user:pw@host/db`, which SQLAlchemy
        rejected years ago. We also force the psycopg (v3) driver because
        psycopg2 has no wheels on newer Pythons.
        """
        url = self.DATABASE_URL.strip()
        if not url:
            return f"sqlite:///{BACKEND_DIR / 'aigym_local.db'}"
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url

    @property
    def cors_list(self) -> list[str]:
        raw = [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]
        return raw or ["*"]

    @property
    def mongo_enabled(self) -> bool:
        return bool(self.MONGO_URL.strip())

    @property
    def mqtt_enabled(self) -> bool:
        return bool(self.MQTT_HOST.strip())

    @property
    def llm_enabled(self) -> bool:
        return bool(self.ANTHROPIC_API_KEY.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

# Render injects PORT; keep it visible for logging.
PORT = int(os.getenv("PORT", "8000"))
