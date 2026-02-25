"""Knowledge base service — search peptides, supplements, biomarkers, foods, expert protocols."""
import logging

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)


# ─── General Knowledge Base ───────────────────────────────────────────

def search_kb(query: str, category: str = None, source: str = None, limit: int = 5) -> list:
    """Full-text search on the knowledge base."""
    with get_cursor() as cur:
        ts_query = " & ".join(query.split())
        if category and source:
            cur.execute(
                """SELECT title, content, category, topic, source, source_episode,
                          tags, evidence_level
                   FROM knowledge_base
                   WHERE search_vector @@ to_tsquery('english', %s)
                     AND category = %s AND source = %s
                   ORDER BY ts_rank(search_vector, to_tsquery('english', %s)) DESC
                   LIMIT %s""",
                (ts_query, category, source, ts_query, limit),
            )
        elif category:
            cur.execute(
                """SELECT title, content, category, topic, source, source_episode,
                          tags, evidence_level
                   FROM knowledge_base
                   WHERE search_vector @@ to_tsquery('english', %s)
                     AND category = %s
                   ORDER BY ts_rank(search_vector, to_tsquery('english', %s)) DESC
                   LIMIT %s""",
                (ts_query, category, ts_query, limit),
            )
        elif source:
            cur.execute(
                """SELECT title, content, category, topic, source, source_episode,
                          tags, evidence_level
                   FROM knowledge_base
                   WHERE search_vector @@ to_tsquery('english', %s)
                     AND source = %s
                   ORDER BY ts_rank(search_vector, to_tsquery('english', %s)) DESC
                   LIMIT %s""",
                (ts_query, source, ts_query, limit),
            )
        else:
            cur.execute(
                """SELECT title, content, category, topic, source, source_episode,
                          tags, evidence_level
                   FROM knowledge_base
                   WHERE search_vector @@ to_tsquery('english', %s)
                   ORDER BY ts_rank(search_vector, to_tsquery('english', %s)) DESC
                   LIMIT %s""",
                (ts_query, ts_query, limit),
            )
        rows = cur.fetchall()
        if rows:
            return [dict(r) for r in rows]

        # Fallback: ILIKE search if full-text returns nothing
        like_q = f"%{query}%"
        sql = """SELECT title, content, category, topic, source, source_episode,
                        tags, evidence_level
                 FROM knowledge_base
                 WHERE title ILIKE %s OR content ILIKE %s OR topic ILIKE %s"""
        params = [like_q, like_q, like_q]
        if category:
            sql += " AND category = %s"
            params.append(category)
        if source:
            sql += " AND source = %s"
            params.append(source)
        sql += " LIMIT %s"
        params.append(limit)
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def add_knowledge_entry(category: str, topic: str, title: str, content: str,
                        source: str = None, source_episode: str = None,
                        tags: list = None, evidence_level: str = "C") -> dict:
    """Add a new knowledge base entry."""
    with get_cursor() as cur:
        # Build search vector manually since we don't have triggers
        tag_str = " ".join(tags) if tags else ""
        sv_text = f"{title} {topic} {content} {category} {tag_str}"
        cur.execute(
            """INSERT INTO knowledge_base
               (category, topic, title, content, source, source_episode, tags,
                evidence_level, search_vector)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                       to_tsvector('english', %s))
               RETURNING id""",
            (category, topic, title, content, source, source_episode, tags,
             evidence_level, sv_text),
        )
        row = cur.fetchone()
        return {"id": row["id"]}


# ─── Peptide Reference ────────────────────────────────────────────────

