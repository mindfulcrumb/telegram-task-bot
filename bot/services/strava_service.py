"""Strava integration — OAuth2, activity sync, running coaching analytics (API v3)."""
import logging
import os
import secrets
import urllib.parse
from datetime import datetime, timedelta, date, timezone

import httpx

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)

# Strava API endpoints (v3)
STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/api/v3/oauth/token"
STRAVA_DEAUTH_URL = "https://www.strava.com/oauth/deauthorize"
STRAVA_API_BASE = "https://www.strava.com/api/v3"

# Scopes — read all activities + full profile (weight, shoes, zones)
STRAVA_SCOPES = "read,activity:read_all,profile:read_all"

# Shared httpx client
_http = httpx.Client(
    timeout=20,
    headers={
        "User-Agent": "Mozilla/5.0 (compatible; ZoeBot/1.0)",
        "Accept": "application/json",
    },
)


def _get_client_id() -> str:
    return os.environ.get("STRAVA_CLIENT_ID", "")


def _get_client_secret() -> str:
    return os.environ.get("STRAVA_CLIENT_SECRET", "")


def _get_redirect_uri() -> str:
    explicit = os.environ.get("STRAVA_REDIRECT_URI", "")
    if explicit:
        return explicit
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if domain:
        return f"https://{domain}/strava/callback"
    return ""


def is_configured() -> bool:
    """Check if Strava credentials are set."""
    return bool(_get_client_id() and _get_client_secret() and _get_redirect_uri())


# ═══════════════════════════════════════════════════
# OAUTH2
# ═══════════════════════════════════════════════════

def get_auth_url(user_id: int) -> str | None:
    """Generate Strava OAuth authorization URL with CSRF protection."""
    client_id = _get_client_id()
    redirect_uri = _get_redirect_uri()
    if not client_id or not redirect_uri:
        return None

    nonce = secrets.token_urlsafe(16)
    state = f"strava_{user_id:06d}_{nonce}"

    from bot.services.google_auth import _store_oauth_state
    _store_oauth_state(user_id, nonce)

    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": STRAVA_SCOPES,
        "state": state,
        "approval_prompt": "auto",
    })
    return f"{STRAVA_AUTH_URL}?{params}"


def exchange_code(user_id: int, code: str) -> tuple[bool, str]:
    """Exchange authorization code for tokens and store in DB."""
    client_id = _get_client_id()
    client_secret = _get_client_secret()

    logger.info(f"Strava token exchange for user {user_id}")

    try:
        resp = _http.post(
            STRAVA_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
        )

        if resp.status_code != 200:
            logger.error(f"Strava token exchange failed: HTTP {resp.status_code}")
            return False, f"Token exchange failed (HTTP {resp.status_code})"

        token_data = resp.json()
    except Exception as e:
        logger.error(f"Strava token exchange failed: {e}")
        return False, str(e)

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token", "")
    expires_at_unix = token_data.get("expires_at", 0)
    athlete = token_data.get("athlete", {})
    strava_athlete_id = athlete.get("id")

    if not access_token:
        return False, "No access_token in Strava response"

    expires_at = datetime.fromtimestamp(expires_at_unix, tz=timezone.utc) if expires_at_unix else (
        datetime.now(timezone.utc) + timedelta(hours=6)
    )

    try:
        with get_cursor() as cur:
            cur.execute(
                """INSERT INTO strava_tokens
                   (user_id, access_token, refresh_token, expires_at, scopes, strava_athlete_id)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (user_id) DO UPDATE SET
                     access_token = EXCLUDED.access_token,
                     refresh_token = EXCLUDED.refresh_token,
                     expires_at = EXCLUDED.expires_at,
                     scopes = EXCLUDED.scopes,
                     strava_athlete_id = EXCLUDED.strava_athlete_id,
                     updated_at = NOW()""",
                (user_id, access_token, refresh_token, expires_at, STRAVA_SCOPES, strava_athlete_id),
            )
    except Exception as e:
        logger.error(f"Strava token storage failed for user {user_id}: {e}")
        return False, "Failed to store tokens."

    # Store athlete profile data
    try:
        _sync_athlete_profile(user_id, athlete)
    except Exception:
        pass

    logger.info(f"Strava connected for user {user_id} (athlete_id={strava_athlete_id})")
    return True, ""


