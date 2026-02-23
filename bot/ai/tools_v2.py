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
            "description": "Create a new task. Infer category (Personal/Business) and priority from context.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Task title"},
                    "category": {"type": "string", "enum": ["Personal", "Business"]},
                    "priority": {"type": "string", "enum": ["Low", "Medium", "High"]},
                    "due_date": {"type": "string", "description": "Due date in YYYY-MM-DD format, or null"}
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
            "description": "Edit a task's title.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_number": {"type": "integer", "description": "Task number to edit"},
                    "new_title": {"type": "string", "description": "New title for the task"}
                },
                "required": ["task_number", "new_title"]
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
                task_list.append(entry)
            return {"tasks": task_list, "count": len(task_list)}

        elif name == "add_task":
            due_date = None
            if args.get("due_date"):
                try:
                    due_date = datetime.fromisoformat(args["due_date"]).date()
                except (ValueError, TypeError):
                    pass
            task_service.add_task(
                user_id=user_id,
                title=args["title"],
                category=args.get("category", "Personal"),
                priority=args.get("priority", "Medium"),
                due_date=due_date,
            )
            return {"success": True, "title": args["title"]}

        elif name == "complete_tasks":
            task_nums = args.get("task_numbers", [])
            completed, not_found = task_service.complete_tasks(user_id, task_nums)
            _undo_buffer[user_id] = [{"action": "done", "task_id": t["id"], "title": t["title"]} for t in completed]
            # Update streak on completion
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

        else:
            return {"error": f"Unknown tool: {name}"}

    except Exception as e:
        logger.error(f"Tool {name} failed: {type(e).__name__}: {e}")
        return {"error": f"Tool failed: {type(e).__name__}: {str(e)[:100]}"}
