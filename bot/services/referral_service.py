"""Referral tracking service — handles deep links, bonus credits, and tier rewards."""
import logging
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)

BONUS_MESSAGES_PER_REFERRAL = 10
BONUS_MESSAGES_FOR_REFERRED = 5

# Tier rewards based on CONVERTED referrals (people who actually subscribed)
TIERS = [
    {"referrals": 3, "reward": "1 free month", "months": 1},
    {"referrals": 10, "reward": "3 free months", "months": 3},
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


def credit_referred_user(telegram_user_id: int) -> bool:
    """Give bonus messages to the referred user (the friend who joined)."""
    with get_cursor() as cur:
        cur.execute(
            """UPDATE users SET bonus_messages = COALESCE(bonus_messages, 0) + %s
               WHERE telegram_user_id = %s RETURNING id""",
            (BONUS_MESSAGES_FOR_REFERRED, telegram_user_id)
        )
        row = cur.fetchone()
        if row:
            logger.info(f"Credited {BONUS_MESSAGES_FOR_REFERRED} bonus messages to referred user {telegram_user_id}")
            return True
        return False


def convert_referral(referred_telegram_id: int) -> dict | None:
    """Mark a referral as converted (referred user subscribed).

    Returns milestone dict if the referrer just hit a tier, else None.
    """
    with get_cursor() as cur:
        # Update referral status
        cur.execute(
            """UPDATE referrals SET status = 'subscribed'
               WHERE referred_telegram_id = %s AND status = 'joined'
               RETURNING referrer_telegram_id""",
            (referred_telegram_id,)
        )
        row = cur.fetchone()
        if not row:
            return None

        referrer_id = row["referrer_telegram_id"]

        # Count converted referrals for this referrer
        cur.execute(
            """SELECT COUNT(*) as cnt FROM referrals
               WHERE referrer_telegram_id = %s AND status = 'subscribed'""",
            (referrer_id,)
        )
        converted = cur.fetchone()["cnt"]

        # Check if they just hit a milestone
        milestone = None
        for tier in TIERS:
            if converted == tier["referrals"]:
                milestone = tier
                break

        if milestone:
            # Grant Pro reward
            _grant_pro_reward(cur, referrer_id, milestone["months"])

            # Queue milestone notification
            cur.execute(
                """INSERT INTO pending_milestones
                   (referrer_telegram_id, milestone_type, months_granted)
                   VALUES (%s, %s, %s)""",
                (referrer_id, milestone["reward"], milestone["months"])
            )
            logger.info(
                f"Referrer {referrer_id} hit milestone: {milestone['reward']} "
                f"({converted} converted referrals)"
            )
            return {"referrer_telegram_id": referrer_id, "milestone": milestone, "converted": converted}

        logger.info(f"Referral converted: referred={referred_telegram_id}, referrer={referrer_id}, total_converted={converted}")
        return None


def _grant_pro_reward(cur, referrer_telegram_id: int, months: int):
    """Grant time-limited Pro access to a referrer."""
    now = datetime.now(timezone.utc)

    # Get current pro_expires_at — extend if already active
    cur.execute(
        "SELECT pro_expires_at FROM users WHERE telegram_user_id = %s",
        (referrer_telegram_id,)
    )
    row = cur.fetchone()
    if not row:
        return

    current_expiry = row["pro_expires_at"]
    if current_expiry and current_expiry > now:
        # Extend from current expiry
        new_expiry = current_expiry + relativedelta(months=months)
    else:
        # Start from now
        new_expiry = now + relativedelta(months=months)

    cur.execute(
        "UPDATE users SET pro_expires_at = %s WHERE telegram_user_id = %s",
        (new_expiry, referrer_telegram_id)
    )
    logger.info(f"Granted {months} months Pro to referrer {referrer_telegram_id}, expires {new_expiry}")


def get_referral_stats(telegram_user_id: int) -> dict:
    """Get referral stats for a user."""
    with get_cursor() as cur:
        # Count all referrals
        cur.execute(
            "SELECT COUNT(*) as count FROM referrals WHERE referrer_telegram_id = %s",
            (telegram_user_id,)
        )
        total = cur.fetchone()["count"]

        # Count converted referrals
        cur.execute(
            """SELECT COUNT(*) as count FROM referrals
               WHERE referrer_telegram_id = %s AND status = 'subscribed'""",
            (telegram_user_id,)
        )
        converted = cur.fetchone()["count"]

        # Get bonus messages
        cur.execute(
            "SELECT COALESCE(bonus_messages, 0) as bonus FROM users WHERE telegram_user_id = %s",
            (telegram_user_id,)
        )
        row = cur.fetchone()
        bonus = row["bonus"] if row else 0

        # Determine tiers (based on converted referrals)
        current_tier = None
        next_tier = None
        for tier in reversed(TIERS):
            if converted >= tier["referrals"]:
                current_tier = tier
                break
        for tier in TIERS:
            if converted < tier["referrals"]:
                next_tier = tier
                break

        return {
            "total_referrals": total,
            "converted_referrals": converted,
            "bonus_messages": bonus,
            "current_tier": current_tier,
            "next_tier": next_tier,
            "referrals_to_next": next_tier["referrals"] - converted if next_tier else 0,
            "referral_link": f"https://t.me/Meet_Zoe_Bot?start=ref_{telegram_user_id}",
        }


def get_pending_milestones() -> list:
    """Get unsent milestone notifications for the proactive job."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT id, referrer_telegram_id, milestone_type, months_granted
               FROM pending_milestones WHERE sent = FALSE
               ORDER BY created_at"""
        )
        return [dict(r) for r in cur.fetchall()]


def mark_milestone_sent(milestone_id: int):
    """Mark a milestone notification as sent."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE pending_milestones SET sent = TRUE WHERE id = %s",
            (milestone_id,)
        )


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
