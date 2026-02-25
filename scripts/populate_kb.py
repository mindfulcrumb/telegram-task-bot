"""One-time knowledge base population script.

Extracts deep content from YouTube transcripts, PubMed abstracts,
and RSS articles, then loads actionable protocol entries into the KB.

Usage:
    python -m scripts.populate_kb youtube [--limit 20] [--dry-run]
    python -m scripts.populate_kb pubmed [--limit 30]
    python -m scripts.populate_kb jaycampbell [--limit 20]
    python -m scripts.populate_kb all [--limit 20]
"""
import argparse
import logging
import os
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Populate Zoe's knowledge base with deep content")
    parser.add_argument(
        "source",
        choices=["youtube", "pubmed", "jaycampbell", "all"],
        help="Content source to process",
    )
    parser.add_argument("--limit", type=int, default=20, help="Max items to process per source")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed without inserting")
    args = parser.parse_args()

    # Load environment
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set. Export it or add to .env file.")
        sys.exit(1)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set. Needed for Haiku summarization.")
        sys.exit(1)

    # Initialize DB
    from bot.db.database import initialize
    initialize()

    from bot.services.content_extractor import (
        process_youtube_channel,
        process_pubmed_deep,
        process_rss_articles,
        PRIORITY_KEYWORDS,
    )

    total = 0

    if args.source in ("youtube", "all"):
        print("\n=== YouTube Transcript Extraction ===")
        for channel in ["huberman", "attia", "doac"]:
            if args.dry_run:
                from bot.services.content_extractor import get_channel_video_ids, YOUTUBE_CHANNELS
                config = YOUTUBE_CHANNELS[channel]
                videos = get_channel_video_ids(config["channel_id"], max_results=args.limit)
                priority = [v for v in videos if any(kw in v["title"].lower() for kw in PRIORITY_KEYWORDS)]
                print(f"  {channel}: {len(videos)} videos found, {len(priority)} priority matches")
                for v in priority[:5]:
                    print(f"    - {v['title']}")
            else:
                added = process_youtube_channel(
                    channel, max_videos=args.limit,
                    priority_keywords=PRIORITY_KEYWORDS,
                )
                total += added
                print(f"  {channel}: {added} KB entries created")

    if args.source in ("pubmed", "all"):
        print("\n=== PubMed Full Abstract Extraction ===")
        if args.dry_run:
            from bot.services.content_extractor import _pubmed_search_ids, PUBMED_SEARCH_TERMS
            for term in PUBMED_SEARCH_TERMS[:5]:
                ids = _pubmed_search_ids(term, max_results=2)
                print(f"  '{term}': {len(ids)} results")
        else:
            added = process_pubmed_deep(max_per_term=max(1, args.limit // 5))
            total += added
            print(f"  PubMed: {added} KB entries created")

    if args.source in ("jaycampbell", "all"):
        print("\n=== Jay Campbell RSS Article Extraction ===")
        if args.dry_run:
            import feedparser
            feed = feedparser.parse("https://jaycampbell.com/feed/")
            print(f"  {len(feed.entries)} articles in feed")
            for entry in feed.entries[:5]:
                print(f"    - {entry.get('title', 'Untitled')}")
        else:
            added = process_rss_articles(
                "https://jaycampbell.com/feed/", "jay_campbell",
                max_articles=args.limit,
            )
            total += added
            print(f"  Jay Campbell: {added} KB entries created")

    if args.dry_run:
        print("\n[DRY RUN] No entries were inserted.")
    else:
        print(f"\n=== Total: {total} new KB entries created ===")


if __name__ == "__main__":
    main()
