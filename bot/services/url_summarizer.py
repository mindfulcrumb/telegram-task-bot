"""URL content extraction and summary storage."""
import logging
import re
from urllib.parse import urlparse

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)


def classify_url(url: str) -> str:
    """Classify a URL into content type."""
    parsed = urlparse(url)
    host = parsed.hostname or ""

    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if url.lower().endswith(".pdf"):
        return "pdf"
    return "article"


def extract_content(url: str, max_chars: int = 8000) -> tuple[str, str]:
    """Extract content from a URL. Returns (content_type, text)."""
    content_type = classify_url(url)

    if content_type == "youtube":
        return content_type, _extract_youtube(url, max_chars)
    elif content_type == "pdf":
        return content_type, _extract_pdf(url, max_chars)
    else:
        return content_type, _extract_article(url, max_chars)


def _extract_article(url: str, max_chars: int) -> str:
    """Extract article text using trafilatura."""
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return ""
        text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
        return (text or "")[:max_chars]
    except Exception as e:
        logger.error(f"Article extraction failed for {url}: {type(e).__name__}: {e}")
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
            from urllib.parse import parse_qs
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
