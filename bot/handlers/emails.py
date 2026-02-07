"""Email inbox handlers - polling job and message formatting."""
import logging
from datetime import datetime, timezone
from telegram.ext import ContextTypes
import config

logger = logging.getLogger(__name__)


def _time_ago(timestamp) -> str:
    """Format a timestamp as relative time (e.g., '5 min ago')."""
    if not timestamp:
        return ""
    try:
        now = datetime.now(timezone.utc)
        if hasattr(timestamp, 'tzinfo') and timestamp.tzinfo is None:
            # Naive datetime, assume UTC
            from datetime import timezone as tz
            timestamp = timestamp.replace(tzinfo=tz.utc)
        elif isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))

        diff = now - timestamp
        seconds = int(diff.total_seconds())
        if seconds < 60:
            return "just now"
        elif seconds < 3600:
            mins = seconds // 60
            return f"{mins} min ago"
        elif seconds < 86400:
            hours = seconds // 3600
            return f"{hours} hr ago"
        else:
            days = seconds // 86400
            return f"{days}d ago"
    except Exception:
        return ""


def format_inbox(messages: list[dict]) -> str:
    """Format a list of messages for Telegram display."""
    if not messages:
        return "Your inbox is empty!"

    lines = [f"Your Inbox ({len(messages)} recent):\n"]
    for i, msg in enumerate(messages, 1):
        sender = msg.get("from", "unknown")
        subject = msg.get("subject", "(no subject)")
        ago = _time_ago(msg.get("timestamp"))
        ago_str = f" ({ago})" if ago else ""

        lines.append(f"{i}. From: {sender}")
        lines.append(f"   Subject: {subject}{ago_str}")

    lines.append("\nSay \"read email 1\" for full content")
    lines.append("or \"reply to email 1: your message\"")
    return "\n".join(lines)


def format_full_email(msg: dict) -> str:
    """Format a full email message for Telegram display."""
    if not msg:
        return "Couldn't load that email."

    sender = msg.get("from", "unknown")
    subject = msg.get("subject", "(no subject)")
    body = msg.get("body", "(empty)")
    ago = _time_ago(msg.get("timestamp"))
    ago_str = f" ({ago})" if ago else ""

    # Truncate very long emails for Telegram (4096 char limit)
    if len(body) > 3000:
        body = body[:3000] + "\n\n... (truncated)"

    attachments = msg.get("attachments", [])
    att_str = ""
    if attachments:
        att_names = [a.get("name", "file") for a in attachments]
        att_str = f"\nAttachments: {', '.join(att_names)}"

    return (
        f"From: {sender}\n"
        f"Subject: {subject}{ago_str}\n"
        f"{att_str}\n"
        f"---\n"
        f"{body}"
    )


def setup_email_check_job(application, chat_id: int = None):
    """Set up the recurring email inbox check job."""
    from bot.handlers.reminders import register_chat_id, _active_chat_ids
    from bot.services.email_inbox import email_inbox

    if not email_inbox.is_configured():
        logger.info("Email inbox not configured, skipping email check job")
        return

    job_queue = application.job_queue

    if chat_id:
        register_chat_id(chat_id)

    # Seed seen IDs on startup so we don't notify about old emails
    try:
        email_inbox.seed_seen_ids()
    except Exception as e:
        logger.warning(f"Could not seed email IDs: {e}")

    async def email_check_callback(context: ContextTypes.DEFAULT_TYPE):
        """Check for new emails and notify active chats."""
        try:
            logger.info(f"Email check running. Active chat IDs: {_active_chat_ids}")

            if not _active_chat_ids:
                logger.warning("No active chat IDs registered - can't send notifications")
                return

            new_messages = email_inbox.get_new_messages()
            logger.info(f"Email check found {len(new_messages)} new message(s)")

            if not new_messages:
                return

            for msg in new_messages:
                sender = msg.get("from", "unknown")
                subject = msg.get("subject", "(no subject)")
                preview = msg.get("preview", "")
                if len(preview) > 150:
                    preview = preview[:150] + "..."

                notification = (
                    f"New email!\n\n"
                    f"From: {sender}\n"
                    f"Subject: {subject}\n"
                )
                if preview:
                    notification += f"\n{preview}\n"
                notification += "\nSay \"check my email\" to see your inbox"

                for cid in _active_chat_ids:
                    try:
                        await context.bot.send_message(chat_id=cid, text=notification)
                        logger.info(f"Sent email notification to chat {cid}")
                    except Exception as e:
                        logger.error(f"Failed to notify chat {cid}: {type(e).__name__}: {e}")

        except Exception as e:
            logger.error(f"Email check failed: {type(e).__name__}: {e}")

    interval = max(60, config.EMAIL_CHECK_INTERVAL * 60)
    job_queue.run_repeating(email_check_callback, interval=interval, first=15)
    logger.info(f"Email check job started (every {interval}s, first check in 15s)")
