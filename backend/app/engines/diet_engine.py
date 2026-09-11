"""
diet_engine - Mifflin-St Jeor BMR, macro split, and a deterministic meal plan.

Deliberately not an optimiser. A linear program over a 60-item food table
would look clever and be impossible to defend in a viva when it returns
900 g of paneer. Instead: published equations, published activity factors,
published protein recommendations, then a greedy portion solver whose every
step you can recite.

References to cite in the report (log these in docs/CITATIONS.md):
  - Mifflin MD et al., "A new predictive equation for resting energy
    expenditure in healthy individuals", Am J Clin Nutr, 1990.
  - Morton RW et al., "A systematic review, meta-analysis of protein
    supplementation on resistance-trained individuals", BJSM, 2018.
  - ICMR-NIN, "Nutrient Requirements for Indians", 2020 (food composition).
"""
from __future__ import annotations

from typing import Any, Literal

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
ACTIVITY_FACTORS: dict[str, float] = {
    "sedentary": 1.200,     # desk job, no training
    "light": 1.375,         # 1-3 sessions/week
    "moderate": 1.550,      # 3-5 sessions/week
    "active": 1.725,        # 6-7 sessions/week
    "very_active": 1.900,   # twice-daily / physical job
}

GOAL_ADJUST: dict[str, float] = {
    "cut": -0.20,           # 20% deficit
    "maintain": 0.0,
    "bulk": +0.12,          # 12% surplus - lean gain, not a dirty bulk
}

PROTEIN_G_PER_KG: dict[str, float] = {"cut": 2.0, "maintain": 1.7, "bulk": 1.8}
FAT_FRACTION_OF_KCAL = 0.25
KCAL_PER_G = {"protein": 4.0, "carbs": 4.0, "fat": 9.0}

MEAL_SPLITS: dict[int, list[tuple[str, float]]] = {
    3: [("Breakfast", 0.32), ("Lunch", 0.38), ("Dinner", 0.30)],
    4: [("Breakfast", 0.27), ("Lunch", 0.32), ("Snack", 0.15), ("Dinner", 0.26)],
    5: [("Breakfast", 0.24), ("Mid-morning", 0.11), ("Lunch", 0.29),
        ("Snack", 0.13), ("Dinner", 0.23)],
    6: [("Breakfast", 0.21), ("Mid-morning", 0.10), ("Lunch", 0.26),
        ("Snack", 0.12), ("Dinner", 0.21), ("Before bed", 0.10)],
}


