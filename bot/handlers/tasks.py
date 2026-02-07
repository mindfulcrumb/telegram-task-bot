"""Task management handlers for Telegram bot."""
import re
import logging
from telegram import Update
from telegram.ext import ContextTypes
from bot.services.notion import notion_service
from bot.services.classifier import parse_task_input
import config

logger = logging.getLogger(__name__)

# Pending email drafts keyed by chat_id
_pending_emails = {}


def is_authorized(user_id: int) -> bool:
    """Check if user is authorized to use the bot."""
    if not config.ALLOWED_USER_IDS:
        return True  # No restrictions if not configured
    return user_id in config.ALLOWED_USER_IDS


def detect_intent(text: str) -> dict:
    """Detect the user's intent from natural language."""
    text_lower = text.lower().strip()

    # Delete/Remove/Cancel patterns
    delete_match = re.match(r"^(delete|remove|cancel|trash)\s*(task\s*)?#?(\d+)$", text_lower)
    if delete_match:
        return {"action": "delete", "task_num": int(delete_match.group(3))}

    delete_match2 = re.match(r"^(delete|remove|cancel|trash)\s+(\d+)$", text_lower)
    if delete_match2:
        return {"action": "delete", "task_num": int(delete_match2.group(2))}

    # Done/Complete/Finish patterns
    done_match = re.match(r"^(done|complete|completed|finish|finished)\s*(task\s*)?#?(\d+)$", text_lower)
    if done_match:
        return {"action": "done", "task_num": int(done_match.group(3))}

    done_match2 = re.match(r"^(done|complete|completed|finish|finished)\s+(\d+)$", text_lower)
    if done_match2:
        return {"action": "done", "task_num": int(done_match2.group(2))}

    mark_done = re.match(r"^mark\s+(\d+)\s+(as\s+)?(done|complete|finished)$", text_lower)
    if mark_done:
        return {"action": "done", "task_num": int(mark_done.group(1))}

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


def _auto_save_email_contact(to_email: str, ai_data: dict):
    """Auto-save a contact after a successful email send, if not already known."""
    try:
        from bot.services.contacts_store import contacts_store
        logger.info(f"Auto-save contact check for: {to_email}")
        all_contacts = contacts_store.get_all()
        for c in all_contacts.values():
            if c.get("email", "").lower() == to_email.lower():
                logger.info(f"Contact already known: {to_email}")
                return  # Already known

        name = ai_data.get("recipient_name", "")
        if not name:
            local_part = to_email.split("@")[0]
            name = local_part.replace(".", " ").replace("_", " ").replace("-", " ").title()

        if name:
            logger.info(f"Saving new contact: {name} -> {to_email}")
            success = contacts_store.add_or_update_contact(name=name, email=to_email, source="auto_email")
            if success:
                logger.info(f"Contact saved successfully: {name}")
            else:
                logger.error(f"contacts_store.add_or_update_contact returned False for {name}")
        else:
            logger.warning(f"Could not derive name from email: {to_email}")
    except Exception as e:
        logger.error(f"Failed to auto-save contact for {to_email}: {type(e).__name__}: {e}")


