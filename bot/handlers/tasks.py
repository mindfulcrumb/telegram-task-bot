"""Task management handlers for Telegram bot."""
import re
import logging
from telegram import Update
from telegram.ext import ContextTypes
from bot.services.notion import notion_service
from bot.services.classifier import parse_task_input
import config

logger = logging.getLogger(__name__)


def is_authorized(user_id: int) -> bool:
    """Check if user is authorized to use the bot."""
    if not config.ALLOWED_USER_IDS:
        return True  # No restrictions if not configured
    return user_id in config.ALLOWED_USER_IDS


def _parse_numbers(text: str) -> list:
    """Extract all numbers from text like '1 3 5', '1, 3, and 5', '#1 #3 #5'."""
    nums = [int(n) for n in re.findall(r'\d+', text)]
    return nums


def detect_intent(text: str) -> dict:
    """Detect the user's intent from natural language."""
    text_lower = text.lower().strip()

    # Delete/Remove/Cancel patterns (supports multiple numbers: "delete 1 3 5", "delete 1, 3 and 5")
    delete_match = re.match(r"^(delete|remove|cancel|trash)\s*(tasks?\s*)?(.+)$", text_lower)
    if delete_match:
        nums = _parse_numbers(delete_match.group(3))
        if nums:
            return {"action": "delete", "task_nums": nums}

    # Done/Complete/Finish patterns (supports multiple: "done 1 3 5")
    done_match = re.match(r"^(done|complete|completed|finish|finished)\s*(tasks?\s*)?(.+)$", text_lower)
    if done_match:
        nums = _parse_numbers(done_match.group(3))
        if nums:
            return {"action": "done", "task_nums": nums}

    mark_done = re.match(r"^mark\s+(.+?)\s+(as\s+)?(done|complete|finished)$", text_lower)
    if mark_done:
        nums = _parse_numbers(mark_done.group(1))
        if nums:
            return {"action": "done", "task_nums": nums}

    # Explicit add/create commands - these ARE tasks
    if re.match(r"^(add|create|new|make|set|schedule)\s+", text_lower):
        return {"action": "add_task"}

    # "remind me" patterns with task content - these ARE tasks
    if re.search(r"remind\s*(me)?\s*(to|about)?\s+\w+", text_lower):
        return {"action": "add_task"}

    # Question patterns - these should NOT be saved as tasks
    question_patterns = [
        r"^(what|which|how|do i|should i|can you|could you|would you|will you)",
        r"\?$",  # Ends with question mark
        r"^(read|show|tell|give|display|see|view|check)",
        r"(my tasks|my to.?do|to.?do list|task list|pending|have to do)",
        r"(anything|something|what).*(do|pending|left|remaining)",
        r"^(any|are there|is there|got any)",
    ]

    for pattern in question_patterns:
        if re.search(pattern, text_lower):
            # It's a question about tasks - determine what kind
            if any(word in text_lower for word in ["today", "due today", "for today"]):
                return {"action": "today"}
            if any(word in text_lower for word in ["personal", "home", "private"]):
                return {"action": "list", "category": "Personal"}
            if any(word in text_lower for word in ["business", "work", "office", "job"]):
                return {"action": "list", "category": "Business"}
            if any(word in text_lower for word in ["help", "how to", "how do", "commands"]):
                return {"action": "help"}
            # Default: show all tasks
            return {"action": "list", "category": None}

    # Explicit list patterns
    list_keywords = ["list", "show", "tasks", "my tasks", "all tasks", "pending", "to-do", "todo", "to do"]
    if any(kw in text_lower for kw in list_keywords):
        if "personal" in text_lower:
            return {"action": "list", "category": "Personal"}
        if any(w in text_lower for w in ["business", "work"]):
            return {"action": "list", "category": "Business"}
        return {"action": "list", "category": None}

    # Today patterns
    today_keywords = ["today", "today's", "todays", "due today", "for today"]
    if any(kw in text_lower for kw in today_keywords) and not any(w in text_lower for w in ["add", "create", "new", "reminder"]):
        return {"action": "today"}

    # Help patterns
    if any(kw in text_lower for kw in ["help", "commands", "how do i", "how to use"]):
        return {"action": "help"}

    # Greetings and acknowledgments (don't add as task)
    greetings = ["hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "great",
                 "cool", "nice", "yes", "no", "sure", "alright", "got it", "noted"]
    if text_lower in greetings or text_lower in ["👍", "👌", "🙏", "✓", "✔"]:
        return {"action": "greeting"}

    # Short responses that are likely not tasks
    if len(text_lower) < 4 and not any(c.isdigit() for c in text_lower):
        return {"action": "greeting"}

    # Only auto-add as task if it has STRONG task signals
    # Must have: (actionable verb) AND (time reference OR hashtag/priority)

    action_verbs = r"\b(buy|email|submit|pay|book|schedule|pick up|drop off|prepare|clean|fix|order|renew|return)\b"
    time_refs = r"\b(tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|next week|this week|end of|at \d|by \d|in \d\s*(day|hour|minute|week)|morning|afternoon|evening|tonight)\b"
    task_tags = r"(#(business|personal|work|home)|!(high|low|urgent|medium))"

    has_action = re.search(action_verbs, text_lower)
    has_time = re.search(time_refs, text_lower)
    has_tag = re.search(task_tags, text_lower)

    # Only auto-add if we have strong signals
    if has_action and (has_time or has_tag):
        return {"action": "add_task"}

    # If just has tags, it's likely meant to be a task
    if has_tag:
        return {"action": "add_task"}

    # Everything else - ask for clarification (don't auto-add)
    return {"action": "unclear", "text": text}


