"""Research auto-update service — monitors podcast RSS feeds, PubMed, and ClinicalTrials.gov."""
import logging
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
from xml.etree import ElementTree

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


# ─── PubMed Research Crawler ─────────────────────────────────────────

# Peptide search terms for PubMed
PUBMED_SEARCH_TERMS = [
    "BPC-157", "TB-500 thymosin beta-4", "Ipamorelin", "CJC-1295",
    "GHK-Cu", "Semaglutide", "Tirzepatide", "Retatrutide",
    "KPV antimicrobial peptide", "Selank anxiolytic",
    "Epithalon telomere", "DSIP sleep peptide",
    "NAD+ longevity", "Rapamycin longevity",
    "peptide therapy clinical trial",
]


def _pubmed_search(query: str, max_results: int = 3) -> list:
    """Search PubMed via E-utilities API (free, no key required for low volume)."""
    results = []
    try:
        # Step 1: Search for article IDs
        search_url = (
            f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
            f"?db=pubmed&retmode=json&retmax={max_results}&sort=date"
            f"&term={urllib.parse.quote(query)}"
        )
        req = urllib.request.Request(search_url, headers={"User-Agent": "ZoeBot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        id_list = data.get("esearchresult", {}).get("idlist", [])
        if not id_list:
            return []

        # Step 2: Fetch article summaries
        ids_str = ",".join(id_list)
        summary_url = (
            f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
            f"?db=pubmed&retmode=json&id={ids_str}"
        )
        req = urllib.request.Request(summary_url, headers={"User-Agent": "ZoeBot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            summary_data = json.loads(resp.read())

        articles = summary_data.get("result", {})
        for pmid in id_list:
            article = articles.get(pmid, {})
            if not article or pmid == "uids":
                continue
            results.append({
                "pmid": pmid,
                "title": article.get("title", ""),
                "source": article.get("fulljournalname", article.get("source", "")),
                "pubdate": article.get("pubdate", ""),
                "authors": ", ".join(
                    a.get("name", "") for a in article.get("authors", [])[:3]
                ),
            })
    except Exception as e:
        logger.error(f"PubMed search failed for '{query}': {e}")

    return results


def check_pubmed_updates() -> int:
    """Check PubMed for new peptide research and add to knowledge base. Returns count added."""
    total_added = 0
    source_name = "pubmed"

    last_id = _get_last_sync(source_name)

    for term in PUBMED_SEARCH_TERMS:
        try:
            articles = _pubmed_search(term, max_results=2)
            if not articles:
                continue

            for article in articles:
                pmid = article["pmid"]

                # Skip if already synced (check by title in KB)
                with get_cursor() as cur:
                    cur.execute(
                        "SELECT id FROM knowledge_base WHERE source = %s AND source_episode LIKE %s LIMIT 1",
                        (source_name, f"%PMID:{pmid}%"),
                    )
                    if cur.fetchone():
                        continue

                # Summarize with Claude
                summary = _summarize_episode(
                    article["title"],
                    f"PubMed article: {article['title']}. Published in {article['source']} ({article['pubdate']}). Authors: {article['authors']}.",
                    source_name,
                )
                if summary:
                    add_knowledge_entry(
                        category="peptides" if "peptide" in term.lower() else "longevity",
                        topic=summary["topic"],
                        title=article["title"][:200],
                        content=summary["content"],
                        source=source_name,
                        source_episode=f"PMID:{pmid} — {article['source']}",
                        tags=[source_name, summary["topic"], "research"],
                        evidence_level="A",  # Peer-reviewed
                    )
                    total_added += 1
                    logger.info(f"Added PubMed article: PMID:{pmid}")

        except Exception as e:
            logger.error(f"PubMed crawl failed for term '{term}': {e}")

    if total_added > 0:
        _record_sync(source_name, "pubmed-eutils", f"batch-{datetime.now(timezone.utc).isoformat()}", total_added)
    logger.info(f"PubMed crawler: {total_added} new articles added")
    return total_added


# ─── ClinicalTrials.gov Crawler ──────────────────────────────────────

CLINICALTRIALS_TERMS = [
    "BPC-157", "TB-500", "semaglutide", "tirzepatide", "retatrutide",
    "survodutide", "ipamorelin", "CJC-1295", "GHK-Cu",
    "NAD+ supplementation", "rapamycin aging",
]


def _search_clinical_trials(query: str, max_results: int = 3) -> list:
    """Search ClinicalTrials.gov v2 API for active/recruiting studies."""
    results = []
    try:
        encoded_q = urllib.parse.quote(query)
        url = (
            f"https://clinicaltrials.gov/api/v2/studies"
            f"?query.term={encoded_q}&pageSize={max_results}"
            f"&filter.overallStatus=RECRUITING,ACTIVE_NOT_RECRUITING,ENROLLING_BY_INVITATION"
            f"&sort=LastUpdatePostDate"
            f"&format=json"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "ZoeBot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        studies = data.get("studies", [])
        for study in studies:
            proto = study.get("protocolSection", {})
            ident = proto.get("identificationModule", {})
            status_mod = proto.get("statusModule", {})
            design = proto.get("designModule", {})
            desc = proto.get("descriptionModule", {})

            nct_id = ident.get("nctId", "")
            title = ident.get("briefTitle", ident.get("officialTitle", ""))
            status = status_mod.get("overallStatus", "")
            phase_list = design.get("phases", [])
            phase = ", ".join(phase_list) if phase_list else "N/A"
            brief_summary = desc.get("briefSummary", "")

            results.append({
                "nct_id": nct_id,
                "title": title,
                "status": status,
                "phase": phase,
                "summary": brief_summary[:500],
            })
    except Exception as e:
        logger.error(f"ClinicalTrials.gov search failed for '{query}': {e}")

    return results


def check_clinical_trials() -> int:
    """Check ClinicalTrials.gov for new/updated peptide trials. Returns count added."""
    total_added = 0
    source_name = "clinicaltrials"

    for term in CLINICALTRIALS_TERMS:
        try:
            trials = _search_clinical_trials(term, max_results=2)
            if not trials:
                continue

            for trial in trials:
                nct_id = trial["nct_id"]

                # Skip if already in KB
                with get_cursor() as cur:
                    cur.execute(
                        "SELECT id FROM knowledge_base WHERE source = %s AND source_episode LIKE %s LIMIT 1",
                        (source_name, f"%{nct_id}%"),
                    )
                    if cur.fetchone():
                        continue

                content = (
                    f"Phase: {trial['phase']}. Status: {trial['status']}. "
                    f"{trial['summary']}"
                )

                # Infer topic
                title_lower = trial["title"].lower()
                topic = "peptides"
                if any(w in title_lower for w in ["weight", "obesity", "bmi", "fat"]):
                    topic = "weight_management"
                elif any(w in title_lower for w in ["aging", "longevity", "senescence"]):
                    topic = "longevity"
                elif any(w in title_lower for w in ["diabetes", "glucose", "insulin"]):
                    topic = "metabolic"
                elif any(w in title_lower for w in ["cognitive", "brain", "neuro"]):
                    topic = "neuroscience"

                add_knowledge_entry(
                    category="longevity",
                    topic=topic,
                    title=f"[Clinical Trial] {trial['title'][:180]}",
                    content=content[:1000],
                    source=source_name,
                    source_episode=f"{nct_id} — clinicaltrials.gov",
                    tags=[source_name, topic, "clinical_trial", trial["phase"]],
                    evidence_level="A",
                )
                total_added += 1
                logger.info(f"Added clinical trial: {nct_id}")

        except Exception as e:
            logger.error(f"ClinicalTrials.gov crawl failed for term '{term}': {e}")

    if total_added > 0:
        _record_sync(source_name, "clinicaltrials-v2", f"batch-{datetime.now(timezone.utc).isoformat()}", total_added)
    logger.info(f"ClinicalTrials.gov crawler: {total_added} new trials added")
    return total_added


# ─── Deep Content Extraction Wrappers ────────────────────────────────

def check_youtube_transcripts() -> int:
    """Check YouTube channels for new episodes and extract deep protocols.
    Called by research_update_job on Mondays. Max 3 new episodes per channel per run."""
    from bot.services.content_extractor import process_youtube_channel
    total = 0
    for channel_key in ["huberman", "attia", "doac"]:
        try:
            added = process_youtube_channel(channel_key, max_videos=3)
            total += added
        except Exception as e:
            logger.error(f"YouTube transcript crawler failed for {channel_key}: {e}")
    return total


def check_pubmed_full_abstracts() -> int:
    """Enhanced PubMed crawler using efetch for full abstracts."""
    from bot.services.content_extractor import process_pubmed_deep
    try:
        return process_pubmed_deep(max_per_term=2)
    except Exception as e:
        logger.error(f"PubMed full abstract crawler failed: {e}")
        return 0


def check_jay_campbell() -> int:
    """Check Jay Campbell's RSS feed for new articles."""
    from bot.services.content_extractor import process_rss_articles
    try:
        return process_rss_articles(
            "https://jaycampbell.com/feed/", "jay_campbell", max_articles=5
        )
    except Exception as e:
        logger.error(f"Jay Campbell crawler failed: {e}")
        return 0
