"""SQLite storage for accounting rules and sessions."""

from __future__ import annotations

import json
import sqlite3
import logging
import os
from pathlib import Path

from bot.accounting.models import CategoryRule

logger = logging.getLogger(__name__)

_connection: sqlite3.Connection | None = None

DATA_DIR = os.getenv("DATA_DIR", "./data")
DB_PATH = os.path.join(DATA_DIR, "accounting.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS category_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,
    category TEXT NOT NULL,
    note_template TEXT DEFAULT '',
    match_type TEXT DEFAULT 'contains',
    confidence REAL DEFAULT 1.0,
    match_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    filename TEXT,
    total_transactions INTEGER DEFAULT 0,
    auto_categorized INTEGER DEFAULT 0,
    needs_review INTEGER DEFAULT 0,
    status TEXT DEFAULT 'processing',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    date TEXT,
    description TEXT,
    value REAL,
    type TEXT,
    category TEXT,
    note TEXT,
    categorization_method TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rules_pattern ON category_rules(pattern);
CREATE INDEX IF NOT EXISTS idx_transactions_session ON transactions(session_id);
"""


def get_connection() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        db_path = Path(DB_PATH)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _connection = sqlite3.connect(str(db_path), check_same_thread=False)
        _connection.row_factory = sqlite3.Row
        _connection.execute("PRAGMA journal_mode=WAL")
        _connection.execute("PRAGMA foreign_keys=ON")
    return _connection


def initialize():
    """Run schema and seed default rules."""
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()

    cursor = conn.execute("SELECT COUNT(*) FROM category_rules")
    if cursor.fetchone()[0] == 0:
        _seed_default_rules(conn)

    logger.info("Accounting database initialized")


def _seed_default_rules(conn: sqlite3.Connection):
    rules_path = Path(__file__).parent / "default_rules.json"
    if not rules_path.exists():
        return

    data = json.loads(rules_path.read_text())
    for rule in data.get("rules", []):
        conn.execute(
            "INSERT INTO category_rules (pattern, category, note_template, match_type) "
            "VALUES (?, ?, ?, ?)",
            (rule["pattern"], rule["category"], rule.get("note", ""), rule.get("match_type", "contains")),
        )
    conn.commit()
    logger.info(f"Seeded {len(data.get('rules', []))} category rules")


def get_all_rules() -> list[CategoryRule]:
    rows = get_connection().execute(
        "SELECT * FROM category_rules ORDER BY match_count DESC, id ASC"
    ).fetchall()
    return [
        CategoryRule(
            id=row["id"], pattern=row["pattern"], category=row["category"],
            note_template=row["note_template"], match_type=row["match_type"],
            confidence=row["confidence"], match_count=row["match_count"],
        )
        for row in rows
    ]


def add_rule(pattern: str, category: str, note: str = "", match_type: str = "contains") -> CategoryRule:
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO category_rules (pattern, category, note_template, match_type) VALUES (?, ?, ?, ?)",
        (pattern, category, note, match_type),
    )
    conn.commit()
    return CategoryRule(id=cursor.lastrowid, pattern=pattern, category=category, note_template=note, match_type=match_type)


def increment_rule_match(rule_id: int):
    conn = get_connection()
    conn.execute("UPDATE category_rules SET match_count = match_count + 1 WHERE id = ?", (rule_id,))
    conn.commit()


def save_session(session_id: str, filename: str, total: int, auto: int, review: int):
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO sessions (id, filename, total_transactions, auto_categorized, needs_review) "
        "VALUES (?, ?, ?, ?, ?)",
        (session_id, filename, total, auto, review),
    )
    conn.commit()


def complete_session(session_id: str):
    conn = get_connection()
    conn.execute("UPDATE sessions SET status='complete', completed_at=CURRENT_TIMESTAMP WHERE id=?", (session_id,))
    conn.commit()


def save_transaction(session_id: str, date: str, description: str, value: float,
                     txn_type: str, category: str, note: str, method: str):
    conn = get_connection()
    conn.execute(
        "INSERT INTO transactions (session_id, date, description, value, type, category, note, categorization_method) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, date, description, value, txn_type, category, note, method),
    )
    conn.commit()
