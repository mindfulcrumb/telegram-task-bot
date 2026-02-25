"""Research auto-update service — monitors podcast RSS feeds, summarizes new episodes."""
import logging
from datetime import datetime, timezone

import feedparser

from bot.db.database import get_cursor
from bot.services.knowledge_service import add_knowledge_entry

logger = logging.getLogger(__name__)

# RSS feeds to monitor
FEEDS = {
    "huberman": {
        "url": "https://feeds.megaphone.fm/hubaboratoryguest",
        "source": "huberman",
        "category": "longevity",
    },
    "attia": {
        "url": "https://peterattiamd.com/feed/podcast/",
        "source": "attia",
        "category": "longevity",
    },
    "doac": {
        "url": "https://feeds.megaphone.fm/diaryofaceo",
        "source": "doac",
        "category": "longevity",
    },
}


def _get_last_sync(source: str) -> str | None:
    """Get the last synced entry ID for a feed source."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT last_entry_id FROM knowledge_sync_log WHERE source = %s ORDER BY last_sync_at DESC LIMIT 1",
            (source,),
        )
        row = cur.fetchone()
        return row["last_entry_id"] if row else None


def _record_sync(source: str, feed_url: str, last_entry_id: str, entries_added: int):
    """Record a sync event."""
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO knowledge_sync_log (source, feed_url, last_entry_id, entries_added)
               VALUES (%s, %s, %s, %s)""",
            (source, feed_url, last_entry_id, entries_added),
        )


def _summarize_episode(title: str, description: str, source: str) -> dict | None:
    """Use Claude Haiku to extract health protocols from an episode description."""
    try:
        from bot.ai.brain_v2 import _call_api

        prompt = (
            f"Extract actionable health/fitness/longevity protocols from this podcast episode.\n\n"
            f"Title: {title}\n"
            f"Description: {description[:2000]}\n\n"
            f"Return a concise 2-4 sentence summary of the KEY actionable recommendations "
            f"(dosages, protocols, timing, mechanisms). Focus on what someone can actually DO. "
            f"If there are no health-related takeaways, respond with just 'SKIP'."
        )

        response, error = _call_api(
            "You are a health research assistant. Extract specific, actionable protocols from podcast descriptions. Be concise and factual.",
            [{"role": "user", "content": prompt}],
            max_tokens=300,
        )

        if error or not response or not response.content:
            return None

        text = response.content[0].text.strip()
        if text.upper() == "SKIP":
            return None

        # Infer topic from title
        title_lower = title.lower()
        topic = "general"
        topic_map = {
            "sleep": "sleep", "cold": "cold_exposure", "sauna": "heat_exposure",
            "fast": "fasting", "protein": "nutrition", "diet": "nutrition",
            "exercise": "fitness", "cardio": "fitness", "strength": "fitness",
            "peptide": "peptides", "hormone": "hormones", "testosterone": "hormones",
            "supplement": "supplements", "vitamin": "supplements",
            "dopamine": "neuroscience", "brain": "neuroscience", "focus": "neuroscience",
            "gut": "gut_health", "microbiome": "gut_health",
            "aging": "longevity", "longevity": "longevity", "lifespan": "longevity",
            "stress": "stress", "anxiety": "mental_health",
            "skin": "dermatology", "light": "light_exposure", "sun": "light_exposure",
        }
        for keyword, t in topic_map.items():
            if keyword in title_lower:
                topic = t
                break

        return {
            "topic": topic,
            "title": title,
            "content": text,
            "source": source,
            "source_episode": title,
            "evidence_level": "B",
        }

    except Exception as e:
        logger.error(f"Episode summarization failed: {e}")
        return None


def check_new_episodes() -> int:
    """Check all RSS feeds for new episodes and summarize them. Returns count of new entries."""
    total_added = 0

    for feed_name, config in FEEDS.items():
        try:
            source = config["source"]
            category = config["category"]
            feed_url = config["url"]

            last_id = _get_last_sync(source)
            feed = feedparser.parse(feed_url)

            if not feed.entries:
                logger.info(f"No entries in {feed_name} feed")
                continue

            new_entries = []
            latest_id = feed.entries[0].get("id") or feed.entries[0].get("link", "")

            if last_id == latest_id:
                logger.info(f"{feed_name}: no new episodes since last sync")
                continue

            # Collect new entries (up to 5 most recent)
            for entry in feed.entries[:5]:
                entry_id = entry.get("id") or entry.get("link", "")
                if entry_id == last_id:
                    break
                new_entries.append(entry)

            added = 0
            for entry in reversed(new_entries):  # Process oldest first
                title = entry.get("title", "Untitled")
                description = entry.get("summary") or entry.get("description") or ""

                summary = _summarize_episode(title, description, source)
                if summary:
                    summary["category"] = category
                    tags = [source, summary["topic"]]
                    add_knowledge_entry(
                        category=summary["category"],
                        topic=summary["topic"],
                        title=summary["title"],
                        content=summary["content"],
                        source=summary["source"],
                        source_episode=summary["source_episode"],
                        tags=tags,
                        evidence_level=summary["evidence_level"],
                    )
                    added += 1
                    logger.info(f"Added KB entry from {source}: {title}")

            _record_sync(source, feed_url, latest_id, added)
            total_added += added
            logger.info(f"{feed_name}: {added} new entries added ({len(new_entries)} episodes checked)")

        except Exception as e:
            logger.error(f"RSS feed check failed for {feed_name}: {e}")

    return total_added
