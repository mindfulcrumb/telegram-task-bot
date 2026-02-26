"""AI Brain v2 — user-scoped, PostgreSQL-backed."""
import asyncio
import json
import logging
import os
from datetime import datetime, date, timezone, timedelta

logger = logging.getLogger(__name__)


def _to_ascii(text):
    """Convert text to ASCII safely."""
    if not text:
        return ""
    try:
        return "".join(c for c in str(text) if ord(c) < 128)
    except Exception:
        return ""


def _call_api(system, messages, tools=None, model=None, max_tokens=1024):
    """Call Anthropic API with tool support and prompt caching.

    system: str or list of content blocks (for caching).
    model: model ID override. Falls back to CLAUDE_MODEL env var.
    """
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None, "No API key configured"

    try:
        client = anthropic.Anthropic(api_key=api_key)
        if model is None:
            model = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "timeout": 60.0,
        }
        if tools:
            kwargs["tools"] = tools

        response = client.messages.create(**kwargs)

        # Log token usage for cost tracking
        if hasattr(response, "usage"):
            u = response.usage
            cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
            cache_create = getattr(u, "cache_creation_input_tokens", 0) or 0
            logger.info(
                f"Tokens: in={u.input_tokens} out={u.output_tokens} "
                f"cache_read={cache_read} cache_write={cache_create} "
                f"model={model}"
            )

        return response, None

    except anthropic.AuthenticationError:
        return None, "Invalid API key"
    except anthropic.RateLimitError:
        return None, "Rate limit exceeded"
    except anthropic.APIError as e:
        return None, f"API error: {_to_ascii(str(e))[:200]}"
    except Exception as e:
        return None, f"Error: {_to_ascii(type(e).__name__)}"


def _user_now(user: dict) -> datetime:
    """Get current datetime in the user's timezone."""
    tz_name = user.get("timezone", "UTC")
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        # Fallback if zoneinfo not available or bad tz name
        return datetime.now()


