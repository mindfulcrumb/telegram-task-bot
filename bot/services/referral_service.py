"""Referral tracking service — handles deep links, bonus credits, and tier rewards."""
import logging
from bot.db.database import get_cursor

logger = logging.getLogger(__name__)

BONUS_MESSAGES_PER_REFERRAL = 10

TIERS = [
    {"referrals": 3, "reward": "1 free month"},
    {"referrals": 10, "reward": "3 free months"},
    {"referrals": 25, "reward": "Lifetime access"},
]


def track_referral(referrer_telegram_id: int, referred_telegram_id: int) -> dict | None:
    """Record a referral and credit bonus messages. Returns referral dict or None if duplicate."""
    if referrer_telegram_id == referred_telegram_id:
        return None

    with get_cursor() as cur:
        # Check for duplicate
        cur.execute(
            "SELECT id FROM referrals WHERE referred_telegram_id = %s",
            (referred_telegram_id,)
        )
        if cur.fetchone():
            logger.info(f"Duplicate referral: {referred_telegram_id} already referred")
            return None

        # Insert referral
        cur.execute(
            """INSERT INTO referrals (referrer_telegram_id, referred_telegram_id, status, bonus_credited)
               VALUES (%s, %s, 'joined', TRUE) RETURNING *""",
            (referrer_telegram_id, referred_telegram_id)
        )
        referral = dict(cur.fetchone())

        # Credit bonus messages to referrer
        cur.execute(
            """UPDATE users SET
                bonus_messages = COALESCE(bonus_messages, 0) + %s,
                referral_count = COALESCE(referral_count, 0) + 1
               WHERE telegram_user_id = %s""",
            (BONUS_MESSAGES_PER_REFERRAL, referrer_telegram_id)
        )

        # Mark referred user
        cur.execute(
            "UPDATE users SET referred_by_telegram_id = %s WHERE telegram_user_id = %s",
            (referrer_telegram_id, referred_telegram_id)
        )

        logger.info(
            f"Referral tracked: {referrer_telegram_id} -> {referred_telegram_id}, "
            f"+{BONUS_MESSAGES_PER_REFERRAL} bonus messages"
        )
        return referral


def get_referral_stats(telegram_user_id: int) -> dict:
    """Get referral stats for a user."""
    with get_cursor() as cur:
        # Count referrals
        cur.execute(
            "SELECT COUNT(*) as count FROM referrals WHERE referrer_telegram_id = %s",
            (telegram_user_id,)
        )
        count = cur.fetchone()["count"]

        # Get bonus messages
        cur.execute(
            "SELECT COALESCE(bonus_messages, 0) as bonus FROM users WHERE telegram_user_id = %s",
            (telegram_user_id,)
        )
        row = cur.fetchone()
        bonus = row["bonus"] if row else 0

        # Determine tiers
        current_tier = None
        next_tier = None
        for tier in reversed(TIERS):
            if count >= tier["referrals"]:
                current_tier = tier
                break
        for tier in TIERS:
            if count < tier["referrals"]:
                next_tier = tier
                break

        return {
            "total_referrals": count,
            "bonus_messages": bonus,
            "current_tier": current_tier,
            "next_tier": next_tier,
            "referrals_to_next": next_tier["referrals"] - count if next_tier else 0,
            "referral_link": f"https://t.me/Meet_Zoe_Bot?start=ref_{telegram_user_id}",
        }


def get_admin_stats() -> dict:
    """Get overall referral stats for admin dashboard."""
    with get_cursor() as cur:
        cur.execute("SELECT COUNT(*) as total FROM referrals")
        total_referrals = cur.fetchone()["total"]

        cur.execute("SELECT COUNT(*) as total FROM referrals WHERE status = 'subscribed'")
        converted = cur.fetchone()["total"]

        cur.execute(
            """SELECT referrer_telegram_id, COUNT(*) as count
               FROM referrals GROUP BY referrer_telegram_id
               ORDER BY count DESC LIMIT 5"""
        )
        top_referrers = [dict(r) for r in cur.fetchall()]

        return {
            "total_referrals": total_referrals,
            "converted_to_paid": converted,
            "top_referrers": top_referrers,
        }