# --------------------------------------------------------------------------
# Food table: values per 100 g edible portion.
# tags: v = vegetarian, e = contains egg, n = non-veg, d = dairy, vg = vegan
# --------------------------------------------------------------------------
FOODS: list[dict[str, Any]] = [
    # ---- protein anchors ----
    {"name": "Paneer (low fat)",      "kcal": 206, "p": 18.0, "c": 3.4,  "f": 14.0, "role": "protein", "tags": "v,d",  "max_g": 200},
    {"name": "Tofu",                  "kcal": 144, "p": 15.8, "c": 2.8,  "f": 8.7,  "role": "protein", "tags": "v,vg", "max_g": 250},
    {"name": "Greek yoghurt",         "kcal": 59,  "p": 10.0, "c": 3.6,  "f": 0.4,  "role": "protein", "tags": "v,d",  "max_g": 400},
    {"name": "Whole egg",             "kcal": 155, "p": 13.0, "c": 1.1,  "f": 11.0, "role": "protein", "tags": "v,e",  "max_g": 200},
    {"name": "Chicken breast",        "kcal": 165, "p": 31.0, "c": 0.0,  "f": 3.6,  "role": "protein", "tags": "n",    "max_g": 300},
    {"name": "Fish (rohu)",           "kcal": 97,  "p": 16.6, "c": 0.0,  "f": 1.4,  "role": "protein", "tags": "n",    "max_g": 300},
    {"name": "Soya chunks (dry)",     "kcal": 345, "p": 52.0, "c": 33.0, "f": 0.5,  "role": "protein", "tags": "v,vg", "max_g": 90},
    {"name": "Rajma (cooked)",        "kcal": 127, "p": 8.7,  "c": 22.8, "f": 0.5,  "role": "protein", "tags": "v,vg", "max_g": 300},
    {"name": "Chana / chickpeas",     "kcal": 164, "p": 8.9,  "c": 27.4, "f": 2.6,  "role": "protein", "tags": "v,vg", "max_g": 250},
    {"name": "Moong dal (cooked)",    "kcal": 105, "p": 7.0,  "c": 19.0, "f": 0.4,  "role": "protein", "tags": "v,vg", "max_g": 350},
    {"name": "Whey protein",          "kcal": 375, "p": 75.0, "c": 8.0,  "f": 4.0,  "role": "protein", "tags": "v,d",  "max_g": 60},

    # ---- carb anchors ----
    {"name": "Cooked rice",           "kcal": 130, "p": 2.7,  "c": 28.0, "f": 0.3,  "role": "carb", "tags": "v,vg", "max_g": 400},
    {"name": "Roti (whole wheat)",    "kcal": 297, "p": 11.0, "c": 58.0, "f": 3.7,  "role": "carb", "tags": "v,vg", "max_g": 180},
    {"name": "Rolled oats (dry)",     "kcal": 389, "p": 16.9, "c": 66.3, "f": 6.9,  "role": "carb", "tags": "v,vg", "max_g": 120},
    {"name": "Poha (dry)",            "kcal": 346, "p": 6.6,  "c": 77.0, "f": 1.2,  "role": "carb", "tags": "v,vg", "max_g": 120},
    {"name": "Sweet potato",          "kcal": 86,  "p": 1.6,  "c": 20.1, "f": 0.1,  "role": "carb", "tags": "v,vg", "max_g": 400},
    {"name": "Banana",                "kcal": 89,  "p": 1.1,  "c": 22.8, "f": 0.3,  "role": "carb", "tags": "v,vg", "max_g": 300},
    {"name": "Brown bread",           "kcal": 247, "p": 13.0, "c": 41.0, "f": 3.4,  "role": "carb", "tags": "v,vg", "max_g": 150},

    # ---- fat anchors ----
    {"name": "Peanut butter",         "kcal": 588, "p": 25.0, "c": 20.0, "f": 50.0, "role": "fat", "tags": "v,vg", "max_g": 60},
    {"name": "Almonds",               "kcal": 579, "p": 21.2, "c": 21.6, "f": 49.9, "role": "fat", "tags": "v,vg", "max_g": 50},
    {"name": "Ghee",                  "kcal": 900, "p": 0.0,  "c": 0.0,  "f": 100.0,"role": "fat", "tags": "v,d",  "max_g": 25},
    {"name": "Olive oil",             "kcal": 884, "p": 0.0,  "c": 0.0,  "f": 100.0,"role": "fat", "tags": "v,vg", "max_g": 25},
    {"name": "Walnuts",               "kcal": 654, "p": 15.2, "c": 13.7, "f": 65.2, "role": "fat", "tags": "v,vg", "max_g": 45},

    # ---- vegetables / fibre (fixed portions) ----
    {"name": "Mixed salad",           "kcal": 25,  "p": 1.3,  "c": 4.5,  "f": 0.2,  "role": "veg", "tags": "v,vg", "max_g": 200},
    {"name": "Palak / spinach",       "kcal": 23,  "p": 2.9,  "c": 3.6,  "f": 0.4,  "role": "veg", "tags": "v,vg", "max_g": 200},
    {"name": "Bhindi / okra",         "kcal": 33,  "p": 1.9,  "c": 7.5,  "f": 0.2,  "role": "veg", "tags": "v,vg", "max_g": 200},
    {"name": "Broccoli",              "kcal": 34,  "p": 2.8,  "c": 6.6,  "f": 0.4,  "role": "veg", "tags": "v,vg", "max_g": 200},
]

DietType = Literal["veg", "nonveg", "vegan"]


def _allowed(food: dict[str, Any], diet_type: str) -> bool:
    tags = set(food["tags"].split(","))
    if diet_type == "vegan":
        return "vg" in tags
    if diet_type == "veg":
        return "n" not in tags
    return True  # nonveg eats everything


# --------------------------------------------------------------------------
# 1. Energy and macros
# --------------------------------------------------------------------------
def mifflin_st_jeor(weight_kg: float, height_cm: float, age: int, sex: str) -> float:
    """
    Resting metabolic rate, kcal/day.

        male   : 10W + 6.25H - 5A + 5
        female : 10W + 6.25H - 5A - 161
    """
    base = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age
    return base + (5.0 if sex.lower().startswith("m") else -161.0)


