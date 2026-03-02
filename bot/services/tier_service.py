"""Free/Pro tier limits and usage tracking."""
import logging
from datetime import datetime, timezone

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)

# Tier limits
LIMITS = {
    "free": {
        "max_tasks": 25,
        "max_ai_messages_per_day": 15,
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


def track_usage(user_id: int, action: str, telegram_user_id: int | None = None):
    """Record a usage event (both per-user and persistent by telegram_user_id)."""
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO usage (user_id, action) VALUES (%s, %s)",
            (user_id, action)
        )
        # Also track in persistent table (survives account deletion)
        if telegram_user_id:
            try:
                cur.execute(
                    """INSERT INTO usage_persistent (telegram_user_id, action, usage_date, count)
                       VALUES (%s, %s, CURRENT_DATE, 1)
                       ON CONFLICT (telegram_user_id, action, usage_date)
                       DO UPDATE SET count = usage_persistent.count + 1""",
                    (telegram_user_id, action)
                )
            except Exception as e:
                logger.debug(f"Persistent usage tracking failed: {e}")


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


def get_persistent_usage_today(telegram_user_id: int, action: str) -> int:
    """Get persistent usage count for today by telegram_user_id.

    This survives account deletion — prevents delete-and-recreate abuse.
    """
    if not telegram_user_id:
        return 0
    try:
        with get_cursor() as cur:
            cur.execute(
                """SELECT count FROM usage_persistent
                   WHERE telegram_user_id = %s AND action = %s AND usage_date = CURRENT_DATE""",
                (telegram_user_id, action)
            )
            row = cur.fetchone()
            return row["count"] if row else 0
    except Exception:
        return 0


def _check_pro_expiry(user_id: int) -> bool:
    """Check if user has active time-limited Pro access (from referral rewards).

    Returns True if pro_expires_at is set and in the future.
    Clears expired pro_expires_at as a side effect.
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT pro_expires_at FROM users WHERE id = %s",
            (user_id,)
        )
        row = cur.fetchone()
        if not row or not row["pro_expires_at"]:
            return False

        if row["pro_expires_at"] > datetime.now(timezone.utc):
            return True

        # Expired — clear it
        cur.execute(
            "UPDATE users SET pro_expires_at = NULL WHERE id = %s",
            (user_id,)
        )
        logger.info(f"Pro reward expired for user {user_id}")
        return False


def _check_supabase_upgrade(user_id: int, telegram_user_id: int | None) -> bool:
    """Check Supabase for an active subscription and upgrade locally if found.

    Also triggers referral conversion tracking when a referred user subscribes.
    """
    if not telegram_user_id:
        return False
    try:
        from bot.db.supabase_bridge import check_subscription
        if check_subscription(telegram_user_id):
            from bot.services import user_service
            user_service.update_tier(user_id, "pro")
            logger.info(f"User {user_id} upgraded to pro via Supabase subscription")

            # Track referral conversion
            try:
                from bot.services import referral_service
                milestone = referral_service.convert_referral(telegram_user_id)
                if milestone:
                    logger.info(f"Referral milestone triggered: {milestone}")
            except Exception as e:
                logger.debug(f"Referral conversion tracking failed: {e}")

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

    # Check time-limited Pro from referral rewards
    if _check_pro_expiry(user_id):
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
            # Also check persistent usage (survives account deletion)
            if telegram_user_id:
                persistent_used = get_persistent_usage_today(telegram_user_id, "ai_message")
                used = max(used, persistent_used)
            if used >= max_msgs:
                # Check Supabase subscription first
                if _check_supabase_upgrade(user_id, telegram_user_id):
                    return True, None
                # Try bonus messages before blocking
                from bot.services import user_service
                if user_service.deduct_bonus_message(user_id):
                    remaining = user_service.get_bonus_messages(user_id)
                    logger.info(f"User {user_id} used bonus message ({remaining} left)")
                    return True, None
                return False, (
                    f"That's a wrap for today — {max_msgs} messages used. "
                    "Go unlimited with /upgrade, or they reset at midnight.\n\n"
                    "Earn bonus messages by referring friends: /referral"
                )
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

    elif action == "pro_feature":
        if _check_supabase_upgrade(user_id, telegram_user_id):
            return True, None
        if _check_pro_expiry(user_id):
            return True, None
        return False, "That's a Pro feature — unlock it with /upgrade."

    return True, None
