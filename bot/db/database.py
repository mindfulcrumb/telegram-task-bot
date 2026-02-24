"""PostgreSQL database connection and schema management."""
import os
import logging
from contextlib import contextmanager

import psycopg2
import psycopg2.pool
import psycopg2.extras

logger = logging.getLogger(__name__)

_pool = None


def get_pool():
    """Get or create the connection pool."""
    global _pool
    if _pool is None:
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL not set")
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            dsn=database_url,
        )
        logger.info("PostgreSQL connection pool created")
    return _pool


@contextmanager
def get_conn():
    """Get a connection from the pool (context manager)."""
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


@contextmanager
def get_cursor(dict_cursor=True):
    """Get a cursor from a pooled connection (context manager)."""
    with get_conn() as conn:
        factory = psycopg2.extras.RealDictCursor if dict_cursor else None
        cur = conn.cursor(cursor_factory=factory)
        try:
            yield cur
        finally:
            cur.close()


def initialize():
    """Create all tables if they don't exist."""
    with get_cursor(dict_cursor=False) as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_user_id BIGINT UNIQUE NOT NULL,
                telegram_username TEXT,
                first_name TEXT,
                timezone TEXT DEFAULT 'UTC',
                briefing_hour INT DEFAULT 8,
                tier TEXT DEFAULT 'free',
                is_admin BOOLEAN DEFAULT FALSE,
                stripe_customer_id TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                last_active TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id SERIAL PRIMARY KEY,
                user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                category TEXT DEFAULT 'Personal',
                priority TEXT DEFAULT 'Medium',
                due_date DATE,
                status TEXT DEFAULT 'active',
                reminder_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                completed_at TIMESTAMPTZ
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id SERIAL PRIMARY KEY,
                user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS usage (
                id SERIAL PRIMARY KEY,
                user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                action TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- Check-ins: evening accountability
            CREATE TABLE IF NOT EXISTS check_ins (
                id SERIAL PRIMARY KEY,
                user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                task_id INT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                check_in_date DATE NOT NULL DEFAULT CURRENT_DATE,
                completed BOOLEAN,
                asked_at TIMESTAMPTZ DEFAULT NOW(),
                responded_at TIMESTAMPTZ,
                UNIQUE(user_id, task_id, check_in_date)
            );

            -- Streaks: gamification
            CREATE TABLE IF NOT EXISTS streaks (
                id SERIAL PRIMARY KEY,
                user_id INT UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                current_streak INT DEFAULT 0,
                longest_streak INT DEFAULT 0,
                last_completion_date DATE
            );

            -- Nudge dedup (replaces in-memory dict)
            CREATE TABLE IF NOT EXISTS nudge_log (
                id SERIAL PRIMARY KEY,
                user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                task_id INT NOT NULL,
                nudge_type TEXT NOT NULL,
                nudged_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- Add check-in hour to users
            ALTER TABLE users ADD COLUMN IF NOT EXISTS check_in_hour INT DEFAULT 20;

            -- Add recurrence to tasks (daily, weekly, monthly, weekdays, or null)
            ALTER TABLE tasks ADD COLUMN IF NOT EXISTS recurrence TEXT;

            -- Google Calendar iCal URL for calendar sync
            ALTER TABLE users ADD COLUMN IF NOT EXISTS google_calendar_url TEXT;

            CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON tasks(user_id, status);
            CREATE INDEX IF NOT EXISTS idx_tasks_user_due ON tasks(user_id, due_date) WHERE status = 'active';
            CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, id);
            CREATE INDEX IF NOT EXISTS idx_usage_user_date ON usage(user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_check_ins_user_date ON check_ins(user_id, check_in_date);
            CREATE INDEX IF NOT EXISTS idx_nudge_log_user_date ON nudge_log(user_id, nudged_at);
        """)
    logger.info("PostgreSQL schema initialized")


def close():
    """Close the connection pool."""
    global _pool
    if _pool:
        _pool.closeall()
        _pool = None
        logger.info("PostgreSQL connection pool closed")
