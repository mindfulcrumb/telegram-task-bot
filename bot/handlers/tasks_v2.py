"""Task command handlers — user-scoped, PostgreSQL-backed."""
import asyncio
import logging
import re
from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.services import user_service, task_service, tier_service
from bot.ai.brain_v2 import ai_brain
from bot.utils import typing_pause
from bot.handlers.message_utils import clean_response as _clean_response, send_chunked

logger = logging.getLogger(__name__)


async def _send_human(update: Update, text: str, add_feedback: bool = False):
    """Send a response in natural chunks with typing delays."""
    if not text:
        return

    feedback_markup = None
    if add_feedback:
        feedback_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("\U0001f44d", callback_data="fb:pos"),
                InlineKeyboardButton("\U0001f44e", callback_data="fb:neg"),
            ]
        ])

    await send_chunked(
        bot=update.effective_chat.bot,
        chat_id=update.effective_chat.id,
        text=text,
        reply_markup=feedback_markup,
    )


async def _get_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    """Get or create user from Telegram update.

    Returns None (and sends a message) if the user hasn't completed
    onboarding/phone verification — this blocks ALL bot functionality.
    """
    user = context.user_data.get("db_user")
    if not user:
        tg = update.effective_user
        user = user_service.get_or_create_user(tg.id, tg.username, tg.first_name)
        context.user_data["db_user"] = user

    if not user.get("onboarding_completed"):
        await update.message.reply_text(
            "You need to verify your phone number first. Type /start to begin."
        )
        return None
    return user


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add command."""
    user = await _get_user(update, context)
    if not user:
        return
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("What's the task? e.g., /add buy groceries")
        return

    # Check tier limit
    allowed, msg = tier_service.check_limit(user["id"], "add_task", user.get("tier", "free"), is_admin=user.get("is_admin", False), telegram_user_id=user.get("telegram_user_id"))
    if not allowed:
        from bot.handlers.payments import get_subscribe_keyboard
        keyboard = get_subscribe_keyboard(update.effective_user.id)
        await update.message.reply_text(msg, reply_markup=keyboard)
        return

    task_service.add_task(user["id"], title=text)
    await typing_pause(update.message.chat, 0.3)
    await update.message.reply_text(f"Added: {text}")


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list command."""
    user = await _get_user(update, context)
    if not user:
        return
    tasks = task_service.get_tasks(user["id"])
    if not tasks:
        await update.message.reply_text("No tasks! Add one with /add or just tell me.")
        return
    await typing_pause(update.message.chat, 0.5)
    await update.message.reply_text(_format_tasks(tasks))


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /today command."""
    user = await _get_user(update, context)
    if not user:
        return
    tasks = task_service.get_tasks(user["id"], "today")
    if not tasks:
        await update.message.reply_text("Nothing due today!")
        return
    await typing_pause(update.message.chat, 0.5)
    await update.message.reply_text(f"Due today:\n\n{_format_tasks(tasks)}")


async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /week command."""
    user = await _get_user(update, context)
    if not user:
        return
    tasks = task_service.get_tasks(user["id"], "week")
    if not tasks:
        await update.message.reply_text("Nothing due this week!")
        return
    await typing_pause(update.message.chat, 0.5)
    await update.message.reply_text(f"This week:\n\n{_format_tasks(tasks)}")