async def handle_ai_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Process message with AI agent. Returns True if handled, False to fallback."""
    try:
        from bot.ai.brain import ai_brain
        from bot.handlers.accounting import get_session_context
        tasks = notion_service.get_tasks()
        acct_context = get_session_context(context)

        response_text = await ai_brain.process(
            user_input=text,
            chat_id=update.effective_chat.id,
            tasks=tasks,
            context=context,
            update=update,
            acct_context=acct_context,
        )

        if response_text is None:
            return False  # Fallback to rule-based

        await update.message.reply_text(response_text)
        return True

    except Exception as e:
        logger.error(f"AI agent failed: {type(e).__name__}: {e}")
        try:
            await update.message.reply_text("Something went wrong. Try again or use a /command.")
        except Exception:
            pass
        return False


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle plain text messages with smart intent detection."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Sorry, you're not authorized to use this bot.")
        return

    # Register this chat for reminder notifications (lazy import to avoid circular)
    from bot.handlers.reminders import register_chat_id
    register_chat_id(update.effective_chat.id)

    text = update.message.text.strip()
    if not text:
        return

    # Use AI mode if enabled
    if config.AI_MODE == "smart" and config.ANTHROPIC_API_KEY:
        handled = await handle_ai_message(update, context, text)
        if handled:
            return

    # Fallback to rule-based detection
    intent = detect_intent(text)

    if intent["action"] == "delete":
        await handle_delete(update, intent["task_nums"])

    elif intent["action"] == "done":
        await handle_done(update, intent["task_nums"])

    elif intent["action"] == "list":
        await handle_list(update, intent.get("category"))

    elif intent["action"] == "today":
        await handle_today(update)

    elif intent["action"] == "help":
        await cmd_help(update, context)

    elif intent["action"] == "greeting":
        await update.message.reply_text("Hey! Send me a task or say 'list' to see your tasks.")

    elif intent["action"] == "unclear":
        # Ask for clarification
        await update.message.reply_text(
            f"I'm not sure what you mean by \"{text}\"\n\n"
            "Did you want to:\n"
            "• Add this as a task? Say: add <your task>\n"
            "• See your tasks? Say: list\n"
            "• See today's tasks? Say: today\n"
            "• Get help? Say: help"
        )

    else:
        # Add as new task
        await add_new_task(update, context, text)


async def handle_delete(update: Update, task_nums: list):
    """Delete/remove one or more tasks."""
    from bot.ai.tools import _undo_buffer
    try:
        tasks = notion_service.get_tasks()
        deleted = []
        not_found = []
        undo_entries = []

        for num in sorted(set(task_nums), reverse=True):
            if num < 1 or num > len(tasks):
                not_found.append(num)
                continue
            task = tasks[num - 1]
            notion_service.delete_task(task["id"])
            deleted.append(task["title"])
            undo_entries.append({"action": "delete", "task_id": task["id"], "title": task["title"]})

        if undo_entries:
            _undo_buffer[update.effective_chat.id] = undo_entries

        parts = []
        if deleted:
            names = ", ".join(f'"{t}"' for t in reversed(deleted))
            parts.append(f'Deleted: {names}')
        if not_found:
            nums_str = ", ".join(f"#{n}" for n in not_found)
            parts.append(f'Not found: {nums_str}')

        msg = "\n".join(parts)
        if deleted:
            msg += "\n_Say /undo to recover_"
        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Delete failed: {type(e).__name__}: {e}")
        await update.message.reply_text("Error occurred")


async def handle_done(update: Update, task_nums: list):
    """Mark one or more tasks as done."""
    from bot.ai.tools import _undo_buffer
    try:
        tasks = notion_service.get_tasks()
        completed = []
        not_found = []
        undo_entries = []

        for num in sorted(set(task_nums), reverse=True):
            if num < 1 or num > len(tasks):
                not_found.append(num)
                continue
            task = tasks[num - 1]
            notion_service.mark_complete(task["id"])
            completed.append(task["title"])
            undo_entries.append({"action": "done", "task_id": task["id"], "title": task["title"]})

        # Save all to undo buffer
        if undo_entries:
            _undo_buffer[update.effective_chat.id] = undo_entries

        # Build response
        parts = []
        if completed:
            names = ", ".join(f'"{t}"' for t in reversed(completed))
            parts.append(f'Done: {names}')
        if not_found:
            nums_str = ", ".join(f"#{n}" for n in not_found)
            parts.append(f'Not found: {nums_str}')

        msg = "\n".join(parts)
        if completed:
            msg += "\n_Say /undo to recover_"
        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Done failed: {type(e).__name__}: {e}")
        await update.message.reply_text("Error occurred")


async def cmd_undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Undo the last delete or done action."""
    from bot.ai.tools import _undo_buffer

    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Sorry, you're not authorized to use this bot.")
        return

    chat_id = update.effective_chat.id
    entries = _undo_buffer.pop(chat_id, None)

    if not entries:
        await update.message.reply_text("Nothing to undo.")
        return

    restored = []
    failed = []
    for entry in entries:
        try:
            notion_service.restore_task(entry["task_id"])
            action_word = "Undeleted" if entry["action"] == "delete" else "Unmarked"
            restored.append(f'{action_word}: "{entry["title"]}"')
        except Exception as e:
            logger.error(f"Undo failed for {entry['title']}: {type(e).__name__}: {e}")
            failed.append(entry["title"])

    parts = []
    if restored:
        parts.append("\u21a9\ufe0f " + "\n\u21a9\ufe0f ".join(restored))
    if failed:
        parts.append(f"Couldn't restore: {', '.join(failed)}")

    await update.message.reply_text("\n".join(parts) or "Couldn't undo that action.")


