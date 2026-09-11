"""Coaching chat - /api/chat."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.engines import chat_service, habit_model
from app.models import DietPlan, HabitLog, User, WorkoutSession
from app.schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatResponse:
    sessions = db.scalars(
        select(WorkoutSession)
        .where(WorkoutSession.user_id == user.id)
        .order_by(WorkoutSession.started_at.desc())
        .limit(8)
    ).all()

    plan_row = db.scalar(
        select(DietPlan)
        .where(DietPlan.user_id == user.id)
        .order_by(DietPlan.created_at.desc())
        .limit(1)
    )
    plan = None
    if plan_row is not None:
        plan = {
            "targets": {
                "target_kcal": plan_row.target_kcal,
                "protein_g": plan_row.protein_g,
                "carbs_g": plan_row.carbs_g,
                "fat_g": plan_row.fat_g,
            }
        }

    logs = db.scalars(select(HabitLog).where(HabitLog.user_id == user.id)).all()
    risk = None
    if logs:
        feats = habit_model.features_from_logs(list(logs), target=date.today())
        r = habit_model.predict(feats)
        risk = {
            "skip_probability": r.probability,
            "risk_band": r.band,
            "top_drivers": r.drivers,
            "recommendation": r.recommendation,
        }

    ctx = chat_service.build_context(user, list(sessions), plan, risk)
    text, source = await chat_service.reply(payload.message, ctx)
    return ChatResponse(reply=text, source=source, context_used=ctx)
