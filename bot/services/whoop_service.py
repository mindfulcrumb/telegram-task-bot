"""WHOOP integration — OAuth2, data sync, recovery/sleep/strain access (API v2)."""
import hashlib
import hmac
import logging
import os
import secrets
import urllib.parse
from datetime import datetime, timedelta, date, timezone

import httpx

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)

# WHOOP API endpoints (v2 — v1 deprecated Oct 2025)
WHOOP_AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
WHOOP_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
WHOOP_API_BASE = "https://api.prod.whoop.com/developer/v2"

# Scopes — offline MUST be first per WHOOP docs (enables refresh tokens)
WHOOP_SCOPES = "offline read:recovery read:sleep read:workout read:cycles read:profile read:body_measurement"

# Shared httpx client with browser-like headers to avoid Cloudflare blocks
_http = httpx.Client(
    timeout=20,
    headers={
        "User-Agent": "Mozilla/5.0 (compatible; ZoeBot/1.0)",
        "Accept": "application/json",
    },
)


def _get_client_id() -> str:
    return os.environ.get("WHOOP_CLIENT_ID", "")


def _get_client_secret() -> str:
    return os.environ.get("WHOOP_CLIENT_SECRET", "")


def _get_redirect_uri() -> str:
    # Explicit env var takes priority
    explicit = os.environ.get("WHOOP_REDIRECT_URI", "")
    if explicit:
        return explicit
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if domain:
        return f"https://{domain}/whoop/callback"
    return ""


def is_configured() -> bool:
    """Check if WHOOP credentials are set."""
    return bool(_get_client_id() and _get_client_secret() and _get_redirect_uri())


# --- OAuth ---

def get_auth_url(user_id: int) -> str | None:
    """Generate OAuth authorization URL with state param."""
    client_id = _get_client_id()
    redirect_uri = _get_redirect_uri()
    if not client_id or not redirect_uri:
        return None

    # CSRF protection: cryptographic nonce tied to user_id
    nonce = secrets.token_urlsafe(16)
    state = f"uid_{user_id:06d}_{nonce}"
    # Store nonce for validation on callback
    from bot.services.google_auth import _store_oauth_state
    _store_oauth_state(user_id, nonce)
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": WHOOP_SCOPES,
        "state": state,
    })
    return f"{WHOOP_AUTH_URL}?{params}"


def exchange_code(user_id: int, code: str) -> tuple[bool, str]:
    """Exchange authorization code for tokens and store in DB.
    Returns (success, error_message)."""
    client_id = _get_client_id()
    client_secret = _get_client_secret()
    redirect_uri = _get_redirect_uri()

    logger.info(f"WHOOP token exchange: redirect_uri={redirect_uri}")

    try:
        resp = _http.post(
            WHOOP_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if resp.status_code != 200:
            logger.error(f"WHOOP token exchange failed: HTTP {resp.status_code}")
            return False, f"Token exchange failed (HTTP {resp.status_code})"

        token_data = resp.json()
    except Exception as e:
        logger.error(f"WHOOP token exchange failed: {e}")
        return False, str(e)

    logger.info(f"WHOOP token response keys: {list(token_data.keys())}")

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3600)
    scopes = token_data.get("scope", "")

    if not access_token:
        logger.error(f"WHOOP token response missing access_token (keys: {list(token_data.keys())})")
        return False, "No access_token in WHOOP response"

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    # Get WHOOP user ID
    whoop_user_id = None
    try:
        profile = _api_get("/user/profile/basic", access_token)
        if profile:
            whoop_user_id = profile.get("user_id")
    except Exception:
        logger.warning(f"WHOOP profile fetch failed for user {user_id} (non-fatal)")

    try:
        with get_cursor() as cur:
            cur.execute(
                """INSERT INTO whoop_tokens (user_id, access_token, refresh_token, expires_at, scopes, whoop_user_id)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (user_id) DO UPDATE SET
                     access_token = EXCLUDED.access_token,
                     refresh_token = EXCLUDED.refresh_token,
                     expires_at = EXCLUDED.expires_at,
                     scopes = EXCLUDED.scopes,
                     whoop_user_id = EXCLUDED.whoop_user_id,
                     updated_at = NOW()""",
                (user_id, access_token, refresh_token, expires_at, scopes, whoop_user_id),
            )
    except Exception as e:
        logger.error(f"WHOOP token storage failed for user {user_id}: {e}")
        return False, "Failed to store tokens. Please try again."

    logger.info(f"WHOOP tokens stored for user {user_id}")
    return True, ""


