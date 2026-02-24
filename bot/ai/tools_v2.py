"""MVP tool definitions and executor — user-scoped, PostgreSQL-backed."""
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Undo buffer (in-memory, keyed by user_id)
_undo_buffer = {}


def get_tool_definitions() -> list:
    """Return MVP tool definitions for Claude tool_use."""
    return [
        {
            "name": "get_tasks",
            "description": "Get the user's current tasks. Use this when they ask to see tasks, what's pending, what's due, etc.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "enum": ["all", "today", "business", "personal", "overdue", "week"],
                        "description": "Filter tasks. 'all' returns everything."
                    }
                },
                "required": ["filter"]
            }
        },
        {
            "name": "add_task",
            "description": "Create a new task. Infer category (Personal/Business) and priority from context. Set recurrence for repeating tasks.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Task title"},
                    "category": {"type": "string", "enum": ["Personal", "Business"]},
                    "priority": {"type": "string", "enum": ["Low", "Medium", "High"]},
                    "due_date": {"type": "string", "description": "Due date in YYYY-MM-DD format, or null"},
                    "recurrence": {"type": "string", "enum": ["daily", "weekdays", "weekly", "monthly"], "description": "Repeat pattern. Use when user says 'every day', 'every Monday', 'every month', 'weekdays', etc."}
                },
                "required": ["title"]
            }
        },
        {
            "name": "complete_tasks",
            "description": "Mark one or more tasks as done by their task numbers from the list.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_numbers": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "List of task numbers to mark as done"
                    }
                },
                "required": ["task_numbers"]
            }
        },
        {
            "name": "delete_tasks",
            "description": "Delete one or more tasks by their task numbers from the list.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_numbers": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "List of task numbers to delete"
                    }
                },
                "required": ["task_numbers"]
            }
        },
        {
            "name": "undo_last_action",
            "description": "Undo the last delete or done action, restoring the affected tasks.",
            "input_schema": {"type": "object", "properties": {}}
        },
        {
            "name": "edit_task",
            "description": "Edit a task's title only. For changing due date, priority, or category, use update_task instead.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_number": {"type": "integer", "description": "Task number to edit"},
                    "new_title": {"type": "string", "description": "New title for the task"}
                },
                "required": ["task_number", "new_title"]
            }
        },
        {
            "name": "update_task",
            "description": "Update a task's due date, priority, or category. Use when user says 'move X to Friday', 'make it high priority', 'change category to business', 'reschedule', 'postpone', etc.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_number": {"type": "integer", "description": "Task number to update"},
                    "due_date": {"type": "string", "description": "New due date in YYYY-MM-DD format. Use null to clear."},
                    "priority": {"type": "string", "enum": ["Low", "Medium", "High"], "description": "New priority level"},
                    "category": {"type": "string", "enum": ["Personal", "Business"], "description": "New category"},
                    "title": {"type": "string", "description": "New title (optional)"}
                },
                "required": ["task_number"]
            }
        },
        {
            "name": "set_reminder",
            "description": "Set a reminder on a task. The bot will send a message at the specified time. Use when user says 'remind me', 'set a reminder', 'alert me at', etc.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_number": {"type": "integer", "description": "Task number to set reminder on"},
                    "reminder_datetime": {"type": "string", "description": "When to remind, in ISO format YYYY-MM-DDTHH:MM:SS. Convert 'tomorrow at 9am' etc. to full datetime."}
                },
                "required": ["task_number", "reminder_datetime"]
            }
        },
    ]


