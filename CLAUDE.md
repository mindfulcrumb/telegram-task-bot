# Zoe — Telegram AI Companion Bot (@Meet_Zoe_Bot)

Multi-user SaaS Telegram bot. AI-powered task management, fitness coaching, biohacking concierge, WHOOP integration, proactive coaching. Deployed on Railway with PostgreSQL.

## Tech Stack

- Python 3.11, python-telegram-bot v21 (async)
- Claude API (anthropic SDK) with native tool_use for agent loop
- Claude Vision (Haiku) for blood test photo extraction
- Prompt caching: static prompt cached (90% cost reduction), dynamic context per-request
- Model routing: Haiku default ($1/$5/M), Sonnet for complex requests ($3/$15/M)
- PostgreSQL on Railway (psycopg2-binary) — 28 tables
- WHOOP API v2 (OAuth2, webhooks, recovery/sleep/strain sync)
- Strava API v3 (OAuth2, webhooks, activity sync, running analytics)
- Groq Whisper for voice transcription
- YouTube Transcript API for podcast content extraction
- iCal parsing for Google Calendar (icalendar library)
- Deployed on Railway (auto-deploys from GitHub mindfulcrumb/telegram-task-bot)

## Architecture

```
User (Telegram) → bot/main_v2.py → handlers/ → bot/ai/brain_v2.py (agent loop)
                                                       ↓
                                            Claude API (tool_use, prompt caching)
                                                       ↓
                                               bot/ai/tools_v2.py (32 tools)
                                                       ↓
                                        PostgreSQL (28 tables — tasks, fitness, biohacking, WHOOP, Strava, etc.)
```

### Key Files

- `bot/main_v2.py` — Entry point, handler registration, WHOOP OAuth callback + webhook, health check
- `bot/ai/brain_v2.py` — Zoe AI brain: static prompt (cached), dynamic context, agent loop, model routing
- `bot/ai/tools_v2.py` — 27 tools: tasks (8), fitness (6), biohacking (6), WHOOP (2), memory (2), knowledge (3)
- `bot/ai/memory_pg.py` — PostgreSQL conversation history (10 turn limit, daily pruning)
- `bot/handlers/onboarding.py` — /start, /help, /settings, /account, /calendar, /deleteaccount
- `bot/handlers/tasks_v2.py` — 23 commands: task management, fitness, biohacking, WHOOP
- `bot/handlers/workout_session.py` — Interactive exercise cards, set tracking, rest timers
- `bot/handlers/payments.py` — Telegram Payments + Stripe (/upgrade, /terms, /support)
- `bot/handlers/proactive_v2.py` — 13 jobs: briefing, check-in, nudges, insights, reminders, pruning, session cleanup, dose reminders, research updates, content extraction, health check, Sunday review, progress report
- `bot/handlers/voice_v2.py` — Voice messages via Groq Whisper → AI brain (async, with timeout + markdown stripping)
- `bot/handlers/photo_handler.py` — Blood test photo upload → Claude Vision extraction → biomarker logging
- `bot/services/fitness_service.py` — Workout CRUD, pattern balance, PR detection, interactive sessions
- `bot/services/biohacking_service.py` — Peptide protocols, supplements, bloodwork, biomarker tracking
- `bot/services/whoop_service.py` — WHOOP OAuth2, data sync (v2 API), webhook handling with HMAC verification
- `bot/services/coaching_service.py` — Streaks, nudge dedup, check-ins, weekly stats
- `bot/services/memory_service.py` — User memory (persistent facts Zoe learns), response feedback
- `bot/services/task_service.py` — Task CRUD, recurring tasks, reminders
- `bot/services/user_service.py` — User management
- `bot/services/tier_service.py` — Free/Pro tier limits and usage tracking
- `bot/services/calendar_service.py` — Google Calendar via iCal URL
- `bot/services/content_extractor.py` — Deep content extraction: YouTube transcripts, PubMed abstracts, RSS articles → Haiku protocol extraction → KB
- `bot/services/knowledge_service.py` — Knowledge base CRUD, search, RSS auto-updates
- `bot/data/seed_knowledge_v3.py` — 15 deep expert protocol entries (Koniver, Jay Campbell, Epitalon, etc.)
- `bot/db/database.py` — PostgreSQL schema (24 tables), connection pool, indexes
- `scripts/populate_kb.py` — CLI batch KB loader (manual use; primary path is cloud startup job)

## Current Features

### Free Tier
- 25 active tasks, 20 AI messages/day, 3 reminders
- Natural language task management (add, complete, edit, delete)
- Due dates, priorities, categories (auto-inferred by AI)
- Recurring tasks (daily, weekdays, weekly, monthly)
- Voice messages → transcription → AI processing
- Google Calendar integration (iCal URL)
- Basic fitness logging, body metrics
- Completion streaks