def macro_targets(
    weight_kg: float,
    height_cm: float,
    age: int,
    sex: str,
    activity_level: str = "moderate",
    goal: str = "maintain",
) -> dict[str, float]:
    bmr = mifflin_st_jeor(weight_kg, height_cm, age, sex)
    tdee = bmr * ACTIVITY_FACTORS.get(activity_level, 1.55)
    target = tdee * (1.0 + GOAL_ADJUST.get(goal, 0.0))
    # Never prescribe below BMR - that is the line between a diet plan and harm.
    target = max(target, bmr * 1.05)

    protein_g = PROTEIN_G_PER_KG.get(goal, 1.7) * weight_kg
    fat_g = (target * FAT_FRACTION_OF_KCAL) / KCAL_PER_G["fat"]
    remaining = target - protein_g * KCAL_PER_G["protein"] - fat_g * KCAL_PER_G["fat"]
    carbs_g = max(40.0, remaining / KCAL_PER_G["carbs"])

    return {
        "bmr": round(bmr, 1),
        "tdee": round(tdee, 1),
        "target_kcal": round(target, 1),
        "protein_g": round(protein_g, 1),
        "carbs_g": round(carbs_g, 1),
        "fat_g": round(fat_g, 1),
    }


# --------------------------------------------------------------------------
# 2. Meal construction
# --------------------------------------------------------------------------
def _portion(food: dict[str, Any], grams: float) -> dict[str, Any]:
    g = max(0.0, min(grams, float(food["max_g"])))
    g = round(g / 5.0) * 5.0                      # kitchen-realistic 5 g steps
    k = g / 100.0
    return {
        "name": food["name"],
        "grams": g,
        "kcal": round(food["kcal"] * k, 1),
        "protein_g": round(food["p"] * k, 1),
        "carbs_g": round(food["c"] * k, 1),
        "fat_g": round(food["f"] * k, 1),
    }


def _pick(pool: list[dict[str, Any]], i: int) -> dict[str, Any]:
    return pool[i % len(pool)]


def _clamp(grams: float, max_g: float) -> float:
    return max(0.0, min(grams, float(max_g)))


def _solve_portions(
    pf: dict[str, Any], cf: dict[str, Any], ff: dict[str, Any],
    p_t: float, c_t: float, f_t: float,
    base_p: float, base_c: float, base_f: float,
    rounds: int = 5,
) -> tuple[float, float, float]:
    """
    Coordinate descent on three portion sizes against three macro targets.

    A single protein-then-carb-then-fat pass overshoots badly, because rice,
    roti and oats are themselves 7-17% protein: that protein is invisible to
    a pass that has already fixed the paneer portion. Each round re-solves
    one food against what the other two currently supply, which converges to
    within a few grams and is still something you can work through by hand
    on a whiteboard - unlike the linear program this replaces.
    """
    g_p = g_c = g_f = 0.0
    for _ in range(rounds):
        have_p = base_p + (cf["p"] * g_c + ff["p"] * g_f) / 100.0
        g_p = _clamp(100.0 * (p_t - have_p) / max(1e-6, pf["p"]), pf["max_g"])

        have_c = base_c + (pf["c"] * g_p + ff["c"] * g_f) / 100.0
        g_c = _clamp(100.0 * (c_t - have_c) / max(1e-6, cf["c"]), cf["max_g"])

        have_f = base_f + (pf["f"] * g_p + cf["f"] * g_c) / 100.0
        g_f = _clamp(100.0 * (f_t - have_f) / max(1e-6, ff["f"]), ff["max_g"])
    return g_p, g_c, g_f