async def handle_list(update: Update, category: str = None):
    """Show tasks."""
    try:
        tasks = notion_service.get_tasks(category=category)

        if not tasks:
            msg = "No tasks" + (f" in {category}" if category else "") + ". Add one by sending a message!"
            await update.message.reply_text(msg)
            return

        header = f"{category} Tasks" if category else "Your Tasks"
        response = f"{header}:\n\n"

        for task in tasks:
            priority = "🔴 " if task["priority"] == "High" else ("⚪ " if task["priority"] == "Low" else "")
            cat = "💼" if task["category"] == "Business" else "🏠"
            due = f" 📅{task['due_date']}" if task["due_date"] else ""

            response += f"{task['index']}. {priority}{cat} {task['title']}{due}\n"

        response += "\n💡 Say 'done 1' or 'delete 2' to manage tasks"
        await update.message.reply_text(response)

    except Exception as e:
        await update.message.reply_text("Error occurred")


async def handle_today(update: Update):
    """Show today's tasks."""
    try:
        tasks = notion_service.get_tasks(due_today=True)

        if not tasks:
            await update.message.reply_text("Nothing due today! 🎉")
            return

        response = "📅 Today's Tasks:\n\n"
        for task in tasks:
            cat = "💼" if task["category"] == "Business" else "🏠"
            response += f"{task['index']}. {cat} {task['title']}\n"

        await update.message.reply_text(response)

    except Exception as e:
        await update.message.reply_text("Error occurred")


