"""Biohacking service — peptide protocols, supplements, bloodwork tracking."""
import logging
from datetime import date, timedelta

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)


# --- Peptide protocols ---

def add_protocol(user_id: int, peptide_name: str, dose_amount: float = None,
                 dose_unit: str = "mcg", frequency: str = None,
                 route: str = "subcutaneous", cycle_start: date = None,
                 cycle_end: date = None, notes: str = None) -> dict:
    """Create a new peptide protocol."""
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO peptide_protocols
               (user_id, peptide_name, dose_amount, dose_unit, frequency, route,
                cycle_start, cycle_end, notes)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (user_id, peptide_name, dose_amount, dose_unit, frequency, route,
             cycle_start, cycle_end, notes)
        )
        return dict(cur.fetchone())


def get_active_protocols(user_id: int) -> list:
    """Get all active peptide protocols for a user."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT * FROM peptide_protocols
               WHERE user_id = %s AND status = 'active'
               ORDER BY created_at DESC""",
            (user_id,)
        )
        return [dict(row) for row in cur.fetchall()]


def get_protocol_by_id(protocol_id: int) -> dict | None:
    """Get a specific protocol."""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM peptide_protocols WHERE id = %s", (protocol_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_protocol_by_name(user_id: int, peptide_name: str) -> dict | None:
    """Find active protocol by peptide name (case-insensitive)."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT * FROM peptide_protocols
               WHERE user_id = %s AND LOWER(peptide_name) = LOWER(%s) AND status = 'active'
               ORDER BY created_at DESC LIMIT 1""",
            (user_id, peptide_name)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def update_protocol_status(protocol_id: int, status: str) -> dict | None:
    """Update protocol status (active/paused/completed)."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE peptide_protocols SET status = %s WHERE id = %s RETURNING *",
            (status, protocol_id)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def log_dose(user_id: int, protocol_id: int, dose_amount: float = None,
             site: str = None, notes: str = None) -> dict:
    """Record a peptide dose administration."""
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO peptide_logs (protocol_id, user_id, dose_amount, site, notes)
               VALUES (%s, %s, %s, %s, %s) RETURNING *""",
            (protocol_id, user_id, dose_amount, site, notes)
        )
        return dict(cur.fetchone())


def get_dose_history(user_id: int, protocol_id: int, days: int = 30) -> list:
    """Get dose history for a protocol."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT * FROM peptide_logs
               WHERE user_id = %s AND protocol_id = %s
               AND administered_at >= NOW() - INTERVAL '%s days'
               ORDER BY administered_at DESC""",
            (user_id, protocol_id, days)
        )
        return [dict(row) for row in cur.fetchall()]


def get_protocol_summary(user_id: int) -> list:
    """Get active protocols with adherence and cycle progress."""
    protocols = get_active_protocols(user_id)
    today = date.today()

    for p in protocols:
        # Cycle progress
        if p.get("cycle_start") and p.get("cycle_end"):
            total_days = (p["cycle_end"] - p["cycle_start"]).days
            elapsed = (today - p["cycle_start"]).days
            p["cycle_day"] = max(0, elapsed)
            p["cycle_total"] = total_days
            p["days_remaining"] = max(0, (p["cycle_end"] - today).days)
        else:
            p["cycle_day"] = None
            p["cycle_total"] = None
            p["days_remaining"] = None

        # Recent adherence (last 7 days)
        doses = get_dose_history(user_id, p["id"], days=7)
        p["doses_last_7d"] = len(doses)

    return protocols


# --- Supplements ---

def add_supplement(user_id: int, supplement_name: str, dose_amount: float = None,
                   dose_unit: str = None, frequency: str = "daily",
                   timing: str = None) -> dict:
    """Add a supplement to the stack."""
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO supplements
               (user_id, supplement_name, dose_amount, dose_unit, frequency, timing)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
            (user_id, supplement_name, dose_amount, dose_unit, frequency, timing)
        )
        return dict(cur.fetchone())


