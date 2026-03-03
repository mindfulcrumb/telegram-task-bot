"""Main entry point — webhook or polling mode, multi-user, PostgreSQL."""
import html as html_mod
import sys
import os
import logging
import secrets
import threading
import json
import urllib.request
import urllib.error
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    PreCheckoutQueryHandler, ContextTypes, filters,
)

# Initialize Sentry for error tracking (optional — only if SENTRY_DSN is set)
sentry_dsn = os.environ.get("SENTRY_DSN")
if sentry_dsn:
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=sentry_dsn,
            traces_sample_rate=0.1,
            environment=os.environ.get("RAILWAY_ENVIRONMENT_NAME", "unknown"),
        )
    except ImportError:
        pass  # sentry-sdk not installed; error tracking disabled

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
_last_db_error = None  # Stores the last DB init error for diagnostics

# Cache for timer HTML (loaded once)
_timer_html_cache = None


class _HealthCheck(BaseHTTPRequestHandler):
    """Health check + WHOOP OAuth callback + timer Mini App + webhook endpoint."""

    def do_GET(self):
        if self.path.startswith("/whoop/callback"):
            self._handle_whoop_callback()
        elif self.path == "/whoop/debug":
            self._handle_whoop_debug()
        elif self.path.startswith("/strava/callback"):
            self._handle_strava_callback()
        elif self.path.startswith("/strava/webhook"):
            self._handle_strava_webhook_verify()
        elif self.path.startswith("/google/callback"):
            self._handle_google_callback()
        elif self.path.startswith("/timer"):
            self._handle_timer()
        elif self.path == "/status":
            self._handle_status()
        elif self.path.startswith("/admin/conversations"):
            self._handle_admin_conversations()
        else:
            self.send_response(200)
            self.end_headers()
            status = f"OK - {_startup_status} - db={'ready' if _db_ready else 'NOT_READY'}"
            self.wfile.write(status.encode())

    def do_POST(self):
        if self.path == "/whoop/webhook":
            self._handle_whoop_webhook()
        elif self.path == "/strava/webhook":
            self._handle_strava_webhook()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_whoop_debug(self):
        """Debug endpoint to verify WHOOP configuration (requires secret token)."""
        try:
            # Require debug token to prevent config leak
            debug_token = os.environ.get("DEBUG_TOKEN", "")
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            provided_token = params.get("token", [""])[0]
            if not debug_token or provided_token != debug_token:
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"Forbidden")
                return

            client_id = os.environ.get("WHOOP_CLIENT_ID", "")
            client_secret = os.environ.get("WHOOP_CLIENT_SECRET", "")
            redirect_uri = os.environ.get("WHOOP_REDIRECT_URI", "")
            railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")

            from bot.services.whoop_service import _get_redirect_uri, is_configured, WHOOP_SCOPES

            effective_redirect = _get_redirect_uri()

            info = {
                "configured": is_configured(),
                "client_id_set": bool(client_id),
                "client_id_preview": client_id[:8] + "..." if client_id else "MISSING",
                "client_secret_set": bool(client_secret),
                "whoop_redirect_uri_env": redirect_uri or "NOT SET",
                "railway_public_domain_env": railway_domain or "NOT SET",
                "effective_redirect_uri": effective_redirect or "EMPTY",
                "scopes": WHOOP_SCOPES,
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(info, indent=2).encode())
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f"Debug error: {html.escape(str(e))}".encode())

    def _handle_timer(self):
        """Serve the rest timer Mini App HTML page."""
        global _timer_html_cache
        try:
            if _timer_html_cache is None:
                html_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "static", "timer.html"
                )
                with open(html_path, "r") as f:
                    _timer_html_cache = f.read()

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(_timer_html_cache.encode())
        except Exception as e:
            logger.error(f"Timer page error: {e}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f"Timer error: {html.escape(str(e))}".encode())

    def _handle_status(self):
        """Diagnostic endpoint — shows DB status, env vars (names only), startup info."""
        try:
            # Always try a live DB connection test
            db_test = "unknown"
            db_live_error = None
            try:
                import psycopg2
                db_url = os.environ.get("DATABASE_URL", "")
                if db_url:
                    conn = psycopg2.connect(db_url, connect_timeout=5)
                    cur = conn.cursor()
                    cur.execute("SELECT 1")
                    cur.close()
                    conn.close()
                    db_test = "connected"
                else:
                    db_test = "no_url"
            except Exception as e:
                db_test = "connection_failed"
                db_live_error = f"{type(e).__name__}: {e}"

            # Extra diagnostics
            extra = {}
            try:
                import psycopg2, psycopg2.extras
                conn2 = psycopg2.connect(os.environ.get("DATABASE_URL", ""), connect_timeout=5)
                conn2.autocommit = True
                cur2 = conn2.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur2.execute("SELECT COUNT(*) as cnt FROM users")
                extra["total_users"] = cur2.fetchone()["cnt"]
                cur2.execute("SELECT COUNT(*) as cnt FROM usage WHERE created_at >= CURRENT_DATE")
                extra["usage_today"] = cur2.fetchone()["cnt"]
                try:
                    cur2.execute("SELECT * FROM usage_persistent ORDER BY usage_date DESC LIMIT 10")
                    extra["persistent_usage"] = [dict(r) for r in cur2.fetchall()]
                except Exception as e:
                    extra["persistent_usage_error"] = str(e)
                cur2.close()
                conn2.close()
            except Exception as e:
                extra["extra_error"] = str(e)

            info = {
                "status": _startup_status,
                "db_ready": _db_ready,
                "db_test": db_test,
                "db_live_error": db_live_error,
                "db_init_error": _last_db_error,
                "extra": extra,
                "env": {
                    "DATABASE_URL": "SET" if os.environ.get("DATABASE_URL") else "MISSING",
                    "TELEGRAM_BOT_TOKEN": "SET" if os.environ.get("TELEGRAM_BOT_TOKEN") else "MISSING",
                    "ANTHROPIC_API_KEY": "SET" if os.environ.get("ANTHROPIC_API_KEY") else "MISSING",
                    "ADMIN_USER_IDS": "SET" if os.environ.get("ADMIN_USER_IDS") else "MISSING",
                },
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(info, indent=2, default=str).encode())
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f"Status error: {e}".encode())

    def _handle_admin_conversations(self):
        """Secure admin endpoint — returns recent conversations for review."""
        try:
            import urllib.parse as _up
            parsed = _up.urlparse(self.path)
            params = _up.parse_qs(parsed.query)
            token = params.get("token", [None])[0]

            # Auth: must provide last 8 chars of bot token
            bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            expected = bot_token[-8:] if len(bot_token) >= 8 else bot_token
            if not token or token != expected:
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"Forbidden")
                return

            if not _db_ready:
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"DB not ready")
                return

            from bot.db.database import get_cursor

            # Get recent conversations (last 7 days)
            with get_cursor() as cur:
                cur.execute("""
                    SELECT c.user_id, u.first_name, u.telegram_username,
                           c.role, c.content, c.created_at
                    FROM conversations c
                    LEFT JOIN users u ON c.user_id = u.id
                    WHERE c.created_at > NOW() - INTERVAL '7 days'
                    ORDER BY c.user_id, c.created_at ASC
                """)
                rows = cur.fetchall()

            # Group by user
            users = {}
            for r in rows:
                uid = r["user_id"]
                if uid not in users:
                    users[uid] = {
                        "user_id": uid,
                        "first_name": r["first_name"],
                        "username": r["telegram_username"],
                        "messages": [],
                    }
                users[uid]["messages"].append({
                    "role": r["role"],
                    "content": r["content"][:500],  # Truncate for readability
                    "time": r["created_at"].isoformat() if r["created_at"] else None,
                })

            result = {
                "total_users": len(users),
                "total_messages": len(rows),
                "users": list(users.values()),
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result, indent=2, default=str).encode())

        except Exception as e:
            logger.error(f"Admin conversations error: {e}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f"Error: {e}".encode())

    def _handle_google_callback(self):
        """Handle Google OAuth callback — exchange code for tokens (Calendar or full Workspace)."""
        try:
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)

            error = params.get("error", [None])[0]
            if error:
                error_desc = params.get("error_description", ["Authorization denied"])[0]
                logger.error(f"Google OAuth error: {error}")
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                safe_desc = html_mod.escape(error_desc)
                self.wfile.write(
                    f"<html><body style='font-family:system-ui;text-align:center;padding:60px'>"
                    f"<h1>Calendar Authorization Failed</h1>"
                    f"<p>{safe_desc}</p>"
                    f"<p>Please try /calendar again in Telegram.</p>"
                    f"</body></html>".encode()
                )
                return

            code = params.get("code", [None])[0]
            state = params.get("state", [None])[0]

            if not code or not state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing code or state. Please try /calendar again in Telegram.")
                return

            # Validate CSRF state — cryptographic nonce check
            from bot.services import google_auth
            user_id = google_auth.validate_oauth_state(state)
            if user_id is None:
                logger.warning(f"Google OAuth invalid/expired state: {state[:20]}...")
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"Invalid or expired authorization. Please try again in Telegram.")
                return

            success, error_msg = google_auth.exchange_code(user_id, code)

            if success:
                # Detect scope level — use full scope URLs
                ws_scopes = [
                    "https://www.googleapis.com/auth/gmail.readonly",
                    "https://www.googleapis.com/auth/gmail.send",
                    "https://www.googleapis.com/auth/drive.readonly",
                    "https://www.googleapis.com/auth/tasks",
                    "https://www.googleapis.com/auth/documents",
                ]
                full_workspace = google_auth.has_scopes(user_id, ws_scopes)

                if full_workspace:
                    title = "Google Workspace Connected!"
                    desc = (
                        "Zoe now has access to your Calendar, Gmail, Drive, "
                        "Tasks, and Docs. Just ask her anything."
                    )
                else:
                    title = "Google Calendar Connected!"
                    desc = (
                        "Zoe now has access to your calendar for scheduling "
                        "and morning briefings."
                    )

                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    f"<html><body style='font-family:system-ui;text-align:center;padding:60px'>"
                    f"<h1>{title}</h1>"
                    f"<p>You can close this window and go back to Telegram.</p>"
                    f"<p>{desc}</p>"
                    f"</body></html>".encode()
                )
            else:
                self.send_response(500)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                safe_err = html_mod.escape(error_msg or "Unknown error")
                self.wfile.write(
                    f"<html><body style='font-family:system-ui;text-align:center;padding:60px'>"
                    f"<h1>Failed to Connect Calendar</h1>"
                    f"<p>{safe_err}</p>"
                    f"<p>Please try /calendar again in Telegram.</p>"
                    f"</body></html>".encode()
                )

        except Exception as e:
            logger.error(f"Google callback error: {e}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"Internal error during Google connection")

    def _handle_whoop_callback(self):
        """Handle WHOOP OAuth callback — exchange code for tokens."""
        try:
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            logger.info("WHOOP callback hit")

            # Check for OAuth error response
            error = params.get("error", [None])[0]
            if error:
                logger.error(f"WHOOP OAuth error: {error}")
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    f"<html><body style='font-family:system-ui;text-align:center;padding:60px'>"
                    f"<h1>WHOOP Authorization Failed</h1>"
                    f"<p>Error: {html_mod.escape(str(error))}</p>"
                    f"<p>{html_mod.escape(str(error_desc))}</p>"
                    f"<p>Please try /connect_whoop again in Telegram.</p>"
                    f"</body></html>".encode()
                )
                return

            code = params.get("code", [None])[0]
            state = params.get("state", [None])[0]

            if not code or not state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing code or state parameter. Please try /connect_whoop again in Telegram.")
                return

            # Validate CSRF state — cryptographic nonce check
            from bot.services.google_auth import validate_oauth_state
            user_id = validate_oauth_state(state)
            if user_id is None:
                logger.warning("WHOOP OAuth invalid/expired state")
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"Invalid or expired authorization. Please try /connect_whoop again in Telegram.")
                return

            from bot.services import whoop_service
            success, error_msg = whoop_service.exchange_code(user_id, code)

            if success:
                # Send response FIRST so Telegram WebView doesn't time out
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body style='font-family:system-ui;text-align:center;padding:60px'>"
                    b"<h1>WHOOP Connected!</h1>"
                    b"<p>You can close this window and go back to Telegram.</p>"
                    b"<p>Zoe now has access to your recovery, sleep, and strain data.</p>"
                    b"</body></html>"
                )
                self.wfile.flush()

                # Sync data AFTER response is sent (user already sees success page)
                try:
                    whoop_service.sync_all(user_id)
                except Exception:
                    pass

                # Send post-connection onboarding message in Telegram
                try:
                    from bot.services import user_service
                    u = user_service.get_user_by_id(user_id)
                    chat_id = u.get("telegram_id") if u else None
                    if chat_id:
                        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                        msg = (
                            "WHOOP is linked. Here's what I can do with it:\n\n"
                            "/recovery \u2014 today's recovery score, HRV, sleep breakdown\n"
                            "/whoop \u2014 full dashboard with strain, sleep, and recovery\n\n"
                            "You can also just ask me things like:\n"
                            "\u2022 \"what should I train today?\"\n"
                            "\u2022 \"am I recovered enough for legs?\"\n"
                            "\u2022 \"how'd I sleep?\"\n\n"
                            "I'll use your real data to answer."
                        )
                        url = f"https://api.telegram.org/bot{token}/sendMessage"
                        data = json.dumps({"chat_id": int(chat_id), "text": msg}).encode()
                        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                        urllib.request.urlopen(req, timeout=10)
                except Exception as e:
                    logger.warning(f"Failed to send WHOOP onboarding message: {e}")
            else:
                self.send_response(500)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    f"<html><body style='font-family:system-ui;text-align:center;padding:60px'>"
                    f"<h1>Failed to connect WHOOP</h1>"
                    f"<p>{html_mod.escape(str(error_msg))}</p>"
                    f"<p>Please try /connect_whoop again in Telegram.</p>"
                    f"</body></html>".encode()
                )

        except Exception as e:
            logger.error(f"WHOOP callback error: {type(e).__name__}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"Internal error during WHOOP connection")

    def _handle_whoop_webhook(self):
        """Handle WHOOP webhook events (v2 with signature verification)."""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            # Reject oversized payloads (max 1MB)
            if content_length > 1_048_576:
                self.send_response(413)
                self.end_headers()
                self.wfile.write(b"Payload too large")
                return
            body = self.rfile.read(content_length)

            # Verify webhook signature (HMAC-SHA256) — REQUIRED when secret is configured
            signature = self.headers.get("X-WHOOP-Signature", "")
            timestamp = self.headers.get("X-WHOOP-Signature-Timestamp", "")
            webhook_secret = os.environ.get("WHOOP_WEBHOOK_SECRET", "")
            if webhook_secret:
                # Secret is configured — signature is mandatory
                if not signature or not timestamp:
                    logger.warning("WHOOP webhook missing signature headers — rejecting")
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(b"Missing signature")
                    return
                from bot.services import whoop_service
                if not whoop_service.verify_webhook_signature(body, signature, timestamp):
                    logger.warning("WHOOP webhook signature verification FAILED")
                    self.send_response(403)
                    self.end_headers()
                    self.wfile.write(b"Invalid signature")
                    return
            elif signature and timestamp:
                # No secret configured but headers present — best-effort verify
                from bot.services import whoop_service
                if not whoop_service.verify_webhook_signature(body, signature, timestamp):
                    logger.warning("WHOOP webhook signature verification FAILED")
                    self.send_response(403)
                    self.end_headers()
                    self.wfile.write(b"Invalid signature")
                    return

            payload = json.loads(body) if body else {}

            event_type = payload.get("type", "")
            whoop_user_id = payload.get("user_id")
            data_id = payload.get("id")  # v2: UUID string

            if event_type and whoop_user_id:
                from bot.services import whoop_service
                whoop_service.handle_webhook(event_type, whoop_user_id, data_id)

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        except Exception as e:
            logger.error(f"WHOOP webhook error: {e}")
            self.send_response(500)
            self.end_headers()

    def _handle_strava_callback(self):
        """Handle Strava OAuth callback — exchange code for tokens."""
        try:
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            logger.info("Strava callback hit")

            error = params.get("error", [None])[0]
            if error:
                logger.error(f"Strava OAuth error: {error}")
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    f"<html><body style='font-family:system-ui;text-align:center;padding:60px'>"
                    f"<h1>Strava Authorization Failed</h1>"
                    f"<p>Error: {html_mod.escape(str(error))}</p>"
                    f"<p>Please try connecting Strava again in Telegram.</p>"
                    f"</body></html>".encode()
                )
                return

            code = params.get("code", [None])[0]
            state = params.get("state", [None])[0]

            if not code or not state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing code or state parameter.")
                return

            # Validate CSRF state
            from bot.services.google_auth import validate_oauth_state
            user_id = validate_oauth_state(state)
            if user_id is None:
                logger.warning("Strava OAuth invalid/expired state")
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"Invalid or expired authorization. Please try again in Telegram.")
                return

            from bot.services import strava_service
            success, error_msg = strava_service.exchange_code(user_id, code)

            if success:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body style='font-family:system-ui;text-align:center;padding:60px'>"
                    b"<h1>Strava Connected!</h1>"
                    b"<p>You can close this window and go back to Telegram.</p>"
                    b"<p>Zoe now has access to your running data, PRs, and training history.</p>"
                    b"</body></html>"
                )
                self.wfile.flush()

                # Sync recent activities after response
                try:
                    strava_service.sync_recent_activities(user_id, days=30)
                except Exception:
                    pass

                # Send onboarding message in Telegram
                try:
                    from bot.services import user_service
                    u = user_service.get_user_by_id(user_id)
                    chat_id = u.get("telegram_id") if u else None
                    if chat_id:
                        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                        msg = (
                            "Strava is linked. Here's what I can do with it:\n\n"
                            "Just ask me things like:\n"
                            "\u2022 \"how's my running?\"\n"
                            "\u2022 \"what are my PRs?\"\n"
                            "\u2022 \"am I overtraining?\"\n"
                            "\u2022 \"race predictions\"\n"
                            "\u2022 \"show me my last run splits\"\n\n"
                            "I'll use your actual Strava data to coach your running."
                        )
                        url = f"https://api.telegram.org/bot{token}/sendMessage"
                        data = json.dumps({"chat_id": int(chat_id), "text": msg}).encode()
                        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                        urllib.request.urlopen(req, timeout=10)
                except Exception as e:
                    logger.warning(f"Failed to send Strava onboarding message: {e}")
            else:
                self.send_response(500)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    f"<html><body style='font-family:system-ui;text-align:center;padding:60px'>"
                    f"<h1>Failed to connect Strava</h1>"
                    f"<p>{html_mod.escape(str(error_msg))}</p>"
                    f"<p>Please try connecting Strava again in Telegram.</p>"
                    f"</body></html>".encode()
                )

        except Exception as e:
            logger.error(f"Strava callback error: {type(e).__name__}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"Internal error during Strava connection")

    def _handle_strava_webhook_verify(self):
        """Handle Strava webhook subscription validation (GET request)."""
        try:
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            mode = params.get("hub.mode", [None])[0]
            challenge = params.get("hub.challenge", [None])[0]
            verify_token = params.get("hub.verify_token", [None])[0]

            expected_token = os.environ.get("STRAVA_WEBHOOK_VERIFY_TOKEN", "zoe_strava_verify")
            if mode == "subscribe" and verify_token == expected_token and challenge:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"hub.challenge": challenge}).encode())
                logger.info("Strava webhook subscription validated")
            else:
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"Verification failed")
        except Exception as e:
            logger.error(f"Strava webhook verify error: {e}")
            self.send_response(500)
            self.end_headers()

    def _handle_strava_webhook(self):
        """Handle Strava webhook events (activity create/update/delete, athlete deauth)."""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 1_048_576:
                self.send_response(413)
                self.end_headers()
                self.wfile.write(b"Payload too large")
                return
            body = self.rfile.read(content_length)

            payload = json.loads(body) if body else {}

            from bot.services import strava_service
            strava_service.handle_webhook_event(payload)

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        except Exception as e:
            logger.error(f"Strava webhook error: {e}")
            self.send_response(500)
            self.end_headers()

    def log_message(self, *args):
        pass


