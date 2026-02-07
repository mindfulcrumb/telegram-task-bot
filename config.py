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

# Your Telegram user ID (for security - only you can use the bot)
# Get this by messaging @userinfobot on Telegram
ALLOWED_USER_IDS = [int(id.strip()) for id in os.getenv("ALLOWED_USER_IDS", "").split(",") if id.strip()]

# Reminder check interval in minutes
REMINDER_CHECK_INTERVAL = int(os.getenv("REMINDER_CHECK_INTERVAL", "5"))

# Anthropic API Key for Claude AI (optional - enables smart mode)
ANTHROPIC_API_KEY = clean_env_value(os.getenv("ANTHROPIC_API_KEY"))

# AI Mode: Set to "smart" to use Claude for all input processing
AI_MODE = clean_env_value(os.getenv("AI_MODE") or "basic").lower()

# Email Configuration (SMTP)
# For Gmail: enable 2FA, create App Password at https://myaccount.google.com/apppasswords
# For Agentmail: use smtp.agentmail.to with your API key as password
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

# Contact book for quick references (name -> email/phone)
# Format: "john:john@email.com,mom:+1234567890"
CONTACTS_RAW = clean_env_value(os.getenv("CONTACTS", ""))
CONTACTS = {}
if CONTACTS_RAW:
    for pair in CONTACTS_RAW.split(","):
        if ":" in pair:
            name, value = pair.split(":", 1)
            CONTACTS[name.strip().lower()] = value.strip()
