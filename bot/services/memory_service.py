"""User memory service — persistent knowledge Zoe learns about each user."""
import logging
from bot.db.database import get_cursor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# User Memory CRUD
# ---------------------------------------------------------------------------

def save_memory(user_id: int, content: str, category: str = "general",
                source: str = "conversation", confidence: float = 1.0,
                importance: int = 5) -> dict:
    """Save a memory about the user. Updates if similar content exists."""
    # Content validation
    if not content or not isinstance(content, str):
        return {"action": "skipped", "reason": "empty"}
    content = content.strip()
    if len(content) < 3:
        return {"action": "skipped", "reason": "too short"}
    if len(content) > 500:
        content = content[:500]
    importance = max(1, min(10, importance))

    with get_cursor() as cur:
        # Check for duplicate/similar memory (exact match on content)
        cur.execute(
            "SELECT id FROM user_memory WHERE user_id = %s AND content = %s",
            (user_id, content),
        )
        existing = cur.fetchone()
        if existing:
            # Update timestamp, confidence, and importance
            cur.execute(
                """UPDATE user_memory SET updated_at = NOW(), confidence = %s,
                   category = %s, importance = %s WHERE id = %s RETURNING id""",
                (confidence, category, importance, existing["id"]),
            )
            logger.info(f"Memory updated: user={user_id} cat={category} id={existing['id']}")
            return {"id": existing["id"], "action": "updated"}

        # Insert new memory
        cur.execute(
            """INSERT INTO user_memory (user_id, category, content, source, confidence, importance)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (user_id, category, content, source, confidence, importance),
        )
        row = cur.fetchone()
        logger.info(f"Memory saved: user={user_id} cat={category} imp={importance} content='{content[:60]}'")
        return {"id": row["id"], "action": "saved"}


def update_memory_by_id(user_id: int, memory_id: int, content: str,
                        category: str = None, importance: int = None) -> bool:
    """Update an existing memory's content (for conflict resolution)."""
    with get_cursor() as cur:
        sets = ["content = %s", "updated_at = NOW()"]
        vals = [content]
        if category:
            sets.append("category = %s")
            vals.append(category)
        if importance is not None:
            sets.append("importance = %s")
            vals.append(max(1, min(10, importance)))
        vals.extend([memory_id, user_id])
        cur.execute(
            f"UPDATE user_memory SET {', '.join(sets)} WHERE id = %s AND user_id = %s RETURNING id",
            tuple(vals),
        )
        return cur.fetchone() is not None


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
    # Escape LIKE wildcards in user input to prevent over-matching
    safe = content_substring.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with get_cursor() as cur:
        cur.execute(
            """DELETE FROM user_memory WHERE user_id = %s
               AND LOWER(content) LIKE LOWER(%s) RETURNING id""",
            (user_id, f"%{safe}%"),
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

def format_memories_for_prompt(user_id: int, topics: list = None) -> str:
    """Format user memories into a section for the system prompt.

    Uses topic-filtered + importance-based loading:
    - Always loads importance >= 8 (safety-critical: allergies, injuries, medical)
    - Loads topic-matching memories with importance >= 4
    - Caps at 15 memories per request
    """
    memories = _get_tiered_memories(user_id, topics)
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
        "nutrition": "Nutrition",
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


def _get_tiered_memories(user_id: int, topics: list = None) -> list:
    """Load memories using tiered importance + topic filtering."""
    with get_cursor() as cur:
        # Always load high-importance memories (allergies, injuries, critical health)
        cur.execute(
            """SELECT id, category, content, importance FROM user_memory
               WHERE user_id = %s AND COALESCE(importance, 5) >= 8
               ORDER BY importance DESC, updated_at DESC LIMIT 10""",
            (user_id,),
        )
        critical = [dict(r) for r in cur.fetchall()]
        critical_ids = {m["id"] for m in critical}

        # Topic-matching categories
        topic_categories = _topics_to_categories(topics) if topics else []

        if topic_categories:
            placeholders = ",".join(["%s"] * len(topic_categories))
            cur.execute(
                f"""SELECT id, category, content, importance FROM user_memory
                   WHERE user_id = %s AND category IN ({placeholders})
                   AND COALESCE(importance, 5) >= 4
                   ORDER BY importance DESC, updated_at DESC LIMIT 10""",
                (user_id, *topic_categories),
            )
            topic_mems = [dict(r) for r in cur.fetchall() if r["id"] not in critical_ids]
        else:
            # No topic detected — load top memories by importance
            cur.execute(
                """SELECT id, category, content, importance FROM user_memory
                   WHERE user_id = %s AND COALESCE(importance, 5) >= 4
                   ORDER BY importance DESC, updated_at DESC LIMIT 10""",
                (user_id,),
            )
            topic_mems = [dict(r) for r in cur.fetchall() if r["id"] not in critical_ids]

        # Combine and cap at 15
        return (critical + topic_mems)[:15]


def _topics_to_categories(topics: list) -> list:
    """Map detected conversation topics to memory categories."""
    topic_map = {
        "training": ["fitness", "goal"],
        "nutrition": ["health", "nutrition", "preference"],
        "supplements": ["health"],
        "bloodwork": ["health"],
        "sleep": ["health", "fitness"],
        "mindset": ["coaching", "personal"],
        "general": ["personal", "preference", "goal"],
    }
    cats = set()
    for t in (topics or []):
        cats.update(topic_map.get(t, ["general"]))
    return list(cats) if cats else []


# ---------------------------------------------------------------------------
# Topic Detection (zero-cost keyword matching)
# ---------------------------------------------------------------------------

TOPIC_KEYWORDS = {
    "training": ["workout", "exercise", "squat", "bench", "deadlift", "sets", "reps",
                  "gym", "train", "lift", "muscle", "strength", "cardio", "hiit", "pr"],
    "nutrition": ["eat", "food", "meal", "calories", "protein", "carbs", "fat", "diet",
                   "macro", "recipe", "cook", "fasting", "vegan", "keto"],
    "supplements": ["supplement", "creatine", "vitamin", "omega", "magnesium", "zinc",
                     "ashwagandha", "protein powder", "pre-workout", "stack", "dose"],
    "bloodwork": ["blood", "test", "testosterone", "cortisol", "hba1c", "glucose",
                   "cholesterol", "vitamin d", "iron", "ferritin", "thyroid", "labs", "markers"],
    "sleep": ["sleep", "insomnia", "tired", "fatigue", "nap", "rest", "recovery",
              "melatonin", "circadian", "wake up"],
}


def detect_topics(message: str) -> list:
    """Detect conversation topics from user message. Zero cost — keyword matching only."""
    lower = message.lower()
    scores = {}
    for topic, keywords in TOPIC_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in lower)
        if score > 0:
            scores[topic] = score
    sorted_topics = sorted(scores, key=scores.get, reverse=True)
    return sorted_topics[:2] if sorted_topics else ["general"]


# ---------------------------------------------------------------------------
# Conversation Summaries (episodic memory)
# ---------------------------------------------------------------------------

def save_conversation_summary(user_id: int, summary: str, topics: list = None,
                              key_events: list = None, conversation_date=None) -> dict:
    """Save a conversation summary for episodic memory."""
    from datetime import date as date_type
    if conversation_date is None:
        conversation_date = date_type.today()

    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO conversation_summaries
               (user_id, conversation_date, summary, topics, key_events)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (user_id, conversation_date, summary, topics, key_events),
        )
        row = cur.fetchone()
        logger.info(f"Conversation summary saved: user={user_id} date={conversation_date}")
        return {"id": row["id"]}


def get_recent_summaries(user_id: int, limit: int = 5) -> list:
    """Get recent conversation summaries for episodic context."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT conversation_date, summary, topics, key_events
               FROM conversation_summaries
               WHERE user_id = %s
               ORDER BY conversation_date DESC, created_at DESC
               LIMIT %s""",
            (user_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def format_summaries_for_prompt(user_id: int) -> str:
    """Format recent conversation summaries into a prompt section."""
    summaries = get_recent_summaries(user_id, limit=5)
    if not summaries:
        return ""

    lines = ["\nRECENT CONVERSATIONS (what you've discussed before):"]
    for s in summaries:
        date_str = str(s["conversation_date"])
        lines.append(f"- {date_str}: {s['summary']}")
        if s.get("key_events"):
            events = [e for e in s["key_events"] if e]
            if events:
                lines.append(f"  Notable: {', '.join(events)}")

    lines.append(
        "\nReference past conversations naturally when relevant. "
        "\"Last time you mentioned...\" shows you pay attention."
    )
    return "\n".join(lines) + "\n"
