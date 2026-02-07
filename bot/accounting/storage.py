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

# Extra columns added after initial schema
_MIGRATIONS = [
    "ALTER TABLE transactions ADD COLUMN section TEXT DEFAULT ''",
    "ALTER TABLE transactions ADD COLUMN row_index INTEGER DEFAULT 0",
    "ALTER TABLE transactions ADD COLUMN original_notes TEXT DEFAULT ''",
    "ALTER TABLE sessions ADD COLUMN bank_balance REAL",
    "ALTER TABLE sessions ADD COLUMN reconciled_balance REAL",
    "ALTER TABLE sessions ADD COLUMN company_balance REAL",
    "ALTER TABLE sessions ADD COLUMN difference REAL",
    "ALTER TABLE sessions ADD COLUMN current_index INTEGER DEFAULT 0",
    "ALTER TABLE sessions ADD COLUMN reviewed_count INTEGER DEFAULT 0",
]


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
    """Run schema, migrations, and seed default rules."""
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()

    # Run migrations (ignore if column already exists)
    for migration in _MIGRATIONS:
        try:
            conn.execute(migration)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists

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


# --- Full session persistence (survives bot restarts) ---


def save_full_session(session_id, filename, result, current_index=0, reviewed_count=0):
    """Save complete session data to SQLite so it survives bot restarts."""
    conn = get_connection()

    conn.execute(
        """INSERT OR REPLACE INTO sessions
           (id, filename, total_transactions, auto_categorized, needs_review,
            bank_balance, reconciled_balance, company_balance, difference,
            current_index, reviewed_count, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
        (session_id, filename, len(result.all_transactions),
         len(result.categorized), len(result.uncategorized),
         result.bank_balance, result.reconciled_balance,
         result.company_balance, result.difference,
         current_index, reviewed_count),
    )

    # Clear old transactions for this session
    conn.execute("DELETE FROM transactions WHERE session_id = ?", (session_id,))

    # Save all transactions with section info
    section_map = [
        ("debit_bank", result.debit_transactions),
        ("credit_bank", result.credit_transactions),
        ("debit_company", result.company_debits),
        ("credit_company", result.company_credits),
    ]

    for section, txns in section_map:
        for txn in txns:
            conn.execute(
                """INSERT INTO transactions
                   (session_id, date, description, value, type, section,
                    category, note, categorization_method, row_index, original_notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, txn.date, txn.description, txn.value, txn.type, section,
                 txn.category or "", txn.note or "", txn.confidence,
                 txn.row_index, txn.original_notes or ""),
            )

    conn.commit()
    logger.info(f"Full session {session_id} saved to SQLite ({len(result.all_transactions)} transactions)")


def load_latest_session() -> dict | None:
    """Load the most recent active session from SQLite. Returns dict matching context.user_data format."""
    conn = get_connection()

    row = conn.execute(
        "SELECT * FROM sessions WHERE status = 'active' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()

    if not row:
        return None

    session_id = row["id"]

    txn_rows = conn.execute(
        "SELECT * FROM transactions WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()

    if not txn_rows:
        return None

    from bot.accounting.models import Transaction, ReconciliationResult

    debit_bank = []
    credit_bank = []
    debit_company = []
    credit_company = []

    for tr in txn_rows:
        txn = Transaction(
            date=tr["date"],
            description=tr["description"],
            value=tr["value"],
            type=tr["type"],
            row_index=tr["row_index"] if tr["row_index"] else 0,
            original_notes=tr["original_notes"] if tr["original_notes"] else "",
            category=tr["category"] if tr["category"] else None,
            note=tr["note"] if tr["note"] else None,
            confidence=tr["categorization_method"] or "unknown",
        )
        section = tr["section"] if tr["section"] else ""
        if section == "debit_bank":
            debit_bank.append(txn)
        elif section == "credit_bank":
            credit_bank.append(txn)
        elif section == "debit_company":
            debit_company.append(txn)
        elif section == "credit_company":
            credit_company.append(txn)

    result = ReconciliationResult(
        filename=row["filename"],
        bank_balance=row["bank_balance"],
        reconciled_balance=row["reconciled_balance"],
        company_balance=row["company_balance"],
        difference=row["difference"],
        debit_transactions=debit_bank,
        credit_transactions=credit_bank,
        company_debits=debit_company,
        company_credits=credit_company,
    )

    pending_review = [t for t in result.all_transactions if t.confidence == "unknown"]

    logger.info(f"Restored session {session_id} from SQLite ({len(result.all_transactions)} txns, {len(pending_review)} pending)")

    return {
        "id": session_id,
        "filename": row["filename"],
        "result": result,
        "pending_review": pending_review,
        "current_index": row["current_index"] if row["current_index"] else 0,
        "reviewed_count": row["reviewed_count"] if row["reviewed_count"] else 0,
    }


def update_transaction_category(session_id: str, date: str, description: str,
                                value: float, category: str, note: str):
    """Update a transaction's category in SQLite after user review."""
    conn = get_connection()
    conn.execute(
        """UPDATE transactions SET category=?, note=?, categorization_method='user'
           WHERE session_id=? AND date=? AND description=? AND ABS(value - ?) < 0.01""",
        (category, note, session_id, date, description, value),
    )
    conn.commit()


def update_session_review_state(session_id: str, current_index: int, reviewed_count: int):
    """Update review progress in SQLite."""
    conn = get_connection()
    conn.execute(
        "UPDATE sessions SET current_index=?, reviewed_count=?, needs_review=needs_review WHERE id=?",
        (current_index, reviewed_count, session_id),
    )
    conn.commit()