def get_active_supplements(user_id: int) -> list:
    """Get all active supplements."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT * FROM supplements
               WHERE user_id = %s AND status = 'active'
               ORDER BY timing, supplement_name""",
            (user_id,)
        )
        return [dict(row) for row in cur.fetchall()]


def get_supplement_by_name(user_id: int, name: str) -> dict | None:
    """Find active supplement by name."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT * FROM supplements
               WHERE user_id = %s AND LOWER(supplement_name) = LOWER(%s) AND status = 'active'
               ORDER BY created_at DESC LIMIT 1""",
            (user_id, name)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def update_supplement_status(supplement_id: int, status: str) -> dict | None:
    """Update supplement status (active/removed)."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE supplements SET status = %s WHERE id = %s RETURNING *",
            (status, supplement_id)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def log_supplement_taken(user_id: int, supplement_id: int) -> dict:
    """Mark a supplement as taken."""
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO supplement_logs (supplement_id, user_id)
               VALUES (%s, %s) RETURNING *""",
            (supplement_id, user_id)
        )
        return dict(cur.fetchone())


def log_all_supplements_taken(user_id: int) -> list:
    """Mark all active supplements as taken."""
    supps = get_active_supplements(user_id)
    logged = []
    for s in supps:
        log_supplement_taken(user_id, s["id"])
        logged.append(s["supplement_name"])
    return logged


def get_supplement_adherence(user_id: int, days: int = 7) -> dict:
    """Get supplement adherence over the last N days."""
    supps = get_active_supplements(user_id)
    if not supps:
        return {"supplements": [], "overall_rate": 0}

    results = []
    total_expected = 0
    total_taken = 0

    with get_cursor() as cur:
        for s in supps:
            cur.execute(
                """SELECT COUNT(*) as cnt FROM supplement_logs
                   WHERE user_id = %s AND supplement_id = %s
                   AND taken_at >= NOW() - INTERVAL '%s days'""",
                (user_id, s["id"], days)
            )
            taken = cur.fetchone()["cnt"]
            expected = days  # Simplified: assume daily
            results.append({
                "name": s["supplement_name"],
                "taken": taken,
                "expected": expected,
                "rate": round(taken / expected * 100) if expected > 0 else 0,
            })
            total_expected += expected
            total_taken += taken

    overall = round(total_taken / total_expected * 100) if total_expected > 0 else 0
    return {"supplements": results, "overall_rate": overall}


# --- Bloodwork ---

def log_bloodwork(user_id: int, test_date: date, lab_name: str = None,
                  notes: str = None, markers: list = None) -> dict:
    """Log a bloodwork panel with individual biomarkers."""
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO bloodwork (user_id, test_date, lab_name, notes)
               VALUES (%s, %s, %s, %s) RETURNING *""",
            (user_id, test_date, lab_name, notes)
        )
        panel = dict(cur.fetchone())

        if markers:
            for m in markers:
                # Determine flag
                flag = None
                value = m.get("value")
                ref_low = m.get("reference_low")
                ref_high = m.get("reference_high")
                if ref_low is not None and value < ref_low:
                    flag = "low"
                elif ref_high is not None and value > ref_high:
                    flag = "high"

                cur.execute(
                    """INSERT INTO biomarkers
                       (bloodwork_id, user_id, marker_name, value, unit,
                        reference_low, reference_high, flag)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (panel["id"], user_id, m["marker_name"], value,
                     m.get("unit"), ref_low, ref_high, flag)
                )

        panel["marker_count"] = len(markers) if markers else 0

    return panel


def get_bloodwork_history(user_id: int, limit: int = 5) -> list:
    """Get recent bloodwork panels with their biomarkers."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT * FROM bloodwork
               WHERE user_id = %s ORDER BY test_date DESC LIMIT %s""",
            (user_id, limit)
        )
        panels = [dict(row) for row in cur.fetchall()]

        for p in panels:
            cur.execute(
                """SELECT * FROM biomarkers
                   WHERE bloodwork_id = %s ORDER BY marker_name""",
                (p["id"],)
            )
            p["markers"] = [dict(row) for row in cur.fetchall()]

    return panels


