"""Coaching service — streaks, nudges, check-ins, insights. PostgreSQL-backed."""
import logging
from datetime import date, timedelta

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)


# --- Streaks ---

def get_streak(user_id: int) -> dict:
    """Get or create streak record."""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM streaks WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        if row:
            return dict(row)
        # Create initial record
        cur.execute(
            "INSERT INTO streaks (user_id) VALUES (%s) RETURNING *",
            (user_id,)
        )
        return dict(cur.fetchone())


def update_streak(user_id: int) -> dict:
    """Called when user completes a task. Returns updated streak."""
    today = date.today()
    streak = get_streak(user_id)
    last = streak.get("last_completion_date")

    if last == today:
        # Already completed today — no change
        return streak

    if last == today - timedelta(days=1):
        # Consecutive day — extend streak
        new_streak = streak["current_streak"] + 1
    else:
        # Gap — reset to 1
        new_streak = 1

    longest = max(new_streak, streak.get("longest_streak", 0))

    with get_cursor() as cur:
        cur.execute(
            """UPDATE streaks
               SET current_streak = %s, longest_streak = %s, last_completion_date = %s
               WHERE user_id = %s RETURNING *""",
            (new_streak, longest, today, user_id)
        )
        return dict(cur.fetchone())


# --- Nudge dedup ---

def was_nudged_today(user_id: int, task_id: int, nudge_type: str) -> bool:
    """Check if this nudge was already sent today."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT 1 FROM nudge_log
               WHERE user_id = %s AND task_id = %s AND nudge_type = %s
               AND nudged_at::date = CURRENT_DATE""",
            (user_id, task_id, nudge_type)
        )
        return cur.fetchone() is not None


def record_nudge(user_id: int, task_id: int, nudge_type: str):
    """Record that a nudge was sent."""
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO nudge_log (user_id, task_id, nudge_type) VALUES (%s, %s, %s)",
            (user_id, task_id, nudge_type)
        )


def count_nudges_today(user_id: int) -> int:
    """Count nudges sent to user today (for daily cap)."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT COUNT(*) as cnt FROM nudge_log
               WHERE user_id = %s AND nudged_at::date = CURRENT_DATE""",
            (user_id,)
        )
        return cur.fetchone()["cnt"]


# --- Check-ins ---

def get_pending_check_ins(user_id: int) -> list:
    """Get tasks due today that haven't been checked in on yet."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT t.* FROM tasks t
               WHERE t.user_id = %s AND t.status = 'active' AND t.due_date = CURRENT_DATE
               AND NOT EXISTS (
                   SELECT 1 FROM check_ins c
                   WHERE c.user_id = t.user_id AND c.task_id = t.id
                   AND c.check_in_date = CURRENT_DATE
               )
               ORDER BY t.id""",
            (user_id,)
        )
        return [dict(row) for row in cur.fetchall()]


def create_check_in(user_id: int, task_id: int) -> int:
    """Create a pending check-in record. Returns check_in id."""
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO check_ins (user_id, task_id)
               VALUES (%s, %s)
               ON CONFLICT (user_id, task_id, check_in_date) DO NOTHING
               RETURNING id""",
            (user_id, task_id)
        )
        row = cur.fetchone()
        return row["id"] if row else 0


def mark_check_in_completed(user_id: int, task_id: int):
    """Mark a check-in as completed (user said yes)."""
    with get_cursor() as cur:
        cur.execute(
            """UPDATE check_ins SET completed = TRUE, responded_at = NOW()
               WHERE user_id = %s AND task_id = %s AND check_in_date = CURRENT_DATE""",
            (user_id, task_id)
        )


def get_check_in_stats(user_id: int, days: int = 7) -> dict:
    """Get check-in response stats for the last N days."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT
                 COUNT(*) as total,
                 COUNT(*) FILTER (WHERE completed = TRUE) as completed,
                 COUNT(*) FILTER (WHERE completed = FALSE) as not_completed,
                 COUNT(*) FILTER (WHERE completed IS NULL) as pending
               FROM check_ins
               WHERE user_id = %s AND check_in_date >= CURRENT_DATE - %s""",
            (user_id, days)
        )
        return dict(cur.fetchone())


def get_daily_summary(user_id: int) -> dict:
    """Get a summary of today's activity for end-of-day assessment."""
    with get_cursor() as cur:
        # Tasks completed today
        cur.execute(
            """SELECT COUNT(*) as cnt FROM tasks
               WHERE user_id = %s AND completed_at >= CURRENT_DATE""",
            (user_id,)
        )
        completed_today = cur.fetchone()["cnt"]

        # Tasks that were due today (completed or not)
        cur.execute(
            """SELECT COUNT(*) as total,
                      COUNT(*) FILTER (WHERE status = 'completed') as done
               FROM tasks
               WHERE user_id = %s AND due_date = CURRENT_DATE""",
            (user_id,)
        )
        due_today = dict(cur.fetchone())

        # Tasks still overdue
        cur.execute(
            """SELECT COUNT(*) as cnt FROM tasks
               WHERE user_id = %s AND status = 'active' AND due_date < CURRENT_DATE""",
            (user_id,)
        )
        overdue = cur.fetchone()["cnt"]

        # Check-in response rate today
        cur.execute(
            """SELECT COUNT(*) as total,
                      COUNT(*) FILTER (WHERE completed = TRUE) as done
               FROM check_ins
               WHERE user_id = %s AND check_in_date = CURRENT_DATE""",
            (user_id,)
        )
        check_ins_today = dict(cur.fetchone())

    return {
        "completed_today": completed_today,
        "due_today_total": due_today["total"],
        "due_today_done": due_today["done"],
        "overdue": overdue,
        "check_ins_total": check_ins_today["total"],
        "check_ins_done": check_ins_today["done"],
    }


