"""Seed the owner's (William's) current training program into user memories.

This ensures Zoe knows the owner's mesocycle structure, current working weights,
movement pattern schedule, and progression targets. Runs on startup, idempotent —
skips if memories with source 'program_seed' already exist for the owner.
"""
import logging
import os

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)

# Owner's Telegram user ID (from ADMIN_USER_IDS)
OWNER_TELEGRAM_ID = int(os.environ.get("ADMIN_USER_IDS", "1631254047").split(",")[0])

_SEED_SOURCE = "program_seed"


def _get_owner_db_id() -> int | None:
    """Look up the owner's database ID from their Telegram user ID."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT id FROM users WHERE telegram_user_id = %s LIMIT 1",
            (OWNER_TELEGRAM_ID,),
        )
        row = cur.fetchone()
        return row["id"] if row else None


def _already_seeded(owner_db_id: int) -> bool:
    """Check if program data has already been loaded for the owner."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT 1 FROM user_memory WHERE user_id = %s AND source = %s LIMIT 1",
            (owner_db_id, _SEED_SOURCE),
        )
        return cur.fetchone() is not None


def _save(owner_db_id: int, content: str, category: str):
    """Insert a memory for the owner if it doesn't already exist."""
    from bot.services.memory_service import save_memory
    save_memory(
        user_id=owner_db_id,
        content=content,
        category=category,
        source=_SEED_SOURCE,
        confidence=1.0,
    )


# ─── Owner's Current Training Program ──────────────────────────────

PROGRAM_MEMORIES = [
    # ── Program Structure ──
    (
        "Current mesocycle: Block 1, Week 3 of 6-week strength+hypertrophy program. "
        "Training 4-5x/week: Mon (heavy squat+bench), Tue (active recovery), "
        "Wed (heavy pull+press), Fri (KB+bodyweight conditioning), Sat (HRV-guided recovery). "
        "Deload scheduled for Week 6.",
        "fitness",
    ),
    # ── Working Weights (current) ──
    (
        "Current working weights (as of Week 3): Back Squat 90kg top set (ascending 60→75→85→90kg), "
        "Deadlift 130kg top set (ascending 60→80→100→120→130kg), "
        "Bench Press 75kg top set (ascending 40→55→65→75kg), "
        "L-sit pull-ups bodyweight for 3x6-8. All compound lifts use ascending weight scheme.",
        "fitness",
    ),
    # ── Squat + Bench Day (Monday) ──
    (
        "Monday session structure: Back Squat 5x5 ascending + L-sit pull-ups superset, "
        "Bench Press 4x6 ascending + face pulls superset, "
        "8-min AMRAP retest every 2-3 weeks (benchmark tracking). "
        "Always includes Pallof press superset with squats for anti-rotation.",
        "fitness",
    ),
    # ── Heavy Pull + Press Day (Wednesday) ──
    (
        "Wednesday session structure: Deadlift 5x5 ascending (top set 130kg), "
        "Bench 8x4 ascending + face pulls superset, "
        "Pendlay rows, hanging knee raises, 3-round metcon finisher for time. "
        "Thoracic rotations superset with deadlifts.",
        "fitness",
    ),
    # ── KB + Bodyweight Day (Friday) ──
    (
        "Friday session structure: E2MOM strength (goblet squat + KB press), "
        "10-min AMRAP (pull-ups + KB RDL), farmer's carry finisher. "
        "Higher conditioning focus, moderate load. Sleep alert check included.",
        "fitness",
    ),
    # ── Active Recovery Days (Tuesday) ──
    (
        "Tuesday active recovery: Zone 1 only (HR under 130), 10min easy row, "
        "mobility circuit (good mornings, goblet squat hold, KB halos, dead hang, inchworms), "
        "targeted foam rolling (priority: right calf — history of cramping). "
        "Week 3 introduced rotational block: landmine rotations 3x10 @20kg each side, "
        "Pallof press 3x10 each side, thoracic spine rotations, goblet squat with rotation.",
        "fitness",
    ),
    # ── Saturday HRV-Guided Recovery ──
    (
        "Saturday HRV-guided session: Recovery based on HRV reading. "
        "HRV baseline ~43. If HRV below baseline: Zone 2 cardio HR under 130, "
        "mobility+stretching, foam roll full body, optional sauna. "
        "Includes week summary review with strength progression table and Week+1 targets.",
        "fitness",
    ),
    # ── Progression Targets ──
    (
        "Week 3 progression targets: Squat 92.5kg top set (up from 90kg), "
        "Deadlift 132.5-135kg top set (up from 130kg), "
        "Bench 77.5kg top set (up from 75kg). "
        "Aim to add 2.5kg upper / 5kg lower per week. "
        "If RPE exceeds 9 on top set, hold weight and add reps instead.",
        "fitness",
    ),
    # ── AMRAP Benchmarks ──
    (
        "AMRAP benchmark tracking: 8-min AMRAP (5 pull-ups + 10 push-ups + 15 air squats). "
        "Retest every 2-3 weeks on Monday. Track rounds+reps to measure conditioning progress. "
        "Week 1 baseline established, Week 3 retest due.",
        "fitness",
    ),
    # ── Key Training Notes ──
    (
        "Training notes: Right calf has history of cramping — prioritize foam rolling. "
        "Rotational work introduced in Week 3 as new dedicated block. "
        "All sessions include warm-up (2-3min row + movement prep + activation sets) "
        "and cool-down (targeted stretches + diaphragmatic breathing). "
        "Prehab: face pulls every upper body day, Pallof press every squat day.",
        "fitness",
    ),
    # ── Coaching Preferences ──
    (
        "Prefers detailed workout plans with HOW TO DO IT / WHY / DON'T DO THIS for each exercise. "
        "Wants ascending weight schemes on all compounds, never flat weight. "
        "Values rotational work — wants it in every session. "
        "Uses WHOOP for HRV tracking, adjusts session intensity based on recovery score.",
        "coaching",
    ),
    # ── Goals ──
    (
        "Primary fitness goals: Build strength on the big 3 (squat, deadlift, bench), "
        "improve athletic conditioning, maintain joint health through rotational work and prehab. "
        "Secondary: body recomposition (maintain weight while increasing lifts). "
        "Long-term: sustainable training that prevents injury and builds real-world athleticism.",
        "goal",
    ),
]


def seed_owner_program():
    """Load the owner's training program into user memories."""
    owner_db_id = _get_owner_db_id()
    if not owner_db_id:
        logger.info(f"Owner (telegram_id={OWNER_TELEGRAM_ID}) not in users table yet — skipping seed")
        return

    if _already_seeded(owner_db_id):
        logger.info("Owner program already seeded — skipping")
        return

    count = 0
    for content, category in PROGRAM_MEMORIES:
        try:
            _save(owner_db_id, content, category)
            count += 1
        except Exception as e:
            logger.warning(f"Failed to seed program memory: {e}")

    logger.info(f"Seeded {count} program memories for owner (db_id={owner_db_id}, telegram_id={OWNER_TELEGRAM_ID})")


def seed_all_owner():
    """Entry point called from main_v2.py."""
    try:
        seed_owner_program()
    except Exception as e:
        logger.error(f"Owner program seeding failed: {type(e).__name__}: {e}")