def _refresh_tokens(user_id: int, refresh_token: str) -> str | None:
    """Refresh expired access token. Returns new access_token or None."""
    client_id = _get_client_id()
    client_secret = _get_client_secret()

    try:
        resp = _http.post(
            STRAVA_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )

        if resp.status_code != 200:
            logger.error(f"Strava token refresh HTTP {resp.status_code}")
            return None

        token_data = resp.json()
    except Exception as e:
        logger.error(f"Strava token refresh failed for user {user_id}: {e}")
        return None

    new_access = token_data.get("access_token")
    new_refresh = token_data.get("refresh_token", refresh_token)
    expires_at_unix = token_data.get("expires_at", 0)

    if not new_access:
        return None

    expires_at = datetime.fromtimestamp(expires_at_unix, tz=timezone.utc) if expires_at_unix else (
        datetime.now(timezone.utc) + timedelta(hours=6)
    )

    with get_cursor() as cur:
        cur.execute(
            """UPDATE strava_tokens
               SET access_token = %s, refresh_token = %s, expires_at = %s, updated_at = NOW()
               WHERE user_id = %s""",
            (new_access, new_refresh, expires_at, user_id),
        )

    return new_access


def get_access_token(user_id: int) -> str | None:
    """Get valid access token, auto-refreshing if expired."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT access_token, refresh_token, expires_at FROM strava_tokens WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            return None

    # Strava tokens expire every 6 hours — refresh with 5-min buffer
    if row["expires_at"] and row["expires_at"] < datetime.now(timezone.utc) + timedelta(minutes=5):
        if row["refresh_token"]:
            logger.info(f"Strava token expired for user {user_id}, refreshing...")
            return _refresh_tokens(user_id, row["refresh_token"])
        return None

    return row["access_token"]


def is_connected(user_id: int) -> bool:
    """Check if user has Strava linked."""
    with get_cursor() as cur:
        cur.execute("SELECT id FROM strava_tokens WHERE user_id = %s", (user_id,))
        return cur.fetchone() is not None


def revoke_access(user_id: int) -> bool:
    """Disconnect Strava — revoke token and clean up."""
    token = get_access_token(user_id)
    if token:
        try:
            _http.post(STRAVA_DEAUTH_URL, data={"access_token": token})
        except Exception:
            pass

    with get_cursor() as cur:
        cur.execute("DELETE FROM strava_tokens WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM strava_activities WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM strava_best_efforts WHERE user_id = %s", (user_id,))
    return True


# ═══════════════════════════════════════════════════
# API HELPERS
# ═══════════════════════════════════════════════════

def _api_get(path: str, access_token: str, params: dict = None) -> dict | list | None:
    """Make authenticated GET request to Strava API."""
    url = f"{STRAVA_API_BASE}{path}"

    try:
        resp = _http.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code == 401:
            logger.error(f"Strava API {path} 401 Unauthorized")
            return None
        if resp.status_code == 429:
            logger.warning(f"Strava API rate limited on {path}")
            return None
        if resp.status_code != 200:
            logger.error(f"Strava API {path} HTTP {resp.status_code}: {resp.text[:300]}")
            return None
        return resp.json()
    except Exception as e:
        logger.error(f"Strava API {path} failed: {e}")
        return None


# ═══════════════════════════════════════════════════
# DATA SYNC
# ═══════════════════════════════════════════════════

def _sync_athlete_profile(user_id: int, athlete: dict):
    """Store athlete profile data from OAuth response or API fetch."""
    if not athlete:
        return
    weight = athlete.get("weight")  # kg
    measurement = athlete.get("measurement_preference", "meters")
    athlete_type = athlete.get("athlete_type", 0)  # 0=cyclist, 1=runner
    shoes = athlete.get("shoes", [])

    with get_cursor() as cur:
        cur.execute(
            """UPDATE strava_tokens SET
               athlete_weight_kg = %s,
               measurement_preference = %s,
               athlete_type = %s,
               shoes_json = %s,
               updated_at = NOW()
               WHERE user_id = %s""",
            (weight, measurement, athlete_type,
             __import__("json").dumps(shoes) if shoes else None,
             user_id),
        )


def sync_recent_activities(user_id: int, days: int = 7) -> list[dict]:
    """Fetch recent activities from Strava and store running activities."""
    token = get_access_token(user_id)
    if not token:
        return []

    after = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    activities = _api_get("/athlete/activities", token, {"after": after, "per_page": 50})
    if not activities:
        return []

    stored = []
    for act in activities:
        sport = act.get("sport_type", act.get("type", ""))
        # Store all activities but focus on running types
        if sport not in ("Run", "TrailRun", "VirtualRun", "Ride", "Walk", "Hike",
                         "Swim", "WeightTraining", "Workout", "Crossfit", "Yoga"):
            continue

        try:
            _upsert_activity(user_id, act)
            stored.append(act)
        except Exception as e:
            logger.warning(f"Strava activity upsert failed: {e}")

    # For running activities, fetch detailed data (best_efforts, splits)
    for act in stored:
        sport = act.get("sport_type", act.get("type", ""))
        if sport in ("Run", "TrailRun", "VirtualRun"):
            try:
                _sync_activity_detail(user_id, act["id"], token)
            except Exception as e:
                logger.warning(f"Strava detail sync failed for {act['id']}: {e}")

    logger.info(f"Strava sync: user={user_id} stored={len(stored)} activities")
    return stored


def _upsert_activity(user_id: int, act: dict):
    """Insert or update a Strava activity."""
    strava_id = act["id"]
    sport = act.get("sport_type", act.get("type", ""))
    workout_type = act.get("workout_type", 0)

    # Parse activity date
    start_str = act.get("start_date_local", act.get("start_date", ""))
    try:
        activity_date = datetime.fromisoformat(start_str.replace("Z", "+00:00")).date()
    except Exception:
        activity_date = date.today()

    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO strava_activities
               (user_id, strava_activity_id, name, sport_type, workout_type,
                distance_m, moving_time_s, elapsed_time_s,
                elevation_gain_m, average_speed_ms, max_speed_ms,
                average_heartrate, max_heartrate, average_cadence,
                suffer_score, calories, gear_id,
                has_heartrate, activity_date)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (strava_activity_id) DO UPDATE SET
                 name = EXCLUDED.name,
                 distance_m = EXCLUDED.distance_m,
                 moving_time_s = EXCLUDED.moving_time_s,
                 elapsed_time_s = EXCLUDED.elapsed_time_s,
                 elevation_gain_m = EXCLUDED.elevation_gain_m,
                 average_speed_ms = EXCLUDED.average_speed_ms,
                 max_speed_ms = EXCLUDED.max_speed_ms,
                 average_heartrate = EXCLUDED.average_heartrate,
                 max_heartrate = EXCLUDED.max_heartrate,
                 average_cadence = EXCLUDED.average_cadence,
                 suffer_score = EXCLUDED.suffer_score,
                 calories = EXCLUDED.calories,
                 updated_at = NOW()""",
            (user_id, strava_id, act.get("name", ""), sport, workout_type,
             act.get("distance"), act.get("moving_time"), act.get("elapsed_time"),
             act.get("total_elevation_gain"), act.get("average_speed"), act.get("max_speed"),
             act.get("average_heartrate"), act.get("max_heartrate"), act.get("average_cadence"),
             act.get("suffer_score"), act.get("calories"), act.get("gear_id"),
             act.get("has_heartrate", False), activity_date),
        )


