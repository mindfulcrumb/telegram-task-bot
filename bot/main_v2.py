"""Main entry point — webhook or polling mode, multi-user, PostgreSQL."""
import sys
import os
import logging
import secrets
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    PreCheckoutQueryHandler, ContextTypes, filters,
)

logger = logging.getLogger(__name__)


class _HealthCheck(BaseHTTPRequestHandler):
    """Minimal health check so Railway doesn't kill polling mode."""
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass


def main():
    """Start the bot."""
    # Logging
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Validate config
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return

    if not os.environ.get("DATABASE_URL"):
        logger.error("DATABASE_URL not set")
        return

    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.warning("ANTHROPIC_API_KEY not set — AI features disabled")

    # Initialize PostgreSQL
    try:
        from bot.db.database import initialize as init_db
        init_db()
        logger.info("PostgreSQL initialized")
    except Exception as e:
        logger.error(f"PostgreSQL init failed: {e}")
        return

    # Build app
    application = Application.builder().token(bot_token).build()

    # --- Handlers ---

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

    # Free text → AI brain (must be last)
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_message
    ))

    # --- Start ---

    railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    port = int(os.environ.get("PORT", "8443"))

    if railway_domain:
        # Webhook mode (production on Railway with public domain)
        webhook_secret = os.environ.get("WEBHOOK_SECRET") or secrets.token_urlsafe(32)
        webhook_path = f"/webhook/{secrets.token_urlsafe(16)}"
        webhook_url = f"https://{railway_domain}{webhook_path}"
        logger.info(f"Starting webhook mode on port {port}")
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
        # Polling mode — start health check server if PORT is set
        # so Railway doesn't kill us for not binding to a port
        if os.environ.get("PORT"):
            health = HTTPServer(("0.0.0.0", port), _HealthCheck)
            threading.Thread(target=health.serve_forever, daemon=True).start()
            logger.info(f"Health check server on port {port}")

        logger.info("Starting in polling mode")
        application.run_polling(
            allowed_updates=["message", "callback_query", "pre_checkout_query"],
            drop_pending_updates=True,
        )


if __name__ == "__main__":
    main()
