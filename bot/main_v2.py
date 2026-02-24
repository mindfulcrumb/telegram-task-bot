"""Main entry point — webhook or polling mode, multi-user, PostgreSQL."""
import sys
import os
import logging
import secrets
import threading
import json
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    PreCheckoutQueryHandler, ContextTypes, filters,
)

logger = logging.getLogger(__name__)


def _notify_admin(msg: str):
    """Send a Telegram message to admin using raw urllib (no deps needed).
    Used for startup telemetry so we can debug Railway remotely."""
    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        admin_ids = os.environ.get("ADMIN_USER_IDS", "1631254047")  # Fallback to owner
        if not token:
            return
        for uid in admin_ids.split(","):
            uid = uid.strip()
            if not uid:
                continue
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = json.dumps({"chat_id": int(uid), "text": msg, "parse_mode": "HTML"}).encode()
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass  # Telemetry must never crash the bot

# Track if DB is available
_db_ready = False


_startup_status = "starting"


class _HealthCheck(BaseHTTPRequestHandler):
    """Minimal health check so Railway doesn't kill polling mode."""
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(f"OK - {_startup_status}".encode())

    def log_message(self, *args):
        pass


async def _post_init(application):
    """Set bot commands so users see the menu in Telegram."""
    try:
        commands = [
            BotCommand("add", "Add a new task"),
            BotCommand("list", "Show all tasks"),
            BotCommand("today", "Today's tasks"),
            BotCommand("week", "This week's tasks"),
            BotCommand("overdue", "Overdue tasks"),
            BotCommand("done", "Complete a task"),
            BotCommand("edit", "Edit a task"),
            BotCommand("streak", "Your completion streak"),
            BotCommand("analyze", "AI analysis of your tasks"),
            BotCommand("calendar", "Connect Google Calendar"),
            BotCommand("settings", "Your preferences"),
            BotCommand("upgrade", "Unlock Zoe Pro"),
            BotCommand("account", "Subscription info"),
            BotCommand("help", "Show all commands"),
            BotCommand("support", "Get help"),
        ]
        await application.bot.set_my_commands(commands)
        logger.info(f"Bot menu commands set ({len(commands)} commands)")
    except Exception as e:
        logger.error(f"Failed to set bot commands: {type(e).__name__}: {e}")


async def _fallback_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback /start when DB is not available."""
    await update.message.reply_text(
        "Hey, I'm Zoe! I'm waking up but my memory isn't connected yet.\n\n"
        "Admin: check Railway env vars — DATABASE_URL must be set."
    )


async def _fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback for any message when DB is not available."""
    await update.message.reply_text(
        "I'm here but can't process messages yet — still connecting.\n"
        "Admin: set DATABASE_URL in Railway."
    )


