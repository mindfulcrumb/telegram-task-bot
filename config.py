"""Configuration management for the Telegram Task Bot."""
import os
from dotenv import load_dotenv

load_dotenv()


def clean_env_value(value):
    """Clean environment variable value - strip whitespace AND quotes.

    Railway's UI sometimes automatically adds quotes around values.
    This function removes them so tokens work correctly.
    """
    if not value:
        return ""
    # Strip whitespace first
    value = value.strip()
    # Strip surrounding quotes (single or double)
    if len(value) >= 2:
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        # Also handle case where only leading quote exists (partial corruption)
        elif value.startswith('"') or value.startswith("'"):
            value = value[1:]
        elif value.endswith('"') or value.endswith("'"):
            value = value[:-1]
    return value.strip()


# Telegram Bot Token (get from @BotFather)
# clean_env_value removes whitespace AND quotes that Railway might add
TELEGRAM_BOT_TOKEN = clean_env_value(os.getenv("TELEGRAM_BOT_TOKEN"))

# Notion Integration Token (get from notion.so/my-integrations)
NOTION_TOKEN = clean_env_value(os.getenv("NOTION_TOKEN"))

# Notion Database ID (the ID from your tasks database URL)
NOTION_DATABASE_ID = clean_env_value(os.getenv("NOTION_DATABASE_ID"))

# Notion Contacts Database ID (for persistent contact storage)
NOTION_CONTACTS_DB_ID = clean_env_value(os.getenv("NOTION_CONTACTS_DB_ID"))

# Your Telegram user ID (for security - only you can use the bot)
# Get this by messaging @userinfobot on Telegram
ALLOWED_USER_IDS = [int(id.strip()) for id in os.getenv("ALLOWED_USER_IDS", "").split(",") if id.strip()]

# Reminder check interval in minutes
REMINDER_CHECK_INTERVAL = int(os.getenv("REMINDER_CHECK_INTERVAL", "5"))

# Email inbox check interval in minutes (for new email notifications)
EMAIL_CHECK_INTERVAL = int(os.getenv("EMAIL_CHECK_INTERVAL", "2"))

# Anthropic API Key for Claude AI (optional - enables smart mode)
ANTHROPIC_API_KEY = clean_env_value(os.getenv("ANTHROPIC_API_KEY"))

# Groq API Key (for Whisper voice transcription - no content filtering)
# Get one free at: https://console.groq.com/keys
GROQ_API_KEY = clean_env_value(os.getenv("GROQ_API_KEY"))

# AI Mode: Set to "smart" to use Claude for all input processing
AI_MODE = clean_env_value(os.getenv("AI_MODE") or "basic").lower()

# Claude model for conversational AI (default: sonnet for reliable tool use)
CLAUDE_MODEL = clean_env_value(os.getenv("CLAUDE_MODEL")) or "claude-sonnet-4-5-20250929"

# Agent settings
AGENT_MAX_TURNS = int(os.getenv("AGENT_MAX_TURNS", "5"))
CONVERSATION_HISTORY_LIMIT = int(os.getenv("CONVERSATION_HISTORY_LIMIT", "20"))

# GitHub Integration (for creating issues on your repos via Telegram)
# Personal access token with 'repo' scope: https://github.com/settings/tokens
GITHUB_TOKEN = clean_env_value(os.getenv("GITHUB_TOKEN"))
GITHUB_OWNER = clean_env_value(os.getenv("GITHUB_OWNER")) or "mindfulcrumb"

# Claude model for AI categorization (default: sonnet for accuracy)
CLAUDE_CATEGORIZER_MODEL = clean_env_value(os.getenv("CLAUDE_CATEGORIZER_MODEL")) or "claude-sonnet-4-5-20250929"

# Email Configuration
# Option 1: Agentmail (recommended for AI agents)
# Get API key from agentmail.to dashboard
AGENTMAIL_API_KEY = clean_env_value(os.getenv("AGENTMAIL_API_KEY"))
AGENTMAIL_INBOX = clean_env_value(os.getenv("AGENTMAIL_INBOX"))  # e.g., marlene@agentmail.to

# Option 2: SMTP (Gmail, etc.)
# For Gmail: enable 2FA, create App Password at https://myaccount.google.com/apppasswords
SMTP_EMAIL = clean_env_value(os.getenv("SMTP_EMAIL"))
SMTP_PASSWORD = clean_env_value(os.getenv("SMTP_PASSWORD"))
SMTP_HOST = clean_env_value(os.getenv("SMTP_HOST")) or "smtp.gmail.com"
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))

# WhatsApp via Twilio
# Get credentials at: https://console.twilio.com
# For sandbox, TWILIO_WHATSAPP_FROM is like: +14155238886
TWILIO_ACCOUNT_SID = clean_env_value(os.getenv("TWILIO_ACCOUNT_SID"))
TWILIO_AUTH_TOKEN = clean_env_value(os.getenv("TWILIO_AUTH_TOKEN"))
TWILIO_WHATSAPP_FROM = clean_env_value(os.getenv("TWILIO_WHATSAPP_FROM"))

# Proactive Features
# Daily briefing: sends task summary at this hour (in your timezone)
BRIEFING_HOUR = int(os.getenv("BRIEFING_HOUR", "8"))
BRIEFING_MINUTE = int(os.getenv("BRIEFING_MINUTE", "0"))

# Timezone for scheduled jobs (e.g., "Europe/Lisbon", "America/New_York")
# See: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones
TIMEZONE = clean_env_value(os.getenv("TIMEZONE")) or ""

# Smart nudges: how often to check for overdue/stale tasks (in hours)
NUDGE_INTERVAL_HOURS = int(os.getenv("NUDGE_INTERVAL_HOURS", "6"))

# Contact book for quick references (name -> email/phone)
# Format: "john:john@email.com,mom:+1234567890"
CONTACTS_RAW = clean_env_value(os.getenv("CONTACTS", ""))
CONTACTS = {}
if CONTACTS_RAW:
    for pair in CONTACTS_RAW.split(","):
        if ":" in pair:
            name, value = pair.split(":", 1)
            CONTACTS[name.strip().lower()] = value.strip()
