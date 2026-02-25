"""Deep content extraction pipeline — YouTube transcripts, PubMed abstracts, RSS articles."""
import json
import logging
import re
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from xml.etree import ElementTree

import feedparser

from bot.db.database import get_cursor
from bot.services.knowledge_service import add_knowledge_entry

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────

CHUNK_SIZE = 4000  # chars per chunk for Haiku input
MAX_CHUNKS_PER_ITEM = 20  # safety limit
HAIKU_DELAY = 0.5  # seconds between Haiku calls
PUBMED_DELAY = 0.4  # seconds between PubMed requests
RSS_DELAY = 1.0  # seconds between RSS fetches

YOUTUBE_CHANNELS = {
    "huberman": {
        "channel_id": "UC2D2CMWXMOVWx7giW1n3LIg",
        "source": "huberman",
        "category": "longevity",
    },
    "attia": {
        "channel_id": "UC8kGsMa0LygSX9nkBcBH1Sg",
        "source": "attia",
        "category": "longevity",
    },
    "doac": {
        "channel_id": "UCGq-a57w-aPwyi3pW7XLiHw",
        "source": "doac",
        "category": "longevity",
    },
}

PRIORITY_KEYWORDS = [
    "peptide", "bpc", "tb-500", "thymosin", "semaglutide", "tirzepatide",
    "growth hormone", "ipamorelin", "cjc", "nad", "nmn", "longevity",
    "sleep protocol", "cold exposure", "sauna", "supplement", "hormone",
    "testosterone", "fasting", "rapamycin", "metformin", "exercise protocol",
    "zone 2", "vo2", "protein", "muscle", "recovery", "gut", "microbiome",
    "dopamine", "focus", "stress", "light exposure", "circadian",
    "ghk", "epithalon", "selank", "semax", "dsip", "kpv",
]

# PubMed search terms (reuse from research_service but can be extended)
PUBMED_SEARCH_TERMS = [
    "BPC-157", "TB-500 thymosin beta-4", "Ipamorelin", "CJC-1295",
    "GHK-Cu", "Semaglutide", "Tirzepatide", "Retatrutide",
    "KPV antimicrobial peptide", "Selank anxiolytic",
    "Epithalon telomere", "DSIP sleep peptide",
    "NAD+ longevity", "Rapamycin longevity",
    "peptide therapy clinical trial",
]

VALID_TOPICS = [
    "sleep", "cold_exposure", "heat_exposure", "supplements", "peptides",
    "hormones", "fitness", "nutrition", "longevity", "neuroscience",
    "gut_health", "metabolic", "stress", "mental_health", "light_exposure",
    "fasting", "recovery", "dermatology", "weight_management", "general",
]

VALID_CATEGORIES = [
    "longevity", "sleep", "fitness", "nutrition", "supplements",
    "hormones", "neuroscience", "cardiovascular", "metabolic",
    "recovery", "gut_health", "mental_health", "peptides",
]

# ─── Haiku Summarization ─────────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = (
    "You are a health research extraction engine. Extract specific, actionable health protocols "
    "from expert content.\n\n"
    "Rules:\n"
    "1. Extract ONLY actionable protocols (dosages, timing, duration, specific recommendations)\n"
    "2. Each protocol: 200-400 words (1500-3000 characters)\n"
    "3. Include expert name, mechanism of action, evidence basis\n"
    "4. Return results as a JSON array\n"
    "5. If no actionable health content, return []\n"
    "6. Be specific: '500mcg BPC-157 subQ twice daily for 6 weeks' not 'take BPC-157'\n"
    "7. Include caveats and safety notes where relevant"
)