async def _post_init(application):
    """Set bot commands, description, and profile on startup."""
    bot = application.bot

    # ── Bot description (shown on "What can this bot do?" screen) ──
    try:
        await bot.set_my_description(
            description=(
                "Zoe manages your tasks, programs your training, "
                "tracks your protocols, and keeps everything moving.\n\n"
                "Talk naturally, send voice notes, or use commands. Tap Start to begin.\n\n"
                "Zoe provides educational wellness information only \u2014 not medical advice. "
                "Always consult a healthcare professional before starting any new protocol."
            )
        )
        await bot.set_my_short_description(
            short_description="Your coach for training, tasks, and biohacking."
        )
        logger.info("Bot description and short description set")
    except Exception as e:
        logger.error(f"Failed to set bot description: {type(e).__name__}: {e}")

    # ── Bot menu commands ──
    try:
        commands = [
            BotCommand("start", "Start / restart Zoe"),
            BotCommand("add", "Add a task"),
            BotCommand("today", "Today's tasks"),
            BotCommand("list", "All tasks"),
            BotCommand("week", "This week's tasks"),
            BotCommand("workout", "Log a workout"),
            BotCommand("recovery", "WHOOP recovery score"),
            BotCommand("gains", "Streak, PRs & patterns"),
            BotCommand("protocols", "Peptide protocols"),
            BotCommand("supplements", "Supplement stack"),
            BotCommand("bloodwork", "Bloodwork analysis"),
            BotCommand("dose", "Log a peptide dose"),
            BotCommand("calendar", "Google Calendar"),
            BotCommand("google", "Google Workspace"),
            BotCommand("settings", "Your preferences"),
            BotCommand("account", "Account info"),
            BotCommand("referral", "Your referral link & stats"),
            BotCommand("upgrade", "Unlock Zoe Pro"),
            BotCommand("billing", "Manage your subscription"),
            BotCommand("help", "All commands"),
        ]
        await bot.set_my_commands(commands)
        logger.info(f"Bot menu commands set ({len(commands)} commands)")
    except Exception as e:
        logger.error(f"Failed to set bot commands: {type(e).__name__}: {e}")


