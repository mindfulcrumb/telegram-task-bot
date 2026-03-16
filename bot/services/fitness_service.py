"""Fitness service — workout tracking, body metrics, pattern analysis, PR detection."""
import logging
from datetime import date, timedelta

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)


# --- Movement pattern mapping ---

PATTERN_MAP = {
    # Squat (knee-dominant)
    "squat": "squat", "back squat": "squat", "front squat": "squat",
    "goblet squat": "squat", "split squat": "squat", "bulgarian split squat": "squat",
    "leg press": "squat", "lunge": "squat", "lunges": "squat",
    "walking lunge": "squat", "hack squat": "squat", "sissy squat": "squat",
    "leg extension": "squat", "step up": "squat", "step ups": "squat",
    # Hinge (hip-dominant)
    "deadlift": "hinge", "rdl": "hinge", "romanian deadlift": "hinge",
    "hip thrust": "hinge", "kettlebell swing": "hinge", "kb swing": "hinge",
    "good morning": "hinge", "glute bridge": "hinge", "sumo deadlift": "hinge",
    "trap bar deadlift": "hinge", "leg curl": "hinge", "hamstring curl": "hinge",
    "nordic curl": "hinge", "back extension": "hinge", "hyperextension": "hinge",
    # Horizontal push
    "bench press": "horizontal_push", "bench": "horizontal_push",
    "dumbbell press": "horizontal_push", "db press": "horizontal_push",
    "push up": "horizontal_push", "push-up": "horizontal_push", "pushup": "horizontal_push",
    "push ups": "horizontal_push", "chest press": "horizontal_push",
    "incline bench": "horizontal_push", "incline press": "horizontal_push",
    "decline bench": "horizontal_push", "floor press": "horizontal_push",
    "dips": "horizontal_push", "dip": "horizontal_push",
    "chest fly": "horizontal_push", "cable fly": "horizontal_push", "pec fly": "horizontal_push",
    # Horizontal pull
    "barbell row": "horizontal_pull", "bent over row": "horizontal_pull",
    "cable row": "horizontal_pull", "seated row": "horizontal_pull",
    "dumbbell row": "horizontal_pull", "db row": "horizontal_pull",
    "t-bar row": "horizontal_pull", "pendlay row": "horizontal_pull",
    "chest supported row": "horizontal_pull", "machine row": "horizontal_pull",
    "face pull": "horizontal_pull", "face pulls": "horizontal_pull",
    "rear delt fly": "horizontal_pull",
    # Vertical push
    "overhead press": "vertical_push", "ohp": "vertical_push",
    "shoulder press": "vertical_push", "military press": "vertical_push",
    "landmine press": "vertical_push", "pike push up": "vertical_push",
    "pike push-up": "vertical_push", "arnold press": "vertical_push",
    "lateral raise": "vertical_push", "lateral raises": "vertical_push",
    "front raise": "vertical_push", "upright row": "vertical_push",
    # Vertical pull
    "pull up": "vertical_pull", "pull-up": "vertical_pull", "pullup": "vertical_pull",
    "pull ups": "vertical_pull", "chin up": "vertical_pull", "chin-up": "vertical_pull",
    "chinup": "vertical_pull", "chin ups": "vertical_pull",
    "lat pulldown": "vertical_pull", "pulldown": "vertical_pull",
    # Carry / rotation / core
    "farmer walk": "carry_rotation", "farmer's walk": "carry_rotation",
    "farmers walk": "carry_rotation", "suitcase carry": "carry_rotation",
    "pallof press": "carry_rotation", "woodchop": "carry_rotation",
    "cable woodchop": "carry_rotation", "plank": "carry_rotation",
    "dead bug": "carry_rotation", "bird dog": "carry_rotation",
    "ab wheel": "carry_rotation", "ab rollout": "carry_rotation",
    "hanging leg raise": "carry_rotation", "russian twist": "carry_rotation",
    "medicine ball throw": "carry_rotation", "med ball slam": "carry_rotation",
    "rotational throw": "carry_rotation", "anti-rotation": "carry_rotation",
}


