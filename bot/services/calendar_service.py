"""Google Calendar integration — OAuth 2.0 with iCal URL fallback."""
import logging
from datetime import datetime, timezone, timedelta

import httpx

from bot.db.database import get_cursor

# Re-export auth functions so existing imports don't break
from bot.services.google_auth import (  # noqa: F401
    is_configured,
    get_auth_url,
    exchange_code,
    get_access_token,
    is_connected,
    revoke_access,
    has_scopes,
    _http,
    GOOGLE_CALENDAR_READONLY_SCOPE,
    GOOGLE_WORKSPACE_SCOPES,
)

logger = logging.getLogger(__name__)

GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3"


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
                "id": item.get("id"),
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


# --- Event creation (requires calendar write scope) ---

def create_event(
    user_id: int,
    summary: str,
    start_dt: datetime,
    end_dt: datetime,
    description: str = None,
    location: str = None,
) -> dict | None:
    """Create a Google Calendar event. Returns event dict or None on failure."""
    token = get_access_token(user_id)
    if not token:
        return None

    body = {
        "summary": summary,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "UTC"},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location

    try:
        resp = _http.post(
            f"{GOOGLE_CALENDAR_API}/calendars/primary/events",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code in (200, 201):
            return resp.json()
        logger.error(f"Calendar create_event failed {resp.status_code}: {resp.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"Calendar create_event error: {type(e).__name__}: {e}")
        return None


def delete_event(user_id: int, event_id: str) -> bool:
    """Delete a Google Calendar event by ID. Returns True on success."""
    token = get_access_token(user_id)
    if not token:
        return False

    try:
        resp = _http.delete(
            f"{GOOGLE_CALENDAR_API}/calendars/primary/events/{event_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code in (200, 204):
            return True
        logger.error(f"Calendar delete_event failed {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        logger.error(f"Calendar delete_event error: {type(e).__name__}: {e}")
        return False


def update_event(
    user_id: int,
    event_id: str,
    summary: str = None,
    start_dt: datetime = None,
    end_dt: datetime = None,
    description: str = None,
    location: str = None,
) -> dict | None:
    """Update a Google Calendar event. Only provided fields are changed."""
    token = get_access_token(user_id)
    if not token:
        return None

    body = {}
    if summary is not None:
        body["summary"] = summary
    if start_dt is not None:
        body["start"] = {"dateTime": start_dt.isoformat(), "timeZone": "UTC"}
    if end_dt is not None:
        body["end"] = {"dateTime": end_dt.isoformat(), "timeZone": "UTC"}
    if description is not None:
        body["description"] = description
    if location is not None:
        body["location"] = location

    if not body:
        return None

    try:
        resp = _http.patch(
            f"{GOOGLE_CALENDAR_API}/calendars/primary/events/{event_id}",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 200:
            return resp.json()
        logger.error(f"Calendar update_event failed {resp.status_code}: {resp.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"Calendar update_event error: {type(e).__name__}: {e}")
        return None


def search_events(user_id: int, query: str, days: int = 14, max_events: int = 10) -> list[dict]:
    """Search upcoming calendar events by text query. Returns events matching the query."""
    token = get_access_token(user_id)
    if not token:
        return []

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
                "q": query,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        if resp.status_code != 200:
            logger.error(f"Calendar search failed {resp.status_code}: {resp.text[:200]}")
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
                "id": item.get("id"),
                "title": item.get("summary", "Untitled"),
                "start": dt,
                "all_day": all_day,
            })

        return events

    except Exception as e:
        logger.error(f"Calendar search error: {type(e).__name__}: {e}")
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
        event_id = e.get("id", "")
        lines.append(f"- {e['title']} — {time_str} [event_id={event_id}]")

    return "\n".join(lines)
