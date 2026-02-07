"""Email inbox service - read incoming emails via Agentmail."""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional
import config

logger = logging.getLogger(__name__)


class EmailInboxService:
    """Read and manage incoming emails from Agentmail inbox."""

    def __init__(self):
        self._client = None
        self._seen_ids: set[str] = set()
        self._messages_cache: list[dict] = []

    def _ensure_client(self):
        """Lazy-init the Agentmail client."""
        if self._client is None:
            api_key = getattr(config, 'AGENTMAIL_API_KEY', '')
            if not api_key:
                raise ValueError("Agentmail not configured")
            from agentmail import AgentMail
            self._client = AgentMail(api_key=api_key)

    def is_configured(self) -> bool:
        """Check if inbox reading is available."""
        return bool(getattr(config, 'AGENTMAIL_API_KEY', '') and
                     getattr(config, 'AGENTMAIL_INBOX', ''))

    def get_recent(self, limit: int = 10) -> list[dict]:
        """Get recent inbox messages. Returns list of message summaries."""
        try:
            self._ensure_client()
            inbox = getattr(config, 'AGENTMAIL_INBOX', '')
            if not inbox:
                return []

            response = self._client.inboxes.messages.list(
                inbox_id=inbox,
                limit=limit
            )

            messages = []
            for msg in response.messages:
                messages.append({
                    "id": msg.message_id,
                    "from": getattr(msg, 'from_', '') or '',
                    "subject": msg.subject or '(no subject)',
                    "preview": getattr(msg, 'preview', '') or '',
                    "timestamp": msg.timestamp if hasattr(msg, 'timestamp') else None,
                })

            self._messages_cache = messages
            return messages

        except Exception as e:
            logger.error(f"Failed to fetch inbox: {type(e).__name__}: {e}")
            return []

    def get_message(self, message_id: str) -> Optional[dict]:
        """Get full message content by ID."""
        try:
            self._ensure_client()
            inbox = getattr(config, 'AGENTMAIL_INBOX', '')
            if not inbox:
                return None

            msg = self._client.inboxes.messages.get(
                inbox_id=inbox,
                message_id=message_id
            )

            body = ''
            if hasattr(msg, 'text') and msg.text:
                body = msg.text
            elif hasattr(msg, 'extracted_text') and msg.extracted_text:
                body = msg.extracted_text
            elif hasattr(msg, 'html') and msg.html:
                # Basic HTML stripping fallback
                import re
                body = re.sub(r'<[^>]+>', '', msg.html)
                body = body.strip()

            return {
                "id": msg.message_id,
                "from": getattr(msg, 'from_', '') or '',
                "to": getattr(msg, 'to', []) or [],
                "subject": msg.subject or '(no subject)',
                "body": body,
                "timestamp": msg.timestamp if hasattr(msg, 'timestamp') else None,
                "attachments": [
                    {"name": a.filename, "size": a.size}
                    for a in (getattr(msg, 'attachments', []) or [])
                    if hasattr(a, 'filename')
                ],
            }

        except Exception as e:
            logger.error(f"Failed to get message {message_id}: {type(e).__name__}: {e}")
            return None

    def get_message_by_num(self, num: int) -> Optional[dict]:
        """Get full message content by its position in the cached list (1-indexed)."""
        if not self._messages_cache:
            self.get_recent()
        if num < 1 or num > len(self._messages_cache):
            return None
        msg_summary = self._messages_cache[num - 1]
        return self.get_message(msg_summary["id"])

    def get_new_messages(self) -> list[dict]:
        """Get messages not yet seen (for notifications). Marks them as seen."""
        messages = self.get_recent()
        new = []
        for msg in messages:
            if msg["id"] not in self._seen_ids:
                new.append(msg)
                self._seen_ids.add(msg["id"])
        return new

    def seed_seen_ids(self):
        """On first run, mark all current messages as seen so we don't spam."""
        messages = self.get_recent()
        for msg in messages:
            self._seen_ids.add(msg["id"])
        logger.info(f"Seeded {len(self._seen_ids)} existing email IDs")

    def reply_to(self, message_id: str, text: str) -> tuple[bool, str]:
        """Reply to a message. Returns (success, message)."""
        try:
            self._ensure_client()
            inbox = getattr(config, 'AGENTMAIL_INBOX', '')
            if not inbox:
                return False, "Inbox not configured"

            self._client.inboxes.messages.reply(
                inbox_id=inbox,
                message_id=message_id,
                text=text
            )
            return True, "Reply sent"

        except Exception as e:
            logger.error(f"Failed to reply to {message_id}: {type(e).__name__}: {e}")
            return False, f"Reply failed: {type(e).__name__}"

    def reply_by_num(self, num: int, text: str) -> tuple[bool, str]:
        """Reply to a message by its position in cached list (1-indexed)."""
        if not self._messages_cache:
            self.get_recent()
        if num < 1 or num > len(self._messages_cache):
            return False, f"Email #{num} not found. Say 'check my email' first."
        msg_id = self._messages_cache[num - 1]["id"]
        return self.reply_to(msg_id, text)


# Singleton
email_inbox = EmailInboxService()
