"""Google Calendar integration via iCal URL — no OAuth needed."""
import logging
from datetime import datetime, timezone, timedelta

import httpx
from icalendar import Calendar

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)


def save_calendar_url(user_id: int, url: str):
    """Save user's Google Calendar iCal URL."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE users SET google_calendar_url = %s WHERE id = %s",
            (url, user_id)
        )


def remove_calendar_url(user_id: int):
    """Remove user's calendar connection."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE users SET google_calendar_url = NULL WHERE id = %s",
            (user_id,)
        )


def get_calendar_url(user_id: int) -> str | None:
    """Get user's saved calendar URL."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT google_calendar_url FROM users WHERE id = %s",
            (user_id,)
        )
        row = cur.fetchone()
        return row["google_calendar_url"] if row else None


def fetch_upcoming_events(user_id: int, days: int = 3, max_events: int = 10) -> list[dict]:
    """Fetch upcoming calendar events from user's iCal URL."""
    url = get_calendar_url(user_id)
    if not url:
        return []

    try:
        response = httpx.get(url, timeout=15.0, follow_redirects=True)
        if response.status_code != 200:
            logger.error(f"Calendar fetch failed for user {user_id}: HTTP {response.status_code}")
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

            # Handle date vs datetime
            if not hasattr(dt, "hour"):
                # All-day event — treat as midnight UTC
                dt = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
            elif dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            if now <= dt <= cutoff:
                summary = str(component.get("SUMMARY", "Untitled"))
                events.append({
                    "title": summary,
                    "start": dt,
                    "all_day": not hasattr(dtstart.dt, "hour"),
                })

        events.sort(key=lambda e: e["start"])
        return events[:max_events]

    except Exception as e:
        logger.error(f"Calendar parse failed for user {user_id}: {type(e).__name__}: {e}")
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
