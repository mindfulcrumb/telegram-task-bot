"""Reminder handlers for Telegram bot."""
import re
from datetime import datetime, timedelta
from telegram import Update, Bot
from telegram.ext import ContextTypes, JobQueue
from bot.services.notion import notion_service
import config

# Store active chat IDs for sending reminders
_active_chat_ids = set()


def register_chat_id(chat_id: int):
    """Register a chat ID to receive reminder notifications."""
    _active_chat_ids.add(chat_id)


def is_authorized(user_id: int) -> bool:
    """Check if user is authorized to use the bot."""
    if not config.ALLOWED_USER_IDS:
        return True
    return user_id in config.ALLOWED_USER_IDS


async def send_reminder_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback that fires when a scheduled reminder is due."""
    job = context.job
    task_data = job.data

    # Build the reminder message
    title = task_data.get("title", "Task")
    priority = task_data.get("priority", "Medium")

    priority_icon = "🔴 " if priority == "High" else ""
    message = f"⏰ **REMINDER**\n\n{priority_icon}📋 {title}"

    if task_data.get("due_date"):
        message += f"\n📅 Due: {task_data['due_date']}"

    message += "\n\n_Say 'done' to mark complete_"

    try:
        await context.bot.send_message(
            chat_id=job.chat_id,
            text=message,
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Error sending scheduled reminder: {e}")


def schedule_reminder(job_queue: JobQueue, chat_id: int, reminder_time: datetime, task_data: dict) -> None:
    """
    Schedule a one-time reminder using job_queue.run_once().

    This fires the reminder at the EXACT specified time instead of polling.

    Args:
        job_queue: The telegram bot's job queue
        chat_id: Chat ID to send the reminder to
        reminder_time: Exact datetime when reminder should fire
        task_data: Dict with 'title', 'priority', 'due_date' etc.
    """
    job_queue.run_once(
        send_reminder_callback,
        when=reminder_time,
        chat_id=chat_id,
        data=task_data,
        name=f"reminder_{chat_id}_{reminder_time.timestamp()}"
    )


def parse_reminder_time(time_str: str) -> timedelta:
    """
    Parse a reminder time string into a timedelta.

    Supports:
    - "30m", "30min", "30 minutes"
    - "2h", "2hr", "2 hours"
    - "1d", "1 day"
    """
    time_str = time_str.lower().strip()

    # Minutes
    match = re.match(r"(\d+)\s*(m|min|mins|minutes?)$", time_str)
    if match:
        return timedelta(minutes=int(match.group(1)))

    # Hours
    match = re.match(r"(\d+)\s*(h|hr|hrs|hours?)$", time_str)
    if match:
        return timedelta(hours=int(match.group(1)))

    # Days
    match = re.match(r"(\d+)\s*(d|days?)$", time_str)
    if match:
        return timedelta(days=int(match.group(1)))

    raise ValueError(f"Could not parse time: {time_str}")


async def cmd_remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /remind command - set a reminder for a task."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Sorry, you're not authorized to use this bot.")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /remind <task number> <time>\n\n"
            "Examples:\n"
            "  /remind 1 30m - Remind in 30 minutes\n"
            "  /remind 2 2h - Remind in 2 hours\n"
            "  /remind 3 1d - Remind in 1 day\n\n"
            "Use /list to see task numbers."
        )
        return

    try:
        task_num = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Please provide a valid task number.")
        return

    try:
        time_delta = parse_reminder_time(context.args[1])
    except ValueError as e:
        await update.message.reply_text(
            f"Invalid time format. Use formats like:\n"
            "  30m (30 minutes)\n"
            "  2h (2 hours)\n"
            "  1d (1 day)"
        )
        return

    try:
        # Get current tasks to find the one to set reminder for
        tasks = notion_service.get_tasks()

        if task_num < 1 or task_num > len(tasks):
            await update.message.reply_text(f"Invalid task number. Use /list to see available tasks (1-{len(tasks)}).")
            return

        task = tasks[task_num - 1]
        reminder_time = datetime.now() + time_delta

        notion_service.set_reminder(task["id"], reminder_time)

        # Format the reminder time for display
        if time_delta.days > 0:
            time_str = f"{time_delta.days} day(s)"
        elif time_delta.seconds >= 3600:
            time_str = f"{time_delta.seconds // 3600} hour(s)"
        else:
            time_str = f"{time_delta.seconds // 60} minute(s)"

        await update.message.reply_text(
            f'Reminder set for "{task["title"]}" in {time_str}\n'
            f'   ({reminder_time.strftime("%I:%M %p")})'
        )

    except Exception as e:
        await update.message.reply_text(f"Error setting reminder: {str(e)}")


async def check_reminders(bot: Bot, chat_ids: set = None):
    """Check for due reminders and send notifications."""
    try:
        tasks = notion_service.get_tasks_with_reminders()

        if not tasks:
            return

        # Use provided chat_ids or fall back to registered ones
        target_chats = chat_ids or _active_chat_ids

        if not target_chats:
            print("No chat IDs registered for reminders")
            return

        for task in tasks:
            # Build reminder notification message
            priority_icon = "🔴 " if task["priority"] == "High" else ""
            message = f"⏰ **REMINDER**\n\n{priority_icon}📋 {task['title']}"

            if task["due_date"]:
                message += f"\n📅 Due: {task['due_date']}"

            message += "\n\n_Reply 'done' to mark complete_"

            # Send to all registered chats
            for chat_id in target_chats:
                try:
                    await bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
                except Exception as e:
                    print(f"Error sending reminder to {chat_id}: {e}")

            # Clear the reminder so it doesn't fire again
            notion_service.clear_reminder(task["id"])

    except Exception as e:
        print(f"Error checking reminders: {e}")


def setup_reminder_job(application, chat_id: int = None):
    """Set up the recurring reminder check job."""
    job_queue = application.job_queue

    # Register the provided chat_id if given
    if chat_id:
        register_chat_id(chat_id)

    async def reminder_callback(context: ContextTypes.DEFAULT_TYPE):
        await check_reminders(context.bot)

    # Run every 1 minute for responsive reminders (minimum interval)
    interval = max(60, config.REMINDER_CHECK_INTERVAL * 60)  # At least every minute
    job_queue.run_repeating(reminder_callback, interval=60, first=10)  # Check every minute
