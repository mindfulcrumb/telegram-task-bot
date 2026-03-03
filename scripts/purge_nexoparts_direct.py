#!/usr/bin/env python3
"""Direct purge script — run with: railway run python scripts/purge_nexoparts_direct.py

This is a standalone version that doesn't rely on bot imports.
"""
import os
import re
import psycopg2
import psycopg2.extras

KEYWORDS = [
    "nexoparts", "nexo parts", "nexo-parts",
    "mobilesentrix", "mobile sentrix",
    "wholesale", "cell phone parts",
    "mexico", "mexican retail",
    "oxxo", "spei", "cfdi",
    "tiendanube", "conekta",
    "sa de cv", "us llc",
    "cross-border", "cross border",
]

PATTERN = "|".join(re.escape(kw) for kw in KEYWORDS)

TABLES = [
    ("user_memory", "content"),
    ("conversation_summaries", "summary"),
]


def purge():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set")
        return

    print(f"Connecting to database...")
    try:
        conn = psycopg2.connect(database_url)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    except Exception as e:
        print(f"ERROR: Failed to connect to database: {e}")
        return

    total = 0
    try:
        for table, column in TABLES:
            print(f"\nSearching {table}.{column} for NexoParts content...")
            cur.execute(
                f"""DELETE FROM {table}
                    WHERE {column} ~* %s RETURNING id, {column}""",
                (PATTERN,),
            )
            rows = cur.fetchall()
            for r in rows:
                content_preview = r[column][:80] if r[column] else "(empty)"
                print(f"  ✓ DELETED {table} id={r['id']}: {content_preview}")
            total += len(rows)
            if len(rows) == 0:
                print(f"  → No NexoParts content found in {table}")

        conn.commit()
        print(f"\n✅ Purged {total} NexoParts-related entries total.")
    except Exception as e:
        conn.rollback()
        print(f"\n❌ ERROR during purge: {e}")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    purge()
