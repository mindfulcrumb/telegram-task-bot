"""Interactive workout session handler — one exercise at a time, set tracking, rest timers."""
import logging
import os
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.services import user_service, fitness_service
from bot.utils import typing_pause_bot

# Railway domain for Mini App timer URL
_RAILWAY_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")

logger = logging.getLogger(__name__)

# In-memory rest timer state: session_id -> {"seconds": int, "label": str}
# Cleared when timer fires or user skips rest.
_rest_state = {}


def _escape_md(text: str) -> str:
    """Escape Telegram Markdown v1 special characters in user-generated text."""
    if not text:
        return text
    for ch in ("\\", "`", "*", "_", "[", "]"):
        text = text.replace(ch, f"\\{ch}")
    return text


def render_exercise_card(exercise: dict, session_id: int, exercise_index: int,
                         total_exercises: int, is_resting: bool = False,
                         rest_label: str = "", rest_seconds: int = 0) -> tuple:
    """Render a single exercise card with inline buttons.

    Returns (text, reply_markup) tuple.
    """
    ex_id = exercise["id"]
    name = _escape_md(exercise["exercise_name"])
    sets_target = exercise["target_sets"]
    sets_done = exercise["sets_completed"]
    reps = exercise["target_reps"]
    weight = exercise.get("target_weight")
    unit = exercise.get("weight_unit", "kg")
    rpe = exercise.get("target_rpe")
    notes = _escape_md(exercise.get("notes") or "")
    all_done = sets_done >= sets_target
    is_last = exercise_index == total_exercises - 1
    is_first = exercise_index == 0

    # ── Header ──
    text = f"*Exercise {exercise_index + 1} of {total_exercises}*\n\n"
    text += f"*{name}*\n"

    # Weight/reps line
    if weight:
        text += f"{sets_target} x {reps} @ {weight}{unit}"
    else:
        text += f"{sets_target} x {reps}"
    if rpe:
        text += f" | RPE {rpe}"
    text += "\n"

    # Coaching note
    if notes and notes.strip():
        text += f"_{notes}_\n"

    # ── Rest indicator ──
    if is_resting:
        text += f"\n\u23f1 *Resting — {rest_label}*\n"

    text += "\n"

    # ── Set progress ──
    for i in range(sets_target):
        if i < sets_done:
            text += f"  \u2705 Set {i + 1}\n"
        else:
            text += f"  \u2b1c Set {i + 1}\n"

    # ── Buttons ──
    buttons = []

    if is_resting:
        # While resting: skip button + Mini App timer for sound
        row = [InlineKeyboardButton(
            "\u23ed Skip Rest",
            callback_data=f"ws:k:{session_id}",
        )]
        buttons.append(row)

        # Add Mini App timer button (plays sound through media channel, bypasses silent mode)
        if _RAILWAY_DOMAIN and rest_seconds > 0:
            timer_url = f"https://{_RAILWAY_DOMAIN}/timer?s={rest_seconds}"
            buttons.append([InlineKeyboardButton(
                "\U0001f514 Open Timer with Sound",
                web_app=WebAppInfo(url=timer_url),
            )])

    elif all_done:
        # All sets complete
        text += "\n\u2705 *All sets complete!*"

        nav_row = []
        if sets_done > 0:
            nav_row.append(InlineKeyboardButton(
                "\u21a9 Undo",
                callback_data=f"ws:u:{session_id}",
            ))
        if not is_last:
            nav_row.append(InlineKeyboardButton(
                "Next Exercise \u25b6",
                callback_data=f"ws:n:{session_id}",
            ))
        else:
            nav_row.append(InlineKeyboardButton(
                "\U0001f3c1 Finish Workout",
                callback_data=f"ws:d:{session_id}",
            ))
        buttons.append(nav_row)

        # Back button if not first
        if not is_first:
            buttons.append([InlineKeyboardButton(
                "\u25c0 Back",
                callback_data=f"ws:p:{session_id}",
            )])

    else:
        # Working on sets
        row1 = [InlineKeyboardButton(
            f"\u2705 Set {sets_done + 1} done",
            callback_data=f"ws:s:{session_id}",
        )]
        if sets_done > 0:
            row1.append(InlineKeyboardButton(
                "\u21a9 Undo",
                callback_data=f"ws:u:{session_id}",
            ))
        buttons.append(row1)

        # Timer row
        timer_row = []
        for seconds, label in [(60, "1:00"), (90, "1:30"), (120, "2:00"), (180, "3:00"), (240, "4:00")]:
            timer_row.append(InlineKeyboardButton(
                f"\u23f1 {label}",
                callback_data=f"ws:t:{seconds}:{session_id}",
            ))
        buttons.append(timer_row)

        # Navigation row (back + skip to next if not first/not last)
        nav_row = []
        if not is_first:
            nav_row.append(InlineKeyboardButton(
                "\u25c0 Back",
                callback_data=f"ws:p:{session_id}",
            ))
        if not is_last:
            nav_row.append(InlineKeyboardButton(
                "Skip \u25b6",
                callback_data=f"ws:n:{session_id}",
            ))
        if nav_row:
            buttons.append(nav_row)

    return text, InlineKeyboardMarkup(buttons)


