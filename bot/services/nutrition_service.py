"""Nutrition service — meal logging, calorie/macro tracking, nutrition profiles."""
import logging
from datetime import date

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)


# --- Nutrition profile ---

def get_nutrition_profile(user_id: int) -> dict | None:
    """Get user's nutrition profile."""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM nutrition_profiles WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def update_nutrition_profile(user_id: int, **kwargs) -> dict:
    """Create or update nutrition profile."""
    profile = get_nutrition_profile(user_id)

    with get_cursor() as cur:
        if profile:
            sets = []
            vals = []
            for key in ("dietary_restrictions", "daily_calorie_target", "protein_target_g",
                        "carbs_target_g", "fat_target_g", "meals_per_day"):
                if key in kwargs and kwargs[key] is not None:
                    sets.append(f"{key} = %s")
                    vals.append(kwargs[key])
            if sets:
                sets.append("updated_at = NOW()")
                vals.append(user_id)
                cur.execute(
                    f"UPDATE nutrition_profiles SET {', '.join(sets)} WHERE user_id = %s RETURNING *",
                    vals
                )
                return dict(cur.fetchone())
            return profile
        else:
            cur.execute(
                """INSERT INTO nutrition_profiles
                   (user_id, dietary_restrictions, daily_calorie_target,
                    protein_target_g, carbs_target_g, fat_target_g, meals_per_day)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                (
                    user_id,
                    kwargs.get("dietary_restrictions"),
                    kwargs.get("daily_calorie_target"),
                    kwargs.get("protein_target_g"),
                    kwargs.get("carbs_target_g"),
                    kwargs.get("fat_target_g"),
                    kwargs.get("meals_per_day", 3),
                )
            )
            return dict(cur.fetchone())


# --- Meal logging ---

def log_meal(user_id: int, meal_type: str = None, description: str = "",
             calories: int = None, protein_g: float = None,
             carbs_g: float = None, fat_g: float = None,
             fiber_g: float = None, source: str = "manual",
             photo_analysis: str = None) -> dict:
    """Log a meal with optional calorie/macro data."""
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO meal_logs
               (user_id, meal_type, description, calories, protein_g,
                carbs_g, fat_g, fiber_g, source, photo_analysis)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (user_id, meal_type, description, calories, protein_g,
             carbs_g, fat_g, fiber_g, source, photo_analysis)
        )
        return dict(cur.fetchone())


def get_meals_today(user_id: int) -> list:
    """Get all meals logged today."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT * FROM meal_logs
               WHERE user_id = %s AND logged_at::date = CURRENT_DATE
               ORDER BY logged_at ASC""",
            (user_id,)
        )
        return [dict(row) for row in cur.fetchall()]


def get_daily_intake(user_id: int, target_date: date = None) -> dict:
    """Get total calorie/macro intake for a day + remaining vs target."""
    if target_date is None:
        target_date = date.today()

    with get_cursor() as cur:
        cur.execute(
            """SELECT
                 COUNT(*) as meal_count,
                 COALESCE(SUM(calories), 0) as total_calories,
                 COALESCE(SUM(protein_g), 0) as total_protein,
                 COALESCE(SUM(carbs_g), 0) as total_carbs,
                 COALESCE(SUM(fat_g), 0) as total_fat,
                 COALESCE(SUM(fiber_g), 0) as total_fiber
               FROM meal_logs
               WHERE user_id = %s AND logged_at::date = %s""",
            (user_id, target_date)
        )
        row = dict(cur.fetchone())

    # Get targets from nutrition profile
    profile = get_nutrition_profile(user_id)
    targets = {}
    remaining = {}
    if profile:
        cal_target = profile.get("daily_calorie_target")
        if cal_target:
            targets["calories"] = cal_target
            remaining["calories"] = cal_target - row["total_calories"]
        pro_target = profile.get("protein_target_g")
        if pro_target:
            targets["protein_g"] = pro_target
            remaining["protein_g"] = round(pro_target - row["total_protein"], 1)
        carb_target = profile.get("carbs_target_g")
        if carb_target:
            targets["carbs_g"] = carb_target
            remaining["carbs_g"] = round(carb_target - row["total_carbs"], 1)
        fat_target = profile.get("fat_target_g")
        if fat_target:
            targets["fat_g"] = fat_target
            remaining["fat_g"] = round(fat_target - row["total_fat"], 1)

    return {
        "date": str(target_date),
        "meal_count": row["meal_count"],
        "total_calories": row["total_calories"],
        "total_protein": round(row["total_protein"], 1),
        "total_carbs": round(row["total_carbs"], 1),
        "total_fat": round(row["total_fat"], 1),
        "total_fiber": round(row["total_fiber"], 1),
        "targets": targets,
        "remaining": remaining,
    }


def get_weekly_intake_summary(user_id: int) -> dict:
    """Get average daily intake over the last 7 days."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT
                 logged_at::date as day,
                 COALESCE(SUM(calories), 0) as cals,
                 COALESCE(SUM(protein_g), 0) as protein,
                 COALESCE(SUM(carbs_g), 0) as carbs,
                 COALESCE(SUM(fat_g), 0) as fat
               FROM meal_logs
               WHERE user_id = %s AND logged_at >= NOW() - INTERVAL '7 days'
               GROUP BY day ORDER BY day DESC""",
            (user_id,)
        )
        days = [dict(row) for row in cur.fetchall()]

    if not days:
        return {"days_tracked": 0, "avg_calories": 0, "avg_protein": 0}

    n = len(days)
    return {
        "days_tracked": n,
        "avg_calories": round(sum(d["cals"] for d in days) / n),
        "avg_protein": round(sum(d["protein"] for d in days) / n, 1),
        "avg_carbs": round(sum(d["carbs"] for d in days) / n, 1),
        "avg_fat": round(sum(d["fat"] for d in days) / n, 1),
        "daily_breakdown": days,
    }