def _sync_activity_detail(user_id: int, strava_activity_id: int, token: str):
    """Fetch detailed activity data — best efforts, splits, laps."""
    detail = _api_get(f"/activities/{strava_activity_id}", token, {"include_all_efforts": "true"})
    if not detail:
        return

    # Store splits (per-km)
    splits = detail.get("splits_metric", [])
    if splits:
        with get_cursor() as cur:
            # Clear old splits for this activity
            cur.execute(
                "DELETE FROM strava_splits WHERE user_id = %s AND strava_activity_id = %s",
                (user_id, strava_activity_id),
            )
            for s in splits:
                cur.execute(
                    """INSERT INTO strava_splits
                       (user_id, strava_activity_id, split_num, distance_m,
                        elapsed_time_s, moving_time_s, elevation_diff_m,
                        average_speed_ms, average_gap_ms, average_heartrate, pace_zone)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (user_id, strava_activity_id, s.get("split"),
                     s.get("distance"), s.get("elapsed_time"), s.get("moving_time"),
                     s.get("elevation_difference"), s.get("average_speed"),
                     s.get("average_grade_adjusted_speed"), s.get("average_heartrate"),
                     s.get("pace_zone")),
                )

    # Store best efforts (PRs)
    best_efforts = detail.get("best_efforts", [])
    if best_efforts:
        with get_cursor() as cur:
            cur.execute(
                "DELETE FROM strava_best_efforts WHERE user_id = %s AND strava_activity_id = %s",
                (user_id, strava_activity_id),
            )
            for be in best_efforts:
                cur.execute(
                    """INSERT INTO strava_best_efforts
                       (user_id, strava_activity_id, name, distance_m,
                        elapsed_time_s, moving_time_s, pr_rank,
                        start_date)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (user_id, strava_activity_id, be.get("name"),
                     be.get("distance"), be.get("elapsed_time"), be.get("moving_time"),
                     be.get("pr_rank"),
                     be.get("start_date")),
                )