def infer_movement_pattern(exercise_name: str) -> str | None:
    """Infer movement pattern from exercise name."""
    name = exercise_name.lower().strip()
    # Direct match
    if name in PATTERN_MAP:
        return PATTERN_MAP[name]
    # Partial match
    for key, pattern in PATTERN_MAP.items():
        if key in name:
            return pattern
    return None


# --- Workout CRUD ---

def log_workout(user_id: int, title: str, duration_minutes: int = None,
                rpe: float = None, notes: str = None,
                exercises: list = None) -> dict:
    """Log a workout session with optional exercises."""
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO workouts (user_id, title, duration_minutes, rpe, notes)
               VALUES (%s, %s, %s, %s, %s) RETURNING *""",
            (user_id, title, duration_minutes, rpe, notes)
        )
        workout = dict(cur.fetchone())

        # Insert exercises
        if exercises:
            for i, ex in enumerate(exercises):
                pattern = ex.get("movement_pattern") or infer_movement_pattern(ex.get("exercise_name", ""))
                cur.execute(
                    """INSERT INTO workout_exercises
                       (workout_id, exercise_name, movement_pattern, sets, reps, weight, weight_unit, rpe, notes, sort_order)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        workout["id"],
                        ex.get("exercise_name", ""),
                        pattern,
                        ex.get("sets"),
                        str(ex.get("reps", "")) if ex.get("reps") else None,
                        ex.get("weight"),
                        ex.get("weight_unit", "kg"),
                        ex.get("rpe"),
                        ex.get("notes"),
                        i,
                    )
                )

    # Update workout streak
    update_workout_streak(user_id)

    # Detect PRs from this workout
    prs = detect_prs_for_workout(user_id, workout["id"]) if exercises else []
    workout["prs"] = prs

    return workout


def get_recent_workouts(user_id: int, days: int = 14) -> list:
    """Get recent workouts with their exercises."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT * FROM workouts
               WHERE user_id = %s AND created_at >= NOW() - INTERVAL '%s days'
               ORDER BY created_at DESC""",
            (user_id, days)
        )
        workouts = [dict(row) for row in cur.fetchall()]

        for w in workouts:
            cur.execute(
                """SELECT * FROM workout_exercises
                   WHERE workout_id = %s ORDER BY sort_order""",
                (w["id"],)
            )
            w["exercises"] = [dict(row) for row in cur.fetchall()]

    return workouts


def get_movement_pattern_balance(user_id: int, days: int = 14) -> dict:
    """Count exercises by movement pattern over the last N days."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT we.movement_pattern, COUNT(*) as cnt
               FROM workout_exercises we
               JOIN workouts w ON w.id = we.workout_id
               WHERE w.user_id = %s AND w.created_at >= NOW() - INTERVAL '%s days'
               AND we.movement_pattern IS NOT NULL
               GROUP BY we.movement_pattern""",
            (user_id, days)
        )
        return {row["movement_pattern"]: row["cnt"] for row in cur.fetchall()}


def get_exercise_history(user_id: int, exercise_name: str, limit: int = 10) -> list:
    """Get history of a specific exercise for progressive overload tracking."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT we.*, w.created_at as workout_date
               FROM workout_exercises we
               JOIN workouts w ON w.id = we.workout_id
               WHERE w.user_id = %s AND LOWER(we.exercise_name) = LOWER(%s)
               ORDER BY w.created_at DESC LIMIT %s""",
            (user_id, exercise_name, limit)
        )
        return [dict(row) for row in cur.fetchall()]


def get_volume_trend(user_id: int, weeks: int = 4) -> dict:
    """Get total sets per week for trend analysis."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT
                 DATE_TRUNC('week', w.created_at) as week,
                 COALESCE(SUM(we.sets), 0) as total_sets,
                 COUNT(DISTINCT w.id) as workout_count
               FROM workouts w
               LEFT JOIN workout_exercises we ON we.workout_id = w.id
               WHERE w.user_id = %s AND w.created_at >= NOW() - INTERVAL '%s weeks'
               GROUP BY week ORDER BY week DESC""",
            (user_id, weeks)
        )
        rows = [dict(row) for row in cur.fetchall()]

    if len(rows) >= 2:
        this_week = rows[0]["total_sets"]
        last_week = rows[1]["total_sets"]
        if this_week > last_week:
            trend = "up"
        elif this_week < last_week:
            trend = "down"
        else:
            trend = "stable"
    else:
        trend = "insufficient_data"

    return {
        "weeks": rows,
        "trend": trend,
        "this_week_sets": rows[0]["total_sets"] if rows else 0,
        "last_week_sets": rows[1]["total_sets"] if len(rows) >= 2 else 0,
    }


