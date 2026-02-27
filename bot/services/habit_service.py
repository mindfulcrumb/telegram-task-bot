"""Habit tracking — daily habits with streaks."""
import logging
from datetime import date, timedelta

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)


def add_habit(user_id: int, name: str, frequency: str = "daily") -> dict | None:
    """Create a new habit. Returns the habit dict or None if duplicate."""
    with get_cursor() as cur:
        try:
            cur.execute(
                """INSERT INTO habits (user_id, name, frequency)
                   VALUES (%s, %s, %s)
                   RETURNING id, name, frequency""",
                (user_id, name.strip(), frequency),
            )
            row = cur.fetchone()
            if row:
                # Init streak row
                cur.execute(
                    """INSERT INTO habit_streaks (habit_id) VALUES (%s)
                       ON CONFLICT (habit_id) DO NOTHING""",
                    (row["id"],),
                )
                return dict(row)
            return None
        except Exception as e:
            if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                return None
            raise


def remove_habit(user_id: int, name: str) -> bool:
    """Deactivate a habit by name. Returns True if found."""
    with get_cursor() as cur:
        cur.execute(
            """UPDATE habits SET active = FALSE
               WHERE user_id = %s AND LOWER(name) = LOWER(%s) AND active = TRUE""",
            (user_id, name.strip()),
        )
        return cur.rowcount > 0


def log_habit(user_id: int, habit_name: str, log_date: date = None) -> dict | None:
    """Log a habit completion. Returns streak info or None if not found."""
    if log_date is None:
        log_date = date.today()

    with get_cursor() as cur:
        # Find the habit
        cur.execute(
            """SELECT id, name FROM habits
               WHERE user_id = %s AND LOWER(name) = LOWER(%s) AND active = TRUE""",
            (user_id, habit_name.strip()),
        )
        habit = cur.fetchone()
        if not habit:
            return None

        habit_id = habit["id"]

        # Insert log (ignore duplicate for same day)
        try:
            cur.execute(
                """INSERT INTO habit_logs (user_id, habit_id, logged_date)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (habit_id, logged_date) DO NOTHING""",
                (user_id, habit_id, log_date),
            )
        except Exception:
            pass

        # Update streak
        cur.execute(
            "SELECT current_streak, longest_streak, last_completion_date FROM habit_streaks WHERE habit_id = %s",
            (habit_id,),
        )
        streak_row = cur.fetchone()

        if not streak_row:
            cur.execute(
                "INSERT INTO habit_streaks (habit_id, current_streak, longest_streak, last_completion_date) VALUES (%s, 1, 1, %s)",
                (habit_id, log_date),
            )
            return {"habit": habit["name"], "current_streak": 1, "longest_streak": 1}

        current = streak_row["current_streak"] or 0
        longest = streak_row["longest_streak"] or 0
        last_date = streak_row["last_completion_date"]

        if last_date == log_date:
            # Already logged today
            return {"habit": habit["name"], "current_streak": current, "longest_streak": longest, "already_logged": True}
        elif last_date == log_date - timedelta(days=1):
            # Consecutive day — extend streak
            current += 1
        else:
            # Gap — reset to 1
            current = 1

        if current > longest:
            longest = current

        cur.execute(
            """UPDATE habit_streaks
               SET current_streak = %s, longest_streak = %s, last_completion_date = %s
               WHERE habit_id = %s""",
            (current, longest, log_date, habit_id),
        )

        return {"habit": habit["name"], "current_streak": current, "longest_streak": longest}


def get_habits(user_id: int) -> list[dict]:
    """Get all active habits with today's status and streak info."""
    today = date.today()
    with get_cursor() as cur:
        cur.execute(
            """SELECT h.id, h.name, h.frequency,
                      hs.current_streak, hs.longest_streak, hs.last_completion_date,
                      CASE WHEN hl.id IS NOT NULL THEN TRUE ELSE FALSE END AS done_today
               FROM habits h
               LEFT JOIN habit_streaks hs ON hs.habit_id = h.id
               LEFT JOIN habit_logs hl ON hl.habit_id = h.id AND hl.logged_date = %s
               WHERE h.user_id = %s AND h.active = TRUE
               ORDER BY h.created_at""",
            (today, user_id),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]


def get_habit_summary(user_id: int) -> dict:
    """Get habits with streaks and completion rates (7d and 30d)."""
    today = date.today()
    habits = get_habits(user_id)

    with get_cursor() as cur:
        for h in habits:
            # 7-day completion rate
            cur.execute(
                """SELECT COUNT(*) as cnt FROM habit_logs
                   WHERE habit_id = %s AND logged_date >= %s""",
                (h["id"], today - timedelta(days=7)),
            )
            h["completions_7d"] = cur.fetchone()["cnt"]

            # 30-day completion rate
            cur.execute(
                """SELECT COUNT(*) as cnt FROM habit_logs
                   WHERE habit_id = %s AND logged_date >= %s""",
                (h["id"], today - timedelta(days=30)),
            )
            h["completions_30d"] = cur.fetchone()["cnt"]

    return {
        "habits": habits,
        "total": len(habits),
        "done_today": sum(1 for h in habits if h.get("done_today")),
    }


def get_incomplete_habits(user_id: int) -> list[dict]:
    """Get habits not yet completed today (for proactive reminders)."""
    habits = get_habits(user_id)
    return [h for h in habits if not h.get("done_today")]
