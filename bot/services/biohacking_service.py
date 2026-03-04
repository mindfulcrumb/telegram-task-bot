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
    """Find active protocol by peptide name (fuzzy match).

    Handles common variations: 'BPC TB' matches 'BPC-157 + TB-500 Blend',
    'BPC' matches 'BPC-157', 'reta' matches 'Retatrutide', etc.
    """
    with get_cursor() as cur:
        # Try exact match first (case-insensitive)
        cur.execute(
            """SELECT * FROM peptide_protocols
               WHERE user_id = %s AND LOWER(peptide_name) = LOWER(%s) AND status = 'active'
               ORDER BY created_at DESC LIMIT 1""",
            (user_id, peptide_name)
        )
        row = cur.fetchone()
        if row:
            return dict(row)

        # Fuzzy match: check if search term appears in protocol name or vice versa
        cur.execute(
            """SELECT * FROM peptide_protocols
               WHERE user_id = %s AND status = 'active'
               ORDER BY created_at DESC""",
            (user_id,)
        )
        rows = cur.fetchall()
        if not rows:
            return None

        search_lower = peptide_name.lower().strip()
        search_clean = search_lower.replace("-", "").replace("+", "").replace("  ", " ")
        search_words = [w for w in search_clean.split() if len(w) >= 3]

        best_match = None
        best_score = 0

        for r in rows:
            name_lower = r["peptide_name"].lower()
            name_clean = name_lower.replace("-", "").replace("+", "").replace("  ", " ")

            # Exact normalized match
            if search_clean == name_clean:
                return dict(r)

            # Search term contained in protocol name or vice versa
            if search_lower in name_lower or search_clean in name_clean:
                return dict(r)
            if name_lower in search_lower or name_clean in search_clean:
                if len(name_lower) > best_score:
                    best_match = r
                    best_score = len(name_lower)
                continue

            # Word-level matching
            if search_words:
                matches = sum(1 for w in search_words if w in name_clean)
                score = matches / len(search_words)
                if score > 0.5 and matches > best_score:
                    best_match = r
                    best_score = matches

        return dict(best_match) if best_match else None


def update_protocol_status(protocol_id: int, status: str) -> dict | None:
    """Update protocol status (active/paused/completed)."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE peptide_protocols SET status = %s WHERE id = %s RETURNING *",
            (status, protocol_id)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def update_protocol(protocol_id: int, **kwargs) -> dict | None:
    """Update protocol fields — peptide_name, dose_amount, dose_unit, frequency, route, cycle_end, notes."""
    allowed = {'peptide_name', 'dose_amount', 'dose_unit', 'frequency', 'route', 'cycle_end', 'notes'}
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        return None
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values()) + [protocol_id]
    with get_cursor() as cur:
        cur.execute(
            f"UPDATE peptide_protocols SET {set_clause} WHERE id = %s RETURNING *",
            values
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

def get_todays_doses(user_id: int) -> list:
    """Get all peptide doses logged today, across all protocols."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT pl.*, pp.peptide_name
               FROM peptide_logs pl
               JOIN peptide_protocols pp ON pl.protocol_id = pp.id
               WHERE pl.user_id = %s AND pl.administered_at >= CURRENT_DATE
               ORDER BY pl.administered_at DESC""",
            (user_id,)
        )
        return [dict(row) for row in cur.fetchall()]


def get_biohacking_summary(user_id: int) -> dict:
    """Everything the AI brain needs for biohacking context."""
    protocols = get_protocol_summary(user_id)
    supplements = get_active_supplements(user_id)
    adherence = get_supplement_adherence(user_id, days=7)
    bloodwork = get_bloodwork_history(user_id, limit=1)
    flagged = get_flagged_biomarkers(user_id)
    todays_doses = get_todays_doses(user_id)

    return {
        "protocols": protocols,
        "supplements": supplements,
        "supplement_adherence": adherence,
        "latest_bloodwork": bloodwork[0] if bloodwork else None,
        "flagged_biomarkers": flagged,
        "todays_doses": todays_doses,
    }


# ═══════════════════════════════════════════════════════════════
# INTERACTIVE PROTOCOL SYSTEM — schedules, adherence, daily doses
# ═══════════════════════════════════════════════════════════════

# --- Schedule CRUD ---

def add_schedule(protocol_id: int, dose_time: str, dose_days: list = None,
                 label: str = None) -> dict:
    """Attach a dose schedule to a protocol.

    Args:
        protocol_id: Protocol to schedule
        dose_time: Time string 'HH:MM' (24h format)
        dose_days: ISO weekdays [1=Mon..7=Sun], default all 7
        label: e.g. 'Morning dose', 'Evening dose'
    """
    if dose_days is None:
        dose_days = [1, 2, 3, 4, 5, 6, 7]
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO protocol_schedules (protocol_id, dose_time, dose_days, label)
               VALUES (%s, %s, %s, %s) RETURNING *""",
            (protocol_id, dose_time, dose_days, label)
        )
        return dict(cur.fetchone())