# --- Body metrics ---

def log_metric(user_id: int, metric_type: str, value: float, unit: str = None) -> dict:
    """Log a body metric (weight, body_fat, 1RMs, measurements)."""
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO body_metrics (user_id, metric_type, value, unit)
               VALUES (%s, %s, %s, %s) RETURNING *""",
            (user_id, metric_type, value, unit)
        )
        new = dict(cur.fetchone())

        # Get previous reading for comparison
        cur.execute(
            """SELECT value, recorded_at FROM body_metrics
               WHERE user_id = %s AND metric_type = %s AND id != %s
               ORDER BY recorded_at DESC LIMIT 1""",
            (user_id, metric_type, new["id"])
        )
        prev = cur.fetchone()
        if prev:
            new["previous_value"] = prev["value"]
            new["previous_date"] = prev["recorded_at"]
            new["change"] = round(value - prev["value"], 2)
        else:
            new["previous_value"] = None
            new["change"] = None

    return new


def get_metrics(user_id: int, metric_type: str, days: int = 60) -> list:
    """Get trend data for a specific metric."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT * FROM body_metrics
               WHERE user_id = %s AND metric_type = %s
               AND recorded_at >= NOW() - INTERVAL '%s days'
               ORDER BY recorded_at DESC""",
            (user_id, metric_type, days)
        )
        return [dict(row) for row in cur.fetchall()]


def get_latest_metrics(user_id: int) -> dict:
    """Get most recent value of each metric type."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT DISTINCT ON (metric_type) metric_type, value, unit, recorded_at
               FROM body_metrics WHERE user_id = %s
               ORDER BY metric_type, recorded_at DESC""",
            (user_id,)
        )
        return {row["metric_type"]: dict(row) for row in cur.fetchall()}


# --- PR detection ---

def detect_prs_for_workout(user_id: int, workout_id: int) -> list:
    """Check if any exercises in this workout are personal records."""
    prs = []
    with get_cursor() as cur:
        # Get exercises from this workout that have weight
        cur.execute(
            """SELECT * FROM workout_exercises
               WHERE workout_id = %s AND weight IS NOT NULL AND weight > 0""",
            (workout_id,)
        )
        exercises = cur.fetchall()

        for ex in exercises:
            # Check if this weight is the highest ever for this exercise
            cur.execute(
                """SELECT MAX(we.weight) as max_weight
                   FROM workout_exercises we
                   JOIN workouts w ON w.id = we.workout_id
                   WHERE w.user_id = %s AND LOWER(we.exercise_name) = LOWER(%s)
                   AND we.id != %s AND we.weight IS NOT NULL""",
                (user_id, ex["exercise_name"], ex["id"])
            )
            row = cur.fetchone()
            prev_max = row["max_weight"] if row and row["max_weight"] else 0
            if ex["weight"] > prev_max and prev_max > 0:
                prs.append({
                    "exercise": ex["exercise_name"],
                    "new_weight": ex["weight"],
                    "previous_best": prev_max,
                    "increase": round(ex["weight"] - prev_max, 1),
                })

    return prs


def detect_prs(user_id: int) -> list:
    """Get recent PRs (last 30 days)."""
    prs = []
    with get_cursor() as cur:
        # Get unique exercises with weight in last 30 days
        cur.execute(
            """SELECT DISTINCT LOWER(we.exercise_name) as exercise_name
               FROM workout_exercises we
               JOIN workouts w ON w.id = we.workout_id
               WHERE w.user_id = %s AND w.created_at >= NOW() - INTERVAL '30 days'
               AND we.weight IS NOT NULL AND we.weight > 0""",
            (user_id,)
        )
        exercises = [row["exercise_name"] for row in cur.fetchall()]

        for exercise in exercises:
            # Max in last 30 days
            cur.execute(
                """SELECT MAX(we.weight) as recent_max
                   FROM workout_exercises we
                   JOIN workouts w ON w.id = we.workout_id
                   WHERE w.user_id = %s AND LOWER(we.exercise_name) = LOWER(%s)
                   AND w.created_at >= NOW() - INTERVAL '30 days'
                   AND we.weight IS NOT NULL""",
                (user_id, exercise)
            )
            recent_max = cur.fetchone()["recent_max"]

            # Max before that
            cur.execute(
                """SELECT MAX(we.weight) as old_max
                   FROM workout_exercises we
                   JOIN workouts w ON w.id = we.workout_id
                   WHERE w.user_id = %s AND LOWER(we.exercise_name) = LOWER(%s)
                   AND w.created_at < NOW() - INTERVAL '30 days'
                   AND we.weight IS NOT NULL""",
                (user_id, exercise)
            )
            row = cur.fetchone()
            old_max = row["old_max"] if row and row["old_max"] else 0

            if recent_max and recent_max > old_max and old_max > 0:
                prs.append({
                    "exercise": exercise,
                    "new_weight": recent_max,
                    "previous_best": old_max,
                })

    return prs


# --- Workout streak ---

def get_workout_streak(user_id: int) -> dict:
    """Get or create workout streak record."""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM workout_streaks WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        if row:
            return dict(row)
        cur.execute(
            "INSERT INTO workout_streaks (user_id) VALUES (%s) RETURNING *",
            (user_id,)
        )
        return dict(cur.fetchone())


def update_workout_streak(user_id: int) -> dict:
    """Called after logging a workout. Returns updated streak."""
    today = date.today()
    streak = get_workout_streak(user_id)
    last = streak.get("last_workout_date")

    if last == today:
        return streak

    if last and (today - last).days <= 2:
        # Within 2 days counts as consecutive (rest days are normal)
        new_streak = streak["current_streak"] + 1
    else:
        new_streak = 1

    longest = max(new_streak, streak.get("longest_streak", 0))

    with get_cursor() as cur:
        cur.execute(
            """UPDATE workout_streaks
               SET current_streak = %s, longest_streak = %s, last_workout_date = %s
               WHERE user_id = %s RETURNING *""",
            (new_streak, longest, today, user_id)
        )
        return dict(cur.fetchone())


# --- Fitness profile ---

def get_fitness_profile(user_id: int) -> dict | None:
    """Get user's fitness profile."""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM fitness_profiles WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def update_fitness_profile(user_id: int, **kwargs) -> dict:
    """Create or update fitness profile."""
    profile = get_fitness_profile(user_id)

    with get_cursor() as cur:
        if profile:
            sets = []
            vals = []
            for key in ("fitness_goal", "experience_level", "training_days_per_week", "limitations", "preferred_style", "equipment"):
                if key in kwargs and kwargs[key] is not None:
                    sets.append(f"{key} = %s")
                    vals.append(kwargs[key])
            if sets:
                sets.append("updated_at = NOW()")
                vals.append(user_id)
                cur.execute(
                    f"UPDATE fitness_profiles SET {', '.join(sets)} WHERE user_id = %s RETURNING *",
                    vals
                )
                return dict(cur.fetchone())
            return profile
        else:
            cur.execute(
                """INSERT INTO fitness_profiles (user_id, fitness_goal, experience_level,
                   training_days_per_week, limitations, preferred_style, equipment)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                (
                    user_id,
                    kwargs.get("fitness_goal"),
                    kwargs.get("experience_level", "intermediate"),
                    kwargs.get("training_days_per_week", 3),
                    kwargs.get("limitations"),
                    kwargs.get("preferred_style"),
                    kwargs.get("equipment"),
                )
            )
            return dict(cur.fetchone())


# --- Full fitness summary (for AI context) ---

def get_fitness_summary(user_id: int) -> dict:
    """Everything the AI brain needs in one call."""
    profile = get_fitness_profile(user_id)
    streak = get_workout_streak(user_id)
    recent = get_recent_workouts(user_id, days=14)
    patterns = get_movement_pattern_balance(user_id, days=14)
    volume = get_volume_trend(user_id, weeks=4)
    metrics = get_latest_metrics(user_id)
    prs = detect_prs(user_id)

    # Count weeks of training (for deload suggestion)
    with get_cursor() as cur:
        cur.execute(
            """SELECT COUNT(DISTINCT DATE_TRUNC('week', created_at)) as weeks
               FROM workouts WHERE user_id = %s
               AND created_at >= NOW() - INTERVAL '8 weeks'""",
            (user_id,)
        )
        row = cur.fetchone()
        active_training_weeks = row["weeks"] if row else 0

    return {
        "profile": profile,
        "streak": streak,
        "recent_workouts": recent[:3],  # last 3 for prompt
        "all_recent": recent,
        "pattern_balance": patterns,
        "volume_trend": volume,
        "latest_metrics": metrics,
        "recent_prs": prs,
        "active_training_weeks": active_training_weeks,
    }


# --- Interactive Workout Sessions ---

def create_workout_session(user_id: int, title: str, exercises: list, chat_id: int = 0) -> dict:
    """Create an active workout session with exercises. Auto-abandons any existing active session."""
    with get_cursor() as cur:
        # Abandon any existing active session for this user
        cur.execute(
            "UPDATE workout_sessions SET status = 'abandoned', completed_at = NOW() WHERE user_id = %s AND status = 'active'",
            (user_id,)
        )

        cur.execute(
            """INSERT INTO workout_sessions (user_id, chat_id, title, total_exercises)
               VALUES (%s, %s, %s, %s) RETURNING *""",
            (user_id, chat_id, title, len(exercises))
        )
        session = dict(cur.fetchone())

        session_exercises = []
        for i, ex in enumerate(exercises):
            pattern = infer_movement_pattern(ex.get("exercise_name", ""))
            cur.execute(
                """INSERT INTO session_exercises
                   (session_id, exercise_name, movement_pattern, target_sets, target_reps,
                    target_weight, weight_unit, target_rpe, sort_order, notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                (
                    session["id"],
                    ex.get("exercise_name", ""),
                    pattern,
                    ex.get("sets", 4),
                    str(ex.get("reps", "8")),
                    ex.get("weight"),
                    ex.get("weight_unit", "kg"),
                    ex.get("rpe"),
                    i,
                    ex.get("notes"),
                )
            )
            session_exercises.append(dict(cur.fetchone()))

        session["exercises"] = session_exercises
    return session


def get_active_session(user_id: int) -> dict | None:
    """Get the user's current active workout session with exercises."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT * FROM workout_sessions
               WHERE user_id = %s AND status = 'active'
               ORDER BY started_at DESC LIMIT 1""",
            (user_id,)
        )
        session = cur.fetchone()
        if not session:
            return None
        session = dict(session)

        cur.execute(
            "SELECT * FROM session_exercises WHERE session_id = %s ORDER BY sort_order",
            (session["id"],)
        )
        session["exercises"] = [dict(row) for row in cur.fetchall()]
    return session