async def cmd_overdue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /overdue command."""
    user = await _get_user(update, context)
    if not user:
        return
    tasks = task_service.get_tasks(user["id"], "overdue")
    if not tasks:
        await update.message.reply_text("Nothing overdue!")
        return
    await typing_pause(update.message.chat, 0.5)
    await update.message.reply_text(f"Overdue:\n\n{_format_tasks(tasks)}")


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /done command."""
    user = await _get_user(update, context)
    if not user:
        return
    if not context.args:
        await update.message.reply_text("Which task? e.g., /done 1 or /done 1 3 5")
        return

    try:
        nums = [int(a) for a in context.args]
    except ValueError:
        await update.message.reply_text("Use task numbers, e.g., /done 1 3")
        return

    completed, not_found = task_service.complete_tasks(user["id"], nums)
    parts = []
    await typing_pause(update.message.chat, 0.3)
    if completed:
        titles = ", ".join(t["title"] for t in completed)
        parts.append(f"Done: {titles}")
        # Update streak
        try:
            from bot.services import coaching_service
            streak = coaching_service.update_streak(user["id"])
            s = streak.get("current_streak", 0)
            if s > 1:
                parts.append(f"\U0001f525 {s}-day streak!")
            elif s == 1:
                parts.append("\U0001f525 Streak started!")
        except Exception:
            pass
    if not_found:
        parts.append(f"Not found: {', '.join(str(n) for n in not_found)}")
    await update.message.reply_text("\n".join(parts) or "Nothing to complete.")


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /delete command."""
    user = await _get_user(update, context)
    if not user:
        return
    if not context.args:
        await update.message.reply_text("Which task? e.g., /delete 2")
        return

    try:
        nums = [int(a) for a in context.args]
    except ValueError:
        await update.message.reply_text("Use task numbers, e.g., /delete 2")
        return

    deleted, not_found = task_service.delete_tasks(user["id"], nums)
    await typing_pause(update.message.chat, 0.3)
    parts = []
    if deleted:
        titles = ", ".join(t["title"] for t in deleted)
        parts.append(f"Deleted: {titles}")
    if not_found:
        parts.append(f"Not found: {', '.join(str(n) for n in not_found)}")
    await update.message.reply_text("\n".join(parts) or "Nothing to delete.")


async def cmd_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /edit command."""
    user = await _get_user(update, context)
    if not user:
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /edit 1 new task title")
        return

    try:
        num = int(context.args[0])
    except ValueError:
        await update.message.reply_text("First argument must be a task number.")
        return

    new_title = " ".join(context.args[1:])
    result = task_service.update_task_title(user["id"], num, new_title)
    await typing_pause(update.message.chat, 0.3)
    if result:
        await update.message.reply_text(f"Updated: \"{result[0]}\" → \"{result[1]}\"")
    else:
        await update.message.reply_text(f"Task #{num} not found.")


async def cmd_undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /undo command."""
    user = await _get_user(update, context)
    if not user:
        return
    from bot.ai.tools_v2 import _undo_buffer
    entries = _undo_buffer.pop(user["id"], None)
    if not entries:
        await update.message.reply_text("Nothing to undo.")
        return

    task_ids = [e["task_id"] for e in entries]
    restored = task_service.restore_tasks(user["id"], task_ids)
    if restored:
        await update.message.reply_text(f"Restored: {', '.join(restored)}")
    else:
        await update.message.reply_text("Couldn't restore those tasks.")


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear conversation history."""
    user = await _get_user(update, context)
    if not user:
        return
    from bot.ai import memory_pg
    memory_pg.clear_history(user["id"])
    await typing_pause(update.message.chat, 0.3)
    await update.message.reply_text("Conversation history cleared.")


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick task analysis."""
    user = await _get_user(update, context)
    if not user:
        return
    tasks = task_service.get_tasks(user["id"])
    if not tasks:
        await update.message.reply_text("No tasks to analyze!")
        return

    total = len(tasks)
    today_d = date.today()
    overdue = sum(1 for t in tasks if t.get("due_date") and t["due_date"] < today_d)
    high = sum(1 for t in tasks if t.get("priority") == "High")
    biz = sum(1 for t in tasks if t.get("category") == "Business")
    personal = total - biz

    text = (
        f"Quick overview:\n\n"
        f"Total: {total} tasks\n"
        f"Overdue: {overdue}\n"
        f"High priority: {high}\n"
        f"Business: {biz} | Personal: {personal}"
    )
    await typing_pause(update.message.chat, 0.6)
    await update.message.reply_text(text)


async def cmd_streak(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's current streak."""
    user = await _get_user(update, context)
    if not user:
        return
    from bot.services import coaching_service
    streak = coaching_service.get_streak(user["id"])
    current = streak.get("current_streak", 0)
    longest = streak.get("longest_streak", 0)
    last = streak.get("last_completion_date")

    if current == 0:
        text = "No streak going yet. Complete a task to start one."
    else:
        text = (
            f"Current streak: {current} day{'s' if current != 1 else ''} \U0001f525\n"
            f"Best ever: {longest} day{'s' if longest != 1 else ''}\n"
            f"Last completed: {last.strftime('%b %d') if last else 'never'}"
        )
    await typing_pause(update.message.chat, 0.6)
    await update.message.reply_text(text)


