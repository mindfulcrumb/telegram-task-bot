# Telegram Task Bot - Project Notes

Last updated: Feb 8, 2026

---

## Architecture Overview

A personal Telegram bot that manages tasks, emails, invoices, and accounting via natural language. Powered by Claude as an **agentic AI** with native tool calling.

```
User (Telegram) --> bot/main.py --> handlers/ --> AI Brain (agent loop)
                                                     |
                                          Claude API (tool_use)
                                                     |
                                               bot/ai/tools.py
                                                     |
                              +-----------+----------+----------+
                              |           |          |          |
                           Notion     Agentmail   Twilio    SQLite
                          (tasks,    (email)    (WhatsApp) (accounting,
                         contacts)                         invoices,
                                                           memory)
```

### Key Files

| File | Purpose |
|------|---------|
| `bot/main.py` | Entry point, handler registration, scheduled jobs |
| `bot/ai/brain.py` | Agent loop - calls Claude, executes tools, repeats |
| `bot/ai/tools.py` | 21 tool definitions (schemas) + executor dispatch |
| `bot/ai/memory.py` | SQLite conversation history (persists across deploys) |
| `bot/handlers/tasks.py` | Telegram command handlers + rule-based fallback |
| `bot/handlers/voice.py` | Voice transcription via Groq Whisper |
| `bot/handlers/accounting.py` | Bank reconciliation + invoice scanning handlers |
| `bot/handlers/reminders.py` | Scheduled reminders |
| `bot/handlers/proactive.py` | Daily briefing + smart nudges |
| `bot/handlers/emails.py` | Email notification polling |
| `bot/services/notion.py` | Notion API (tasks CRUD, categories, due dates) |
| `bot/services/email_service.py` | Agentmail send |
| `bot/services/email_inbox.py` | Agentmail inbox read |
| `bot/services/whatsapp_service.py` | Twilio WhatsApp |
| `bot/services/contacts_store.py` | Notion-backed contact book with cache |
| `bot/services/classifier.py` | Rule-based task parsing (dates, categories, priority) |
| `bot/accounting/storage.py` | SQLite for transactions + invoices |
| `bot/accounting/invoice_parser.py` | PDF/photo invoice extraction via Claude Vision |
| `bot/accounting/invoice_export.py` | Excel/CSV invoice export |
| `bot/accounting/pdf_parser.py` | Bank statement PDF parsing |
| `bot/accounting/ai_categorizer.py` | AI-powered transaction categorization |
| `config.py` | All env vars and settings |

---

## Agent System (Current)

### How It Works

1. User sends message via Telegram (text or voice)
2. `handle_message()` routes to `handle_ai_message()` if `AI_MODE=smart`
3. `ai_brain.process()` runs the **agent loop**:
   - Loads conversation history from SQLite
   - Calls Claude with system prompt + tools
   - If Claude returns `tool_use` blocks → executes tools → feeds results back
   - Repeats up to 5 turns until Claude returns a text response
   - Saves conversation to persistent memory
4. Response sent back to Telegram

### Tools Available (21 total)

**Task tools (always loaded):**
- `get_tasks` - list tasks with filters (all/today/business/personal/overdue/week)
- `add_task` - create task with title, category, priority, due date
- `complete_tasks` - mark tasks done by number
- `delete_tasks` - archive tasks by number
- `undo_last_action` - restore last deleted/completed tasks
- `edit_task` - rename a task

**Email tools (always loaded):**
- `send_email` - send email via Agentmail
- `check_inbox` - list recent emails
- `read_email` - read full email content
- `reply_to_email` - reply to an email

**Contact tools (always loaded):**
- `lookup_contact` - find contact by name
- `save_contact` - save name/email/phone

**Accounting tools (loaded when session active):**
- `export_accounting` - export as Excel/CSV/PDF
- `get_accounting_status` - show session info
- `update_transactions` - categorize transactions
- `skip_transaction` - skip current in review

**Invoice tools (loaded when invoice data exists):**
- `get_invoice_status` - show scanned invoice details
- `list_invoices` - list all stored invoices
- `update_invoice` - edit invoice fields
- `delete_invoice` - remove an invoice
- `export_invoices` - export as Excel/CSV

### Model

- Default: `claude-sonnet-4-5-20250929` (for reliable tool use)
- Categorizer: `claude-sonnet-4-5-20250929`
- Max agent turns: 5
- Conversation history: last 20 messages

### What It Can Do Now

- Chain multiple actions: "email will about the meeting and add a follow-up task for friday" → lookup_contact → send_email → add_task
- Remember conversations across Railway redeploys (SQLite memory)
- Multi-step reasoning (up to 5 tool calls per message)
- Voice messages transcribed and processed through the same agent

