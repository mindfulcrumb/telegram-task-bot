"""Seed the owner's training program into the structured workout_programs table.

Migrated from text-only user_memory entries to structured program_json.
Runs on startup, idempotent — skips if an active program already exists.
Also seeds coaching preference memories that don't belong in the program structure.
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


def _has_active_program(owner_db_id: int) -> bool:
    """Check if owner already has an active structured program."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT 1 FROM workout_programs WHERE user_id = %s AND status = 'active' LIMIT 1",
            (owner_db_id,),
        )
        return cur.fetchone() is not None


# ─── Owner's Structured Program ──────────────────────────────

OWNER_PROGRAM = {
    "title": "Block 1 — Strength + Hypertrophy",
    "goal": "strength",
    "duration_weeks": 6,
    "days_per_week": 5,
    "program_json": {
        "weeks": {
            "1": {
                "monday": {
                    "title": "Heavy Squat + Bench",
                    "type": "strength",
                    "exercises": [
                        {
                            "name": "Back Squat",
                            "sets": 5, "reps": "5",
                            "weight_scheme": "ascending",
                            "weights": [60, 75, 85, 90, 90],
                            "unit": "kg",
                            "rpe_target": 8,
                            "notes": "Full depth, brace hard on top sets. 3s eccentric on warm-ups.",
                            "superset_with": "L-sit Pull-ups"
                        },
                        {
                            "name": "L-sit Pull-ups",
                            "sets": 3, "reps": "6-8",
                            "weight_scheme": "bodyweight",
                            "unit": "kg",
                            "rpe_target": 7,
                            "notes": "Full L-sit hold throughout. Slow eccentric."
                        },
                        {
                            "name": "Bench Press",
                            "sets": 4, "reps": "6",
                            "weight_scheme": "ascending",
                            "weights": [40, 55, 65, 75],
                            "unit": "kg",
                            "rpe_target": 8,
                            "notes": "Retract scapulae, arch, leg drive.",
                            "superset_with": "Face Pulls"
                        },
                        {
                            "name": "Face Pulls",
                            "sets": 4, "reps": "15",
                            "weight_scheme": "flat",
                            "unit": "kg",
                            "rpe_target": 6,
                            "notes": "External rotation at top. Prehab — every upper day."
                        },
                        {
                            "name": "Pallof Press",
                            "sets": 3, "reps": "10 each side",
                            "weight_scheme": "flat",
                            "unit": "kg",
                            "rpe_target": 7,
                            "notes": "Anti-rotation. Superset with squats."
                        }
                    ],
                    "warmup": "2-3 min row + movement prep (hip circles, leg swings, band pull-aparts) + 2 activation sets",
                    "cooldown": "Targeted stretches (hip flexors, quads, pecs) + 2 min diaphragmatic breathing",
                    "finisher": "8-min AMRAP: 5 pull-ups + 10 push-ups + 15 air squats (benchmark — retest every 2-3 weeks)"
                },
                "tuesday": {
                    "title": "Active Recovery",
                    "type": "recovery",
                    "exercises": [
                        {
                            "name": "Easy Row",
                            "sets": 1, "reps": "10 min",
                            "notes": "Zone 1 only. HR under 130."
                        },
                        {
                            "name": "Good Mornings",
                            "sets": 2, "reps": "10",
                            "notes": "Light, mobility focus"
                        },
                        {
                            "name": "Goblet Squat Hold",
                            "sets": 3, "reps": "30s",
                            "notes": "Deep squat, open hips"
                        },
                        {
                            "name": "KB Halos",
                            "sets": 2, "reps": "8 each direction",
                            "notes": "Light KB, shoulder mobility"
                        },
                        {
                            "name": "Dead Hang",
                            "sets": 3, "reps": "30s",
                            "notes": "Decompress spine, grip work"
                        },
                        {
                            "name": "Foam Rolling",
                            "sets": 1, "reps": "5 min",
                            "notes": "Priority: right calf (history of cramping), then quads, lats"
                        }
                    ],
                    "warmup": "5 min easy walk or row",
                    "notes": "Zone 1 only. HR ceiling 130. No intensity."
                },
                "wednesday": {
                    "title": "Heavy Pull + Press",
                    "type": "strength",
                    "exercises": [
                        {
                            "name": "Deadlift",
                            "sets": 5, "reps": "5",
                            "weight_scheme": "ascending",
                            "weights": [60, 80, 100, 120, 130],
                            "unit": "kg",
                            "rpe_target": 8,
                            "notes": "Reset each rep. Brace before pull. No bounce.",
                            "superset_with": "Thoracic Rotations"
                        },
                        {
                            "name": "Thoracic Rotations",
                            "sets": 3, "reps": "8 each side",
                            "notes": "Between deadlift sets. Maintain mobility."
                        },
                        {
                            "name": "Bench Press",
                            "sets": 8, "reps": "4",
                            "weight_scheme": "ascending",
                            "weights": [40, 50, 55, 60, 60, 65, 65, 70],
                            "unit": "kg",
                            "rpe_target": 7,
                            "notes": "Speed work. Explosive concentric.",
                            "superset_with": "Face Pulls"
                        },
                        {
                            "name": "Pendlay Rows",
                            "sets": 4, "reps": "6-8",
                            "weight_scheme": "ascending",
                            "unit": "kg",
                            "rpe_target": 8,
                            "notes": "Dead stop each rep. Strict form."
                        },
                        {
                            "name": "Hanging Knee Raises",
                            "sets": 3, "reps": "12",
                            "notes": "Control the swing. Pause at top."
                        }
                    ],
                    "warmup": "2-3 min row + hip hinges + band pull-aparts + 2 activation sets",
                    "cooldown": "Hamstring stretch, thoracic foam roll, 2 min breathing",
                    "finisher": "3-round metcon for time: 10 KB swings + 10 burpees + 200m row"
                },
                "friday": {
                    "title": "KB + Bodyweight Conditioning",
                    "type": "conditioning",
                    "exercises": [
                        {
                            "name": "Goblet Squat",
                            "sets": 5, "reps": "5",
                            "notes": "E2MOM format. Moderate weight.",
                            "superset_with": "KB Press"
                        },
                        {
                            "name": "KB Press",
                            "sets": 5, "reps": "5 each arm",
                            "notes": "E2MOM format. Strict press, no push."
                        },
                        {
                            "name": "Pull-ups",
                            "sets": 1, "reps": "AMRAP in 10 min",
                            "notes": "10-min AMRAP with KB RDL.",
                            "superset_with": "KB RDL"
                        },
                        {
                            "name": "KB RDL",
                            "sets": 1, "reps": "AMRAP in 10 min",
                            "notes": "Alternate with pull-ups. 10 reps each round."
                        },
                        {
                            "name": "Farmer's Carry",
                            "sets": 3, "reps": "40m",
                            "notes": "Heavy. Finisher. Shoulders packed, core tight."
                        }
                    ],
                    "warmup": "3 min jump rope + mobility flow",
                    "cooldown": "Full body stretch 5 min",
                    "notes": "Higher conditioning focus. Moderate load. Check sleep score before session."
                },
                "saturday": {
                    "title": "HRV-Guided Recovery",
                    "type": "recovery",
                    "exercises": [
                        {
                            "name": "Zone 2 Cardio",
                            "sets": 1, "reps": "20-30 min",
                            "notes": "HR under 130. Row, bike, or walk. Only if HRV below baseline (~43)."
                        },
                        {
                            "name": "Full Body Mobility",
                            "sets": 1, "reps": "15 min",
                            "notes": "World's greatest stretch, 90/90, couch stretch, thoracic extensions"
                        },
                        {
                            "name": "Foam Rolling",
                            "sets": 1, "reps": "10 min",
                            "notes": "Full body. Priority: right calf, quads, lats, thoracic."
                        }
                    ],
                    "notes": "HRV baseline ~43. If green (HRV above baseline): light play, sport, active fun. If yellow/red: Zone 2 + mobility only. Week summary review."
                }
            }
        },
        "progression_rules": {
            "upper_increment_kg": 2.5,
            "lower_increment_kg": 5,
            "rpe_ceiling": 9,
            "deload_week": 6,
            "rule": "If RPE exceeds 9 on top set, hold weight and add reps instead of adding load."
        },
        "coaching_notes": (
            "Ascending weight scheme on ALL compounds — never flat weight. "
            "Right calf has history of cramping — always prioritize foam rolling. "
            "Face pulls every upper body day, Pallof press every squat day. "
            "Rotational work in every session. "
            "AMRAP benchmark retest every 2-3 weeks on Monday. "
            "Uses WHOOP for HRV — adjust intensity based on recovery score."
        )
    },
    "notes": "Block 1 strength+hypertrophy. Weeks repeat (linear progression via weight increments). Week 6 = deload at 50% volume."
}

