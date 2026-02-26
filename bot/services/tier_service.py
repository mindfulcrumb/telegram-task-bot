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


def _check_supabase_upgrade(user_id: int, telegram_user_id: int | None) -> bool:
    """Check Supabase for an active subscription and upgrade locally if found."""
    if not telegram_user_id:
        return False
    try:
        from bot.db.supabase_bridge import check_subscription
        if check_subscription(telegram_user_id):
            from bot.services import user_service
            user_service.update_tier(user_id, "pro")
            logger.info(f"User {user_id} upgraded to pro via Supabase subscription")
            return True
    except Exception as e:
        logger.debug(f"Supabase subscription check skipped: {e}")
    return False


def check_limit(
    user_id: int,
    action: str,
    tier: str = "free",
    is_admin: bool = False,
    telegram_user_id: int | None = None,
) -> tuple[bool, str | None]:
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
                if _check_supabase_upgrade(user_id, telegram_user_id):
                    return True, None
                return False, f"You're running a tight ship — {max_tasks} active tasks. Unlock unlimited with /upgrade."
        return True, None

    elif action == "ai_message":
        max_msgs = limits["max_ai_messages_per_day"]
        if max_msgs is not None:
            used = get_usage_today(user_id, "ai_message")
            if used >= max_msgs:
                if _check_supabase_upgrade(user_id, telegram_user_id):
                    return True, None
                return False, f"That's a wrap for today — {max_msgs} messages used. Go unlimited with /upgrade, or they reset at midnight."
        return True, None

    elif action == "set_reminder":
        from bot.services.task_service import count_active_reminders
        max_reminders = limits["max_reminders"]
        if max_reminders is not None:
            current = count_active_reminders(user_id)
            if current >= max_reminders:
                if _check_supabase_upgrade(user_id, telegram_user_id):
                    return True, None
                return False, f"{max_reminders} reminders maxed out. Unlock unlimited with /upgrade."
        return True, None

    elif action == "daily_briefing":
        return limits["daily_briefing"], None

    return True, None