async def cmd_workout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /workout command — quick log or prompt."""
    user = await _get_user(update, context)
    if not user:
        return
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text(
            "Log a workout:\n"
            "/workout push day 45min\n"
            "/workout cardio 30min\n\n"
            "Or just tell me naturally what you did!"
        )
        return
    # Feed into AI brain as a workout log
    chat = update.message.chat
    await chat.send_action(ChatAction.TYPING)

    async def _keep_typing():
        await chat.send_action(ChatAction.TYPING)

    tasks = task_service.get_tasks(user["id"])
    prompt = f"Log this workout: {text}"
    response = await ai_brain.process(prompt, user, tasks, typing_callback=_keep_typing)

    pending_session_id = ai_brain._pending_session.pop(user["id"], None)

    if response:
        await _send_human(update, response)

    if pending_session_id:
        from bot.handlers.workout_session import send_current_exercise
        await send_current_exercise(update.message.chat, context, pending_session_id)


async def cmd_metrics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /metrics command — show body metrics."""
    user = await _get_user(update, context)
    if not user:
        return
    from bot.services import fitness_service
    metrics = fitness_service.get_latest_metrics(user["id"])
    if not metrics:
        await update.message.reply_text("No body metrics yet. Tell me your weight, body fat, or 1RMs.")
        return
    lines = []
    for metric_type, data in metrics.items():
        label = metric_type.replace("_", " ").title()
        unit = data.get("unit", "")
        val = data["value"]
        date_str = data["recorded_at"].strftime("%b %d") if data.get("recorded_at") else ""
        lines.append(f"{label}: {val}{unit} ({date_str})")
    await typing_pause(update.message.chat, 0.7)
    await update.message.reply_text("\n".join(lines))


