"""
chat_service - the coaching chat endpoint.

Two implementations behind one interface:

  * `_rules_reply`  - a deterministic coach built from the user's own numbers.
    Always available, costs nothing, and is what the demo runs on if the
    network or the API key is missing. Your viva should never depend on an
    external API being up.
  * `_llm_reply`    - Claude, called through the official Anthropic Python
    SDK with the user's stats injected into the system prompt.

Set ANTHROPIC_API_KEY in the environment to switch the LLM path on. The
response object reports which path answered, so the UI can label it honestly.
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the in-app coach for an AI gym assistant used by a \
college student in India. You are given the user's measured training and \
nutrition numbers. Ground every answer in those numbers and quote them.

Rules:
- Be concise: at most 150 words unless asked for detail.
- Never invent numbers. If a stat is missing, say it is not tracked yet.
- You are not a doctor. For pain, injury, dizziness or medication questions, \
say plainly that this needs a clinician, and stop there.
- Prefer one concrete next action over a list of general advice."""


def build_context(user: Any, recent_sessions: list[Any], plan: dict | None,
                  skip_risk: dict | None) -> dict[str, Any]:
    """Collect the facts the coach is allowed to talk about."""
    sessions = [
        {
            "exercise": s.exercise,
            "date": s.started_at.date().isoformat() if s.started_at else None,
            "fused_reps": s.fused_reps,
            "camera_reps": s.camera_reps,
            "imu_reps": s.imu_reps,
            "avg_form_score": round(s.avg_form_score, 2),
        }
        for s in recent_sessions[:8]
    ]
    total = sum(s["fused_reps"] for s in sessions)
    forms = [s["avg_form_score"] for s in sessions if s["avg_form_score"]]
    return {
        "profile": {
            "name": user.full_name or "athlete",
            "age": user.age,
            "sex": user.sex,
            "height_cm": user.height_cm,
            "weight_kg": user.weight_kg,
            "goal": user.goal,
            "activity_level": user.activity_level,
        },
        "training": {
            "sessions_logged": len(sessions),
            "total_reps_recent": total,
            "mean_form_score": round(sum(forms) / len(forms), 2) if forms else None,
            "recent": sessions,
        },
        "nutrition": (
            {
                "target_kcal": plan["targets"]["target_kcal"],
                "protein_g": plan["targets"]["protein_g"],
                "carbs_g": plan["targets"]["carbs_g"],
                "fat_g": plan["targets"]["fat_g"],
            }
            if plan else None
        ),
        "habit": skip_risk,
    }


# --------------------------------------------------------------------------
# Rule-based coach
# --------------------------------------------------------------------------
def _rules_reply(message: str, ctx: dict[str, Any]) -> str:
    m = message.lower()
    prof = ctx["profile"]
    tr = ctx["training"]
    nut = ctx["nutrition"]
    habit = ctx["habit"]

    medical = ("pain", "hurt", "injur", "dizzy", "sharp", "swollen", "medic")
    if any(w in m for w in medical):
        return (
            "That sounds like something to get looked at rather than trained "
            "through. Stop the movement that provokes it and see a clinician. "
            "I can only help with training and nutrition planning."
        )

    if any(w in m for w in ("protein", "calorie", "kcal", "diet", "eat", "meal", "macro")):
        if not nut:
            return ("You have not generated a diet plan yet. Open the Diet tab "
                    "and press Generate - it needs your weight, height and goal.")
        return (
            f"Your plan targets {nut['target_kcal']:.0f} kcal a day with "
            f"{nut['protein_g']:.0f} g protein, {nut['carbs_g']:.0f} g carbs and "
            f"{nut['fat_g']:.0f} g fat, based on a {prof['goal']} goal at "
            f"{prof['weight_kg']:.0f} kg. Protein is the one to hit first - split it "
            f"across four meals at roughly {nut['protein_g'] / 4:.0f} g each, "
            "which is far easier than chasing it at dinner."
        )

    if any(w in m for w in ("form", "technique", "posture", "score")):
        if tr["mean_form_score"] is None:
            return ("No form scores yet. Record one workout in the Workout tab and "
                    "I will have something concrete to comment on.")
        score = tr["mean_form_score"]
        if score >= 0.85:
            return (f"Your mean form score is {score:.2f} across "
                    f"{tr['sessions_logged']} sessions - that is clean. Add load or "
                    "slow the eccentric to three seconds before adding reps.")
        return (f"Your mean form score is {score:.2f}. That is usually range of "
                "motion, not strength. Drop the weight about 20 percent and hold "
                "the end position for one count - the score responds to that fast.")

    if any(w in m for w in ("skip", "motivat", "lazy", "habit", "streak")):
        if not habit:
            return ("Log a few days in the Habits tab and I can predict your skip "
                    "risk and tell you which day is the weak one.")
        return (f"Your predicted skip risk today is {habit['skip_probability'] * 100:.0f} "
                f"percent ({habit['risk_band']}). Main driver: "
                f"{habit['top_drivers'][0]}. {habit['recommendation']}")

    if any(w in m for w in ("rep", "workout", "session", "progress", "count")):
        if not tr["sessions_logged"]:
            return ("No sessions recorded yet. Start with the Workout tab - pick "
                    "bicep curl, allow the camera, and do ten reps.")
        return (f"Across your last {tr['sessions_logged']} sessions you logged "
                f"{tr['total_reps_recent']} fused reps. Fused means the camera and "
                "the IMU agreed, or one of them had a documented reason to be "
                "trusted alone - it is the number to quote, not the raw camera count.")

    return (f"Ask me about your reps, your form scores, your macros or your skip "
            f"risk, {prof['name']}. I answer from your logged numbers only - "
            f"right now that is {tr['sessions_logged']} sessions and "
            f"{tr['total_reps_recent']} reps.")


# --------------------------------------------------------------------------
# LLM coach
# --------------------------------------------------------------------------
async def _llm_reply(message: str, ctx: dict[str, Any]) -> str:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    resp = await client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Here are my current measured stats as JSON:\n{ctx}\n\n"
                    f"My question: {message}"
                ),
            }
        ],
    )
    if getattr(resp, "stop_reason", None) == "refusal":
        return ("I cannot answer that one. Ask me about your training numbers, "
                "your macros, or your schedule.")
    parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    return "\n".join(parts).strip() or "I did not get a usable answer - try rephrasing."


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
async def reply(message: str, ctx: dict[str, Any]) -> tuple[str, str]:
    """Returns (reply_text, source) where source is 'llm' or 'rules'."""
    if settings.llm_enabled:
        try:
            return await _llm_reply(message, ctx), "llm"
        except Exception as exc:  # noqa: BLE001 - never fail the chat endpoint
            log.warning("LLM coach unavailable, falling back to rules: %s", exc)
    return _rules_reply(message, ctx), "rules"