def sync_athlete(user_id: int) -> dict | None:
    """Fetch fresh athlete profile from Strava API."""
    token = get_access_token(user_id)
    if not token:
        return None

    athlete = _api_get("/athlete", token)
    if athlete:
        _sync_athlete_profile(user_id, athlete)
    return athlete


# ═══════════════════════════════════════════════════
# DATA ACCESS — for AI brain context
# ═══════════════════════════════════════════════════

def get_running_summary(user_id: int, days: int = 30) -> dict:
    """Get running summary for AI context — recent runs, PRs, volume, pace trends."""
    result = {
        "connected": is_connected(user_id),
        "recent_runs": [],
        "weekly_volume": {},
        "prs": [],
        "pace_trend": None,
        "shoe_info": None,
    }

    if not result["connected"]:
        return result

    with get_cursor() as cur:
        # Recent running activities
        cur.execute(
            """SELECT * FROM strava_activities
               WHERE user_id = %s AND sport_type IN ('Run', 'TrailRun', 'VirtualRun')
               AND activity_date >= %s
               ORDER BY activity_date DESC LIMIT 15""",
            (user_id, date.today() - timedelta(days=days)),
        )
        runs = [dict(r) for r in cur.fetchall()]
        result["recent_runs"] = runs

        # Weekly volume (last 4 weeks)
        cur.execute(
            """SELECT
                 DATE_TRUNC('week', activity_date) AS week_start,
                 COUNT(*) AS run_count,
                 ROUND(SUM(distance_m)::numeric / 1000, 1) AS total_km,
                 ROUND(SUM(moving_time_s)::numeric / 60, 0) AS total_minutes,
                 ROUND(SUM(elevation_gain_m)::numeric, 0) AS total_elevation_m,
                 ROUND(AVG(average_heartrate)::numeric, 0) AS avg_hr
               FROM strava_activities
               WHERE user_id = %s AND sport_type IN ('Run', 'TrailRun', 'VirtualRun')
               AND activity_date >= %s
               GROUP BY week_start
               ORDER BY week_start DESC""",
            (user_id, date.today() - timedelta(days=28)),
        )
        weeks = [dict(r) for r in cur.fetchall()]
        result["weekly_volume"] = weeks

        # All-time PRs (best efforts where pr_rank = 1)
        cur.execute(
            """SELECT DISTINCT ON (name) name, distance_m, elapsed_time_s, moving_time_s,
                      pr_rank, start_date
               FROM strava_best_efforts
               WHERE user_id = %s AND pr_rank = 1
               ORDER BY name, elapsed_time_s ASC""",
            (user_id,),
        )
        prs = [dict(r) for r in cur.fetchall()]
        result["prs"] = prs

        # Pace trend — compare last 2 weeks vs previous 2 weeks
        cur.execute(
            """SELECT
                 CASE WHEN activity_date >= CURRENT_DATE - 14 THEN 'recent'
                      ELSE 'older' END AS period,
                 ROUND(AVG(average_speed_ms)::numeric, 3) AS avg_speed,
                 ROUND(AVG(average_heartrate)::numeric, 0) AS avg_hr,
                 COUNT(*) AS runs
               FROM strava_activities
               WHERE user_id = %s AND sport_type IN ('Run', 'TrailRun')
               AND activity_date >= %s AND average_speed_ms > 0
               GROUP BY period""",
            (user_id, date.today() - timedelta(days=28)),
        )
        trend_rows = {r["period"]: dict(r) for r in cur.fetchall()}
        if "recent" in trend_rows and "older" in trend_rows:
            recent_pace = trend_rows["recent"]["avg_speed"]
            older_pace = trend_rows["older"]["avg_speed"]
            if older_pace and older_pace > 0:
                pct = ((recent_pace - older_pace) / older_pace) * 100
                if pct > 3:
                    result["pace_trend"] = "getting_faster"
                elif pct < -3:
                    result["pace_trend"] = "getting_slower"
                else:
                    result["pace_trend"] = "stable"

        # Shoe info
        cur.execute(
            "SELECT shoes_json FROM strava_tokens WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        if row and row.get("shoes_json"):
            import json
            try:
                result["shoe_info"] = json.loads(row["shoes_json"])
            except Exception:
                pass

    return result


def get_run_splits(user_id: int, strava_activity_id: int) -> list[dict]:
    """Get per-km splits for a specific run."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT * FROM strava_splits
               WHERE user_id = %s AND strava_activity_id = %s
               ORDER BY split_num""",
            (user_id, strava_activity_id),
        )
        return [dict(r) for r in cur.fetchall()]


