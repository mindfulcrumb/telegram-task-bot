"""Keyword-based task classifier for categorization and parsing."""
import re
from datetime import datetime, timedelta, date
from typing import Optional, Tuple
from dateutil import parser as date_parser
from dateutil.relativedelta import relativedelta, MO, TU, WE, TH, FR, SA, SU


# Keywords that indicate business tasks
BUSINESS_KEYWORDS = [
    "client", "meeting", "invoice", "project", "deadline", "report",
    "presentation", "email", "call", "contract", "proposal", "budget",
    "quarterly", "annual", "review", "stakeholder", "deliverable",
    "milestone", "sprint", "standup", "sync", "office", "work",
    "colleague", "boss", "manager", "team", "company", "business",
    "professional", "corporate", "vendor", "supplier", "customer"
]

# Keywords that indicate personal tasks
PERSONAL_KEYWORDS = [
    "gym", "groceries", "doctor", "dentist", "pharmacy", "laundry",
    "clean", "cook", "family", "friend", "birthday", "anniversary",
    "vacation", "holiday", "exercise", "workout", "meditation",
    "hobby", "game", "movie", "book", "read", "relax", "sleep",
    "pet", "dog", "cat", "home", "apartment", "car", "repair",
    "shop", "buy", "personal", "self", "health", "wellness"
]

# Day name mappings for date parsing
WEEKDAY_MAP = {
    "monday": MO, "mon": MO,
    "tuesday": TU, "tue": TU, "tues": TU,
    "wednesday": WE, "wed": WE,
    "thursday": TH, "thu": TH, "thur": TH, "thurs": TH,
    "friday": FR, "fri": FR,
    "saturday": SA, "sat": SA,
    "sunday": SU, "sun": SU
}


def classify_task(text: str) -> str:
    """
    Classify a task as 'Personal' or 'Business' based on keywords.

    Returns 'Business' if business keywords found, otherwise 'Personal'.
    Explicit hashtags (#personal, #business) override keyword detection.
    """
    text_lower = text.lower()

    # Check for explicit hashtags first (highest priority)
    if "#business" in text_lower or "@business" in text_lower:
        return "Business"
    if "#personal" in text_lower or "@personal" in text_lower:
        return "Personal"

    # Count keyword matches
    business_count = sum(1 for kw in BUSINESS_KEYWORDS if kw in text_lower)
    personal_count = sum(1 for kw in PERSONAL_KEYWORDS if kw in text_lower)

    # Return based on which has more matches
    if business_count > personal_count:
        return "Business"
    return "Personal"


def extract_priority(text: str) -> Tuple[str, str]:
    """
    Extract priority from text and return (priority, cleaned_text).

    Supports: !high, !low, !urgent, !medium
    """
    priority = "Medium"
    cleaned = text

    priority_patterns = {
        r"!high\b": "High",
        r"!urgent\b": "High",
        r"!low\b": "Low",
        r"!medium\b": "Medium",
        r"!med\b": "Medium"
    }

    for pattern, prio in priority_patterns.items():
        if re.search(pattern, text, re.IGNORECASE):
            priority = prio
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
            break

    return priority, cleaned.strip()


def extract_date(text: str) -> Tuple[Optional[date], str]:
    """
    Extract due date from text and return (date, cleaned_text).

    Supports:
    - "today", "tomorrow", "day after tomorrow"
    - "next monday", "next week", "next month"
    - "in 3 days", "in 2 weeks"
    - Explicit dates like "jan 15", "2024-01-15"
    """
    text_lower = text.lower()
    today = date.today()
    extracted_date = None
    cleaned = text

    # Pattern: "today"
    if re.search(r"\btoday\b", text_lower):
        extracted_date = today
        cleaned = re.sub(r"\btoday\b", "", cleaned, flags=re.IGNORECASE)

    # Pattern: "tomorrow"
    elif re.search(r"\btomorrow\b", text_lower):
        extracted_date = today + timedelta(days=1)
        cleaned = re.sub(r"\btomorrow\b", "", cleaned, flags=re.IGNORECASE)

    # Pattern: "day after tomorrow"
    elif re.search(r"\bday after tomorrow\b", text_lower):
        extracted_date = today + timedelta(days=2)
        cleaned = re.sub(r"\bday after tomorrow\b", "", cleaned, flags=re.IGNORECASE)

    # Pattern: "next week"
    elif re.search(r"\bnext week\b", text_lower):
        extracted_date = today + timedelta(weeks=1)
        cleaned = re.sub(r"\bnext week\b", "", cleaned, flags=re.IGNORECASE)

    # Pattern: "next month"
    elif re.search(r"\bnext month\b", text_lower):
        extracted_date = today + relativedelta(months=1)
        cleaned = re.sub(r"\bnext month\b", "", cleaned, flags=re.IGNORECASE)

    # Pattern: "next [weekday]"
    else:
        weekday_match = re.search(r"\bnext\s+(monday|mon|tuesday|tue|tues|wednesday|wed|thursday|thu|thur|thurs|friday|fri|saturday|sat|sunday|sun)\b", text_lower)
        if weekday_match:
            day_name = weekday_match.group(1)
            weekday = WEEKDAY_MAP.get(day_name)
            if weekday:
                extracted_date = today + relativedelta(weekday=weekday(+1))
                cleaned = re.sub(r"\bnext\s+" + day_name + r"\b", "", cleaned, flags=re.IGNORECASE)

    # Pattern: "in X days/weeks/months"
    if not extracted_date:
        in_match = re.search(r"\bin\s+(\d+)\s+(days?|weeks?|months?)\b", text_lower)
        if in_match:
            amount = int(in_match.group(1))
            unit = in_match.group(2)
            if "day" in unit:
                extracted_date = today + timedelta(days=amount)
            elif "week" in unit:
                extracted_date = today + timedelta(weeks=amount)
            elif "month" in unit:
                extracted_date = today + relativedelta(months=amount)
            cleaned = re.sub(r"\bin\s+\d+\s+(days?|weeks?|months?)\b", "", cleaned, flags=re.IGNORECASE)

    # Pattern: "on [date]" - try to parse with dateutil
    if not extracted_date:
        on_match = re.search(r"\bon\s+([a-zA-Z0-9\s,]+?)(?:\s*$|\s+(?:at|by|for))", text_lower)
        if on_match:
            try:
                parsed = date_parser.parse(on_match.group(1), fuzzy=True)
                extracted_date = parsed.date()
                cleaned = re.sub(r"\bon\s+" + re.escape(on_match.group(1)), "", cleaned, flags=re.IGNORECASE)
            except (ValueError, TypeError):
                pass

    # Clean up extra whitespace
    cleaned = " ".join(cleaned.split())

    return extracted_date, cleaned.strip()