---

## Feature History (Commits)

| Commit | Feature |
|--------|---------|
| `f20444b` | Agent upgrade: native tool_use, agent loop, persistent memory |
| `3a3803d` | Multi-number delete/done with bulk undo |
| `28bea00` | Undo system for accidental deletions |
| `39f66b8` | Voice: switch to Groq API directly via httpx |
| `f1a17da` | Voice: OpenAI → Groq (no content filtering) |
| `d511d7d` | Fix voice: process text directly (frozen Message) |
| `a20cea4` | Voice message support via Whisper |
| `ede5b8c` | Fix proactive: timezone-aware + nudge dedup |
| `3c3632c` | Daily briefing + smart nudges |
| `615cb18` | Fix silent errors, API timeout, JSON parsing |
| `3b9e43d` | AI-powered transaction updates |
| `eb29e0b` | Persist accounting sessions to SQLite |

---

## Railway Environment Variables

### Required
```
TELEGRAM_BOT_TOKEN=<from @BotFather>
NOTION_TOKEN=<from notion.so/my-integrations>
NOTION_DATABASE_ID=<tasks database>
AI_MODE=smart
ANTHROPIC_API_KEY=<from console.anthropic.com>
ALLOWED_USER_IDS=<your telegram user id>
```

### Email
```
AGENTMAIL_API_KEY=<from agentmail.to dashboard>
AGENTMAIL_INBOX=marlene@agentmail.to
```

### Contacts
```
NOTION_CONTACTS_DB_ID=889548ba-f8e8-4569-97ff-60330f339fbb
```

### Voice
```
GROQ_API_KEY=<from console.groq.com>
```

### WhatsApp
```
TWILIO_ACCOUNT_SID=<from twilio.com>
TWILIO_AUTH_TOKEN=<from twilio.com>
TWILIO_WHATSAPP_FROM=+14155238886
```

### Optional Tuning
```
CLAUDE_MODEL=claude-sonnet-4-5-20250929
AGENT_MAX_TURNS=5
CONVERSATION_HISTORY_LIMIT=20
REMINDER_CHECK_INTERVAL=5
EMAIL_CHECK_INTERVAL=2
BRIEFING_HOUR=8
BRIEFING_MINUTE=0
TIMEZONE=Europe/Lisbon
NUDGE_INTERVAL_HOURS=6
```

---

## What's Working

- Task CRUD via natural language and /commands
- Multi-step agent tool chaining
- Persistent conversation memory (SQLite)
- Email send/read/reply (Agentmail)
- Contact lookup + auto-save on new emails
- Voice messages (Groq Whisper)
- Undo (delete + done, single and bulk)
- Daily briefing at configured hour
- Smart nudges for overdue/stale tasks
- Bank statement reconciliation (PDF upload)
- Invoice scanning (PDF/photo upload via Claude Vision)
- Invoice storage, editing, export (Excel/CSV)
- AI transaction categorization

---

## Next Steps - Making It Fully Autonomous

The bot is currently an **agentic chatbot** (reacts to messages with multi-step tool chaining). To make it a **fully autonomous agent**, consider:

### 1. Event-Driven Triggers
- **Incoming email → auto-action**: New email arrives → agent decides to create a task, draft a reply, or notify you with a summary
- **Task deadline approaching → proactive outreach**: Agent emails/WhatsApps relevant people without being asked
- Currently the daily briefing and nudges are cron-based and template-driven; upgrade them to run through the agent loop so Claude decides what to say and which tools to use

### 2. Background Goal Loops
- Agent periodically reviews all tasks and takes initiative:
  - Re-prioritize based on deadlines
  - Follow up on overdue items ("Hey, task X has been overdue for 3 days - want me to reschedule or email someone about it?")
  - Suggest task breakdowns for large items
- Needs a `goals` table in SQLite where you can set high-level objectives the agent works toward

### 3. Planning + Multi-Session Execution
- "Prepare for Monday's meeting" → agent breaks into subtasks, schedules them across days, follows up
- Requires a planning tool that creates sub-tasks and a scheduler that triggers the agent to check progress

### 4. Learning + Preferences
- Track which categories you assign most, which contacts you email often, what times you're most active
- Agent adapts its suggestions and tool choices based on patterns
- Could use a `preferences` SQLite table the agent reads/writes

### 5. External Integrations
- Calendar sync (Google Calendar) → agent knows about meetings and blocks time
- Slack/Teams → agent can post updates to channels
- Bank feed → auto-import transactions instead of PDF upload
