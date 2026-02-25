"""User memory service — persistent knowledge Zoe learns about each user."""
import logging
from bot.db.database import get_cursor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# User Memory CRUD
# ---------------------------------------------------------------------------

def save_memory(user_id: int, content: str, category: str = "general",
                source: str = "conversation", confidence: float = 1.0) -> dict:
    """Save a memory about the user. Updates if similar content exists."""
    with get_cursor() as cur:
        # Check for duplicate/similar memory (exact match on content)
        cur.execute(
            "SELECT id FROM user_memory WHERE user_id = %s AND content = %s",
            (user_id, content),
        )
        existing = cur.fetchone()
        if existing:
            # Update timestamp and confidence
            cur.execute(
                """UPDATE user_memory SET updated_at = NOW(), confidence = %s,
                   category = %s WHERE id = %s RETURNING id""",
                (confidence, category, existing["id"]),
            )
            return {"id": existing["id"], "action": "updated"}

        # Insert new memory
        cur.execute(
            """INSERT INTO user_memory (user_id, category, content, source, confidence)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (user_id, category, content, source, confidence),
        )
        row = cur.fetchone()
        return {"id": row["id"], "action": "saved"}


def get_memories(user_id: int, category: str = None, limit: int = 50) -> list:
    """Get all memories for a user, optionally filtered by category."""
    with get_cursor() as cur:
        if category:
            cur.execute(
                """SELECT id, category, content, confidence, created_at, updated_at
                   FROM user_memory WHERE user_id = %s AND category = %s
                   ORDER BY updated_at DESC LIMIT %s""",
                (user_id, category, limit),
            )
        else:
            cur.execute(
                """SELECT id, category, content, confidence, created_at, updated_at
                   FROM user_memory WHERE user_id = %s
                   ORDER BY updated_at DESC LIMIT %s""",
                (user_id, limit),
            )
        return [dict(r) for r in cur.fetchall()]


def forget_memory(user_id: int, memory_id: int) -> bool:
    """Delete a specific memory by ID."""
    with get_cursor() as cur:
        cur.execute(
            "DELETE FROM user_memory WHERE id = %s AND user_id = %s RETURNING id",
            (memory_id, user_id),
        )
        return cur.fetchone() is not None


def forget_by_content(user_id: int, content_substring: str) -> int:
    """Delete memories matching a content substring. Returns count deleted."""
    with get_cursor() as cur:
        cur.execute(
            """DELETE FROM user_memory WHERE user_id = %s
               AND LOWER(content) LIKE LOWER(%s) RETURNING id""",
            (user_id, f"%{content_substring}%"),
        )
        return len(cur.fetchall())


def get_memory_count(user_id: int) -> int:
    """Get total memory count for a user."""
    with get_cursor() as cur:
        cur.execute("SELECT COUNT(*) as cnt FROM user_memory WHERE user_id = %s", (user_id,))
        return cur.fetchone()["cnt"]


# ---------------------------------------------------------------------------
# Response Feedback
# ---------------------------------------------------------------------------

def save_feedback(user_id: int, feedback: str, message_text: str = None,
                  context: str = None) -> dict:
    """Save response feedback (thumbs up/down)."""
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO response_feedback (user_id, feedback, message_text, context)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (user_id, feedback, message_text, context),
        )
        row = cur.fetchone()
        return {"id": row["id"]}


def get_feedback_stats(user_id: int, days: int = 30) -> dict:
    """Get feedback summary for a user."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT feedback, COUNT(*) as cnt FROM response_feedback
               WHERE user_id = %s AND created_at > NOW() - INTERVAL '%s days'
               GROUP BY feedback""",
            (user_id, days),
        )
        stats = {"positive": 0, "negative": 0, "total": 0}
        for row in cur.fetchall():
            if row["feedback"] == "positive":
                stats["positive"] = row["cnt"]
            elif row["feedback"] == "negative":
                stats["negative"] = row["cnt"]
        stats["total"] = stats["positive"] + stats["negative"]
        return stats


def get_recent_negative_feedback(user_id: int, limit: int = 5) -> list:
    """Get recent negative feedback for AI to learn from."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT message_text, context, created_at FROM response_feedback
               WHERE user_id = %s AND feedback = 'negative'
               ORDER BY created_at DESC LIMIT %s""",
            (user_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Memory formatting for system prompt
# ---------------------------------------------------------------------------

def format_memories_for_prompt(user_id: int) -> str:
    """Format all user memories into a section for the system prompt."""
    memories = get_memories(user_id, limit=50)
    if not memories:
        return ""

    # Group by category
    by_category = {}
    for m in memories:
        cat = m["category"]
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(m["content"])

    lines = ["\nWHAT YOU KNOW ABOUT THIS USER (from past conversations):"]

    category_labels = {
        "preference": "Preferences",
        "personal": "Personal",
        "fitness": "Fitness",
        "health": "Health",
        "coaching": "Coaching style",
        "goal": "Goals",
        "general": "Notes",
    }

    for cat, items in by_category.items():
        label = category_labels.get(cat, cat.title())
        lines.append(f"- {label}: {'; '.join(items)}")

    lines.append(
        "\nUse this knowledge naturally. Don't announce you \"remembered\" something — "
        "just use it. Reference their goals, preferences, and history as a coach who "
        "actually knows them would."
    )

    return "\n".join(lines) + "\n"
