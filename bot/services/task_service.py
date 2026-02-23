"""Task management service — PostgreSQL-backed, fully user-scoped."""
import logging
from datetime import date, datetime, timedelta

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)


def get_tasks(user_id: int, filter_type: str = "all") -> list:
    """Get tasks for a user with optional filtering."""
    with get_cursor() as cur:
        base = "SELECT * FROM tasks WHERE user_id = %s AND status = 'active'"
        params = [user_id]

        if filter_type == "today":
            base += " AND due_date = CURRENT_DATE"
        elif filter_type == "overdue":
            base += " AND due_date < CURRENT_DATE"
        elif filter_type == "week":
            base += " AND due_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '7 days'"
        elif filter_type == "business":
            base += " AND category = 'Business'"
        elif filter_type == "personal":
            base += " AND category = 'Personal'"

        base += " ORDER BY CASE priority WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END, due_date ASC NULLS LAST, id ASC"

        cur.execute(base, params)
        rows = cur.fetchall()

        tasks = []
        for i, row in enumerate(rows, 1):
            task = dict(row)
            task["index"] = i
            if task.get("due_date"):
                task["due_date_iso"] = task["due_date"].isoformat()
            tasks.append(task)
        return tasks


def add_task(user_id: int, title: str, category: str = "Personal",
             priority: str = "Medium", due_date: date = None) -> dict:
    """Create a new task for a user."""
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO tasks (user_id, title, category, priority, due_date)
               VALUES (%s, %s, %s, %s, %s) RETURNING *""",
            (user_id, title, category, priority, due_date)
        )
        return dict(cur.fetchone())


def complete_tasks(user_id: int, task_indices: list[int]) -> tuple[list, list]:
    """Mark tasks as completed by their display index. Returns (completed, not_found)."""
    tasks = get_tasks(user_id)
    completed = []
    not_found = []

    with get_cursor() as cur:
        for idx in sorted(set(task_indices), reverse=True):
            if 1 <= idx <= len(tasks):
                task = tasks[idx - 1]
                cur.execute(
                    "UPDATE tasks SET status = 'completed', completed_at = NOW() WHERE id = %s AND user_id = %s",
                    (task["id"], user_id)
                )
                completed.append(task)
            else:
                not_found.append(idx)

    return list(reversed(completed)), not_found


def delete_tasks(user_id: int, task_indices: list[int]) -> tuple[list, list]:
    """Soft-delete tasks by their display index. Returns (deleted, not_found)."""
    tasks = get_tasks(user_id)
    deleted = []
    not_found = []

    with get_cursor() as cur:
        for idx in sorted(set(task_indices), reverse=True):
            if 1 <= idx <= len(tasks):
                task = tasks[idx - 1]
                cur.execute(
                    "UPDATE tasks SET status = 'deleted' WHERE id = %s AND user_id = %s",
                    (task["id"], user_id)
                )
                deleted.append(task)
            else:
                not_found.append(idx)

    return list(reversed(deleted)), not_found


def restore_tasks(user_id: int, task_ids: list[int]) -> list:
    """Restore deleted/completed tasks by their DB ids."""
    restored = []
    with get_cursor() as cur:
        for task_id in task_ids:
            cur.execute(
                "UPDATE tasks SET status = 'active', completed_at = NULL WHERE id = %s AND user_id = %s RETURNING title",
                (task_id, user_id)
            )
            row = cur.fetchone()
            if row:
                restored.append(row["title"])
    return restored


def update_task_title(user_id: int, task_index: int, new_title: str) -> tuple[str, str] | None:
    """Update a task's title by index. Returns (old_title, new_title) or None."""
    tasks = get_tasks(user_id)
    if task_index < 1 or task_index > len(tasks):
        return None

    task = tasks[task_index - 1]
    old_title = task["title"]
    with get_cursor() as cur:
        cur.execute(
            "UPDATE tasks SET title = %s WHERE id = %s AND user_id = %s",
            (new_title, task["id"], user_id)
        )
    return old_title, new_title


def get_tasks_with_reminders(user_id: int) -> list:
    """Get tasks that have active reminders due now or in the past."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT * FROM tasks
               WHERE user_id = %s AND status = 'active'
               AND reminder_at IS NOT NULL AND reminder_at <= NOW()
               ORDER BY reminder_at ASC""",
            (user_id,)
        )
        return [dict(row) for row in cur.fetchall()]


def set_reminder(user_id: int, task_index: int, reminder_at: datetime) -> bool:
    """Set a reminder on a task by index."""
    tasks = get_tasks(user_id)
    if task_index < 1 or task_index > len(tasks):
        return False

    task = tasks[task_index - 1]
    with get_cursor() as cur:
        cur.execute(
            "UPDATE tasks SET reminder_at = %s WHERE id = %s AND user_id = %s",
            (reminder_at, task["id"], user_id)
        )
    return True


def clear_reminder(task_id: int):
    """Clear a reminder after it fires."""
    with get_cursor() as cur:
        cur.execute("UPDATE tasks SET reminder_at = NULL WHERE id = %s", (task_id,))


def count_active_tasks(user_id: int) -> int:
    """Count active tasks for tier limit checks."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) as cnt FROM tasks WHERE user_id = %s AND status = 'active'",
            (user_id,)
        )
        return cur.fetchone()["cnt"]


def count_active_reminders(user_id: int) -> int:
    """Count tasks with active reminders for tier limit checks."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) as cnt FROM tasks WHERE user_id = %s AND status = 'active' AND reminder_at IS NOT NULL",
            (user_id,)
        )
        return cur.fetchone()["cnt"]