def get_recent_prs(user_id: int, days: int = 30) -> list[dict]:
    """Get recent PRs achieved."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT be.*, sa.name AS activity_name, sa.activity_date
               FROM strava_best_efforts be
               JOIN strava_activities sa ON sa.strava_activity_id = be.strava_activity_id
               WHERE be.user_id = %s AND be.pr_rank = 1
               AND sa.activity_date >= %s
               ORDER BY sa.activity_date DESC""",
            (user_id, date.today() - timedelta(days=days)),
        )
        return [dict(r) for r in cur.fetchall()]


# ═══════════════════════════════════════════════════
# RUNNING COACHING ANALYTICS
# ═══════════════════════════════════════════════════

def _speed_to_pace_str(speed_ms: float) -> str:
    """Convert m/s speed to min:sec/km pace string."""
    if not speed_ms or speed_ms <= 0:
        return "N/A"
    pace_seconds = 1000 / speed_ms
    minutes = int(pace_seconds // 60)
    seconds = int(pace_seconds % 60)
    return f"{minutes}:{seconds:02d}"


def _seconds_to_time_str(seconds: int) -> str:
    """Convert seconds to H:MM:SS or MM:SS."""
    if not seconds:
        return "N/A"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def analyze_running_performance(user_id: int) -> dict:
    """Deep analysis of running performance for coaching.

    Returns insights on:
    - Training load (acute vs chronic — injury risk)
    - Pace consistency (split evenness)
    - HR efficiency (pace at same HR over time)
    - Volume progression (10% rule)
    - Race readiness based on best efforts
    """
    insights = []
    analysis = {"insights": insights, "training_load": {}, "race_predictions": {}}

    try:
        with get_cursor() as cur:
            # 1. Training load — acute:chronic workload ratio
            cur.execute(
                """SELECT
                     SUM(CASE WHEN activity_date >= CURRENT_DATE - 7
                         THEN distance_m ELSE 0 END) / 1000.0 AS acute_km,
                     SUM(CASE WHEN activity_date >= CURRENT_DATE - 28
                         THEN distance_m ELSE 0 END) / 4000.0 AS chronic_km_avg,
                     COUNT(CASE WHEN activity_date >= CURRENT_DATE - 7 THEN 1 END) AS runs_this_week,
                     COUNT(CASE WHEN activity_date >= CURRENT_DATE - 28 THEN 1 END)::numeric / 4
                         AS avg_runs_per_week
                   FROM strava_activities
                   WHERE user_id = %s AND sport_type IN ('Run', 'TrailRun')
                   AND activity_date >= CURRENT_DATE - 28""",
                (user_id,),
            )
            row = cur.fetchone()
            if row and row["chronic_km_avg"] and row["chronic_km_avg"] > 0:
                acute = float(row["acute_km"] or 0)
                chronic = float(row["chronic_km_avg"])
                acwr = round(acute / chronic, 2) if chronic > 0 else 0
                analysis["training_load"] = {
                    "acute_km": round(acute, 1),
                    "chronic_km_avg": round(chronic, 1),
                    "acwr": acwr,
                    "runs_this_week": row["runs_this_week"],
                    "avg_runs_per_week": round(float(row["avg_runs_per_week"] or 0), 1),
                }

                if acwr > 1.5:
                    insights.append(
                        f"Injury risk: acute:chronic workload ratio is {acwr} "
                        f"(>{'>'}1.5 danger zone). This week: {round(acute, 1)}km vs "
                        f"{round(chronic, 1)}km/week avg. Scale back 20-30%."
                    )
                elif acwr > 1.3:
                    insights.append(
                        f"Workload climbing: ACWR at {acwr}. Fine if you're building, "
                        f"but don't jump more than 10% week-over-week."
                    )
                elif acwr < 0.8 and chronic > 5:
                    insights.append(
                        f"Detraining signal: only {round(acute, 1)}km this week vs "
                        f"{round(chronic, 1)}km avg. Consistency matters more than big weeks."
                    )

            # 2. Pace consistency — analyze split evenness in recent runs
            cur.execute(
                """SELECT strava_activity_id, sa.name, sa.activity_date,
                          sa.distance_m, sa.moving_time_s
                   FROM strava_activities sa
                   WHERE sa.user_id = %s AND sa.sport_type = 'Run'
                   AND sa.activity_date >= CURRENT_DATE - 14
                   AND sa.distance_m > 3000
                   ORDER BY sa.activity_date DESC LIMIT 5""",
                (user_id,),
            )
            recent_runs = cur.fetchall()

            for run in recent_runs:
                cur.execute(
                    """SELECT average_speed_ms FROM strava_splits
                       WHERE user_id = %s AND strava_activity_id = %s
                       AND average_speed_ms > 0
                       ORDER BY split_num""",
                    (user_id, run["strava_activity_id"]),
                )
                splits = [r["average_speed_ms"] for r in cur.fetchall()]
                if len(splits) >= 3:
                    first_half = splits[:len(splits) // 2]
                    second_half = splits[len(splits) // 2:]
                    avg_first = sum(first_half) / len(first_half)
                    avg_second = sum(second_half) / len(second_half)
                    # Positive split = slowed down, negative = sped up
                    if avg_first > 0:
                        diff_pct = ((avg_first - avg_second) / avg_first) * 100
                        if diff_pct > 8:
                            insights.append(
                                f"Positive split on {run['name']}: started too fast, "
                                f"faded {round(abs(diff_pct))}% in the second half. "
                                f"Start 10-15s/km slower."
                            )

            # 3. Race predictions from best efforts (Jack Daniels VDOT)
            cur.execute(
                """SELECT DISTINCT ON (name) name, elapsed_time_s, start_date
                   FROM strava_best_efforts
                   WHERE user_id = %s AND pr_rank = 1
                   AND name IN ('5K', '10K', 'Half-Marathon', 'Marathon', '1 mile')
                   ORDER BY name, elapsed_time_s ASC""",
                (user_id,),
            )
            prs = {r["name"]: dict(r) for r in cur.fetchall()}

            if "5K" in prs:
                t5k = prs["5K"]["elapsed_time_s"]
                # Simplified race prediction (Riegel formula: T2 = T1 * (D2/D1)^1.06)
                pred_10k = t5k * (10 / 5) ** 1.06
                pred_half = t5k * (21.0975 / 5) ** 1.06
                pred_marathon = t5k * (42.195 / 5) ** 1.06
                analysis["race_predictions"] = {
                    "based_on": f"5K PR: {_seconds_to_time_str(t5k)}",
                    "10K": _seconds_to_time_str(int(pred_10k)),
                    "half_marathon": _seconds_to_time_str(int(pred_half)),
                    "marathon": _seconds_to_time_str(int(pred_marathon)),
                }

            # 4. HR efficiency trend — same pace at lower HR = improvement
            cur.execute(
                """SELECT
                     CASE WHEN activity_date >= CURRENT_DATE - 14 THEN 'recent'
                          ELSE 'older' END AS period,
                     ROUND(AVG(average_heartrate)::numeric, 0) AS avg_hr,
                     ROUND(AVG(average_speed_ms)::numeric, 3) AS avg_speed,
                     COUNT(*) AS runs
                   FROM strava_activities
                   WHERE user_id = %s AND sport_type = 'Run'
                   AND average_heartrate IS NOT NULL AND average_heartrate > 0
                   AND activity_date >= CURRENT_DATE - 28
                   GROUP BY period
                   HAVING COUNT(*) >= 2""",
                (user_id,),
            )
            hr_rows = {r["period"]: dict(r) for r in cur.fetchall()}
            if "recent" in hr_rows and "older" in hr_rows:
                r_hr = float(hr_rows["recent"]["avg_hr"])
                o_hr = float(hr_rows["older"]["avg_hr"])
                r_speed = float(hr_rows["recent"]["avg_speed"])
                o_speed = float(hr_rows["older"]["avg_speed"])
                # Faster at lower HR = better efficiency
                if r_speed >= o_speed and r_hr < o_hr - 2:
                    insights.append(
                        f"Aerobic fitness improving: running {_speed_to_pace_str(r_speed)}/km "
                        f"at {int(r_hr)}bpm vs {_speed_to_pace_str(o_speed)}/km at "
                        f"{int(o_hr)}bpm two weeks ago. Heart is getting more efficient."
                    )
                elif r_speed <= o_speed and r_hr > o_hr + 3:
                    insights.append(
                        f"Cardiac drift signal: HR up to {int(r_hr)}bpm for same pace. "
                        f"Could be fatigue, heat, or dehydration. Check recovery."
                    )

            # 5. Shoe mileage check
            cur.execute(
                "SELECT shoes_json FROM strava_tokens WHERE user_id = %s",
                (user_id,),
            )
            shoes_row = cur.fetchone()
            if shoes_row and shoes_row.get("shoes_json"):
                import json
                try:
                    shoes = json.loads(shoes_row["shoes_json"])
                    for shoe in shoes:
                        dist_km = (shoe.get("distance", 0) or 0) / 1000
                        if dist_km > 700:
                            insights.append(
                                f"Shoe alert: {shoe.get('name', 'Unknown')} has "
                                f"{round(dist_km)}km — most running shoes lose cushion "
                                f"after 500-800km. Consider rotating in a new pair."
                            )
                except Exception:
                    pass

    except Exception as e:
        logger.warning(f"Strava running analysis failed for user {user_id}: {e}")

    return analysis


# ═══════════════════════════════════════════════════
# CROSS-DOMAIN: STRAVA x WHOOP
# ═══════════════════════════════════════════════════

def get_cross_domain_insights(user_id: int) -> list[str]:
    """Correlate Strava running data with WHOOP recovery for coaching insights."""
    insights = []

    try:
        with get_cursor() as cur:
            # Recovery score vs run performance
            cur.execute(
                """SELECT
                     CASE WHEN wd.recovery_score >= 67 THEN 'green'
                          WHEN wd.recovery_score >= 34 THEN 'yellow'
                          ELSE 'red' END AS zone,
                     ROUND(AVG(sa.average_speed_ms)::numeric, 3) AS avg_speed,
                     ROUND(AVG(sa.average_heartrate)::numeric, 0) AS avg_hr,
                     COUNT(*) AS runs
                   FROM strava_activities sa
                   JOIN whoop_daily wd ON wd.user_id = sa.user_id
                     AND wd.cycle_date = sa.activity_date
                   WHERE sa.user_id = %s AND sa.sport_type IN ('Run', 'TrailRun')
                   AND sa.activity_date >= CURRENT_DATE - 30
                   AND wd.recovery_score IS NOT NULL
                   GROUP BY zone
                   HAVING COUNT(*) >= 2""",
                (user_id,),
            )
            rows = {r["zone"]: dict(r) for r in cur.fetchall()}
            if "green" in rows and ("yellow" in rows or "red" in rows):
                green = rows["green"]
                compare = rows.get("red") or rows.get("yellow")
                compare_zone = "red" if "red" in rows else "yellow"
                g_pace = _speed_to_pace_str(float(green["avg_speed"]))
                c_pace = _speed_to_pace_str(float(compare["avg_speed"]))
                insights.append(
                    f"Green recovery runs: {g_pace}/km at {int(green['avg_hr'])}bpm. "
                    f"{compare_zone.title()} recovery runs: {c_pace}/km at "
                    f"{int(compare['avg_hr'])}bpm. Your body runs measurably better "
                    f"when recovered — respect the zones."
                )

            # Running strain vs next-day recovery
            cur.execute(
                """SELECT
                     CASE WHEN sa.distance_m > 10000 THEN 'long'
                          WHEN sa.distance_m > 5000 THEN 'medium'
                          ELSE 'short' END AS run_type,
                     ROUND(AVG(next_wd.recovery_score)::numeric, 0) AS next_day_recovery,
                     COUNT(*) AS runs
                   FROM strava_activities sa
                   JOIN whoop_daily next_wd ON next_wd.user_id = sa.user_id
                     AND next_wd.cycle_date = sa.activity_date + 1
                   WHERE sa.user_id = %s AND sa.sport_type IN ('Run', 'TrailRun')
                   AND sa.activity_date >= CURRENT_DATE - 30
                   AND next_wd.recovery_score IS NOT NULL
                   GROUP BY run_type
                   HAVING COUNT(*) >= 2""",
                (user_id,),
            )
            rows = {r["run_type"]: dict(r) for r in cur.fetchall()}
            if "long" in rows and "short" in rows:
                long_rec = int(rows["long"]["next_day_recovery"])
                short_rec = int(rows["short"]["next_day_recovery"])
                diff = short_rec - long_rec
                if diff > 8:
                    insights.append(
                        f"Long runs (10km+) tank next-day recovery: {long_rec}% vs "
                        f"{short_rec}% after shorter runs. Plan easy days after long runs."
                    )

    except Exception as e:
        logger.warning(f"Strava cross-domain insights failed for user {user_id}: {e}")

    return insights


# ═══════════════════════════════════════════════════
# WEBHOOK HANDLING
# ═══════════════════════════════════════════════════

def handle_webhook_event(event: dict) -> bool:
    """Process a Strava webhook event."""
    object_type = event.get("object_type")
    aspect_type = event.get("aspect_type")
    object_id = event.get("object_id")
    owner_id = event.get("owner_id")

    logger.info(f"Strava webhook: {object_type}.{aspect_type} id={object_id} owner={owner_id}")

    if object_type == "athlete" and aspect_type == "update":
        updates = event.get("updates", {})
        if updates.get("authorized") == "false":
            # User deauthorized — clean up
            with get_cursor() as cur:
                cur.execute(
                    "SELECT user_id FROM strava_tokens WHERE strava_athlete_id = %s",
                    (owner_id,),
                )
                row = cur.fetchone()
                if row:
                    revoke_access(row["user_id"])
                    logger.info(f"Strava deauth webhook: cleaned up user {row['user_id']}")
            return True

    if object_type == "activity":
        # Find our user from strava athlete ID
        with get_cursor() as cur:
            cur.execute(
                "SELECT user_id FROM strava_tokens WHERE strava_athlete_id = %s",
                (owner_id,),
            )
            row = cur.fetchone()
            if not row:
                logger.warning(f"Strava webhook for unknown athlete {owner_id}")
                return False
            user_id = row["user_id"]

        if aspect_type in ("create", "update"):
            token = get_access_token(user_id)
            if token:
                detail = _api_get(f"/activities/{object_id}", token,
                                  {"include_all_efforts": "true"})
                if detail:
                    _upsert_activity(user_id, detail)
                    sport = detail.get("sport_type", detail.get("type", ""))
                    if sport in ("Run", "TrailRun", "VirtualRun"):
                        _sync_activity_detail(user_id, object_id, token)
                    logger.info(f"Strava webhook: synced activity {object_id} for user {user_id}")
        elif aspect_type == "delete":
            with get_cursor() as cur:
                cur.execute(
                    "DELETE FROM strava_activities WHERE strava_activity_id = %s AND user_id = %s",
                    (object_id, user_id),
                )
                cur.execute(
                    "DELETE FROM strava_best_efforts WHERE strava_activity_id = %s AND user_id = %s",
                    (object_id, user_id),
                )
                cur.execute(
                    "DELETE FROM strava_splits WHERE strava_activity_id = %s AND user_id = %s",
                    (object_id, user_id),
                )
            logger.info(f"Strava webhook: deleted activity {object_id} for user {user_id}")
        return True

    return False
