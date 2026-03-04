"""Nutrition service — meal logging, calorie/macro tracking, nutrition profiles."""
import logging
from datetime import date, datetime, time, timedelta, timezone

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)


def _user_today(user_id: int = None) -> date:
    """Get today's date in the user's timezone (not server UTC).

    Falls back to UTC if user timezone is unknown.
    """
    try:
        if user_id:
            from bot.services.user_service import get_user_by_id
            user = get_user_by_id(user_id)
            if user and user.get("timezone"):
                from zoneinfo import ZoneInfo
                return datetime.now(ZoneInfo(user["timezone"])).date()
    except Exception:
        pass
    return date.today()


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
             photo_analysis: str = None,
             vitamin_d_mcg: float = None, magnesium_mg: float = None,
             zinc_mg: float = None, iron_mg: float = None,
             b12_mcg: float = None, potassium_mg: float = None,
             vitamin_c_mg: float = None, calcium_mg: float = None,
             sodium_mg: float = None) -> dict:
    """Log a meal. If identical meal was already logged today, update it instead (dedup)."""
    with get_cursor() as cur:
        # Dedup check: same description + same meal_type + today = update, not duplicate
        # Use user's timezone, not server UTC
        today = _user_today(user_id)
        day_start = datetime.combine(today, time.min)
        day_end = datetime.combine(today + timedelta(days=1), time.min)

        cur.execute(
            """SELECT id FROM meal_logs
               WHERE user_id = %s
                 AND LOWER(description) = LOWER(%s)
                 AND meal_type = %s
                 AND logged_at >= %s AND logged_at < %s""",
            (user_id, description, meal_type, day_start, day_end)
        )
        existing = cur.fetchone()

        if existing:
            # UPDATE: replace the meal with new values
            cur.execute(
                """UPDATE meal_logs SET
                     calories = %s, protein_g = %s, carbs_g = %s, fat_g = %s, fiber_g = %s,
                     source = %s, vitamin_d_mcg = %s, magnesium_mg = %s, zinc_mg = %s,
                     iron_mg = %s, b12_mcg = %s, potassium_mg = %s, vitamin_c_mg = %s,
                     calcium_mg = %s, sodium_mg = %s, photo_analysis = %s, logged_at = NOW()
                   WHERE id = %s RETURNING *""",
                (calories, protein_g, carbs_g, fat_g, fiber_g,
                 source, vitamin_d_mcg, magnesium_mg, zinc_mg,
                 iron_mg, b12_mcg, potassium_mg, vitamin_c_mg,
                 calcium_mg, sodium_mg, photo_analysis, existing["id"])
            )
        else:
            # INSERT: new meal entry
            cur.execute(
                """INSERT INTO meal_logs
                   (user_id, meal_type, description, calories, protein_g,
                    carbs_g, fat_g, fiber_g, source, photo_analysis,
                    vitamin_d_mcg, magnesium_mg, zinc_mg, iron_mg, b12_mcg,
                    potassium_mg, vitamin_c_mg, calcium_mg, sodium_mg)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                (user_id, meal_type, description, calories, protein_g,
                 carbs_g, fat_g, fiber_g, source, photo_analysis,
                 vitamin_d_mcg, magnesium_mg, zinc_mg, iron_mg, b12_mcg,
                 potassium_mg, vitamin_c_mg, calcium_mg, sodium_mg)
            )
        return dict(cur.fetchone())


def get_meals_today(user_id: int) -> list:
    """Get all meals logged today (user's timezone)."""
    today = _user_today(user_id)
    with get_cursor() as cur:
        cur.execute(
            """SELECT * FROM meal_logs
               WHERE user_id = %s AND logged_at::date = %s
               ORDER BY logged_at ASC""",
            (user_id, today)
        )
        return [dict(row) for row in cur.fetchall()]


def delete_meal(user_id: int, meal_id: int) -> bool:
    """Delete a specific meal log entry by ID (user-scoped for safety)."""
    with get_cursor() as cur:
        cur.execute(
            "DELETE FROM meal_logs WHERE id = %s AND user_id = %s RETURNING id",
            (meal_id, user_id)
        )
        row = cur.fetchone()
        if row:
            logger.info(f"Deleted meal {meal_id} for user {user_id}")
            return True
        return False


def delete_last_meal(user_id: int) -> dict | None:
    """Delete the most recently logged meal for the user today. Returns the deleted meal."""
    today = _user_today(user_id)
    with get_cursor() as cur:
        cur.execute(
            """DELETE FROM meal_logs
               WHERE id = (
                   SELECT id FROM meal_logs
                   WHERE user_id = %s AND logged_at::date = %s
                   ORDER BY logged_at DESC LIMIT 1
               ) RETURNING *""",
            (user_id, today)
        )
        row = cur.fetchone()
        if row:
            result = dict(row)
            logger.info(f"Deleted last meal '{result.get('description')}' for user {user_id}")
            return result
        return None


def clear_today_meals(user_id: int) -> int:
    """Delete ALL meal logs for today. Returns number of meals deleted."""
    today = _user_today(user_id)
    with get_cursor() as cur:
        cur.execute(
            """DELETE FROM meal_logs
               WHERE user_id = %s AND logged_at::date = %s
               RETURNING id""",
            (user_id, today)
        )
        deleted = cur.fetchall()
        count = len(deleted)
        if count:
            logger.info(f"Cleared {count} meals for user {user_id} today")
        return count


def get_daily_intake(user_id: int, target_date: date = None) -> dict:
    """Get total calorie/macro intake for a day + remaining vs target."""
    if target_date is None:
        target_date = _user_today(user_id)

    with get_cursor() as cur:
        cur.execute(
            """SELECT
                 COUNT(*) as meal_count,
                 COALESCE(SUM(calories), 0) as total_calories,
                 COALESCE(SUM(protein_g), 0) as total_protein,
                 COALESCE(SUM(carbs_g), 0) as total_carbs,
                 COALESCE(SUM(fat_g), 0) as total_fat,
                 COALESCE(SUM(fiber_g), 0) as total_fiber,
                 COALESCE(SUM(vitamin_d_mcg), 0) as total_vitamin_d,
                 COALESCE(SUM(magnesium_mg), 0) as total_magnesium,
                 COALESCE(SUM(zinc_mg), 0) as total_zinc,
                 COALESCE(SUM(iron_mg), 0) as total_iron,
                 COALESCE(SUM(b12_mcg), 0) as total_b12,
                 COALESCE(SUM(potassium_mg), 0) as total_potassium,
                 COALESCE(SUM(vitamin_c_mg), 0) as total_vitamin_c,
                 COALESCE(SUM(calcium_mg), 0) as total_calcium,
                 COALESCE(SUM(sodium_mg), 0) as total_sodium
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
        "micros": {
            "vitamin_d_mcg": round(row["total_vitamin_d"], 2),
            "magnesium_mg": round(row["total_magnesium"], 1),
            "zinc_mg": round(row["total_zinc"], 2),
            "iron_mg": round(row["total_iron"], 2),
            "b12_mcg": round(row["total_b12"], 2),
            "potassium_mg": round(row["total_potassium"], 1),
            "vitamin_c_mg": round(row["total_vitamin_c"], 1),
            "calcium_mg": round(row["total_calcium"], 1),
            "sodium_mg": round(row["total_sodium"], 1),
        },
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


def get_micro_trends(user_id: int, days: int = 7) -> dict:
    """Average daily intake of each tracked micronutrient over N days."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT
                 COUNT(DISTINCT logged_at::date) as days_with_data,
                 COALESCE(AVG(daily_vd), 0) as avg_vitamin_d,
                 COALESCE(AVG(daily_mg), 0) as avg_magnesium,
                 COALESCE(AVG(daily_zn), 0) as avg_zinc,
                 COALESCE(AVG(daily_fe), 0) as avg_iron,
                 COALESCE(AVG(daily_b12), 0) as avg_b12,
                 COALESCE(AVG(daily_k), 0) as avg_potassium,
                 COALESCE(AVG(daily_vc), 0) as avg_vitamin_c,
                 COALESCE(AVG(daily_ca), 0) as avg_calcium,
                 COALESCE(AVG(daily_na), 0) as avg_sodium
               FROM (
                 SELECT logged_at::date as day,
                   SUM(vitamin_d_mcg) as daily_vd,
                   SUM(magnesium_mg) as daily_mg,
                   SUM(zinc_mg) as daily_zn,
                   SUM(iron_mg) as daily_fe,
                   SUM(b12_mcg) as daily_b12,
                   SUM(potassium_mg) as daily_k,
                   SUM(vitamin_c_mg) as daily_vc,
                   SUM(calcium_mg) as daily_ca,
                   SUM(sodium_mg) as daily_na
                 FROM meal_logs
                 WHERE user_id = %s AND logged_at >= NOW() - INTERVAL '%s days'
                 GROUP BY day
               ) daily_sums""",
            (user_id, days)
        )
        row = cur.fetchone()
        if not row or row["days_with_data"] == 0:
            return {}
        return {
            "days_with_data": row["days_with_data"],
            "vitamin_d_mcg": round(row["avg_vitamin_d"], 2),
            "magnesium_mg": round(row["avg_magnesium"], 1),
            "zinc_mg": round(row["avg_zinc"], 2),
            "iron_mg": round(row["avg_iron"], 2),
            "b12_mcg": round(row["avg_b12"], 2),
            "potassium_mg": round(row["avg_potassium"], 1),
            "vitamin_c_mg": round(row["avg_vitamin_c"], 1),
            "calcium_mg": round(row["avg_calcium"], 1),
            "sodium_mg": round(row["avg_sodium"], 1),
        }
