"""Interactive workout session handler — exercise cards, set tracking, rest timers."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.services import user_service, fitness_service

logger = logging.getLogger(__name__)


def render_exercise_card(exercise: dict, session_id: int, exercise_index: int, total_exercises: int) -> tuple:
    """Render an exercise card message with inline buttons.

    Returns (text, reply_markup) tuple.
    """
    ex_id = exercise["id"]
    name = exercise["exercise_name"]
    sets_target = exercise["target_sets"]
    sets_done = exercise["sets_completed"]
    reps = exercise["target_reps"]
    weight = exercise.get("target_weight")
    unit = exercise.get("weight_unit", "kg")
    rpe = exercise.get("target_rpe")
    notes = exercise.get("notes")

    # Header
    position = f"{exercise_index + 1}/{total_exercises}"
    text = f"*{name}* ({position})\n"

    # Weight/reps line
    if weight:
        text += f"{sets_target} x {reps} @ {weight}{unit}"
    else:
        text += f"{sets_target} x {reps}"
    if rpe:
        text += f" | RPE {rpe}"
    text += "\n"

    # Coaching note
    if notes:
        text += f"_{notes}_\n"

    text += "\n"

    # Set progress
    for i in range(sets_target):
        if i < sets_done:
            text += f"  \u2705 Set {i + 1}\n"
        else:
            text += f"  \u2b1c Set {i + 1}\n"

    # Buttons
    buttons = []

    # Row 1: Set tracking + undo
    row1 = []
    if sets_done < sets_target:
        row1.append(InlineKeyboardButton(
            f"\u2705 Set {sets_done + 1} done",
            callback_data=f"ws:s:{ex_id}",
        ))
    else:
        row1.append(InlineKeyboardButton(
            "\u2705 All sets done",
            callback_data="ws:noop",
        ))
    if sets_done > 0:
        row1.append(InlineKeyboardButton(
            "\u21a9 Undo",
            callback_data=f"ws:u:{ex_id}",
        ))
    buttons.append(row1)

    # Row 2: Rest timers
    timer_row = []
    for seconds, label in [(60, "1:00"), (90, "1:30"), (120, "2:00"), (180, "3:00"), (240, "4:00")]:
        timer_row.append(InlineKeyboardButton(
            f"\u23f1 {label}",
            callback_data=f"ws:t:{seconds}:{ex_id}",
        ))
    buttons.append(timer_row)

    # Row 3: Finish workout
    buttons.append([InlineKeyboardButton(
        "\U0001f3c1 Finish Workout",
        callback_data=f"ws:d:{session_id}",
    )])

    return text, InlineKeyboardMarkup(buttons)


async def send_workout_cards(update, context, session_id: int):
    """Send exercise cards as individual messages for an active session."""
    session = fitness_service.get_session_by_id(session_id)
    if not session:
        return

    chat = update.message.chat if hasattr(update, "message") and update.message else update

    # Store chat_id on session for timer callbacks
    fitness_service.update_session_chat_id(session_id, chat.id)

    exercises = session["exercises"]
    total = len(exercises)

    for i, ex in enumerate(exercises):
        text, markup = render_exercise_card(ex, session_id, i, total)
        msg = await chat.send_message(text, reply_markup=markup, parse_mode="Markdown")
        # Store message_id so we can edit this card later
        fitness_service.update_session_exercise_message_id(ex["id"], msg.message_id)


async def handle_workout_session_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all workout session inline button callbacks (ws:* pattern)."""
    query = update.callback_query
    data = query.data

    parts = data.split(":")
    if len(parts) < 3:
        await query.answer()
        return

    action = parts[1]

    if action == "noop":
        await query.answer("All sets already complete!")
        return

    # Get user
    user = context.user_data.get("db_user")
    if not user:
        tg = update.effective_user
        user = user_service.get_or_create_user(tg.id, tg.username, tg.first_name)
        context.user_data["db_user"] = user

    if action == "s":
        # Complete a set
        exercise_id = int(parts[2])
        ex = fitness_service.complete_set(exercise_id)
        await query.answer(f"Set {ex['sets_completed']} done!")
        await _refresh_exercise_card(query, ex)

    elif action == "u":
        # Undo last set
        exercise_id = int(parts[2])
        ex = fitness_service.undo_set(exercise_id)
        await query.answer("Set undone")
        await _refresh_exercise_card(query, ex)

    elif action == "t":
        # Start rest timer
        seconds = int(parts[2])
        exercise_id = int(parts[3]) if len(parts) > 3 else None
        chat_id = query.message.chat.id

        minutes = seconds // 60
        remaining_secs = seconds % 60
        time_str = f"{minutes}:{remaining_secs:02d}"
        await query.answer(f"\u23f1 Rest: {time_str}")

        # Cancel any existing rest timer for this user
        if exercise_id:
            job_name = f"rest_{user['id']}_{exercise_id}"
            current_jobs = context.job_queue.get_jobs_by_name(job_name)
            for job in current_jobs:
                job.schedule_removal()

            # Schedule timer callback
            context.job_queue.run_once(
                _rest_timer_callback,
                when=seconds,
                data={"chat_id": chat_id, "exercise_id": exercise_id},
                name=job_name,
            )

    elif action == "d":
        # Finish workout
        session_id = int(parts[2])
        await query.answer()

        try:
            session = fitness_service.finish_session(session_id)
        except Exception as e:
            logger.error(f"Failed to finish session: {e}")
            await query.message.reply_text("Couldn't log workout. Try again.")
            return

        workout = session.get("workout", {})
        duration = session.get("duration_minutes", 0)

        # Build completion summary
        prs = workout.get("prs", [])
        streak = fitness_service.get_workout_streak(user["id"])
        ws = streak.get("current_streak", 0)

        lines = ["*Workout complete!*\n"]
        lines.append(f"{session['title']} \u2014 {duration} min")
        if ws > 1:
            lines.append(f"\U0001f525 {ws}-session streak")
        if prs:
            for p in prs:
                lines.append(f"\U0001f3c6 PR: {p['exercise']} {p['new_weight']}kg")
        lines.append("\nLogged and tracked. Nice work.")

        await query.message.reply_text("\n".join(lines), parse_mode="Markdown")

    else:
        await query.answer()


async def _refresh_exercise_card(query, exercise: dict):
    """Edit the exercise card message to reflect updated set state."""
    # Get session info for rendering
    session = fitness_service.get_session_by_id(exercise["session_id"])
    if not session:
        return

    total = len(session["exercises"])
    sort_order = exercise["sort_order"]

    text, markup = render_exercise_card(exercise, session["id"], sort_order, total)

    try:
        await query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
    except Exception:
        pass  # Telegram error if text is identical


async def _rest_timer_callback(context: ContextTypes.DEFAULT_TYPE):
    """Fired when rest timer expires. Sends 'Time's up!' message."""
    job_data = context.job.data
    chat_id = job_data["chat_id"]

    await context.bot.send_message(
        chat_id=chat_id,
        text="\u23f0 *Time's up!* Next set.",
        parse_mode="Markdown",
    )