def get_schedules(protocol_id: int) -> list:
    """Get all schedule entries for a protocol, ordered by time."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT * FROM protocol_schedules
               WHERE protocol_id = %s ORDER BY dose_time""",
            (protocol_id,)
        )
        return [dict(row) for row in cur.fetchall()]


def delete_schedules(protocol_id: int):
    """Remove all schedules for a protocol."""
    with get_cursor() as cur:
        cur.execute(
            "DELETE FROM protocol_schedules WHERE protocol_id = %s",
            (protocol_id,)
        )


# --- Frequency code helpers ---

FREQUENCY_CODES = {
    "daily":      {"label": "Daily",        "days": [1, 2, 3, 4, 5, 6, 7]},
    "2x_daily":   {"label": "2x Daily",     "days": [1, 2, 3, 4, 5, 6, 7]},
    "eod":        {"label": "Every Other Day", "days": [1, 3, 5, 7]},
    "3x_weekly":  {"label": "3x Weekly",    "days": [1, 3, 5]},
    "5on2off":    {"label": "5 on / 2 off", "days": [1, 2, 3, 4, 5]},
}


def add_protocol_with_schedule(
    user_id: int, peptide_name: str,
    dose_amount: float, dose_unit: str,
    frequency_code: str, route: str,
    schedule_times: list, cycle_weeks: int,
    notes: str = None
) -> dict:
    """Create a protocol with structured schedule in one transaction.

    Args:
        schedule_times: list of 'HH:MM' strings (1 for daily/eod/3x/5on2off, 2 for 2x_daily)
        cycle_weeks: 4, 6, 8, 12 etc.
    """
    today = date.today()
    cycle_end = today + timedelta(weeks=cycle_weeks)
    freq_info = FREQUENCY_CODES.get(frequency_code, FREQUENCY_CODES["daily"])
    freq_label = freq_info["label"]
    dose_days = freq_info["days"]

    with get_cursor() as cur:
        # 1. Create protocol
        cur.execute(
            """INSERT INTO peptide_protocols
               (user_id, peptide_name, dose_amount, dose_unit, frequency, route,
                cycle_start, cycle_end, notes)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (user_id, peptide_name, dose_amount, dose_unit, freq_label, route,
             today, cycle_end, notes)
        )
        protocol = dict(cur.fetchone())
        protocol_id = protocol["id"]

        # 2. Create schedule entries
        for i, t in enumerate(schedule_times):
            label = None
            if len(schedule_times) == 2:
                label = "Morning dose" if i == 0 else "Evening dose"
            elif len(schedule_times) == 1:
                label = "Daily dose"
            cur.execute(
                """INSERT INTO protocol_schedules (protocol_id, dose_time, dose_days, label)
                   VALUES (%s, %s, %s, %s)""",
                (protocol_id, t, dose_days, label)
            )

        # 3. Generate today's scheduled doses
        _generate_doses_for_date(cur, protocol_id, user_id, today, dose_days, schedule_times)

    protocol["cycle_total"] = cycle_weeks * 7
    protocol["schedules"] = get_schedules(protocol_id)
    return protocol


# --- Daily dose generation ---

def generate_daily_doses(user_id: int, target_date: date = None):
    """Generate scheduled_doses rows for target_date. Idempotent (UNIQUE constraint)."""
    if target_date is None:
        target_date = date.today()

    protocols = get_active_protocols(user_id)
    if not protocols:
        return

    with get_cursor() as cur:
        for p in protocols:
            # Skip if outside cycle window
            if p.get("cycle_start") and target_date < p["cycle_start"]:
                continue
            if p.get("cycle_end") and target_date > p["cycle_end"]:
                continue

            schedules = get_schedules(p["id"])
            if not schedules:
                continue

            weekday_iso = target_date.isoweekday()  # 1=Mon..7=Sun

            for sched in schedules:
                dose_days = sched.get("dose_days") or [1, 2, 3, 4, 5, 6, 7]
                if weekday_iso not in dose_days:
                    continue

                dose_time = sched["dose_time"]
                try:
                    cur.execute(
                        """INSERT INTO scheduled_doses
                           (protocol_id, user_id, scheduled_date, scheduled_time)
                           VALUES (%s, %s, %s, %s)
                           ON CONFLICT (protocol_id, scheduled_date, scheduled_time) DO NOTHING""",
                        (p["id"], user_id, target_date, dose_time)
                    )
                except Exception as e:
                    logger.debug(f"Dose generation skip: {e}")


def _generate_doses_for_date(cur, protocol_id, user_id, target_date, dose_days, schedule_times):
    """Internal: generate doses for a specific date (within existing cursor/transaction)."""
    weekday_iso = target_date.isoweekday()
    if weekday_iso not in dose_days:
        return
    for t in schedule_times:
        try:
            cur.execute(
                """INSERT INTO scheduled_doses
                   (protocol_id, user_id, scheduled_date, scheduled_time)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (protocol_id, scheduled_date, scheduled_time) DO NOTHING""",
                (protocol_id, user_id, target_date, t)
            )
        except Exception:
            pass


# --- Dose status management ---

def get_todays_scheduled_doses(user_id: int) -> list:
    """Get today's scheduled doses with protocol info, ordered by time."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT sd.*, pp.peptide_name, pp.dose_amount, pp.dose_unit, pp.route
               FROM scheduled_doses sd
               JOIN peptide_protocols pp ON sd.protocol_id = pp.id
               WHERE sd.user_id = %s AND sd.scheduled_date = CURRENT_DATE
               ORDER BY sd.scheduled_time""",
            (user_id,)
        )
        return [dict(row) for row in cur.fetchall()]


def get_pending_doses(user_id: int) -> list:
    """Get today's doses that are still pending."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT sd.*, pp.peptide_name, pp.dose_amount, pp.dose_unit, pp.route
               FROM scheduled_doses sd
               JOIN peptide_protocols pp ON sd.protocol_id = pp.id
               WHERE sd.user_id = %s AND sd.scheduled_date = CURRENT_DATE
                 AND sd.status = 'pending'
               ORDER BY sd.scheduled_time""",
            (user_id,)
        )
        return [dict(row) for row in cur.fetchall()]


def get_pending_doses_in_window(user_id: int, current_time, window_minutes: int = 30) -> list:
    """Get pending doses within ±window_minutes of current_time."""
    from datetime import time as dt_time, datetime as dt, timedelta as td
    if isinstance(current_time, dt):
        current_time = current_time.time()

    # Calculate window boundaries
    now_dt = dt.combine(date.today(), current_time)
    window_start = (now_dt - td(minutes=window_minutes)).time()
    window_end = (now_dt + td(minutes=window_minutes)).time()

    with get_cursor() as cur:
        cur.execute(
            """SELECT sd.*, pp.peptide_name, pp.dose_amount, pp.dose_unit, pp.route
               FROM scheduled_doses sd
               JOIN peptide_protocols pp ON sd.protocol_id = pp.id
               WHERE sd.user_id = %s AND sd.scheduled_date = CURRENT_DATE
                 AND sd.status = 'pending'
                 AND sd.scheduled_time BETWEEN %s AND %s
               ORDER BY sd.scheduled_time""",
            (user_id, window_start, window_end)
        )
        return [dict(row) for row in cur.fetchall()]


def mark_dose_taken(scheduled_dose_id: int, user_id: int,
                    site: str = None, notes: str = None) -> dict:
    """Mark a scheduled dose as taken. Creates peptide_logs entry and updates scheduled_doses."""
    with get_cursor() as cur:
        # Get the scheduled dose info
        cur.execute(
            "SELECT * FROM scheduled_doses WHERE id = %s AND user_id = %s",
            (scheduled_dose_id, user_id)
        )
        sd = cur.fetchone()
        if not sd:
            return None
        sd = dict(sd)

        if sd["status"] != "pending":
            return sd  # Already handled

        # Get protocol for dose amount
        cur.execute(
            "SELECT * FROM peptide_protocols WHERE id = %s",
            (sd["protocol_id"],)
        )
        protocol = dict(cur.fetchone())

        # 1. Create actual dose log
        cur.execute(
            """INSERT INTO peptide_logs (protocol_id, user_id, dose_amount, site, notes)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (sd["protocol_id"], user_id, protocol.get("dose_amount"), site, notes)
        )
        log_id = cur.fetchone()["id"]

        # 2. Update scheduled dose
        cur.execute(
            """UPDATE scheduled_doses
               SET status = 'taken', log_id = %s, responded_at = NOW()
               WHERE id = %s""",
            (log_id, scheduled_dose_id)
        )

        sd["status"] = "taken"
        sd["log_id"] = log_id
        sd["peptide_name"] = protocol["peptide_name"]
        return sd