def get_peptide_info(name: str) -> dict | None:
    """Get peptide by name or slug (exact match first, then fuzzy)."""
    with get_cursor() as cur:
        # Exact match on slug or name
        cur.execute(
            """SELECT * FROM peptide_reference
               WHERE slug = %s OR LOWER(name) = LOWER(%s)""",
            (name.lower().replace(" ", "-"), name),
        )
        row = cur.fetchone()
        if row:
            return dict(row)

        # Fuzzy ILIKE
        cur.execute(
            """SELECT * FROM peptide_reference
               WHERE name ILIKE %s OR slug ILIKE %s
               LIMIT 1""",
            (f"%{name}%", f"%{name}%"),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def search_peptides(query: str, category: str = None) -> list:
    """Search peptides by text or category."""
    with get_cursor() as cur:
        ts_query = " & ".join(query.split())
        if category:
            cur.execute(
                """SELECT name, slug, description, categories, standard_dose,
                          evidence_level, beginner_friendly
                   FROM peptide_reference
                   WHERE search_vector @@ to_tsquery('english', %s)
                     AND %s = ANY(categories)
                   ORDER BY ts_rank(search_vector, to_tsquery('english', %s)) DESC
                   LIMIT 10""",
                (ts_query, category, ts_query),
            )
        else:
            cur.execute(
                """SELECT name, slug, description, categories, standard_dose,
                          evidence_level, beginner_friendly
                   FROM peptide_reference
                   WHERE search_vector @@ to_tsquery('english', %s)
                   ORDER BY ts_rank(search_vector, to_tsquery('english', %s)) DESC
                   LIMIT 10""",
                (ts_query, ts_query),
            )
        rows = cur.fetchall()
        if rows:
            return [dict(r) for r in rows]

        # Fallback ILIKE
        like_q = f"%{query}%"
        cur.execute(
            """SELECT name, slug, description, categories, standard_dose,
                      evidence_level, beginner_friendly
               FROM peptide_reference
               WHERE name ILIKE %s OR description ILIKE %s OR mechanism ILIKE %s
               LIMIT 10""",
            (like_q, like_q, like_q),
        )
        return [dict(r) for r in cur.fetchall()]


# ─── Biomarker Reference ──────────────────────────────────────────────

def get_biomarker_info(marker_name: str) -> dict | None:
    """Get biomarker reference by name."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT * FROM biomarker_reference
               WHERE marker_name_normalized = LOWER(%s)
                  OR marker_name ILIKE %s""",
            (marker_name, f"%{marker_name}%"),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def interpret_biomarker(marker_name: str, value: float) -> dict:
    """Interpret a biomarker value against optimal and lab ranges."""
    info = get_biomarker_info(marker_name)
    if not info:
        return {"marker": marker_name, "value": value, "interpretation": "No reference data available"}

    result = {
        "marker": info["marker_name"],
        "value": value,
        "unit": info["unit"],
        "lab_range": f"{info['lab_range_low']}-{info['lab_range_high']}",
        "optimal_range": f"{info['optimal_range_low']}-{info['optimal_range_high']}",
    }

    if info["optimal_range_low"] and info["optimal_range_high"]:
        if value < info["optimal_range_low"]:
            result["status"] = "below_optimal"
            result["interpretation"] = info.get("interpretation_low", "Below optimal range")
        elif value > info["optimal_range_high"]:
            result["status"] = "above_optimal"
            result["interpretation"] = info.get("interpretation_high", "Above optimal range")
        else:
            result["status"] = "optimal"
            result["interpretation"] = "Within optimal range"

    if info.get("optimization_tips"):
        result["tips"] = info["optimization_tips"]

    return result


# ─── Food Reference ───────────────────────────────────────────────────

def search_foods(query: str, blood_type: str = None) -> list:
    """Search foods by name, optionally filtered by blood type classification."""
    with get_cursor() as cur:
        like_q = f"%{query.lower()}%"
        if blood_type:
            bt_col = f"blood_type_{blood_type.lower()}"
            cur.execute(
                f"""SELECT name, category, calories_per_100g, protein_g, carbs_g,
                           fat_g, fiber_g, {bt_col} as classification,
                           serving_size_g, serving_description
                    FROM food_reference
                    WHERE name_normalized ILIKE %s
                    ORDER BY name
                    LIMIT 20""",
                (like_q,),
            )
        else:
            cur.execute(
                """SELECT name, category, calories_per_100g, protein_g, carbs_g,
                          fat_g, fiber_g, blood_type_o, blood_type_a,
                          blood_type_b, blood_type_ab,
                          serving_size_g, serving_description
                   FROM food_reference
                   WHERE name_normalized ILIKE %s
                   ORDER BY name
                   LIMIT 20""",
                (like_q,),
            )
        return [dict(r) for r in cur.fetchall()]


def get_foods_by_blood_type(blood_type: str, classification: str = None,
                            category: str = None, query: str = None) -> list:
    """Get foods filtered by blood type and classification."""
    bt_col = f"blood_type_{blood_type.lower()}"
    with get_cursor() as cur:
        sql = f"""SELECT name, category, calories_per_100g, protein_g,
                         {bt_col} as classification
                  FROM food_reference WHERE 1=1"""
        params = []
        if classification:
            sql += f" AND {bt_col} = %s"
            params.append(classification)
        if category:
            sql += " AND category = %s"
            params.append(category)
        if query:
            sql += " AND name_normalized ILIKE %s"
            params.append(f"%{query.lower()}%")
        sql += " ORDER BY name LIMIT 50"
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


# ─── Supplement Reference ─────────────────────────────────────────────

def get_supplement_info(name: str) -> dict | None:
    """Get supplement by name (exact then fuzzy)."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT * FROM supplement_reference
               WHERE name_normalized = LOWER(%s)""",
            (name,),
        )
        row = cur.fetchone()
        if row:
            return dict(row)

        cur.execute(
            """SELECT * FROM supplement_reference
               WHERE name ILIKE %s OR name_normalized ILIKE %s
               LIMIT 1""",
            (f"%{name}%", f"%{name}%"),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def search_supplements(query: str) -> list:
    """Search supplements by text."""
    with get_cursor() as cur:
        ts_query = " & ".join(query.split())
        cur.execute(
            """SELECT name, category, description, standard_dose, timing,
                      benefits, evidence_level
               FROM supplement_reference
               WHERE search_vector @@ to_tsquery('english', %s)
               ORDER BY ts_rank(search_vector, to_tsquery('english', %s)) DESC
               LIMIT 10""",
            (ts_query, ts_query),
        )
        rows = cur.fetchall()
        if rows:
            return [dict(r) for r in rows]

        like_q = f"%{query}%"
        cur.execute(
            """SELECT name, category, description, standard_dose, timing,
                      benefits, evidence_level
               FROM supplement_reference
               WHERE name ILIKE %s OR description ILIKE %s OR mechanism ILIKE %s
               LIMIT 10""",
            (like_q, like_q, like_q),
        )
        return [dict(r) for r in cur.fetchall()]