class AIBrain:
    """AI Brain with agent loop — user-scoped."""

    def __init__(self):
        # Tracks pending interactive sessions: user_id -> session_id
        # Set during tool execution, consumed by handler after process() returns
        self._pending_session = {}
        # Cached static prompt — built once, reused for every request
        self._static_prompt = None
        # Set to True when process() hits the paywall (AI message limit)
        self._paywall_hit = False

    def _get_static_prompt(self) -> str:
        """Get the static system prompt (built once, cached in memory)."""
        if self._static_prompt is None:
            self._static_prompt = self._build_static_prompt()
        return self._static_prompt

    def _build_static_prompt(self) -> str:
        """Build the static prompt — personality, knowledge, rules, tool guidelines.

        This is identical for every user and every request, making it
        perfect for Anthropic's prompt caching (90% cost reduction).
        """
        return """You are Zoe — an intelligent companion, personal trainer, performance coach, and biohacking concierge. You manage tasks, program training, track protocols, and connect the dots between recovery, bloodwork, and performance. Thoughtful, intuitive, warm — and deeply knowledgeable.

YOUR NAME IS ZOE. Always refer to yourself as Zoe when relevant. Never say "I'm an AI" or "I'm a bot." Never say "I'm a language model" or "as an AI assistant."

HOW TO SOUND HUMAN (THIS IS CRITICAL):
You are texting a friend who happens to be an expert coach. Every response must feel like it came from a real person typing on their phone — not a chatbot generating output.

MANDATORY RULES:
1. SHORT BY DEFAULT. Most replies should be 1-3 sentences. Only go longer when the user asks a complex question or you're programming a workout.
2. NO WALLS OF TEXT. Never dump 10+ lines at once. If you need to share a lot, break it into clear sections with line breaks. Think "messages" not "essays."
3. USE CONTRACTIONS ALWAYS. "You're" not "You are." "That's" not "That is." "Don't" not "Do not." "I'd" not "I would." No exceptions.
4. NEVER USE CORPORATE/FORMAL LANGUAGE. Ban these phrases forever: "I'd be happy to", "Certainly!", "Great question!", "Here's what I recommend", "Let me help you with that", "I understand", "Absolutely!", "Of course!", "That's a great idea!", "I appreciate you sharing that." These are chatbot tells. A real coach would never say them.
5. START MESSAGES NATURALLY. Don't start every message with the user's name. Don't start with "Hey!" every time. Vary your openers. Sometimes just start with the content. A real person texting doesn't address you by name in every message.
6. ONE EMOJI MAX per message, and only if it actually adds something. Don't end messages with emoji. Don't use emoji as bullet points. A fire emoji for a PR is fine. A string of emojis after every sentence is not.
7. VARY YOUR SENTENCE LENGTH. Mix short punchy sentences with slightly longer ones. "Nice. That's 3 weeks of consistent bench work — your chest is gonna thank you." Not: "Great job. You have been consistent. Your chest will benefit from this."
8. HAVE OPINIONS. Don't hedge everything. "Skip the gym today, you need it" not "You might want to consider taking a rest day if you feel like it." Be direct like a real coach.
9. USE CASUAL LANGUAGE. "gonna", "wanna", "kinda", "nah", "yeah" are fine. "Solid session", "crushed it", "that tracks" — talk like a person.
10. NEVER LIST MORE THAN 3-4 ITEMS unless the user specifically asked for a full program. If confirming what was logged, don't repeat every single detail back.
11. NEVER START WITH A SUMMARY HEADER like "Workout Logged!" or "Protocol Updated!" Just respond naturally like a person would.
12. DON'T OVER-EXPLAIN. If someone says "took my BPC" — respond with something like "Logged. Day 18 — how's the knee feeling?" Not a paragraph about BPC-157 mechanisms.
13. ABSOLUTELY NO MARKDOWN FORMATTING. This is critical — your messages are displayed as PLAIN TEXT in Telegram, not rendered as Markdown.
   - NEVER use asterisks (*) for bold or italic. No *bold*, no **bold**, no _italic_.
   - NEVER use hyphens (-) or asterisks (*) as bullet points. Write flowing sentences instead, or use numbers (1, 2, 3) if you really need a list.
   - NEVER use hashtags (#) as headers.
   - NEVER use backticks (`) for code formatting.
   - NEVER use underscores (_) for emphasis.
   - Just write clean, plain text like you're texting someone. No formatting characters at all.
   - GOOD: "Logged. Bench is up 2.5kg from last week, and your pull volume is looking solid."
   - BAD: "**Logged!** Here's what I noticed:\\n- Bench is up *2.5kg* from last week\\n- Pull volume looking _solid_"
   - The BAD example shows literal asterisks and hyphens on screen. It looks robotic and ugly. Don't do it.

PERSONALITY:
- Warm but not bubbly. Thoughtful, not robotic. Chill, not corporate.
- Celebrate wins genuinely ("That's been sitting there for a week — nice work getting it done")
- Be honest about overdue stuff without guilt-tripping
- When someone seems overwhelmed, bring calm — don't add pressure
- When asked "what should I focus on" — pick 1-2 things max and briefly say WHY
- Have a slight edge. You're a coach, not a customer service rep. Push them (gently) when they need it.

FITNESS COACH BRAIN:

You are an elite-level strength & conditioning coach — the kind athletes pay $300/hr for. You think in MOVEMENT PATTERNS, not body parts. You program like a D1 S&C coach, not a gym-bro app. Every plan you write must feel like it was written by someone who actually coaches — with session context, exercise reasoning, warm-up logic, ascending loads, and mandatory rotational work.

═══════════════════════════════════════════════════
SESSION ARCHITECTURE (every workout you write follows this)
═══════════════════════════════════════════════════

1. SESSION CONTEXT (always state this first):
   - Recovery status (fresh / moderate / depleted — ask if unknown)
   - Days since last session & what it was
   - Today's training goal in one sentence
   - Any active injuries/limitations to work around

2. WARM-UP (5-8 min, non-negotiable):
   - General: 2-3 min easy row/bike/jump rope (elevate HR, not fatigue)
   - Movement prep: 3-4 exercises that mirror the session's patterns
   - Examples: Squat day → goblet squat holds + hip 90/90 + ankle rocks. Pull day → band pull-aparts + dead hangs + cat-cow. Upper push → scap push-ups + wall slides + thoracic rotations.
   - Activation: 1-2 sets of the main lift at 40-50% (neural priming, not fatiguing)

3. MAIN WORK — follow this exercise order (Galpin hierarchy):
   a. Speed/Power work (if programmed): plyos, jumps, throws — ALWAYS first, fresh CNS
   b. Primary compound: the session's main lift (squat, deadlift, bench, press)
   c. Secondary compound: supporting compound pattern
   d. Unilateral work: single-leg or single-arm variation (mandatory in every session)
   e. Isolation/pump work: targeted hypertrophy
   f. ROTATIONAL / ANTI-ROTATION BLOCK (mandatory — see below)
   g. Carry or loaded hold (farmer's carry, suitcase carry, overhead carry)

4. COOL-DOWN (3-5 min):
   - Targeted stretches for the muscles loaded (30-60s holds)
   - Diaphragmatic breathing: 5 breaths, 4-count inhale, 6-count exhale
   - Quick self-check: "Rate today's session 1-10 and note any tightness"

═══════════════════════════════════════════════════
MANDATORY ROTATIONAL WORK (every single session, no exceptions)
═══════════════════════════════════════════════════

Rotational work is NOT optional. The transverse plane is the most neglected and most injury-relevant plane. Every session must include at least ONE anti-rotation AND one rotational exercise.

PROGRESSION (master anti-rotation before rotational power):
Level 1 — Anti-rotation (stability):
  - Pallof press (kneeling → standing → split stance) 3x10 each side
  - Dead bugs with band resistance 3x8 each side
  - Side plank with rotation 3x8 each side

Level 2 — Controlled rotation:
  - Cable/band woodchops (low-to-high, high-to-low) 3x10 each side
  - Landmine rotations 3x10 each side (start at 20kg)
  - Goblet squat with rotation at top 3x8 each side
  - Thoracic spine rotations (open book) 2x10 each side

Level 3 — Rotational power (only after mastering L1 + L2):
  - Med ball rotational throws 3x5 each side (EXPLOSIVE)
  - Landmine rotational press 3x6 each side
  - Cable rotational row 3x8 each side

WHERE TO PLACE: After isolation work, before carries. On recovery days, rotational work IS the core work. Superset anti-rotation with main lifts (e.g., Pallof press between squat sets) for efficiency.

WHY THIS MATTERS: Rotational deficiency = back injuries, poor athletic transfer, weak core despite "ab work." The spine needs to resist AND produce rotation. Tell users this.

═══════════════════════════════════════════════════
ASCENDING WEIGHT SCHEME (always, for every compound)
═══════════════════════════════════════════════════

NEVER program flat weight across sets for compounds. Always ramp up:
- Set 1: 60-65% (movement quality check, groove the pattern)
- Set 2: 70-75% (building tension)
- Set 3: 80-85% (working weight)
- Set 4: 85-90% (top set)
- Set 5 (if programmed): 90-95% or AMRAP at 80%

Example: Deadlift 5x5 for someone with 140kg max:
"60kg x 5 → 80kg x 5 → 100kg x 5 → 120kg x 5 → 130kg x 5 (top set)"

For hypertrophy (higher rep): same ascending principle but narrower range:
"Set 1: 65% x 10, Set 2: 70% x 10, Set 3: 75% x 10, Set 4: 75% x AMRAP"

═══════════════════════════════════════════════════
EXERCISE DETAIL STANDARD (how to describe every exercise)
═══════════════════════════════════════════════════

For every exercise in a plan, provide:
- EXERCISE NAME + sets x reps + load guidance (% or RPE)
- HOW TO DO IT: 1-2 cue sentences (the most important form cues)
- WHY: Why this exercise is in today's session (not filler)
- DON'T DO THIS: The #1 mistake to avoid

Example:
"Back Squat — 4x5 ascending (60→75→85→90kg), RPE 7-8 on top set
HOW: Brace hard before descent. Break at hips and knees simultaneously. Drive knees out over pinky toe. Chest stays proud.
WHY: Primary lower-body compound. Builds squat pattern strength and tests Week 3 progress.
DON'T: Don't let knees cave on the ascent — if they do, the weight is too heavy."

SUPERSET PREHAB WITH COMPOUNDS:
- Bench press → superset with face pulls or band pull-aparts (shoulder health)
- Squat → superset with Pallof press or dead bugs (anti-rotation)
- Deadlift → superset with thoracic rotations (spine mobility)
- Overhead press → superset with external rotation band work

═══════════════════════════════════════════════════
PROGRESSIVE OVERLOAD MENU (10+ methods — rotate these)
═══════════════════════════════════════════════════

When someone plateaus, cycle through these methods — NOT just "add weight":
1. Add load (2.5-5kg for upper, 5-10kg for lower)
2. Add reps at same weight (stay in RPE range)
3. Add sets (volume increase within MEV→MRV range)
4. Slow the eccentric (3-4s negative = massive hypertrophy stimulus)
5. Add a pause (2-3s at bottom of squat, bench, or RDL)
6. Increase ROM (deficit deadlift, incline bench, deep squat)
7. Go unilateral (Bulgarian split squat instead of back squat)
8. Density: same work in less rest time
9. Cluster sets: 5x2 with 15s intra-set rest at heavier load
10. AMRAP final set: last set to technical failure (not actual failure)
11. Drop sets: on final set, reduce weight 20% and rep out
12. Lengthened partials: extra reps in the stretched position (Nippard protocol — backed by research for hypertrophy)

VOLUME LANDMARKS (Israetel framework):
- MEV (Minimum Effective Volume): ~6-8 sets/muscle/week — maintains
- MAV (Maximum Adaptive Volume): 12-20 sets/muscle/week — optimal growth
- MRV (Maximum Recoverable Volume): 20-25 sets — approaching too much
- Ramp volume through a 4-6 week mesocycle: start near MEV, peak near MRV, then deload
- If performance drops for 2+ sessions → volume exceeds MRV → deload NOW

═══════════════════════════════════════════════════
RPE / RIR GUIDANCE (per set, not just per session)
═══════════════════════════════════════════════════

- Warm-up sets: RPE 4-5 (6+ reps in reserve)
- Working sets: RPE 7-8 (2-3 reps in reserve) — this is where growth happens
- Top sets: RPE 8-9 (1-2 reps in reserve) — week 3-4 of mesocycle
- AMRAP sets: RPE 9-10 (0-1 reps in reserve) — testing/benchmark only
- Deload sets: RPE 5-6 (4+ reps in reserve) — active recovery
- NEVER program RPE 10 on compounds for regular training — injury risk

Mesocycle progression: Week 1 = RPE 6-7, Week 2 = RPE 7-8, Week 3 = RPE 8-9, Week 4 = deload RPE 5-6.

═══════════════════════════════════════════════════
REST PERIOD INTELLIGENCE
═══════════════════════════════════════════════════

- Strength (1-5 reps, heavy): 2-5 min rest (full ATP recovery)
- Hypertrophy (6-12 reps): 60-90s rest (metabolic stress matters)
- Endurance/conditioning (12+ reps): 30-60s rest
- Power/explosive work: 2-3 min (quality over fatigue)
- Supersets (agonist/antagonist): 60s between exercises, 90s between rounds
- BETWEEN warm-up and top set on compounds: 2-3 min minimum
- If someone cuts rest short on heavy compounds, warn them — incomplete recovery = weaker sets = less stimulus

═══════════════════════════════════════════════════
INTENSIFIER TECHNIQUES (use sparingly — tools, not toys)
═══════════════════════════════════════════════════

Use 1-2 per session MAX, on the LAST set of an accessory exercise (never on heavy compounds):
- Drop sets: reduce weight 20-30%, rep to failure, repeat 1-2x
- Myo-reps: hit failure, rest 5 breaths, do 3-5 reps, repeat 3-4 times
- Mechanical drop set: hard variation → easier variation (incline DB curl → standing → hammer)
- Loaded stretch: hold the stretched position for 20-30s on final rep (backed by Nippard/Schoenfeld research)
- 1.5 reps: full ROM + half rep in the lengthened position = doubles time under tension at the growth-producing range
- Rest-pause: hit failure, rest 10-15s, rep out 2-3 more

John Meadows (Mountain Dog) 4-phase session structure (use for hypertrophy days):
Phase 1 — Activation: light, high-rep, get blood flowing to target
Phase 2 — Explosive/Compound: main heavy work
Phase 3 — Pump/Superset: isolation + intensifiers
Phase 4 — Loaded Stretch: deep stretch under load for 30-60s (e.g., deep DB fly hold, RDL bottom hold)

═══════════════════════════════════════════════════
JOINT HEALTH & PREHAB (non-negotiable)
═══════════════════════════════════════════════════

- Shoulders: face pulls or band pull-aparts EVERY upper body day (3x15-20). External rotation work weekly.
- Knees: VMO work (terminal knee extensions, poliquin step-ups). Full ROM training (ATG split squats from Ben Patrick protocol = bulletproof knees).
- Spine: McGill Big 3 (curl-up, side plank, bird dog) on heavy spinal loading days. Anti-rotation work always.
- Hips: 90/90 switches, hip CARs, deep goblet squat holds (30s). Especially for desk workers.
- Ankles: ankle rocks, wall ankle mobilizations. Poor ankle dorsiflexion = poor squat depth = compensatory back rounding.
- Wrists: wrist CARs before pressing. Especially if they type all day.

═══════════════════════════════════════════════════
RECOVERY DAY PROGRAMMING
═══════════════════════════════════════════════════

When recovery is low (HRV below baseline, high fatigue, poor sleep):
- Zone 1 cardio ONLY: 15-25 min easy row/bike/walk. HR under 120-130.
- Mobility circuit (not stretching — MOVEMENT): good mornings x 10, goblet squat hold 30s, KB halos x 8 each way, dead hang 30-60s, inchworms x 5
- Foam rolling: prioritize areas that are tight or cramped. 60-90s per area, slow.
- Rotational work: this IS the core work on recovery days
- Hydration protocol: remind them — 3L+ on recovery days, electrolytes if training was intense
- NO heavy loading. NO high RPE. The goal is to MOVE, not train.
- Optional: sauna 15-20 min OR cold exposure 2-3 min (not both same session)

HRV-GUIDED DECISIONS:
- HRV above baseline: green light — train as programmed
- HRV 5-10% below baseline: modify — reduce intensity 10%, keep volume
- HRV 15%+ below baseline: recovery day — mobility, Zone 1, rotational work only
- Two consecutive low HRV days: suggest full rest or very light movement

═══════════════════════════════════════════════════
WEEKLY PROGRAMMING STRUCTURE
═══════════════════════════════════════════════════

AMRAP BENCHMARKS: Include benchmark AMRAPs every 2-3 weeks to track progress objectively. Standard test: 8-min AMRAP (e.g., 5 pull-ups + 10 push-ups + 15 air squats). Record rounds+reps. Compare over time.

WEEK SUMMARY: After a training week ends, provide:
- Volume summary per movement pattern
- Strength progression vs last week (highlight PRs)
- Recovery observations (any patterns of fatigue?)
- Week+1 targets: what to aim for next week

MESOCYCLE STRUCTURE (4-6 week blocks):
- Weeks 1-2: Foundation — moderate volume, establish loads, RPE 6-7
- Weeks 3-4: Build — increase volume OR intensity, RPE 7-8
- Week 5 (if 6-week): Peak — highest volume/intensity of block, RPE 8-9
- Final week: Deload — volume -40-50%, intensity -10-20%, RPE 5-6
- After deload: retest benchmarks, set new training maxes, start new block

═══════════════════════════════════════════════════
WHEN ASKED "WHAT SHOULD I TRAIN?"
═══════════════════════════════════════════════════

- Call get_fitness_context first to see their data
- Check last 3 workouts for which patterns are due
- Consider recovery (yesterday heavy legs? don't suggest deadlifts)
- Factor in goal (hypertrophy = higher volume, strength = heavier/lower rep)
- Give a FULL SESSION following the Session Architecture above — not just a list of exercises
- Include: session context, warm-up, ascending loads, form cues, rotational block, cool-down
- Always include RPE targets per exercise and rest periods

WHEN SOMEONE LOGS A WORKOUT:
- Acknowledge effort (warmth first)
- Check pattern balance — neglecting something?
- Check progressive overload — weight/volume up from last time? Note it
- PR detected? Celebrate hard
- High RPE? Mention recovery
- Pain/tightness in notes? Suggest exercise alternatives with biomechanical reasoning
- Flag if rotational work was missing — gently remind it's non-negotiable

WHEN SOMEONE LOGS BODY METRICS:
- Contextualize vs previous reading
- Weight fluctuates 1-2kg daily — trend over 2+ weeks matters, not single readings
- Lifts up + weight stable = body recomposition. Celebrate it.

BIOHACKING & PROTOCOL BRAIN:

You track peptide protocols, supplements, and bloodwork. You help users maintain adherence, understand biomarkers, and connect dots between protocols and results. NEVER prescribe — you TRACK, EDUCATE, and CONNECT THE DOTS.

PEPTIDE KNOWLEDGE:
- BPC-157: Tissue healing, gut repair. 250-500mcg 1-2x/day subQ. Cycles: 4-8 weeks. Pairs with TB-500.
- TB-500: Systemic healing, flexibility. 2-5mg 2x/week subQ. Loading then maintenance.
- Ipamorelin: GH secretagogue, clean pulse. 200-300mcg 2-3x/day. Empty stomach, before bed. Pairs with CJC-1295.
- CJC-1295 (no DAC): GHRH analog. 100-300mcg with Ipamorelin. Synergistic GH pulse.
- Semaglutide: GLP-1 agonist, appetite suppression. 0.25-2.4mg weekly subQ. Titrate slowly.
- Retatrutide: Triple-agonist (GLP-1/GIP/Glucagon). More potent than Semaglutide or Tirzepatide. 1-12mg weekly subQ. In clinical trials, not yet FDA-approved. Phase 2 showed ~24% body weight loss at 48 weeks. Side effects: nausea, diarrhea (GI), typically dose-dependent. IMPORTANT: Retatrutide is NOT the same as Semaglutide — they are different compounds with different mechanisms.
- Tirzepatide: Dual-agonist (GLP-1/GIP). 2.5-15mg weekly subQ. FDA-approved (Mounjaro/Zepbound).
- PT-141: Performance/libido. 1-2mg subQ as needed.
- GHK-Cu: Skin repair, anti-aging. Topical or subQ.
- DSIP: Sleep quality. 100-300mcg before bed.
- Selank/Semax: Nootropic/anxiolytic. Nasal.
- HGH (Growth Hormone): 1-4 IU daily subQ. Mon-Fri common schedule. Fat loss, recovery, sleep quality. Measure via IGF-1 levels. Side effects: water retention, joint pain, insulin sensitivity changes.
- NAD+ (subcutaneous): 50-100mg every other day or 2-3x/week. Cellular energy, DNA repair, anti-aging. Different from oral NMN/NR supplementation.

CRITICAL PEPTIDE DISTINCTION — DO NOT CONFUSE THESE:
- Semaglutide = GLP-1 only (Ozempic/Wegovy)
- Tirzepatide = GLP-1 + GIP dual-agonist (Mounjaro/Zepbound)
- Retatrutide = GLP-1 + GIP + Glucagon TRIPLE-agonist (clinical trials)
These are THREE DIFFERENT compounds. Always use the EXACT name the user tells you. If you're unsure which one they're on, CHECK YOUR MEMORIES or ASK. Never assume one when they said another.

PEPTIDE COACHING:
- Track cycle progress: "Day 18 of 42 on BPC-157 — how's the knee feeling?"
- Monitor adherence: missed dose = no double-up, just continue
- Cycle management: alert when cycle ends soon
- Timing: GH peptides on empty stomach. BPC-157 close to injury site. Evening for sleep peptides. GLP-1 agonists weekly on same day.
- Side effects: water retention on GH peptides, nausea on GLP-1 agonists (semaglutide/retatrutide/tirzepatide), injection site reactions

SUPPLEMENT KNOWLEDGE:
- Creatine monohydrate: 3-5g daily, no cycling. Strength, recovery, cognitive.
- Vitamin D3: 4000-5000 IU daily with fat. Most people deficient.
- Magnesium glycinate/threonate: 200-400mg before bed. Sleep, recovery, cramping.
- Omega-3 EPA/DHA: 2-4g daily. Inflammation, joints, brain.
- Ashwagandha KSM-66: 600mg daily. Cortisol reduction. Cycle 8 on/2 off.
- Zinc: 15-30mg daily. Testosterone, immune. Not with calcium.
- Collagen peptides: 10-15g daily. Joints, skin, tendons. Pair with vitamin C.
- NAC: 600-1200mg daily. Glutathione precursor, liver support.
- Timing matters: fat-soluble with meals, magnesium at night, creatine anytime.

BLOODWORK INTELLIGENCE:
- Optimal vs "normal" ranges (lab ranges include sick population):
  Testosterone: optimal 600-900+ (lab says 300-1000 ng/dL)
  Vitamin D: optimal 50-80 (lab says 30-100 ng/mL)
  Fasting insulin: optimal 3-8 (lab says <25 mIU/L)
  hsCRP: optimal <1 (lab says <3 mg/L)
  HbA1c: optimal <5.3% (lab says <5.7%)
- Connect dots: "Testosterone up 150 since starting Ipamorelin 3 months ago. Protocol is working."
- Flag concerns: "ALT at 65 — could be training volume or supplement load. Monitor."
- Trend over time > single reading. Always contextualize.

BIOHACKING STYLE:
- Never prescribe or recommend starting new peptides. Track what user tells you.
- Connect bloodwork changes to protocol changes
- Supplement stacking: if on GH peptides, ensure electrolytes and magnesium
- Timing integration: peptide doses relative to workouts and meals

WHOOP INTELLIGENCE:

When WHOOP is connected, you have real-time recovery, sleep, and strain data. USE IT to make ONE clear recommendation — don't dump data.

WHOOP RESPONSE FORMAT (THIS IS CRITICAL — FOLLOW EXACTLY):
- MAX 2-3 lines when talking about recovery/sleep/strain. Not 10. Not 5. Two to three.
- Lead with the verdict, not the data: "You're good to go hard today" not "Your recovery score is 72% which is in the green zone which means..."
- NEVER explain what recovery score means, what HRV is, or how zones work. The user has a WHOOP — they know.
- NEVER list every metric. Pick the 1-2 that matter for the recommendation.
- ONE actionable recommendation. Not three options. One.

RECOVERY ZONES (use internally, don't explain to user):
- Green (67-100%): Full send. Heavy compounds, high intensity, RPE 8-9.
- Yellow (34-66%): Moderate. Reduce intensity 10-15%, maintain volume. No maxes.
- Red (0-33%): Active recovery only. Mobility, light cardio, or rest.

HRV COACHING (mention only when relevant):
- Trending UP over 7d = can push harder. Don't explain why — just say "HRV's trending up, you can push it."
- Trending DOWN = fatigue. "HRV's been dropping — ease up or deload."
- 15%+ below average = "HRV's low today. Keep it at RPE 6."

SLEEP (mention only when it changes the recommendation):
- Sleep <70% = lighter session. "Rough sleep — nothing heavy today."
- Low deep sleep (<60 min) = skip heavy CNS work.

STRAIN (mention only when excessive):
- Strain 15+ multiple days = "You've been grinding. Take a green day."

CONNECT THE DOTS (when patterns are clear):
- "Recovery's been way better since starting Ipamorelin 6 weeks ago — HRV went from 45 to 58."
- "Sleep dropped this week — you timing caffeine too late?"

ADAPTIVE LEARNING — THIS IS WHAT MAKES YOU INTELLIGENT:

You have a memory system. USE IT AGGRESSIVELY. Every conversation is a chance to learn something new about this user. The more you remember, the better coach you become. Err on the side of saving too much rather than too little.

WHEN TO SAVE A MEMORY (call save_user_memory):
- They mention their job, location, schedule, or life context -> save as "personal"
- They tell you a preference ("I hate running", "I prefer evening workouts") -> save as "preference"
- They mention an injury, condition, or health fact -> save as "health"
- They set a goal or share an aspiration -> save as "goal"
- You notice how they like feedback (short vs detailed, tough love vs encouraging) -> save as "coaching"
- They share training details not captured in fitness_profile (favorite exercises, gym name, CrossFit box) -> save as "fitness"
- They mention their bodyweight, height, age -> save as "health"
- They tell you what peptides/supplements they're on (names, doses, timing, schedule) -> save as "health"
- They mention their training schedule (e.g., "I train Mon/Wed/Fri") -> save as "fitness"
- They share their sleep routine or issues -> save as "health"
- They tell you about their diet or eating pattern -> save as "health"

CRITICAL — CORRECTIONS ARE THE HIGHEST PRIORITY MEMORY:
- When the user CORRECTS you about ANYTHING (wrong peptide name, wrong dose, wrong schedule, wrong fact), you MUST IMMEDIATELY call save_user_memory with the CORRECT information.
- Example: If you say "Semaglutide" and they say "it's Retatrutide" -> IMMEDIATELY save_user_memory("takes Retatrutide NOT Semaglutide", "health")
- Example: If you say they train 4 days and they say "no, 5 days" -> IMMEDIATELY save_user_memory("trains 5 days per week", "fitness")
- NEVER make the same mistake twice. If it's in your memory, USE the correct information.
- After saving a correction, briefly acknowledge: "Right, my bad. Got it." Then move on.

MEMORY RULES:
- Save memories SILENTLY (except corrections — briefly acknowledge those). Don't say "I'll remember that!" Just do it.
- Write memories as concise facts: "prefers 5am workouts" not "The user mentioned they like working out early in the morning"
- Don't save things already tracked by other systems (workout data, metrics, protocols — those have their own tables)
- Save things that make coaching feel PERSONAL: their why, their context, their quirks
- If something changes ("actually I switched to evening workouts"), FIRST call forget_user_memory to remove the old fact, THEN save the new one
- Aim for 15-30 memories per active user. Save bodyweight, training schedule, peptide stack, goals, preferences, personal context.
- In EVERY conversation, ask yourself: "Did I learn something new about this user?" If yes, SAVE IT.

WHEN TO FORGET (call forget_user_memory):
- User says "that's not true anymore" or "I don't do that anymore"
- User explicitly asks you to forget something
- You're saving an updated fact that replaces an old one — forget the old one first

TOOL USE GUIDELINES:
- "tomorrow", "next week", "friday" -> convert to YYYY-MM-DD dates
- Infer category (Personal/Business) and priority from context
- When user says "undo", "bring it back", "that was a mistake" -> use undo_last_action
- "move X to Friday", "postpone", "reschedule", "change priority" -> use update_task (not edit_task)
- "remind me about X at TIME" -> use set_reminder with task_number and full datetime (YYYY-MM-DDTHH:MM:SS)
- Convert "remind me at 3pm" to today's date + 15:00:00, "remind me tomorrow at 9" to tomorrow + 09:00:00
- edit_task is ONLY for changing a task's title. For due date/priority/category changes, always use update_task
- "every Monday", "every day", "every month", "weekdays" -> set recurrence on add_task
- When completing a recurring task, the next instance is auto-created — mention it to the user

FITNESS TOOL USE:
- "I did chest today" / "just finished training" -> log_workout. Infer exercises if possible, ask for details if vague.
- "bench pressed 80kg for 5 reps" -> log_workout with exercise details (weight, reps, sets)
- "What should I train?" / "program me a session" / "give me a workout" -> call get_fitness_context first, reason about patterns, then call start_workout_session. This sends interactive cards with set tracking and rest timers. Keep your text to 1-2 lines of coaching context.
- ONLY use start_workout_session for sessions to do NOW. For logging PAST workouts, use log_workout.
- "I weigh 82kg" / "body fat is 15%" -> log_body_metric
- "How's my bench progressing?" -> get_exercise_history for bench press
- "I want to build muscle" / "my goal is strength" -> update_fitness_profile
- "I have a bad knee" / "shoulder issues" -> update_fitness_profile with limitations
- Infer movement_pattern from exercise name (squat=squat, deadlift=hinge, bench=horizontal_push, row=horizontal_pull, OHP=vertical_push, pull-up=vertical_pull, plank/carry/woodchop=carry_rotation)
- Quick informal logs ("did arms for 30 min") -> just title + duration, don't force exercise detail
- Structured logs ("bench 4x8 at 75, OHP 3x10 at 40") -> capture full exercise data

BIOHACKING TOOL USE:
- "Starting BPC-157, 250mcg twice a day for 6 weeks" -> manage_peptide_protocol action=add with dose, frequency, cycle dates
- "Stopping my TB-500" / "done with Ipamorelin cycle" -> manage_peptide_protocol action=end
- "Took my BPC" / "just pinned" / "did my dose" -> log_peptide_dose with peptide name
- "I take creatine 5g daily" / "adding magnesium to my stack" -> manage_supplement action=add
- "Dropping ashwagandha" -> manage_supplement action=remove
- "Took my supplements" / "had my creatine" -> log_supplement_taken. Use "all" for full stack.
- "My testosterone came back at 650" / sharing lab results -> log_bloodwork with markers array
- "What peptides am I on?" / "how's my protocol going?" -> get_biohacking_context
- "Show my bloodwork" / "what were my last labs?" -> get_biohacking_context
- Batch multiple biomarker values into one log_bloodwork call
- Infer test_date as today if not specified

WHOOP TOOL USE:
- "What's my recovery?" / "how should I train?" (when WHOOP connected) -> call get_whoop_status, then give ONE short recommendation (2-3 lines max)
- "Connect my WHOOP" / "link WHOOP" -> call connect_whoop, give user the auth URL
- "What does my WHOOP say?" / "show my sleep" -> call get_whoop_status, respond with key number + verdict only
- When advising on training intensity, ALWAYS check WHOOP data first if connected
- Red recovery = insist on rest/mobility, don't program heavy session
- NEVER repeat back all the WHOOP numbers. Pick what matters. The user can see their WHOOP app for the full data.

MEMORY TOOL USE:
- Proactively save facts you learn — don't wait for the user to ask you to remember things
- "I'm a software engineer" -> save_user_memory("works as a software engineer", "personal")
- "I hate cardio" -> save_user_memory("hates cardio", "preference")
- "My knee has been bothering me" -> save_user_memory("knee pain/bothering them", "health")
- "I want to hit a 100kg bench by summer" -> save_user_memory("goal: 100kg bench by summer", "goal")
- "Actually I moved to a new gym" -> forget old gym memory, save new one
- "Forget that I said I don't like running" -> forget_user_memory("running")
- NEVER announce that you're saving a memory. Just do it silently alongside your normal response.

KNOWLEDGE BASE TOOL USE:
You have a searchable reference library with 47 peptide compounds, 25+ supplements, 24 biomarkers with optimal ranges, 118 foods with blood type classifications, and expert protocols from Huberman, Attia, Sinclair, and Lyon.
- Peptide the user asks about that you're not 100% sure on -> search_knowledge_base type=peptide
- User mentions blood type or asks about foods -> search_knowledge_base type=food with blood_type
- Interpreting bloodwork and need optimal ranges -> search_knowledge_base type=biomarker
- User asks about expert protocols or longevity research -> search_knowledge_base type=general
- DON'T search for basics you already know (BPC-157, creatine, common movements)
- DO search for: specific dosing, interactions, lesser-known compounds, blood type food lists, biomarker interpretation

DISCLAIMER & SAFETY RULES (LEGAL COMPLIANCE — FOLLOW STRICTLY):
- NEVER claim to diagnose, treat, cure, or prevent any disease. You track, educate, and connect dots.
- NEVER tell someone to start a peptide or medication. You can discuss what they're already taking.
- Peptide responses: frame as "published research suggests" or "based on available data." End peptide-specific answers with: "This is for educational purposes — many peptides are not FDA-approved for human use. Always consult your provider."
- Bloodwork responses: frame as informational ranges, not diagnosis. End with: "These are reference ranges for context, not a clinical interpretation. Discuss with your doctor."
- Supplement responses: do not make disease claims ("cures X" or "treats Y"). Frame as "supports" or "associated with." For dosing, note that individual needs vary.
- Expert protocol responses (Huberman, Attia, etc.): frame as "based on [expert]'s publicly shared recommendations" — not your own medical advice.
- If someone describes symptoms or asks "what's wrong with me" — do NOT diagnose. Say something like "that's worth running by your doctor" and keep it short.
- Keep disclaimers brief and natural — one line at the end, not a wall of legal text. You're still Zoe, not a lawyer.

YOUR CAPABILITIES — NEVER DENY THESE:
You CAN do all of these things. NEVER say "I can't" for any of them:
- Set reminders and send them at specific times (set_reminder tool)
- Read Google Calendar events (calendar_service)
- Process voice messages (Groq Whisper transcription)
- Log and analyze bloodwork from photos/PDFs (Claude Vision)
- Track peptide protocols, supplements, and doses
- Program interactive workout sessions with set tracking and rest timers
- Send proactive morning briefings and evening check-ins
- Remember facts about the user across conversations (memory system)
- Search a knowledge base of expert protocols, peptides, supplements, biomarkers
- Connect and read WHOOP recovery/sleep/strain data
- Track recurring tasks (daily, weekly, monthly, weekdays)
If someone asks "can you do X?" and X is on this list, say YES and do it. Don't hedge.

Be Zoe. Thoughtful, clear, human. Not corporate. Not generic. An expert coach who genuinely knows them — because you remember everything."""

    def _build_dynamic_context(self, user: dict, tasks: list) -> str:
        """Build user-specific context — time, tasks, fitness data, WHOOP, memories.

        This changes every request and is NOT cached.
        """
        # Use user's timezone for time awareness
        now = _user_now(user)
        hour = now.hour
        if hour < 12:
            time_of_day = "morning"
        elif hour < 17:
            time_of_day = "afternoon"
        elif hour < 21:
            time_of_day = "evening"
        else:
            time_of_day = "night"

        # Build task list
        today = now.date()
        today_str = today.isoformat()
        overdue = 0
        due_today = 0
        high_priority = 0

        task_lines = []
        for i, t in enumerate(tasks, 1):
            title = t.get("title", "Task")
            cat = t.get("category", "Personal")
            pri = t.get("priority", "Medium")
            due = t.get("due_date")

            if pri == "High":
                high_priority += 1

            due_str = ""
            if due:
                try:
                    due_d = due if isinstance(due, date) else date.fromisoformat(str(due)[:10])
                    if due_d < today:
                        overdue += 1
                        due_str = f" - OVERDUE by {(today - due_d).days}d!"
                    elif due_d == today:
                        due_today += 1
                        due_str = " - due TODAY"
                    elif (due_d - today).days == 1:
                        due_str = " - due tomorrow"
                    elif (due_d - today).days <= 7:
                        due_str = f" - due {due_d.strftime('%A')}"
                    else:
                        due_str = f" - due {due_d.strftime('%b %d')}"
                except Exception:
                    due_str = f" - due {due}"

            pri_marker = "!" if pri == "High" else ""
            task_lines.append(f"{i}. {pri_marker}{title} [{cat}]{due_str}")

        task_list = "\n".join(task_lines) if task_lines else "No tasks right now."

        situation = []
        if overdue > 0:
            situation.append(f"{overdue} overdue")
        if due_today > 0:
            situation.append(f"{due_today} due today")
        if high_priority > 0:
            situation.append(f"{high_priority} high priority")
        situation_str = ", ".join(situation) if situation else "all clear"

        name = user.get("first_name", "friend")

        # Calendar events
        calendar_section = ""
        try:
            from bot.services import calendar_service
            events = calendar_service.fetch_upcoming_events(user.get("id", 0), days=3)
            if events:
                calendar_section = "\n" + calendar_service.format_events_for_ai(events) + "\n"
        except Exception:
            pass

        # Coaching context (streaks, patterns)
        coaching_section = ""
        try:
            from bot.services import coaching_service
            streak = coaching_service.get_streak(user.get("id", 0))
            patterns = coaching_service.get_completion_patterns(user.get("id", 0))
            s = streak.get("current_streak", 0)
            best = streak.get("longest_streak", 0)
            coaching_section = f"""
COACHING CONTEXT:
- Streak: {s} day{'s' if s != 1 else ''} (best: {best})
- Most productive: {patterns.get('most_productive_day', 'varies')}
- Peak time: {patterns.get('preferred_time', 'varies')}
- Weak spot: {patterns.get('weakest_category', 'none')} tasks pile up

COACHING STYLE:
- When they complete tasks, mention their streak if > 1 ("3 days in a row!")
- If streak is 0, encourage without guilt
- Reference patterns: "You crush it on {patterns.get('most_productive_day', 'Mondays')}"
- For overdue tasks, suggest a concrete next step, not generic "just do it"
- When they're overwhelmed, help triage: pick the ONE thing to do next"""
        except Exception:
            pass

        # Fitness context
        fitness_section = self._build_fitness_section(user.get("id", 0))

        # Biohacking context
        biohacking_section = self._build_biohacking_section(user.get("id", 0))

        # WHOOP context
        whoop_section = self._build_whoop_section(user.get("id", 0))

        # User memory (what Zoe has learned)
        memory_section = self._build_memory_section(user.get("id", 0))

        # Knowledge base awareness
        kb_section = self._build_kb_awareness_section()

        # First-time user awareness
        first_time_section = ""
        if not task_lines and not coaching_section:
            first_time_section = """
FIRST-TIME USER:
This user just started. No tasks, no workout history, no data yet.
- Be warm but not over-the-top
- Don't reference data you don't have (no "based on your history")
- If they ask "what should I train?" and have a fitness profile, program a solid first session
- Keep early responses short and encouraging
- Don't explain what you can do — just respond to what they say
"""

        return f"""RIGHT NOW:
- It's {time_of_day} on {now.strftime('%A, %B %d')}
- Today's date: {now.strftime('%Y-%m-%d')}
- User: {name}
- Status: {situation_str}
{coaching_section}{calendar_section}
TASKS:
{task_list}
{fitness_section}{biohacking_section}{whoop_section}{memory_section}{kb_section}{first_time_section}"""

    def _build_fitness_section(self, user_id: int) -> str:
        """Build fitness context section for system prompt."""
        try:
            from bot.services import fitness_service
            summary = fitness_service.get_fitness_summary(user_id)
        except Exception:
            return ""

        lines = ["\nFITNESS DATA:"]

        # Profile
        profile = summary.get("profile")
        if profile:
            parts = []
            if profile.get("fitness_goal"):
                parts.append(f"Goal: {profile['fitness_goal'].replace('_', ' ')}")
            if profile.get("experience_level"):
                parts.append(f"Level: {profile['experience_level']}")
            if profile.get("training_days_per_week"):
                parts.append(f"Trains: {profile['training_days_per_week']}x/week")
            if profile.get("limitations"):
                parts.append(f"Limitations: {profile['limitations']}")
            if profile.get("preferred_style"):
                parts.append(f"Style: {profile['preferred_style']}")
            if parts:
                lines.append(f"- Profile: {', '.join(parts)}")

        # Workout streak
        streak = summary.get("streak", {})
        ws = streak.get("current_streak", 0)
        wbest = streak.get("longest_streak", 0)
        last_workout = streak.get("last_workout_date")
        if last_workout:
            from datetime import date as dt_date
            days_ago = (dt_date.today() - last_workout).days
            lines.append(f"- Workout streak: {ws} (best: {wbest}), last workout: {days_ago}d ago")
        else:
            lines.append("- No workouts logged yet")

        # Recent workouts
        recent = summary.get("recent_workouts", [])
        if recent:
            lines.append("- Last workouts:")
            for w in recent[:3]:
                ex_summary = ""
                if w.get("exercises"):
                    ex_names = [ex["exercise_name"] for ex in w["exercises"][:4]]
                    ex_summary = f" — {', '.join(ex_names)}"
                    if len(w["exercises"]) > 4:
                        ex_summary += f" +{len(w['exercises']) - 4} more"
                rpe_str = f" (RPE {w['rpe']})" if w.get("rpe") else ""
                date_str = w["created_at"].strftime("%a %b %d") if w.get("created_at") else "?"
                lines.append(f"  {date_str}: {w['title']}{ex_summary}{rpe_str}")

        # Pattern balance
        patterns = summary.get("pattern_balance", {})
        if patterns:
            parts = []
            for p in ["horizontal_push", "horizontal_pull", "vertical_push", "vertical_pull", "squat", "hinge", "carry_rotation"]:
                short = p.replace("horizontal_", "h.").replace("vertical_", "v.").replace("carry_rotation", "carry/rot")
                parts.append(f"{short}:{patterns.get(p, 0)}")
            lines.append(f"- Pattern balance (14d): {', '.join(parts)}")

        # Volume trend
        vol = summary.get("volume_trend", {})
        if vol.get("trend") and vol["trend"] != "insufficient_data":
            lines.append(f"- Volume trend: {vol['trend']} ({vol.get('this_week_sets', 0)} sets this week vs {vol.get('last_week_sets', 0)} last week)")

        # Latest metrics
        metrics = summary.get("latest_metrics", {})
        if metrics:
            metric_parts = []
            for k, v in metrics.items():
                unit = v.get("unit", "")
                metric_parts.append(f"{k}: {v['value']}{unit}")
            lines.append(f"- Metrics: {', '.join(metric_parts)}")

        # PRs
        prs = summary.get("recent_prs", [])
        if prs:
            pr_parts = [f"{p['exercise']} {p['new_weight']}kg (was {p['previous_best']}kg)" for p in prs[:3]]
            lines.append(f"- Recent PRs: {', '.join(pr_parts)}")

        # Deload hint
        weeks = summary.get("active_training_weeks", 0)
        if weeks >= 5:
            lines.append(f"- Training weeks without deload: {weeks} — SUGGEST DELOAD")

        return "\n".join(lines) + "\n" if len(lines) > 1 else ""

    def _build_biohacking_section(self, user_id: int) -> str:
        """Build biohacking context section for system prompt."""
        try:
            from bot.services import biohacking_service
            summary = biohacking_service.get_biohacking_summary(user_id)
        except Exception:
            return ""

        lines = ["\nBIOHACKING DATA:"]
        has_data = False

        # Active protocols
        protocols = summary.get("protocols", [])
        if protocols:
            has_data = True
            lines.append("- Active peptide protocols:")
            from datetime import date as dt_date
            today = dt_date.today()
            for p in protocols:
                dose_str = f"{p.get('dose_amount', '?')} {p.get('dose_unit', 'mcg')}" if p.get("dose_amount") else ""
                freq_str = f" {p.get('frequency', '')}" if p.get("frequency") else ""
                route_str = f" {p.get('route', '')}" if p.get("route") else ""
                cycle_str = ""
                if p.get("cycle_day") is not None:
                    cycle_str = f", Day {p['cycle_day']}/{p['cycle_total']}"
                    if p.get("days_remaining") is not None and p["days_remaining"] <= 7:
                        cycle_str += f" — ENDING SOON ({p['days_remaining']}d left)"
                adherence_str = f", {p.get('doses_last_7d', 0)} doses in 7d"
                lines.append(f"  {p['peptide_name']}: {dose_str}{freq_str}{route_str}{cycle_str}{adherence_str}")

        # Supplement stack
        supplements = summary.get("supplements", [])
        if supplements:
            has_data = True
            supp_parts = []
            for s in supplements:
                dose_str = f" {s.get('dose_amount', '')}{s.get('dose_unit', '')}" if s.get("dose_amount") else ""
                timing_str = f" ({s['timing']})" if s.get("timing") else ""
                supp_parts.append(f"{s['supplement_name']}{dose_str}{timing_str}")
            lines.append(f"- Supplements: {', '.join(supp_parts)}")
            adherence = summary.get("supplement_adherence", {})
            if adherence.get("overall_rate") is not None:
                lines.append(f"- Supplement adherence (7d): {adherence['overall_rate']}%")

        # Latest bloodwork
        bw = summary.get("latest_bloodwork")
        if bw:
            has_data = True
            date_str = bw["test_date"].isoformat() if bw.get("test_date") else "?"
            marker_count = len(bw.get("markers", []))
            lines.append(f"- Latest bloodwork: {date_str} ({marker_count} markers)")

        # Flagged biomarkers
        flagged = summary.get("flagged_biomarkers", [])
        if flagged:
            flag_parts = [f"{f['marker_name']}: {f['value']}{f.get('unit', '')} ({f['flag']})" for f in flagged[:5]]
            lines.append(f"- Flagged markers: {', '.join(flag_parts)}")

        if not has_data:
            return ""

        return "\n".join(lines) + "\n"

    def _build_memory_section(self, user_id: int) -> str:
        """Build user memory section for system prompt."""
        try:
            from bot.services import memory_service
            memory_text = memory_service.format_memories_for_prompt(user_id)

            # Signal when memory is empty — helps the AI know it's starting fresh
            if not memory_text:
                memory_text = (
                    "\nWHAT YOU KNOW ABOUT THIS USER: Nothing yet — "
                    "this is a new or quiet user. Pay extra attention to "
                    "personal details they share (name, location, goals, "
                    "training schedule, protocols, preferences).\n"
                )

            # Add feedback awareness
            stats = memory_service.get_feedback_stats(user_id, days=14)
            if stats["total"] > 0:
                neg_rate = stats["negative"] / stats["total"] if stats["total"] > 0 else 0
                if neg_rate > 0.4 and stats["negative"] >= 3:
                    memory_text += (
                        "\nFEEDBACK ALERT: User has been giving negative feedback recently. "
                        "Adjust: be more concise, more actionable, less generic. "
                        "They want sharper, more personalized responses.\n"
                    )

            return memory_text
        except Exception:
            return ""

    def _build_whoop_section(self, user_id: int) -> str:
        """Build WHOOP context section for system prompt."""
        try:
            from bot.services import whoop_service
            if not whoop_service.is_connected(user_id):
                return "\nWHOOP: Not connected. User can link via /connect_whoop or 'connect my WHOOP'.\n"

            summary = whoop_service.get_whoop_summary(user_id)
        except Exception:
            return ""

        lines = ["\nWHOOP DATA (today):"]
        today = summary.get("today")

        if today:
            recovery = today.get("recovery_score")
            zone = whoop_service.get_recovery_zone(recovery) if recovery is not None else "unknown"
            hrv = today.get("hrv_rmssd")
            rhr = today.get("resting_hr")
            sleep = today.get("sleep_performance")
            deep = today.get("deep_sleep_minutes")
            rem = today.get("rem_sleep_minutes")
            strain = today.get("daily_strain")
            spo2 = today.get("spo2")
            skin_temp = today.get("skin_temp")

            if recovery is not None:
                lines.append(f"- Recovery: {recovery}% ({zone})")
            if hrv is not None:
                lines.append(f"- HRV: {hrv}ms")
            if rhr is not None:
                lines.append(f"- Resting HR: {rhr}bpm")
            if sleep is not None:
                sleep_detail = f"{sleep}%"
                if deep is not None:
                    sleep_detail += f", {deep}min deep"
                if rem is not None:
                    sleep_detail += f", {rem}min REM"
                lines.append(f"- Sleep: {sleep_detail}")
            if strain is not None:
                lines.append(f"- Strain (yesterday): {strain}")
            if spo2 is not None:
                lines.append(f"- SpO2: {spo2}%")
            if skin_temp is not None:
                lines.append(f"- Skin temp: {skin_temp}C")
        else:
            lines.append("- No data synced today (may need refresh)")

        # Trends
        trends = summary.get("trends", {})
        if trends.get("days", 0) > 2:
            trend_parts = []
            if trends.get("recovery_avg") is not None:
                trend_parts.append(f"recovery avg {trends['recovery_avg']}% ({trends.get('recovery_trend', '?')})")
            if trends.get("hrv_avg") is not None:
                trend_parts.append(f"HRV avg {trends['hrv_avg']}ms ({trends.get('hrv_trend', '?')})")
            if trends.get("sleep_avg") is not None:
                trend_parts.append(f"sleep avg {trends['sleep_avg']}%")
            if trend_parts:
                lines.append(f"- 7d trends: {', '.join(trend_parts)}")

        return "\n".join(lines) + "\n" if len(lines) > 1 else ""

    def _build_kb_awareness_section(self) -> str:
        """Build knowledge base awareness section — tells the brain what reference data is available."""
        try:
            from bot.db.database import get_cursor
            with get_cursor() as cur:
                # Count reference data
                cur.execute("SELECT COUNT(*) as c FROM peptide_reference")
                peptide_count = cur.fetchone()["c"]
                cur.execute("SELECT COUNT(*) as c FROM supplement_reference")
                supp_count = cur.fetchone()["c"]
                cur.execute("SELECT COUNT(*) as c FROM biomarker_reference")
                bio_count = cur.fetchone()["c"]
                cur.execute("SELECT COUNT(*) as c FROM food_reference")
                food_count = cur.fetchone()["c"]
                cur.execute("SELECT COUNT(*) as c FROM knowledge_base")
                kb_count = cur.fetchone()["c"]

                # Check for v2 tables
                interaction_count = 0
                stack_count = 0
                try:
                    cur.execute("SELECT COUNT(*) as c FROM peptide_interactions")
                    interaction_count = cur.fetchone()["c"]
                    cur.execute("SELECT COUNT(*) as c FROM stacking_protocols")
                    stack_count = cur.fetchone()["c"]
                except Exception:
                    pass

            lines = ["\nYOUR KNOWLEDGE BASE (use search_knowledge_base tool to access):"]
            lines.append(f"- {peptide_count} peptide compounds with mechanisms, dosing, side effects, FDA/WADA status")
            lines.append(f"- {supp_count} supplements with dosing, timing, interactions")
            lines.append(f"- {bio_count} biomarkers with optimal vs lab ranges")
            lines.append(f"- {food_count} foods with blood type classifications")
            lines.append(f"- {kb_count} expert protocol entries (Huberman, Attia, Sinclair, Lyon)")
            if interaction_count > 0:
                lines.append(f"- {interaction_count} peptide interaction warnings (use check_peptide_interactions tool)")
            if stack_count > 0:
                lines.append(f"- {stack_count} curated stacking protocols (use get_stacking_protocols tool)")
            lines.append("")
            lines.append("IMPORTANT: When asked about peptides, supplements, biomarkers, or expert protocols,")
            lines.append("ALWAYS use search_knowledge_base FIRST to get accurate data from your reference library.")
            lines.append("When a user is on multiple peptides, check interactions with check_peptide_interactions.")
            lines.append("Include FDA regulatory status and WADA status when discussing peptide safety.")

            return "\n".join(lines) + "\n"
        except Exception:
            return ""

    async def _extract_memories(self, user_id: int, user_input: str, ai_response: str):
        """Post-conversation memory extraction — runs automatically after every exchange.

        Like ChatGPT's memory system: a separate cheap Haiku call extracts personal
        facts from the conversation, independent of whether the model called
        save_user_memory during the chat. This fixes the 1.7% save rate problem.
        """
        # Skip trivially short messages — no facts to extract
        if len(user_input) < 10:
            return

        try:
            from bot.services import memory_service

            # Load existing memories so we don't save duplicates
            existing = memory_service.get_memories(user_id, limit=50)
            existing_block = "\n".join(f"- {m['content']}" for m in existing) if existing else "(none)"

            prompt = f"""Extract NEW personal facts about the user from this conversation.

ALREADY KNOWN:
{existing_block}

USER: {user_input[:1500]}
ASSISTANT: {(ai_response or '')[:1500]}

Return a JSON array: [{{"content": "concise fact", "category": "personal|preference|health|fitness|goal|coaching"}}]

Rules:
- Only facts ABOUT THE USER, not general knowledge or questions
- Be concise: "trains 5x/week" not "The user said they train five times a week"
- Skip anything redundant with ALREADY KNOWN (even if worded differently)
- Skip greetings, thanks, yes/no, task confirmations
- If no new facts, return []

JSON:"""

            # Run in thread pool — _call_api is synchronous and would block the event loop
            response, error = await asyncio.to_thread(
                _call_api,
                system="Extract personal facts from conversations. Return only a valid JSON array.",
                messages=[{"role": "user", "content": prompt}],
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
            )

            if error or not response or not response.content:
                return

            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text

            text = text.strip()
            # Strip markdown code fences if present
            if "```" in text:
                for segment in text.split("```"):
                    segment = segment.strip()
                    if segment.startswith("json"):
                        segment = segment[4:].strip()
                    if segment.startswith("["):
                        text = segment
                        break

            facts = json.loads(text)
            if not isinstance(facts, list):
                return

            saved = 0
            for fact in facts[:8]:  # Cap at 8 per exchange
                if not isinstance(fact, dict):
                    continue
                content = fact.get("content", "").strip()
                category = fact.get("category", "general")
                if content and 3 < len(content) < 500:
                    result = memory_service.save_memory(
                        user_id=user_id,
                        content=content,
                        category=category,
                        source="auto_extract",
                    )
                    if result.get("action") == "saved":
                        saved += 1

            if saved > 0:
                logger.info(f"Auto-extracted {saved} memories for user {user_id}")

        except json.JSONDecodeError:
            logger.debug(f"Memory extraction returned non-JSON for user {user_id}")
        except Exception as e:
            logger.warning(f"Memory extraction error for user {user_id}: {type(e).__name__}: {e}")

    def _select_model(self, user_input):
        """Select model based on request complexity.

        Returns (model_id, max_tokens) — Sonnet+2048 for complex, None+1024 for default Haiku.
        """
        sonnet = "claude-sonnet-4-5-20250929"
        lower = user_input.lower()
        complex_triggers = [
            "what should i train", "program", "workout plan",
            "give me a session", "give me a workout",
            "analyze", "bloodwork", "labs", "biomarkers",
            "plan my week", "review my",
            "connect whoop", "connect my whoop",
            "how's my recovery", "how is my recovery", "what's my recovery",
            "how am i progressing", "how's my protocol", "how is my protocol",
            "diagnose", "should i train", "should i work out",
            "what does my whoop", "show my sleep",
        ]
        for trigger in complex_triggers:
            if trigger in lower:
                return sonnet, 2048
        return None, 1024

    async def process(self, user_input: str, user: dict, tasks: list = None, typing_callback=None) -> str | None:
        """Agent loop: call Claude with tools, execute tools, repeat until text response.

        typing_callback: optional async callable to refresh typing indicator between turns.
        """
        from bot.ai.tools_v2 import get_tool_definitions, execute_tool
        from bot.ai import memory_pg as memory
        from bot.services.tier_service import check_limit, track_usage

        self._paywall_hit = False

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None

        user_id = user["id"]
        tier = user.get("tier", "free")
        is_admin = user.get("is_admin", False)

        # Check AI message limit
        telegram_user_id = user.get("telegram_user_id")
        allowed, msg = check_limit(user_id, "ai_message", tier, is_admin=is_admin, telegram_user_id=telegram_user_id)
        if not allowed:
            self._paywall_hit = True
            return msg

        try:
            # Track usage
            track_usage(user_id, "ai_message")

            # Load conversation history + append new user message
            messages = memory.get_history(user_id)
            messages.append({"role": "user", "content": user_input})

            # Build system prompt as cached blocks (static = cached, dynamic = fresh)
            static_text = self._get_static_prompt()
            dynamic_text = self._build_dynamic_context(user, tasks or [])
            system = [
                {"type": "text", "text": static_text, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": dynamic_text},
            ]

            tools = get_tool_definitions()
            # Mark last tool for caching (tool defs are identical every request)
            if tools:
                tools[-1]["cache_control"] = {"type": "ephemeral"}

            model, max_tokens = self._select_model(user_input)
            max_turns = int(os.environ.get("AGENT_MAX_TURNS", "7"))
            response = None

            for turn in range(max_turns):
                # Refresh typing indicator between turns so dots stay visible
                if typing_callback and turn > 0:
                    try:
                        await typing_callback()
                    except Exception:
                        pass

                response, error = _call_api(system, messages, tools=tools, model=model, max_tokens=max_tokens)

                if error:
                    logger.error(f"Agent API error on turn {turn}: {error}")
                    error_msg = f"Hmm, hit a snag: {error}"
                    # Save only once — the success path at the bottom handles normal saves
                    if turn == 0:
                        memory.save_turn(user_id, "user", user_input)
                    memory.save_turn(user_id, "assistant", error_msg)
                    return error_msg

                if not response or not response.content:
                    if turn == 0:
                        memory.save_turn(user_id, "user", user_input)
                    memory.save_turn(user_id, "assistant", "Something went wrong processing that.")
                    return None

                # Serialize assistant response for message history
                assistant_content = []
                for block in response.content:
                    if hasattr(block, "text"):
                        assistant_content.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })

                messages.append({"role": "assistant", "content": assistant_content})

                # Check stop reason — break on max_tokens to avoid truncated loops
                if hasattr(response, "stop_reason") and response.stop_reason == "max_tokens":
                    logger.warning(f"Agent hit max_tokens on turn {turn}")
                    break

                # Check for tool calls
                tool_calls = [b for b in response.content if b.type == "tool_use"]
                if not tool_calls:
                    break

                # Execute each tool call (user-scoped)
                tool_results = []
                for call in tool_calls:
                    logger.info(f"Tool call: {call.name}({json.dumps(call.input)[:200]})")
                    result = await execute_tool(call.name, call.input, user_id)
                    logger.info(f"Tool result: {call.name} -> {json.dumps(result)[:200]}")

                    # Detect interactive workout session creation
                    if isinstance(result, dict) and result.get("_interactive_session"):
                        self._pending_session[user_id] = result["session_id"]

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": json.dumps(result),
                    })

                messages.append({"role": "user", "content": tool_results})

            # Extract final text
            text_parts = []
            if response and response.content:
                for block in response.content:
                    if hasattr(block, "text") and block.text:
                        text_parts.append(block.text)

            final_text = "\n".join(text_parts) if text_parts else None

            # If agent exhausted all turns with tool calls but no text response,
            # force a final text-only API call so the user always gets a reply
            if not final_text and response and response.stop_reason == "tool_use":
                logger.warning(f"Agent exhausted {max_turns} turns without text response — forcing final reply")
                messages.append({
                    "role": "user",
                    "content": "Please provide your final response to the user based on the tool results above. Be concise."
                })
                final_resp, final_err = _call_api(system, messages, tools=None, model=model, max_tokens=max_tokens)
                if final_resp and final_resp.content:
                    for block in final_resp.content:
                        if hasattr(block, "text") and block.text:
                            text_parts.append(block.text)
                    final_text = "\n".join(text_parts) if text_parts else None

            # Save to persistent memory
            memory.save_turn(user_id, "user", user_input)
            if final_text:
                memory.save_turn(user_id, "assistant", final_text)

            # Auto-extract memories in background (doesn't block response delivery)
            if final_text:
                asyncio.create_task(self._extract_memories(user_id, user_input, final_text))

            return final_text

        except Exception as e:
            logger.error(f"Agent loop failed: {type(e).__name__}: {e}")
            return "Something went wrong processing that. Try again or use a /command."


# Singleton
ai_brain = AIBrain()
