"""Workout program service — structured training plan CRUD.

Stores multi-week programs as structured JSON so the AI can:
1. Build a program from user's fitness profile
2. Retrieve today's prescribed session
3. Track week-to-week progression
4. Compare planned vs actual (adherence)

program_json schema:
{
    "weeks": {
        "1": {
            "monday": {
                "title": "Heavy Squat + Bench",
                "type": "strength",
                "exercises": [
                    {
                        "name": "Back Squat",
                        "sets": 5, "reps": "5",
                        "weight_scheme": "ascending",
                        "weights": [60, 75, 85, 90, 92.5],
                        "unit": "kg",
                        "rpe_target": 8,
                        "notes": "Full depth, brace hard on top sets",
                        "superset_with": "L-sit Pull-ups"
                    },
                    ...
                ],
                "warmup": "...",
                "cooldown": "...",
                "finisher": "8-min AMRAP: 5 pull-ups + 10 push-ups + 15 air squats"
            },
            "wednesday": { ... },
            "friday": { ... },
            "tuesday": { "title": "Active Recovery", "type": "recovery", ... },
            "saturday": { "title": "HRV-Guided", "type": "recovery", ... }
        },
        "2": { ... }
    },
    "progression_rules": {
        "upper_increment_kg": 2.5,
        "lower_increment_kg": 5,
        "rpe_ceiling": 9,
        "deload_week": 6
    },
    "coaching_notes": "..."
}
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)

# Map day-of-week (0=Mon..6=Sun) to day names used in program_json
_DOW_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def create_program(user_id: int, title: str, goal: str, duration_weeks: int,
                   days_per_week: int, program_json: dict, notes: str = None) -> dict:
    """Create a new workout program for a user.

    Deactivates any existing active program first (one active program per user).
    """
    with get_cursor() as cur:
        # Deactivate existing active programs
        cur.execute(
            """UPDATE workout_programs SET status = 'replaced', updated_at = NOW()
               WHERE user_id = %s AND status = 'active'""",
            (user_id,)
        )

        cur.execute(
            """INSERT INTO workout_programs
               (user_id, title, goal, duration_weeks, current_week, days_per_week, program_json, notes)
               VALUES (%s, %s, %s, %s, 1, %s, %s, %s)
               RETURNING *""",
            (user_id, title, goal, duration_weeks, days_per_week,
             json.dumps(program_json), notes)
        )
        program = dict(cur.fetchone())

    logger.info(f"Created program '{title}' for user {user_id} ({duration_weeks}w, {days_per_week}d/wk)")
    return program


def get_active_program(user_id: int) -> Optional[dict]:
    """Get the user's active workout program, if any."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT * FROM workout_programs
               WHERE user_id = %s AND status = 'active'
               ORDER BY created_at DESC LIMIT 1""",
            (user_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        program = dict(row)
        # Parse JSONB if it came back as string
        if isinstance(program.get("program_json"), str):
            program["program_json"] = json.loads(program["program_json"])
        return program


def get_todays_session(user_id: int, day_override: str = None) -> Optional[dict]:
    """Get today's prescribed session from the active program.

    Returns the session dict with exercises, or None if rest day / no program.
    day_override: force a specific day name (e.g. "monday") instead of today.
    """
    program = get_active_program(user_id)
    if not program:
        return None

    pj = program["program_json"]
    current_week = program["current_week"]
    week_key = str(current_week)

    weeks = pj.get("weeks", {})
    week_data = weeks.get(week_key)

    # If current week doesn't exist in program, check if it's a repeating pattern
    if not week_data:
        # Fall back to last available week (plateau week)
        available = sorted(weeks.keys(), key=lambda x: int(x))
        if available:
            week_data = weeks[available[-1]]
        else:
            return None

    if day_override:
        day_name = day_override.lower()
    else:
        dow = datetime.now(timezone.utc).weekday()  # 0=Mon
        day_name = _DOW_NAMES[dow]

    session = week_data.get(day_name)
    if not session:
        return None

    return {
        "program_id": program["id"],
        "program_title": program["title"],
        "week": current_week,
        "day": day_name,
        "session": session,
    }


def get_week_overview(user_id: int) -> Optional[dict]:
    """Get the full week's sessions from the active program.

    Returns dict with program info + all sessions for the current week.
    """
    program = get_active_program(user_id)
    if not program:
        return None

    pj = program["program_json"]
    current_week = program["current_week"]
    week_key = str(current_week)

    weeks = pj.get("weeks", {})
    week_data = weeks.get(week_key)

    if not week_data:
        available = sorted(weeks.keys(), key=lambda x: int(x))
        if available:
            week_data = weeks[available[-1]]
        else:
            return None

    return {
        "program_id": program["id"],
        "program_title": program["title"],
        "goal": program["goal"],
        "week": current_week,
        "total_weeks": program["duration_weeks"],
        "days_per_week": program["days_per_week"],
        "sessions": week_data,
        "progression_rules": pj.get("progression_rules", {}),
        "coaching_notes": pj.get("coaching_notes", ""),
    }


def advance_week(user_id: int) -> Optional[dict]:
    """Advance the program to the next week. Returns updated program or None."""
    program = get_active_program(user_id)
    if not program:
        return None

    new_week = program["current_week"] + 1

    # If past duration, mark completed
    if new_week > program["duration_weeks"]:
        with get_cursor() as cur:
            cur.execute(
                """UPDATE workout_programs SET status = 'completed', updated_at = NOW()
                   WHERE id = %s RETURNING *""",
                (program["id"],)
            )
            result = dict(cur.fetchone())
        logger.info(f"Program '{program['title']}' completed for user {user_id}")
        return result

    with get_cursor() as cur:
        cur.execute(
            """UPDATE workout_programs SET current_week = %s, updated_at = NOW()
               WHERE id = %s RETURNING *""",
            (new_week, program["id"])
        )
        result = dict(cur.fetchone())

    logger.info(f"Advanced program '{program['title']}' to week {new_week} for user {user_id}")
    return result


def update_program(program_id: int, updates: dict) -> Optional[dict]:
    """Update specific fields on a program (title, notes, program_json, current_week)."""
    allowed = {"title", "notes", "program_json", "current_week", "goal", "days_per_week"}
    filtered = {k: v for k, v in updates.items() if k in allowed}

    if not filtered:
        return None

    if "program_json" in filtered and isinstance(filtered["program_json"], dict):
        filtered["program_json"] = json.dumps(filtered["program_json"])

    set_clauses = ", ".join(f"{k} = %s" for k in filtered)
    values = list(filtered.values())

    with get_cursor() as cur:
        cur.execute(
            f"""UPDATE workout_programs SET {set_clauses}, updated_at = NOW()
                WHERE id = %s RETURNING *""",
            values + [program_id]
        )
        row = cur.fetchone()
        return dict(row) if row else None


def log_program_session(program_id: int, user_id: int, week_number: int,
                        day_name: str, workout_id: int = None,
                        planned_json: dict = None, status: str = "completed") -> dict:
    """Log a session completion against the program (adherence tracking)."""
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO program_session_logs
               (program_id, user_id, week_number, day_name, workout_id, planned_json, status, completed_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
               RETURNING *""",
            (program_id, user_id, week_number, day_name, workout_id,
             json.dumps(planned_json) if planned_json else None, status)
        )
        return dict(cur.fetchone())