# --- Weekly insights ---

def get_weekly_stats(user_id: int) -> dict:
    """Compare this week vs last week."""
    with get_cursor() as cur:
        # This week
        cur.execute(
            """SELECT COUNT(*) as cnt FROM tasks
               WHERE user_id = %s AND completed_at >= CURRENT_DATE - INTERVAL '7 days'""",
            (user_id,)
        )
        this_week = cur.fetchone()["cnt"]

        # Last week
        cur.execute(
            """SELECT COUNT(*) as cnt FROM tasks
               WHERE user_id = %s
               AND completed_at >= CURRENT_DATE - INTERVAL '14 days'
               AND completed_at < CURRENT_DATE - INTERVAL '7 days'""",
            (user_id,)
        )
        last_week = cur.fetchone()["cnt"]

        # By category
        cur.execute(
            """SELECT category, COUNT(*) as cnt FROM tasks
               WHERE user_id = %s AND completed_at >= CURRENT_DATE - INTERVAL '7 days'
               GROUP BY category""",
            (user_id,)
        )
        by_category = {row["category"]: row["cnt"] for row in cur.fetchall()}

        # Most productive day of week
        cur.execute(
            """SELECT EXTRACT(DOW FROM completed_at) as dow, COUNT(*) as cnt FROM tasks
               WHERE user_id = %s AND completed_at >= CURRENT_DATE - INTERVAL '30 days'
               GROUP BY dow ORDER BY cnt DESC LIMIT 1""",
            (user_id,)
        )
        row = cur.fetchone()
        day_names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        most_productive_day = day_names[int(row["dow"])] if row else "varies"

        # Current overdue
        cur.execute(
            """SELECT COUNT(*) as cnt FROM tasks
               WHERE user_id = %s AND status = 'active' AND due_date < CURRENT_DATE""",
            (user_id,)
        )
        overdue = cur.fetchone()["cnt"]

    return {
        "completed_this_week": this_week,
        "completed_last_week": last_week,
        "by_category": by_category,
        "most_productive_day": most_productive_day,
        "current_overdue": overdue,
    }


def get_completion_patterns(user_id: int) -> dict:
    """Analyze completion patterns for AI coaching prompts."""
    with get_cursor() as cur:
        # Peak hour
        cur.execute(
            """SELECT EXTRACT(HOUR FROM completed_at) as hour, COUNT(*) as cnt FROM tasks
               WHERE user_id = %s AND completed_at IS NOT NULL
               GROUP BY hour ORDER BY cnt DESC LIMIT 1""",
            (user_id,)
        )
        row = cur.fetchone()
        if row:
            h = int(row["hour"])
            if h < 12:
                preferred_time = "morning"
            elif h < 17:
                preferred_time = "afternoon"
            else:
                preferred_time = "evening"
        else:
            preferred_time = "varies"

        # Weakest category (most overdue)
        cur.execute(
            """SELECT category, COUNT(*) as cnt FROM tasks
               WHERE user_id = %s AND status = 'active' AND due_date < CURRENT_DATE
               GROUP BY category ORDER BY cnt DESC LIMIT 1""",
            (user_id,)
        )
        row = cur.fetchone()
        weakest_category = row["category"] if row else "none"

        # Most productive day
        cur.execute(
            """SELECT EXTRACT(DOW FROM completed_at) as dow, COUNT(*) as cnt FROM tasks
               WHERE user_id = %s AND completed_at IS NOT NULL
               GROUP BY dow ORDER BY cnt DESC LIMIT 1""",
            (user_id,)
        )
        row = cur.fetchone()
        day_names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        most_productive_day = day_names[int(row["dow"])] if row else "varies"

    return {
        "preferred_time": preferred_time,
        "weakest_category": weakest_category,
        "most_productive_day": most_productive_day,
    }
