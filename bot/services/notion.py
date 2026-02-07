"""Notion API service for task management."""
import logging
from datetime import datetime, date
from typing import Optional
from notion_client import Client
import httpx
import config

logger = logging.getLogger(__name__)


class NotionTaskService:
    """Service for managing tasks in Notion."""

    def __init__(self):
        self.client = Client(auth=config.NOTION_TOKEN)
        self.database_id = config.NOTION_DATABASE_ID
        self._db_schema = None

    def _get_db_schema(self) -> dict:
        """Get and cache the database schema to check available properties."""
        if self._db_schema is None:
            try:
                # Use raw API call as notion-client may not return properties
                headers = {
                    'Authorization': f'Bearer {config.NOTION_TOKEN}',
                    'Notion-Version': '2022-06-28'
                }
                resp = httpx.get(
                    f'https://api.notion.com/v1/databases/{self.database_id}',
                    headers=headers
                )
                if resp.status_code == 200:
                    self._db_schema = resp.json().get("properties", {})
                else:
                    self._db_schema = {}
            except Exception as e:
                logger.error(f"Failed to fetch DB schema: {type(e).__name__}: {e}")
                self._db_schema = {}
        return self._db_schema

    def _get_property_name(self, prop_name: str) -> Optional[str]:
        """Get the actual property name from the database schema."""
        schema = self._get_db_schema()
        variations = [prop_name, prop_name.replace(" ", ""), prop_name.lower()]
        for var in variations:
            for key in schema:
                if key.lower() == var.lower():
                    return key
        return None

    def add_task(
        self,
        title: str,
        category: str = "Personal",
        due_date: Optional[date] = None,
        priority: str = "Medium",
        reminder_time: Optional[datetime] = None
    ) -> dict:
        """Add a new task to Notion using database properties."""
        # Determine the title property name
        title_prop = self._get_property_name("Task") or self._get_property_name("Name") or "Name"

        properties = {
            title_prop: {"title": [{"text": {"content": title}}]}
        }

        # Add Category if property exists
        category_prop = self._get_property_name("Category")
        if category_prop:
            properties[category_prop] = {"select": {"name": category}}

        # Add Priority if property exists
        priority_prop = self._get_property_name("Priority")
        if priority_prop:
            properties[priority_prop] = {"select": {"name": priority}}

        # Add Status if property exists
        status_prop = self._get_property_name("Status")
        if status_prop:
            properties[status_prop] = {"select": {"name": "To Do"}}

        # Set Done checkbox to false for new tasks
        done_prop = self._get_property_name("Done")
        if done_prop:
            properties[done_prop] = {"checkbox": False}

        # Add Due Date if property exists and date provided
        due_prop = self._get_property_name("Due Date") or self._get_property_name("Due")
        if due_prop and due_date:
            properties[due_prop] = {"date": {"start": due_date.isoformat()}}

        # Add Reminder if property exists and reminder time provided
        reminder_prop = self._get_property_name("Reminder")
        if reminder_prop and reminder_time:
            properties[reminder_prop] = {"date": {"start": reminder_time.isoformat()}}

        response = self.client.pages.create(
            parent={"database_id": self.database_id},
            properties=properties
        )
        return response

    def _extract_property_value(self, props: dict, prop_name: str, prop_type: str):
        """Extract value from a Notion property."""
        actual_name = self._get_property_name(prop_name)
        if not actual_name or actual_name not in props:
            return None

        prop_data = props[actual_name]
        if prop_type == "select":
            select_data = prop_data.get("select")
            return select_data.get("name") if select_data else None
        elif prop_type == "date":
            date_data = prop_data.get("date")
            return date_data.get("start") if date_data else None
        elif prop_type == "title":
            title_data = prop_data.get("title", [])
            return title_data[0].get("text", {}).get("content") if title_data else None
        elif prop_type == "checkbox":
            return prop_data.get("checkbox", False)
        return None

    def get_tasks(
        self,
        category: Optional[str] = None,
        status: str = None,
        due_today: bool = False,
        due_this_week: bool = False,
        overdue: bool = False
    ) -> list:
        """Get tasks from Notion using database query."""
        from datetime import timedelta

        # Query the database directly via raw API
        try:
            headers = {
                'Authorization': f'Bearer {config.NOTION_TOKEN}',
                'Notion-Version': '2022-06-28',
                'Content-Type': 'application/json'
            }
            resp = httpx.post(
                f'https://api.notion.com/v1/databases/{self.database_id}/query',
                headers=headers,
                json={}
            )
            if resp.status_code == 200:
                response = resp.json()
            else:
                # Safe print - avoid encoding errors
                response = {"results": []}
        except Exception as e:
            logger.error(f"Failed to query Notion tasks: {type(e).__name__}: {e}")
            response = {"results": []}

        tasks = []
        idx = 1
        today = date.today()
        today_str = today.strftime("%Y-%m-%d")
        week_end = (today + timedelta(days=7)).strftime("%Y-%m-%d")

        for page in response.get("results", []):
            # Skip if archived
            if page.get("archived", False):
                continue

            props = page.get("properties", {})

            # Try to get title from "Task" or "Name" property
            title = self._extract_property_value(props, "Task", "title")
            if not title:
                title = self._extract_property_value(props, "Name", "title")
            if not title:
                # Fallback: find any title property
                for key, val in props.items():
                    if val.get("type") == "title":
                        title_data = val.get("title", [])
                        title = title_data[0].get("text", {}).get("content") if title_data else "Untitled"
                        break
            title = title or "Untitled"

            # Try database properties first, then fall back to emoji parsing
            task_category = self._extract_property_value(props, "Category", "select")
            task_priority = self._extract_property_value(props, "Priority", "select")
            task_status = self._extract_property_value(props, "Status", "select")
            task_due = self._extract_property_value(props, "Due Date", "date") or \
                       self._extract_property_value(props, "Due", "date")

            # Check Done checkbox (direct access since we know the property name)
            done_data = props.get("Done", {})
            is_done = done_data.get("checkbox", False) if done_data.get("type") == "checkbox" else False

            # Fallback: parse from emoji format for legacy tasks
            if not task_category or not task_priority:
                if "🔴" in title:
                    task_priority = task_priority or "High"
                    title = title.replace("🔴", "")
                elif "⚪" in title:
                    task_priority = task_priority or "Low"
                    title = title.replace("⚪", "")

                if "💼" in title:
                    task_category = task_category or "Business"
                    title = title.replace("💼", "")
                elif "🏠" in title:
                    task_category = task_category or "Personal"
                    title = title.replace("🏠", "")

                if "📅" in title:
                    parts = title.split("📅")
                    title = parts[0]
                    if not task_due and len(parts) > 1:
                        task_due = parts[1].strip()

                # Support old [B]/[P] format
                if "[B] " in title:
                    task_category = task_category or "Business"
                    title = title.replace("[B] ", "")
                elif "[P] " in title:
                    title = title.replace("[P] ", "")

            # Set defaults
            task_category = task_category or "Personal"
            task_priority = task_priority or "Medium"
            task_status = task_status or "To Do"

            # Format due date for display
            due_display = None
            task_due_date = None
            if task_due:
                try:
                    due_dt = datetime.fromisoformat(task_due.replace("Z", "+00:00"))
                    due_display = due_dt.strftime("%b %d")
                    task_due_date = task_due[:10]

                    # Check if due today
                    if due_today and task_due_date != today_str:
                        continue

                    # Check if due this week (today to 7 days from now)
                    if due_this_week and not (today_str <= task_due_date <= week_end):
                        continue

                    # Check if overdue (before today)
                    if overdue and task_due_date >= today_str:
                        continue

                except (ValueError, AttributeError):
                    due_display = task_due

            # For overdue filter, skip tasks without due date
            if overdue and not task_due_date:
                continue

            # Filter by category if specified
            if category and task_category != category:
                continue

            # Skip completed tasks (checked checkbox) unless showing specific status
            if is_done and status != "Done":
                continue

            tasks.append({
                "index": idx,
                "id": page["id"],
                "title": title.strip(),
                "category": task_category,
                "due_date": due_display,
                "due_date_iso": task_due_date,
                "priority": task_priority,
                "status": task_status,
                "done": is_done
            })
            idx += 1

        return tasks

    def get_tasks_with_reminders(self) -> list:
        """Get tasks with reminders that are due now or in the past."""
        reminder_prop = self._get_property_name("Reminder")
        if not reminder_prop:
            return []

        try:
            now = datetime.now()
            response = self.client.databases.query(
                database_id=self.database_id,
                filter={
                    "and": [
                        {"property": reminder_prop, "date": {"is_not_empty": True}},
                        {"property": reminder_prop, "date": {"on_or_before": now.isoformat()}}
                    ]
                }
            )

            tasks = []
            for page in response.get("results", []):
                if page.get("archived", False):
                    continue

                props = page.get("properties", {})
                title = self._extract_property_value(props, "Task", "title") or \
                        self._extract_property_value(props, "Name", "title") or "Untitled"

                # Clean legacy emoji formatting
                for emoji in ["🔴", "⚪", "💼", "🏠"]:
                    title = title.replace(emoji, "")
                if "📅" in title:
                    title = title.split("📅")[0]

                tasks.append({
                    "id": page["id"],
                    "title": title.strip(),
                    "priority": self._extract_property_value(props, "Priority", "select") or "Medium",
                    "due_date": self._extract_property_value(props, "Due Date", "date")
                })
            return tasks
        except Exception as e:
            logger.error(f"Failed to get tasks with reminders: {type(e).__name__}: {e}")
            return []

    def mark_complete(self, page_id: str) -> dict:
        """Mark a task as complete using Done checkbox."""
        done_prop = self._get_property_name("Done")
        status_prop = self._get_property_name("Status")

        properties = {}

        # Primary: check the Done checkbox
        if done_prop:
            properties[done_prop] = {"checkbox": True}

        # Also update Status if it exists
        if status_prop:
            properties[status_prop] = {"select": {"name": "Done"}}

        if properties:
            try:
                return self.client.pages.update(
                    page_id=page_id,
                    properties=properties
                )
            except Exception as e:
                logger.error(f"Failed to mark task complete: {type(e).__name__}: {e}")

        # Fallback: archive
        return self.client.pages.update(page_id=page_id, archived=True)

    def delete_task(self, page_id: str) -> dict:
        """Delete a task by archiving it in Notion."""
        return self.client.pages.update(page_id=page_id, archived=True)

    def restore_task(self, page_id: str) -> dict:
        """Restore an archived task by unarchiving it in Notion."""
        try:
            result = self.client.pages.update(page_id=page_id, archived=False)
            # Also uncheck Done if it was marked complete
            done_prop = self._get_property_name("Done")
            status_prop = self._get_property_name("Status")
            props = {}
            if done_prop:
                props[done_prop] = {"checkbox": False}
            if status_prop:
                props[status_prop] = {"select": {"name": "To Do"}}
            if props:
                self.client.pages.update(page_id=page_id, properties=props)
            return result
        except Exception as e:
            logger.error(f"Failed to restore task: {type(e).__name__}: {e}")
            raise

    def update_task_title(self, page_id: str, new_title: str) -> dict:
        """Update a task's title."""
        title_prop = self._get_property_name("Task") or self._get_property_name("Name") or "Name"
        return self.client.pages.update(
            page_id=page_id,
            properties={
                title_prop: {"title": [{"text": {"content": new_title}}]}
            }
        )

    def clear_reminder(self, page_id: str) -> dict:
        """Clear the reminder from a task."""
        reminder_prop = self._get_property_name("Reminder")
        if not reminder_prop:
            return {}
        try:
            return self.client.pages.update(
                page_id=page_id,
                properties={reminder_prop: {"date": None}}
            )
        except Exception as e:
            logger.error(f"Failed to clear reminder: {type(e).__name__}: {e}")
            return {}

    def set_reminder(self, page_id: str, reminder_time: datetime) -> dict:
        """Set a reminder time for a task."""
        reminder_prop = self._get_property_name("Reminder")
        if not reminder_prop:
            return {}
        try:
            return self.client.pages.update(
                page_id=page_id,
                properties={reminder_prop: {"date": {"start": reminder_time.isoformat()}}}
            )
        except Exception as e:
            logger.error(f"Failed to set reminder: {type(e).__name__}: {e}")
            return {}


# Singleton instance
notion_service = NotionTaskService()