async def send_current_exercise(chat, context, session_id: int):
    """Send or edit the card to show the current exercise."""
    session = fitness_service.get_session_by_id(session_id)
    if not session:
        return

    # Store chat_id for timer callbacks
    fitness_service.update_session_chat_id(session_id, chat.id)

    exercises = session["exercises"]
    total = len(exercises)
    idx = session.get("current_exercise_idx", 0) or 0

    if idx >= total:
        idx = total - 1

    exercise = exercises[idx]
    is_resting = session_id in _rest_state
    rest_label = _rest_state[session_id]["label"] if is_resting else ""
    rest_seconds = _rest_state[session_id]["seconds"] if is_resting else 0

    text, markup = render_exercise_card(
        exercise, session_id, idx, total,
        is_resting=is_resting, rest_label=rest_label, rest_seconds=rest_seconds,
    )

    # Check if we already have a card message to edit
    existing_msg_id = session.get("card_message_id")
    if existing_msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat.id,
                message_id=existing_msg_id,
                text=text,
                reply_markup=markup,
                parse_mode="Markdown",
            )
            return
        except Exception:
            # Markdown failed or message too old — try without parse_mode
            try:
                await context.bot.edit_message_text(
                    chat_id=chat.id,
                    message_id=existing_msg_id,
                    text=text,
                    reply_markup=markup,
                )
                return
            except Exception:
                # Message too old or deleted — send new one
                pass

    # Send new card message — fallback to plaintext if Markdown fails
    try:
        msg = await chat.send_message(text, reply_markup=markup, parse_mode="Markdown")
    except Exception as md_err:
        logger.warning(f"Markdown send failed for session {session_id}, falling back to plain: {md_err}")
        msg = await chat.send_message(text, reply_markup=markup)
    fitness_service.set_card_message_id(session_id, msg.message_id)


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
        # ── Complete a set on current exercise ──
        session_id = int(parts[2])
        session = fitness_service.get_session_by_id(session_id)
        if not session:
            await query.answer("Session not found")
            return

        idx = session.get("current_exercise_idx", 0) or 0
        exercises = session["exercises"]
        if idx >= len(exercises):
            await query.answer("No exercise")
            return

        exercise = exercises[idx]
        ex = fitness_service.complete_set(exercise["id"])
        await query.answer(f"Set {ex['sets_completed']} done!")

        # Clear any rest timer
        _rest_state.pop(session_id, None)
        _cancel_timer(context, user["id"], session_id)

        await _refresh_card(query, context, session_id)

    elif action == "u":
        # ── Undo last set on current exercise ──
        session_id = int(parts[2])
        session = fitness_service.get_session_by_id(session_id)
        if not session:
            await query.answer("Session not found")
            return

        idx = session.get("current_exercise_idx", 0) or 0
        exercises = session["exercises"]
        if idx >= len(exercises):
            await query.answer("No exercise")
            return

        exercise = exercises[idx]
        if exercise["sets_completed"] <= 0:
            await query.answer("Nothing to undo")
            return

        fitness_service.undo_set(exercise["id"])
        await query.answer("Set undone")
        await _refresh_card(query, context, session_id)

    elif action == "t":
        # ── Start rest timer ──
        seconds = int(parts[2])
        session_id = int(parts[3])
        chat_id = query.message.chat.id

        minutes = seconds // 60
        remaining_secs = seconds % 60
        time_str = f"{minutes}:{remaining_secs:02d}"

        # Cancel any existing timer
        _cancel_timer(context, user["id"], session_id)

        # Set rest state
        _rest_state[session_id] = {"seconds": seconds, "label": time_str}

        await query.answer(f"\u23f1 Rest: {time_str}")

        # Update card to show resting state
        await _refresh_card(query, context, session_id)

        # Schedule timer callback
        job_name = f"rest_{user['id']}_{session_id}"
        context.job_queue.run_once(
            _rest_timer_callback,
            when=seconds,
            data={"chat_id": chat_id, "session_id": session_id, "user_id": user["id"]},
            name=job_name,
        )

    elif action == "k":
        # ── Skip / cancel rest timer ──
        session_id = int(parts[2])
        _rest_state.pop(session_id, None)
        _cancel_timer(context, user["id"], session_id)
        await query.answer("Rest skipped")
        await _refresh_card(query, context, session_id)

    elif action == "n":
        # ── Next exercise ──
        session_id = int(parts[2])
        session = fitness_service.get_session_by_id(session_id)
        if not session:
            await query.answer("Session not found")
            return

        idx = session.get("current_exercise_idx", 0) or 0
        total = len(session["exercises"])

        if idx < total - 1:
            new_idx = idx + 1
            fitness_service.set_current_exercise_idx(session_id, new_idx)

            # Clear any rest timer
            _rest_state.pop(session_id, None)
            _cancel_timer(context, user["id"], session_id)

            await query.answer(f"Exercise {new_idx + 1}/{total}")
            await _refresh_card(query, context, session_id)
        else:
            await query.answer("Already on the last exercise")

    elif action == "p":
        # ── Previous exercise ──
        session_id = int(parts[2])
        session = fitness_service.get_session_by_id(session_id)
        if not session:
            await query.answer("Session not found")
            return

        idx = session.get("current_exercise_idx", 0) or 0

        if idx > 0:
            new_idx = idx - 1
            fitness_service.set_current_exercise_idx(session_id, new_idx)

            _rest_state.pop(session_id, None)
            _cancel_timer(context, user["id"], session_id)

            await query.answer(f"Exercise {new_idx + 1}/{len(session['exercises'])}")
            await _refresh_card(query, context, session_id)
        else:
            await query.answer("Already on the first exercise")

    elif action == "d":
        # ── Finish workout ──
        session_id = int(parts[2])
        await query.answer()

        # Clear any timers
        _rest_state.pop(session_id, None)
        _cancel_timer(context, user["id"], session_id)

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

        lines = ["\U0001f3c1 Workout complete\n"]
        lines.append(f"{session['title']} \u2014 {duration} min")
        if ws > 1:
            lines.append(f"{ws}-session streak \U0001f525")
        if prs:
            for p in prs:
                lines.append(f"PR: {p['exercise']} {p['new_weight']}kg")
        lines.append("\nLogged and tracked. Nice work.")

        # Edit the card to show summary (replace the exercise card)
        try:
            await query.edit_message_text("\n".join(lines))
        except Exception:
            await query.message.reply_text("\n".join(lines))

    else:
        await query.answer()


