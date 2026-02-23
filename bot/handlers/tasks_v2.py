"""Task command handlers — user-scoped, PostgreSQL-backed."""
import logging
from datetime import datetime, date
from telegram import Update
from telegram.ext import ContextTypes

from bot.services import user_service, task_service, tier_service
from bot.ai.brain_v2 import ai_brain

logger = logging.getLogger(__name__)


async def _get_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Get or create user from Telegram update."""
    user = context.user_data.get("db_user")
    if user:
        return user
    tg = update.effective_user
    user = user_service.get_or_create_user(tg.id, tg.username, tg.first_name)
    context.user_data["db_user"] = user
    return user


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add command."""
    user = await _get_user(update, context)
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("What's the task? e.g., /add buy groceries")
        return

    # Check tier limit
    allowed, msg = tier_service.check_limit(user["id"], "add_task", user.get("tier", "free"))
    if not allowed:
        await update.message.reply_text(msg)
        return

    task_service.add_task(user["id"], title=text)
    await update.message.reply_text(f"Added: {text}")


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list command."""
    user = await _get_user(update, context)
    tasks = task_service.get_tasks(user["id"])
    if not tasks:
        await update.message.reply_text("No tasks! Add one with /add or just tell me.")
        return
    await update.message.reply_text(_format_tasks(tasks))


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /today command."""
    user = await _get_user(update, context)
    tasks = task_service.get_tasks(user["id"], "today")
    if not tasks:
        await update.message.reply_text("Nothing due today!")
        return
    await update.message.reply_text(f"Due today:\n\n{_format_tasks(tasks)}")


async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /week command."""
    user = await _get_user(update, context)
    tasks = task_service.get_tasks(user["id"], "week")
    if not tasks:
        await update.message.reply_text("Nothing due this week!")
        return
    await update.message.reply_text(f"This week:\n\n{_format_tasks(tasks)}")


async def cmd_overdue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /overdue command."""
    user = await _get_user(update, context)
    tasks = task_service.get_tasks(user["id"], "overdue")
    if not tasks:
        await update.message.reply_text("Nothing overdue!")
        return
    await update.message.reply_text(f"Overdue:\n\n{_format_tasks(tasks)}")


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /done command."""
    user = await _get_user(update, context)
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
    if completed:
        titles = ", ".join(t["title"] for t in completed)
        parts.append(f"Done: {titles}")
    if not_found:
        parts.append(f"Not found: {', '.join(str(n) for n in not_found)}")
    await update.message.reply_text("\n".join(parts) or "Nothing to complete.")


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /delete command."""
    user = await _get_user(update, context)
    if not context.args:
        await update.message.reply_text("Which task? e.g., /delete 2")
        return

    try:
        nums = [int(a) for a in context.args]
    except ValueError:
        await update.message.reply_text("Use task numbers, e.g., /delete 2")
        return

    deleted, not_found = task_service.delete_tasks(user["id"], nums)
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
    if result:
        await update.message.reply_text(f"Updated: \"{result[0]}\" → \"{result[1]}\"")
    else:
        await update.message.reply_text(f"Task #{num} not found.")


async def cmd_undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /undo command."""
    user = await _get_user(update, context)
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
    from bot.ai import memory_pg
    memory_pg.clear_history(user["id"])
    await update.message.reply_text("Conversation history cleared.")


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick task analysis."""
    user = await _get_user(update, context)
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
    await update.message.reply_text(text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle free-text messages — pass to AI brain."""
    user = await _get_user(update, context)
    text = update.message.text
    if not text:
        return

    tasks = task_service.get_tasks(user["id"])
    response = await ai_brain.process(text, user, tasks)

    if response:
        # Split long messages (Telegram limit is 4096 chars)
        if len(response) <= 4096:
            await update.message.reply_text(response)
        else:
            for i in range(0, len(response), 4096):
                await update.message.reply_text(response[i:i + 4096])


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
