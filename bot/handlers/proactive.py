"""Proactive notifications - daily briefing and smart nudges."""
import logging
from datetime import datetime, date, time
from telegram.ext import ContextTypes
from bot.services.notion import notion_service
from bot.ai.brain import ai_brain, call_anthropic_chat, to_ascii
import config

logger = logging.getLogger(__name__)


def _days_overdue(task):
    """Get number of days a task is overdue. Returns 0 if not overdue."""
    due = task.get("due_date_iso")
    if not due:
        return 0
    try:
        today = date.today()
        due_date = datetime.fromisoformat(due).date() if isinstance(due, str) else due
        diff = (today - due_date).days
        return diff if diff > 0 else 0
    except Exception:
        return 0


def _is_due_today(task):
    """Check if task is due today."""
    due = task.get("due_date_iso")
    if not due:
        return False
    try:
        today_str = date.today().isoformat()
        return due[:10] == today_str
    except Exception:
        return False


async def send_daily_briefing(context: ContextTypes.DEFAULT_TYPE):
    """Send morning briefing with task summary and email status."""
    target_chats = set(config.ALLOWED_USER_IDS or [])
    if not target_chats:
        logger.warning("No chat IDs for daily briefing")
        return

    try:
        tasks = notion_service.get_tasks()
        stats = ai_brain._analyze_tasks(tasks)

        # Check for new emails
        unread_count = 0
        try:
            from bot.services.email_inbox import email_inbox
            if email_inbox.is_configured():
                new_emails = email_inbox.get_new_messages()
                unread_count = len(new_emails)
        except Exception as e:
            logger.warning(f"Could not check emails for briefing: {e}")

        # Build briefing message
        now = datetime.now()
        greeting = "Good morning" if now.hour < 12 else "Good afternoon"
        day_name = now.strftime("%A, %B %d")

        lines = [f"\u2600\ufe0f **{greeting}!** Here's your {day_name} briefing:\n"]

        # Task overview
        if stats["total"] == 0:
            lines.append("\U0001f4cb No active tasks \u2014 fresh slate!")
        else:
            lines.append(f"\U0001f4cb **{stats['total']} active task(s)**")
            if stats["overdue"] > 0:
                lines.append(f"\U0001f534 {stats['overdue']} overdue!")
            if stats["today"] > 0:
                lines.append(f"\U0001f4c5 {stats['today']} due today")
            if stats["high_priority"] > 0:
                lines.append(f"\u26a1 {stats['high_priority']} high priority")

        # Today's tasks
        today_tasks = [t for t in tasks if _is_due_today(t)]
        if today_tasks:
            lines.append("\n\U0001f3af **Today's focus:**")
            for t in today_tasks[:5]:
                pri = "\U0001f534 " if t.get("priority") == "High" else ""
                lines.append(f"  \u2022 {pri}{t['title']}")

        # Overdue tasks
        overdue_tasks = [t for t in tasks if _days_overdue(t) > 0]
        if overdue_tasks:
            lines.append(f"\n\u23f0 **Overdue ({len(overdue_tasks)}):**")
            for t in overdue_tasks[:3]:
                days = _days_overdue(t)
                lines.append(f"  \u2022 {t['title']} ({days}d overdue)")
            if len(overdue_tasks) > 3:
                lines.append(f"  ... and {len(overdue_tasks) - 3} more")

        # Email status
        if unread_count > 0:
            lines.append(f"\n\U0001f4e7 {unread_count} new email(s) \u2014 say 'check my email' to read")

        # AI focus suggestion
        if stats["total"] > 0 and config.ANTHROPIC_API_KEY:
            try:
                suggestion = await _get_ai_focus_suggestion(tasks)
                if suggestion:
                    lines.append(f"\n\U0001f4a1 {suggestion}")
            except Exception:
                pass

        lines.append("\n_What would you like to tackle first?_")

        message = "\n".join(lines)

        for chat_id in target_chats:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to send briefing to {chat_id}: {e}")

        logger.info(f"Daily briefing sent to {len(target_chats)} chat(s)")

    except Exception as e:
        logger.error(f"Failed to generate daily briefing: {type(e).__name__}: {e}")


async def check_nudges(context: ContextTypes.DEFAULT_TYPE):
    """Check for tasks that need proactive nudges."""
    target_chats = set(config.ALLOWED_USER_IDS or [])
    if not target_chats:
        return

    try:
        tasks = notion_service.get_tasks()
        if not tasks:
            return

        nudges = []

        # Tasks overdue by 3+ days
        for t in tasks:
            days = _days_overdue(t)
            if days >= 3:
                nudges.append(f"\U0001f534 \"{t['title']}\" is {days} days overdue")

        # High-priority tasks with no due date
        for t in tasks:
            if t.get("priority") == "High" and not t.get("due_date_iso"):
                nudges.append(f"\u26a1 \"{t['title']}\" is high priority but has no due date")

        if not nudges:
            return

        # Cap at 3 nudges to avoid spam
        nudges = nudges[:3]

        message = "\U0001f916 **Quick nudge:**\n\n" + "\n".join(nudges)
        message += "\n\n_Need help with any of these?_"

        for chat_id in target_chats:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to send nudge to {chat_id}: {e}")

        logger.info(f"Sent {len(nudges)} nudge(s)")

    except Exception as e:
        logger.error(f"Failed to check nudges: {type(e).__name__}: {e}")


async def _get_ai_focus_suggestion(tasks):
    """Get a short AI suggestion for what to focus on."""
    task_lines = []
    for i, t in enumerate(tasks[:8], 1):
        title = to_ascii(t.get("title", "Task"))
        pri = t.get("priority", "Medium")
        due = t.get("due_date", "no date")
        task_lines.append(f"{i}. {title} [{pri}] due: {due}")

    prompt = (
        f"Given these tasks:\n" + "\n".join(task_lines) +
        "\n\nIn ONE sentence (max 15 words), suggest what to focus on first and why. "
        "Be casual, like texting a friend."
    )

    result, error = call_anthropic_chat("", [{"role": "user", "content": prompt}], max_tokens=50)
    return result if result else None


def setup_proactive_jobs(application):
    """Set up daily briefing and smart nudge scheduled jobs."""
    job_queue = application.job_queue

    # Parse timezone
    tz = None
    if config.TIMEZONE:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(config.TIMEZONE)
        except Exception as e:
            logger.warning(f"Invalid timezone '{config.TIMEZONE}': {e}, using server time")

    # Daily briefing
    briefing_time = time(
        hour=config.BRIEFING_HOUR,
        minute=config.BRIEFING_MINUTE,
        tzinfo=tz
    )

    job_queue.run_daily(
        send_daily_briefing,
        time=briefing_time,
        name="daily_briefing"
    )
    tz_label = config.TIMEZONE or "server time"
    logger.info(f"Daily briefing scheduled at {briefing_time.strftime('%H:%M')} ({tz_label})")

    # Smart nudges - check every N hours
    nudge_seconds = config.NUDGE_INTERVAL_HOURS * 3600

    job_queue.run_repeating(
        check_nudges,
        interval=nudge_seconds,
        first=300,  # First check 5 minutes after boot
        name="smart_nudges"
    )
    logger.info(f"Smart nudges scheduled every {config.NUDGE_INTERVAL_HOURS}h")
