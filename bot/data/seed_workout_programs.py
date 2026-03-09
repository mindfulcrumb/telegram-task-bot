"""Seed workout programs v1 — Elite training protocols for busy professionals.

Research-backed programs from top coaches:
- Jim Wendler 5/3/1 philosophy and variants
- GZCLP / GZCL method
- RP Strength MEV/MAV/MRV framework
- Jeff Nippard minimum effective dose approach
- Andy Galpin / Huberman Lab protocols
- Eric Cressey longevity principles
- Paul Carter joint-friendly hypertrophy
- Movement pattern foundations
- Progressive overload methods
- Recovery and deload protocols
"""
import logging

from bot.db.database import get_cursor
from bot.services.knowledge_service import add_knowledge_entry

logger = logging.getLogger(__name__)

_SENTINEL_SOURCE = "workout_programs_v1"


def _already_seeded() -> bool:
    with get_cursor() as cur:
        cur.execute(
            "SELECT 1 FROM knowledge_base WHERE source = %s LIMIT 1",
            (_SENTINEL_SOURCE,),
        )
        return cur.fetchone() is not None


WORKOUT_ENTRIES = [

    # ── Movement Foundations ──
    {
        "category": "fitness",
        "topic": "workout_programming",
        "title": "The 6 Foundational Movement Patterns — Every Program Must Include These",
        "content": (
            "Every elite program — regardless of coach or split — is built around 6 movement patterns. "
            "Missing any one leads to imbalances, injury, and stalled progress.\n\n"
            "1. SQUAT — Bilateral knee-dominant: back squat, front squat, goblet squat, Bulgarian split squat. "
            "Trains quads, glutes, core stability. Load progressively; master depth before adding weight.\n\n"
            "2. HINGE — Hip-dominant: deadlift, Romanian deadlift, hip thrust, kettlebell swing. "
            "Trains posterior chain (hamstrings, glutes, spinal erectors). The single most important pattern for "
            "longevity and athletic performance. Most undertrained in general population.\n\n"
            "3. HORIZONTAL PUSH — Bench press, dumbbell press, push-up variations. Chest, anterior deltoid, triceps. "
            "Most overtrained pattern in gym culture — must be balanced with equal pulling volume.\n\n"
            "4. VERTICAL PUSH — Overhead press (barbell, dumbbell, landmine). Deltoids, upper traps, triceps. "
            "Critical for shoulder health and overhead stability. Often neglected by beginners who only bench.\n\n"
            "5. PULL (horizontal + vertical) — Barbell/dumbbell rows, pull-ups, lat pulldowns, cable rows, face pulls. "
            "Rear delts, lats, rhomboids, biceps. Pull volume should MATCH or exceed push volume. "
            "Rear delt and external rotation work is the most neglected injury prevention tool.\n\n"
            "6. CARRY — Farmer's walk, suitcase carry, overhead carry. The most underrated pattern. "
            "Trains grip, core anti-lateral flexion, postural endurance. "
            "Carries weekly = joint-friendly total-body load that transfers to every other lift.\n\n"
            "Rule of thumb: If your program doesn't include all 6 patterns each week, it is incomplete. "
            "Zoe will identify which patterns you're missing and suggest additions."
        ),
    },

    # ── 5/3/1 Program ──
    {
        "category": "fitness",
        "topic": "workout_programming",
        "title": "Jim Wendler's 5/3/1 — The Gold Standard for Busy Professionals",
        "content": (
            "5/3/1 by Jim Wendler is the most widely recommended strength program on Reddit (r/fitness, r/weightroom) "
            "and among coaches for busy people. Designed after years of powerlifting left Wendler broken — "
            "built specifically for sustainability, longevity, and real-life schedules.\n\n"
            "CORE STRUCTURE:\n"
            "- 3 or 4 days/week, one main lift per session: squat, bench, deadlift, overhead press\n"
            "- Percentage-based wave loading across 4-week cycles\n"
            "- Week 1: 3x5 at 65/75/85% + AMRAP on top set\n"
            "- Week 2: 3x3 at 70/80/90% + AMRAP\n"
            "- Week 3: 3x5/3/1 at 75/85/95% + AMRAP\n"
            "- Week 4: Deload at 40/50/60%\n"
            "- Each cycle, training max increases: +5 lb upper body, +10 lb lower body\n\n"
            "AMRAP (as many reps as possible) on the top set is key — it reveals your real strength "
            "and provides hypertrophy stimulus on top of strength work.\n\n"
            "TRAINING MAX: Set at 90% of true 1RM. This protects from grinding and forces conservative "
            "progression — the secret to Wendler's longevity. 'The goal of every workout is to never miss a rep.'\n\n"
            "BEST VARIANTS FOR BUSY PROFESSIONALS:\n"
            "- 5/3/1 for Beginners (3 days, full body per session, works great for 3-day weeks)\n"
            "- 5/3/1 Boring But Big (BBB): main lift + 5x10 of same lift at 50% — excellent for strength + size\n"
            "- 5/3/1 Building the Monolith: 3 days lifting + 3 days conditioning, complete transformation program\n"
            "- 5/3/1 for the Busy Professional: squat, deadlift, press each session, under 60 minutes\n\n"
            "ASSISTANCE WORK (the 'beyond 5/3/1' model):\n"
            "Choose from: First Set Last (FSL), Boring But Big (BBB), Pyramid, Jokers\n"
            "Keep it simple: push, pull, single-leg, core — 25-50 reps each\n\n"
            "IDEAL FOR: Anyone who wants a set-it-and-run system that works for years. "
            "No constant reprogramming. No guessing. The math is done for you."
        ),
    },

    # ── GZCLP ──
    {
        "category": "fitness",
        "topic": "workout_programming",
        "title": "GZCLP / GZCL Method — Best Structured Program for Beginners to Intermediates",
        "content": (
            "GZCLP (by Cody Lefever, known as /u/gzcl on Reddit) is one of the top-rated programs on "
            "r/fitness and consistently recommended as the best intermediate step between novice and advanced programs.\n\n"
            "TIERED STRUCTURE:\n"
            "- T1 (Tier 1): Heavy compound lifts, 5x3 format, highest intensity (85-90%+ 1RM). "
            "Squat, bench, deadlift, OHP. These are your strength builders.\n"
            "- T2 (Tier 2): Secondary compounds, 3x10 format, moderate intensity (65-70% 1RM). "
            "Front squats, rows, incline press, Romanian deadlifts. Volume builders.\n"
            "- T3 (Tier 3): Accessories, 3x15+ format, low intensity. Isolation work for weak points.\n\n"
            "PROGRESSION:\n"
            "- T1: Add weight each session. If you fail (can't complete 5x3), drop to 6x2, then 10x1, then reset.\n"
            "- T2: Complete all sets at given weight, then add weight next session.\n"
            "- T3: Increase reps until hitting top of range, then add weight.\n\n"
            "4-DAY TEMPLATE (Upper/Lower split with GZCL methodology):\n"
            "- Day A: Squat (T1) + Bench (T2) + Rows (T3)\n"
            "- Day B: OHP (T1) + Deadlift (T2) + Lat pulldowns (T3)\n"
            "- Day C: Bench (T1) + Squat (T2) + Core/curls (T3)\n"
            "- Day D: Deadlift (T1) + OHP (T2) + Accessories (T3)\n\n"
            "WHY IT WORKS: More exercise variety than Starting Strength/StrongLifts, "
            "linear progression on all tiers simultaneously, built-in strength AND hypertrophy volume. "
            "The wiki.fitness.com calls it 'one of the most complete novice-to-intermediate programs available.'\n\n"
            "IDEAL FOR: Intermediates who've outgrown 3x5 linear progression and want clear structure "
            "without complex periodization."
        ),
    },

    # ── RP Strength Framework ──
    {
        "category": "fitness",
        "topic": "workout_programming",
        "title": "RP Strength MEV/MAV/MRV Framework — Science-Based Volume Management",
        "content": (
            "Dr. Mike Israetel's Renaissance Periodization (RP Strength) framework is the most "
            "scientifically rigorous approach to training volume. Used by competitive bodybuilders, "
            "powerlifters, and increasingly by busy professionals who want data-driven efficiency.\n\n"
            "THE VOLUME LANDMARKS (per muscle group per week):\n"
            "- MV (Maintenance Volume): 4-6 sets/week — keeps existing muscle without growing\n"
            "- MEV (Minimum Effective Volume): 6-8 sets/week — smallest dose that produces growth\n"
            "- MAV (Maximum Adaptive Volume): 12-20 sets/week — optimal range for most people\n"
            "- MRV (Maximum Recoverable Volume): 20-30+ sets/week — ceiling before breakdown\n\n"
            "HOW TO USE THIS FOR BUSY PROFESSIONALS:\n"
            "1. Start at MEV (6-8 sets/muscle/week) — this is enough to grow on a time-limited schedule\n"
            "2. Run a 4-6 week mesocycle, adding 1-2 sets per week per muscle group\n"
            "3. When fatigue accumulates and performance drops, you've hit MRV — deload\n"
            "4. Return to MEV at slightly higher strength baseline — this is periodization\n\n"
            "KEY INSIGHT FOR BUSY PEOPLE: 'Minimum effective volume' is real. "
            "6-8 hard sets per muscle per week will produce meaningful hypertrophy. "
            "The research is clear: intensity (proximity to failure) compensates for lower volume. "
            "One set taken to true failure is worth 3 sets leaving 4+ reps in the tank.\n\n"
            "PROXIMITY TO FAILURE MATTERS MOST:\n"
            "- Sets taken to 0-2 RIR (reps in reserve) produce ~3x more hypertrophic signal than sets at 4+ RIR\n"
            "- A 3-day program at MEV volume with high effort outperforms a 5-day program done half-heartedly\n\n"
            "PRACTICAL 3-DAY RP TEMPLATE:\n"
            "Full body 3x/week, each muscle hit twice, starting at MEV:\n"
            "- Each session: 2-3 sets per major muscle group (squat/hinge/push/pull)\n"
            "- Sets done at RPE 8-9 (1-2 RIR)\n"
            "- Add 1 set per muscle every 2 weeks until performance declines\n"
            "- Deload at Week 5-6: reduce volume 40%, keep intensity\n\n"
            "IDEAL FOR: Science-driven individuals who want to optimize every hour in the gym "
            "and understand why they're doing what they're doing."
        ),
    },

    # ── RPE/RIR Autoregulation ──
    {
        "category": "fitness",
        "topic": "workout_programming",
        "title": "RPE and RIR Autoregulation — Why Busy People Should Train by Feel",
        "content": (
            "Rate of Perceived Exertion (RPE) and Reps in Reserve (RIR) autoregulation is now considered "
            "superior to fixed-percentage programming for busy professionals. A 2025 systematic review found "
            "APRE (autoregulating progressive resistance exercise) produced the strongest strength gains "
            "among all programming methods.\n\n"
            "THE SCALE:\n"
            "- RPE 6 / 4 RIR: Could do 4 more reps. Too easy for strength adaptation.\n"
            "- RPE 7 / 3 RIR: Could do 3 more reps. Warm-up territory.\n"
            "- RPE 8 / 2 RIR: Could do 2 more reps. Sweet spot for most working sets.\n"
            "- RPE 9 / 1 RIR: Could do 1 more rep. Top sets, competition prep.\n"
            "- RPE 10 / 0 RIR: True maximum. Reserve for testing, not regular training.\n\n"
            "WHY THIS MATTERS FOR BUSY PEOPLE:\n"
            "Strength fluctuates 15-20% based on sleep, stress, and recovery. "
            "A professional who slept 5 hours and has a major presentation cannot safely train at "
            "their normal 85% 1RM — that's now 95%+ relative effort. "
            "RPE-based training automatically adjusts: you train at RPE 8 regardless of what the "
            "bar says, and the weights adjust accordingly.\n\n"
            "PRACTICAL APPLICATION:\n"
            "- Replace '5x3 at 85%' with '5x3 at RPE 8'\n"
            "- Start conservatively — most beginners underestimate RIR by 2-3 reps\n"
            "- Track your actual weights alongside RPE to learn your own patterns\n"
            "- 'Good day': RPE 8 might be 10 lbs more than usual — go with it\n"
            "- 'Bad day': RPE 8 might be 10 lbs less — also go with it\n\n"
            "PROGRESSION WITH RPE:\n"
            "- If you complete all sets at RPE 7 or below, add weight next session\n"
            "- If sets consistently hit RPE 9+, reduce weight or volume before injury\n"
            "- When the same weight becomes RPE 7 that was previously RPE 8, you've gotten stronger — add weight\n\n"
            "IDEAL FOR: Anyone with a variable schedule, high stress job, or inconsistent sleep. "
            "Autoregulation is not laziness — it's intelligent programming."
        ),
    },

    # ── Progressive Overload ──
    {
        "category": "fitness",
        "topic": "workout_programming",
        "title": "Progressive Overload Methods — The Only Variable That Drives Long-Term Gains",
        "content": (
            "Progressive overload is the foundational principle behind all strength and muscle gains. "
            "Without it, adaptation stops. Every expert from Wendler to Israetel to Galpin agrees: "
            "the method of overload matters less than consistently applying it.\n\n"
            "METHOD 1 — DOUBLE PROGRESSION (best for busy professionals):\n"
            "- Choose a rep range (e.g., 3-5 sets of 6-12 reps)\n"
            "- Add reps each session until you hit the top of the range across all sets\n"
            "- Then add weight (5 lb upper / 10 lb lower) and drop back to the bottom\n"
            "- Example: 3x6 at 100 lb → 3x8 → 3x10 → 3x12 → add 5 lb → 3x6 at 105 lb\n"
            "- Simple, requires no percentage calculation, automatic progressive overload\n\n"
            "METHOD 2 — WAVE LOADING (5/3/1 style):\n"
            "- Week 1: 3x5 at 65/75/85% of training max\n"
            "- Week 2: 3x3 at 70/80/90%\n"
            "- Week 3: 3x1 at 75/85/95%\n"
            "- Week 4: Deload\n"
            "- Increase training max by 5 lb (upper) / 10 lb (lower) each cycle\n"
            "- Automates intensity periodization over months\n\n"
            "METHOD 3 — LINEAR PROGRESSION (novices only):\n"
            "- Add weight every single session (5 lb upper / 10 lb lower)\n"
            "- Works for 3-6 months for true beginners — enjoy it, it ends\n"
            "- When this stalls, switch to double progression or wave loading\n\n"
            "METHOD 4 — MESO PROGRESSION (RP Strength model):\n"
            "- Week 1: MEV volume, moderate intensity\n"
            "- Weeks 2-5: Add 1-2 sets per muscle per week (volume overload)\n"
            "- Deload: Reset to MEV volume with slightly heavier weights\n"
            "- Each meso, start heavier than the last — this is the long-term progression\n\n"
            "ANDY GALPIN'S GUIDELINES:\n"
            "- Aim for ~3% weekly intensity increase (weight on bar)\n"
            "- ~5% weekly volume increase (sets x reps)\n"
            "- Never exceed 10% jump in either in a single week\n"
            "- After 6 weeks of overload: deload to ~70% load/volume, then return\n\n"
            "CRITICAL RULE: You cannot always add weight. Other overload methods include:\n"
            "adding reps, adding sets, reducing rest periods, improving form/range of motion, "
            "increasing time under tension, switching to harder variations."
        ),
    },

    # ── Deload & Recovery ──
    {
        "category": "fitness",
        "topic": "workout_programming",
        "title": "Deload Protocols and Recovery — Non-Negotiable for Longevity",
        "content": (
            "Deloads are the most skipped part of programming and the #1 reason experienced lifters plateau "
            "or get injured. Research from PMC (2024) shows average deload frequency among strength athletes "
            "is every 5.6 weeks. Skipping deloads doesn't mean you're tough — it means you're accumulating "
            "fatigue faster than you're recovering.\n\n"
            "WHAT HAPPENS WITHOUT DELOADS:\n"
            "- Accumulated fatigue masks actual fitness (you're stronger than you feel, but can't express it)\n"
            "- Central nervous system (CNS) fatigue accumulates: reaction time slows, coordination drops\n"
            "- Connective tissue stress increases injury risk (tendons and ligaments lag behind muscle adaptation)\n"
            "- Motivation and drive decrease — a sign of neurological overtraining, not weakness\n\n"
            "DELOAD PROTOCOL (consensus from Reddit + research):\n"
            "- Frequency: Every 4-8 weeks for most people (beginners: every 8 weeks; advanced: every 4 weeks)\n"
            "- Volume reduction: Drop 30-50% of normal sets\n"
            "- Intensity: Reduce load by ~10% OR add 2-3 RIR per set (stay lighter)\n"
            "- Frequency: Still go to the gym same days — deload means less, not nothing\n"
            "- Duration: 1 full week\n\n"
            "SIGNS YOU NEED AN UNPLANNED DELOAD NOW:\n"
            "- Performance has dropped 3+ sessions in a row\n"
            "- Joints aching rather than muscle soreness\n"
            "- Motivation to train is unusually low\n"
            "- Sleep quality has decreased despite no lifestyle changes\n"
            "- You dread the gym (not normal pre-workout laziness — genuine aversion)\n\n"
            "ERIC CRESSEY'S LONGEVITY PRINCIPLE:\n"
            "'The person who gets to the gym 52 weeks a year beats the person sidelined by overtraining.' "
            "Sustainability is the primary metric. One missed week of training every 6 weeks via structured deload "
            "produces more long-term adaptation than 6 weeks of grinding then 2 weeks injured.\n\n"
            "BUSY PROFESSIONAL DELOAD TIP:\n"
            "Travel weeks, holidays, and high-stress work periods are NATURAL deloads. "
            "Use them intentionally rather than fighting them. Come back fresh and add weight."
        ),
    },

    # ── Longevity & Joint Health ──
    {
        "category": "fitness",
        "topic": "workout_programming",
        "title": "Training for Longevity — Joint Health, Movement Quality, and Injury Prevention",
        "content": (
            "The most successful lifters are not the ones who trained hardest in their 20s — "
            "they're the ones still training in their 50s and 60s. Eric Cressey, Paul Carter, and "
            "Jim Wendler all emphasize longevity over short-term performance.\n\n"
            "CRESSEY'S NON-NEGOTIABLE PRINCIPLES:\n"
            "1. Match training intensity to life stress — high-stress week = lower volume, maintain quality\n"
            "2. Introduce novel movements gradually — ~10% volume increase per week maximum\n"
            "3. Movement quality over load — address mobility deficits before adding weight\n"
            "4. Have backup plans for busy days — 20-minute bodyweight circuit beats zero\n"
            "5. Address imbalances early — unilateral work catches asymmetries before they become injuries\n\n"
            "JOINT HEALTH ESSENTIALS (do these every week, no exceptions):\n"
            "- Rear delt and external rotation work (face pulls, band pull-aparts, Y raises): "
            "Most neglected muscles. Protect the rotator cuff. Counteracts the internally-rotated "
            "posture from desk work and horizontal pressing.\n"
            "- Single-leg and unilateral movements (split squats, step-ups, single-leg RDL): "
            "Reduces joint loading vs. heavy bilateral lifts. Catches imbalances before they compound.\n"
            "- Carries weekly (farmer's walk, suitcase carry): Most joint-friendly total-body load. "
            "Builds grip, core, and postural endurance without spinal compression.\n"
            "- Hip hinge proficiency before heavy deadlifts: Poor hinge mechanics = fast back injury. "
            "Use kettlebell deadlifts, trap bar, or RDLs to build the pattern before loading the barbell.\n\n"
            "PAUL CARTER'S AVT (ACCUMULATIVE VOLUME TRAINING):\n"
            "- Short 'rounds' of compound movements to accumulate volume without grinding joints\n"
            "- Emphasizes mechanical tension over metabolic damage — the sustainable hypertrophy signal\n"
            "- Stretch under load: incline curls, Romanian deadlifts, deficit push-ups\n"
            "- Particularly effective for lifters 35+ with joint history\n\n"
            "MINIMUM EFFECTIVE DOSE FOR JOINT HEALTH:\n"
            "- 2-3 sets of rear delt/external rotation per session\n"
            "- 1-2 unilateral lower body movements per lower session\n"
            "- 1 carry variation per week\n"
            "- Never train through sharp joint pain — train around it, address the cause"
        ),
    },

    # ── Upper/Lower Split ──
    {
        "category": "fitness",
        "topic": "workout_programming",
        "title": "Upper/Lower Split — Best 4-Day Program for Strength and Hypertrophy",
        "content": (
            "The Upper/Lower split is the most recommended 4-day structure on r/fitness, r/weightroom, "
            "and r/strength_training for intermediate lifters. Hits each muscle group twice per week — "
            "the research-optimal training frequency — without requiring 5+ sessions.\n\n"
            "STANDARD TEMPLATE:\n"
            "Day 1 — Lower (Squat focus):\n"
            "  - Back squat or front squat (T1 strength: 3-5x3-5)\n"
            "  - Romanian deadlift or leg press (T2 volume: 3-4x8-12)\n"
            "  - Leg curl (3x10-15)\n"
            "  - Calf raise (3x15-20)\n"
            "  - Carries optional\n\n"
            "Day 2 — Upper (Push focus):\n"
            "  - Bench press or incline press (T1: 3-5x3-5)\n"
            "  - Overhead press (T2: 3-4x8-12)\n"
            "  - Dumbbell row or cable row (3-4x10-15)\n"
            "  - Face pull (3x15-20) — always included\n"
            "  - Tricep isolation (2-3x12-15)\n\n"
            "Day 3 — Lower (Hinge focus):\n"
            "  - Conventional or trap bar deadlift (T1: 3-5x3-5)\n"
            "  - Bulgarian split squat (T2: 3x8-12 each leg)\n"
            "  - Hip thrust (3x12-15)\n"
            "  - Core (planks, pallof press, ab wheel)\n\n"
            "Day 4 — Upper (Pull focus):\n"
            "  - Pull-up or lat pulldown (T1: 3-5 sets)\n"
            "  - Barbell row or pendlay row (T2: 3-4x8-12)\n"
            "  - Incline dumbbell press (accessory press: 3x10-15)\n"
            "  - Rear delt fly (3x15-20)\n"
            "  - Bicep curl (2-3x12-15)\n\n"
            "PROGRESSION: Use double progression on all lifts — add reps until hitting top of range, "
            "then add weight. Simple, effective, sustainable.\n\n"
            "SCHEDULING: Mon/Tue/Thu/Fri works best — two consecutive days, one rest day, two more. "
            "The off days between Lower-Upper pairs allow partial recovery of opposite muscle groups.\n\n"
            "MODIFICATION FOR 3 DAYS: Combine Day 2 and Day 4 into one Upper session "
            "(alternate push/pull focus weekly). Run Mon/Wed/Fri.\n\n"
            "IDEAL FOR: Intermediates with 4 days available, 45-75 minutes per session, "
            "wanting both strength and hypertrophy gains."
        ),
    },

    # ── Full Body 3x ──
    {
        "category": "fitness",
        "topic": "workout_programming",
        "title": "Full Body 3x Per Week — Best Program for Time-Constrained Professionals",
        "content": (
            "Full body training 3x/week is the most resilient and time-efficient split for busy professionals. "
            "Popularized by 5/3/1 and GZCLP, validated by research: hitting each muscle 3x/week at MEV volume "
            "produces equal or superior gains vs. higher frequency at lower volume per session.\n\n"
            "WHY FULL BODY WINS FOR BUSY PEOPLE:\n"
            "- Maximum pattern frequency (squat, hinge, push, pull every session)\n"
            "- Maximum recovery time between sessions (48-72 hours)\n"
            "- Highly resilient to missed days — miss one session, you still hit every pattern twice that week\n"
            "- Shorter sessions possible (45 min) vs. split routines (60-90 min)\n\n"
            "TEMPLATE (Mon/Wed/Fri or similar):\n\n"
            "Session A:\n"
            "  - Main squat variation (3-5x3-5 at RPE 8 or 75-85%)\n"
            "  - Horizontal push (3-4x6-12)\n"
            "  - Horizontal pull / row (3-4x8-15)\n"
            "  - Core or carry\n\n"
            "Session B:\n"
            "  - Main hinge variation (3-5x3-5 at RPE 8)\n"
            "  - Vertical push / overhead press (3-4x6-12)\n"
            "  - Vertical pull / pull-up or lat pulldown (3-4x8-15)\n"
            "  - Rear delt / external rotation (3x15-20)\n\n"
            "Session C (rotate A/B emphasis):\n"
            "  - Secondary squat variation (front squat, split squat, goblet squat)\n"
            "  - Secondary push (incline press, dips, push-up variation)\n"
            "  - Secondary pull (face pulls, cable rows, chest-supported row)\n"
            "  - Carries or loaded core\n\n"
            "PROGRESSION: Rotate between A/B/C, or run A/B/A one week then B/A/B next. "
            "Use double progression or wave loading on main lifts.\n\n"
            "TIME-CRUNCH VERSION (30-40 minutes):\n"
            "- 1 compound squat or hinge (3x5)\n"
            "- 1 compound push (3x8)\n"
            "- 1 compound pull (3x8)\n"
            "- Done. This is sufficient for strength maintenance and moderate hypertrophy.\n\n"
            "GALPIN'S RULE: 'Work training backward around life — don't force a program into "
            "an impossible schedule. Start with what you can commit to and build from there.'"
        ),
    },

    # ── Minimum Effective Dose ──
    {
        "category": "fitness",
        "topic": "workout_programming",
        "title": "Minimum Effective Dose Training — The Science of Doing Less, Better",
        "content": (
            "A 2019 PubMed study confirmed that even 1-3 sets per movement taken to near-failure "
            "produces significant strength gains in trained individuals. Jeff Nippard built an entire "
            "program (Min-Max) around this principle — each set done at 0-1 RIR (failure or one rep shy).\n\n"
            "THE RESEARCH:\n"
            "- 1 hard set per exercise, 3x/week = statistically significant strength and hypertrophy gains\n"
            "- 3-5 hard sets per exercise per week = optimal dose for most people with limited time\n"
            "- Beyond 5 sets per exercise per week: diminishing returns unless recovery is prioritized\n"
            "- A dose-response meta-analysis found each additional set adds ~0.24% hypertrophy gain, "
            "with clear diminishing returns beyond 20 sets/muscle/week\n\n"
            "MINIMUM EFFECTIVE DOSE WEEKLY TARGETS:\n"
            "- Squat pattern: 4-6 hard sets (2-3 sessions × 2 sets)\n"
            "- Hinge pattern: 4-6 hard sets\n"
            "- Push: 6-10 hard sets (chest + shoulder)\n"
            "- Pull: 6-10 hard sets (back + rear delt)\n"
            "- Carries: 2-3 sets\n"
            "TOTAL: ~25-35 working sets/week across all patterns\n\n"
            "THE NIPPARD MIN-MAX APPROACH:\n"
            "- 4 days/week, 45 minutes/session\n"
            "- 1-2 hard sets per exercise taken to failure or 1 RIR\n"
            "- Higher effort compensates for lower volume\n"
            "- Built-in deload every 7th week\n"
            "- Full progressive overload system with deload timing\n\n"
            "PRACTICAL IMPLEMENTATION:\n"
            "If you only have 30 minutes:\n"
            "1. Pick 1 compound lift (squat, deadlift, press, or row)\n"
            "2. Do 3 hard sets (not warm-ups — working sets at RPE 8+)\n"
            "3. Pick 1-2 accessory exercises for balance\n"
            "4. Done — this is genuinely enough to make progress\n\n"
            "KEY MINDSET SHIFT: The goal is not to do MORE. The goal is to apply the right STIMULUS "
            "and then recover. A 30-minute focused session beats a 90-minute half-effort session every time."
        ),
    },

    # ── Andy Galpin / Huberman Protocol ──
    {
        "category": "fitness",
        "topic": "workout_programming",
        "title": "Andy Galpin's 3-Day Protocol — Strength, Power, and Endurance for Busy People",
        "content": (
            "Dr. Andy Galpin, exercise physiologist at Cal State Fullerton and advisor to elite athletes, "
            "appeared on the Huberman Lab podcast to outline optimal training for people who want "
            "strength, hypertrophy, AND longevity in minimal time.\n\n"
            "GALPIN'S 3-DAY TEMPLATE:\n\n"
            "Day 1 — Speed/Power + Hypertrophy:\n"
            "  - Explosive movements first (jump squats, med ball throws, sprint): 3-5 sets x 3-5 reps\n"
            "  - Hypertrophy compounds: 3-4 sets x 8-12 reps at RPE 8\n"
            "  - Goal: train neuromuscular power when CNS is fresh, then build muscle volume\n\n"
            "Day 2 — Strength + Aerobic:\n"
            "  - Heavy compound lifts: 4-5 sets x 3-5 reps at RPE 8-9\n"
            "  - 20-30 min Zone 2 cardio (150-165 BPM) OR HIIT (4x4 min at 90%+ heart rate)\n"
            "  - Goal: maximum strength expression + cardiovascular adaptation same day\n\n"
            "Day 3 — Endurance + Muscular Endurance:\n"
            "  - Higher rep strength work: 3-5 sets x 11-30 reps\n"
            "  - Longer cardio session: 45-60 min Zone 2 or circuit-style conditioning\n"
            "  - Goal: metabolic fitness, lactate threshold, muscular stamina\n\n"
            "GALPIN'S CORE PROGRAMMING RULES:\n"
            "1. Never skip the movement assessment — know your weaknesses before adding load\n"
            "2. Train 3-4 days is optimal for most people — beyond this, recovery suffers\n"
            "3. Sleep is the #1 recovery tool: strength fluctuates 15-20% on poor sleep nights\n"
            "4. Protein: minimum 1.6 g/kg body weight daily for muscle protein synthesis\n"
            "5. Work training backward around life — the best program is one you can actually do\n\n"
            "WEEKLY NON-NEGOTIABLES (Galpin's 9-3-2-1-0 rule for general health):\n"
            "- 9 hours of sleep or as close as possible\n"
            "- 3 strength sessions minimum\n"
            "- 2 cardiovascular sessions (one Zone 2, one higher intensity)\n"
            "- 1 long slow duration session (hike, long walk, bike)\n"
            "- 0 consecutive days without some movement\n\n"
            "IDEAL FOR: Professionals who want the full performance picture — "
            "strength, power, endurance, and longevity — in 3 sessions per week."
        ),
    },

]


def seed_workout_programs():
    """Seed elite workout program knowledge into the knowledge base."""
    if _already_seeded():
        logger.info("Workout programs already seeded — skipping")
        return 0

    count = 0
    for entry in WORKOUT_ENTRIES:
        try:
            add_knowledge_entry(
                category=entry["category"],
                topic=entry["topic"],
                title=entry["title"],
                content=entry["content"],
                source=_SENTINEL_SOURCE,
            )
            count += 1
            logger.info(f"Seeded: {entry['title'][:60]}")
        except Exception as e:
            logger.error(f"Failed to seed '{entry['title'][:40]}': {e}")

    logger.info(f"Workout programs seed complete: {count}/{len(WORKOUT_ENTRIES)} entries added")
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seeded = seed_workout_programs()
    print(f"Done: {seeded} workout program entries seeded")