def extract_reminder(text: str) -> Tuple[Optional[datetime], str]:
    """
    Extract reminder time from text and return (reminder_datetime, cleaned_text).

    Supports:
    - "remind me at 3pm", "remind me at 15:00"
    - "remind me in 2 hours", "remind me in 30 minutes"
    - "reminder at 5pm", "reminder in 1 hour"
    """
    text_lower = text.lower()
    now = datetime.now()
    reminder_time = None
    cleaned = text

    # Pattern: "remind(er)? (me)? at [time]"
    at_time_match = re.search(r"remind(?:er)?\s*(?:me)?\s*at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text_lower)
    if at_time_match:
        hour = int(at_time_match.group(1))
        minute = int(at_time_match.group(2)) if at_time_match.group(2) else 0
        ampm = at_time_match.group(3)

        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        elif not ampm and hour < 8:  # Assume PM for small hours without am/pm
            hour += 12

        reminder_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if reminder_time <= now:
            reminder_time += timedelta(days=1)

        cleaned = re.sub(r"remind(?:er)?\s*(?:me)?\s*at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?", "", cleaned, flags=re.IGNORECASE)

    # Pattern: "remind(er)? (me)? in X hours/minutes"
    if not reminder_time:
        in_time_match = re.search(r"remind(?:er)?\s*(?:me)?\s*in\s+(\d+)\s*(hours?|minutes?|mins?|hrs?)", text_lower)
        if in_time_match:
            amount = int(in_time_match.group(1))
            unit = in_time_match.group(2)

            if "hour" in unit or "hr" in unit:
                reminder_time = now + timedelta(hours=amount)
            else:  # minutes
                reminder_time = now + timedelta(minutes=amount)

            cleaned = re.sub(r"remind(?:er)?\s*(?:me)?\s*in\s+\d+\s*(?:hours?|minutes?|mins?|hrs?)", "", cleaned, flags=re.IGNORECASE)

    # Pattern: just "remind me" without time - default to 1 hour
    if not reminder_time:
        simple_remind = re.search(r"\bremind(?:er)?\s*(?:me)?\b(?!\s*(?:at|in))", text_lower)
        if simple_remind:
            reminder_time = now + timedelta(hours=1)
            cleaned = re.sub(r"\bremind(?:er)?\s*(?:me)?\b", "", cleaned, flags=re.IGNORECASE)

    # Clean up "and" that might be left over
    cleaned = re.sub(r"\band\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = " ".join(cleaned.split())

    return reminder_time, cleaned.strip()


def parse_task_input(text: str) -> dict:
    """
    Parse a task input string and extract all components.

    Returns dict with:
    - title: cleaned task title
    - category: Personal or Business
    - priority: High, Medium, or Low
    - due_date: date object or None
    - reminder_time: datetime object or None
    """
    # Remove hashtags from the final title
    cleaned = re.sub(r"[#@](personal|business)\b", "", text, flags=re.IGNORECASE)

    # Remove command words from the beginning
    cleaned = re.sub(r"^(add|create|new|make|set|schedule)\s+", "", cleaned, flags=re.IGNORECASE)

    # Remove "remind me to/about" patterns (keep the actual task)
    cleaned = re.sub(r"^remind\s*(?:me)?\s*(?:to|about)?\s+", "", cleaned, flags=re.IGNORECASE)

    # Extract components
    priority, cleaned = extract_priority(cleaned)
    reminder_time, cleaned = extract_reminder(cleaned)
    due_date, cleaned = extract_date(cleaned)
    category = classify_task(text)  # Use original text for classification

    # Final cleanup - remove leftover "and" at start/end
    cleaned = re.sub(r"^\s*and\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+and\s*$", "", cleaned, flags=re.IGNORECASE)

    return {
        "title": cleaned.strip(),
        "category": category,
        "priority": priority,
        "due_date": due_date,
        "reminder_time": reminder_time
    }
