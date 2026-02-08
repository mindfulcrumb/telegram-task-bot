# Telegram Task Bot

Personal Telegram bot deployed on Railway. Manages tasks, emails, invoices, and accounting via natural language, powered by Claude as an agentic AI with native tool calling.

## Tech Stack

- Python 3.9+, python-telegram-bot v21 (async)
- Claude API (anthropic SDK) with native tool_use for agent loop
- Notion API for tasks and contacts storage
- Agentmail for email send/read/reply
- Twilio for WhatsApp
- Groq Whisper for voice transcription
- SQLite for conversation memory, accounting, and invoices
- Deployed on Railway (auto-deploys from main branch)

## Architecture

```
User (Telegram) → bot/main.py → handlers/ → bot/ai/brain.py (agent loop)
                                                    ↓
                                         Claude API (tool_use)
                                                    ↓
                                            bot/ai/tools.py (21 tools)
                                                    ↓
                              Notion | Agentmail | Twilio | SQLite
```

### Key Files

- `bot/ai/brain.py` — Agent loop: call Claude → execute tools → repeat until text response
- `bot/ai/tools.py` — 21 tool definitions (JSON schemas) + executor dispatch
- `bot/ai/memory.py` — SQLite persistent conversation history
- `bot/handlers/tasks.py` — Telegram /command handlers + rule-based fallback
- `bot/handlers/accounting.py` — Bank reconciliation + invoice scanning
- `bot/services/` — Service layer (notion.py, email_service.py, whatsapp_service.py, contacts_store.py)
- `bot/accounting/` — Invoice parsing, storage, categorization, export
- `config.py` — All env vars, clean_env_value() strips Railway quote corruption

## Code Conventions

- Async everywhere (python-telegram-bot v21 requires it)
- Lazy imports inside functions to avoid circular dependencies (common pattern here)
- Tool executor functions prefixed with `_exec_` in tools.py
- Shared state (`_undo_buffer`, `_pending_emails`) lives in tools.py, imported where needed
- All Notion/service calls are synchronous (only the handler layer is async)
- Error messages to users should be casual/friendly, not technical
- The bot personality is "chill friend", not corporate — keep that vibe in any text the bot sends

## Testing

No test suite exists yet. To verify changes:
- `python3 -c "import ast; ast.parse(open('FILE').read())"` for syntax
- `python3 -c "from module import thing"` for import checks
- telegram package is not installed locally (runs on Railway), so mock it for import tests

## Common Pitfalls

- python-telegram-bot v21 Message objects are FROZEN (immutable) — never set .text on them
- Railway may add quotes around env vars — config.py's clean_env_value() handles this
- Agentmail's `to` param is a string, not a list
- SQLite connections need `check_same_thread=False`
- The agent loop maxes at 5 turns (AGENT_MAX_TURNS) to prevent runaway calls
- Tool schemas must have `"type": "object"` and `"properties"` keys or Claude API rejects them