### Pro Tier (Zoe Pro)
- Unlimited everything
- AI fitness coaching, workout programming, PR tracking
- Interactive workout sessions with set tracking + rest timers
- Peptide protocol tracking + dose reminders
- Supplement stack management + adherence tracking
- Bloodwork intelligence + biomarker trends
- WHOOP integration + recovery-based training
- Personalized morning briefings (AI-generated, WHOOP-enhanced)
- Evening accountability check-ins
- Smart nudges + weekly performance insights
- Dose reminders (morning/evening based on protocol timing)

## Environment Variables (Railway)

- `TELEGRAM_BOT_TOKEN` — Bot token from BotFather (REQUIRED)
- `DATABASE_URL` — PostgreSQL connection string (REQUIRED)
- `ANTHROPIC_API_KEY` — Claude API key (REQUIRED for AI)
- `WHOOP_CLIENT_ID` — WHOOP OAuth client ID
- `WHOOP_CLIENT_SECRET` — WHOOP OAuth client secret
- `WHOOP_REDIRECT_URI` — Or auto-derived from RAILWAY_PUBLIC_DOMAIN
- `ADMIN_USER_IDS` — Comma-separated Telegram user IDs for admin commands
- `GROQ_API_KEY` — Groq API key for voice transcription
- `STRIPE_PROVIDER_TOKEN` — From BotFather Payments → Stripe (NOT SET YET)
- `RAILWAY_PUBLIC_DOMAIN` — telegram-task-bot-production-6784.up.railway.app
- `CLAUDE_MODEL` — Default model (claude-haiku-4-5-20251001)
- `CONVERSATION_HISTORY_LIMIT` — Default 10 turns
- `AGENT_MAX_TURNS` — Default 5

## Code Conventions

- Async everywhere (python-telegram-bot v21 requires it)
- Lazy imports inside functions to avoid circular dependencies
- All service layer is synchronous using `get_cursor()` context manager
- NO markdown formatting in Telegram messages (plain text only, no parse_mode for AI text)
- Zoe personality: thoughtful, warm, calm, human — not bubbly, not robotic, not corporate

## What's Working

- Full task management via chat and 23 commands
- Zoe AI personality with coaching context (streaks, patterns)
- Prompt caching (static prompt cached, 90% cost reduction)
- Model routing (Haiku default, Sonnet for complex requests)
- 27 AI tools (tasks, fitness, biohacking, WHOOP, memory, knowledge)
- Interactive workout sessions (exercise cards, set tracking, rest timers)
- Fitness tracking (workouts, exercises, pattern balance, PR detection)
- Biohacking tracking (peptide protocols, supplements, bloodwork)
- WHOOP integration (OAuth2, recovery/sleep/strain sync, v2 API)
- Voice messages transcription
- Recurring tasks (auto-spawn next on completion)
- Google Calendar read (iCal URL)
- User memory system (Zoe learns facts about users)
- Response feedback (thumbs up/down)
- 13 proactive jobs (briefing, check-in, nudges, insights, reminders, pruning, session cleanup, dose reminders, research updates, content extraction, health check, Sunday review, progress report)
- Blood test photo upload — Claude Vision extracts biomarkers, logs to DB, flags out-of-range
- Deep content extraction pipeline — YouTube transcripts, PubMed, RSS → Haiku summarization → KB entries
- Knowledge base: 261 v2 entries + 15 v3 expert protocols + auto-extracted content (1,500-3,000 chars each)
- Webhook HMAC signature verification for WHOOP
- Conversation pruning (daily, 7-day retention)
- Stale workout session cleanup (3-hour timeout)
- Health check server for Railway
- All sync work (content extraction, research) runs in asyncio.to_thread() to never block event loop

## What's Working (confirmed by user Mar 1, 2026)

- **Payments** — Stripe web flow, fully working
- **Strava** — OAuth + webhooks, fully working
- **Google Calendar** — OAuth, fully working
- **WHOOP** — OAuth + webhooks, fully working

## Remaining TODO

- No test suite
- No referral system yet
- **OAuth state is predictable** — uses `uid_{user_id}` not cryptographic nonce. Lower priority but noted.
- **Tokens stored as plaintext in DB** — would need `cryptography`/Fernet encryption
- **Sync psycopg2** — all DB calls are sync, blocks event loop under load. Needs asyncpg or to_thread() wrappers.
- **`date.today()` timezone mismatch** — streaks/proactive use server UTC, not user timezone
- **NexoParts memory purge** — run `railway run python scripts/purge_nexoparts.py`