async def cmd_gains(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /gains command — workout streak + PRs + pattern balance."""
    user = await _get_user(update, context)
    if not user:
        return
    from bot.services import fitness_service

    streak = fitness_service.get_workout_streak(user["id"])
    patterns = fitness_service.get_movement_pattern_balance(user["id"], days=14)
    prs = fitness_service.detect_prs(user["id"])

    lines = []

    # Streak
    current = streak.get("current_streak", 0)
    longest = streak.get("longest_streak", 0)
    if current > 0:
        lines.append(f"Workout streak: {current} (best: {longest}) \U0001f525")
    else:
        lines.append("No workout streak going. Log a session to start one.")

    # Pattern balance
    if patterns:
        lines.append("\nPattern Balance (14d):")
        pattern_labels = {
            "horizontal_push": "Push (horiz)",
            "horizontal_pull": "Pull (horiz)",
            "vertical_push": "Push (vert)",
            "vertical_pull": "Pull (vert)",
            "squat": "Squat",
            "hinge": "Hinge",
            "carry_rotation": "Carry/Rotation",
        }
        for key, label in pattern_labels.items():
            count = patterns.get(key, 0)
            bar = "\u2588" * count if count > 0 else "-"
            lines.append(f"  {label}: {bar} {count}")

    # PRs
    if prs:
        lines.append("\nRecent PRs:")
        for p in prs[:5]:
            lines.append(f"  {p['exercise'].title()}: {p['new_weight']}kg (was {p['previous_best']}kg)")

    await typing_pause(update.message.chat, 0.8)
    await update.message.reply_text("\n".join(lines))


async def cmd_protocols(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /protocols command — show active peptide protocols."""
    user = await _get_user(update, context)
    if not user:
        return
    from bot.services import biohacking_service
    protocols = biohacking_service.get_protocol_summary(user["id"])
    if not protocols:
        await update.message.reply_text(
            "No active protocols.\n\n"
            "Just tell me naturally to start one:\n"
            '"Starting BPC-157, 250mcg twice a day for 6 weeks"'
        )
        return
    lines = ["Active Protocols\n"]
    for p in protocols:
        dose_str = f"{p.get('dose_amount', '?')} {p.get('dose_unit', 'mcg')}" if p.get("dose_amount") else ""
        freq_str = f" {p.get('frequency', '')}" if p.get("frequency") else ""
        line = f"{p['peptide_name']}: {dose_str}{freq_str}"
        if p.get("cycle_day") is not None:
            line += f"\n  Day {p['cycle_day']}/{p['cycle_total']}"
            if p.get("days_remaining") is not None:
                line += f" ({p['days_remaining']}d remaining)"
        line += f"\n  Doses (7d): {p.get('doses_last_7d', 0)}"
        lines.append(line)
    await typing_pause(update.message.chat, 0.8)
    await update.message.reply_text("\n\n".join(lines))


async def cmd_supplements(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /supplements command — show supplement stack."""
    user = await _get_user(update, context)
    if not user:
        return
    from bot.services import biohacking_service
    supps = biohacking_service.get_active_supplements(user["id"])
    if not supps:
        await update.message.reply_text(
            "Nothing in your stack yet.\n\n"
            "Tell me what you take:\n"
            '"creatine 5g daily, vitamin D 5000IU with breakfast"'
        )
        return
    lines = ["Your Stack\n"]
    for s in supps:
        dose_str = f"{s.get('dose_amount', '')}{s.get('dose_unit', '')}" if s.get("dose_amount") else ""
        timing_str = f" ({s['timing']})" if s.get("timing") else ""
        lines.append(f"  {s['supplement_name']}: {dose_str}{timing_str}")
    # Adherence
    adherence = biohacking_service.get_supplement_adherence(user["id"], days=7)
    if adherence["overall_rate"] > 0:
        lines.append(f"\n7-day adherence: {adherence['overall_rate']}%")
    await typing_pause(update.message.chat, 0.7)
    await update.message.reply_text("\n".join(lines))


async def cmd_bloodwork(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /bloodwork command — show latest bloodwork with optimal ranges & trends."""
    user = await _get_user(update, context)
    if not user:
        return
    from bot.services import biohacking_service
    enriched = biohacking_service.get_enriched_bloodwork(user["id"])
    if not enriched:
        await update.message.reply_text(
            "No bloodwork logged yet.\n\n"
            "Tell me your results:\n"
            '"testosterone came back at 650 ng/dL, vitamin D at 45 ng/mL"'
        )
        return
    panel = enriched["panel"]
    date_str = panel["test_date"].isoformat() if panel.get("test_date") else "?"
    lab_str = f" ({panel['lab_name']})" if panel.get("lab_name") else ""
    lines = [f"Bloodwork \u2014 {date_str}{lab_str}\n"]

    for m in enriched["markers"]:
        unit_str = f" {m['unit']}" if m.get("unit") else ""

        # Status icon based on optimal range (preferred) or lab flag
        status_icon = ""
        if m.get("optimal_status") == "below":
            status_icon = " \u2b07\ufe0f"
        elif m.get("optimal_status") == "above":
            status_icon = " \u2b06\ufe0f"
        elif m.get("flag") == "high":
            status_icon = " \u2b06\ufe0f"
        elif m.get("flag") == "low":
            status_icon = " \u2b07\ufe0f"
        elif m.get("optimal_status") == "optimal":
            status_icon = " \u2705"

        # Show optimal range if available, otherwise lab range
        range_str = ""
        if m.get("optimal_low") is not None and m.get("optimal_high") is not None:
            range_str = f" (optimal: {m['optimal_low']}-{m['optimal_high']})"
        elif m.get("reference_low") is not None and m.get("reference_high") is not None:
            range_str = f" (ref: {m['reference_low']}-{m['reference_high']})"

        # Trend arrow vs previous panel
        trend_str = ""
        if m.get("change") is not None:
            change = m["change"]
            if abs(change) > 0.01:
                arrow = "\u25b2" if change > 0 else "\u25bc"
                trend_str = f" {arrow}{abs(change):.0f}"

        lines.append(f"  {m['marker_name']}: {m['value']}{unit_str}{range_str}{status_icon}{trend_str}")

    # Summary footer
    flagged = enriched["flagged_count"]
    suboptimal = enriched["suboptimal_count"]
    total = len(enriched["markers"])
    optimal_count = total - suboptimal
    if total > 0:
        lines.append(f"\n{optimal_count}/{total} markers in optimal range")
    if enriched.get("previous_date"):
        lines.append(f"Compared to: {enriched['previous_date'].isoformat()}")
    if enriched.get("active_protocols"):
        protocol_names = [p["peptide_name"] for p in enriched["active_protocols"][:3]]
        if protocol_names:
            lines.append(f"Active protocols: {', '.join(protocol_names)}")

    await typing_pause(update.message.chat, 1.0)
    await update.message.reply_text("\n".join(lines))


async def cmd_dose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /dose command — quick log a peptide dose."""
    user = await _get_user(update, context)
    if not user:
        return
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text(
            "Quick-log a dose:\n"
            "/dose BPC-157\n"
            "/dose Ipamorelin abdomen\n\n"
            'Or just tell me: "Took my BPC"'
        )
        return
    # Feed into AI brain
    chat = update.message.chat
    await chat.send_action(ChatAction.TYPING)

    async def _keep_typing():
        await chat.send_action(ChatAction.TYPING)

    tasks = task_service.get_tasks(user["id"])
    prompt = f"Log this peptide dose: {text}"
    response = await ai_brain.process(prompt, user, tasks, typing_callback=_keep_typing)
    if response:
        await _send_human(update, response)


async def cmd_connect_whoop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /connect_whoop command — link WHOOP device."""
    user = await _get_user(update, context)
    if not user:
        return
    from bot.services import whoop_service

    if not whoop_service.is_configured():
        await update.message.reply_text(
            "WHOOP integration isn't live yet \u2014 it's coming."
        )
        return

    if whoop_service.is_connected(user["id"]):
        await update.message.reply_text(
            "Already connected. Use /recovery to see your data."
        )
        return

    url = whoop_service.get_auth_url(user["id"])
    if url:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Connect WHOOP", url=url)]
        ])
        await update.message.reply_text(
            "Connect your WHOOP and I'll use your recovery data to guide training intensity.\n\n"
            "Tap below, log in, and authorize.",
            reply_markup=keyboard,
        )
    else:
        await update.message.reply_text("Couldn't generate the link. Try again in a bit.")


