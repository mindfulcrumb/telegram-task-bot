"""URL content extraction and summary storage."""
import asyncio
import logging
import re
from html import unescape
from urllib.parse import urlparse, parse_qs

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Content-type classification (keyword-based, zero LLM cost)
# ---------------------------------------------------------------------------

_RECIPE_KEYWORDS = {
    "ingredients", "tablespoon", "teaspoon", "cups", "preheat", "bake",
    "recipe", "calories", "servings", "prep time", "cook time", "nutrition",
    "oven", "whisk", "stir", "simmer", "marinate", "dice", "mince",
}
_WORKOUT_KEYWORDS = {
    "sets", "reps", "exercise", "superset", "rest period", "workout",
    "training", "warm up", "cooldown", "squat", "deadlift", "bench press",
    "hiit", "circuit", "amrap", "emom", "wod",
}
_EVENT_KEYWORDS = {
    "register", "tickets", "rsvp", "location", "venue", "event date",
    "join us", "sign up", "admission", "conference", "meetup", "workshop",
    "race", "marathon", "5k", "10k",
}
_PRODUCT_KEYWORDS = {
    "add to cart", "buy now", "shop", "price", "checkout", "shipping",
    "in stock", "out of stock", "discount", "coupon", "order now",
}
_SOCIAL_HOSTS = {
    "instagram.com", "twitter.com", "x.com", "reddit.com", "threads.net",
    "tiktok.com", "facebook.com", "linkedin.com",
}
_VIDEO_HOSTS = {
    "youtube.com", "youtu.be", "vimeo.com", "tiktok.com", "twitch.tv",
}


def classify_url(url: str, title: str = "", text: str = "") -> str:
    """Classify a URL into content type using domain + keyword matching."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    # Domain-based classification first
    if any(vh in host for vh in _VIDEO_HOSTS):
        return "video"
    if any(sh in host for sh in _SOCIAL_HOSTS):
        return "social"
    if url.lower().endswith(".pdf"):
        return "pdf"

    # Keyword-based classification on content
    combined = f"{title} {text}".lower()
    scores = {
        "recipe": sum(1 for kw in _RECIPE_KEYWORDS if kw in combined),
        "workout": sum(1 for kw in _WORKOUT_KEYWORDS if kw in combined),
        "event": sum(1 for kw in _EVENT_KEYWORDS if kw in combined),
        "product": sum(1 for kw in _PRODUCT_KEYWORDS if kw in combined),
    }
    best = max(scores, key=scores.get)
    if scores[best] >= 2:  # Need at least 2 keyword hits
        return best

    return "article"


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------

def _strip_html(html: str) -> str:
    """Remove HTML tags, scripts, styles. Keep readable text."""
    # Remove script, style, nav, footer, header blocks
    for tag in ("script", "style", "nav", "footer", "header", "aside"):
        html = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Decode HTML entities
    text = unescape(text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_title(html: str) -> str:
    """Extract <title> from HTML."""
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if match:
        return unescape(match.group(1)).strip()
    # Try og:title
    match = re.search(r'property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.IGNORECASE)
    if match:
        return unescape(match.group(1)).strip()
    return ""


def extract_content(url: str, max_chars: int = 8000) -> tuple[str, str, str]:
    """Extract content from a URL. Returns (content_type, text, title).

    This is synchronous — call via asyncio.to_thread() from async code.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    # YouTube
    if any(vh in host for vh in ("youtube.com", "youtu.be")):
        text = _extract_youtube(url, max_chars)
        title = _extract_youtube_title(url)
        content_type = classify_url(url, title, text)
        return content_type, text, title

    # PDF
    if url.lower().endswith(".pdf"):
        text = _extract_pdf(url, max_chars)
        return "pdf", text, ""

    # Everything else — try trafilatura first, httpx fallback
    title, text = _extract_article(url, max_chars)
    content_type = classify_url(url, title, text)
    return content_type, text, title


