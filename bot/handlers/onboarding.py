"""User onboarding — /start, /help, /settings, /account, /deleteaccount."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot.services import user_service

logger = logging.getLogger(__name__)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start — create account or welcome back."""
    tg_user = update.effective_user
    user = user_service.get_or_create_user(
        telegram_user_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name,
    )
    context.user_data["db_user"] = user

    # Check if this is a brand new user (created just now)
    is_new = user.get("last_active") is None or user["created_at"] == user["last_active"]

    if is_new:
        await update.message.reply_text(
            f"Hey {tg_user.first_name}! I'm your personal task assistant.\n\n"
            "Just tell me what you need to do and I'll keep track of it. "
            "You can talk to me naturally — no special commands needed.\n\n"
            "Try saying:\n"
            '• "add buy groceries tomorrow"\n'
            '• "what\'s due this week?"\n'
            '• "done 1" to complete a task\n\n'
            "Type /help anytime to see all commands."
        )
    else:
        from bot.services import task_service
        tasks = task_service.get_tasks(user["id"])
        count = len(tasks)
        overdue = sum(1 for t in tasks if t.get("due_date") and t["due_date"].isoformat() < __import__("datetime").date.today().isoformat())

        status = f"You have {count} active task{'s' if count != 1 else ''}"
        if overdue:
            status += f" ({overdue} overdue!)"
        status += "."

        await update.message.reply_text(f"Welcome back, {tg_user.first_name}! {status}")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show available commands."""
    await update.message.reply_text(
        "Here's what I can do:\n\n"
        "Just chat with me naturally, or use commands:\n\n"
        "/add <task> — Add a task\n"
        "/list — Show all tasks\n"
        "/today — Today's tasks\n"
        "/week — This week's tasks\n"
        "/overdue — Overdue tasks\n"
        "/done <number> — Complete a task\n"
        "/delete <number> — Delete a task\n"
        "/edit <number> <new title> — Edit a task\n"
        "/remind <number> <time> — Set a reminder\n"
        "/undo — Undo last action\n"
        "/clear — Clear chat history\n"
        "/settings — Your preferences\n"
        "/account — Subscription info\n"
        "/upgrade — Get Pro features\n"
        "/deleteaccount — Delete all your data\n"
        "/help — This message"
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show and manage user settings."""
    user = await _ensure_user(update, context)
    if not user:
        return

    await update.message.reply_text(
        f"Your settings:\n\n"
        f"Timezone: {user.get('timezone', 'UTC')}\n"
        f"Daily briefing: {user.get('briefing_hour', 8)}:00\n"
        f"Tier: {user.get('tier', 'free').title()}\n\n"
        "To change timezone: /settings timezone Europe/Lisbon\n"
        "To change briefing hour: /settings briefing 9"
    )

    # Handle setting changes
    args = context.args
    if args and len(args) >= 2:
        if args[0] == "timezone":
            user_service.update_settings(user["id"], timezone=args[1])
            await update.message.reply_text(f"Timezone updated to {args[1]}")
        elif args[0] == "briefing":
            try:
                hour = int(args[1])
                if 0 <= hour <= 23:
                    user_service.update_settings(user["id"], briefing_hour=hour)
                    await update.message.reply_text(f"Daily briefing set to {hour}:00")
            except ValueError:
                pass


async def cmd_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show account and subscription info."""
    user = await _ensure_user(update, context)
    if not user:
        return

    from bot.services import task_service, tier_service
    task_count = task_service.count_active_tasks(user["id"])
    ai_used = tier_service.get_usage_today(user["id"], "ai_message")
    tier = user.get("tier", "free")
    limits = tier_service.LIMITS.get(tier, tier_service.LIMITS["free"])

    task_limit = limits["max_tasks"]
    ai_limit = limits["max_ai_messages_per_day"]

    task_str = f"{task_count}/{task_limit}" if task_limit else f"{task_count} (unlimited)"
    ai_str = f"{ai_used}/{ai_limit}" if ai_limit else f"{ai_used} (unlimited)"

    text = (
        f"Account: {user.get('first_name', 'User')}\n"
        f"Tier: {tier.title()}\n"
        f"Active tasks: {task_str}\n"
        f"AI messages today: {ai_str}\n"
        f"Member since: {user['created_at'].strftime('%b %d, %Y')}"
    )

    if tier == "free":
        text += "\n\nWant unlimited tasks and AI? /upgrade"

    await update.message.reply_text(text)


async def cmd_delete_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete user account and all data (GDPR)."""
    user = await _ensure_user(update, context)
    if not user:
        return

    # Require confirmation
    if context.user_data.get("confirm_delete"):
        user_service.delete_user(user["id"])
        context.user_data.clear()
        await update.message.reply_text(
            "Your account and all data have been permanently deleted. "
            "If you ever want to come back, just /start again."
        )
    else:
        context.user_data["confirm_delete"] = True
        await update.message.reply_text(
            "This will permanently delete your account, all tasks, "
            "conversation history, and usage data.\n\n"
            "Send /deleteaccount again to confirm."
        )


async def _ensure_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    """Get the DB user from context or create one. Returns user dict."""
    user = context.user_data.get("db_user")
    if user:
        return user

    tg_user = update.effective_user
    user = user_service.get_or_create_user(
        telegram_user_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name,
    )
    context.user_data["db_user"] = user
    return user