async def execute_tool(name: str, args: dict, user_id: int) -> dict:
    """Execute a tool scoped to user_id."""
    try:
        from bot.services import task_service

        if name == "get_tasks":
            filter_type = args.get("filter", "all")
            tasks = task_service.get_tasks(user_id, filter_type)
            if not tasks:
                return {"tasks": [], "message": "No tasks found."}
            task_list = []
            for t in tasks:
                entry = {
                    "number": t["index"],
                    "title": t["title"],
                    "category": t["category"],
                    "priority": t["priority"],
                }
                if t.get("due_date"):
                    entry["due_date"] = t["due_date"].isoformat() if hasattr(t["due_date"], "isoformat") else str(t["due_date"])
                if t.get("recurrence"):
                    entry["recurrence"] = t["recurrence"]
                task_list.append(entry)
            return {"tasks": task_list, "count": len(task_list)}

        elif name == "add_task":
            due_date = None
            if args.get("due_date"):
                try:
                    due_date = datetime.fromisoformat(args["due_date"]).date()
                except (ValueError, TypeError):
                    pass
            recurrence = args.get("recurrence")
            task_service.add_task(
                user_id=user_id,
                title=args["title"],
                category=args.get("category", "Personal"),
                priority=args.get("priority", "Medium"),
                due_date=due_date,
                recurrence=recurrence,
            )
            result = {"success": True, "title": args["title"]}
            if recurrence:
                result["recurrence"] = recurrence
            return result

        elif name == "complete_tasks":
            task_nums = args.get("task_numbers", [])
            completed, not_found = task_service.complete_tasks(user_id, task_nums)
            _undo_buffer[user_id] = [{"action": "done", "task_id": t["id"], "title": t["title"]} for t in completed]
            # Update streak + spawn recurring tasks
            recurring_spawned = []
            if completed:
                try:
                    from bot.services import coaching_service
                    streak = coaching_service.update_streak(user_id)
                    result = {
                        "completed": [t["title"] for t in completed],
                        "streak": streak.get("current_streak", 0),
                    }
                except Exception:
                    result = {"completed": [t["title"] for t in completed]}
                # Spawn next instance for recurring tasks
                for t in completed:
                    spawned = task_service.spawn_next_recurring(user_id, t)
                    if spawned:
                        recurring_spawned.append(f"{spawned['title']} (next: {spawned['due_date']})")
                if recurring_spawned:
                    result["recurring_created"] = recurring_spawned
            else:
                result = {"completed": []}
            if not_found:
                result["not_found"] = not_found
            return result

        elif name == "delete_tasks":
            task_nums = args.get("task_numbers", [])
            deleted, not_found = task_service.delete_tasks(user_id, task_nums)
            _undo_buffer[user_id] = [{"action": "delete", "task_id": t["id"], "title": t["title"]} for t in deleted]
            result = {"deleted": [t["title"] for t in deleted]}
            if not_found:
                result["not_found"] = not_found
            return result

        elif name == "undo_last_action":
            entries = _undo_buffer.pop(user_id, None)
            if not entries:
                return {"message": "Nothing to undo."}
            task_ids = [e["task_id"] for e in entries]
            restored = task_service.restore_tasks(user_id, task_ids)
            return {"restored": restored}

        elif name == "edit_task":
            result = task_service.update_task_title(user_id, args["task_number"], args["new_title"])
            if result:
                return {"old_title": result[0], "new_title": result[1]}
            return {"error": f"Task #{args['task_number']} not found."}

        elif name == "update_task":
            updates = {}
            if args.get("due_date"):
                try:
                    updates["due_date"] = datetime.fromisoformat(args["due_date"]).date()
                except (ValueError, TypeError):
                    return {"error": "Invalid date format. Use YYYY-MM-DD."}
            if args.get("priority"):
                updates["priority"] = args["priority"]
            if args.get("category"):
                updates["category"] = args["category"]
            if args.get("title"):
                updates["title"] = args["title"]
            if not updates:
                return {"error": "No changes specified."}
            result = task_service.update_task(user_id, args["task_number"], **updates)
            if result:
                changes = ", ".join(f"{k}={v}" for k, v in updates.items())
                return {"success": True, "task": result["title"], "changes": changes}
            return {"error": f"Task #{args['task_number']} not found."}

        elif name == "set_reminder":
            from bot.services.tier_service import check_limit
            user_tier = "free"
            try:
                from bot.services import user_service
                u = user_service.get_user_by_id(user_id)
                user_tier = u.get("tier", "free") if u else "free"
            except Exception:
                pass
            allowed, limit_msg = check_limit(user_id, "set_reminder", user_tier)
            if not allowed:
                return {"error": limit_msg}
            try:
                reminder_dt = datetime.fromisoformat(args["reminder_datetime"])
            except (ValueError, TypeError):
                return {"error": "Invalid datetime format. Use YYYY-MM-DDTHH:MM:SS"}
            if reminder_dt <= datetime.now():
                return {"error": "Reminder time must be in the future."}
            ok = task_service.set_reminder(user_id, args["task_number"], reminder_dt)
            if ok:
                tasks = task_service.get_tasks(user_id)
                idx = args["task_number"]
                title = tasks[idx - 1]["title"] if 1 <= idx <= len(tasks) else "task"
                return {"success": True, "task": title, "remind_at": reminder_dt.strftime("%b %d at %I:%M %p")}
            return {"error": f"Task #{args['task_number']} not found."}

        else:
            return {"error": f"Unknown tool: {name}"}

    except Exception as e:
        logger.error(f"Tool {name} failed: {type(e).__name__}: {e}")
        return {"error": f"Tool failed: {type(e).__name__}: {str(e)[:100]}"}
