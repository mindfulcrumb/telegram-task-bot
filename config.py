"""Configuration management for the Telegram Task Bot."""
import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Token (get from @BotFather)
# .strip() removes any accidental whitespace/newlines from environment variables
TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()

# Notion Integration Token (get from notion.so/my-integrations)
NOTION_TOKEN = (os.getenv("NOTION_TOKEN") or "").strip()

# Notion Database ID (the ID from your tasks database URL)
NOTION_DATABASE_ID = (os.getenv("NOTION_DATABASE_ID") or "").strip()

# Your Telegram user ID (for security - only you can use the bot)
# Get this by messaging @userinfobot on Telegram
ALLOWED_USER_IDS = [int(id.strip()) for id in os.getenv("ALLOWED_USER_IDS", "").split(",") if id.strip()]

# Reminder check interval in minutes
REMINDER_CHECK_INTERVAL = int(os.getenv("REMINDER_CHECK_INTERVAL", "5"))
