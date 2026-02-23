"""Admin commands — migration, diagnostics."""
import logging
import os
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


def _is_admin(telegram_user_id: int) -> bool:
    """Check if user is an admin."""
    admin_ids = os.environ.get("ADMIN_USER_IDS", "")
    if not admin_ids:
        admin_ids = os.environ.get("ALLOWED_USER_IDS", "")
    return str(telegram_user_id) in [x.strip() for x in admin_ids.split(",") if x.strip()]


async def cmd_migrate_notion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Migrate tasks from Notion to PostgreSQL. Admin only."""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return

    notion_token = os.environ.get("NOTION_TOKEN")
    notion_db_id = os.environ.get("NOTION_DATABASE_ID")

    if not notion_token or not notion_db_id:
        await update.message.reply_text("NOTION_TOKEN or NOTION_DATABASE_ID not set.")
        return

    await update.message.reply_text("Migrating tasks from Notion... hold on.")

    try:
        import httpx
        from bot.services import user_service, task_service

        # Get or create the admin user
        tg = update.effective_user
        user = user_service.get_or_create_user(tg.id, tg.username, tg.first_name)

        # Fetch all tasks from Notion
        headers = {
            "Authorization": f"Bearer {notion_token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

        all_results = []
        has_more = True
        start_cursor = None

        while has_more:
            body = {}
            if start_cursor:
                body["start_cursor"] = start_cursor

            resp = httpx.post(
                f"https://api.notion.com/v1/databases/{notion_db_id}/query",
                headers=headers,
                json=body,
                timeout=30.0,
            )

            if resp.status_code != 200:
                await update.message.reply_text(f"Notion API error: {resp.status_code}")
                return

            data = resp.json()
            all_results.extend(data.get("results", []))
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")

        if not all_results:
            await update.message.reply_text("No tasks found in Notion.")
            return

        # Parse and insert tasks
        migrated = 0
        skipped = 0

        for page in all_results:
            props = page.get("properties", {})

            # Extract title
            title = _get_notion_title(props)
            if not title:
                skipped += 1
                continue

            # Extract category
            category = _get_notion_select(props, ["Category"])

            # Extract priority
            priority = _get_notion_select(props, ["Priority"])

            # Extract due date
            due_date = _get_notion_date(props, ["Due Date", "Due", "Date"])

            # Extract status/done
            is_done = _get_notion_checkbox(props, ["Done"])
            status_text = _get_notion_select(props, ["Status"])

            # Skip completed tasks
            if is_done or (status_text and status_text.lower() in ("done", "completed")):
                skipped += 1
                continue

            # Insert into PostgreSQL
            task_service.add_task(
                user_id=user["id"],
                title=title,
                category=category or "Personal",
                priority=priority or "Medium",
                due_date=due_date,
            )
            migrated += 1

        await update.message.reply_text(
            f"Migration complete!\n\n"
            f"Migrated: {migrated} active tasks\n"
            f"Skipped: {skipped} (completed or no title)\n\n"
            f"Use /list to see your tasks."
        )

    except Exception as e:
        logger.error(f"Migration failed: {type(e).__name__}: {e}")
        await update.message.reply_text(f"Migration failed: {type(e).__name__}: {e}")


async def cmd_diagnostics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot diagnostics. Admin only."""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return

    lines = ["Bot Diagnostics:\n"]
    lines.append(f"TELEGRAM_BOT_TOKEN: {'SET' if os.environ.get('TELEGRAM_BOT_TOKEN') else 'MISSING'}")
    lines.append(f"DATABASE_URL: {'SET' if os.environ.get('DATABASE_URL') else 'MISSING'}")
    lines.append(f"ANTHROPIC_API_KEY: {'SET' if os.environ.get('ANTHROPIC_API_KEY') else 'MISSING'}")
    lines.append(f"NOTION_TOKEN: {'SET' if os.environ.get('NOTION_TOKEN') else 'MISSING'}")
    lines.append(f"STRIPE_PROVIDER_TOKEN: {'SET' if os.environ.get('STRIPE_PROVIDER_TOKEN') else 'MISSING'}")
    lines.append(f"ADMIN_USER_IDS: {os.environ.get('ADMIN_USER_IDS', 'NOT SET')}")
    lines.append(f"RAILWAY_PUBLIC_DOMAIN: {os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'NOT SET')}")
    lines.append(f"PORT: {os.environ.get('PORT', 'NOT SET')}")

    # DB check
    try:
        from bot.db.database import get_cursor
        with get_cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM users")
            users = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) as cnt FROM tasks WHERE status = 'active'")
            tasks = cur.fetchone()["cnt"]
        lines.append(f"\nDB: {users} users, {tasks} active tasks")
    except Exception as e:
        lines.append(f"\nDB: ERROR - {e}")

    await update.message.reply_text("\n".join(lines))


def _get_notion_title(props: dict) -> str:
    """Extract title from Notion properties."""
    for key, val in props.items():
        if val.get("type") == "title":
            title_arr = val.get("title", [])
            if title_arr:
                return "".join(t.get("plain_text", "") for t in title_arr).strip()
    return ""


def _get_notion_select(props: dict, names: list) -> str:
    """Extract a select value from Notion properties."""
    for name in names:
        for key, val in props.items():
            if key.lower() == name.lower() and val.get("type") == "select":
                sel = val.get("select")
                if sel:
                    return sel.get("name", "")
    return ""


def _get_notion_date(props: dict, names: list):
    """Extract a date from Notion properties."""
    from datetime import date as date_type
    for name in names:
        for key, val in props.items():
            if key.lower() == name.lower() and val.get("type") == "date":
                date_obj = val.get("date")
                if date_obj and date_obj.get("start"):
                    try:
                        return date_type.fromisoformat(date_obj["start"][:10])
                    except (ValueError, TypeError):
                        pass
    return None


def _get_notion_checkbox(props: dict, names: list) -> bool:
    """Extract a checkbox value from Notion properties."""
    for name in names:
        for key, val in props.items():
            if key.lower() == name.lower() and val.get("type") == "checkbox":
                return val.get("checkbox", False)
    return False
