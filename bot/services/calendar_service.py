"""Google Calendar integration — OAuth 2.0 with iCal URL fallback."""
import logging
import os
import urllib.parse
from datetime import datetime, timezone, timedelta

import httpx

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)

# Google OAuth endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3"
GOOGLE_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"

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

def get_auth_url(user_id: int) -> str | None:
    """Generate Google OAuth consent URL."""
    client_id = _get_client_id()
    redirect_uri = _get_redirect_uri()
    if not client_id or not redirect_uri:
        return None

    state = f"uid_{user_id:06d}"
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })
    return f"{GOOGLE_AUTH_URL}?{params}"


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
            body = resp.text[:500]
            logger.error(f"Google token exchange HTTP {resp.status_code}: {body}")
            return False, f"HTTP {resp.status_code}"

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
        return False, "No refresh_token — try disconnecting and reconnecting"

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

    logger.info(f"Google Calendar tokens stored for user {user_id}")
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
            logger.error(f"Google token refresh HTTP {resp.status_code}: {resp.text[:200]}")
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
    """Check if user has Google Calendar connected (OAuth or iCal)."""
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


def revoke_access(user_id: int):
    """Disconnect Google Calendar — delete tokens and legacy URL."""
    with get_cursor() as cur:
        cur.execute("DELETE FROM google_calendar_tokens WHERE user_id = %s", (user_id,))
        cur.execute("UPDATE users SET google_calendar_url = NULL WHERE id = %s", (user_id,))
    logger.info(f"Google Calendar disconnected for user {user_id}")


# --- Legacy iCal functions (kept for backward compat) ---

def save_calendar_url(user_id: int, url: str):
    """Save user's Google Calendar iCal URL (legacy)."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE users SET google_calendar_url = %s WHERE id = %s",
            (url, user_id)
        )


def get_calendar_url(user_id: int) -> str | None:
    """Get user's saved iCal URL (legacy)."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT google_calendar_url FROM users WHERE id = %s",
            (user_id,)
        )
        row = cur.fetchone()
        return row["google_calendar_url"] if row else None


# --- Event fetching ---

def fetch_upcoming_events(user_id: int, days: int = 3, max_events: int = 10) -> list[dict]:
    """Fetch upcoming events — tries OAuth first, falls back to iCal URL."""
    # Try OAuth first
    token = get_access_token(user_id)
    if token:
        return _fetch_events_oauth(token, days, max_events)

    # Fallback to iCal URL
    return _fetch_events_ical(user_id, days, max_events)


def _fetch_events_oauth(token: str, days: int, max_events: int) -> list[dict]:
    """Fetch events from Google Calendar API using OAuth token."""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days)

    try:
        resp = _http.get(
            f"{GOOGLE_CALENDAR_API}/calendars/primary/events",
            params={
                "timeMin": now.isoformat(),
                "timeMax": cutoff.isoformat(),
                "maxResults": max_events,
                "singleEvents": "true",
                "orderBy": "startTime",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        if resp.status_code == 401:
            logger.error("Google Calendar API 401 — token expired or revoked")
            return []
        if resp.status_code != 200:
            logger.error(f"Google Calendar API HTTP {resp.status_code}: {resp.text[:200]}")
            return []

        events = []
        for item in resp.json().get("items", []):
            start = item.get("start", {})
            dt_str = start.get("dateTime") or start.get("date")
            if not dt_str:
                continue

            all_day = "dateTime" not in start
            if all_day:
                dt = datetime.strptime(dt_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            else:
                dt = datetime.fromisoformat(dt_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)

            events.append({
                "title": item.get("summary", "Untitled"),
                "start": dt,
                "all_day": all_day,
            })

        return events

    except Exception as e:
        logger.error(f"Google Calendar API failed: {type(e).__name__}: {e}")
        return []


def _fetch_events_ical(user_id: int, days: int, max_events: int) -> list[dict]:
    """Fetch events from iCal URL (legacy fallback)."""
    url = get_calendar_url(user_id)
    if not url:
        return []

    try:
        from icalendar import Calendar

        response = httpx.get(url, timeout=15.0, follow_redirects=True)
        if response.status_code != 200:
            logger.error(f"iCal fetch failed for user {user_id}: HTTP {response.status_code}")
            return []

        cal = Calendar.from_ical(response.text)
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=days)
        events = []

        for component in cal.walk():
            if component.name != "VEVENT":
                continue

            dtstart = component.get("DTSTART")
            if not dtstart:
                continue
            dt = dtstart.dt

            if not hasattr(dt, "hour"):
                dt = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
            elif dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            if now <= dt <= cutoff:
                events.append({
                    "title": str(component.get("SUMMARY", "Untitled")),
                    "start": dt,
                    "all_day": not hasattr(dtstart.dt, "hour"),
                })

        events.sort(key=lambda e: e["start"])
        return events[:max_events]

    except Exception as e:
        logger.error(f"iCal parse failed for user {user_id}: {type(e).__name__}: {e}")
        return []


def format_events_for_ai(events: list[dict]) -> str:
    """Format calendar events as text for the AI system prompt."""
    if not events:
        return ""

    lines = ["UPCOMING CALENDAR EVENTS:"]
    for e in events:
        dt = e["start"]
        if e.get("all_day"):
            time_str = dt.strftime("%A %b %d") + " (all day)"
        else:
            time_str = dt.strftime("%A %b %d at %I:%M %p")
        lines.append(f"- {e['title']} — {time_str}")

    return "\n".join(lines)
