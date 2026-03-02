"""TDEE (Total Daily Energy Expenditure) calculator — two-phase adaptive system.

Phase 1: Formula-based estimate (Mifflin-St Jeor or Katch-McArdle).
Phase 2: Adaptive refinement from real weight + calorie data (weekly).

Zoe's unique advantage: WHOOP strain + Strava calories feed the adaptive model.
"""
import logging
import math
from datetime import date, timedelta

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)

# Activity multipliers (Harris-Benedict scale, validated by ISSN)
ACTIVITY_MULTIPLIERS = {
    "sedentary": 1.2,        # Desk job, no exercise
    "lightly_active": 1.375,  # 1-3 days/week light exercise
    "moderately_active": 1.55,  # 3-5 days/week moderate exercise
    "very_active": 1.725,     # 6-7 days/week hard exercise
    "extremely_active": 1.9,  # Athlete, physical job + training
}

# Goal-based calorie adjustments (kcal/day)
GOAL_ADJUSTMENTS = {
    "lose_fast": -750,    # ~0.7 kg/week (aggressive cut)
    "lose": -500,         # ~0.45 kg/week (standard cut)
    "lose_slow": -250,    # ~0.25 kg/week (lean cut)
    "maintain": 0,
    "gain_slow": 250,     # ~0.25 kg/week (lean bulk)
    "gain": 500,          # ~0.45 kg/week (standard bulk)
}


def calculate_bmr(sex: str, age: int, height_cm: float, weight_kg: float,
                  body_fat_pct: float = None) -> float:
    """Calculate Basal Metabolic Rate.

    Uses Katch-McArdle if body fat % is known (more accurate for lean/muscular),
    otherwise falls back to Mifflin-St Jeor (most accurate general formula).
    """
    if body_fat_pct and 3 <= body_fat_pct <= 60:
        # Katch-McArdle: BMR = 370 + 21.6 * lean_mass_kg
        lean_mass = weight_kg * (1 - body_fat_pct / 100)
        bmr = 370 + 21.6 * lean_mass
        logger.info(f"BMR (Katch-McArdle): {bmr:.0f} kcal (LBM={lean_mass:.1f}kg)")
        return bmr

    # Mifflin-St Jeor (1990) — most accurate for general population
    # Male:   10 * weight(kg) + 6.25 * height(cm) - 5 * age - 5
    # Female: 10 * weight(kg) + 6.25 * height(cm) - 5 * age - 161
    sex_lower = (sex or "male").lower()
    if sex_lower in ("female", "f"):
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age - 161
    else:
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5

    logger.info(f"BMR (Mifflin-St Jeor): {bmr:.0f} kcal (sex={sex_lower})")
    return bmr


def calculate_tdee(sex: str, age: int, height_cm: float, weight_kg: float,
                   activity_level: str = "moderately_active",
                   body_fat_pct: float = None) -> float:
    """Calculate TDEE = BMR * activity multiplier."""
    bmr = calculate_bmr(sex, age, height_cm, weight_kg, body_fat_pct)
    multiplier = ACTIVITY_MULTIPLIERS.get(activity_level, 1.55)
    tdee = bmr * multiplier
    logger.info(f"TDEE: {tdee:.0f} kcal (activity={activity_level}, mult={multiplier})")
    return round(tdee)


def calculate_targets(sex: str, age: int, height_cm: float, weight_kg: float,
                      activity_level: str = "moderately_active",
                      goal: str = "maintain",
                      body_fat_pct: float = None) -> dict:
    """Calculate full nutrition targets: TDEE, calories, macros.

    Returns dict with all computed values ready to save to nutrition_profiles.
    """
    tdee = calculate_tdee(sex, age, height_cm, weight_kg, activity_level, body_fat_pct)
    adjustment = GOAL_ADJUSTMENTS.get(goal, 0)
    target_calories = max(1200, tdee + adjustment)  # Never go below 1200

    # Macro splits based on goal
    # Protein: 2g/kg (standard for active people, backed by ISSN)
    protein_g = round(weight_kg * 2)

    # Fat: 25-30% of target calories (essential hormones, brain function)
    fat_pct = 0.28 if goal in ("lose_fast", "lose") else 0.30
    fat_g = round(target_calories * fat_pct / 9)

    # Carbs: remainder
    protein_cal = protein_g * 4
    fat_cal = fat_g * 9
    carb_cal = max(0, target_calories - protein_cal - fat_cal)
    carbs_g = round(carb_cal / 4)

    result = {
        "tdee_calculated": tdee,
        "daily_calorie_target": round(target_calories),
        "protein_target_g": protein_g,
        "carbs_target_g": carbs_g,
        "fat_target_g": fat_g,
        "goal_adjustment": adjustment,
        "bmr": round(calculate_bmr(sex, age, height_cm, weight_kg, body_fat_pct)),
    }

    logger.info(
        f"Nutrition targets: {target_calories} cal "
        f"({protein_g}P / {carbs_g}C / {fat_g}F) "
        f"TDEE={tdee}, goal={goal} ({adjustment:+d})"
    )
    return result