def get_program_adherence(program_id: int) -> dict:
    """Get adherence stats for a program."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT
                 COUNT(*) FILTER (WHERE status = 'completed') as completed,
                 COUNT(*) FILTER (WHERE status = 'skipped') as skipped,
                 COUNT(*) FILTER (WHERE status = 'partial') as partial,
                 COUNT(*) as total
               FROM program_session_logs WHERE program_id = %s""",
            (program_id,)
        )
        row = dict(cur.fetchone())
        total = row["total"] or 1
        row["adherence_pct"] = round(row["completed"] / total * 100, 1)
        return row


def get_program_history(user_id: int, limit: int = 5) -> list:
    """Get past programs for a user."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT id, title, goal, duration_weeks, current_week, status, created_at
               FROM workout_programs WHERE user_id = %s
               ORDER BY created_at DESC LIMIT %s""",
            (user_id, limit)
        )
        return [dict(r) for r in cur.fetchall()]


def deactivate_program(program_id: int) -> bool:
    """Deactivate a program (user wants to stop it)."""
    with get_cursor() as cur:
        cur.execute(
            """UPDATE workout_programs SET status = 'deactivated', updated_at = NOW()
               WHERE id = %s AND status = 'active'""",
            (program_id,)
        )
        return cur.rowcount > 0
