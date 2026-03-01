# Zoe — Telegram AI Companion Bot (@Meet_Zoe_Bot)

Multi-user SaaS Telegram bot. AI-powered task management, fitness coaching, biohacking concierge, WHOOP integration, proactive coaching. Deployed on Railway with PostgreSQL.

## Tech Stack

- Python 3.11, python-telegram-bot v21 (async)
- Claude API (anthropic SDK) with native tool_use for agent loop
- Prompt caching: static prompt cached (90% cost reduction), dynamic context per-request
- Model routing: Haiku default ($1/$5/M), Sonnet for complex requests ($3/$15/M)
- PostgreSQL on Railway (psycopg2-binary) — 23 tables
- WHOOP API v2 (OAuth2, webhooks, recovery/sleep/strain sync)
- Groq Whisper for voice transcription
- iCal parsing for Google Calendar (icalendar library)
- Deployed on Railway (auto-deploys from GitHub mindfulcrumb/telegram-task-bot)

## Architecture

```
User (Telegram) → bot/main_v2.py → handlers/ → bot/ai/brain_v2.py (agent loop)
                                                       ↓
                                            Claude API (tool_use, prompt caching)
                                                       ↓
                                               bot/ai/tools_v2.py (24 tools)
                                                       ↓
                                        PostgreSQL (23 tables — tasks, fitness, biohacking, WHOOP, etc.)
```

### Key Files

- `bot/main_v2.py` — Entry point, handler registration, WHOOP OAuth callback + webhook, health check
- `bot/ai/brain_v2.py` — Zoe AI brain: static prompt (cached), dynamic context, agent loop, model routing
- `bot/ai/tools_v2.py` — 24 tools: tasks (8), fitness (6), biohacking (6), WHOOP (2), memory (2)
- `bot/ai/memory_pg.py` — PostgreSQL conversation history (10 turn limit, daily pruning)
- `bot/handlers/onboarding.py` — /start, /help, /settings, /account, /calendar, /deleteaccount
- `bot/handlers/tasks_v2.py` — 23 commands: task management, fitness, biohacking, WHOOP
- `bot/handlers/workout_session.py` — Interactive exercise cards, set tracking, rest timers
- `bot/handlers/payments.py` — Telegram Payments + Stripe (/upgrade, /terms, /support)
- `bot/handlers/proactive_v2.py` — 8 jobs: briefing, check-in, nudges, insights, reminders, pruning, session cleanup, dose reminders
- `bot/handlers/voice_v2.py` — Voice messages via Groq Whisper → AI brain
- `bot/services/fitness_service.py` — Workout CRUD, pattern balance, PR detection, interactive sessions
- `bot/services/biohacking_service.py` — Peptide protocols, supplements, bloodwork, biomarker tracking
- `bot/services/whoop_service.py` — WHOOP OAuth2, data sync (v2 API), webhook handling with HMAC verification
- `bot/services/coaching_service.py` — Streaks, nudge dedup, check-ins, weekly stats
- `bot/services/memory_service.py` — User memory (persistent facts Zoe learns), response feedback
- `bot/services/task_service.py` — Task CRUD, recurring tasks, reminders
- `bot/services/user_service.py` — User management
- `bot/services/tier_service.py` — Free/Pro tier limits and usage tracking
- `bot/services/calendar_service.py` — Google Calendar via iCal URL
- `bot/db/database.py` — PostgreSQL schema (23 tables), connection pool, indexes

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
- 24 AI tools (tasks, fitness, biohacking, WHOOP, memory)
- Interactive workout sessions (exercise cards, set tracking, rest timers)
- Fitness tracking (workouts, exercises, pattern balance, PR detection)
- Biohacking tracking (peptide protocols, supplements, bloodwork)
- WHOOP integration (OAuth2, recovery/sleep/strain sync, v2 API)
- Voice messages transcription
- Recurring tasks (auto-spawn next on completion)
- Google Calendar read (iCal URL)
- User memory system (Zoe learns facts about users)
- Response feedback (thumbs up/down)
- 8 proactive jobs (briefing, check-in, nudges, insights, reminders, pruning, session cleanup, dose reminders)
- Webhook HMAC signature verification for WHOOP
- Conversation pruning (daily, 7-day retention)
- Stale workout session cleanup (3-hour timeout)
- Health check server for Railway

## What's NOT Working / TODO

- **Payments provider pending approval** — applied via BotFather (Smart Glocal or Unlimint), waiting for approval. Once approved, set `STRIPE_PROVIDER_TOKEN` env var on Railway. Code is ready, no changes needed.
- **Google Calendar OAuth** — code deployed, env vars set (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`). User needs to test `/calendar` flow. Redirect URI: `https://telegram-task-bot-production-6784.up.railway.app/google/callback`. Google Cloud project is in Testing mode — user's email added as test user.
- No test suite
- No referral system yet

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