## Session Log — Mar 1, 2026 (Session 9: Senior-Level Code Audit + Fixes)

### What was done:
Full codebase audit across all 14+ core files, then implemented fixes for 15 bugs across 9 files.

### Bugs fixed (by severity):

**CRITICAL:**
1. **`set_reminder` naive vs aware datetime** (tools_v2.py:589) — `datetime.fromisoformat()` can return aware datetime, `datetime.now()` is always naive. Comparing them crashes with TypeError. Fix: match timezone context dynamically.
2. **`set_reminder` unbound `u` variable** (tools_v2.py:581) — If `user_service.get_user_by_id()` fails, `u` was referenced outside the try/except → `NameError` crash. Fix: Initialize `u = None` before try block.
3. **`finish_session` duplicate workouts** (fitness_service.py:644) — Double-tap "Finish Workout" could create 2 workout records. Fix: Atomic `UPDATE ... WHERE status = 'active' RETURNING *` claims the session, preventing duplicates.

**HIGH:**
4. **WHOOP webhook auth bypass** (main_v2.py:280) — Missing signature headers allowed unsigned requests through. Fix: When `WHOOP_WEBHOOK_SECRET` is configured, signature headers are REQUIRED (401 if missing).
5. **Webhook payload size limit** (main_v2.py:274) — No body size limit → DoS risk. Fix: Reject payloads > 1MB.
6. **`/whoop/debug` config leak** (main_v2.py:76) — Exposed WHOOP client config without auth. Fix: Requires `DEBUG_TOKEN` query param.
7. **`_paywall_hit` race condition** (brain_v2.py:109) — Singleton `AIBrain` stored paywall flag as flat bool — concurrent users overwrite each other. Fix: Changed to dict keyed by `user_id`. Updated 3 callers (tasks_v2.py, voice_v2.py, photo_handler.py).
8. **`_call_api` blocking event loop** (brain_v2.py:1253) — Sync Anthropic API call (up to 60s) blocked the entire event loop for all users. Fix: Wrapped both `_call_api` invocations in `asyncio.to_thread()`.
9. **User message lost on turn > 0 errors** (brain_v2.py:1259) — If API error occurred after turn 0, user's message was never saved to conversation history. Fix: Always save user message on error regardless of turn.
10. **`_select_model` missing workout triggers** (brain_v2.py:1178) — "was my workout good", "analyze my session", etc. were routed to Haiku instead of Sonnet. Fix: Added 7 workout analysis trigger phrases.