# Coaching preferences (stay as user_memory — these are personal, not program-specific)
COACHING_MEMORIES = [
    (
        "Prefers detailed workout plans with HOW TO DO IT / WHY / DON'T DO THIS for each exercise. "
        "Wants ascending weight schemes on all compounds, never flat weight. "
        "Values rotational work — wants it in every session. "
        "Uses WHOOP for HRV tracking, adjusts session intensity based on recovery score.",
        "coaching",
    ),
    (
        "Primary fitness goals: Build strength on the big 3 (squat, deadlift, bench), "
        "improve athletic conditioning, maintain joint health through rotational work and prehab. "
        "Secondary: body recomposition (maintain weight while increasing lifts). "
        "Long-term: sustainable training that prevents injury and builds real-world athleticism.",
        "goal",
    ),
]


def seed_owner_program():
    """Load the owner's training program into the structured workout_programs table."""
    owner_db_id = _get_owner_db_id()
    if not owner_db_id:
        logger.info(f"Owner (telegram_id={OWNER_TELEGRAM_ID}) not in users table yet — skipping seed")
        return

    # Seed structured program if none exists
    if not _has_active_program(owner_db_id):
        from bot.services import program_service
        program = program_service.create_program(
            user_id=owner_db_id,
            title=OWNER_PROGRAM["title"],
            goal=OWNER_PROGRAM["goal"],
            duration_weeks=OWNER_PROGRAM["duration_weeks"],
            days_per_week=OWNER_PROGRAM["days_per_week"],
            program_json=OWNER_PROGRAM["program_json"],
            notes=OWNER_PROGRAM["notes"],
        )
        logger.info(f"Seeded structured program '{program['title']}' (id={program['id']}) for owner")
    else:
        logger.info("Owner already has an active program — skipping program seed")

    # Seed coaching memories (idempotent via source check)
    with get_cursor() as cur:
        cur.execute(
            "SELECT 1 FROM user_memory WHERE user_id = %s AND source = %s LIMIT 1",
            (owner_db_id, _SEED_SOURCE),
        )
        if not cur.fetchone():
            from bot.services.memory_service import save_memory
            for content, category in COACHING_MEMORIES:
                try:
                    save_memory(
                        user_id=owner_db_id,
                        content=content,
                        category=category,
                        source=_SEED_SOURCE,
                        confidence=1.0,
                    )
                except Exception as e:
                    logger.warning(f"Failed to seed coaching memory: {e}")
            logger.info(f"Seeded {len(COACHING_MEMORIES)} coaching memories for owner")


def seed_all_owner():
    """Entry point called from main_v2.py."""
    try:
        seed_owner_program()
    except Exception as e:
        logger.error(f"Owner program seeding failed: {type(e).__name__}: {e}")