def get_biomarker_trend(user_id: int, marker_name: str, limit: int = 10) -> list:
    """Get trend data for a specific biomarker."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT b.marker_name, b.value, b.unit, b.flag,
                      bw.test_date, bw.lab_name
               FROM biomarkers b
               JOIN bloodwork bw ON bw.id = b.bloodwork_id
               WHERE b.user_id = %s AND LOWER(b.marker_name) = LOWER(%s)
               ORDER BY bw.test_date DESC LIMIT %s""",
            (user_id, marker_name, limit)
        )
        return [dict(row) for row in cur.fetchall()]


def get_flagged_biomarkers(user_id: int) -> list:
    """Get out-of-range biomarkers from the latest panel."""
    with get_cursor() as cur:
        # Get latest panel
        cur.execute(
            "SELECT id FROM bloodwork WHERE user_id = %s ORDER BY test_date DESC LIMIT 1",
            (user_id,)
        )
        row = cur.fetchone()
        if not row:
            return []

        cur.execute(
            """SELECT * FROM biomarkers
               WHERE bloodwork_id = %s AND flag IS NOT NULL
               ORDER BY marker_name""",
            (row["id"],)
        )
        return [dict(row) for row in cur.fetchall()]


def get_enriched_bloodwork(user_id: int) -> dict | None:
    """Get latest panel enriched with optimal ranges, trends, and protocol connections."""
    panels = get_bloodwork_history(user_id, limit=2)
    if not panels:
        return None

    latest = panels[0]
    previous = panels[1] if len(panels) > 1 else None

    from bot.services.knowledge_service import get_biomarker_info

    enriched_markers = []
    for m in latest.get("markers", []):
        marker_data = {
            "marker_name": m["marker_name"],
            "value": m["value"],
            "unit": m.get("unit", ""),
            "flag": m.get("flag"),
            "reference_low": m.get("reference_low"),
            "reference_high": m.get("reference_high"),
        }

        # Add optimal ranges from knowledge base
        info = get_biomarker_info(m["marker_name"])
        if info:
            marker_data["optimal_low"] = info.get("optimal_range_low")
            marker_data["optimal_high"] = info.get("optimal_range_high")
            # Determine optimal status
            opt_low = info.get("optimal_range_low")
            opt_high = info.get("optimal_range_high")
            if opt_low is not None and opt_high is not None:
                if m["value"] < opt_low:
                    marker_data["optimal_status"] = "below"
                elif m["value"] > opt_high:
                    marker_data["optimal_status"] = "above"
                else:
                    marker_data["optimal_status"] = "optimal"

        # Add trend vs previous panel
        if previous:
            for pm in previous.get("markers", []):
                if pm["marker_name"].lower() == m["marker_name"].lower():
                    diff = m["value"] - pm["value"]
                    marker_data["prev_value"] = pm["value"]
                    marker_data["change"] = diff
                    marker_data["prev_date"] = previous["test_date"]
                    break

        enriched_markers.append(marker_data)

    # Get active protocols for connection
    protocols = get_protocol_summary(user_id)

    return {
        "panel": latest,
        "markers": enriched_markers,
        "previous_date": previous["test_date"] if previous else None,
        "active_protocols": protocols,
        "flagged_count": sum(1 for m in enriched_markers if m.get("flag")),
        "suboptimal_count": sum(1 for m in enriched_markers if m.get("optimal_status") in ("below", "above")),
    }


# --- Full biohacking summary (for AI context) ---

def get_biohacking_summary(user_id: int) -> dict:
    """Everything the AI brain needs for biohacking context."""
    protocols = get_protocol_summary(user_id)
    supplements = get_active_supplements(user_id)
    adherence = get_supplement_adherence(user_id, days=7)
    bloodwork = get_bloodwork_history(user_id, limit=1)
    flagged = get_flagged_biomarkers(user_id)

    return {
        "protocols": protocols,
        "supplements": supplements,
        "supplement_adherence": adherence,
        "latest_bloodwork": bloodwork[0] if bloodwork else None,
        "flagged_biomarkers": flagged,
    }
