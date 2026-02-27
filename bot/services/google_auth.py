"""Google OAuth 2.0 — shared auth module for all Google Workspace APIs."""
import logging
import os
import secrets
import urllib.parse
from datetime import datetime, timezone, timedelta

import httpx

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)

# Google OAuth endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Scope constants
GOOGLE_CALENDAR_READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"

GOOGLE_WORKSPACE_SCOPES = " ".join([
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/documents",
])

_http = httpx.Client(timeout=15)


# --- Configuration ---

def _get_client_id() -> str:
    return os.environ.get("GOOGLE_CLIENT_ID", "")


def _get_client_secret() -> str:
    return os.environ.get("GOOGLE_CLIENT_SECRET", "")


def _get_redirect_uri() -> str:
    explicit = os.environ.get("GOOGLE_REDIRECT_URI", "")
    if explicit:
        return explicit
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if domain:
        return f"https://{domain}/google/callback"
    return ""


def is_configured() -> bool:
    """Check if Google OAuth credentials are set."""
    return bool(_get_client_id() and _get_client_secret() and _get_redirect_uri())


# --- OAuth ---

def get_auth_url(user_id: int, scopes: str = None) -> str | None:
    """Generate Google OAuth consent URL.

    scopes: space-separated scope string. Defaults to GOOGLE_WORKSPACE_SCOPES.
    """
    client_id = _get_client_id()
    redirect_uri = _get_redirect_uri()
    if not client_id or not redirect_uri:
        return None

    if scopes is None:
        scopes = GOOGLE_WORKSPACE_SCOPES

    # CSRF protection: cryptographic nonce tied to user_id, stored in DB
    nonce = secrets.token_urlsafe(32)
    state = f"uid_{user_id:06d}_{nonce}"
    _store_oauth_state(user_id, nonce)

    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })
    return f"{GOOGLE_AUTH_URL}?{params}"


