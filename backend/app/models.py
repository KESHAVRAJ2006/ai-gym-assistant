"""
Relational schema (PostgreSQL in production, SQLite on your laptop).

Only structured, queryable data lives here. High-volume raw signal
(33 landmarks x 30 fps, raw IMU samples) goes to MongoDB instead - putting it
in Postgres would make the reps table unusable within one demo session.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), default="")

    # Anthropometrics - needed by diet_engine (Mifflin-St Jeor).
    age: Mapped[int] = mapped_column(Integer, default=21)
    sex: Mapped[str] = mapped_column(String(10), default="male")          # male | female
    height_cm: Mapped[float] = mapped_column(Float, default=170.0)
    weight_kg: Mapped[float] = mapped_column(Float, default=65.0)
    activity_level: Mapped[str] = mapped_column(String(20), default="moderate")
    goal: Mapped[str] = mapped_column(String(20), default="maintain")     # cut | maintain | bulk
    device_id: Mapped[str] = mapped_column(String(64), default="")        # ESP32 pairing

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sessions: Mapped[list["WorkoutSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    diet_plans: Mapped[list["DietPlan"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    habit_logs: Mapped[list["HabitLog"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class WorkoutSession(Base):
    """One continuous bout of one exercise in front of the camera."""

    __tablename__ = "workout_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    exercise: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # The three counts we compare in the report.
    camera_reps: Mapped[int] = mapped_column(Integer, default=0)
    imu_reps: Mapped[int] = mapped_column(Integer, default=0)
    fused_reps: Mapped[int] = mapped_column(Integer, default=0)
    ground_truth_reps: Mapped[int | None] = mapped_column(Integer, nullable=True)

    avg_form_score: Mapped[float] = mapped_column(Float, default=0.0)
    mean_visibility: Mapped[float] = mapped_column(Float, default=0.0)
    clock_offset_s: Mapped[float] = mapped_column(Float, default=0.0)
    device_id: Mapped[str] = mapped_column(String(64), default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    user: Mapped["User"] = relationship(back_populates="sessions")
    reps: Mapped[list["Rep"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class Rep(Base):
    """
    One repetition as decided by one modality.

    `source` is the whole point of this table: 'camera', 'imu' and 'fused'
    rows coexist so the evaluation can be recomputed after the fact without
    re-running any session.
    """

    __tablename__ = "reps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("workout_sessions.id", ondelete="CASCADE"), index=True
    )

    rep_index: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(10), default="camera")

    start_ts: Mapped[float] = mapped_column(Float, default=0.0)   # epoch seconds
    end_ts: Mapped[float] = mapped_column(Float, default=0.0)
    tempo_s: Mapped[float] = mapped_column(Float, default=0.0)
    rom_deg: Mapped[float] = mapped_column(Float, default=0.0)
    peak_angle: Mapped[float] = mapped_column(Float, default=0.0)
    min_angle: Mapped[float] = mapped_column(Float, default=0.0)

    form_score: Mapped[float] = mapped_column(Float, default=1.0)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    matched: Mapped[bool] = mapped_column(Boolean, default=False)
    flags: Mapped[list] = mapped_column(JSON, default=list)

    session: Mapped["WorkoutSession"] = relationship(back_populates="reps")


class DietPlan(Base):
    __tablename__ = "diet_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    bmr: Mapped[float] = mapped_column(Float, default=0.0)
    tdee: Mapped[float] = mapped_column(Float, default=0.0)
    target_kcal: Mapped[float] = mapped_column(Float, default=0.0)
    protein_g: Mapped[float] = mapped_column(Float, default=0.0)
    carbs_g: Mapped[float] = mapped_column(Float, default=0.0)
    fat_g: Mapped[float] = mapped_column(Float, default=0.0)
    diet_type: Mapped[str] = mapped_column(String(20), default="veg")
    meals: Mapped[dict] = mapped_column(JSON, default=dict)

    user: Mapped["User"] = relationship(back_populates="diet_plans")


class HabitLog(Base):
    """One row per user per day - the training table for habit_model."""

    __tablename__ = "habit_logs"
    __table_args__ = (UniqueConstraint("user_id", "log_date", name="uq_habit_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    log_date: Mapped[date] = mapped_column(Date, default=lambda: datetime.now(timezone.utc).date())

    planned: Mapped[bool] = mapped_column(Boolean, default=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_min: Mapped[float] = mapped_column(Float, default=0.0)
    sleep_h: Mapped[float] = mapped_column(Float, default=7.0)
    soreness: Mapped[int] = mapped_column(Integer, default=2)     # 0-5
    mood: Mapped[int] = mapped_column(Integer, default=3)         # 1-5
    planned_hour: Mapped[int] = mapped_column(Integer, default=18)

    user: Mapped["User"] = relationship(back_populates="habit_logs")