def get_session_by_id(session_id: int) -> dict | None:
    """Get a workout session by ID with exercises."""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM workout_sessions WHERE id = %s", (session_id,))
        session = cur.fetchone()
        if not session:
            return None
        session = dict(session)

        cur.execute(
            "SELECT * FROM session_exercises WHERE session_id = %s ORDER BY sort_order",
            (session["id"],)
        )
        session["exercises"] = [dict(row) for row in cur.fetchall()]
    return session


def get_session_exercise(exercise_id: int) -> dict | None:
    """Get a single session exercise by ID."""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM session_exercises WHERE id = %s", (exercise_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def update_session_exercise_message_id(exercise_id: int, message_id: int):
    """Store the Telegram message_id on a session exercise for later editing."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE session_exercises SET message_id = %s WHERE id = %s",
            (message_id, exercise_id)
        )


def update_session_chat_id(session_id: int, chat_id: int):
    """Set the chat_id on a session (needed for timer callbacks)."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE workout_sessions SET chat_id = %s WHERE id = %s",
            (chat_id, session_id)
        )


def complete_set(exercise_id: int) -> dict:
    """Increment sets_completed for an exercise. Returns updated exercise."""
    with get_cursor() as cur:
        cur.execute(
            """UPDATE session_exercises
               SET sets_completed = LEAST(sets_completed + 1, target_sets)
               WHERE id = %s RETURNING *""",
            (exercise_id,)
        )
        ex = dict(cur.fetchone())

        # Update session completed_exercises count if this exercise just finished
        if ex["sets_completed"] >= ex["target_sets"]:
            cur.execute(
                """UPDATE workout_sessions
                   SET completed_exercises = (
                       SELECT COUNT(*) FROM session_exercises
                       WHERE session_id = %s AND sets_completed >= target_sets
                   )
                   WHERE id = %s""",
                (ex["session_id"], ex["session_id"])
            )
    return ex


