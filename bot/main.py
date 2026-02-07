"""Main entry point for the Telegram Task Bot."""
# CRITICAL: Import encoding fix FIRST before ANY other imports
# This must be the very first import to fix Railway/Docker ASCII encoding issues
from bot import encoding_fix
encoding_fix.disable_httpx_logging()
encoding_fix.configure_safe_logging()

# Now import everything else
import sys
import os
import logging
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
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
    cmd_analyze,
    handle_message
)
from bot.handlers.reminders import cmd_remind, setup_reminder_job
from bot.handlers.proactive import setup_proactive_jobs
from bot.handlers.voice import handle_voice
from bot.handlers.accounting import (
    cmd_reconcile,
    cmd_acct_categories,
    cmd_acct_export,
    cmd_acct_skip,
    handle_pdf_upload,
    handle_acct_callback,
)
from bot.accounting import storage as acct_db

# Suppress httpx debug logging (can cause encoding issues)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Set up logging with UTF-8 handler
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
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

    logger.info("Starting Task Bot + Accounting Assistant...")

    # Initialize accounting database
    acct_db.initialize()

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
    application.add_handler(CommandHandler("analyze", cmd_analyze))

    # Accounting handlers
    application.add_handler(CommandHandler("reconcile", cmd_reconcile))
    application.add_handler(CommandHandler("acct_categories", cmd_acct_categories))
    application.add_handler(CommandHandler("acct_export", cmd_acct_export))
    application.add_handler(CommandHandler("acct_skip", cmd_acct_skip))

    # PDF document handler (for accounting reconciliation)
    application.add_handler(MessageHandler(filters.Document.PDF, handle_pdf_upload))

    # Inline keyboard callback handler (for accounting category selection)
    application.add_handler(CallbackQueryHandler(handle_acct_callback))

    # Voice message handler (transcribe with Whisper, then process as text)
    application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))

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

    # Set up email inbox check job - polls for new emails
    if getattr(config, 'AGENTMAIL_API_KEY', '') and getattr(config, 'AGENTMAIL_INBOX', ''):
        from bot.handlers.emails import setup_email_check_job
        primary_chat = config.ALLOWED_USER_IDS[0] if config.ALLOWED_USER_IDS else None
        setup_email_check_job(application, primary_chat)
        logger.info("Email check job set up")

    # Set up proactive jobs (daily briefing + smart nudges)
    setup_proactive_jobs(application)

    # Send startup notification to register chat ID and confirm deploy
    if config.ALLOWED_USER_IDS:
        async def startup_notify(context: ContextTypes.DEFAULT_TYPE):
            """Send startup message to register chat ID for notifications."""
            from bot.handlers.reminders import register_chat_id
            for uid in config.ALLOWED_USER_IDS:
                try:
                    await context.bot.send_message(
                        chat_id=uid,
                        text="I'm back online! All systems ready."
                    )
                    register_chat_id(uid)
                    logger.info(f"Startup notification sent to {uid}, chat ID registered")
                except Exception as e:
                    logger.error(f"Failed to send startup notification to {uid}: {e}")

        application.job_queue.run_once(startup_notify, when=5)
        logger.info("Startup notification scheduled (5s after boot)")

    logger.info("Bot is ready! Starting polling...")

    # Start the bot
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
