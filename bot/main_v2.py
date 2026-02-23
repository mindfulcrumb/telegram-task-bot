"""Main entry point — webhook or polling mode, multi-user, PostgreSQL."""
import sys
import os
import logging
import secrets
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    PreCheckoutQueryHandler, ContextTypes, filters,
)

logger = logging.getLogger(__name__)

# Track if DB is available
_db_ready = False


class _HealthCheck(BaseHTTPRequestHandler):
    """Minimal health check so Railway doesn't kill polling mode."""
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass


async def _fallback_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback /start when DB is not available."""
    await update.message.reply_text(
        "Bot is alive! But the database isn't connected yet.\n\n"
        "Admin: check Railway env vars — DATABASE_URL must be set."
    )


async def _fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback for any message when DB is not available."""
    await update.message.reply_text(
        "I'm running but can't process messages yet — database not connected.\n"
        "Admin: set DATABASE_URL in Railway."
    )


def main():
    """Start the bot."""
    global _db_ready

    # Logging
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Log environment for debugging
    logger.info("=== STARTUP DIAGNOSTICS ===")
    logger.info(f"TELEGRAM_BOT_TOKEN: {'SET' if os.environ.get('TELEGRAM_BOT_TOKEN') else 'MISSING'}")
    logger.info(f"DATABASE_URL: {'SET' if os.environ.get('DATABASE_URL') else 'MISSING'}")
    logger.info(f"ANTHROPIC_API_KEY: {'SET' if os.environ.get('ANTHROPIC_API_KEY') else 'MISSING'}")
    logger.info(f"RAILWAY_PUBLIC_DOMAIN: {os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'NOT SET')}")
    logger.info(f"PORT: {os.environ.get('PORT', 'NOT SET')}")
    logger.info(f"ADMIN_USER_IDS: {os.environ.get('ADMIN_USER_IDS', 'NOT SET')}")
    logger.info("===========================")

    # Validate bot token (hard requirement)
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        logger.error("TELEGRAM_BOT_TOKEN not set — cannot start")
        return

    # Build app
    application = Application.builder().token(bot_token).build()

    # Try to initialize PostgreSQL
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        try:
            from bot.db.database import initialize as init_db
            init_db()
            _db_ready = True
            logger.info("PostgreSQL initialized successfully")
        except Exception as e:
            logger.error(f"PostgreSQL init failed: {type(e).__name__}: {e}")
            _db_ready = False
    else:
        logger.warning("DATABASE_URL not set — running in degraded mode (no DB)")
        _db_ready = False

    if _db_ready:
        # Full handler registration
        _register_full_handlers(application)
    else:
        # Degraded mode — just respond that DB is missing
        application.add_handler(CommandHandler("start", _fallback_start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _fallback_message))
        logger.warning("Running in DEGRADED MODE — only basic responses available")

    # --- Start ---

    railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    port = int(os.environ.get("PORT", "8443"))

    if railway_domain:
        # Webhook mode
        webhook_secret = os.environ.get("WEBHOOK_SECRET") or secrets.token_urlsafe(32)
        webhook_path = f"/webhook/{secrets.token_urlsafe(16)}"
        webhook_url = f"https://{railway_domain}{webhook_path}"
        logger.info(f"Starting WEBHOOK mode on port {port}")
        logger.info(f"Webhook URL: {webhook_url}")

        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=webhook_path,
            webhook_url=webhook_url,
            secret_token=webhook_secret,
            allowed_updates=["message", "callback_query", "pre_checkout_query"],
        )
    else:
        # Polling mode
        if os.environ.get("PORT"):
            health = HTTPServer(("0.0.0.0", port), _HealthCheck)
            threading.Thread(target=health.serve_forever, daemon=True).start()
            logger.info(f"Health check server on port {port}")

        logger.info("Starting POLLING mode")
        application.run_polling(
            allowed_updates=["message", "callback_query", "pre_checkout_query"],
            drop_pending_updates=True,
        )


def _register_full_handlers(application):
    """Register all handlers (requires DB)."""
    # Onboarding
    from bot.handlers.onboarding import (
        cmd_start, cmd_help, cmd_settings, cmd_account, cmd_delete_account,
    )
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("settings", cmd_settings))
    application.add_handler(CommandHandler("account", cmd_account))
    application.add_handler(CommandHandler("deleteaccount", cmd_delete_account))

    # Tasks
    from bot.handlers.tasks_v2 import (
        cmd_add, cmd_list, cmd_today, cmd_week, cmd_overdue,
        cmd_done, cmd_delete, cmd_edit, cmd_undo, cmd_clear,
        cmd_analyze, handle_message,
    )
    application.add_handler(CommandHandler("add", cmd_add))
    application.add_handler(CommandHandler("list", cmd_list))
    application.add_handler(CommandHandler("today", cmd_today))
    application.add_handler(CommandHandler("week", cmd_week))
    application.add_handler(CommandHandler("overdue", cmd_overdue))
    application.add_handler(CommandHandler("done", cmd_done))
    application.add_handler(CommandHandler("delete", cmd_delete))
    application.add_handler(CommandHandler("edit", cmd_edit))
    application.add_handler(CommandHandler("undo", cmd_undo))
    application.add_handler(CommandHandler("clear", cmd_clear))
    application.add_handler(CommandHandler("analyze", cmd_analyze))

    # Payments
    from bot.handlers.payments import (
        cmd_upgrade, handle_pre_checkout, handle_successful_payment,
        cmd_terms, cmd_support,
    )
    application.add_handler(CommandHandler("upgrade", cmd_upgrade))
    application.add_handler(CommandHandler("terms", cmd_terms))
    application.add_handler(CommandHandler("support", cmd_support))
    application.add_handler(PreCheckoutQueryHandler(handle_pre_checkout))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, handle_successful_payment))

    # Admin
    from bot.handlers.admin import cmd_migrate_notion, cmd_diagnostics
    application.add_handler(CommandHandler("migrate", cmd_migrate_notion))
    application.add_handler(CommandHandler("diagnostics", cmd_diagnostics))

    # Free text → AI brain (must be last)
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_message
    ))


if __name__ == "__main__":
    main()