def mark_dose_skipped(scheduled_dose_id: int, user_id: int) -> dict:
    """Mark a scheduled dose as skipped."""
    with get_cursor() as cur:
        cur.execute(
            """UPDATE scheduled_doses SET status = 'skipped', responded_at = NOW()
               WHERE id = %s AND user_id = %s RETURNING *""",
            (scheduled_dose_id, user_id)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def mark_overdue_doses_missed(user_id: int) -> int:
    """Mark pending doses from previous days as missed. Returns count."""
    with get_cursor() as cur:
        cur.execute(
            """UPDATE scheduled_doses SET status = 'missed', responded_at = NOW()
               WHERE user_id = %s AND status = 'pending'
                 AND scheduled_date < CURRENT_DATE
               RETURNING id""",
            (user_id,)
        )
        return len(cur.fetchall())


def set_dose_reminder_message_id(scheduled_dose_id: int, message_id: int):
    """Store the Telegram message_id for in-place card editing."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE scheduled_doses SET reminder_message_id = %s WHERE id = %s",
            (message_id, scheduled_dose_id)
        )


# --- Adherence calculations ---

def get_adherence(protocol_id: int, days: int = 7) -> dict:
    """Calculate adherence for a protocol over the last N days."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT status, COUNT(*) as c
               FROM scheduled_doses
               WHERE protocol_id = %s
                 AND scheduled_date >= CURRENT_DATE - %s
                 AND scheduled_date <= CURRENT_DATE
               GROUP BY status""",
            (protocol_id, days)
        )
        counts = {row["status"]: row["c"] for row in cur.fetchall()}

    taken = counts.get("taken", 0)
    skipped = counts.get("skipped", 0)
    missed = counts.get("missed", 0)
    pending = counts.get("pending", 0)
    total = taken + skipped + missed + pending
    rate = round(taken / total * 100) if total > 0 else 0

    # Streak: consecutive days where ALL doses were taken
    streak = _calc_adherence_streak(protocol_id)

    return {
        "total_expected": total,
        "taken": taken,
        "skipped": skipped,
        "missed": missed,
        "pending": pending,
        "rate": rate,
        "streak": streak,
    }


def _calc_adherence_streak(protocol_id: int) -> int:
    """Count consecutive days (backwards from yesterday) with 100% adherence."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT scheduled_date,
                      COUNT(*) as total,
                      COUNT(*) FILTER (WHERE status = 'taken') as taken
               FROM scheduled_doses
               WHERE protocol_id = %s AND scheduled_date < CURRENT_DATE
               GROUP BY scheduled_date
               ORDER BY scheduled_date DESC
               LIMIT 30""",
            (protocol_id,)
        )
        streak = 0
        for row in cur.fetchall():
            if row["taken"] == row["total"]:
                streak += 1
            else:
                break
        return streak


def get_daily_adherence(user_id: int, days: int = 7) -> list:
    """Get day-by-day adherence for the 7-day visual across all protocols."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT scheduled_date,
                      COUNT(*) as total,
                      COUNT(*) FILTER (WHERE status = 'taken') as taken
               FROM scheduled_doses
               WHERE user_id = %s
                 AND scheduled_date >= CURRENT_DATE - %s
                 AND scheduled_date <= CURRENT_DATE
               GROUP BY scheduled_date
               ORDER BY scheduled_date""",
            (user_id, days)
        )
        results = []
        for row in cur.fetchall():
            total = row["total"]
            taken = row["taken"]
            if total == 0:
                status = "none"
            elif taken == total:
                status = "full"
            elif taken > 0:
                status = "partial"
            else:
                status = "missed"
            results.append({
                "date": row["scheduled_date"],
                "total": total,
                "taken": taken,
                "status": status,
            })
        return results


def get_protocol_adherence(protocol_id: int, days: int = 7) -> list:
    """Day-by-day adherence for a single protocol (for dashboard visual)."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT scheduled_date,
                      COUNT(*) as total,
                      COUNT(*) FILTER (WHERE status = 'taken') as taken
               FROM scheduled_doses
               WHERE protocol_id = %s
                 AND scheduled_date >= CURRENT_DATE - %s
                 AND scheduled_date <= CURRENT_DATE
               GROUP BY scheduled_date
               ORDER BY scheduled_date""",
            (protocol_id, days)
        )
        results = []
        for row in cur.fetchall():
            total = row["total"]
            taken = row["taken"]
            if taken == total:
                status = "full"
            elif taken > 0:
                status = "partial"
            else:
                status = "missed"
            results.append({
                "date": row["scheduled_date"],
                "total": total,
                "taken": taken,
                "status": status,
            })
        return results
