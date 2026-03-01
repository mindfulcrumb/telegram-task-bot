"""Conversation history using PostgreSQL — user-scoped."""
import json
import logging
import os

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)

_HISTORY_LIMIT = int(os.getenv("CONVERSATION_HISTORY_LIMIT", "10"))


def get_history(user_id: int, limit: int = None) -> list:
    """Get recent conversation messages in Anthropic API format."""
    limit = limit or _HISTORY_LIMIT
    with get_cursor() as cur:
        cur.execute(
            "SELECT role, content FROM conversations WHERE user_id = %s ORDER BY id DESC LIMIT %s",
            (user_id, limit)
        )
        rows = cur.fetchall()

    messages = []
    for row in reversed(rows):
        try:
            content = json.loads(row["content"])
        except (json.JSONDecodeError, TypeError):
            content = row["content"]
        messages.append({"role": row["role"], "content": content})
    return messages


def save_turn(user_id: int, role: str, content):
    """Save a single conversation turn."""
    serialized = json.dumps(content) if not isinstance(content, str) else content
    try:
        with get_cursor() as cur:
            cur.execute(
                "INSERT INTO conversations (user_id, role, content) VALUES (%s, %s, %s)",
                (user_id, role, serialized)
            )
    except Exception as e:
        logger.warning(f"Failed to save conversation turn for user {user_id}: {e}")


def clear_history(user_id: int):
    """Clear conversation history for a user."""
    with get_cursor() as cur:
        cur.execute("DELETE FROM conversations WHERE user_id = %s", (user_id,))


def get_last_message_time(user_id: int):
    """Get timestamp of user's most recent conversation message."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT created_at FROM conversations WHERE user_id = %s ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        row = cur.fetchone()
        return row["created_at"] if row else None


def prune_old(days: int = 7):
    """Delete conversation history older than N days."""
    with get_cursor() as cur:
        cur.execute(
            "DELETE FROM conversations WHERE created_at < NOW() - make_interval(days => %s)",
            (days,)
        )
        deleted = cur.rowcount
        if deleted > 0:
            logger.info(f"Pruned {deleted} conversation rows older than {days} days")