def _extract_protocols_from_chunk(chunk_text: str, context: dict) -> list[dict]:
    """Use Claude Haiku to extract actionable protocols from a content chunk.

    Returns list of protocol dicts or empty list.
    """
    try:
        from bot.ai.brain_v2 import _call_api

        topics_str = ", ".join(VALID_TOPICS)
        categories_str = ", ".join(VALID_CATEGORIES)

        user_prompt = (
            f"Extract actionable health/fitness/longevity protocols from this content.\n\n"
            f"Source: {context.get('source', 'unknown')}\n"
            f"Episode/Article: {context.get('title', 'unknown')}\n"
            f"Chunk {context.get('chunk_index', 1)} of {context.get('total_chunks', 1)}\n\n"
            f"---\n{chunk_text}\n---\n\n"
            f"Return a JSON array where each element has:\n"
            f'{{\n'
            f'  "title": "Protocol Name (concise, searchable)",\n'
            f'  "content": "Detailed protocol (200-400 words with dosages, timing, mechanisms, evidence, caveats)",\n'
            f'  "topic": "one of: {topics_str}",\n'
            f'  "category": "one of: {categories_str}",\n'
            f'  "evidence_level": "A (strong clinical) / B (moderate) / C (emerging)",\n'
            f'  "tags": ["keyword1", "keyword2", "keyword3"]\n'
            f'}}\n\n'
            f"Return [] if no actionable health content."
        )

        response, error = _call_api(
            EXTRACTION_SYSTEM_PROMPT,
            [{"role": "user", "content": user_prompt}],
            max_tokens=2000,
        )

        if error or not response or not response.content:
            logger.warning(f"Haiku extraction failed: {error}")
            return []

        text = response.content[0].text.strip()

        # Parse JSON from response (handle markdown code blocks)
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        if not text or text == "[]":
            return []

        protocols = json.loads(text)
        if not isinstance(protocols, list):
            return []

        # Validate and clean each protocol
        valid = []
        for p in protocols:
            if not isinstance(p, dict):
                continue
            if not p.get("title") or not p.get("content"):
                continue
            # Ensure topic/category are valid
            if p.get("topic") not in VALID_TOPICS:
                p["topic"] = "general"
            if p.get("category") not in VALID_CATEGORIES:
                p["category"] = "longevity"
            if p.get("evidence_level") not in ("A", "B", "C"):
                p["evidence_level"] = "B"
            if not isinstance(p.get("tags"), list):
                p["tags"] = []
            valid.append(p)

        return valid

    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error in extraction: {e}")
        return []
    except Exception as e:
        logger.error(f"Protocol extraction failed: {e}")
        return []


# ─── Text Chunking ───────────────────────────────────────────────────

def _chunk_transcript(segments: list[dict], chunk_duration_minutes: int = 12) -> list[str]:
    """Group YouTube transcript segments into time-based chunks.

    segments: list of {text, start, duration} from youtube_transcript_api.
    Returns list of text strings.
    """
    if not segments:
        return []

    chunk_duration_secs = chunk_duration_minutes * 60
    chunks = []
    current_chunk = []
    chunk_start = 0

    for seg in segments:
        start = seg.get("start", 0)
        text = seg.get("text", "").strip()
        if not text:
            continue

        if start - chunk_start >= chunk_duration_secs and current_chunk:
            chunks.append(" ".join(current_chunk))
            current_chunk = []
            chunk_start = start

        current_chunk.append(text)

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks[:MAX_CHUNKS_PER_ITEM]


def _chunk_text(text: str, max_chars: int = CHUNK_SIZE) -> list[str]:
    """Split text into chunks at sentence boundaries."""
    if not text or len(text) <= max_chars:
        return [text] if text else []

    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current = []
    current_len = 0

    for sentence in sentences:
        if current_len + len(sentence) + 1 > max_chars and current:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
        current.append(sentence)
        current_len += len(sentence) + 1

    if current:
        chunks.append(" ".join(current))

    return chunks[:MAX_CHUNKS_PER_ITEM]


# ─── YouTube Transcript Extraction ───────────────────────────────────

def fetch_youtube_transcript(video_id: str) -> list[dict] | None:
    """Fetch transcript for a YouTube video. Returns list of {text, start, duration} or None."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        return transcript
    except Exception as e:
        logger.debug(f"Transcript unavailable for {video_id}: {e}")
        return None


def get_channel_video_ids(channel_id: str, max_results: int = 50) -> list[dict]:
    """Get video IDs and titles from a YouTube channel RSS feed (free, no API key).

    Returns [{video_id, title, published}].
    """
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        feed = feedparser.parse(feed_url)
        results = []
        for entry in feed.entries[:max_results]:
            video_id = entry.get("yt_videoid", "")
            if not video_id:
                # Extract from link
                link = entry.get("link", "")
                if "v=" in link:
                    video_id = link.split("v=")[1].split("&")[0]
            if video_id:
                results.append({
                    "video_id": video_id,
                    "title": entry.get("title", "Untitled"),
                    "published": entry.get("published", ""),
                })
        return results
    except Exception as e:
        logger.error(f"Channel RSS fetch failed for {channel_id}: {e}")
        return []


def _is_priority_video(title: str, keywords: list = None) -> bool:
    """Check if video title matches priority health/protocol keywords."""
    keywords = keywords or PRIORITY_KEYWORDS
    title_lower = title.lower()
    return any(kw in title_lower for kw in keywords)


# ─── Processing Log Helpers ──────────────────────────────────────────

def _is_already_processed(source: str, source_id: str) -> bool:
    """Check if a content item has already been processed."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT status FROM content_processing_log WHERE source = %s AND source_id = %s",
            (source, source_id),
        )
        row = cur.fetchone()
        return row is not None and row["status"] == "completed"