async def _refresh_card(query, context, session_id: int):
    """Re-render and edit the current exercise card in place."""
    session = fitness_service.get_session_by_id(session_id)
    if not session:
        return

    exercises = session["exercises"]
    total = len(exercises)
    idx = session.get("current_exercise_idx", 0) or 0
    if idx >= total:
        idx = total - 1

    exercise = exercises[idx]
    is_resting = session_id in _rest_state
    rest_label = _rest_state[session_id]["label"] if is_resting else ""
    rest_seconds = _rest_state[session_id]["seconds"] if is_resting else 0

    text, markup = render_exercise_card(
        exercise, session_id, idx, total,
        is_resting=is_resting, rest_label=rest_label, rest_seconds=rest_seconds,
    )

    try:
        await query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
    except Exception:
        # Markdown failed or text identical — try without parse_mode
        try:
            await query.edit_message_text(text, reply_markup=markup)
        except Exception:
            pass  # Text truly identical or message too old


def _cancel_timer(context, user_id: int, session_id: int):
    """Cancel any active rest timer job for this session."""
    job_name = f"rest_{user_id}_{session_id}"
    try:
        current_jobs = context.job_queue.get_jobs_by_name(job_name)
        for job in current_jobs:
            job.schedule_removal()
    except Exception:
        pass


async def _rest_timer_callback(context: ContextTypes.DEFAULT_TYPE):
    """Fired when rest timer expires. Updates the card + sends notification."""
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    session_id = job_data["session_id"]

    # Clear rest state
    _rest_state.pop(session_id, None)

    # Send notification
    await typing_pause_bot(context.bot, chat_id, 0.4)
    await context.bot.send_message(
        chat_id=chat_id,
        text="\u23f0 Time's up. Next set.",
    )

    # Re-render the card to remove rest indicator
    session = fitness_service.get_session_by_id(session_id)
    if not session:
        return

    msg_id = session.get("card_message_id")
    if not msg_id:
        return

    exercises = session["exercises"]
    total = len(exercises)
    idx = session.get("current_exercise_idx", 0) or 0
    if idx >= total:
        idx = total - 1

    exercise = exercises[idx]
    text, markup = render_exercise_card(exercise, session_id, idx, total)

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=text,
            reply_markup=markup,
            parse_mode="Markdown",
        )
    except Exception:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                reply_markup=markup,
            )
        except Exception:
            pass