def _store_oauth_state(user_id: int, nonce: str):
    """Store OAuth state nonce in DB for CSRF validation (expires after 10 min)."""
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO oauth_states (user_id, nonce, expires_at)
               VALUES (%s, %s, NOW() + INTERVAL '10 minutes')
               ON CONFLICT (user_id) DO UPDATE SET
                   nonce = EXCLUDED.nonce, expires_at = EXCLUDED.expires_at""",
            (user_id, nonce),
        )


def validate_oauth_state(state: str) -> int | None:
    """Validate OAuth state and return user_id if valid. Deletes state after use."""
    if not state or "_" not in state:
        return None
    parts = state.split("_", 2)
    if len(parts) < 3 or parts[0] != "uid":
        return None
    try:
        user_id = int(parts[1])
    except ValueError:
        return None
    nonce = parts[2]

    with get_cursor() as cur:
        cur.execute(
            """DELETE FROM oauth_states
               WHERE user_id = %s AND nonce = %s AND expires_at > NOW()
               RETURNING user_id""",
            (user_id, nonce),
        )
        row = cur.fetchone()
        return row["user_id"] if row else None


def exchange_code(user_id: int, code: str) -> tuple[bool, str]:
    """Exchange authorization code for tokens and store in DB."""
    try:
        resp = _http.post(
            GOOGLE_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _get_redirect_uri(),
                "client_id": _get_client_id(),
                "client_secret": _get_client_secret(),
            },
        )

        if resp.status_code != 200:
            logger.error(f"Google token exchange failed: HTTP {resp.status_code}")
            return False, f"Token exchange failed (HTTP {resp.status_code})"

        token_data = resp.json()
    except Exception as e:
        logger.error(f"Google token exchange failed: {e}")
        return False, str(e)

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3600)
    scopes = token_data.get("scope", "")

    if not access_token:
        return False, "No access_token in response"

    if not refresh_token:
        return False, "No refresh_token -- try disconnecting and reconnecting"

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    try:
        with get_cursor() as cur:
            cur.execute(
                """INSERT INTO google_calendar_tokens
                       (user_id, access_token, refresh_token, expires_at, scopes)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (user_id) DO UPDATE SET
                       access_token = EXCLUDED.access_token,
                       refresh_token = EXCLUDED.refresh_token,
                       expires_at = EXCLUDED.expires_at,
                       scopes = EXCLUDED.scopes,
                       updated_at = NOW()""",
                (user_id, access_token, refresh_token, expires_at, scopes),
            )
    except Exception as e:
        logger.error(f"Google token storage failed for user {user_id}: {e}")
        return False, "Failed to store tokens"

    logger.info(f"Google tokens stored for user {user_id} (scopes: {scopes})")
    return True, ""


def _refresh_tokens(user_id: int, refresh_token: str) -> str | None:
    """Refresh expired access token. Returns new access_token or None."""
    try:
        resp = _http.post(
            GOOGLE_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": _get_client_id(),
                "client_secret": _get_client_secret(),
            },
        )

        if resp.status_code != 200:
            logger.error(f"Google token refresh failed for user {user_id}: HTTP {resp.status_code}")
            return None

        token_data = resp.json()
    except Exception as e:
        logger.error(f"Google token refresh failed for user {user_id}: {e}")
        return None

    new_access = token_data.get("access_token")
    new_refresh = token_data.get("refresh_token", refresh_token)
    expires_in = token_data.get("expires_in", 3600)

    if not new_access:
        return None

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    with get_cursor() as cur:
        cur.execute(
            """UPDATE google_calendar_tokens
               SET access_token = %s, refresh_token = %s, expires_at = %s, updated_at = NOW()
               WHERE user_id = %s""",
            (new_access, new_refresh, expires_at, user_id),
        )

    logger.info(f"Google token refreshed for user {user_id}")
    return new_access


def get_access_token(user_id: int) -> str | None:
    """Get valid access token, auto-refreshing if expired."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT access_token, refresh_token, expires_at FROM google_calendar_tokens WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            return None

    # Refresh if expiring within 5 minutes
    if row["expires_at"] and row["expires_at"] < datetime.now(timezone.utc) + timedelta(minutes=5):
        if row["refresh_token"]:
            return _refresh_tokens(user_id, row["refresh_token"])
        return None

    return row["access_token"]


# --- Connection checks ---

def is_connected(user_id: int) -> bool:
    """Check if user has any Google tokens stored (OAuth or legacy iCal)."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT EXISTS(SELECT 1 FROM google_calendar_tokens WHERE user_id = %s)",
            (user_id,),
        )
        if cur.fetchone()["exists"]:
            return True
        # Fallback: check legacy iCal URL
        cur.execute(
            "SELECT google_calendar_url FROM users WHERE id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        return bool(row and row.get("google_calendar_url"))


def has_scopes(user_id: int, required_scopes: list[str]) -> bool:
    """Check if stored token includes all required scopes."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT scopes FROM google_calendar_tokens WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        if not row or not row["scopes"]:
            return False
    stored = row["scopes"].split()
    return all(s in stored for s in required_scopes)


def revoke_access(user_id: int):
    """Disconnect Google — revoke token at Google, then delete locally."""
    # Attempt to revoke at Google first (best-effort)
    with get_cursor() as cur:
        cur.execute(
            "SELECT refresh_token FROM google_calendar_tokens WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()

    if row and row.get("refresh_token"):
        try:
            _http.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": row["refresh_token"]},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except Exception as e:
            logger.warning(f"Google token revocation failed for user {user_id}: {e}")

    with get_cursor() as cur:
        cur.execute("DELETE FROM google_calendar_tokens WHERE user_id = %s", (user_id,))
        cur.execute("UPDATE users SET google_calendar_url = NULL WHERE id = %s", (user_id,))
    logger.info(f"Google disconnected for user {user_id}")