def undo_set(exercise_id: int) -> dict:
    """Decrement sets_completed. Returns updated exercise."""
    with get_cursor() as cur:
        cur.execute(
            """UPDATE session_exercises
               SET sets_completed = GREATEST(0, sets_completed - 1)
               WHERE id = %s RETURNING *""",
            (exercise_id,)
        )
        ex = dict(cur.fetchone())

        # Recount completed exercises on the session
        cur.execute(
            """UPDATE workout_sessions
               SET completed_exercises = (
                   SELECT COUNT(*) FROM session_exercises
                   WHERE session_id = %s AND sets_completed >= target_sets
               )
               WHERE id = %s""",
            (ex["session_id"], ex["session_id"])
        )
    return ex


def finish_session(session_id: int) -> dict:
    """Complete a session and log it as a real workout.

    Idempotent: if session is already completed, returns the existing workout.
    """
    from datetime import datetime, timezone

    with get_cursor() as cur:
        # Atomically claim the session — prevents duplicates on double-tap
        cur.execute(
            """UPDATE workout_sessions SET status = 'completing'
               WHERE id = %s AND status = 'active' RETURNING *""",
            (session_id,)
        )
        session = cur.fetchone()
        if not session:
            # Already completed or abandoned — return existing data
            cur.execute("SELECT * FROM workout_sessions WHERE id = %s", (session_id,))
            session = cur.fetchone()
            if not session:
                raise ValueError(f"Session {session_id} not found")
            session = dict(session)
            if session.get("workout_id"):
                # Already has a logged workout — return it
                workout = {"id": session["workout_id"]}
                session["workout"] = workout
                session["duration_minutes"] = 0
                return session
            raise ValueError(f"Session {session_id} is {session.get('status')}, cannot finish")
        session = dict(session)

        cur.execute(
            "SELECT * FROM session_exercises WHERE session_id = %s ORDER BY sort_order",
            (session_id,)
        )
        exercises = [dict(row) for row in cur.fetchall()]

        # Calculate duration
        started = session["started_at"]
        now = datetime.now(timezone.utc)
        duration = max(1, int((now - started).total_seconds() / 60))

        # Build exercises list for log_workout (only exercises with completed sets)
        workout_exercises = []
        for ex in exercises:
            if ex["sets_completed"] > 0:
                workout_exercises.append({
                    "exercise_name": ex["exercise_name"],
                    "movement_pattern": ex["movement_pattern"],
                    "sets": ex["sets_completed"],
                    "reps": ex["target_reps"],
                    "weight": ex["target_weight"],
                    "weight_unit": ex["weight_unit"],
                    "rpe": ex.get("target_rpe"),
                })

        # Log as a real workout
        workout = log_workout(
            user_id=session["user_id"],
            title=session["title"],
            duration_minutes=duration,
            rpe=None,
            notes=None,
            exercises=workout_exercises if workout_exercises else None,
        )

        # Mark session complete (was 'completing' → 'completed')
        cur.execute(
            """UPDATE workout_sessions
               SET status = 'completed', completed_at = NOW(), workout_id = %s
               WHERE id = %s AND status = 'completing'""",
            (workout["id"], session_id)
        )

    session["workout"] = workout
    session["duration_minutes"] = duration
    session["exercises"] = exercises
    return session