def _log_processing_start(source: str, source_id: str, title: str, chunks_total: int):
    """Record the start of content processing."""
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO content_processing_log (source, source_id, source_title, status, chunks_total)
               VALUES (%s, %s, %s, 'processing', %s)
               ON CONFLICT (source, source_id) DO UPDATE SET
                   status = 'processing', chunks_total = EXCLUDED.chunks_total,
                   error_message = NULL""",
            (source, source_id, title[:500], chunks_total),
        )


def _log_chunk_progress(source: str, source_id: str, chunks_done: int):
    """Update chunk progress for a content item."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE content_processing_log SET chunks_processed = %s WHERE source = %s AND source_id = %s",
            (chunks_done, source, source_id),
        )


def _log_processing_complete(source: str, source_id: str, entries_created: int):
    """Mark content processing as complete."""
    with get_cursor() as cur:
        cur.execute(
            """UPDATE content_processing_log
               SET status = 'completed', entries_created = %s, processed_at = NOW()
               WHERE source = %s AND source_id = %s""",
            (entries_created, source, source_id),
        )


def _log_processing_failed(source: str, source_id: str, error: str):
    """Mark content processing as failed."""
    with get_cursor() as cur:
        cur.execute(
            """UPDATE content_processing_log
               SET status = 'failed', error_message = %s, processed_at = NOW()
               WHERE source = %s AND source_id = %s""",
            (error[:1000], source, source_id),
        )


# ─── Deduplication ───────────────────────────────────────────────────

def _is_duplicate_kb_entry(title: str, source: str) -> bool:
    """Check if a similar KB entry already exists."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT id FROM knowledge_base WHERE source = %s AND title ILIKE %s LIMIT 1",
            (source, f"%{title[:100]}%"),
        )
        return cur.fetchone() is not None


# ─── YouTube Channel Pipeline ────────────────────────────────────────

def process_youtube_channel(channel_key: str, max_videos: int = 20,
                            priority_keywords: list = None) -> int:
    """Process a YouTube channel's videos into KB entries. Returns count of entries created."""
    config = YOUTUBE_CHANNELS.get(channel_key)
    if not config:
        logger.error(f"Unknown channel key: {channel_key}")
        return 0

    channel_id = config["channel_id"]
    source = config["source"]
    category = config["category"]
    total_entries = 0

    # Get recent videos
    videos = get_channel_video_ids(channel_id, max_results=max_videos * 2)
    if not videos:
        logger.info(f"No videos found for {channel_key}")
        return 0

    # Filter by priority if keywords provided
    if priority_keywords:
        priority = [v for v in videos if _is_priority_video(v["title"], priority_keywords)]
        other = [v for v in videos if not _is_priority_video(v["title"], priority_keywords)]
        videos = (priority + other)[:max_videos]
    else:
        videos = videos[:max_videos]

    for video in videos:
        vid = video["video_id"]
        title = video["title"]

        if _is_already_processed("youtube", vid):
            continue

        try:
            # Fetch transcript
            segments = fetch_youtube_transcript(vid)
            if not segments:
                _log_processing_start("youtube", vid, title, 0)
                _log_processing_complete("youtube", vid, 0)
                continue

            # Chunk transcript
            chunks = _chunk_transcript(segments)
            if not chunks:
                continue

            _log_processing_start("youtube", vid, title, len(chunks))

            entries_created = 0
            for i, chunk in enumerate(chunks):
                context = {
                    "source": source,
                    "title": title,
                    "chunk_index": i + 1,
                    "total_chunks": len(chunks),
                }

                protocols = _extract_protocols_from_chunk(chunk, context)
                for proto in protocols:
                    if _is_duplicate_kb_entry(proto["title"], source):
                        continue

                    tags = proto.get("tags", []) + [source, proto["topic"]]
                    add_knowledge_entry(
                        category=proto.get("category", category),
                        topic=proto["topic"],
                        title=proto["title"][:200],
                        content=proto["content"][:3000],
                        source=source,
                        source_episode=title,
                        tags=list(set(tags)),
                        evidence_level=proto.get("evidence_level", "B"),
                    )
                    entries_created += 1

                _log_chunk_progress("youtube", vid, i + 1)
                time.sleep(HAIKU_DELAY)

            _log_processing_complete("youtube", vid, entries_created)
            total_entries += entries_created
            logger.info(f"YouTube [{source}] '{title}': {entries_created} KB entries")

        except Exception as e:
            _log_processing_failed("youtube", vid, str(e))
            logger.error(f"YouTube processing failed for {vid}: {e}")

    return total_entries