async def cmd_recovery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /recovery command — show today's WHOOP recovery."""
    user = await _get_user(update, context)
    if not user:
        return
    from bot.services import whoop_service

    if not whoop_service.is_connected(user["id"]):
        await update.message.reply_text(
            "WHOOP not connected. Use /connect_whoop to link your device."
        )
        return

    chat = update.message.chat
    await chat.send_action(ChatAction.TYPING)

    # Sync fresh data
    try:
        whoop_service.sync_all(user["id"])
    except Exception as e:
        logger.error(f"WHOOP sync failed in /recovery: {type(e).__name__}: {e}")

    data = whoop_service.get_today_recovery(user["id"])
    if not data:
        await update.message.reply_text("No recovery data available yet. Check back after your WHOOP syncs.")
        return

    recovery = data.get("recovery_score")
    zone = whoop_service.get_recovery_zone(recovery)
    zone_emoji = {"green": "\U0001f7e2", "yellow": "\U0001f7e1", "red": "\U0001f534"}.get(zone, "\u26aa")

    lines = [f"{zone_emoji} Recovery: {recovery}% ({zone.upper()})\n"]

    if data.get("hrv_rmssd") is not None:
        lines.append(f"HRV: {data['hrv_rmssd']}ms")
    if data.get("resting_hr") is not None:
        lines.append(f"Resting HR: {data['resting_hr']}bpm")
    if data.get("sleep_performance") is not None:
        sleep_line = f"Sleep: {data['sleep_performance']}%"
        if data.get("deep_sleep_minutes") is not None:
            sleep_line += f" ({data['deep_sleep_minutes']}min deep"
            if data.get("rem_sleep_minutes") is not None:
                sleep_line += f", {data['rem_sleep_minutes']}min REM"
            sleep_line += ")"
        lines.append(sleep_line)
    if data.get("daily_strain") is not None:
        lines.append(f"Strain: {data['daily_strain']}")
    if data.get("spo2") is not None:
        lines.append(f"SpO2: {data['spo2']}%")
    if data.get("respiratory_rate") is not None:
        lines.append(f"Respiratory rate: {data['respiratory_rate']}")
    if data.get("skin_temp") is not None:
        lines.append(f"Skin temp: {data['skin_temp']}C")

    # Add recommendation based on zone
    if zone == "green":
        lines.append("\nGreen zone — go hard today.")
    elif zone == "yellow":
        lines.append("\nYellow zone — moderate intensity, no maxes.")
    else:
        lines.append("\nRed zone — recovery day. Mobility or rest.")

    await typing_pause(update.message.chat, 0.8)

    # Inline action buttons
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("What should I train?", callback_data="whoop_train"),
            InlineKeyboardButton("Log workout", callback_data="whoop_log"),
        ],
        [
            InlineKeyboardButton("Full dashboard", callback_data="whoop_dashboard"),
        ],
    ])

    await update.message.reply_text("\n".join(lines), reply_markup=keyboard)