def abandon_session(session_id: int):
    """Mark a session as abandoned."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE workout_sessions SET status = 'abandoned', completed_at = NOW() WHERE id = %s",
            (session_id,)
        )


def get_current_exercise(session_id: int) -> dict | None:
    """Get the exercise at the session's current_exercise_idx."""
    with get_cursor() as cur:
        cur.execute("SELECT current_exercise_idx FROM workout_sessions WHERE id = %s", (session_id,))
        row = cur.fetchone()
        if not row:
            return None
        idx = row["current_exercise_idx"] or 0

        cur.execute(
            "SELECT * FROM session_exercises WHERE session_id = %s AND sort_order = %s",
            (session_id, idx)
        )
        ex = cur.fetchone()
        return dict(ex) if ex else None


def set_current_exercise_idx(session_id: int, idx: int):
    """Set the current exercise index on a session."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE workout_sessions SET current_exercise_idx = %s WHERE id = %s",
            (idx, session_id)
        )


def set_card_message_id(session_id: int, message_id: int):
    """Store the Telegram message_id of the active card for this session."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE workout_sessions SET card_message_id = %s WHERE id = %s",
            (message_id, session_id)
        )


def get_card_message_id(session_id: int) -> int | None:
    """Get the stored card message_id for a session."""
    with get_cursor() as cur:
        cur.execute("SELECT card_message_id FROM workout_sessions WHERE id = %s", (session_id,))
        row = cur.fetchone()
        return row["card_message_id"] if row else None