async def _fallback_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback /start when DB is not available."""
    await update.message.reply_text(
        "Hey! I'm having a brief technical hiccup — give me a minute and try again 🙏"
    )


async def _fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback for any message when DB is not available."""
    await update.message.reply_text(
        "Sorry, I'm briefly unavailable — try again in a minute!"
    )


def main():
    """Start the bot."""
    global _db_ready, _last_db_error

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

    # Stage 1 — process started (logged only, no admin message)

    global _startup_status

    port = int(os.environ.get("PORT", "8443"))
    # Only use webhook if explicitly set via BOT_MODE=webhook
    # RAILWAY_PUBLIC_DOMAIN is auto-injected and can conflict
    use_webhook = os.environ.get("BOT_MODE", "").lower() == "webhook"
    webhook_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "") if use_webhook else ""

    # Start health check — always needed for Railway in polling mode
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
    except Exception as e:
        _notify_admin(f"🔴 <b>Stage 2 FAILED</b>: Application.build() crashed\n<code>{type(e).__name__}: {e}</code>")
        raise

    # Try to initialize PostgreSQL (with retry for transient failures)
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        from bot.db.database import initialize as init_db
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                init_db()
                _db_ready = True
                logger.info(f"PostgreSQL initialized successfully (attempt {attempt})")
                break
            except Exception as e:
                _last_db_error = f"{type(e).__name__}: {e}"
                logger.error(f"PostgreSQL init attempt {attempt}/{max_retries} failed: {_last_db_error}")
                if attempt < max_retries:
                    import time as _time
                    wait = attempt * 2  # 2s, 4s
                    logger.info(f"Retrying DB connection in {wait}s...")
                    _time.sleep(wait)
                    # Reset pool so next attempt creates fresh connections
                    try:
                        from bot.db.database import close as close_db
                        close_db()
                    except Exception:
                        pass
                else:
                    _notify_admin(f"🟠 <b>Stage 3</b>: PostgreSQL FAILED after {max_retries} attempts — degraded mode\n<code>{type(e).__name__}: {e}</code>")
                    _db_ready = False

        if _db_ready:

            # Seed knowledge base (idempotent — skips if data exists)
            try:
                from bot.data.seed_knowledge import seed_all
                seed_all()
            except Exception as e:
                logger.error(f"Knowledge base seeding failed: {type(e).__name__}: {e}")

            # Seed v2 data (regulatory, interactions, stacking protocols, new compounds)
            try:
                from bot.data.seed_knowledge_v2 import seed_all_v2
                seed_all_v2()
            except Exception as e:
                logger.error(f"Knowledge v2 seeding failed: {type(e).__name__}: {e}")

            # Seed v3 data (deep expert protocols — Koniver, Jay Campbell, Epitalon+Thymalin, etc.)
            try:
                from bot.data.seed_knowledge_v3 import seed_all_v3
                seed_all_v3()
            except Exception as e:
                logger.error(f"Knowledge v3 seeding failed: {type(e).__name__}: {e}")

            # Seed owner's training program into user memories
            try:
                from bot.data.seed_owner_program import seed_all_owner
                seed_all_owner()
            except Exception as e:
                logger.error(f"Owner program seeding failed: {type(e).__name__}: {e}")
    else:
        logger.warning("DATABASE_URL not set — running in degraded mode (no DB)")
        _notify_admin("🟠 <b>Stage 3</b>: No DATABASE_URL — degraded mode")
        _db_ready = False

    if _db_ready:
        # Full handler registration
        try:
            _register_full_handlers(application)
            logger.info("All handlers registered successfully")
        except Exception as e:
            logger.error(f"Handler registration failed: {type(e).__name__}: {e}")
            _notify_admin(f"🟠 <b>Stage 4</b>: Handler registration FAILED\n<code>{type(e).__name__}: {e}</code>")
    else:
        # Degraded mode — just respond that DB is missing
        application.add_handler(CommandHandler("start", _fallback_start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _fallback_message))
        logger.warning("Running in DEGRADED MODE — only basic responses available")
        _notify_admin("🔴 <b>Zoe DEGRADED MODE</b>: DB not ready — fallback handlers only")

    # --- Start ---

    try:
        if use_webhook and webhook_domain:
            # Webhook mode — only if BOT_MODE=webhook is explicitly set
            webhook_secret = os.environ.get("WEBHOOK_SECRET") or secrets.token_urlsafe(32)
            webhook_path = f"/webhook/{secrets.token_urlsafe(16)}"
            webhook_url = f"https://{webhook_domain}{webhook_path}"
            logger.info(f"Starting WEBHOOK mode on port {port}")
            logger.info(f"Webhook URL: {webhook_url}")
            _startup_status = "running (webhook)"

            application.run_webhook(
                listen="0.0.0.0",
                port=port,
                url_path=webhook_path,
                webhook_url=webhook_url,
                secret_token=webhook_secret,
                allowed_updates=["message", "callback_query", "pre_checkout_query"],
            )
        else:
            # Polling mode (default) — health check already running on PORT
            logger.info("Starting POLLING mode")
            _startup_status = "running (polling)"

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
            cmd_calendar, cmd_google, cmd_referral, cmd_memory,
            handle_onboarding_callback, handle_location, handle_contact,
        )
        application.add_handler(CommandHandler("start", cmd_start))
        application.add_handler(CommandHandler("referral", cmd_referral))
        application.add_handler(CommandHandler("refer", cmd_referral))
        application.add_handler(CommandHandler("memory", cmd_memory))
        application.add_handler(CommandHandler("help", cmd_help))
        application.add_handler(CommandHandler("settings", cmd_settings))
        application.add_handler(CommandHandler("account", cmd_account))
        application.add_handler(CommandHandler("calendar", cmd_calendar))
        application.add_handler(CommandHandler("google", cmd_google))
        application.add_handler(CommandHandler("deleteaccount", cmd_delete_account))
        # Pattern-specific callbacks must be registered BEFORE the catch-all onboarding handler
        try:
            from bot.handlers.tasks_v2 import handle_whoop_callback
            application.add_handler(CallbackQueryHandler(handle_whoop_callback, pattern="^whoop_"))
        except Exception:
            pass
        try:
            from bot.handlers.workout_session import handle_workout_session_callback
            application.add_handler(CallbackQueryHandler(handle_workout_session_callback, pattern="^ws:"))
        except Exception:
            pass
        try:
            from bot.handlers.protocol_cards import (
                handle_protocol_wizard_callback,
                handle_protocol_dashboard_callback,
                handle_dose_reminder_callback,
                handle_quick_dose_callback,
            )
            application.add_handler(CallbackQueryHandler(handle_protocol_wizard_callback, pattern="^pw:"))
            application.add_handler(CallbackQueryHandler(handle_protocol_dashboard_callback, pattern="^pd:"))
            application.add_handler(CallbackQueryHandler(handle_dose_reminder_callback, pattern="^dr:"))
            application.add_handler(CallbackQueryHandler(handle_quick_dose_callback, pattern="^qd:"))
            logger.info("Protocol card handlers registered (pw, pd, dr, qd)")
        except Exception as e:
            logger.error(f"Failed to register protocol card handlers: {type(e).__name__}: {e}")
        try:
            from bot.handlers.tasks_v2 import handle_feedback_callback
            application.add_handler(CallbackQueryHandler(handle_feedback_callback, pattern="^fb:"))
        except Exception:
            pass
        try:
            from bot.handlers.payments import callback_upgrade_dismiss
            application.add_handler(CallbackQueryHandler(callback_upgrade_dismiss, pattern="^upgrade:dismiss$"))
        except Exception:
            pass
        application.add_handler(CallbackQueryHandler(
            handle_onboarding_callback,
            pattern="^(ob:|tz:|settings:|settime:|show_help$|show_calendar$|show_capabilities$)"
        ))
        application.add_handler(MessageHandler(filters.LOCATION, handle_location))
        application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
        logger.info("Onboarding handlers registered")
    except Exception as e:
        logger.error(f"Failed to register onboarding handlers: {type(e).__name__}: {e}")

    # Tasks + Fitness + Biohacking
    try:
        from bot.handlers.tasks_v2 import (
            cmd_add, cmd_list, cmd_today, cmd_week, cmd_overdue,
            cmd_done, cmd_delete, cmd_edit, cmd_undo, cmd_clear,
            cmd_analyze, cmd_streak, cmd_workout, cmd_wtest, cmd_metrics, cmd_gains,
            cmd_protocols, cmd_supplements, cmd_bloodwork, cmd_dose,
            cmd_connect_whoop, cmd_recovery, cmd_whoop, cmd_disconnect_whoop,
            handle_whoop_callback, handle_feedback_callback, handle_message,
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
        application.add_handler(CommandHandler("workout", cmd_workout))
        application.add_handler(CommandHandler("wtest", cmd_wtest))
        application.add_handler(CommandHandler("metrics", cmd_metrics))
        application.add_handler(CommandHandler("gains", cmd_gains))
        application.add_handler(CommandHandler("protocols", cmd_protocols))
        application.add_handler(CommandHandler("supplements", cmd_supplements))
        application.add_handler(CommandHandler("bloodwork", cmd_bloodwork))
        application.add_handler(CommandHandler("dose", cmd_dose))
        application.add_handler(CommandHandler("connect_whoop", cmd_connect_whoop))
        application.add_handler(CommandHandler("recovery", cmd_recovery))
        application.add_handler(CommandHandler("whoop", cmd_whoop))
        application.add_handler(CommandHandler("disconnect_whoop", cmd_disconnect_whoop))
        _text_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
        logger.info("Task + fitness + biohacking + WHOOP handlers registered")
    except Exception as e:
        logger.error(f"Failed to register task handlers: {type(e).__name__}: {e}")

    # Payments
    try:
        from bot.handlers.payments import (
            cmd_upgrade, cmd_billing,
            handle_pre_checkout, handle_successful_payment,
            cmd_terms, cmd_support,
        )
        application.add_handler(CommandHandler("upgrade", cmd_upgrade))
        application.add_handler(CommandHandler("billing", cmd_billing))
        application.add_handler(CommandHandler("terms", cmd_terms))
        application.add_handler(CommandHandler("support", cmd_support))
        application.add_handler(PreCheckoutQueryHandler(handle_pre_checkout))
        application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, handle_successful_payment))
        logger.info("Payment handlers registered")
    except Exception as e:
        logger.error(f"Failed to register payment handlers: {type(e).__name__}: {e}")

    # Admin
    try:
        from bot.handlers.admin import cmd_migrate_notion, cmd_diagnostics, cmd_audit
        application.add_handler(CommandHandler("migrate", cmd_migrate_notion))
        application.add_handler(CommandHandler("diagnostics", cmd_diagnostics))
        application.add_handler(CommandHandler("audit", cmd_audit))
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

    # Photo/document uploads (blood tests, lab results — images + PDFs)
    try:
        from bot.handlers.photo_handler import handle_photo
        application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
        application.add_handler(MessageHandler(
            filters.Document.IMAGE, handle_photo
        ))
        application.add_handler(MessageHandler(
            filters.Document.PDF, handle_photo
        ))
        logger.info("Photo handler registered (blood test uploads — images + PDFs)")
    except Exception as e:
        logger.error(f"Failed to register photo handler: {type(e).__name__}: {e}")

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
