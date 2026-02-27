"""User management service — PostgreSQL-backed."""
import logging
import os
from datetime import datetime

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)


def _is_admin_id(telegram_user_id: int) -> bool:
    """Check if this Telegram user ID is the configured admin."""
    admin_ids = os.environ.get("ADMIN_USER_IDS", "")
    if not admin_ids:
        # Fall back to old ALLOWED_USER_IDS for backwards compat
        admin_ids = os.environ.get("ALLOWED_USER_IDS", "")
    return str(telegram_user_id) in [x.strip() for x in admin_ids.split(",") if x.strip()]


def get_or_create_user(telegram_user_id: int, username: str = None, first_name: str = None) -> dict:
    """Get existing user or create a new one. Returns user dict."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM users WHERE telegram_user_id = %s",
            (telegram_user_id,)
        )
        user = cur.fetchone()

        if user:
            # Auto-fix: ensure admin users always have pro tier + is_admin flag
            is_admin = _is_admin_id(telegram_user_id)
            if is_admin and (user.get("tier") != "pro" or not user.get("is_admin")):
                cur.execute(
                    "UPDATE users SET tier = 'pro', is_admin = TRUE, last_active = NOW(), "
                    "telegram_username = COALESCE(%s, telegram_username), "
                    "first_name = COALESCE(%s, first_name) WHERE telegram_user_id = %s",
                    (username, first_name, telegram_user_id)
                )
                user = dict(user)
                user["tier"] = "pro"
                user["is_admin"] = True
                return user

            # Update last_active and profile info if changed
            cur.execute(
                """UPDATE users SET last_active = NOW(),
                   telegram_username = COALESCE(%s, telegram_username),
                   first_name = COALESCE(%s, first_name)
                   WHERE telegram_user_id = %s""",
                (username, first_name, telegram_user_id)
            )
            return dict(user)

        # Create new user — admin gets Pro tier automatically
        is_admin = _is_admin_id(telegram_user_id)
        tier = "pro" if is_admin else "free"

        cur.execute(
            """INSERT INTO users (telegram_user_id, telegram_username, first_name, is_admin, tier)
               VALUES (%s, %s, %s, %s, %s) RETURNING *""",
            (telegram_user_id, username, first_name, is_admin, tier)
        )
        new_user = dict(cur.fetchone())
        logger.info(f"New user created: {telegram_user_id} ({first_name}) admin={is_admin}")
        return new_user


def get_user(telegram_user_id: int) -> dict | None:
    """Get user by Telegram user ID."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM users WHERE telegram_user_id = %s",
            (telegram_user_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    """Get user by internal DB id."""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def update_tier(user_id: int, tier: str, stripe_customer_id: str = None):
    """Update user's subscription tier."""
    with get_cursor() as cur:
        if stripe_customer_id:
            cur.execute(
                "UPDATE users SET tier = %s, stripe_customer_id = %s WHERE id = %s",
                (tier, stripe_customer_id, user_id)
            )
        else:
            cur.execute(
                "UPDATE users SET tier = %s WHERE id = %s",
                (tier, user_id)
            )


def update_settings(user_id: int, timezone: str = None, briefing_hour: int = None,
                     check_in_hour: int = None, assessment_hour: int = None):
    """Update user preferences."""
    with get_cursor() as cur:
        if timezone is not None:
            cur.execute("UPDATE users SET timezone = %s WHERE id = %s", (timezone, user_id))
        if briefing_hour is not None:
            cur.execute("UPDATE users SET briefing_hour = %s WHERE id = %s", (briefing_hour, user_id))
        if check_in_hour is not None:
            cur.execute("UPDATE users SET check_in_hour = %s WHERE id = %s", (check_in_hour, user_id))
        if assessment_hour is not None:
            cur.execute("UPDATE users SET assessment_hour = %s WHERE id = %s", (assessment_hour, user_id))


def get_all_active_users() -> list:
    """Get all users for proactive features (briefings, nudges)."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM users WHERE last_active > NOW() - INTERVAL '30 days' ORDER BY id"
        )
        return [dict(row) for row in cur.fetchall()]


def set_phone_number(user_id: int, phone: str):
    """Store user's verified phone number."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE users SET phone_number = %s WHERE id = %s",
            (phone, user_id),
        )


def phone_number_exists(phone: str, exclude_user_id: int = None) -> bool:
    """Check if a phone number is already linked to another account."""
    with get_cursor() as cur:
        if exclude_user_id:
            cur.execute(
                "SELECT 1 FROM users WHERE phone_number = %s AND id != %s",
                (phone, exclude_user_id),
            )
        else:
            cur.execute(
                "SELECT 1 FROM users WHERE phone_number = %s",
                (phone,),
            )
        return cur.fetchone() is not None


def mark_onboarding_complete(user_id: int):
    """Mark user's onboarding as complete."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE users SET onboarding_completed = TRUE WHERE id = %s",
            (user_id,),
        )


def delete_user(user_id: int):
    """Delete a user and all their data (GDPR compliance)."""
    with get_cursor() as cur:
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        logger.info(f"User {user_id} deleted (GDPR request)")
