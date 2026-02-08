"""Persistent conversation history using SQLite."""
import json
import sqlite3
import logging
import os
from datetime import datetime, timedelta

import config

logger = logging.getLogger(__name__)

_DB_DIR = os.getenv("DATA_DIR", "./data")
_DB_PATH = os.path.join(_DB_DIR, "conversations.db")
_conn = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        os.makedirs(_DB_DIR, exist_ok=True)
        _conn = sqlite3.Connection(_DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        _conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_conv_chat ON conversations(chat_id, id)
        """)
        _conn.commit()
    return _conn


def get_history(chat_id: int, limit: int = None) -> list:
    """Get recent conversation messages in Anthropic API format."""
    limit = limit or int(os.getenv("CONVERSATION_HISTORY_LIMIT", "20"))
    conn = _get_conn()
    rows = conn.execute(
        "SELECT role, content FROM conversations WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
        (chat_id, limit)
    ).fetchall()
    messages = []
    for row in reversed(rows):
        try:
            content = json.loads(row["content"])
        except (json.JSONDecodeError, TypeError):
            content = row["content"]
        messages.append({"role": row["role"], "content": content})
    return messages


def save_turn(chat_id: int, role: str, content):
    """Save a single conversation turn."""
    conn = _get_conn()
    serialized = json.dumps(content) if not isinstance(content, str) else content
    conn.execute(
        "INSERT INTO conversations (chat_id, role, content) VALUES (?, ?, ?)",
        (chat_id, role, serialized)
    )
    conn.commit()


def clear_history(chat_id: int):
    """Clear conversation history for a chat."""
    conn = _get_conn()
    conn.execute("DELETE FROM conversations WHERE chat_id = ?", (chat_id,))
    conn.commit()


def prune_old(days: int = 7):
    """Delete conversation history older than N days."""
    conn = _get_conn()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn.execute("DELETE FROM conversations WHERE created_at < ?", (cutoff,))
    conn.commit()
