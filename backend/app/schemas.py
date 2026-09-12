"""
Pydantic request/response models.

These are the API contract with the React client. Keeping them separate from
the SQLAlchemy models means we never accidentally leak `hashed_password` to
the browser.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Sex = Literal["male", "female"]
Goal = Literal["cut", "maintain", "bulk"]
Activity = Literal["sedentary", "light", "moderate", "active", "very_active"]


# ----------------------------------------------------------------- auth ----
class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    full_name: str = ""
    age: int = Field(default=21, ge=10, le=100)
    sex: Sex = "male"
    height_cm: float = Field(default=170.0, ge=100, le=250)
    weight_kg: float = Field(default=65.0, ge=25, le=300)
    activity_level: Activity = "moderate"
    goal: Goal = "maintain"


class UserUpdate(BaseModel):
    full_name: str | None = None
    age: int | None = Field(default=None, ge=10, le=100)
    sex: Sex | None = None
    height_cm: float | None = Field(default=None, ge=100, le=250)
    weight_kg: float | None = Field(default=None, ge=25, le=300)
    activity_level: Activity | None = None
    goal: Goal | None = None
    device_id: str | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    full_name: str
    age: int
    sex: str
    height_cm: float
    weight_kg: float
    activity_level: str
    goal: str
    device_id: str
    created_at: datetime
    is_admin: bool = False


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


# ------------------------------------------------------------- sessions ----
class RepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    rep_index: int
    source: str
    start_ts: float
    end_ts: float
    tempo_s: float
    rom_deg: float
    form_score: float
    confidence: float
    matched: bool
    flags: list[Any]


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    exercise: str
    started_at: datetime
    ended_at: datetime | None
    camera_reps: int
    imu_reps: int
    fused_reps: int
    ground_truth_reps: int | None
    avg_form_score: float
    mean_visibility: float
    clock_offset_s: float
    device_id: str
    notes: str


class SessionDetail(SessionOut):
    reps: list[RepOut] = []


class SessionPatch(BaseModel):
    """Records the human rep count after a session, for evaluation."""

    ground_truth_reps: int | None = Field(default=None, ge=0, le=1000)
    notes: str | None = None


# ----------------------------------------------------------------- diet ----
class DietRequest(BaseModel):
    diet_type: Literal["veg", "nonveg", "vegan"] = "veg"
    meals_per_day: int = Field(default=4, ge=3, le=6)


class MacroTargets(BaseModel):
    bmr: float
    tdee: float
    target_kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float


class MealItem(BaseModel):
    name: str
    grams: float
    kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float


class Meal(BaseModel):
    slot: str
    items: list[MealItem]
    kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float


class DietPlanOut(BaseModel):
    id: int | None = None
    created_at: datetime | None = None
    diet_type: str
    targets: MacroTargets
    meals: list[Meal]
    achieved: MacroTargets


# --------------------------------------------------------------- habits ----
class HabitLogIn(BaseModel):
    log_date: date | None = None
    planned: bool = True
    completed: bool = False
    duration_min: float = Field(default=0.0, ge=0, le=600)
    sleep_h: float = Field(default=7.0, ge=0, le=16)
    soreness: int = Field(default=2, ge=0, le=5)
    mood: int = Field(default=3, ge=1, le=5)
    planned_hour: int = Field(default=18, ge=0, le=23)


class HabitLogOut(HabitLogIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    log_date: date


class SkipRiskOut(BaseModel):
    skip_probability: float
    risk_band: str
    top_drivers: list[str]
    recommendation: str
    model_kind: str


# ----------------------------------------------------------------- chat ----
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    reply: str
    source: str  # "llm" or "rules"
    context_used: dict


# -------------------------------------------------------------- IMU/HTTP ---
class ImuIngest(BaseModel):
    """HTTP fallback for the ESP32 when no MQTT broker is available."""

    device_id: str
    t: float  # device epoch seconds
    ax: float
    ay: float
    az: float
    gx: float = 0.0
    gy: float = 0.0
    gz: float = 0.0


class ImuBatch(BaseModel):
    samples: list[ImuIngest]