def main():
    """Start the bot."""
    global _db_ready

    # Print immediately — bypasses logging in case logging setup crashes
    print("[ZOE] Starting bot...", flush=True)

    # Logging
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Env snapshot for diagnostics
    has_token = bool(os.environ.get("TELEGRAM_BOT_TOKEN"))
    has_db = bool(os.environ.get("DATABASE_URL"))
    has_ai = bool(os.environ.get("ANTHROPIC_API_KEY"))
    railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    port_str = os.environ.get("PORT", "8443")
    has_admin = bool(os.environ.get("ADMIN_USER_IDS"))

    # Log environment for debugging
    logger.info("=== STARTUP DIAGNOSTICS ===")
    logger.info(f"Python: {sys.version}")
    logger.info(f"TELEGRAM_BOT_TOKEN: {'SET' if has_token else 'MISSING'}")
    logger.info(f"DATABASE_URL: {'SET' if has_db else 'MISSING'}")
    logger.info(f"ANTHROPIC_API_KEY: {'SET' if has_ai else 'MISSING'}")
    logger.info(f"RAILWAY_PUBLIC_DOMAIN: {railway_domain or 'NOT SET'}")
    logger.info(f"PORT: {port_str}")
    logger.info(f"ADMIN_USER_IDS: {os.environ.get('ADMIN_USER_IDS', 'NOT SET')}")
    logger.info("===========================")

    # TELEMETRY: Stage 1 — process started
    _notify_admin(
        f"🟡 <b>Zoe Boot Stage 1</b>\n"
        f"Process started\n"
        f"Python: {sys.version.split()[0]}\n"
        f"Token: {'YES' if has_token else 'NO'}\n"
        f"DB: {'YES' if has_db else 'NO'}\n"
        f"AI: {'YES' if has_ai else 'NO'}\n"
        f"Domain: {railway_domain or 'none (polling mode)'}\n"
        f"Port: {port_str}\n"
        f"Admin IDs: {'YES' if has_admin else 'NO'}"
    )

    global _startup_status

    port = int(os.environ.get("PORT", "8443"))
    railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")

    # Start health check FIRST (polling mode only — webhook uses its own server)
    if not railway_domain:
        try:
            health = HTTPServer(("0.0.0.0", port), _HealthCheck)
            threading.Thread(target=health.serve_forever, daemon=True).start()
            logger.info(f"Health check server on port {port}")
        except Exception as e:
            logger.error(f"Health check failed to start: {e}")

    # Validate bot token (hard requirement)
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        logger.error("TELEGRAM_BOT_TOKEN not set — cannot start")
        _startup_status = "error: no token"
        return

    # Build app
    logger.info("Building application...")
    try:
        application = Application.builder().token(bot_token).post_init(_post_init).build()
        logger.info("Application built")
        _notify_admin("🟡 <b>Stage 2</b>: Application built OK")
    except Exception as e:
        _notify_admin(f"🔴 <b>Stage 2 FAILED</b>: Application.build() crashed\n<code>{type(e).__name__}: {e}</code>")
        raise

    # Try to initialize PostgreSQL
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        try:
            from bot.db.database import initialize as init_db
            init_db()
            _db_ready = True
            logger.info("PostgreSQL initialized successfully")
            _notify_admin("🟡 <b>Stage 3</b>: PostgreSQL initialized OK")
        except Exception as e:
            logger.error(f"PostgreSQL init failed: {type(e).__name__}: {e}")
            _notify_admin(f"🟠 <b>Stage 3</b>: PostgreSQL FAILED — degraded mode\n<code>{type(e).__name__}: {e}</code>")
            _db_ready = False
    else:
        logger.warning("DATABASE_URL not set — running in degraded mode (no DB)")
        _notify_admin("🟠 <b>Stage 3</b>: No DATABASE_URL — degraded mode")
        _db_ready = False

    if _db_ready:
        # Full handler registration
        try:
            _register_full_handlers(application)
            logger.info("All handlers registered successfully")
            _notify_admin("🟡 <b>Stage 4</b>: All handlers registered OK")
        except Exception as e:
            logger.error(f"Handler registration failed: {type(e).__name__}: {e}")
            _notify_admin(f"🟠 <b>Stage 4</b>: Handler registration FAILED\n<code>{type(e).__name__}: {e}</code>")
    else:
        # Degraded mode — just respond that DB is missing
        application.add_handler(CommandHandler("start", _fallback_start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _fallback_message))
        logger.warning("Running in DEGRADED MODE — only basic responses available")
        _notify_admin("🟠 <b>Stage 4</b>: Degraded mode — fallback handlers only")

    # --- Start ---

    try:
        if railway_domain:
            # Webhook mode
            webhook_secret = os.environ.get("WEBHOOK_SECRET") or secrets.token_urlsafe(32)
            webhook_path = f"/webhook/{secrets.token_urlsafe(16)}"
            webhook_url = f"https://{railway_domain}{webhook_path}"
            logger.info(f"Starting WEBHOOK mode on port {port}")
            logger.info(f"Webhook URL: {webhook_url}")
            _startup_status = "running (webhook)"
            _notify_admin(f"🟢 <b>Stage 5</b>: Starting WEBHOOK mode\nURL: {webhook_url}")

            application.run_webhook(
                listen="0.0.0.0",
                port=port,
                url_path=webhook_path,
                webhook_url=webhook_url,
                secret_token=webhook_secret,
                allowed_updates=["message", "callback_query", "pre_checkout_query"],
            )
        else:
            # Polling mode — health check already running on PORT
            logger.info("Starting POLLING mode")
            _startup_status = "running (polling)"
            _notify_admin("🟢 <b>Stage 5</b>: Starting POLLING mode — bot should be live!")

            application.run_polling(
                allowed_updates=["message", "callback_query", "pre_checkout_query"],
                drop_pending_updates=True,
            )
    except Exception as e:
        import traceback
        _startup_status = f"CRASHED: {type(e).__name__}: {e}"
        logger.error(f"FATAL: Bot failed to start: {type(e).__name__}: {e}")
        traceback.print_exc()
        _notify_admin(f"🔴 <b>Stage 5 CRASHED</b>\n<code>{type(e).__name__}: {e}</code>")
        # Keep process alive so health check can report the error
        import time
        while True:
            time.sleep(60)