async def cmd_whoop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /whoop command — full WHOOP dashboard."""
    user = await _get_user(update, context)
    if not user:
        return
    from bot.services import whoop_service

    if not whoop_service.is_connected(user["id"]):
        await update.message.reply_text(
            "WHOOP not connected. Use /connect_whoop to link your device."
        )
        return

    chat = update.message.chat
    await chat.send_action(ChatAction.TYPING)

    try:
        whoop_service.sync_all(user["id"])
    except Exception as e:
        logger.error(f"WHOOP sync failed in /whoop: {type(e).__name__}: {e}")

    data = whoop_service.get_today_recovery(user["id"])
    trends = whoop_service.get_whoop_trends(user["id"], days=7)

    if not data:
        await update.message.reply_text("No WHOOP data available yet.")
        return

    recovery = data.get("recovery_score")
    zone = whoop_service.get_recovery_zone(recovery)
    zone_emoji = {"green": "\U0001f7e2", "yellow": "\U0001f7e1", "red": "\U0001f534"}.get(zone, "\u26aa")

    lines = [f"WHOOP Dashboard\n"]

    # Today
    lines.append(f"{zone_emoji} Recovery: {recovery}% ({zone})")
    if data.get("hrv_rmssd") is not None:
        lines.append(f"HRV: {data['hrv_rmssd']}ms")
    if data.get("resting_hr") is not None:
        lines.append(f"Resting HR: {data['resting_hr']}bpm")
    if data.get("sleep_performance") is not None:
        sleep_str = f"Sleep: {data['sleep_performance']}%"
        parts = []
        if data.get("deep_sleep_minutes") is not None:
            parts.append(f"{data['deep_sleep_minutes']}min deep")
        if data.get("rem_sleep_minutes") is not None:
            parts.append(f"{data['rem_sleep_minutes']}min REM")
        if data.get("light_sleep_minutes") is not None:
            parts.append(f"{data['light_sleep_minutes']}min light")
        if parts:
            sleep_str += f" ({', '.join(parts)})"
        lines.append(sleep_str)
    if data.get("daily_strain") is not None:
        lines.append(f"Strain: {data['daily_strain']}")
    if data.get("spo2") is not None:
        lines.append(f"SpO2: {data['spo2']}%")
    if data.get("respiratory_rate") is not None:
        lines.append(f"Respiratory rate: {data['respiratory_rate']}")
    if data.get("skin_temp") is not None:
        lines.append(f"Skin temp: {data['skin_temp']}C")
    if data.get("calories_kj") is not None:
        lines.append(f"Calories: {round(data['calories_kj'])} kJ")

    # 7-day trends
    if trends and trends.get("days", 0) > 2:
        lines.append("\n7-Day Trends:")
        if trends.get("recovery_avg") is not None:
            arrow = {"trending_up": "\u2191", "trending_down": "\u2193", "stable": "\u2192"}.get(
                trends.get("recovery_trend", ""), ""
            )
            lines.append(f"Recovery avg: {trends['recovery_avg']}% {arrow}")
        if trends.get("hrv_avg") is not None:
            arrow = {"trending_up": "\u2191", "trending_down": "\u2193", "stable": "\u2192"}.get(
                trends.get("hrv_trend", ""), ""
            )
            lines.append(f"HRV avg: {trends['hrv_avg']}ms {arrow}")
        if trends.get("sleep_avg") is not None:
            lines.append(f"Sleep avg: {trends['sleep_avg']}%")
        if trends.get("strain_avg") is not None:
            lines.append(f"Strain avg: {trends['strain_avg']}")

    await typing_pause(update.message.chat, 1.0)

    # Inline action buttons
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("What should I train?", callback_data="whoop_train"),
            InlineKeyboardButton("Log workout", callback_data="whoop_log"),
        ],
    ])

    await update.message.reply_text("\n".join(lines), reply_markup=keyboard)


async def cmd_disconnect_whoop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /disconnect_whoop — unlink WHOOP device."""
    user = await _get_user(update, context)
    if not user:
        return
    from bot.services import whoop_service

    if not whoop_service.is_connected(user["id"]):
        await update.message.reply_text("WHOOP isn't connected.")
        return

    whoop_service.revoke_access(user["id"])
    await typing_pause(update.message.chat, 0.4)
    await update.message.reply_text("WHOOP disconnected. All WHOOP data removed.")


