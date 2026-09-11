"""Diet planning - /api/diet/*."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.engines import diet_engine
from app.models import DietPlan, User
from app.schemas import DietPlanOut, DietRequest

router = APIRouter(prefix="/api/diet", tags=["diet"])


def _to_out(plan: dict[str, Any], row: DietPlan | None = None) -> DietPlanOut:
    return DietPlanOut(
        id=row.id if row else None,
        created_at=row.created_at if row else None,
        diet_type=plan["diet_type"],
        targets=plan["targets"],
        meals=plan["meals"],
        achieved=plan["achieved"],
    )


@router.post("/generate", response_model=DietPlanOut)
def generate(
    payload: DietRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DietPlanOut:
    plan = diet_engine.generate_plan(
        weight_kg=user.weight_kg,
        height_cm=user.height_cm,
        age=user.age,
        sex=user.sex,
        activity_level=user.activity_level,
        goal=user.goal,
        diet_type=payload.diet_type,
        meals_per_day=payload.meals_per_day,
    )
    row = DietPlan(
        user_id=user.id,
        bmr=plan["targets"]["bmr"],
        tdee=plan["targets"]["tdee"],
        target_kcal=plan["targets"]["target_kcal"],
        protein_g=plan["targets"]["protein_g"],
        carbs_g=plan["targets"]["carbs_g"],
        fat_g=plan["targets"]["fat_g"],
        diet_type=payload.diet_type,
        meals={"meals": plan["meals"], "achieved": plan["achieved"]},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(plan, row)


@router.get("/latest", response_model=DietPlanOut | None)
def latest(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DietPlanOut | None:
    row = db.scalar(
        select(DietPlan)
        .where(DietPlan.user_id == user.id)
        .order_by(DietPlan.created_at.desc())
        .limit(1)
    )
    if row is None:
        return None
    payload = row.meals or {}
    plan = {
        "diet_type": row.diet_type,
        "targets": {
            "bmr": row.bmr, "tdee": row.tdee, "target_kcal": row.target_kcal,
            "protein_g": row.protein_g, "carbs_g": row.carbs_g, "fat_g": row.fat_g,
        },
        "meals": payload.get("meals", []),
        "achieved": payload.get("achieved", {
            "bmr": row.bmr, "tdee": row.tdee, "target_kcal": row.target_kcal,
            "protein_g": row.protein_g, "carbs_g": row.carbs_g, "fat_g": row.fat_g,
        }),
    }
    return _to_out(plan, row)


@router.get("/foods")
def foods() -> list[dict[str, Any]]:
    """The food composition table, so the UI can show where numbers came from."""
    return diet_engine.FOODS