def cleanup_stale_sessions(hours: int = 3):
    """Auto-finish or abandon sessions older than N hours.

    If a stale session has completed sets, finish it (log the workout).
    If no sets were completed, abandon it.
    Also clears short-term session context for affected users.
    """
    from bot.services import session_context

    with get_cursor() as cur:
        # Find stale active sessions
        cur.execute(
            """SELECT ws.id, ws.user_id,
                      COALESCE(SUM(se.sets_completed), 0) AS total_sets_done
               FROM workout_sessions ws
               LEFT JOIN session_exercises se ON se.session_id = ws.id
               WHERE ws.status = 'active'
                 AND ws.started_at < NOW() - INTERVAL '%s hours'
               GROUP BY ws.id, ws.user_id""",
            (hours,)
        )
        stale = cur.fetchall()

    for row in stale:
        sid, uid, sets_done = row["id"], row["user_id"], row["total_sets_done"]
        try:
            if sets_done > 0:
                # Has work done — auto-finish so progress isn't lost
                finish_session(sid)
                logger.info(f"Auto-finished stale session {sid} for user {uid} ({sets_done} sets)")
            else:
                # No work — just abandon
                with get_cursor() as cur:
                    cur.execute(
                        """UPDATE workout_sessions SET status = 'abandoned', completed_at = NOW()
                           WHERE id = %s AND status = 'active'""",
                        (sid,)
                    )
                logger.info(f"Abandoned empty stale session {sid} for user {uid}")
            # Clear session context either way
            session_context.clear_active_workout(uid)
        except Exception as e:
            logger.error(f"Stale session cleanup failed for {sid}: {e}")


# --- Proactive helpers ---

def get_typical_training_days(user_id: int, weeks: int = 4) -> list:
    """Detect which days of the week (PG DOW: 0=Sun..6=Sat) the user typically trains.
    Returns days that have at least 2 workouts in the window."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT EXTRACT(DOW FROM created_at)::int as dow, COUNT(*) as cnt
               FROM workouts WHERE user_id = %s
               AND created_at >= NOW() - make_interval(weeks => %s)
               GROUP BY dow HAVING COUNT(*) >= 2
               ORDER BY cnt DESC""",
            (user_id, weeks)
        )
        return [row["dow"] for row in cur.fetchall()]


def has_workout_today(user_id: int) -> bool:
    """Check if user logged a workout today."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT 1 FROM workouts WHERE user_id = %s AND created_at::date = CURRENT_DATE",
            (user_id,)
        )
        return cur.fetchone() is not None


def get_workouts_this_week(user_id: int) -> list:
    """Get workouts from the last 7 days with exercises."""
    return get_recent_workouts(user_id, days=7)