# --- Biometric persistence ---

def save_biometrics(user_id: int, sex: str = None, age: int = None,
                    height_cm: float = None, weight_kg: float = None,
                    activity_level: str = None, nutrition_goal: str = None,
                    body_fat_pct: float = None) -> dict:
    """Save user biometrics to nutrition_profiles and auto-calculate targets."""
    with get_cursor() as cur:
        # Check if profile exists
        cur.execute("SELECT * FROM nutrition_profiles WHERE user_id = %s", (user_id,))
        existing = cur.fetchone()

        if existing:
            existing = dict(existing)
            # Merge: use new values where provided, keep existing otherwise
            sex = sex or existing.get("sex")
            age = age if age is not None else existing.get("age")
            height_cm = height_cm if height_cm is not None else existing.get("height_cm")
            weight_kg = weight_kg if weight_kg is not None else existing.get("weight_kg")
            activity_level = activity_level or existing.get("activity_level")
            nutrition_goal = nutrition_goal or existing.get("nutrition_goal")
            body_fat_pct = body_fat_pct if body_fat_pct is not None else existing.get("body_fat_pct")

        # Calculate targets if we have enough data
        targets = {}
        if sex and age and height_cm and weight_kg:
            targets = calculate_targets(
                sex=sex, age=age, height_cm=height_cm, weight_kg=weight_kg,
                activity_level=activity_level or "moderately_active",
                goal=nutrition_goal or "maintain",
                body_fat_pct=body_fat_pct,
            )

        if existing:
            cur.execute(
                """UPDATE nutrition_profiles SET
                    sex = COALESCE(%s, sex),
                    age = COALESCE(%s, age),
                    height_cm = COALESCE(%s, height_cm),
                    weight_kg = COALESCE(%s, weight_kg),
                    activity_level = COALESCE(%s, activity_level),
                    nutrition_goal = COALESCE(%s, nutrition_goal),
                    body_fat_pct = COALESCE(%s, body_fat_pct),
                    daily_calorie_target = COALESCE(%s, daily_calorie_target),
                    protein_target_g = COALESCE(%s, protein_target_g),
                    carbs_target_g = COALESCE(%s, carbs_target_g),
                    fat_target_g = COALESCE(%s, fat_target_g),
                    tdee_calculated = COALESCE(%s, tdee_calculated),
                    updated_at = NOW()
                WHERE user_id = %s RETURNING *""",
                (sex, age, height_cm, weight_kg, activity_level, nutrition_goal,
                 body_fat_pct,
                 targets.get("daily_calorie_target"),
                 targets.get("protein_target_g"),
                 targets.get("carbs_target_g"),
                 targets.get("fat_target_g"),
                 targets.get("tdee_calculated"),
                 user_id)
            )
        else:
            cur.execute(
                """INSERT INTO nutrition_profiles
                   (user_id, sex, age, height_cm, weight_kg, activity_level,
                    nutrition_goal, body_fat_pct, daily_calorie_target,
                    protein_target_g, carbs_target_g, fat_target_g, tdee_calculated)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING *""",
                (user_id, sex, age, height_cm, weight_kg, activity_level,
                 nutrition_goal, body_fat_pct,
                 targets.get("daily_calorie_target"),
                 targets.get("protein_target_g"),
                 targets.get("carbs_target_g"),
                 targets.get("fat_target_g"),
                 targets.get("tdee_calculated"))
            )

        result = dict(cur.fetchone())

    logger.info(f"Biometrics saved for user {user_id}: {sex}, {age}y, {height_cm}cm, {weight_kg}kg")
    return result


# --- Weight tracking for adaptive TDEE ---