async def add_new_task(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Add a new task from natural text."""
    parsed = parse_task_input(text)

    if not parsed["title"]:
        await update.message.reply_text("I didn't understand that. Try something like 'Buy milk tomorrow'")
        return

    try:
        notion_service.add_task(
            title=parsed["title"],
            category=parsed["category"],
            due_date=parsed["due_date"],
            priority=parsed["priority"],
            reminder_time=parsed.get("reminder_time")
        )

        # Schedule reminder using job_queue.run_once() for EXACT timing
        if parsed.get("reminder_time"):
            from bot.handlers.reminders import schedule_reminder
            schedule_reminder(
                job_queue=context.job_queue,
                chat_id=update.effective_chat.id,
                reminder_time=parsed["reminder_time"],
                task_data={
                    "title": parsed["title"],
                    "priority": parsed["priority"],
                    "due_date": parsed["due_date"].strftime('%b %d') if parsed["due_date"] else None
                }
            )

        # Clean response
        cat_emoji = "💼" if parsed["category"] == "Business" else "🏠"
        response = f"✅ {cat_emoji} {parsed['title']}"

        if parsed["due_date"]:
            response += f" 📅 {parsed['due_date'].strftime('%b %d')}"

        if parsed.get("reminder_time"):
            response += f" ⏰ {parsed['reminder_time'].strftime('%I:%M %p')}"

        if parsed["priority"] == "High":
            response = "🔴 " + response
        elif parsed["priority"] == "Low":
            response = "⚪ " + response

        await update.message.reply_text(response)

    except Exception as e:
        await update.message.reply_text("Error occurred")


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add command - explicit task creation."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Sorry, you're not authorized to use this bot.")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /add <task description>\n\n"
            "Examples:\n"
            "  /add Buy groceries tomorrow\n"
            "  /add Call client #business !high\n"
            "  /add Review report next monday"
        )
        return

    text = " ".join(context.args)
    parsed = parse_task_input(text)

    if not parsed["title"]:
        await update.message.reply_text("Please provide a task description.")
        return

    try:
        notion_service.add_task(
            title=parsed["title"],
            category=parsed["category"],
            due_date=parsed["due_date"],
            priority=parsed["priority"],
            reminder_time=parsed.get("reminder_time")
        )

        # Schedule reminder using job_queue.run_once() for EXACT timing
        if parsed.get("reminder_time"):
            from bot.handlers.reminders import schedule_reminder
            schedule_reminder(
                job_queue=context.job_queue,
                chat_id=update.effective_chat.id,
                reminder_time=parsed["reminder_time"],
                task_data={
                    "title": parsed["title"],
                    "priority": parsed["priority"],
                    "due_date": parsed["due_date"].strftime('%b %d') if parsed["due_date"] else None
                }
            )

        response = f"Task added to {parsed['category']}\n"
        response += f"   {parsed['title']}"

        if parsed["due_date"]:
            response += f"\n   Due: {parsed['due_date'].strftime('%b %d, %Y')}"

        if parsed.get("reminder_time"):
            response += f"\n   Reminder: {parsed['reminder_time'].strftime('%I:%M %p')}"

        if parsed["priority"] != "Medium":
            response += f"\n   Priority: {parsed['priority']}"

        await update.message.reply_text(response)

    except Exception as e:
        await update.message.reply_text("Error adding task")


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list command - show tasks."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Sorry, you're not authorized to use this bot.")
        return

    category = None
    if context.args:
        arg = context.args[0].lower()
        if arg in ["personal", "p"]:
            category = "Personal"
        elif arg in ["business", "b", "work"]:
            category = "Business"

    try:
        tasks = notion_service.get_tasks(category=category)

        if not tasks:
            msg = "No pending tasks"
            if category:
                msg += f" in {category}"
            await update.message.reply_text(msg + ".")
            return

        # Build task list
        header = f"{category} Tasks" if category else "All Tasks"
        response = f"{header}:\n\n"

        for task in tasks:
            # Status indicator
            status_icon = "[ ]" if task["status"] == "To Do" else "[x]"

            # Priority indicator
            priority_icon = ""
            if task["priority"] == "High":
                priority_icon = " !"
            elif task["priority"] == "Low":
                priority_icon = " ~"

            # Due date
            due_str = ""
            if task["due_date"]:
                due_str = f" (Due: {task['due_date']})"

            # Category indicator (only if showing all)
            cat_icon = ""
            if not category:
                cat_icon = " [B]" if task["category"] == "Business" else " [P]"

            response += f"{task['index']}. {status_icon} {task['title']}{priority_icon}{due_str}{cat_icon}\n"

        await update.message.reply_text(response)

    except Exception as e:
        await update.message.reply_text("Error fetching tasks")


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /today command - show today's tasks."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Sorry, you're not authorized to use this bot.")
        return

    try:
        tasks = notion_service.get_tasks(due_today=True)

        if not tasks:
            await update.message.reply_text("No tasks due today!")
            return

        response = "Today's Tasks:\n\n"

        for task in tasks:
            priority_icon = ""
            if task["priority"] == "High":
                priority_icon = " !"
            elif task["priority"] == "Low":
                priority_icon = " ~"

            cat_icon = " [B]" if task["category"] == "Business" else " [P]"

            response += f"{task['index']}. [ ] {task['title']}{priority_icon}{cat_icon}\n"

        await update.message.reply_text(response)

    except Exception as e:
        await update.message.reply_text("Error fetching tasks")


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /done command - mark task(s) as complete. Supports multiple: /done 1 3 5"""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Sorry, you're not authorized to use this bot.")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /done <task numbers>\n\n"
            "Examples:\n"
            "  /done 1\n"
            "  /done 1 3 5\n\n"
            "Use /list to see task numbers."
        )
        return

    nums = _parse_numbers(" ".join(context.args))
    if not nums:
        await update.message.reply_text("Please provide valid task number(s).")
        return

    await handle_done(update, nums)


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /delete command - remove task(s). Supports multiple: /delete 1 3 5"""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Sorry, you're not authorized to use this bot.")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /delete <task numbers>\n\n"
            "Examples:\n"
            "  /delete 1\n"
            "  /delete 1 3 5\n\n"
            "Use /list to see task numbers."
        )
        return

    nums = _parse_numbers(" ".join(context.args))
    if not nums:
        await update.message.reply_text("Please provide valid task number(s).")
        return

    await handle_delete(update, nums)


async def cmd_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /edit command - edit a task's title."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Sorry, you're not authorized to use this bot.")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /edit <task number> <new title>\n\n"
            "Example: /edit 1 Buy groceries and milk\n\n"
            "Use /list to see task numbers."
        )
        return

    try:
        task_num = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Please provide a valid task number.")
        return

    new_title = " ".join(context.args[1:])

    try:
        tasks = notion_service.get_tasks()

        if task_num < 1 or task_num > len(tasks):
            await update.message.reply_text(f"Invalid task number. Use /list to see available tasks (1-{len(tasks)}).")
            return

        task = tasks[task_num - 1]
        notion_service.update_task_title(task["id"], new_title)

        await update.message.reply_text(f'✏️ Updated: "{task["title"]}" → "{new_title}"')

    except Exception as e:
        await update.message.reply_text("Error editing task")


async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /week command - show this week's tasks."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Sorry, you're not authorized to use this bot.")
        return

    try:
        tasks = notion_service.get_tasks(due_this_week=True)

        if not tasks:
            await update.message.reply_text("No tasks due this week! 🎉")
            return

        response = "📅 This Week's Tasks:\n\n"

        for task in tasks:
            priority_icon = ""
            if task["priority"] == "High":
                priority_icon = "🔴 "
            elif task["priority"] == "Low":
                priority_icon = "⚪ "

            cat_icon = "💼" if task["category"] == "Business" else "🏠"
            due = f" ({task['due_date']})" if task["due_date"] else ""

            response += f"{task['index']}. {priority_icon}{cat_icon} {task['title']}{due}\n"

        await update.message.reply_text(response)

    except Exception:
        await update.message.reply_text("Error fetching tasks")


async def cmd_overdue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /overdue command - show overdue tasks."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Sorry, you're not authorized to use this bot.")
        return

    try:
        tasks = notion_service.get_tasks(overdue=True)

        if not tasks:
            await update.message.reply_text("No overdue tasks! You're all caught up! ✨")
            return

        response = "⚠️ Overdue Tasks:\n\n"

        for task in tasks:
            priority_icon = ""
            if task["priority"] == "High":
                priority_icon = "🔴 "
            elif task["priority"] == "Low":
                priority_icon = "⚪ "

            cat_icon = "💼" if task["category"] == "Business" else "🏠"
            due = f" (was due: {task['due_date']})" if task["due_date"] else ""

            response += f"{task['index']}. {priority_icon}{cat_icon} {task['title']}{due}\n"

        response += "\n💡 Use /done <number> to complete or /delete <number> to remove"
        await update.message.reply_text(response)

    except Exception:
        await update.message.reply_text("Error fetching tasks")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    help_text = """*Task Bot Commands*