def build_meals(targets: dict[str, float], diet_type: str = "veg",
                meals_per_day: int = 4) -> list[dict[str, Any]]:
    """
    Greedy portion solver, one meal at a time.

    Order matters: protein first (hardest macro to hit and the one the user
    actually cares about), then carbohydrate to fill the energy gap, then fat
    last because fat is calorically dense and makes the best final adjustment.
    """
    splits = MEAL_SPLITS.get(meals_per_day, MEAL_SPLITS[4])
    proteins = [f for f in FOODS if f["role"] == "protein" and _allowed(f, diet_type)]
    carbs = [f for f in FOODS if f["role"] == "carb" and _allowed(f, diet_type)]
    fats = [f for f in FOODS if f["role"] == "fat" and _allowed(f, diet_type)]
    vegs = [f for f in FOODS if f["role"] == "veg" and _allowed(f, diet_type)]

    meals: list[dict[str, Any]] = []
    used: list[str] = []          # variety penalty: foods already used today
    for i, (slot, frac) in enumerate(splits):
        p_t = targets["protein_g"] * frac
        c_t = targets["carbs_g"] * frac
        f_t = targets["fat_g"] * frac

        cf = _pick(carbs, i)
        veg = _pick(vegs, i) if slot in ("Lunch", "Dinner") else None
        veg_g = 150.0 if veg else 0.0

        # Fixed contribution from the vegetable portion.
        base_p = (veg["p"] * veg_g / 100.0) if veg else 0.0
        base_c = (veg["c"] * veg_g / 100.0) if veg else 0.0
        base_f = (veg["f"] * veg_g / 100.0) if veg else 0.0

        # ---- choose the anchors, then solve the portions ----
        # Rotating blindly through the protein list cannot hit a low-fat
        # target: on a cut, paneer and egg blow the fat budget before the oil
        # is even considered, the fat portion clamps to zero and the meal is
        # still 30% over. So every (protein, fat) anchor pair is costed and
        # the cheapest kept, with a small penalty on foods already used today
        # so the day does not become four identical plates.
        best: tuple[float, Any, Any, float, float, float] | None = None
        for pf in proteins:
            for ff in fats:
                g_p, g_c, g_f = _solve_portions(
                    pf, cf, ff, p_t, c_t, f_t, base_p, base_c, base_f
                )
                got_p = base_p + (pf["p"] * g_p + cf["p"] * g_c + ff["p"] * g_f) / 100.0
                got_c = base_c + (pf["c"] * g_p + cf["c"] * g_c + ff["c"] * g_f) / 100.0
                got_f = base_f + (pf["f"] * g_p + cf["f"] * g_c + ff["f"] * g_f) / 100.0
                cost = (abs(got_p - p_t) / max(1.0, p_t)
                        + abs(got_c - c_t) / max(1.0, c_t)
                        + abs(got_f - f_t) / max(1.0, f_t))
                cost += 0.25 * used.count(pf["name"]) + 0.10 * used.count(ff["name"])
                if best is None or cost < best[0]:
                    best = (cost, pf, ff, g_p, g_c, g_f)

        _, pf, ff, g_p, g_c, g_f = best  # type: ignore[misc]
        used.append(pf["name"])
        used.append(ff["name"])

        items: list[dict[str, Any]] = [_portion(pf, g_p), _portion(cf, g_c)]
        if g_f >= 2.5:                       # skip a pointless 1 g of oil
            items.append(_portion(ff, g_f))
        if veg is not None:
            items.append(_portion(veg, veg_g))

        items = [it for it in items if it["grams"] > 0]
        meals.append({
            "slot": slot,
            "items": items,
            "kcal": round(sum(it["kcal"] for it in items), 1),
            "protein_g": round(sum(it["protein_g"] for it in items), 1),
            "carbs_g": round(sum(it["carbs_g"] for it in items), 1),
            "fat_g": round(sum(it["fat_g"] for it in items), 1),
        })
    return meals


def achieved_totals(meals: list[dict[str, Any]], targets: dict[str, float]) -> dict[str, float]:
    return {
        "bmr": targets["bmr"],
        "tdee": targets["tdee"],
        "target_kcal": round(sum(m["kcal"] for m in meals), 1),
        "protein_g": round(sum(m["protein_g"] for m in meals), 1),
        "carbs_g": round(sum(m["carbs_g"] for m in meals), 1),
        "fat_g": round(sum(m["fat_g"] for m in meals), 1),
    }


def generate_plan(
    weight_kg: float,
    height_cm: float,
    age: int,
    sex: str,
    activity_level: str = "moderate",
    goal: str = "maintain",
    diet_type: str = "veg",
    meals_per_day: int = 4,
) -> dict[str, Any]:
    targets = macro_targets(weight_kg, height_cm, age, sex, activity_level, goal)
    meals = build_meals(targets, diet_type, meals_per_day)
    return {
        "diet_type": diet_type,
        "targets": targets,
        "meals": meals,
        "achieved": achieved_totals(meals, targets),
    }