async def handle_whoop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks from WHOOP commands."""
    query = update.callback_query
    await query.answer()

    user = context.user_data.get("db_user")
    if not user:
        tg = update.effective_user
        user = user_service.get_or_create_user(tg.id, tg.username, tg.first_name)
        context.user_data["db_user"] = user

    chat = query.message.chat

    if query.data == "whoop_train":
        await chat.send_action(ChatAction.TYPING)

        async def _keep_typing():
            await chat.send_action(ChatAction.TYPING)

        tasks = task_service.get_tasks(user["id"])
        response = await ai_brain.process(
            "Based on my WHOOP recovery, what should I train today? Give me a specific session.",
            user, tasks, typing_callback=_keep_typing,
        )

        pending_session_id = ai_brain._pending_session.pop(user["id"], None)

        if response:
            await typing_pause(chat, 0.6)
            await chat.send_message(_clean_response(response))

        if pending_session_id:
            from bot.handlers.workout_session import send_current_exercise
            await send_current_exercise(chat, context, pending_session_id)

    elif query.data == "whoop_log":
        await chat.send_message("What'd you do? e.g. 'push day \u2014 bench 4x8 at 75kg, OHP 3x10 at 40kg, 50 min'")

    elif query.data == "whoop_dashboard":
        # Trigger the full dashboard inline
        from bot.services import whoop_service
        if not whoop_service.is_connected(user["id"]):
            await chat.send_message("WHOOP not connected. Use /connect_whoop to link.")
            return

        await chat.send_action(ChatAction.TYPING)
        try:
            whoop_service.sync_all(user["id"])
        except Exception:
            pass

        data = whoop_service.get_today_recovery(user["id"])
        trends = whoop_service.get_whoop_trends(user["id"], days=7)

        if not data:
            await chat.send_message("No WHOOP data available yet.")
            return

        recovery = data.get("recovery_score")
        zone = whoop_service.get_recovery_zone(recovery)
        zone_emoji = {"green": "\U0001f7e2", "yellow": "\U0001f7e1", "red": "\U0001f534"}.get(zone, "\u26aa")

        lines = [f"WHOOP Dashboard\n"]
        lines.append(f"{zone_emoji} Recovery: {recovery}% ({zone})")
        if data.get("hrv_rmssd") is not None:
            lines.append(f"HRV: {data['hrv_rmssd']}ms")
        if data.get("resting_hr") is not None:
            lines.append(f"Resting HR: {data['resting_hr']}bpm")
        if data.get("sleep_performance") is not None:
            sleep_str = f"Sleep: {data['sleep_performance']}%"
            parts = []
            if data.get("deep_sleep_minutes") is not None:
                parts.append(f"{data['deep_sleep_minutes']}min deep")
            if data.get("rem_sleep_minutes") is not None:
                parts.append(f"{data['rem_sleep_minutes']}min REM")
            if parts:
                sleep_str += f" ({', '.join(parts)})"
            lines.append(sleep_str)
        if data.get("daily_strain") is not None:
            lines.append(f"Strain: {data['daily_strain']}")

        if trends and trends.get("days", 0) > 2:
            lines.append("\n7-Day Trends:")
            if trends.get("recovery_avg") is not None:
                arrow = {"trending_up": "\u2191", "trending_down": "\u2193", "stable": "\u2192"}.get(
                    trends.get("recovery_trend", ""), "")
                lines.append(f"Recovery avg: {trends['recovery_avg']}% {arrow}")
            if trends.get("hrv_avg") is not None:
                arrow = {"trending_up": "\u2191", "trending_down": "\u2193", "stable": "\u2192"}.get(
                    trends.get("hrv_trend", ""), "")
                lines.append(f"HRV avg: {trends['hrv_avg']}ms {arrow}")
            if trends.get("sleep_avg") is not None:
                lines.append(f"Sleep avg: {trends['sleep_avg']}%")
            if trends.get("strain_avg") is not None:
                lines.append(f"Strain avg: {trends['strain_avg']}")

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("What should I train?", callback_data="whoop_train"),
                InlineKeyboardButton("Log workout", callback_data="whoop_log"),
            ],
        ])

        await typing_pause(chat, 1.0)
        await chat.send_message("\n".join(lines), reply_markup=keyboard)


async def handle_feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle feedback button callbacks (fb:pos / fb:neg)."""
    query = update.callback_query

    user = context.user_data.get("db_user")
    if not user:
        tg = update.effective_user
        user = user_service.get_or_create_user(tg.id, tg.username, tg.first_name)
        context.user_data["db_user"] = user

    feedback_type = "positive" if query.data == "fb:pos" else "negative"

    # Get the message text that was rated
    message_text = query.message.text[:500] if query.message.text else None

    try:
        from bot.services import memory_service
        memory_service.save_feedback(
            user_id=user["id"],
            feedback=feedback_type,
            message_text=message_text,
        )
    except Exception as e:
        logger.error(f"Failed to save feedback: {e}")

    # Acknowledge and remove buttons
    if feedback_type == "positive":
        await query.answer("\U0001f44d Thanks!")
    else:
        await query.answer("Got it — I'll do better.")

    # Remove the feedback buttons from the message
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle free-text messages — pass to AI brain."""
    import asyncio

    # Guard: don't pass to AI during onboarding flow
    if context.user_data.get("ob") is not None:
        otp_phase = context.user_data["ob"].get("otp_phase")
        if otp_phase in ("awaiting_phone", "awaiting_code"):
            from bot.handlers.onboarding import handle_otp_text
            await handle_otp_text(update, context)
            return
        await update.message.reply_text(
            "Tap one of the buttons above to finish setup \u2014 almost done!"
        )
        return

    user = await _get_user(update, context)
    if not user:
        return
    text = update.message.text
    if not text:
        return

    # Keep typing dots alive the entire time AI is processing
    chat = update.message.chat
    typing_active = True

    async def _typing_loop():
        while typing_active:
            try:
                await chat.send_action(ChatAction.TYPING)
            except Exception:
                pass
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(_typing_loop())

    try:
        async def _keep_typing():
            await chat.send_action(ChatAction.TYPING)

        tasks = task_service.get_tasks(user["id"])

        # Timeout safety: if brain takes >120s, return a fallback instead of hanging
        try:
            response = await asyncio.wait_for(
                ai_brain.process(text, user, tasks, typing_callback=_keep_typing),
                timeout=120.0,
            )
        except asyncio.TimeoutError:
            logger.error(f"Brain processing timed out for user {user['id']}")
            response = "That took too long — try again or break your question into something simpler."
    finally:
        typing_active = False
        typing_task.cancel()

    # Check for pending interactive workout session
    pending_session_id = ai_brain._pending_session.pop(user["id"], None)

    if response:
        # If paywall was hit, attach subscribe button
        if ai_brain._paywall_hit:
            from bot.handlers.payments import get_subscribe_keyboard
            keyboard = get_subscribe_keyboard(update.effective_user.id)
            await update.message.reply_text(response, reply_markup=keyboard)
        else:
            # Add feedback buttons on substantive responses (longer than a quick ack)
            show_feedback = len(response) > 80
            await _send_human(update, response, add_feedback=show_feedback)
    elif not pending_session_id:
        await update.message.reply_text("Something went wrong processing that. Try again or use a /command.")

    if pending_session_id:
        from bot.handlers.workout_session import send_current_exercise
        await send_current_exercise(update.message.chat, context, pending_session_id)


def _format_tasks(tasks: list) -> str:
    """Format task list for display."""
    today = date.today()
    lines = []
    for t in tasks:
        pri = "!" if t.get("priority") == "High" else ""
        cat = t.get("category", "Personal")
        due_str = ""
        if t.get("due_date"):
            d = t["due_date"]
            if d < today:
                due_str = f" - OVERDUE ({(today - d).days}d)"
            elif d == today:
                due_str = " - TODAY"
            elif (d - today).days == 1:
                due_str = " - tomorrow"
            elif (d - today).days <= 7:
                due_str = f" - {d.strftime('%A')}"
            else:
                due_str = f" - {d.strftime('%b %d')}"
        lines.append(f"{t['index']}. {pri}{t['title']} [{cat}]{due_str}")
    return "\n".join(lines)