def _extract_article(url: str, max_chars: int) -> tuple[str, str]:
    """Extract article text. Trafilatura first, httpx+regex fallback.

    Returns (title, text).
    """
    title = ""
    text = ""

    # Try trafilatura
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            title = _extract_title(downloaded)
            text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
            text = (text or "")[:max_chars]
            if text:
                return title, text
    except Exception as e:
        logger.warning(f"Trafilatura failed for {url}: {type(e).__name__}: {e}")

    # Fallback: httpx + HTML stripping
    try:
        import httpx
        resp = httpx.get(url, timeout=10.0, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; ZoeBot/1.0)"
        })
        if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
            html = resp.text
            title = title or _extract_title(html)
            text = _strip_html(html)[:max_chars]
            return title, text
    except Exception as e:
        logger.warning(f"httpx fallback failed for {url}: {type(e).__name__}: {e}")

    return title, text


def _extract_youtube_title(url: str) -> str:
    """Get YouTube video title via noembed (free, no API key)."""
    try:
        import httpx
        resp = httpx.get(
            f"https://noembed.com/embed?url={url}",
            timeout=5.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("title", "")
    except Exception:
        pass
    return ""


def _extract_youtube(url: str, max_chars: int) -> str:
    """Extract YouTube transcript."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        # Extract video ID
        parsed = urlparse(url)
        if "youtu.be" in (parsed.hostname or ""):
            video_id = parsed.path.lstrip("/")
        else:
            video_id = parse_qs(parsed.query).get("v", [""])[0]

        if not video_id:
            return ""

        ytt_api = YouTubeTranscriptApi()
        transcript = ytt_api.fetch(video_id)
        text = " ".join(entry.text for entry in transcript.snippets)
        return text[:max_chars]
    except Exception as e:
        logger.error(f"YouTube extraction failed for {url}: {type(e).__name__}: {e}")
        return ""


def _extract_pdf(url: str, max_chars: int) -> str:
    """Extract text from a PDF URL."""
    try:
        import httpx
        from pypdf import PdfReader
        import io

        resp = httpx.get(url, timeout=20.0, follow_redirects=True)
        if resp.status_code != 200:
            return ""

        reader = PdfReader(io.BytesIO(resp.content))
        pages = reader.pages[:15]  # Cap at 15 pages
        text = "\n".join(page.extract_text() or "" for page in pages)
        return text[:max_chars]
    except Exception as e:
        logger.error(f"PDF extraction failed for {url}: {type(e).__name__}: {e}")
        return ""


# ---------------------------------------------------------------------------
# Async wrapper for use from handlers
# ---------------------------------------------------------------------------

async def async_extract_content(url: str, max_chars: int = 8000) -> tuple[str, str, str]:
    """Async wrapper — runs extract_content in a thread to avoid blocking."""
    return await asyncio.to_thread(extract_content, url, max_chars)


# ---------------------------------------------------------------------------
# DB operations
# ---------------------------------------------------------------------------

def save_url_summary(
    user_id: int, url: str, title: str, summary: str, content_type: str
) -> None:
    """Save a URL summary for later recall."""
    domain = urlparse(url).hostname or ""
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO url_summaries (user_id, url, domain, title, summary, content_type)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (user_id, url, domain, title, summary, content_type),
        )


def search_saved_urls(user_id: int, query: str, limit: int = 5) -> list[dict]:
    """Search saved URL summaries by keyword."""
    with get_cursor() as cur:
        # Search in title, summary, domain, and url
        pattern = f"%{query.lower()}%"
        cur.execute(
            """SELECT url, domain, title, summary, content_type, created_at
               FROM url_summaries
               WHERE user_id = %s
                 AND (LOWER(title) LIKE %s OR LOWER(summary) LIKE %s
                      OR LOWER(domain) LIKE %s OR LOWER(url) LIKE %s)
               ORDER BY created_at DESC
               LIMIT %s""",
            (user_id, pattern, pattern, pattern, pattern, limit),
        )
        rows = cur.fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if hasattr(d.get("created_at"), "isoformat"):
                d["created_at"] = d["created_at"].isoformat()
            results.append(d)
        return results


def extract_urls_from_entities(message) -> list[str]:
    """Extract URLs from Telegram message entities."""
    urls = []
    if not message or not message.entities:
        return urls

    text = message.text or ""
    for entity in message.entities:
        if entity.type == "url":
            url = text[entity.offset:entity.offset + entity.length]
            if not url.startswith("http"):
                url = "https://" + url
            urls.append(url)
        elif entity.type == "text_link" and entity.url:
            urls.append(entity.url)

    return urls