def _refresh_tokens(user_id: int, refresh_token: str) -> str | None:
    """Refresh expired access token. Returns new access_token or None."""
    client_id = _get_client_id()
    client_secret = _get_client_secret()

    try:
        resp = _http.post(
            WHOOP_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "offline",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if resp.status_code != 200:
            logger.error(f"WHOOP token refresh HTTP {resp.status_code}: {resp.text[:200]}")
            return None

        token_data = resp.json()
    except Exception as e:
        logger.error(f"WHOOP token refresh failed for user {user_id}: {e}")
        return None

    new_access = token_data.get("access_token")
    new_refresh = token_data.get("refresh_token", refresh_token)
    expires_in = token_data.get("expires_in", 3600)

    if not new_access:
        return None

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    with get_cursor() as cur:
        cur.execute(
            """UPDATE whoop_tokens
               SET access_token = %s, refresh_token = %s, expires_at = %s, updated_at = NOW()
               WHERE user_id = %s""",
            (new_access, new_refresh, expires_at, user_id),
        )

    return new_access


def get_access_token(user_id: int) -> str | None:
    """Get valid access token, auto-refreshing if expired."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT access_token, refresh_token, expires_at FROM whoop_tokens WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            return None

    # Check if expired (with 5-min buffer)
    if row["expires_at"] and row["expires_at"] < datetime.now(timezone.utc) + timedelta(minutes=5):
        if row["refresh_token"]:
            logger.info(f"WHOOP token expired for user {user_id}, refreshing...")
            new_token = _refresh_tokens(user_id, row["refresh_token"])
            if new_token:
                logger.info(f"WHOOP token refreshed for user {user_id}")
            else:
                logger.error(f"WHOOP token refresh FAILED for user {user_id}")
            return new_token
        logger.warning(f"WHOOP token expired for user {user_id} but no refresh_token")
        return None

    return row["access_token"]


def is_connected(user_id: int) -> bool:
    """Check if user has WHOOP linked."""
    with get_cursor() as cur:
        cur.execute("SELECT id FROM whoop_tokens WHERE user_id = %s", (user_id,))
        return cur.fetchone() is not None


def revoke_access(user_id: int) -> bool:
    """Disconnect WHOOP."""
    with get_cursor() as cur:
        cur.execute("DELETE FROM whoop_tokens WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM whoop_daily WHERE user_id = %s", (user_id,))
    return True


# --- API helpers ---

def _api_get(path: str, access_token: str, params: dict = None) -> dict | None:
    """Make authenticated GET request to WHOOP API."""
    url = f"{WHOOP_API_BASE}{path}"

    try:
        resp = _http.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code == 401:
            logger.error(f"WHOOP API {path} 401 Unauthorized — token likely expired or revoked")
            return None
        if resp.status_code != 200:
            logger.error(f"WHOOP API {path} HTTP {resp.status_code}: {resp.text[:300]}")
            return None
        data = resp.json()
        record_count = len(data.get("records", [])) if isinstance(data, dict) else 0
        logger.info(f"WHOOP API {path} OK: {record_count} records")
        return data
    except Exception as e:
        logger.error(f"WHOOP API {path} failed: {e}")
        return None


# --- Data sync ---

def sync_recovery(user_id: int) -> dict | None:
    """Fetch latest scored recovery from WHOOP and store."""
    token = get_access_token(user_id)
    if not token:
        logger.warning(f"WHOOP sync_recovery: no token for user {user_id}")
        return None

    data = _api_get("/recovery", token, {"limit": 5})
    if not data or not data.get("records"):
        logger.warning(f"WHOOP sync_recovery: no records for user {user_id}")
        return None

    # Find first SCORED record (latest might be PENDING_SCORE)
    rec = None
    for r in data["records"]:
        state = r.get("score_state", "unknown")
        if state == "SCORED" and r.get("score"):
            rec = r
            break

    if not rec:
        states = [r.get("score_state", "?") for r in data["records"]]
        logger.info(f"WHOOP sync_recovery: no scored records for user {user_id}, states={states}")
        return None

    score = rec["score"]
    recovery_score = score.get("recovery_score")
    hrv = score.get("hrv_rmssd_milli")
    rhr = score.get("resting_heart_rate")
    spo2 = score.get("spo2_percentage")
    skin_temp = score.get("skin_temp_celsius")

    cycle_date = date.today()
    if rec.get("created_at"):
        try:
            cycle_date = datetime.fromisoformat(rec["created_at"].replace("Z", "+00:00")).date()
        except Exception as e:
            logger.warning(f"WHOOP recovery date parse failed: {rec.get('created_at')} — {e}")

    logger.info(
        f"WHOOP recovery synced: user={user_id} date={cycle_date} "
        f"score={recovery_score} hrv={hrv} rhr={rhr} spo2={spo2}"
    )

    _upsert_daily(user_id, cycle_date,
                   recovery_score=recovery_score, hrv_rmssd=hrv,
                   resting_hr=rhr, spo2=spo2, skin_temp=skin_temp)

    return {
        "recovery_score": recovery_score,
        "hrv_rmssd": hrv,
        "resting_hr": rhr,
        "spo2": spo2,
        "skin_temp": skin_temp,
        "date": cycle_date.isoformat(),
    }


def sync_sleep(user_id: int) -> dict | None:
    """Fetch latest scored sleep data from WHOOP and store."""
    token = get_access_token(user_id)
    if not token:
        logger.warning(f"WHOOP sync_sleep: no token for user {user_id}")
        return None

    data = _api_get("/activity/sleep", token, {"limit": 5})
    if not data or not data.get("records"):
        logger.warning(f"WHOOP sync_sleep: no records for user {user_id}")
        return None

    # Find first SCORED record
    rec = None
    for r in data["records"]:
        state = r.get("score_state", "unknown")
        if state == "SCORED" and r.get("score"):
            rec = r
            break

    if not rec:
        states = [r.get("score_state", "?") for r in data["records"]]
        logger.info(f"WHOOP sync_sleep: no scored records for user {user_id}, states={states}")
        return None

    score = rec["score"]
    sleep_performance = score.get("sleep_performance_percentage")
    sleep_efficiency = score.get("sleep_efficiency_percentage")
    stage = score.get("stage_summary", {})
    deep = stage.get("total_slow_wave_sleep_time_milli")
    rem = stage.get("total_rem_sleep_time_milli")
    light = stage.get("total_light_sleep_time_milli")
    resp_rate = score.get("respiratory_rate")

    # Convert ms to minutes
    deep_min = round(deep / 60000) if deep else None
    rem_min = round(rem / 60000) if rem else None
    light_min = round(light / 60000) if light else None

    cycle_date = date.today()
    if rec.get("created_at"):
        try:
            cycle_date = datetime.fromisoformat(rec["created_at"].replace("Z", "+00:00")).date()
        except Exception as e:
            logger.warning(f"WHOOP sleep date parse failed: {rec.get('created_at')} — {e}")

    logger.info(
        f"WHOOP sleep synced: user={user_id} date={cycle_date} "
        f"perf={sleep_performance}% deep={deep_min}min rem={rem_min}min"
    )

    _upsert_daily(user_id, cycle_date,
                   sleep_performance=sleep_performance,
                   sleep_efficiency=sleep_efficiency,
                   deep_sleep_minutes=deep_min,
                   rem_sleep_minutes=rem_min,
                   light_sleep_minutes=light_min,
                   respiratory_rate=resp_rate)

    return {
        "sleep_performance": sleep_performance,
        "sleep_efficiency": sleep_efficiency,
        "deep_sleep_minutes": deep_min,
        "rem_sleep_minutes": rem_min,
        "light_sleep_minutes": light_min,
        "respiratory_rate": resp_rate,
        "date": cycle_date.isoformat(),
    }


def sync_strain(user_id: int) -> dict | None:
    """Fetch latest scored cycle/strain data from WHOOP and store."""
    token = get_access_token(user_id)
    if not token:
        logger.warning(f"WHOOP sync_strain: no token for user {user_id}")
        return None

    data = _api_get("/cycle", token, {"limit": 5})
    if not data or not data.get("records"):
        logger.warning(f"WHOOP sync_strain: no records for user {user_id}")
        return None

    # Find first SCORED record
    rec = None
    for r in data["records"]:
        state = r.get("score_state", "unknown")
        if state == "SCORED" and r.get("score"):
            rec = r
            break

    if not rec:
        states = [r.get("score_state", "?") for r in data["records"]]
        logger.info(f"WHOOP sync_strain: no scored records for user {user_id}, states={states}")
        return None

    score = rec["score"]
    strain = score.get("strain")
    calories = score.get("kilojoule")

    cycle_date = date.today()
    if rec.get("start"):
        try:
            cycle_date = datetime.fromisoformat(rec["start"].replace("Z", "+00:00")).date()
        except Exception as e:
            logger.warning(f"WHOOP strain date parse failed: {rec.get('start')} — {e}")

    logger.info(f"WHOOP strain synced: user={user_id} date={cycle_date} strain={strain} cal={calories}")

    _upsert_daily(user_id, cycle_date,
                   daily_strain=strain, calories_kj=calories)

    return {
        "daily_strain": strain,
        "calories_kj": calories,
        "date": cycle_date.isoformat(),
    }


def sync_all(user_id: int) -> dict:
    """Sync recovery, sleep, and strain. Returns combined result."""
    recovery = sync_recovery(user_id)
    sleep = sync_sleep(user_id)
    strain = sync_strain(user_id)

    synced = sum(1 for x in [recovery, sleep, strain] if x)
    logger.info(
        f"WHOOP sync_all: user={user_id} {synced}/3 "
        f"(recovery={'OK' if recovery else 'MISS'}, "
        f"sleep={'OK' if sleep else 'MISS'}, "
        f"strain={'OK' if strain else 'MISS'})"
    )

    return {
        "recovery": recovery,
        "sleep": sleep,
        "strain": strain,
    }


_ALLOWED_DAILY_FIELDS = {
    "recovery_score", "hrv_rmssd", "resting_hr", "spo2", "skin_temp",
    "sleep_performance", "sleep_efficiency", "deep_sleep_minutes",
    "rem_sleep_minutes", "light_sleep_minutes", "respiratory_rate",
    "daily_strain", "calories_kj",
}


def _upsert_daily(user_id: int, cycle_date: date, **kwargs):
    """Upsert whoop_daily row with given fields (whitelist-validated).

    Uses INSERT ... ON CONFLICT to avoid race conditions between
    concurrent sync calls for the same user+date.
    """
    # Filter out None values and validate against whitelist
    fields = {k: v for k, v in kwargs.items() if v is not None and k in _ALLOWED_DAILY_FIELDS}
    if not fields:
        return

    with get_cursor() as cur:
        # Build atomic upsert — safe against concurrent inserts
        all_fields = {"user_id": user_id, "cycle_date": cycle_date, **fields}
        cols = ", ".join(all_fields.keys())
        placeholders = ", ".join(["%s"] * len(all_fields))
        # On conflict, only update the data fields (not user_id/cycle_date)
        update_clause = ", ".join(f"{k} = EXCLUDED.{k}" for k in fields)
        cur.execute(
            f"""INSERT INTO whoop_daily ({cols}) VALUES ({placeholders})
                ON CONFLICT (user_id, cycle_date) DO UPDATE SET {update_clause}""",
            list(all_fields.values()),
        )


# --- Data access ---

def get_today_recovery(user_id: int) -> dict | None:
    """Get today's recovery data (from DB, syncs if stale)."""
    today = date.today()

    with get_cursor() as cur:
        cur.execute(
            """SELECT * FROM whoop_daily
               WHERE user_id = %s AND cycle_date >= %s
               ORDER BY cycle_date DESC LIMIT 1""",
            (user_id, today - timedelta(days=1)),
        )
        row = cur.fetchone()

    if not row:
        # Try syncing fresh data
        sync_all(user_id)
        with get_cursor() as cur:
            cur.execute(
                """SELECT * FROM whoop_daily
                   WHERE user_id = %s AND cycle_date >= %s
                   ORDER BY cycle_date DESC LIMIT 1""",
                (user_id, today - timedelta(days=1)),
            )
            row = cur.fetchone()

    return dict(row) if row else None


def get_whoop_trends(user_id: int, days: int = 14) -> dict:
    """Get WHOOP trends over N days."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT * FROM whoop_daily
               WHERE user_id = %s AND cycle_date >= %s
               ORDER BY cycle_date DESC""",
            (user_id, date.today() - timedelta(days=days)),
        )
        rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        return {"days": 0}

    # Calculate averages and trends
    recoveries = [r["recovery_score"] for r in rows if r.get("recovery_score") is not None]
    hrvs = [r["hrv_rmssd"] for r in rows if r.get("hrv_rmssd") is not None]
    rhrs = [r["resting_hr"] for r in rows if r.get("resting_hr") is not None]
    sleeps = [r["sleep_performance"] for r in rows if r.get("sleep_performance") is not None]
    strains = [r["daily_strain"] for r in rows if r.get("daily_strain") is not None]

    def _avg(lst):
        return round(sum(lst) / len(lst), 1) if lst else None

    def _trend(lst):
        if len(lst) < 3:
            return "insufficient_data"
        recent = lst[:len(lst)//2]
        older = lst[len(lst)//2:]
        r_avg = sum(recent) / len(recent)
        o_avg = sum(older) / len(older)
        diff_pct = ((r_avg - o_avg) / o_avg * 100) if o_avg != 0 else 0
        if diff_pct > 5:
            return "trending_up"
        elif diff_pct < -5:
            return "trending_down"
        return "stable"

    return {
        "days": len(rows),
        "recovery_avg": _avg(recoveries),
        "recovery_trend": _trend(recoveries),
        "hrv_avg": _avg(hrvs),
        "hrv_trend": _trend(hrvs),
        "rhr_avg": _avg(rhrs),
        "rhr_trend": _trend(rhrs),
        "sleep_avg": _avg(sleeps),
        "sleep_trend": _trend(sleeps),
        "strain_avg": _avg(strains),
        "strain_trend": _trend(strains),
    }


def get_recovery_zone(score: int | None) -> str:
    """Classify recovery score into zone."""
    if score is None:
        return "unknown"
    if score >= 67:
        return "green"
    elif score >= 34:
        return "yellow"
    return "red"


def get_whoop_summary(user_id: int) -> dict:
    """Everything the AI brain needs for WHOOP context."""
    today_data = get_today_recovery(user_id)
    trends = get_whoop_trends(user_id, days=7)
    connected = is_connected(user_id)

    return {
        "connected": connected,
        "today": today_data,
        "trends": trends,
    }


def get_whoop_summary_cached(user_id: int) -> dict:
    """DB-only WHOOP summary — never syncs via HTTP. For system prompt building."""
    today = date.today()
    with get_cursor() as cur:
        cur.execute(
            """SELECT *,
               EXTRACT(EPOCH FROM (NOW() - COALESCE(updated_at, created_at))) / 60.0
                   AS data_age_minutes
               FROM whoop_daily
               WHERE user_id = %s AND cycle_date >= %s
               ORDER BY cycle_date DESC LIMIT 1""",
            (user_id, today - timedelta(days=1)),
        )
        row = cur.fetchone()
    today_data = dict(row) if row else None
    trends = get_whoop_trends(user_id, days=7)
    return {"connected": True, "today": today_data, "trends": trends}


# --- Cross-domain insights ---

def get_whoop_insights(user_id: int) -> list[str]:
    """Compute cross-domain correlations from WHOOP + fitness + biohacking data.

    Returns list of plain-English insight strings for AI coaching.
    All queries: bounded to 30 days, indexed columns, HAVING COUNT >= 2.
    """
    insights: list[str] = []

    try:
        with get_cursor() as cur:
            # 1. Recovery vs previous-day strain
            cur.execute("""
                SELECT
                    CASE WHEN prev.daily_strain >= 14 THEN 'high'
                         WHEN prev.daily_strain < 8 THEN 'low'
                         ELSE 'moderate' END AS bucket,
                    ROUND(AVG(curr.recovery_score)) AS avg_rec,
                    COUNT(*) AS days
                FROM whoop_daily curr
                JOIN whoop_daily prev
                    ON prev.user_id = curr.user_id
                    AND prev.cycle_date = curr.cycle_date - 1
                WHERE curr.user_id = %s
                    AND curr.recovery_score IS NOT NULL
                    AND prev.daily_strain IS NOT NULL
                    AND curr.cycle_date >= CURRENT_DATE - 30
                GROUP BY bucket
                HAVING COUNT(*) >= 2
            """, (user_id,))
            rows = {r["bucket"]: r for r in cur.fetchall()}
            if "high" in rows and "low" in rows:
                high = int(rows["high"]["avg_rec"])
                low = int(rows["low"]["avg_rec"])
                diff = low - high
                if diff > 5:
                    insights.append(
                        f"Recovery after heavy strain days (14+) averages {high}% "
                        f"vs {low}% after easy days — {diff}pt drop. "
                        f"Space out high-strain sessions."
                    )

            # 2. Recovery vs sleep quality
            cur.execute("""
                SELECT
                    CASE WHEN sleep_performance >= 85 THEN 'good'
                         WHEN sleep_performance < 70 THEN 'poor'
                         ELSE 'ok' END AS bucket,
                    ROUND(AVG(recovery_score)) AS avg_rec,
                    COUNT(*) AS days
                FROM whoop_daily
                WHERE user_id = %s
                    AND recovery_score IS NOT NULL
                    AND sleep_performance IS NOT NULL
                    AND cycle_date >= CURRENT_DATE - 30
                GROUP BY bucket
                HAVING COUNT(*) >= 2
            """, (user_id,))
            rows = {r["bucket"]: r for r in cur.fetchall()}
            if "good" in rows and "poor" in rows:
                good = int(rows["good"]["avg_rec"])
                poor = int(rows["poor"]["avg_rec"])
                diff = good - poor
                if diff > 5:
                    insights.append(
                        f"Recovery is {diff}pts higher on good sleep nights "
                        f"({good}%) vs poor sleep ({poor}%). "
                        f"Sleep quality is the biggest lever."
                    )

            # 3. Recovery on peptide dosing vs non-dosing days
            cur.execute("""
                SELECT
                    CASE WHEN pl.id IS NOT NULL THEN 'dosing' ELSE 'off' END AS status,
                    ROUND(AVG(wd.recovery_score)) AS avg_rec,
                    ROUND(AVG(wd.hrv_rmssd)::numeric, 1) AS avg_hrv,
                    COUNT(DISTINCT wd.cycle_date) AS days
                FROM whoop_daily wd
                LEFT JOIN peptide_logs pl
                    ON pl.user_id = wd.user_id
                    AND pl.administered_at::date = wd.cycle_date
                WHERE wd.user_id = %s
                    AND wd.recovery_score IS NOT NULL
                    AND wd.cycle_date >= CURRENT_DATE - 30
                GROUP BY status
                HAVING COUNT(DISTINCT wd.cycle_date) >= 3
            """, (user_id,))
            rows = {r["status"]: r for r in cur.fetchall()}
            if "dosing" in rows and "off" in rows:
                dose_rec = int(rows["dosing"]["avg_rec"])
                off_rec = int(rows["off"]["avg_rec"])
                diff = dose_rec - off_rec
                if abs(diff) > 3:
                    direction = "higher" if diff > 0 else "lower"
                    dose_hrv = rows["dosing"]["avg_hrv"] or 0
                    off_hrv = rows["off"]["avg_hrv"] or 0
                    insights.append(
                        f"Recovery on peptide dosing days: {dose_rec}% (HRV {dose_hrv}ms) "
                        f"vs off days: {off_rec}% (HRV {off_hrv}ms) — "
                        f"{abs(diff)}pts {direction} with peptides."
                    )

            # 4. Deep sleep after high vs low strain
            cur.execute("""
                SELECT
                    CASE WHEN prev.daily_strain >= 14 THEN 'high'
                         ELSE 'low' END AS bucket,
                    ROUND(AVG(curr.deep_sleep_minutes)) AS avg_deep,
                    COUNT(*) AS days
                FROM whoop_daily curr
                JOIN whoop_daily prev
                    ON prev.user_id = curr.user_id
                    AND prev.cycle_date = curr.cycle_date - 1
                WHERE curr.user_id = %s
                    AND curr.deep_sleep_minutes IS NOT NULL
                    AND prev.daily_strain IS NOT NULL
                    AND curr.cycle_date >= CURRENT_DATE - 30
                GROUP BY bucket
                HAVING COUNT(*) >= 2
            """, (user_id,))
            rows = {r["bucket"]: r for r in cur.fetchall()}
            if "high" in rows and "low" in rows:
                deep_hard = int(rows["high"]["avg_deep"])
                deep_easy = int(rows["low"]["avg_deep"])
                diff = deep_hard - deep_easy
                if abs(diff) > 5:
                    if diff > 0:
                        insights.append(
                            f"Deep sleep after heavy training: {deep_hard}min "
                            f"vs easy days: {deep_easy}min (+{diff}min). "
                            f"Body recovers well through sleep after hard sessions."
                        )
                    else:
                        insights.append(
                            f"Deep sleep after heavy training: {deep_hard}min "
                            f"vs easy days: {deep_easy}min ({diff}min). "
                            f"Hard training may be disrupting deep sleep."
                        )

            # 5. HRV vs 14-day baseline
            cur.execute("""
                SELECT
                    AVG(hrv_rmssd) AS baseline,
                    (SELECT hrv_rmssd FROM whoop_daily
                     WHERE user_id = %s AND hrv_rmssd IS NOT NULL
                     ORDER BY cycle_date DESC LIMIT 1) AS latest
                FROM whoop_daily
                WHERE user_id = %s
                    AND hrv_rmssd IS NOT NULL
                    AND cycle_date >= CURRENT_DATE - 14
            """, (user_id, user_id))
            row = cur.fetchone()
            if row and row["baseline"] and row["latest"]:
                baseline = round(float(row["baseline"]), 1)
                latest = round(float(row["latest"]), 1)
                if baseline > 0:
                    pct = ((latest - baseline) / baseline) * 100
                    if pct < -15:
                        insights.append(
                            f"HRV today ({latest}ms) is {abs(round(pct))}% below "
                            f"14d baseline ({baseline}ms). Fatigue signal — prioritize recovery."
                        )
                    elif pct > 15:
                        insights.append(
                            f"HRV today ({latest}ms) is {round(pct)}% above "
                            f"14d baseline ({baseline}ms). Excellent readiness — can push intensity."
                        )

            # 6. Recovery after consecutive training vs rest days
            cur.execute("""
                WITH training_days AS (
                    SELECT created_at::date AS wd
                    FROM workouts
                    WHERE user_id = %s AND created_at >= CURRENT_DATE - 30
                ),
                day_ctx AS (
                    SELECT wd.cycle_date, wd.recovery_score,
                        CASE WHEN t1.wd IS NOT NULL THEN 1 ELSE 0 END AS prev_trained,
                        CASE WHEN t2.wd IS NOT NULL THEN 1 ELSE 0 END AS prev2_trained
                    FROM whoop_daily wd
                    LEFT JOIN training_days t1 ON t1.wd = wd.cycle_date - 1
                    LEFT JOIN training_days t2 ON t2.wd = wd.cycle_date - 2
                    WHERE wd.user_id = %s AND wd.recovery_score IS NOT NULL
                        AND wd.cycle_date >= CURRENT_DATE - 30
                )
                SELECT
                    CASE
                        WHEN prev_trained = 1 AND prev2_trained = 1 THEN 'consecutive'
                        WHEN prev_trained = 0 THEN 'after_rest'
                        ELSE 'other'
                    END AS ctx,
                    ROUND(AVG(recovery_score)) AS avg_rec,
                    COUNT(*) AS days
                FROM day_ctx
                GROUP BY ctx
                HAVING COUNT(*) >= 2
            """, (user_id, user_id))
            rows = {r["ctx"]: r for r in cur.fetchall()}
            if "consecutive" in rows and "after_rest" in rows:
                consec = int(rows["consecutive"]["avg_rec"])
                rest = int(rows["after_rest"]["avg_rec"])
                diff = rest - consec
                if diff > 5:
                    insights.append(
                        f"Recovery after rest days: {rest}% vs after "
                        f"back-to-back training: {consec}% ({diff}pt drop). "
                        f"Consider adding rest days between hard sessions."
                    )

    except Exception as e:
        logger.warning(f"WHOOP insights failed for user {user_id}: {type(e).__name__}: {e}")

    return insights


# --- Workout-Recovery Analysis Algorithm ---

def get_whoop_for_date(user_id: int, target_date: date) -> dict | None:
    """Get WHOOP data for a specific date (or closest prior day)."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT * FROM whoop_daily
               WHERE user_id = %s AND cycle_date BETWEEN %s AND %s
               ORDER BY cycle_date DESC LIMIT 1""",
            (user_id, target_date - timedelta(days=1), target_date),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def get_multi_day_strain(user_id: int, days: int = 3) -> list[float]:
    """Get daily strain values for the last N days (most recent first)."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT daily_strain FROM whoop_daily
               WHERE user_id = %s AND daily_strain IS NOT NULL
               AND cycle_date >= %s
               ORDER BY cycle_date DESC LIMIT %s""",
            (user_id, date.today() - timedelta(days=days + 1), days),
        )
        return [row["daily_strain"] for row in cur.fetchall()]


def _classify_workout_intensity(exercises: list, session_rpe: float | None = None) -> dict:
    """Classify workout intensity from exercise data.

    Returns:
        dict with keys: level (high/moderate/low), score (0-100),
        has_heavy_compounds, compound_count, total_sets, reasoning.
    """
    if not exercises:
        return {"level": "low", "score": 20, "has_heavy_compounds": False,
                "compound_count": 0, "total_sets": 0, "reasoning": "No exercise data"}

    # Heavy compound patterns (CNS-demanding)
    heavy_patterns = {"squat", "hinge", "horizontal_push", "vertical_push"}
    # All compound patterns
    compound_patterns = heavy_patterns | {"horizontal_pull", "vertical_pull"}

    total_sets = 0
    compound_count = 0
    heavy_compound_count = 0
    has_heavy_weight = False
    max_rpe = session_rpe or 0
    intensity_signals = []

    for ex in exercises:
        sets = ex.get("sets") or ex.get("target_sets") or 0
        total_sets += sets
        pattern = ex.get("movement_pattern") or ex.get("pattern")

        if pattern in compound_patterns:
            compound_count += 1
        if pattern in heavy_patterns:
            heavy_compound_count += 1

        # Weight analysis — heavy if above typical bodyweight-relative thresholds
        weight = ex.get("weight") or ex.get("target_weight")
        if weight and weight > 60:
            has_heavy_weight = True

        # Rep analysis — low reps + heavy weight = strength (high intensity)
        reps_str = str(ex.get("reps") or ex.get("target_reps") or "")
        try:
            reps = int(reps_str.split("-")[0]) if reps_str else 0
        except (ValueError, IndexError):
            reps = 8  # default assumption

        if reps <= 5 and weight and weight > 40:
            intensity_signals.append("strength")
        elif 6 <= reps <= 12:
            intensity_signals.append("hypertrophy")
        elif reps > 12:
            intensity_signals.append("endurance")

        ex_rpe = ex.get("rpe") or ex.get("target_rpe")
        if ex_rpe:
            try:
                max_rpe = max(max_rpe, float(ex_rpe))
            except (ValueError, TypeError):
                pass

    # Score calculation (0-100)
    score = 0

    # Compound density (0-35 points)
    score += min(heavy_compound_count * 12, 35)

    # Volume load (0-25 points)
    score += min(total_sets * 1.5, 25)

    # Intensity signals (0-20 points)
    strength_ratio = intensity_signals.count("strength") / max(len(intensity_signals), 1)
    score += strength_ratio * 20

    # Heavy weight bonus (0-10 points)
    if has_heavy_weight:
        score += 10

    # RPE modifier (0-10 points)
    if max_rpe >= 8:
        score += 10
    elif max_rpe >= 6:
        score += 5

    score = min(100, score)

    # Classify
    if score >= 65:
        level = "high"
    elif score >= 35:
        level = "moderate"
    else:
        level = "low"

    reasoning = []
    if heavy_compound_count >= 2:
        reasoning.append(f"{heavy_compound_count} heavy compounds")
    if has_heavy_weight:
        reasoning.append("heavy loading")
    if strength_ratio > 0.5:
        reasoning.append("strength-rep ranges")
    if total_sets >= 20:
        reasoning.append(f"high volume ({total_sets} sets)")
    if max_rpe >= 8:
        reasoning.append(f"high RPE ({max_rpe})")
    if not reasoning:
        reasoning.append("moderate or light work")

    return {
        "level": level,
        "score": round(score),
        "has_heavy_compounds": heavy_compound_count > 0,
        "compound_count": compound_count,
        "total_sets": total_sets,
        "max_rpe": max_rpe,
        "reasoning": ", ".join(reasoning),
    }


# Alignment matrix: (recovery_zone, workout_intensity) -> (score, verdict, recommendation)
_ALIGNMENT_MATRIX = {
    ("green", "high"):     (95, "dialed_in", "Perfect match — you pushed hard on a green day. This is how you make gains."),
    ("green", "moderate"): (65, "undertrained", "Recovery was green — you could've gone heavier. Next green day, load up the compounds."),
    ("green", "low"):      (40, "missed_opportunity", "Green day wasted on light work. Your body was ready for heavy lifting."),
    ("yellow", "high"):    (35, "overreached", "Yellow recovery but you went heavy. Next time in yellow, drop intensity 10-15% and keep volume."),
    ("yellow", "moderate"):(90, "dialed_in", "Good read on your body. Moderate effort on a yellow day is textbook."),
    ("yellow", "low"):     (70, "cautious_ok", "Conservative but fine. You could've done moderate compounds safely."),
    ("red", "high"):       (15, "reckless", "Red recovery and heavy session — that's how injuries happen. Red days = mobility, Zone 1, or rest."),
    ("red", "moderate"):   (35, "overreached", "Body was in red but you pushed moderate. Should've been light mobility or rest."),
    ("red", "low"):        (90, "smart", "Smart call. Low intensity on a red day lets your body actually recover."),
}


def analyze_workout_vs_recovery(user_id: int, workout: dict, exercises: list,
                                  workout_date: date = None) -> dict:
    """Core algorithm: analyze a workout against WHOOP recovery data.

    Args:
        user_id: User ID.
        workout: Workout dict (from workouts table).
        exercises: List of exercise dicts.
        workout_date: Date of workout (defaults to today).

    Returns:
        dict with alignment_score, verdict, recovery_context, workout_intensity,
        what_was_good, what_to_change, alternative_session.
    """
    workout_date = workout_date or date.today()

    # --- Gather WHOOP data ---
    whoop_data = get_whoop_for_date(user_id, workout_date)
    if not whoop_data:
        return {"error": "No WHOOP data available for this date. Sync your WHOOP first."}

    recovery_score = whoop_data.get("recovery_score")
    zone = get_recovery_zone(recovery_score)
    hrv = whoop_data.get("hrv_rmssd")
    rhr = whoop_data.get("resting_hr")
    sleep_perf = whoop_data.get("sleep_performance")
    deep_sleep = whoop_data.get("deep_sleep_minutes")
    prev_strain = whoop_data.get("daily_strain")

    # Get trends for modifiers
    trends = get_whoop_trends(user_id, days=7)
    hrv_avg = trends.get("hrv_avg")
    hrv_trend = trends.get("hrv_trend")
    strain_history = get_multi_day_strain(user_id, days=3)

    # --- Classify workout intensity ---
    intensity = _classify_workout_intensity(exercises, workout.get("rpe"))

    # --- Base alignment from matrix ---
    key = (zone, intensity["level"])
    base_score, verdict, base_rec = _ALIGNMENT_MATRIX.get(
        key, (50, "unknown", "Not enough data to fully assess.")
    )

    # --- Apply modifiers ---
    modifiers = []
    adjusted_score = base_score

    # Modifier 1: HRV trend (down for 3+ days = more caution needed)
    if hrv_trend == "trending_down":
        if intensity["level"] in ("high", "moderate"):
            adjusted_score -= 10
            modifiers.append("HRV trending down — body accumulating fatigue")

    # Modifier 2: Sleep quality gate
    if sleep_perf is not None and sleep_perf < 70:
        if intensity["has_heavy_compounds"]:
            adjusted_score -= 10
            modifiers.append(f"Sleep was {sleep_perf}% — heavy compounds risky on poor sleep")

    # Modifier 3: Deep sleep gate (CNS recovery)
    if deep_sleep is not None and deep_sleep < 45:
        if intensity["has_heavy_compounds"]:
            adjusted_score -= 8
            modifiers.append(f"Only {deep_sleep}min deep sleep — CNS not fully recovered for heavy work")

    # Modifier 4: Cumulative strain (3-day average > 14 = high load)
    if len(strain_history) >= 2:
        avg_strain = sum(strain_history) / len(strain_history)
        if avg_strain > 14 and intensity["level"] == "high":
            adjusted_score -= 10
            modifiers.append(f"High cumulative strain (avg {avg_strain:.1f} over {len(strain_history)}d)")

    # Modifier 5: HRV significantly below personal baseline
    if hrv is not None and hrv_avg is not None and hrv_avg > 0:
        hrv_deficit_pct = ((hrv_avg - hrv) / hrv_avg) * 100
        if hrv_deficit_pct > 15 and intensity["level"] != "low":
            adjusted_score -= 8
            modifiers.append(f"HRV {hrv_deficit_pct:.0f}% below your 7d average")

    # Positive modifier: nailed it despite modifiers
    if zone == "green" and intensity["level"] == "high" and not modifiers:
        adjusted_score = min(adjusted_score + 5, 100)

    adjusted_score = max(0, min(100, adjusted_score))

    # --- Build recommendations ---
    what_was_good = []
    what_to_change = []
    alternative = None

    if verdict == "dialed_in":
        what_was_good.append("Workout intensity matched your recovery state")
        if intensity["has_heavy_compounds"]:
            what_was_good.append("Good compound selection for a strong recovery day")
        if intensity["total_sets"] >= 15:
            what_was_good.append("Solid volume — you're accumulating stimulus")

    elif verdict == "undertrained":
        what_to_change.append("Add 1-2 heavy compound movements on green days")
        what_to_change.append("Push RPE to 8-9 on top sets when recovery is green")
        alternative = "heavy compound session (squat or deadlift + secondary compound + accessories)"

    elif verdict == "missed_opportunity":
        what_to_change.append("Green recovery is your window for heavy lifting — don't waste it on light work")
        what_to_change.append("If you needed a break mentally, that's valid — but physically you were ready")
        alternative = "full strength session with ascending loads on primary compound"

    elif verdict == "overreached":
        what_to_change.append("Scale back to RPE 6-7 when in yellow/red zone")
        if intensity["has_heavy_compounds"]:
            what_to_change.append("Swap heavy compounds for moderate-weight, higher-rep work")
        alternative = "moderate hypertrophy session — same movements, lighter weight, higher reps"

    elif verdict == "reckless":
        what_to_change.append("Red days = mobility, Zone 1 cardio, or full rest. Non-negotiable.")
        what_to_change.append("Heavy training on red recovery increases injury risk and delays recovery")
        alternative = "20min Zone 1 cardio + mobility circuit + rotational work"

    elif verdict == "smart":
        what_was_good.append("You respected your recovery state")
        what_was_good.append("Light work on red days accelerates recovery without adding stress")

    elif verdict == "cautious_ok":
        what_was_good.append("Playing it safe is fine, but you had room for moderate work")
        what_to_change.append("Yellow doesn't mean stop — moderate compounds at RPE 6-7 are safe")

    # Add modifier-based recommendations
    for mod in modifiers:
        what_to_change.append(mod)

    return {
        "alignment_score": adjusted_score,
        "verdict": verdict,
        "recovery_context": {
            "recovery_score": recovery_score,
            "zone": zone,
            "hrv": hrv,
            "hrv_avg_7d": hrv_avg,
            "hrv_trend": hrv_trend,
            "sleep_performance": sleep_perf,
            "deep_sleep_min": deep_sleep,
            "previous_day_strain": prev_strain,
        },
        "workout_intensity": intensity,
        "what_was_good": what_was_good,
        "what_to_change": what_to_change,
        "alternative_session": alternative,
        "modifiers_applied": modifiers,
        "date": workout_date.isoformat(),
    }


# --- Webhook handling ---

def verify_webhook_signature(body: bytes, signature: str, timestamp: str) -> bool:
    """Verify WHOOP webhook HMAC-SHA256 signature."""
    secret = _get_client_secret()
    if not secret:
        logger.error("WHOOP webhook rejected: no client secret configured (fail-closed)")
        return False  # Fail closed — reject webhooks when we can't verify them

    expected = hmac.new(
        secret.encode(),
        timestamp.encode() + body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


def handle_webhook(event_type: str, whoop_user_id: int, data_id: str = None) -> bool:
    """Process a WHOOP webhook event (v2: data_id is UUID string)."""
    # Find our user_id from whoop_user_id
    with get_cursor() as cur:
        cur.execute(
            "SELECT user_id FROM whoop_tokens WHERE whoop_user_id = %s",
            (whoop_user_id,),
        )
        row = cur.fetchone()
        if not row:
            logger.warning(f"WHOOP webhook for unknown whoop_user_id {whoop_user_id}")
            return False
        user_id = row["user_id"]

    logger.info(f"WHOOP webhook: event={event_type} user={user_id} data_id={data_id}")

    if event_type in ("recovery.updated", "recovery.created"):
        sync_recovery(user_id)
    elif event_type in ("sleep.updated", "sleep.created"):
        sync_sleep(user_id)
    elif event_type in ("workout.updated", "workout.created"):
        sync_strain(user_id)
    else:
        logger.info(f"Unhandled WHOOP event: {event_type}")

    return True
