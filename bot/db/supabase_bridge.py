"""Bridge to Supabase PostgreSQL for subscription checks."""
import os
import logging

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

_conn = None


def _get_conn():
    """Get or create a Supabase PostgreSQL connection."""
    global _conn
    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        return None
    if _conn is None or _conn.closed:
        try:
            _conn = psycopg2.connect(url)
            _conn.autocommit = True
            logger.info("Connected to Supabase PostgreSQL")
        except Exception as e:
            logger.error(f"Failed to connect to Supabase: {e}")
            return None
    return _conn


def check_subscription(telegram_user_id: int) -> bool:
    """Check if a telegram user has an active subscription in Supabase."""
    conn = _get_conn()
    if not conn:
        return False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT status FROM subscriptions WHERE telegram_user_id = %s",
                (telegram_user_id,)
            )
            row = cur.fetchone()
            return row is not None and row["status"] == "active"
    except Exception as e:
        logger.error(f"Supabase subscription check failed: {e}")
        return False


def close():
    """Close the Supabase connection."""
    global _conn
    if _conn and not _conn.closed:
        _conn.close()
        _conn = None
        logger.info("Supabase connection closed")
