"""Free/Pro tier limits and usage tracking."""
import logging
from bot.db.database import get_cursor

logger = logging.getLogger(__name__)

# Tier limits
LIMITS = {
    "free": {
        "max_tasks": 25,
        "max_ai_messages_per_day": 20,
        "daily_briefing": False,
        "check_ins": False,
        "weekly_insights": False,
        "max_reminders": 3,
        "max_workouts_per_day": 2,
        "body_metrics": True,
        "fitness_insights": False,
    },
    "pro": {
        "max_tasks": None,  # unlimited
        "max_ai_messages_per_day": None,
        "daily_briefing": True,
        "check_ins": True,
        "weekly_insights": True,
        "max_reminders": None,
        "max_workouts_per_day": None,
        "body_metrics": True,
        "fitness_insights": True,
    },
}


def track_usage(user_id: int, action: str):
    """Record a usage event."""
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO usage (user_id, action) VALUES (%s, %s)",
            (user_id, action)
        )


def get_usage_today(user_id: int, action: str) -> int:
    """Count how many times a user performed an action today."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT COUNT(*) as cnt FROM usage
               WHERE user_id = %s AND action = %s
               AND created_at >= CURRENT_DATE""",
            (user_id, action)
        )
        return cur.fetchone()["cnt"]


def check_limit(user_id: int, action: str, tier: str = "free", is_admin: bool = False) -> tuple[bool, str | None]:
    """Check if user can perform action. Returns (allowed, message_if_blocked)."""
    # Admin users bypass all limits
    if is_admin or tier == "pro":
        return True, None
    limits = LIMITS.get(tier, LIMITS["free"])

    if action == "add_task":
        from bot.services.task_service import count_active_tasks
        max_tasks = limits["max_tasks"]
        if max_tasks is not None:
            current = count_active_tasks(user_id)
            if current >= max_tasks:
                return False, f"You've hit the free tier limit of {max_tasks} active tasks. Complete some tasks or /upgrade to Pro for unlimited!"
        return True, None

    elif action == "ai_message":
        max_msgs = limits["max_ai_messages_per_day"]
        if max_msgs is not None:
            used = get_usage_today(user_id, "ai_message")
            if used >= max_msgs:
                return False, f"You've used all {max_msgs} AI messages for today. They reset at midnight, or /upgrade to Pro for unlimited!"
        return True, None

    elif action == "set_reminder":
        from bot.services.task_service import count_active_reminders
        max_reminders = limits["max_reminders"]
        if max_reminders is not None:
            current = count_active_reminders(user_id)
            if current >= max_reminders:
                return False, f"Free tier allows {max_reminders} active reminders. /upgrade to Pro for unlimited!"
        return True, None

    elif action == "daily_briefing":
        return limits["daily_briefing"], None

    return True, None