**MEDIUM:**
11. **`manage_peptide_protocol` missing return** (tools_v2.py:812) — Invalid action (not add/pause/resume/end) fell through returning `None`. Fix: Added explicit error return for unknown actions.
12. **Settings shows stale values** (onboarding.py:844) — `/settings timezone X` showed OLD timezone first, then applied change. Fix: Process args before displaying settings.
13. **No timezone validation** (onboarding.py:863) — Any string accepted as timezone. Fix: Validate with `zoneinfo.ZoneInfo()`, return helpful error if invalid.
14. **`_escape_md` over-escaping** (workout_session.py:22) — Escaped 20 Markdown v2 chars but used `parse_mode='Markdown'` (v1). Caused visible backslashes in exercise cards. Fix: Only escape v1 chars (`_`, `*`, `` ` ``, `[`).

### Files modified (9 files):
- `bot/ai/tools_v2.py` — 3 fixes (unbound `u`, datetime comparison, peptide protocol return)
- `bot/ai/brain_v2.py` — 4 fixes (paywall race condition, async _call_api, message loss, model triggers)
- `bot/services/fitness_service.py` — 1 fix (finish_session idempotency)
- `bot/main_v2.py` — 3 fixes (webhook auth, payload limit, debug auth)
- `bot/handlers/onboarding.py` — 2 fixes (timezone validation, settings order)
- `bot/handlers/workout_session.py` — 1 fix (_escape_md over-escaping)
- `bot/handlers/tasks_v2.py` — 1 fix (paywall_hit dict access)
- `bot/handlers/voice_v2.py` — 1 fix (paywall_hit dict access)
- `bot/handlers/photo_handler.py` — 1 fix (paywall_hit dict access)

### Verification:
- All 9 modified files compile clean (py_compile)

### Remaining lower-priority items (not fixed — larger refactors):
- **Sync psycopg2 blocking event loop** — All DB calls are sync. Would need `asyncpg` or `to_thread()` wrappers on every DB function. Significant refactor.
- **Sync httpx in whoop_service.py** — Uses sync `httpx.Client`. Would need async client or `to_thread()` wrappers.
- **`date.today()` timezone mismatch** — Used in streaks, `has_workout_today`, proactive checks. Uses server UTC not user timezone. Needs user timezone lookup helper.
- **OAuth state predictable** — Uses `uid_{user_id}`. Needs cryptographic nonce + DB state table.
- **`_undo_buffer` unbounded growth** — In-memory dict, no cleanup. Low risk in practice.
- **`_rest_state` lost on restart** — In-memory dict for rest timers. Would need DB-backed state.
- **`db_user` cache staleness** — Tier can change mid-session but cached user dict isn't refreshed.

---

## Session Log — Mar 1, 2026 (Session 8: WHOOP Workout Analysis Algorithm)

### What was done:
1. **Workout-Recovery Analysis Algorithm** — Built `analyze_workout_vs_recovery()` in `whoop_service.py`. Cross-references workout intensity against WHOOP recovery data to score alignment (0-100) and provide specific feedback.
2. **Workout Intensity Classifier** — `_classify_workout_intensity()` scores exercise data (compound density, volume, rep ranges, weight, RPE) into high/moderate/low with reasoning.
3. **9-cell Alignment Matrix** — Maps (recovery_zone x intensity_level) to verdicts: dialed_in, undertrained, missed_opportunity, overreached, reckless, smart, cautious_ok.
4. **5 Modifier Layers** — HRV trend (fatigue accumulation), sleep quality gate, deep sleep CNS gate, cumulative strain (3-day), HRV deficit vs personal baseline.
5. **New AI Tool** — `analyze_workout` tool definition + executor in tools_v2.py. Handles workout_id or date-based lookup, falls back to most recent workout.
6. **System Prompt Updates** — Added WORKOUT ANALYSIS section to brain_v2.py with response format guidelines, proactive trigger rules, and connection to existing workout logging flow.
7. **Helper Functions** — `get_whoop_for_date()` (date-specific WHOOP lookup), `get_multi_day_strain()` (cumulative strain history).

### How the algorithm works:
- Classifies workout intensity from exercise data (compounds, weight, reps, RPE, volume)
- Cross-references against recovery zone (green/yellow/red) via alignment matrix
- Applies 5 modifiers: HRV trend, sleep %, deep sleep, cumulative strain, HRV vs baseline
- Returns alignment_score, verdict, what_was_good, what_to_change, alternative_session
- AI is instructed to lead with verdict, never show raw score, keep to 3-4 lines

### Files modified:
- `bot/services/whoop_service.py` — Added ~200 lines: analysis algorithm, intensity classifier, alignment matrix, helper functions
- `bot/ai/tools_v2.py` — Added tool definition + ~50-line executor with workout lookup logic
- `bot/ai/brain_v2.py` — Added WORKOUT ANALYSIS section to system prompt, updated WHEN SOMEONE LOGS A WORKOUT

### Verification:
- All 3 modified files compile clean (py_compile)

### What still needs doing:
- Deploy to Railway (push to main) to go live
- Test with real WHOOP data and workout history
- Consider adding proactive post-workout analysis in proactive_v2.py evening check-in

---

## Session Log — Mar 1, 2026 (Session 7)

### What was done:
1. **Full codebase audit** — Read and audited all 18 core Python files (main_v2.py, brain_v2.py, tools_v2.py, memory_pg.py, tasks_v2.py, proactive_v2.py, workout_session.py, whoop_service.py, fitness_service.py, database.py, onboarding.py, message_utils.py, etc.)
2. **XSS fix (CRITICAL)** — Added `html.escape()` to all 6 dynamic HTML injection points in OAuth callbacks (Google Calendar + WHOOP) in main_v2.py. Previous security audit claimed this was done but it wasn't in the code.
3. **Workout card Markdown escape (CRITICAL)** — Added `_escape_md()` function to workout_session.py. Exercise names/notes with `_`, `*`, `[`, `` ` `` now properly escaped. Added plaintext fallbacks on all 4 Markdown send paths (send, edit/refresh, timer callback). Previous security audit said this was done but the function didn't exist.
4. **WHOOP webhook fail-closed (SECURITY)** — Changed `verify_webhook_signature()` to return `False` when client secret is missing (was returning `True`, allowing unauthenticated webhooks). Security audit said "fail closed" but code said otherwise.
5. **CONVERSATION_HISTORY_LIMIT default fixed** — Changed from 20 to 10 in memory_pg.py. Was sending 2x the tokens per request vs documented default.
6. **Anthropic client singleton** — Replaced per-request `anthropic.Anthropic()` instantiation with `_get_client()` singleton in brain_v2.py. Reuses HTTP connection pool, reduces connection churn.
7. **_upsert_daily race condition fixed** — Replaced check-then-insert pattern with atomic `INSERT ... ON CONFLICT DO UPDATE` in whoop_service.py. Prevents unique constraint violations from concurrent WHOOP sync calls.
8. **_user_now() timezone fix** — Changed fallback from naive `datetime.now()` to timezone-aware `datetime.now(timezone.utc)`. Prevents comparison errors with tz-aware datetimes elsewhere.

### Known remaining items (lower priority):
- OAuth state parameter uses predictable `uid_{user_id}` — should be cryptographic nonce with DB-backed validation
- No `oauth_states` table exists despite previous security audit claiming it was added
- `_undo_buffer` dict grows without bounds (in-memory, per-user, only cleared on /undo)
- `_rest_state` dict is in-memory, lost on Railway restart
- `db_user` cached in `context.user_data` — stale if tier changes mid-session
- SQL INTERVAL with string formatting (`'%s days'`) is safe but fragile

### Files modified:
- `bot/main_v2.py` — 7 edits (html import, 6 XSS fixes in OAuth + error pages)
- `bot/handlers/workout_session.py` — 5 edits (_escape_md function, name/notes escaping, 3 plaintext fallbacks)
- `bot/ai/brain_v2.py` — 2 edits (Anthropic client singleton, _user_now UTC fallback)
- `bot/ai/memory_pg.py` — 1 edit (history limit 20→10)
- `bot/services/whoop_service.py` — 2 edits (_upsert_daily ON CONFLICT, webhook fail-closed)

### Verification:
- All 18 core Python files compile clean (py_compile)

## Session Log — Feb 26, 2026 (Session 6)

### What was done:
1. **Brand voice audit** — Full audit of every user-facing string across all active handler files against Zoe's brand voice rules (brain_v2.py lines 108-145). Found and fixed violations in 5 files.
2. **Stripped parse_mode="Markdown"** from 15+ conversational messages across all handlers. Kept it ONLY on workout exercise cards (structured UI, not conversation).
3. **Killed corporate language** — Removed: "Stay tuned!", "Enjoy unlimited everything", "You're all set!", "Unlock recovery-based training", "Thanks for trusting me", "Good morning, {name}!"
4. **Fixed formatting violations** — Converted hyphen-as-bullets to flowing text or indented items, removed bold markdown headers (`*Tasks*`, `**Active Protocols**`), removed emoji-as-bullet-points in nudges.
5. **Rewrote copy to match voice** — Payments, /help, /support, /terms, proactive nudges, morning briefings, evening check-ins, weekly recaps all rewritten in Zoe's casual, short, opinionated voice.

### Key decision:
- **Exercise cards keep parse_mode="Markdown"** — These are structured interactive UI (set progress checkboxes, timer buttons), not conversational Zoe text. All other messages are plain text only.

### Files modified:
- `bot/handlers/onboarding.py` — 9 edits (referral, /memory, /help, capabilities, timezone, delete account, location)
- `bot/handlers/tasks_v2.py` — 10 edits (/streak, /metrics, /gains, /protocols, /supplements, /bloodwork, WHOOP commands)
- `bot/handlers/proactive_v2.py` — 7 edits (evening check-in, nudges, dose reminders, briefing, assessment, weekly templates)
- `bot/handlers/payments.py` — 5 edits (/upgrade, successful payment, /terms, /support)
- `bot/handlers/workout_session.py` — 3 edits (completion text, timer notification)

### Verification:
- All 12 critical Python files compile clean (py_compile)
- Zero `parse_mode="Markdown"` in conversational handlers (only in workout_session exercise cards)
- Zero corporate language patterns remaining
- Zero markdown formatting characters in user-facing strings
- Bot confirmed live on Railway (Telegram API getMe = OK)
- Legacy files (voice.py, tasks.py, proactive.py, reminders.py) confirmed NOT imported in main_v2.py — dead code, untouched

### Commits:
- `516dcc7` — Brand voice audit — strip markdown, kill corporate language across all handlers

## Session Log — Feb 25-26, 2026 (Session 5)

### What was done:
1. **Elite workout programming brain** — Full exercise library, movement pattern balance, progressive overload, deload weeks, RPE-based auto-regulation
2. **Owner program seed** — Seeded the user's actual training program into the bot
3. **Onboarding upgrade** — Equipment selection, training style, injury history, biohacking preferences + memory seeding from onboarding answers
4. **/memory command** — Users can see what Zoe remembers about them
5. **Typing delays + voice rewrite** — Added realistic typing delays to onboarding, rewrote all onboarding copy in Zoe's voice

### Commits:
- `0d9341f` — Elite workout programming brain + owner program seed
- `ef9bb3d` — Onboarding upgrade: equipment, style, injuries, biohacking + memory seeding
- `85f2b12` — /memory command
- `67a750b` — Human feel: typing delays + Zoe's voice in onboarding

## Session Log — Feb 25, 2026 (Session 4)

### What was done:
1. **Deep content extraction pipeline** — `content_extractor.py` extracts protocols from YouTube transcripts (Huberman, Attia, DOAC), PubMed full abstracts, Jay Campbell RSS articles. Chunks transcripts by 12-min segments, uses Haiku to extract actionable protocols as 200-400 word KB entries.
2. **Expert protocol seeds (v3)** — 15 deep entries covering Koniver NAD IV, Jay Campbell GLOW/KLOW/TOT, Epitalon+Thymalin, Hexarelin, BPC-157+TB-500, Ipamorelin/CJC-1295, GLP-1 comparison, nootropic peptides, expert consensus map.
3. **Cloud-first extraction** — `initial_content_extraction_job` runs 5 min after Railway deploy (not locally). Monday research job also runs new crawlers.
4. **Critical fix: event loop blocking** — Content extraction was running sync HTTP/API calls on the async event loop, freezing the entire bot for 10+ minutes. All sync work now wrapped in `asyncio.to_thread()`.
5. **Critical fix: voice/text silent failures** — (a) `_transcribe()` changed from blocking `httpx.post()` to async `httpx.AsyncClient`, (b) `brain_v2.process()` forces final text reply when all turns are tool_use, (c) 120s `asyncio.wait_for()` timeout on both handlers.
6. **Markdown stripping in voice handler** — Added `_clean_response()` to voice handler (text handler already had it).
7. **Blood test photo upload** — `photo_handler.py` uses Claude Vision (Haiku) to detect blood tests, extract all biomarkers (name, value, unit, reference ranges), log to DB via `biohacking_service.log_bloodwork()`, flag out-of-range markers.
8. **Database** — Added `content_processing_log` table (24th table) for extraction tracking/dedup/resumability.

### Commits:
- `2bac141` — Deep content extraction pipeline + v2/v3 knowledge
- `66a2933` — Auto-extraction on Railway deploy
- `664a857` — Voice/text silent failure fixes (3 critical issues)
- `bb17bb9` — Event loop blocking fix + voice markdown stripping
- `51c8fd0` — Blood test photo upload via Claude Vision

## Session Log — Mar 1, 2026 (Session 11)

### What was done:
1. **Committed Session 10** (protocol system + feature discovery) — commit `57492bb`
2. **Applied Session 8+9 stash** — resolved 8 merge conflicts (brain_v2, tools_v2, onboarding, photo_handler, tasks_v2, workout_session, main_v2, whoop_service) — commit `b56ac88`
3. **Bug fix**: `_undo_buffer` unbounded growth — capped to 50 users with FIFO eviction
4. **Full Strava Integration** (commit `7c71929`, 1544 lines across 6 files):
   - `bot/services/strava_service.py` (NEW, ~936 lines): Complete Strava service
     - OAuth2: auth URL, code exchange, token refresh (6h expiry), revoke
     - API helpers with rate limit handling
     - Activity sync: recent activities, detailed data (splits + best efforts)
     - Running analytics: ACWR training load, pace consistency (split evenness), race predictions (Riegel formula), HR efficiency trends, shoe mileage alerts
     - Cross-domain: Strava x WHOOP correlations (recovery vs run performance, long run impact on next-day recovery)
     - Webhook handling (activity CRUD + athlete deauth)
   - `bot/db/database.py`: 4 new tables (strava_tokens, strava_activities, strava_best_efforts, strava_splits) with performance indexes
   - `bot/ai/brain_v2.py`: Running coach knowledge engine (pace zones, 80/20 rule, periodization, PR strategies by distance, ACWR coaching, form cues, weather adjustments, shoe rotation, cross-training) + `_build_strava_section()` dynamic context
   - `bot/ai/tools_v2.py`: 5 new tools (connect_strava, disconnect_strava, get_strava_summary, get_running_analysis, get_run_details)
   - `bot/main_v2.py`: Strava OAuth callback, webhook endpoint + subscription verification (GET), onboarding message
   - `bot/handlers/proactive_v2.py`: Strava sync job (every 2h for all connected users)

### Status:
- All files compile clean
- Committed and pushed to main → Railway auto-deploys
- 3 commits pushed: Session 10, stash merge, Session 11

### Environment variables needed (Railway):
- `STRAVA_CLIENT_ID` — from Strava API application
- `STRAVA_CLIENT_SECRET` — from Strava API application
- `STRAVA_REDIRECT_URI` — (optional, auto-derives from RAILWAY_PUBLIC_DOMAIN)
- `STRAVA_WEBHOOK_VERIFY_TOKEN` — defaults to "zoe_strava_verify"

### What's next:
- Set up Strava API application at https://www.strava.com/settings/api
- Add env vars to Railway
- Register Strava webhook subscription
- Test OAuth flow end-to-end
- Consider adding Oura Ring integration (next in market study roadmap)

## Session Log — Mar 1, 2026 (Session 10)

### What was done:
1. **Interactive Peptide Protocol System** — Full implementation across 8 files + 1 new file
   - `bot/handlers/protocol_cards.py` (NEW, ~1100 lines): wizard (pw:), dashboard (pd:), dose reminders (dr:), quick dose (qd:)
   - `bot/db/database.py`: protocol_schedules + scheduled_doses tables, card_message_id column, hint_log table
   - `bot/services/biohacking_service.py`: ~300 lines new — schedule CRUD, daily dose generation, adherence engine
   - `bot/ai/tools_v2.py`: 2 new tools (start_protocol_wizard, get_protocol_dashboard) + executors
   - `bot/ai/brain_v2.py`: _pending_protocol_wizard/_pending_protocol_dashboard detection, peptide timing rules, dose safety rules
   - `bot/main_v2.py`: registered pw:, pd:, dr:, qd: callback handlers
   - `bot/handlers/tasks_v2.py`: wizard text interceptor, pending detection, /protocols → dashboard, /dose → quick buttons
   - `bot/handlers/proactive_v2.py`: interactive dose reminder cards (15-min schedule-based), generate_daily_doses_job
2. **Feature Discovery System** — subtle UI guidance hints (brain_v2.py, database.py, proactive_v2.py, voice_v2.py)
3. **Conversation Audit** — reviewed 232 messages, found 12 issues, fixed 3 critical:
   - Peptide timing (HGH on empty stomach) never enforced → added comprehensive timing rules
   - Semaglutide/Retatrutide confusion → added misspelling variants + task rename rule
   - Dose increase recommendation → added DOSE CHANGE SAFETY section
4. **Git state**: pulled 50+ remote commits, stashed Session 8+9 local changes (stash@{0})

### Status:
- All 8 modified files compile clean (py_compile verified)
- NOT YET COMMITTED — needs commit + push to main → Railway auto-deploys
- stash@{0} has Session 8+9 changes (15-bug audit + WHOOP workout analysis) — may conflict

## Session Log — Feb 25, 2026 (Session 3)

### What was done:
1. **Legal disclaimers** — added to onboarding, AI prompt, and bot description (simplified after user feedback)
2. **Typing indicator fix** — background asyncio loop refreshes every 4s so dots stay visible during API calls
3. **Voice handler fix** — removed transcription echo, added fallback when AI returns None
4. **Google Calendar OAuth** — full rewrite from iCal to OAuth 2.0 (following WHOOP pattern). Added `google_calendar_tokens` table, OAuth flow with auto-refresh, `/google/callback` route, inline button in `/calendar` command. iCal kept as fallback.
5. **Knowledge base system** — 261 seeded entries, RSS auto-updates, research service
6. **Bot menu updated** — expanded from 10 to 17 commands
7. **Google Cloud Console setup** — guided user through project creation, OAuth credentials (Web application), Calendar API enablement, test user addition
8. **Stripe/payments** — Stripe not available in user's region via BotFather. User applying through available providers (Smart Glocal/Unlimint). Code ready, just needs token.

### Commits:
- `7a782bf` — Knowledge base system with 261 seeded entries and RSS auto-updates
- `6502f13` — Legal disclaimers for health/wellness content compliance
- `807292c` — Google Calendar OAuth + typing indicator fix + voice fix
- `2a7c034` — Telegram bot menu with all key commands

### Railway domain:
`telegram-task-bot-production-6784.up.railway.app`

## Session Log — Feb 25, 2026 (Session 2)

### What was done:
1. Comprehensive 5-agent system audit (database, services, tools/brain, handlers, WHOOP API)
2. **WHOOP v1 → v2 migration**: Updated API base URL from deprecated v1 to v2, added webhook HMAC-SHA256 signature verification, whitelisted _upsert_daily fields, wrapped token storage in try/except
3. **Brain optimizations**: Increased max_tokens to 2048 for Sonnet complex requests, expanded model routing triggers (recovery, protocol, progressing, diagnose, etc.), fixed duplicate conversation saves on error path, added max_tokens stop reason handling
4. **Proactive fixes**: Fixed _call_api positional args (was using keyword system_prompt=), removed parse_mode="Markdown" from all proactive messages (AI text was showing literal asterisks), stripped markdown from template fallbacks, fixed prompts to say "no markdown"
5. **New jobs**: Conversation pruning (daily, 7d retention), stale session cleanup (2h), dose reminder job (4h, morning/evening based on protocol timing)
6. **Database**: Added 7 performance indexes (tasks due_date, conversations created_at, peptide_logs protocol, supplement_logs supplement, biomarkers bloodwork, workout sessions, exercise names)
7. **Memory**: Fixed prune_old SQL (INTERVAL with make_interval), added deletion count logging

### Previous session (Feb 25, Session 1):
- Cost optimization: Split system prompt into static (cached) + dynamic blocks, added model routing (Haiku default, Sonnet for complex), reduced projected costs 88-91%
- WHOOP data fix: Added score_state checking, fetch limit=5, comprehensive logging
- Fixed /recovery and /whoop commands (removed parse_mode="Markdown")
- Memory system implementation (commit e55e0b0)
- Workout UX refactor (commit 53a9a48)

### Commits this session:
- `7b9113a` — Comprehensive audit fixes (WHOOP v2, brain optimizations, proactive improvements)
- Previous: `1ae9e7f` (WHOOP fix), `f107bd5` (cost optimization)

---

## Session Log — Mar 3, 2026 (Session 12 — Stability & Error Visibility)

### What was done:
1. **Diagnosed 93MB error log crash loop**: Found local launchd agent `com.whiz.taskbot` continuously restarting the bot with broken venv, causing 700K+ crash cycles. Unloaded the agent permanently.
2. **Fixed local environment**: Killed auto-restart process, created fresh venv with Python 3.14 (matching Railway Python 3+), cleared bot.error.log
3. **Added smoke test script** — `scripts/smoke_test.py`: Pre-deploy verification that checks:
   - All critical env vars present (TELEGRAM_BOT_TOKEN, DATABASE_URL, ANTHROPIC_API_KEY)
   - All imports load without error (brain, tools, services, DB)
   - Anthropic API is callable
   - Database connection works
   - Runs in 3 seconds, exit code 0 = safe to deploy, 1 = blocked
4. **Added pre-push checklist** — `CHECKLIST.md`: Enforces discipline before every deploy:
   - Run smoke_test.py (non-negotiable)
   - Update CLAUDE.md with session summary
   - Verify Railway health is green
   - Check for untracked files
5. **Added error visibility**:
   - Enhanced exception handler in `brain_v2.py` (line ~2565) to log full context: user_id, model, turn number, full traceback
   - Added Sentry SDK initialization to `main_v2.py` (if SENTRY_DSN env var set) for production error tracking
   - Fallback: if Sentry unavailable, structured logging still goes to Railway logs
6. **Identified remaining blockers** (from CLAUDE.md audit):
   - `STRIPE_PROVIDER_TOKEN` not set in Railway → payments broken
   - Strava env vars (CLIENT_ID, CLIENT_SECRET, WEBHOOK_VERIFY_TOKEN) likely not confirmed → OAuth broken
   - Sync psycopg2 blocks event loop under concurrent users (architectural issue, lower priority)
   - NexoParts memory purge still pending (`railway run python scripts/purge_nexoparts.py`)

### Status:
- ✓ Local environment fixed (no more crash loop)
- ✓ Error visibility infrastructure in place
- ✓ Pre-deploy smoke test automated
- ✓ Session discipline checklist created
- ⚠ STRIPE_PROVIDER_TOKEN and Strava env vars NOT YET SET IN RAILWAY
- ⚠ NexoParts purge NOT YET RUN

### What's next:
1. Set STRIPE_PROVIDER_TOKEN in Railway (get from Smart Glocal or Unlimint)
2. Confirm Strava env vars in Railway (or set them if missing)
3. Run `railway run python scripts/purge_nexoparts.py` to clear NexoParts from conversation memory
4. Test full deploy flow: local smoke_test.py → git push → Railway auto-deploy → health check green
5. (Nice-to-have) Add Sentry account + configure SENTRY_DSN if error tracking is desired

### Key insight:
The bot wasn't "broken" — it was a missing deployment infrastructure. Three systemic gaps were causing errors to keep appearing:
1. No automated verification (just "compiles clean" ≠ working)
2. No error visibility (found out about crashes via 93MB log file)
3. No session discipline (every session left code in uncertain state)

With these three gaps closed, bugs will announce themselves before reaching users.
