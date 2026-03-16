"""Short-term session context — active state tracking per user.

Human brain analogy:
- SHORT-TERM: Active workout, pending food to log, current conversation intent.
  Stored in-memory (dict keyed by user_id). Lost on restart — that's fine,
  these are ephemeral.
- LONG-TERM: user_memory table (persistent facts), conversation_summaries
  (episodic), meal_logs/workouts (structured data).

This module provides the short-term layer that prevents:
1. Bot forgetting an active workout and not logging it
2. Bot re-logging food when user just asks a question about it
3. Bot losing track of what was just discussed
"""
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Per-user session context — ephemeral, in-memory
_sessions: dict[int, dict] = {}

# Max idle time before auto-expiring a context (2 hours)
_MAX_IDLE_SECONDS = 7200

# Max users to track (FIFO eviction)
_MAX_USERS = 200


def get(user_id: int) -> dict:
    """Get or create the session context for a user."""
    _evict_if_needed()
    if user_id not in _sessions:
        _sessions[user_id] = _new_context()
    ctx = _sessions[user_id]
    ctx["last_active"] = time.time()
    return ctx


def clear(user_id: int):
    """Clear a user's session context."""
    _sessions.pop(user_id, None)


def set_field(user_id: int, key: str, value):
    """Set a specific field in the user's session context."""
    ctx = get(user_id)
    ctx[key] = value


def get_field(user_id: int, key: str, default=None):
    """Get a specific field from the user's session context."""
    ctx = get(user_id)
    return ctx.get(key, default)


def clear_field(user_id: int, key: str):
    """Remove a specific field from the user's session context."""
    ctx = get(user_id)
    ctx.pop(key, None)


# ─── Active workout tracking ───

def set_active_workout(user_id: int, session_id: int, title: str,
                       exercises: list = None, started_at: str = None):
    """Mark that the user has an active workout in progress."""
    ctx = get(user_id)
    ctx["active_workout"] = {
        "session_id": session_id,
        "title": title,
        "exercises": exercises or [],
        "started_at": started_at or datetime.now(timezone.utc).isoformat(),
        "sets_completed": 0,
    }


def get_active_workout(user_id: int) -> dict | None:
    """Get the user's active workout, if any."""
    ctx = get(user_id)
    return ctx.get("active_workout")


def clear_active_workout(user_id: int):
    """Clear the active workout (after finishing or abandoning)."""
    ctx = get(user_id)
    ctx.pop("active_workout", None)


# ─── Pending food confirmation ───

def set_pending_food(user_id: int, food_data: dict):
    """Store food data awaiting user confirmation before logging.

    food_data should contain: description, calories, protein_g, carbs_g,
    fat_g, fiber_g, meal_type, source, and any micros.
    """
    ctx = get(user_id)
    ctx["pending_food"] = {
        **food_data,
        "timestamp": time.time(),
    }


def get_pending_food(user_id: int) -> dict | None:
    """Get pending food waiting for log confirmation.

    Returns None if no pending food or if it's older than 10 minutes.
    """
    ctx = get(user_id)
    pending = ctx.get("pending_food")
    if not pending:
        return None
    # Expire after 10 minutes
    if time.time() - pending.get("timestamp", 0) > 600:
        ctx.pop("pending_food", None)
        return None
    return pending


def clear_pending_food(user_id: int):
    """Clear pending food (after logging or user said no)."""
    ctx = get(user_id)
    ctx.pop("pending_food", None)


# ─── Recent conversation intent tracking ───

def set_last_intent(user_id: int, intent: str, details: dict = None):
    """Track what the user's last message was about.

    intent: 'asking_about_food', 'logging_food', 'asking_calories',
            'workout_question', 'logging_workout', 'general', etc.
    """
    ctx = get(user_id)
    ctx["last_intent"] = {
        "intent": intent,
        "details": details or {},
        "timestamp": time.time(),
    }


def get_last_intent(user_id: int) -> dict | None:
    """Get the user's last conversation intent."""
    ctx = get(user_id)
    intent = ctx.get("last_intent")
    if not intent:
        return None
    # Expire after 30 minutes
    if time.time() - intent.get("timestamp", 0) > 1800:
        ctx.pop("last_intent", None)
        return None
    return intent


# ─── Format for system prompt ───

def format_for_prompt(user_id: int) -> str:
    """Format the active session context as a system prompt section."""
    ctx = get(user_id)
    lines = []

    # Active workout
    workout = ctx.get("active_workout")
    if workout:
        session_id = workout.get("session_id")
        title = workout.get("title", "Workout")
        started = workout.get("started_at", "")
        lines.append(f"\nACTIVE WORKOUT SESSION (ID: {session_id}):")
        lines.append(f"- Title: {title}")
        lines.append(f"- Started: {started}")
        exercises = workout.get("exercises", [])
        if exercises:
            ex_names = [e.get("exercise_name", "?") for e in exercises[:6]]
            lines.append(f"- Exercises: {', '.join(ex_names)}")
        lines.append(
            "- The user has a workout IN PROGRESS. If they say 'done', 'finished', "
            "'that's it', or similar — call finish_interactive_session to save it. "
            "If they go silent for a while and come back, remind them they have an "
            "active session and ask if they want to finish it."
        )

    # Pending food
    pending = get_pending_food(user_id)
    if pending:
        desc = pending.get("description", "food")
        cals = pending.get("calories", "?")
        lines.append(f"\nPENDING FOOD (awaiting confirmation):")
        lines.append(f"- {desc}: ~{cals} cal")
        lines.append(
            "- The user was shown this food but hasn't confirmed logging yet. "
            "If they say 'yes', 'log it', 'add it', or similar — log_meal with "
            "the data above. If they say 'no' or ask a different question, "
            "clear the pending food and move on. Do NOT re-log or re-ask."
        )

    if not lines:
        return ""

    return "\n".join(lines) + "\n"


# ─── Internal helpers ───

def _new_context() -> dict:
    """Create a fresh session context."""
    return {
        "last_active": time.time(),
    }


def _evict_if_needed():
    """Evict stale sessions if over capacity."""
    if len(_sessions) <= _MAX_USERS:
        return

    now = time.time()
    # First pass: remove expired
    expired = [uid for uid, ctx in _sessions.items()
               if now - ctx.get("last_active", 0) > _MAX_IDLE_SECONDS]
    for uid in expired:
        _sessions.pop(uid, None)

    # If still over capacity, FIFO evict oldest
    if len(_sessions) > _MAX_USERS:
        sorted_users = sorted(_sessions.items(), key=lambda x: x[1].get("last_active", 0))
        to_remove = len(_sessions) - _MAX_USERS
        for uid, _ in sorted_users[:to_remove]:
            _sessions.pop(uid, None)
