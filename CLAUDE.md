# Zoe — Telegram AI Companion Bot (@Meet_Zoe_Bot)

Multi-user SaaS Telegram bot. AI-powered task management, proactive coaching, voice support, Google Calendar integration. Deployed on Railway with PostgreSQL.

## Tech Stack

- Python 3.11, python-telegram-bot v21 (async)
- Claude API (anthropic SDK) with native tool_use for agent loop
- PostgreSQL on Railway (psycopg2-binary) — all data
- Groq Whisper for voice transcription
- iCal parsing for Google Calendar (icalendar library)
- Deployed on Railway (auto-deploys from GitHub mindfulcrumb/telegram-task-bot)

## Architecture

```
User (Telegram) → bot/main_v2.py → handlers/ → bot/ai/brain_v2.py (agent loop)
                                                       ↓
                                            Claude API (tool_use)
                                                       ↓
                                               bot/ai/tools_v2.py (9 tools)
                                                       ↓
                                        PostgreSQL (tasks, users, streaks, etc.)
```

### Key Files

- `bot/main_v2.py` — Entry point, handler registration, bot menu commands, health check
- `bot/ai/brain_v2.py` — Zoe AI personality + agent loop (Claude tool_use)
- `bot/ai/tools_v2.py` — 9 tools: get_tasks, add_task, complete_tasks, delete_tasks, undo, edit_task, update_task, set_reminder
- `bot/ai/memory_pg.py` — PostgreSQL conversation history
- `bot/handlers/onboarding.py` — /start, /help, /settings, /account, /calendar, /deleteaccount
- `bot/handlers/tasks_v2.py` — /add, /list, /today, /week, /done, /delete, /edit, /streak, etc.
- `bot/handlers/payments.py` — Telegram Payments + Stripe (/upgrade, /terms, /support)
- `bot/handlers/proactive_v2.py` — Morning briefings, evening check-ins, smart nudges, weekly insights, reminder firing
- `bot/handlers/voice_v2.py` — Voice messages via Groq Whisper → AI brain
- `bot/handlers/admin.py` — /migrate (Notion import), /diagnostics
- `bot/services/task_service.py` — Task CRUD, recurring tasks, reminders
- `bot/services/user_service.py` — User management
- `bot/services/coaching_service.py` — Streaks, nudge dedup, check-ins, weekly stats
- `bot/services/calendar_service.py` — Google Calendar via iCal URL
- `bot/services/tier_service.py` — Free/Pro tier limits and usage tracking
- `bot/db/database.py` — PostgreSQL schema, connection pool

## Current Features

### Free Tier
- 25 active tasks, 20 AI messages/day, 3 reminders
- Natural language task management (add, complete, edit, delete)
- Due dates, priorities, categories (auto-inferred by AI)
- Recurring tasks (daily, weekdays, weekly, monthly)
- Update tasks via chat ("move dentist to Friday", "make it high priority")
- Voice messages → transcription → AI processing
- Google Calendar integration (iCal URL)
- Completion streaks
- Undo support

### Pro Tier (Zoe Pro)
- Unlimited everything
- Personalized morning briefings (AI-generated, timezone-aware)
- Evening accountability check-ins
- Smart nudges (overdue 3+ days, high-priority no due date, max 3/day)
- Weekly performance insights (Sunday)
- Unlimited reminders

## Environment Variables (Railway)

- `TELEGRAM_BOT_TOKEN` — Bot token from BotFather (REQUIRED)
- `DATABASE_URL` — PostgreSQL connection string (REQUIRED)
- `ANTHROPIC_API_KEY` — Claude API key (REQUIRED for AI)
- `ADMIN_USER_IDS` — Comma-separated Telegram user IDs for admin commands
- `GROQ_API_KEY` — Groq API key for voice transcription
- `STRIPE_PROVIDER_TOKEN` — From BotFather Payments → Stripe (NOT SET YET)
- `RAILWAY_PUBLIC_DOMAIN` — Set to enable webhook mode (optional, polling works fine)
- `PORT` — Railway sets this automatically

## Code Conventions

- Async everywhere (python-telegram-bot v21 requires it)
- Lazy imports inside functions to avoid circular dependencies
- All service layer (task_service, coaching_service, etc.) is synchronous using `get_cursor()` context manager
- Error messages to users should be warm/thoughtful (Zoe's personality)
- Zoe personality: thoughtful, warm, calm, human — not bubbly, not robotic, not corporate

## What's Working

- Full task management via chat and commands
- Zoe AI personality with coaching context (streaks, patterns)
- Voice messages transcription
- Recurring tasks (auto-spawn next on completion)
- Update task tool (change due date, priority, category via AI)
- Reminders (set via AI + firing job every 60s)
- Google Calendar read (iCal URL, events in AI prompt + briefings)
- Interactive onboarding with inline buttons
- Proactive coaching jobs (briefing, check-in, nudges, insights)
- Completion streaks
- Free/Pro tier gating
- Bot menu commands (setMyCommands on startup)
- Health check server for Railway polling mode
- Degraded mode when DB unavailable

## What's NOT Working / TODO

- **STRIPE_PROVIDER_TOKEN not set** — payments show "coming soon"
- **Bot token needs to be swapped** to @Meet_Zoe_Bot (waiting for user to provide token)
- **ADMIN_USER_IDS** may not be set in Railway (user's Telegram ID: 1631254047)
- No test suite
- No auto-detect timezone on first use
- No referral system yet

## Session Log — Feb 24, 2026

### What was done:
1. Rebranded entire bot to "Zoe" (AI personality, all user-facing text, proactive messages)
2. Added `update_task` tool — AI can now change due dates, priority, category
3. Added recurring tasks — daily/weekdays/weekly/monthly with auto-spawn on completion
4. Added Google Calendar integration via iCal URL
5. Built better onboarding with interactive inline buttons
6. Added reminders — AI tool + firing job
7. Added bot menu commands (setMyCommands)

### Previous session:
- Fixed deployment failures (old imports, health check, degraded mode)
- Built coaching service (streaks, nudges, check-ins, insights)
- Built proactive coaching jobs
- Built voice handler
- Built payments system
- Built admin commands (/migrate, /diagnostics)