*Adding Tasks:*
Just send any message to create a task!

Or use /add <task description>

*Smart Features:*
- Dates: "tomorrow", "next monday", "in 3 days"
- Categories: #personal or #business (auto-detected)
- Priority: !high or !low

*Examples:*
- Buy groceries tomorrow
- Call client next monday #business !high
- Review report in 3 days

*Commands:*
/add - Add a new task
/list - Show all pending tasks
/list personal - Show personal tasks
/list business - Show business tasks
/today - Show today's tasks
/week - Show this week's tasks
/overdue - Show overdue tasks
/done <number> - Mark task as complete
/delete <number> - Delete a task
/edit <number> <new title> - Edit task
/remind <number> <time> - Set reminder
/help - Show this help

*Priority Icons:*
! = High priority
~ = Low priority

*Category Icons:*
[P] = Personal
[B] = Business"""

    await update.message.reply_text(help_text, parse_mode="Markdown")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    welcome = """Welcome to Task Bot!

I help you manage your tasks via Telegram. All tasks are saved to your Notion database.

*Quick Start:*
Just send me a message like:
"Buy groceries tomorrow"

I'll create a task, auto-detect the category, and set the due date.

Type /help for all commands."""

    await update.message.reply_text(welcome, parse_mode="Markdown")


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /analyze command - AI analysis of tasks."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Sorry, you're not authorized to use this bot.")
        return

    if not config.ANTHROPIC_API_KEY:
        await update.message.reply_text("AI features require ANTHROPIC_API_KEY to be set.")
        return

    await update.message.reply_text("Analyzing your tasks...")

    try:
        from bot.ai.brain import ai_brain, to_ascii
        tasks = notion_service.get_tasks()

        if not tasks:
            await update.message.reply_text("No tasks to analyze. Add some tasks first!")
            return

        summary = await ai_brain.weekly_summary(tasks)
        # Force ASCII to prevent any encoding issues
        safe_summary = to_ascii(summary) if summary else "Analysis unavailable"
        await update.message.reply_text("TASK ANALYSIS\n\n" + safe_summary)

    except Exception as e:
        # Safe error message
        try:
            error_type = to_ascii(type(e).__name__) or "Unknown"
        except Exception:
            error_type = "Unknown"
        await update.message.reply_text("Analysis error: " + error_type)