# ─── PubMed Full Abstracts ───────────────────────────────────────────

def fetch_pubmed_abstract(pmid: str) -> dict | None:
    """Fetch full abstract via PubMed efetch API (XML).

    Returns {pmid, title, abstract, authors, journal, pubdate} or None.
    """
    try:
        url = (
            f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            f"?db=pubmed&id={pmid}&retmode=xml&rettype=abstract"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "ZoeBot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml_data = resp.read()

        root = ElementTree.fromstring(xml_data)
        article = root.find(".//PubmedArticle")
        if article is None:
            return None

        # Title
        title_el = article.find(".//ArticleTitle")
        title = title_el.text if title_el is not None and title_el.text else ""

        # Abstract — may have multiple labeled sections
        abstract_parts = []
        for abs_text in article.findall(".//AbstractText"):
            label = abs_text.get("Label", "")
            text = abs_text.text or ""
            if label:
                abstract_parts.append(f"{label}: {text}")
            else:
                abstract_parts.append(text)
        abstract = " ".join(abstract_parts)

        # Authors (first 3)
        authors = []
        for author in article.findall(".//Author")[:3]:
            last = author.findtext("LastName", "")
            first = author.findtext("ForeName", "")
            if last:
                authors.append(f"{last} {first}".strip())

        # Journal
        journal = article.findtext(".//Journal/Title", "")

        # Date
        pubdate = article.findtext(".//PubDate/Year", "")
        month = article.findtext(".//PubDate/Month", "")
        if month:
            pubdate = f"{pubdate} {month}"

        return {
            "pmid": pmid,
            "title": title,
            "abstract": abstract,
            "authors": ", ".join(authors),
            "journal": journal,
            "pubdate": pubdate,
        }

    except Exception as e:
        logger.error(f"PubMed efetch failed for PMID:{pmid}: {e}")
        return None


def _pubmed_search_ids(query: str, max_results: int = 5) -> list[str]:
    """Search PubMed and return PMIDs."""
    try:
        search_url = (
            f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
            f"?db=pubmed&retmode=json&retmax={max_results}&sort=date"
            f"&term={urllib.parse.quote(query)}"
        )
        req = urllib.request.Request(search_url, headers={"User-Agent": "ZoeBot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return data.get("esearchresult", {}).get("idlist", [])
    except Exception as e:
        logger.error(f"PubMed search failed for '{query}': {e}")
        return []


def process_pubmed_deep(search_terms: list = None, max_per_term: int = 3) -> int:
    """Enhanced PubMed crawler using efetch for full abstracts. Returns entries created."""
    terms = search_terms or PUBMED_SEARCH_TERMS
    total_entries = 0

    for term in terms:
        try:
            pmids = _pubmed_search_ids(term, max_results=max_per_term)
            time.sleep(PUBMED_DELAY)

            for pmid in pmids:
                if _is_already_processed("pubmed_deep", pmid):
                    continue

                article = fetch_pubmed_abstract(pmid)
                time.sleep(PUBMED_DELAY)

                if not article or not article["abstract"]:
                    _log_processing_start("pubmed_deep", pmid, article["title"] if article else term, 0)
                    _log_processing_complete("pubmed_deep", pmid, 0)
                    continue

                _log_processing_start("pubmed_deep", pmid, article["title"], 1)

                # Build rich content for extraction
                content_for_extraction = (
                    f"Title: {article['title']}\n"
                    f"Journal: {article['journal']} ({article['pubdate']})\n"
                    f"Authors: {article['authors']}\n\n"
                    f"Abstract: {article['abstract']}"
                )

                context = {
                    "source": "pubmed",
                    "title": article["title"],
                    "chunk_index": 1,
                    "total_chunks": 1,
                }

                protocols = _extract_protocols_from_chunk(content_for_extraction, context)
                time.sleep(HAIKU_DELAY)

                entries_created = 0
                for proto in protocols:
                    if _is_duplicate_kb_entry(proto["title"], "pubmed"):
                        continue

                    # Determine category
                    cat = "peptides" if "peptide" in term.lower() else proto.get("category", "longevity")

                    tags = proto.get("tags", []) + ["pubmed", "research", proto["topic"]]
                    add_knowledge_entry(
                        category=cat,
                        topic=proto["topic"],
                        title=proto["title"][:200],
                        content=proto["content"][:3000],
                        source="pubmed",
                        source_episode=f"PMID:{pmid} — {article['journal']}",
                        tags=list(set(tags)),
                        evidence_level="A",  # Peer-reviewed
                    )
                    entries_created += 1

                _log_processing_complete("pubmed_deep", pmid, entries_created)
                total_entries += entries_created
                if entries_created:
                    logger.info(f"PubMed PMID:{pmid}: {entries_created} KB entries")

        except Exception as e:
            logger.error(f"PubMed deep crawl failed for '{term}': {e}")

    logger.info(f"PubMed deep crawler: {total_entries} new entries")
    return total_entries


# ─── RSS Article Extraction ──────────────────────────────────────────

def _strip_html(html: str) -> str:
    """Simple HTML tag stripping for RSS article content."""
    # Remove script/style blocks
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Decode common entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def process_rss_articles(feed_url: str, source: str, max_articles: int = 20) -> int:
    """Process full articles from RSS feed into KB entries. Returns entries created."""
    total_entries = 0

    try:
        feed = feedparser.parse(feed_url)
    except Exception as e:
        logger.error(f"RSS feed fetch failed for {feed_url}: {e}")
        return 0

    if not feed.entries:
        return 0

    for entry in feed.entries[:max_articles]:
        link = entry.get("link", entry.get("id", ""))
        title = entry.get("title", "Untitled")

        if not link or _is_already_processed("rss_article", link):
            continue

        # Get full content (RSS feeds often include it in content:encoded or summary)
        content_html = ""
        if hasattr(entry, "content") and entry.content:
            content_html = entry.content[0].get("value", "")
        if not content_html:
            content_html = entry.get("summary", entry.get("description", ""))

        if not content_html:
            continue

        article_text = _strip_html(content_html)
        if len(article_text) < 200:  # Too short to be useful
            continue

        chunks = _chunk_text(article_text)
        _log_processing_start("rss_article", link, title, len(chunks))

        entries_created = 0
        for i, chunk in enumerate(chunks):
            context = {
                "source": source,
                "title": title,
                "chunk_index": i + 1,
                "total_chunks": len(chunks),
            }

            protocols = _extract_protocols_from_chunk(chunk, context)
            for proto in protocols:
                if _is_duplicate_kb_entry(proto["title"], source):
                    continue

                tags = proto.get("tags", []) + [source, proto["topic"]]
                add_knowledge_entry(
                    category=proto.get("category", "longevity"),
                    topic=proto["topic"],
                    title=proto["title"][:200],
                    content=proto["content"][:3000],
                    source=source,
                    source_episode=title,
                    tags=list(set(tags)),
                    evidence_level=proto.get("evidence_level", "B"),
                )
                entries_created += 1

            _log_chunk_progress("rss_article", link, i + 1)
            time.sleep(HAIKU_DELAY)

        _log_processing_complete("rss_article", link, entries_created)
        total_entries += entries_created
        if entries_created:
            logger.info(f"RSS [{source}] '{title}': {entries_created} KB entries")
        time.sleep(RSS_DELAY)

    logger.info(f"RSS [{source}] crawler: {total_entries} new entries")
    return total_entries


# ─── Manual Document Processing ──────────────────────────────────────

def process_manual_document(text: str, source: str, document_title: str) -> int:
    """Process a user-provided research document into KB entries. Returns entries created."""
    if not text:
        return 0

    chunks = _chunk_text(text)
    doc_id = f"doc-{hash(text[:500]) & 0xFFFFFFFF:08x}"

    if _is_already_processed("manual", doc_id):
        logger.info(f"Document already processed: {document_title}")
        return 0

    _log_processing_start("manual", doc_id, document_title, len(chunks))
    total_entries = 0

    for i, chunk in enumerate(chunks):
        context = {
            "source": source,
            "title": document_title,
            "chunk_index": i + 1,
            "total_chunks": len(chunks),
        }

        protocols = _extract_protocols_from_chunk(chunk, context)
        for proto in protocols:
            if _is_duplicate_kb_entry(proto["title"], source):
                continue

            tags = proto.get("tags", []) + [source, proto["topic"]]
            add_knowledge_entry(
                category=proto.get("category", "longevity"),
                topic=proto["topic"],
                title=proto["title"][:200],
                content=proto["content"][:3000],
                source=source,
                source_episode=document_title,
                tags=list(set(tags)),
                evidence_level=proto.get("evidence_level", "B"),
            )
            total_entries += 1

        _log_chunk_progress("manual", doc_id, i + 1)
        time.sleep(HAIKU_DELAY)

    _log_processing_complete("manual", doc_id, total_entries)
    logger.info(f"Document '{document_title}': {total_entries} KB entries")
    return total_entries
