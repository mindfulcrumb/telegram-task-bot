"""WHOOP integration — OAuth2, data sync, recovery/sleep/strain access (API v2)."""
import hashlib
import hmac
import logging
import os
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

    # State encodes user_id — WHOOP requires min 8 chars
    state = f"uid_{user_id:06d}"
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
            body = resp.text[:500]
            logger.error(f"WHOOP token exchange HTTP {resp.status_code}: {body}")
            return False, f"HTTP {resp.status_code}: {body}"

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
        logger.error(f"WHOOP token response missing access_token: {token_data}")
        return False, f"No access_token in response. Keys: {list(token_data.keys())}"

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
    """Upsert whoop_daily row with given fields (whitelist-validated)."""
    # Filter out None values and validate against whitelist
    fields = {k: v for k, v in kwargs.items() if v is not None and k in _ALLOWED_DAILY_FIELDS}
    if not fields:
        return

    with get_cursor() as cur:
        # Check if row exists
        cur.execute(
            "SELECT id FROM whoop_daily WHERE user_id = %s AND cycle_date = %s",
            (user_id, cycle_date),
        )
        existing = cur.fetchone()

        if existing:
            set_clause = ", ".join(f"{k} = %s" for k in fields)
            values = list(fields.values()) + [user_id, cycle_date]
            cur.execute(
                f"UPDATE whoop_daily SET {set_clause} WHERE user_id = %s AND cycle_date = %s",
                values,
            )
        else:
            fields["user_id"] = user_id
            fields["cycle_date"] = cycle_date
            cols = ", ".join(fields.keys())
            placeholders = ", ".join(["%s"] * len(fields))
            cur.execute(
                f"INSERT INTO whoop_daily ({cols}) VALUES ({placeholders})",
                list(fields.values()),
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


# --- Webhook handling ---

def verify_webhook_signature(body: bytes, signature: str, timestamp: str) -> bool:
    """Verify WHOOP webhook HMAC-SHA256 signature."""
    secret = _get_client_secret()
    if not secret:
        logger.warning("WHOOP webhook signature check skipped: no client secret")
        return True  # Allow if not configured (dev mode)

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
