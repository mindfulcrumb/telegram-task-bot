"""Main entry point for the Telegram Task Bot."""
import logging
from telegram.ext import Application, CommandHandler, MessageHandler, filters
import config
from bot.handlers.tasks import (
    cmd_start,
    cmd_help,
    cmd_add,
    cmd_list,
    cmd_today,
    cmd_done,
    cmd_delete,
    cmd_edit,
    cmd_week,
    cmd_overdue,
    handle_message
)
from bot.handlers.reminders import cmd_remind, setup_reminder_job

# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def main():
    """Start the bot."""
    # Validate configuration
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set. Please check your .env file.")
        return

    if not config.NOTION_TOKEN:
        logger.error("NOTION_TOKEN not set. Please check your .env file.")
        return

    if not config.NOTION_DATABASE_ID:
        logger.error("NOTION_DATABASE_ID not set. Please check your .env file.")
        return

    logger.info("Starting Task Bot...")

    # Security warning if no user restrictions
    if not config.ALLOWED_USER_IDS:
        logger.warning("=" * 60)
        logger.warning("SECURITY WARNING: ALLOWED_USER_IDS is not set!")
        logger.warning("Anyone can use this bot. To restrict access:")
        logger.warning("1. Message @userinfobot on Telegram to get your user ID")
        logger.warning("2. Add it to .env: ALLOWED_USER_IDS=123456789")
        logger.warning("=" * 60)

    # Create the Application
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Add command handlers
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("add", cmd_add))
    application.add_handler(CommandHandler("list", cmd_list))
    application.add_handler(CommandHandler("today", cmd_today))
    application.add_handler(CommandHandler("done", cmd_done))
    application.add_handler(CommandHandler("delete", cmd_delete))
    application.add_handler(CommandHandler("edit", cmd_edit))
    application.add_handler(CommandHandler("week", cmd_week))
    application.add_handler(CommandHandler("overdue", cmd_overdue))
    application.add_handler(CommandHandler("remind", cmd_remind))

    # Add message handler for plain text (creates tasks)
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_message
    ))

    # Set up reminder job - checks every minute and sends to all active chats
    if config.ALLOWED_USER_IDS:
        setup_reminder_job(application, config.ALLOWED_USER_IDS[0])
        logger.info(f"Reminder job set up for user {config.ALLOWED_USER_IDS[0]}")
    else:
        # Still set up the job - it will use dynamically registered chat IDs
        setup_reminder_job(application)
        logger.info("Reminder job set up (will use dynamic chat registration)")

    logger.info("Bot is ready! Starting polling...")

    # Start the bot
    application.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