def log_weight(user_id: int, weight_kg: float, source: str = "manual") -> dict:
    """Log a daily weight entry. One per day (upserts)."""
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO daily_weight_logs (user_id, weight_kg, source)
               VALUES (%s, %s, %s)
               ON CONFLICT (user_id, log_date)
               DO UPDATE SET weight_kg = EXCLUDED.weight_kg,
                            source = EXCLUDED.source,
                            updated_at = NOW()
               RETURNING *""",
            (user_id, weight_kg, source)
        )
        row = dict(cur.fetchone())

    # Also update weight in nutrition_profiles
    with get_cursor() as cur:
        cur.execute(
            "UPDATE nutrition_profiles SET weight_kg = %s WHERE user_id = %s",
            (weight_kg, user_id)
        )

    return row


def get_weight_trend(user_id: int, days: int = 28) -> dict:
    """Get weight trend data for adaptive TDEE calculation.

    Returns exponentially weighted moving average to smooth water fluctuations.
    """
    with get_cursor() as cur:
        cur.execute(
            """SELECT log_date, weight_kg FROM daily_weight_logs
               WHERE user_id = %s AND log_date >= CURRENT_DATE - %s
               ORDER BY log_date ASC""",
            (user_id, days)
        )
        rows = [dict(r) for r in cur.fetchall()]

    if len(rows) < 3:
        return {"entries": len(rows), "insufficient_data": True}

    weights = [r["weight_kg"] for r in rows]
    dates = [r["log_date"] for r in rows]

    # Exponentially weighted moving average (alpha=0.1, same as MacroFactor)
    ewma = [weights[0]]
    alpha = 0.1
    for w in weights[1:]:
        ewma.append(alpha * w + (1 - alpha) * ewma[-1])

    # Weekly rate of change (kg/week)
    if len(ewma) >= 7:
        weekly_change = (ewma[-1] - ewma[-7]) if len(ewma) >= 7 else 0
    else:
        span_days = (dates[-1] - dates[0]).days or 1
        weekly_change = (ewma[-1] - ewma[0]) / span_days * 7

    return {
        "entries": len(rows),
        "current_smoothed": round(ewma[-1], 2),
        "start_smoothed": round(ewma[0], 2),
        "weekly_change_kg": round(weekly_change, 2),
        "first_date": str(dates[0]),
        "last_date": str(dates[-1]),
    }


def calculate_adaptive_tdee(user_id: int) -> dict | None:
    """Phase 2: Back-calculate real TDEE from weight trend + calorie intake.

    Formula: Actual TDEE = avg_calories_in - (weight_change_kg * 7700 / 7)
    (7700 kcal per kg of body weight change)

    Requires >= 14 days of weight + calorie data.
    """
    trend = get_weight_trend(user_id, days=28)
    if trend.get("insufficient_data") or trend["entries"] < 14:
        return None

    # Get average daily calorie intake over the same period
    from bot.services import nutrition_service
    weekly = nutrition_service.get_weekly_intake_summary(user_id)
    if weekly["days_tracked"] < 7:
        return None

    avg_calories = weekly["avg_calories"]
    weekly_change_kg = trend["weekly_change_kg"]

    # Back-calculate: TDEE = calories_in - (change_in_stored_energy / 7)
    # 1 kg body mass ~ 7700 kcal stored energy
    daily_energy_change = (weekly_change_kg * 7700) / 7
    adaptive_tdee = round(avg_calories - daily_energy_change)

    # Sanity check: TDEE should be between 1000-5000
    if not (1000 <= adaptive_tdee <= 5000):
        logger.warning(f"Adaptive TDEE out of range: {adaptive_tdee} for user {user_id}")
        return None

    # Save adaptive TDEE
    with get_cursor() as cur:
        cur.execute(
            """UPDATE nutrition_profiles SET
                tdee_adaptive = %s,
                tdee_last_updated = NOW(),
                updated_at = NOW()
            WHERE user_id = %s""",
            (adaptive_tdee, user_id)
        )

    logger.info(
        f"Adaptive TDEE for user {user_id}: {adaptive_tdee} kcal "
        f"(avg intake={avg_calories}, weight change={weekly_change_kg:+.2f}kg/week)"
    )

    return {
        "adaptive_tdee": adaptive_tdee,
        "avg_daily_calories": avg_calories,
        "weekly_weight_change_kg": weekly_change_kg,
        "data_points": trend["entries"],
        "confidence": "high" if trend["entries"] >= 21 else "moderate",
    }