async def handle_ai_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Process message with AI brain. Returns True if handled, False to fallback."""
    try:
        from bot.ai.brain import ai_brain
        tasks = notion_service.get_tasks()
        result = await ai_brain.process(text, tasks)

        if result["action"] == "fallback":
            return False  # Use rule-based fallback

        action = result["action"]
        data = result.get("data", {})
        response = result.get("response", "")

        if action == "add_task":
            # Create task from AI-parsed data
            from datetime import datetime, timedelta
            due_date = None
            if data.get("due_date"):
                try:
                    due_date = datetime.fromisoformat(data["due_date"]).date()
                except (ValueError, TypeError):
                    pass

            reminder_time = None
            if data.get("reminder_minutes"):
                reminder_time = datetime.now() + timedelta(minutes=data["reminder_minutes"])

            notion_service.add_task(
                title=data.get("title", text),
                category=data.get("category", "Personal"),
                due_date=due_date,
                priority=data.get("priority", "Medium"),
                reminder_time=reminder_time
            )

            if reminder_time:
                from bot.handlers.reminders import schedule_reminder
                schedule_reminder(
                    context.job_queue, update.effective_chat.id,
                    reminder_time, {"title": data.get("title"), "priority": data.get("priority")}
                )

            await update.message.reply_text(response or f"✅ Added: {data.get('title')}")

        elif action == "done":
            task_num = data.get("task_num", 1)
            if 1 <= task_num <= len(tasks):
                task = tasks[task_num - 1]
                notion_service.mark_complete(task["id"])
                await update.message.reply_text(response or f'✅ Done: "{task["title"]}"')
            else:
                await update.message.reply_text(f"Task #{task_num} not found.")

        elif action == "delete":
            task_num = data.get("task_num", 1)
            if 1 <= task_num <= len(tasks):
                task = tasks[task_num - 1]
                notion_service.delete_task(task["id"])
                await update.message.reply_text(response or f'🗑️ Deleted: "{task["title"]}"')
            else:
                await update.message.reply_text(f"Task #{task_num} not found.")

        elif action == "list":
            filter_type = data.get("filter", "all")
            if filter_type == "today":
                await handle_today(update)
            elif filter_type == "business":
                await handle_list(update, "Business")
            elif filter_type == "personal":
                await handle_list(update, "Personal")
            else:
                await handle_list(update, None)

        elif action == "summary":
            from bot.ai.brain import to_ascii
            summary = await ai_brain.weekly_summary(tasks)
            safe_summary = to_ascii(summary) if summary else "Analysis unavailable"
            await update.message.reply_text("TASK ANALYSIS\n\n" + safe_summary)

        elif action == "preview_email":
            to_email = data.get("to", "")
            subject = data.get("subject", "")
            body = data.get("body", "")

            if not to_email or not subject or not body:
                await update.message.reply_text(response or "Need email address, subject, and message to send.")
            else:
                chat_id = update.effective_chat.id
                _pending_emails[chat_id] = {"to": to_email, "subject": subject, "body": body}
                preview = (
                    f"📧 Email Draft:\n\n"
                    f"To: {to_email}\n"
                    f"Subject: {subject}\n\n"
                    f"{body}\n\n"
                    f"---\n"
                    f"{response or 'Send it? (yes/no, or tell me what to change)'}"
                )
                await update.message.reply_text(preview)

        elif action == "confirm_email":
            chat_id = update.effective_chat.id
            pending = _pending_emails.pop(chat_id, None)
            if not pending:
                await update.message.reply_text("No email draft to send. Tell me what to email!")
            else:
                from bot.services.email_service import send_email
                to_raw = pending["to"]
                recipients = [e.strip() for e in to_raw.replace(";", ",").split(",") if e.strip()]
                sent = []
                failed = []
                for rcpt in recipients:
                    success, msg = send_email(rcpt, pending["subject"], pending["body"])
                    if success:
                        sent.append(rcpt)
                        _auto_save_email_contact(rcpt, pending)
                    else:
                        failed.append(f"{rcpt}: {msg}")
                if sent and not failed:
                    await update.message.reply_text(response or f"✉️ Email sent to {', '.join(sent)}!")
                elif sent and failed:
                    await update.message.reply_text(f"✉️ Sent to {', '.join(sent)}\n❌ Failed: {'; '.join(failed)}")
                else:
                    _pending_emails[chat_id] = pending  # Restore draft on failure
                    await update.message.reply_text(f"Couldn't send email: {'; '.join(failed)}")

        elif action == "send_email":
            # Fallback if AI skips preview (shouldn't happen but just in case)
            from bot.services.email_service import send_email
            to_raw = data.get("to", "")
            subject = data.get("subject", "")
            body = data.get("body", "")

            if not to_raw or not subject or not body:
                await update.message.reply_text(response or "Need email address, subject, and message to send.")
            else:
                recipients = [e.strip() for e in to_raw.replace(";", ",").split(",") if e.strip()]
                sent = []
                failed = []
                for rcpt in recipients:
                    success, msg = send_email(rcpt, subject, body)
                    if success:
                        sent.append(rcpt)
                        _auto_save_email_contact(rcpt, data)
                    else:
                        failed.append(f"{rcpt}: {msg}")
                if sent and not failed:
                    await update.message.reply_text(response or f"✉️ Email sent to {', '.join(sent)}!")
                elif sent and failed:
                    await update.message.reply_text(f"✉️ Sent to {', '.join(sent)}\n❌ Failed: {'; '.join(failed)}")
                else:
                    await update.message.reply_text(f"Couldn't send email: {'; '.join(failed)}")

        elif action == "check_inbox":
            from bot.services.email_inbox import email_inbox
            from bot.handlers.emails import format_inbox
            messages = email_inbox.get_recent(10)
            formatted = format_inbox(messages)
            reply_text = response + "\n\n" + formatted if response else formatted
            await update.message.reply_text(reply_text)

        elif action == "read_email":
            from bot.services.email_inbox import email_inbox
            from bot.handlers.emails import format_full_email
            email_num = data.get("email_num", 1)
            msg_data = email_inbox.get_message_by_num(email_num)
            if msg_data:
                formatted = format_full_email(msg_data)
                reply_text = response + "\n\n" + formatted if response else formatted
                await update.message.reply_text(reply_text)
            else:
                await update.message.reply_text(
                    response or f"Email #{email_num} not found. Say 'check my email' first."
                )

        elif action == "reply_email":
            from bot.services.email_inbox import email_inbox
            email_num = data.get("email_num", 1)
            reply_body = data.get("body", "")
            if not reply_body:
                await update.message.reply_text(response or "What should the reply say?")
            else:
                success, msg = email_inbox.reply_by_num(email_num, reply_body)
                if success:
                    await update.message.reply_text(response or f"Reply sent to email #{email_num}!")
                else:
                    await update.message.reply_text(f"Couldn't reply: {msg}")

        elif action == "send_whatsapp":
            from bot.services.whatsapp_service import send_whatsapp
            to_number = data.get("to", "")
            message = data.get("message", "")

            if not to_number or not message:
                await update.message.reply_text(response or "Need phone number and message to send.")
            else:
                success, msg = send_whatsapp(to_number, message)
                if success:
                    await update.message.reply_text(response or f"📱 WhatsApp sent to {to_number}!")
                else:
                    await update.message.reply_text(f"Couldn't send WhatsApp: {msg}")

        elif action == "save_contact":
            from bot.services.contacts_store import contacts_store
            contact_name = data.get("name", "")
            contact_email = data.get("email", "")
            contact_phone = data.get("phone", "")

            if not contact_name:
                await update.message.reply_text(response or "Need a name to save a contact.")
            elif not contact_email and not contact_phone:
                await update.message.reply_text(response or "Need an email or phone number to save.")
            else:
                success = contacts_store.add_or_update_contact(
                    name=contact_name, email=contact_email,
                    phone=contact_phone, source="manual"
                )
                if success:
                    await update.message.reply_text(
                        response or f"Contact saved: {contact_name} ({contact_email or contact_phone})"
                    )
                else:
                    await update.message.reply_text(f"Couldn't save contact for {contact_name}.")

        elif action == "answer":
            await update.message.reply_text(response or data.get("text", "I'm here to help!"))

        else:
            return False  # Unknown action, fallback

        return True

    except Exception:
        # Silently fallback on any error
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
        await handle_delete(update, intent["task_num"])

    elif intent["action"] == "done":
        await handle_done(update, intent["task_num"])

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


async def handle_delete(update: Update, task_num: int):
    """Delete/remove a task."""
    try:
        tasks = notion_service.get_tasks()

        if task_num < 1 or task_num > len(tasks):
            await update.message.reply_text(f"Task #{task_num} not found. Say 'list' to see your tasks.")
            return

        task = tasks[task_num - 1]
        notion_service.mark_complete(task["id"])  # Archive = delete

        await update.message.reply_text(f'Deleted: "{task["title"]}"')

    except Exception as e:
        await update.message.reply_text("Error occurred")


async def handle_done(update: Update, task_num: int):
    """Mark a task as done."""
    try:
        tasks = notion_service.get_tasks()

        if task_num < 1 or task_num > len(tasks):
            await update.message.reply_text(f"Task #{task_num} not found. Say 'list' to see your tasks.")
            return

        task = tasks[task_num - 1]
        notion_service.mark_complete(task["id"])

        await update.message.reply_text(f'Done: "{task["title"]}"')

    except Exception as e:
        await update.message.reply_text("Error occurred")


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
    """Handle /done command - mark task as complete."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Sorry, you're not authorized to use this bot.")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /done <task number>\n\n"
            "Use /list to see task numbers."
        )
        return

    try:
        task_num = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Please provide a valid task number.")
        return

    try:
        # Get current tasks to find the one to mark done
        tasks = notion_service.get_tasks()

        if task_num < 1 or task_num > len(tasks):
            await update.message.reply_text(f"Invalid task number. Use /list to see available tasks (1-{len(tasks)}).")
            return

        task = tasks[task_num - 1]
        notion_service.mark_complete(task["id"])

        await update.message.reply_text(f'"{task["title"]}" marked complete!')

    except Exception as e:
        await update.message.reply_text("Error completing task")


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /delete command - remove a task."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Sorry, you're not authorized to use this bot.")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /delete <task number>\n\n"
            "Use /list to see task numbers."
        )
        return

    try:
        task_num = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Please provide a valid task number.")
        return

    try:
        tasks = notion_service.get_tasks()

        if task_num < 1 or task_num > len(tasks):
            await update.message.reply_text(f"Invalid task number. Use /list to see available tasks (1-{len(tasks)}).")
            return

        task = tasks[task_num - 1]
        notion_service.delete_task(task["id"])

        await update.message.reply_text(f'🗑️ Deleted: "{task["title"]}"')

    except Exception as e:
        await update.message.reply_text("Error deleting task")


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
