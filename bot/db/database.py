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

            -- Workout sessions
            CREATE TABLE IF NOT EXISTS workouts (
                id SERIAL PRIMARY KEY,
                user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                duration_minutes INT,
                rpe REAL,
                notes TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- Individual exercises within a workout
            CREATE TABLE IF NOT EXISTS workout_exercises (
                id SERIAL PRIMARY KEY,
                workout_id INT NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
                exercise_name TEXT NOT NULL,
                movement_pattern TEXT,
                sets INT,
                reps TEXT,
                weight REAL,
                weight_unit TEXT DEFAULT 'kg',
                rpe REAL,
                notes TEXT,
                sort_order INT DEFAULT 0
            );

            -- Body metrics (weight, body fat, 1RMs, measurements)
            CREATE TABLE IF NOT EXISTS body_metrics (
                id SERIAL PRIMARY KEY,
                user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                metric_type TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT,
                recorded_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- User fitness profile (goals, experience, limitations)
            CREATE TABLE IF NOT EXISTS fitness_profiles (
                id SERIAL PRIMARY KEY,
                user_id INT UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                fitness_goal TEXT,
                experience_level TEXT DEFAULT 'intermediate',
                training_days_per_week INT DEFAULT 3,
                limitations TEXT,
                preferred_style TEXT,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- Workout streaks (separate from task streaks)
            CREATE TABLE IF NOT EXISTS workout_streaks (
                id SERIAL PRIMARY KEY,
                user_id INT UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                current_streak INT DEFAULT 0,
                longest_streak INT DEFAULT 0,
                last_workout_date DATE,
                weekly_target INT DEFAULT 3
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON tasks(user_id, status);
            CREATE INDEX IF NOT EXISTS idx_tasks_user_due ON tasks(user_id, due_date) WHERE status = 'active';
            CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, id);
            CREATE INDEX IF NOT EXISTS idx_usage_user_date ON usage(user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_check_ins_user_date ON check_ins(user_id, check_in_date);
            CREATE INDEX IF NOT EXISTS idx_nudge_log_user_date ON nudge_log(user_id, nudged_at);
            CREATE INDEX IF NOT EXISTS idx_workouts_user ON workouts(user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_workout_exercises_workout ON workout_exercises(workout_id);
            CREATE INDEX IF NOT EXISTS idx_body_metrics_user ON body_metrics(user_id, metric_type, recorded_at);

            -- Peptide protocols
            CREATE TABLE IF NOT EXISTS peptide_protocols (
                id SERIAL PRIMARY KEY,
                user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                peptide_name TEXT NOT NULL,
                dose_amount REAL,
                dose_unit TEXT DEFAULT 'mcg',
                frequency TEXT,
                route TEXT DEFAULT 'subcutaneous',
                cycle_start DATE,
                cycle_end DATE,
                status TEXT DEFAULT 'active',
                notes TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- Peptide dose log
            CREATE TABLE IF NOT EXISTS peptide_logs (
                id SERIAL PRIMARY KEY,
                protocol_id INT NOT NULL REFERENCES peptide_protocols(id) ON DELETE CASCADE,
                user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                administered_at TIMESTAMPTZ DEFAULT NOW(),
                dose_amount REAL,
                site TEXT,
                notes TEXT
            );

            -- Supplement stack
            CREATE TABLE IF NOT EXISTS supplements (
                id SERIAL PRIMARY KEY,
                user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                supplement_name TEXT NOT NULL,
                dose_amount REAL,
                dose_unit TEXT,
                frequency TEXT DEFAULT 'daily',
                timing TEXT,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- Supplement logs
            CREATE TABLE IF NOT EXISTS supplement_logs (
                id SERIAL PRIMARY KEY,
                supplement_id INT NOT NULL REFERENCES supplements(id) ON DELETE CASCADE,
                user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                taken_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- Bloodwork panels
            CREATE TABLE IF NOT EXISTS bloodwork (
                id SERIAL PRIMARY KEY,
                user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                test_date DATE NOT NULL,
                lab_name TEXT,
                notes TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- Individual biomarkers within a bloodwork panel
            CREATE TABLE IF NOT EXISTS biomarkers (
                id SERIAL PRIMARY KEY,
                bloodwork_id INT NOT NULL REFERENCES bloodwork(id) ON DELETE CASCADE,
                user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                marker_name TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT,
                reference_low REAL,
                reference_high REAL,
                flag TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_peptide_protocols_user ON peptide_protocols(user_id, status);
            CREATE INDEX IF NOT EXISTS idx_peptide_logs_user ON peptide_logs(user_id, administered_at);
            CREATE INDEX IF NOT EXISTS idx_supplements_user ON supplements(user_id, status);
            CREATE INDEX IF NOT EXISTS idx_bloodwork_user ON bloodwork(user_id, test_date);
            CREATE INDEX IF NOT EXISTS idx_biomarkers_user ON biomarkers(user_id, marker_name);

            -- WHOOP OAuth tokens
            CREATE TABLE IF NOT EXISTS whoop_tokens (
                id SERIAL PRIMARY KEY,
                user_id INT UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                access_token TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                scopes TEXT,
                whoop_user_id BIGINT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- WHOOP daily data (cached from API)
            CREATE TABLE IF NOT EXISTS whoop_daily (
                id SERIAL PRIMARY KEY,
                user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                cycle_date DATE NOT NULL,
                recovery_score INT,
                hrv_rmssd REAL,
                resting_hr INT,
                spo2 REAL,
                skin_temp REAL,
                sleep_performance INT,
                sleep_efficiency REAL,
                deep_sleep_minutes INT,
                rem_sleep_minutes INT,
                light_sleep_minutes INT,
                respiratory_rate REAL,
                daily_strain REAL,
                calories_kj REAL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(user_id, cycle_date)
            );

            CREATE INDEX IF NOT EXISTS idx_whoop_daily_user ON whoop_daily(user_id, cycle_date);

            -- Active workout sessions (interactive tracking)
            CREATE TABLE IF NOT EXISTS workout_sessions (
                id SERIAL PRIMARY KEY,
                user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                chat_id BIGINT NOT NULL DEFAULT 0,
                title TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                total_exercises INT DEFAULT 0,
                completed_exercises INT DEFAULT 0,
                started_at TIMESTAMPTZ DEFAULT NOW(),
                completed_at TIMESTAMPTZ,
                rpe REAL,
                workout_id INT REFERENCES workouts(id)
            );

            -- Exercises within an active session
            CREATE TABLE IF NOT EXISTS session_exercises (
                id SERIAL PRIMARY KEY,
                session_id INT NOT NULL REFERENCES workout_sessions(id) ON DELETE CASCADE,
                exercise_name TEXT NOT NULL,
                movement_pattern TEXT,
                target_sets INT NOT NULL DEFAULT 4,
                target_reps TEXT NOT NULL DEFAULT '8',
                target_weight REAL,
                weight_unit TEXT DEFAULT 'kg',
                target_rpe REAL,
                sets_completed INT DEFAULT 0,
                message_id BIGINT,
                sort_order INT DEFAULT 0,
                notes TEXT
            );

            -- Track current exercise position and the single card message
            ALTER TABLE workout_sessions ADD COLUMN IF NOT EXISTS current_exercise_idx INT DEFAULT 0;
            ALTER TABLE workout_sessions ADD COLUMN IF NOT EXISTS card_message_id BIGINT;

            CREATE INDEX IF NOT EXISTS idx_workout_sessions_user ON workout_sessions(user_id, status);
            CREATE INDEX IF NOT EXISTS idx_session_exercises_session ON session_exercises(session_id);

            -- User memory (Zoe's persistent knowledge about each user)
            CREATE TABLE IF NOT EXISTS user_memory (
                id SERIAL PRIMARY KEY,
                user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                category TEXT NOT NULL DEFAULT 'general',
                content TEXT NOT NULL,
                source TEXT DEFAULT 'conversation',
                confidence REAL DEFAULT 1.0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- Response feedback (thumbs up/down on AI responses)
            CREATE TABLE IF NOT EXISTS response_feedback (
                id SERIAL PRIMARY KEY,
                user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                message_text TEXT,
                feedback TEXT NOT NULL,
                context TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_user_memory_user ON user_memory(user_id, category);
            CREATE INDEX IF NOT EXISTS idx_response_feedback_user ON response_feedback(user_id, created_at);

            -- Onboarding fields
            ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_number TEXT;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_completed BOOLEAN DEFAULT FALSE;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone ON users(phone_number) WHERE phone_number IS NOT NULL;

            -- Additional performance indexes (audit Feb 2026)
            CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks(due_date) WHERE status = 'active' AND due_date IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_conv_created ON conversations(user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_peptide_logs_protocol ON peptide_logs(protocol_id);
            CREATE INDEX IF NOT EXISTS idx_supplement_logs_supplement ON supplement_logs(supplement_id);
            CREATE INDEX IF NOT EXISTS idx_biomarkers_bloodwork ON biomarkers(bloodwork_id);
            CREATE INDEX IF NOT EXISTS idx_workout_sessions_started ON workout_sessions(user_id, started_at);
            CREATE INDEX IF NOT EXISTS idx_workout_exercises_name ON workout_exercises(exercise_name);

            -- ═══════════════════════════════════════════════════════
            -- KNOWLEDGE BASE SYSTEM
            -- ═══════════════════════════════════════════════════════

            -- General knowledge base (expert protocols, podcast summaries, research)
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id SERIAL PRIMARY KEY,
                category TEXT NOT NULL,
                topic TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT,
                source_episode TEXT,
                tags TEXT[],
                evidence_level TEXT DEFAULT 'C',
                search_vector TSVECTOR,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_kb_category ON knowledge_base(category);
            CREATE INDEX IF NOT EXISTS idx_kb_source ON knowledge_base(source);
            CREATE INDEX IF NOT EXISTS idx_kb_search ON knowledge_base USING GIN(search_vector);
            CREATE INDEX IF NOT EXISTS idx_kb_tags ON knowledge_base USING GIN(tags);

            -- Peptide reference database (47 compounds)
            CREATE TABLE IF NOT EXISTS peptide_reference (
                id SERIAL PRIMARY KEY,
                slug TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                mechanism TEXT,
                benefits TEXT[],
                categories TEXT[],
                routes TEXT[],
                standard_dose TEXT,
                standard_frequency TEXT,
                standard_duration TEXT,
                dosage_notes TEXT,
                stack_suggestions TEXT[],
                side_effects TEXT[],
                contraindications TEXT[],
                evidence_level TEXT DEFAULT 'C',
                research_summary TEXT,
                half_life TEXT,
                beginner_friendly BOOLEAN DEFAULT FALSE,
                search_vector TSVECTOR,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_peptide_ref_search ON peptide_reference USING GIN(search_vector);
            CREATE INDEX IF NOT EXISTS idx_peptide_ref_categories ON peptide_reference USING GIN(categories);

            -- Supplement reference database
            CREATE TABLE IF NOT EXISTS supplement_reference (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                name_normalized TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                standard_dose TEXT NOT NULL,
                timing TEXT,
                benefits TEXT[],
                mechanism TEXT,
                interactions TEXT[],
                side_effects TEXT[],
                cycle_recommendation TEXT,
                evidence_level TEXT DEFAULT 'B',
                notes TEXT,
                search_vector TSVECTOR,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_supp_ref_search ON supplement_reference USING GIN(search_vector);
            CREATE INDEX IF NOT EXISTS idx_supp_ref_name ON supplement_reference(name_normalized);

            -- Biomarker reference (optimal vs lab ranges)
            CREATE TABLE IF NOT EXISTS biomarker_reference (
                id SERIAL PRIMARY KEY,
                marker_name TEXT UNIQUE NOT NULL,
                marker_name_normalized TEXT NOT NULL,
                unit TEXT NOT NULL,
                category TEXT NOT NULL,
                lab_range_low REAL,
                lab_range_high REAL,
                optimal_range_low REAL,
                optimal_range_high REAL,
                interpretation_low TEXT,
                interpretation_high TEXT,
                optimization_tips TEXT,
                related_markers TEXT[],
                notes TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_biomarker_ref_name ON biomarker_reference(marker_name_normalized);
            CREATE INDEX IF NOT EXISTS idx_biomarker_ref_category ON biomarker_reference(category);

            -- Food reference (blood type diet, 118 foods)
            CREATE TABLE IF NOT EXISTS food_reference (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                name_normalized TEXT NOT NULL,
                category TEXT NOT NULL,
                calories_per_100g REAL NOT NULL,
                protein_g REAL DEFAULT 0,
                carbs_g REAL DEFAULT 0,
                fat_g REAL DEFAULT 0,
                fiber_g REAL DEFAULT 0,
                blood_type_o TEXT NOT NULL,
                blood_type_a TEXT NOT NULL,
                blood_type_b TEXT NOT NULL,
                blood_type_ab TEXT NOT NULL,
                serving_size_g REAL,
                serving_description TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_food_ref_name ON food_reference(name_normalized);
            CREATE INDEX IF NOT EXISTS idx_food_ref_category ON food_reference(category);

            -- RSS feed sync tracking (for research auto-updates)
            CREATE TABLE IF NOT EXISTS knowledge_sync_log (
                id SERIAL PRIMARY KEY,
                source TEXT NOT NULL,
                feed_url TEXT,
                last_entry_id TEXT,
                last_sync_at TIMESTAMPTZ DEFAULT NOW(),
                entries_added INT DEFAULT 0
            );
        """)
    logger.info("PostgreSQL schema initialized")


def close():
    """Close the connection pool."""
    global _pool
    if _pool:
        _pool.closeall()
        _pool = None
        logger.info("PostgreSQL connection pool closed")