def _register_full_handlers(application):
    """Register all handlers (requires DB)."""
    _text_handler = None

    # Onboarding
    try:
        from bot.handlers.onboarding import (
            cmd_start, cmd_help, cmd_settings, cmd_account, cmd_delete_account,
            cmd_calendar, handle_onboarding_callback,
        )
        application.add_handler(CommandHandler("start", cmd_start))
        application.add_handler(CommandHandler("help", cmd_help))
        application.add_handler(CommandHandler("settings", cmd_settings))
        application.add_handler(CommandHandler("account", cmd_account))
        application.add_handler(CommandHandler("calendar", cmd_calendar))
        application.add_handler(CommandHandler("deleteaccount", cmd_delete_account))
        application.add_handler(CallbackQueryHandler(handle_onboarding_callback))
        logger.info("Onboarding handlers registered")
    except Exception as e:
        logger.error(f"Failed to register onboarding handlers: {type(e).__name__}: {e}")

    # Tasks
    try:
        from bot.handlers.tasks_v2 import (
            cmd_add, cmd_list, cmd_today, cmd_week, cmd_overdue,
            cmd_done, cmd_delete, cmd_edit, cmd_undo, cmd_clear,
            cmd_analyze, cmd_streak, handle_message,
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
        application.add_handler(CommandHandler("streak", cmd_streak))
        _text_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
        logger.info("Task handlers registered")
    except Exception as e:
        logger.error(f"Failed to register task handlers: {type(e).__name__}: {e}")

    # Payments
    try:
        from bot.handlers.payments import (
            cmd_upgrade, handle_pre_checkout, handle_successful_payment,
            cmd_terms, cmd_support,
        )
        application.add_handler(CommandHandler("upgrade", cmd_upgrade))
        application.add_handler(CommandHandler("terms", cmd_terms))
        application.add_handler(CommandHandler("support", cmd_support))
        application.add_handler(PreCheckoutQueryHandler(handle_pre_checkout))
        application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, handle_successful_payment))
        logger.info("Payment handlers registered")
    except Exception as e:
        logger.error(f"Failed to register payment handlers: {type(e).__name__}: {e}")

    # Admin
    try:
        from bot.handlers.admin import cmd_migrate_notion, cmd_diagnostics
        application.add_handler(CommandHandler("migrate", cmd_migrate_notion))
        application.add_handler(CommandHandler("diagnostics", cmd_diagnostics))
        logger.info("Admin handlers registered")
    except Exception as e:
        logger.error(f"Failed to register admin handlers: {type(e).__name__}: {e}")

    # Voice (before text catch-all)
    try:
        from bot.handlers.voice_v2 import handle_voice, is_voice_configured
        if is_voice_configured():
            application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
            logger.info("Voice handler registered (Groq Whisper)")
        else:
            logger.info("Voice skipped (GROQ_API_KEY not set)")
    except Exception as e:
        logger.error(f"Failed to register voice handler: {type(e).__name__}: {e}")

    # Free text → AI brain (must be last)
    if _text_handler:
        application.add_handler(_text_handler)

    # Proactive coaching jobs
    try:
        if application.job_queue is None:
            logger.warning("Job queue unavailable (APScheduler not installed). Proactive features disabled.")
        else:
            from bot.handlers.proactive_v2 import setup_proactive_jobs
            setup_proactive_jobs(application)
    except Exception as e:
        logger.error(f"Failed to setup proactive jobs: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
