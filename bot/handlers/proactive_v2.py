"""Proactive AI Coach v2 — briefings, check-ins, nudges, weekly insights."""
import logging
from datetime import datetime, date
from zoneinfo import ZoneInfo

from telegram.ext import ContextTypes

from bot.services import user_service, task_service
from bot.services import coaching_service

logger = logging.getLogger(__name__)


# --- Morning Briefing ---

async def morning_briefing_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every 15 min. Sends AI briefing to Pro users at their briefing_hour."""
    users = user_service.get_all_active_users()
    for user in users:
        try:
            if user.get("tier") != "pro":
                continue

            tz = _get_tz(user)
            now_user = datetime.now(tz)
            if now_user.hour != user.get("briefing_hour", 8):
                continue

            if coaching_service.was_nudged_today(user["id"], 0, "briefing"):
                continue

            tasks = task_service.get_tasks(user["id"])
            streak = coaching_service.get_streak(user["id"])
            patterns = coaching_service.get_completion_patterns(user["id"])

            text = _generate_briefing(user, tasks, streak, patterns)

            await context.bot.send_message(
                chat_id=user["telegram_user_id"],
                text=text,
            )
            coaching_service.record_nudge(user["id"], 0, "briefing")
            logger.info(f"Briefing sent to user {user['id']}")

        except Exception as e:
            logger.error(f"Briefing failed for user {user.get('id')}: {e}")


# --- Evening Check-in ---

async def evening_check_in_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every 15 min. Asks Pro users about tasks due today."""
    users = user_service.get_all_active_users()
    for user in users:
        try:
            if user.get("tier") != "pro":
                continue

            tz = _get_tz(user)
            now_user = datetime.now(tz)
            if now_user.hour != user.get("check_in_hour", 20):
                continue

            if coaching_service.was_nudged_today(user["id"], 0, "check_in"):
                continue

            pending = coaching_service.get_pending_check_ins(user["id"])
            if not pending:
                continue

            name = user.get("first_name", "friend")
            lines = [f"Hey {name}, quick check-in:\n"]
            for i, t in enumerate(pending[:5], 1):
                lines.append(f"{i}. {t['title']} \u2014 did you finish this?")
            lines.append("\nJust reply with the numbers you completed, or say 'none'.")
            text = "\n".join(lines)

            await context.bot.send_message(
                chat_id=user["telegram_user_id"],
                text=text,
            )

            for t in pending[:5]:
                coaching_service.create_check_in(user["id"], t["id"])
            coaching_service.record_nudge(user["id"], 0, "check_in")
            logger.info(f"Check-in sent to user {user['id']} ({len(pending)} tasks)")

        except Exception as e:
            logger.error(f"Check-in failed for user {user.get('id')}: {e}")


# --- Smart Nudges ---

async def smart_nudge_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every 2 hours. Nudges about overdue/high-priority tasks. Max 3/day."""
    users = user_service.get_all_active_users()
    for user in users:
        try:
            if user.get("tier") != "pro":
                continue

            today_count = coaching_service.count_nudges_today(user["id"])
            if today_count >= 3:
                continue

            tasks = task_service.get_tasks(user["id"])
            today_d = date.today()
            nudges = []

            for t in tasks:
                if today_count + len(nudges) >= 3:
                    break

                # Overdue 3+ days
                if t.get("due_date") and (today_d - t["due_date"]).days >= 3:
                    if not coaching_service.was_nudged_today(user["id"], t["id"], "overdue"):
                        nudges.append((t, "overdue"))
                        continue

                # High priority, no due date
                if t.get("priority") == "High" and not t.get("due_date"):
                    if not coaching_service.was_nudged_today(user["id"], t["id"], "no_due_date"):
                        nudges.append((t, "no_due_date"))

            if not nudges:
                continue

            lines = ["Hey, quick thought:\n"]
            for t, ntype in nudges:
                if ntype == "overdue":
                    days = (today_d - t["due_date"]).days
                    lines.append(f"\U0001f534 \"{t['title']}\" is {days} days overdue")
                elif ntype == "no_due_date":
                    lines.append(f"\u26a1 \"{t['title']}\" is high priority but has no due date")
            lines.append("\nWant me to help with any of these?")

            await context.bot.send_message(
                chat_id=user["telegram_user_id"],
                text="\n".join(lines),
            )

            for t, ntype in nudges:
                coaching_service.record_nudge(user["id"], t["id"], ntype)
            logger.info(f"Nudges sent to user {user['id']} ({len(nudges)} tasks)")

        except Exception as e:
            logger.error(f"Nudge failed for user {user.get('id')}: {e}")


# --- Weekly Insights ---

async def weekly_insights_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every 6 hours. On Sundays, sends weekly performance summary."""
    if datetime.now().weekday() != 6:
        return

    users = user_service.get_all_active_users()
    for user in users:
        try:
            if user.get("tier") != "pro":
                continue

            if coaching_service.was_nudged_today(user["id"], 0, "weekly_insight"):
                continue

            stats = coaching_service.get_weekly_stats(user["id"])
            if stats["completed_this_week"] == 0 and stats["completed_last_week"] == 0:
                continue

            text = _generate_weekly_insight(user, stats)

            await context.bot.send_message(
                chat_id=user["telegram_user_id"],
                text=text,
            )
            coaching_service.record_nudge(user["id"], 0, "weekly_insight")
            logger.info(f"Weekly insight sent to user {user['id']}")

        except Exception as e:
            logger.error(f"Weekly insight failed for user {user.get('id')}: {e}")


# --- Reminders ---

async def reminder_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every 60 seconds. Fires reminders that are due."""
    from bot.services import task_service
    users = user_service.get_all_active_users()
    for user in users:
        try:
            due_reminders = task_service.get_tasks_with_reminders(user["id"])
            for task in due_reminders:
                await context.bot.send_message(
                    chat_id=user["telegram_user_id"],
                    text=f"\u23f0 Reminder: {task['title']}",
                )
                task_service.clear_reminder(task["id"])
                logger.info(f"Reminder fired for user {user['id']}: {task['title']}")
        except Exception as e:
            logger.error(f"Reminder failed for user {user.get('id')}: {e}")


# --- Conversation Pruning ---

async def prune_conversations_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs daily. Deletes conversation history older than 7 days."""
    try:
        from bot.ai import memory_pg
        memory_pg.prune_old(days=7)
    except Exception as e:
        logger.error(f"Conversation pruning failed: {e}")


# --- Stale Session Cleanup ---

async def cleanup_sessions_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every 2 hours. Abandons workout sessions older than 3 hours."""
    try:
        from bot.services import fitness_service
        fitness_service.cleanup_stale_sessions(hours=3)
    except Exception as e:
        logger.error(f"Session cleanup failed: {e}")


# --- Dose Reminders ---

async def dose_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every 4 hours. Reminds Pro users about peptide doses and supplements."""
    users = user_service.get_all_active_users()
    for user in users:
        try:
            if user.get("tier") != "pro":
                continue

            tz = _get_tz(user)
            now_user = datetime.now(tz)
            hour = now_user.hour

            # Only remind at reasonable hours (8am, 12pm, 8pm)
            if hour not in (8, 12, 20):
                continue

            # Check dedup
            if coaching_service.was_nudged_today(user["id"], 0, f"dose_{hour}"):
                continue

            reminders = []

            # Check active peptide protocols
            try:
                from bot.services import biohacking_service
                protocols = biohacking_service.get_active_protocols(user["id"])
                for p in protocols:
                    freq = (p.get("frequency") or "").lower()
                    # Match timing: morning=8, afternoon=12, evening=20
                    if hour == 8 and any(w in freq for w in ["morning", "am", "daily", "2x", "twice"]):
                        reminders.append(f"{p['peptide_name']} ({p.get('dose_amount', '?')}{p.get('dose_unit', 'mcg')})")
                    elif hour == 20 and any(w in freq for w in ["evening", "pm", "night", "bed", "daily", "2x", "twice"]):
                        reminders.append(f"{p['peptide_name']} ({p.get('dose_amount', '?')}{p.get('dose_unit', 'mcg')})")
                    elif hour == 12 and "midday" in freq:
                        reminders.append(f"{p['peptide_name']} ({p.get('dose_amount', '?')}{p.get('dose_unit', 'mcg')})")
            except Exception:
                pass

            # Check supplements at morning
            if hour == 8:
                try:
                    from bot.services import biohacking_service
                    supplements = biohacking_service.get_active_supplements(user["id"])
                    if supplements:
                        supp_names = [s["supplement_name"] for s in supplements[:5]]
                        if supp_names:
                            reminders.append(f"Supplements: {', '.join(supp_names)}")
                except Exception:
                    pass

            if not reminders:
                continue

            name = user.get("first_name", "friend")
            if hour == 8:
                text = f"Morning {name} \u2615 Time for:\n" + "\n".join(f"  {r}" for r in reminders)
            elif hour == 20:
                text = f"Evening dose check, {name}:\n" + "\n".join(f"  {r}" for r in reminders)
            else:
                text = f"Dose reminder:\n" + "\n".join(f"  {r}" for r in reminders)

            text += "\n\nJust say 'took my BPC' or 'took supplements' to log it."

            await context.bot.send_message(
                chat_id=user["telegram_user_id"],
                text=text,
            )
            coaching_service.record_nudge(user["id"], 0, f"dose_{hour}")
            logger.info(f"Dose reminder sent to user {user['id']} ({len(reminders)} items)")

        except Exception as e:
            logger.error(f"Dose reminder failed for user {user.get('id')}: {e}")


# --- Workout Reminder (pre-workout nudge) ---

async def workout_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every 15 min. Sends pre-workout nudge the evening before a likely training day."""
    users = user_service.get_all_active_users()
    for user in users:
        try:
            if user.get("tier") != "pro":
                continue

            tz = _get_tz(user)
            now_user = datetime.now(tz)
            if now_user.hour != user.get("check_in_hour", 20):
                continue

            if coaching_service.was_nudged_today(user["id"], 0, "workout_reminder"):
                continue

            from bot.services import fitness_service
            training_days = fitness_service.get_typical_training_days(user["id"])
            if not training_days:
                continue

            # Tomorrow's PG DOW (0=Sun..6=Sat)
            # Python weekday: Mon=0..Sun=6 → PG DOW: Sun=0..Sat=6
            tomorrow_python = (now_user.weekday() + 1) % 7
            tomorrow_dow = (tomorrow_python + 1) % 7

            if tomorrow_dow not in training_days:
                continue

            # Suggest what to train based on pattern balance
            patterns = fitness_service.get_movement_pattern_balance(user["id"], days=14)
            suggestion = ""
            if patterns:
                push = patterns.get("horizontal_push", 0) + patterns.get("vertical_push", 0)
                pull = patterns.get("horizontal_pull", 0) + patterns.get("vertical_pull", 0)
                squat = patterns.get("squat", 0)
                hinge = patterns.get("hinge", 0)
                candidates = [("pull", pull), ("push", push), ("legs (squat)", squat), ("hinge", hinge)]
                weakest = min(candidates, key=lambda x: x[1])
                suggestion = f" {weakest[0].title()} is due based on your recent balance."

            name = user.get("first_name", "friend")
            day_names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

            text = (
                f"Hey {name}, {day_names[tomorrow_dow]} is usually a training day for you.{suggestion}\n\n"
                "Got your gym bag ready?"
            )

            await context.bot.send_message(
                chat_id=user["telegram_user_id"],
                text=text,
            )
            coaching_service.record_nudge(user["id"], 0, "workout_reminder")
            logger.info(f"Workout reminder sent to user {user['id']}")

        except Exception as e:
            logger.error(f"Workout reminder failed for user {user.get('id')}: {e}")


# --- Weekly Fitness Report ---

async def weekly_fitness_report_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every 6 hours. On Sundays, sends weekly fitness summary to Pro users."""
    if datetime.now().weekday() != 6:
        return

    users = user_service.get_all_active_users()
    for user in users:
        try:
            if user.get("tier") != "pro":
                continue

            if coaching_service.was_nudged_today(user["id"], 0, "weekly_fitness"):
                continue

            from bot.services import fitness_service
            summary = fitness_service.get_fitness_summary(user["id"])
            workouts_this_week = fitness_service.get_workouts_this_week(user["id"])

            if not workouts_this_week:
                continue

            text = _generate_weekly_fitness_report(user, summary, workouts_this_week)

            await context.bot.send_message(
                chat_id=user["telegram_user_id"],
                text=text,
            )
            coaching_service.record_nudge(user["id"], 0, "weekly_fitness")
            logger.info(f"Weekly fitness report sent to user {user['id']}")

        except Exception as e:
            logger.error(f"Weekly fitness report failed for user {user.get('id')}: {e}")


# --- Streak at Risk Alert ---

async def streak_at_risk_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every 2 hours. Alerts Pro users if their workout streak might break."""
    users = user_service.get_all_active_users()
    for user in users:
        try:
            if user.get("tier") != "pro":
                continue

            tz = _get_tz(user)
            now_user = datetime.now(tz)
            # Only alert in the evening (6pm-10pm)
            if now_user.hour < 18 or now_user.hour > 22:
                continue

            if coaching_service.was_nudged_today(user["id"], 0, "streak_risk"):
                continue

            from bot.services import fitness_service
            streak = fitness_service.get_workout_streak(user["id"])
            current = streak.get("current_streak", 0)

            if current < 2:
                continue

            # Already worked out today — no risk
            if fitness_service.has_workout_today(user["id"]):
                continue

            # Check gap: streak allows 2-day gap, alert when it's getting tight
            last_workout = streak.get("last_workout_date")
            if not last_workout:
                continue
            gap_days = (date.today() - last_workout).days
            if gap_days < 1:
                continue  # Worked out today (shouldn't reach here, but safety)

            name = user.get("first_name", "friend")
            text = (
                f"Hey {name}, your workout streak is at {current} sessions. "
                f"No workout logged today — even 20 min of mobility counts.\n\n"
                "Want me to give you something quick?"
            )

            await context.bot.send_message(
                chat_id=user["telegram_user_id"],
                text=text,
            )
            coaching_service.record_nudge(user["id"], 0, "streak_risk")
            logger.info(f"Streak risk alert sent to user {user['id']} (streak: {current})")

        except Exception as e:
            logger.error(f"Streak risk alert failed for user {user.get('id')}: {e}")


# --- Research Auto-Update ---

async def research_update_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs weekly (Mondays). Checks podcast RSS, PubMed, ClinicalTrials, YouTube transcripts, and articles."""
    if datetime.now().weekday() != 0:  # Monday = 0
        return

    try:
        from bot.services.research_service import (
            check_new_episodes, check_pubmed_updates, check_clinical_trials,
            check_youtube_transcripts, check_pubmed_full_abstracts, check_jay_campbell,
        )
        total = 0

        # Existing crawlers
        added = check_new_episodes()
        total += added

        pubmed_added = check_pubmed_updates()
        total += pubmed_added

        ct_added = check_clinical_trials()
        total += ct_added

        # Deep content extraction crawlers
        yt_added = check_youtube_transcripts()
        total += yt_added

        pubmed_deep = check_pubmed_full_abstracts()
        total += pubmed_deep

        jc_added = check_jay_campbell()
        total += jc_added

        if total > 0:
            logger.info(
                f"Research update: {total} new KB entries "
                f"(RSS={added}, PubMed={pubmed_added}, ClinicalTrials={ct_added}, "
                f"YouTube={yt_added}, PubMedDeep={pubmed_deep}, JayCampbell={jc_added})"
            )
    except Exception as e:
        logger.error(f"Research update job failed: {e}")


# --- One-Time Deep Content Extraction ---

async def initial_content_extraction_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs once after startup. Extracts deep content from YouTube, PubMed, and Jay Campbell.
    Checks content_processing_log to avoid re-processing — safe to run on every deploy."""
    try:
        from bot.db.database import get_cursor

        # Check how many items we've already processed
        with get_cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM content_processing_log WHERE status = 'completed'")
            row = cur.fetchone()
            already_done = row["cnt"] if row else 0

        # Determine batch size: first run = bigger batch, subsequent deploys = just new content
        if already_done == 0:
            logger.info("Initial content extraction: first run — processing up to 15 videos + PubMed + articles")
            yt_limit = 15
            pubmed_per_term = 2
            jc_limit = 10
        else:
            logger.info(f"Initial content extraction: {already_done} items already done — checking for new content only")
            yt_limit = 5
            pubmed_per_term = 1
            jc_limit = 5

        from bot.services.content_extractor import (
            process_youtube_channel, process_pubmed_deep, process_rss_articles,
            PRIORITY_KEYWORDS,
        )

        total = 0

        # YouTube transcripts (priority episodes first)
        for channel in ["huberman", "attia", "doac"]:
            try:
                added = process_youtube_channel(
                    channel, max_videos=yt_limit, priority_keywords=PRIORITY_KEYWORDS,
                )
                total += added
                logger.info(f"Content extraction [{channel}]: {added} KB entries")
            except Exception as e:
                logger.error(f"Content extraction [{channel}] failed: {e}")

        # PubMed full abstracts
        try:
            added = process_pubmed_deep(max_per_term=pubmed_per_term)
            total += added
            logger.info(f"Content extraction [PubMed]: {added} KB entries")
        except Exception as e:
            logger.error(f"Content extraction [PubMed] failed: {e}")

        # Jay Campbell articles
        try:
            added = process_rss_articles(
                "https://jaycampbell.com/feed/", "jay_campbell", max_articles=jc_limit,
            )
            total += added
            logger.info(f"Content extraction [Jay Campbell]: {added} KB entries")
        except Exception as e:
            logger.error(f"Content extraction [Jay Campbell] failed: {e}")

        logger.info(f"Initial content extraction complete: {total} total new KB entries")

    except Exception as e:
        logger.error(f"Initial content extraction job failed: {e}")


# --- Job Registration ---

def setup_proactive_jobs(application):
    """Register all proactive coaching jobs."""
    jq = application.job_queue

    # Reminders: every 60 seconds
    jq.run_repeating(reminder_job, interval=60, first=10, name="reminders")

    # Morning briefing scanner: every 15 min
    jq.run_repeating(morning_briefing_job, interval=900, first=60, name="morning_briefing")

    # Evening check-in scanner: every 15 min
    jq.run_repeating(evening_check_in_job, interval=900, first=120, name="evening_check_in")

    # Smart nudges: every 2 hours
    jq.run_repeating(smart_nudge_job, interval=7200, first=300, name="smart_nudges")

    # Weekly insights: every 6 hours (self-skips on non-Sundays)
    jq.run_repeating(weekly_insights_job, interval=21600, first=600, name="weekly_insights")

    # Conversation pruning: daily (every 24h)
    jq.run_repeating(prune_conversations_job, interval=86400, first=3600, name="prune_conversations")

    # Stale session cleanup: every 2 hours
    jq.run_repeating(cleanup_sessions_job, interval=7200, first=600, name="cleanup_sessions")

    # Dose reminders: every 4 hours
    jq.run_repeating(dose_reminder_job, interval=14400, first=900, name="dose_reminders")

    # Workout reminder (pre-workout nudge): every 15 min
    jq.run_repeating(workout_reminder_job, interval=900, first=180, name="workout_reminder")

    # Weekly fitness report: every 6 hours (self-skips on non-Sundays)
    jq.run_repeating(weekly_fitness_report_job, interval=21600, first=900, name="weekly_fitness_report")

    # Streak at risk alert: every 2 hours
    jq.run_repeating(streak_at_risk_job, interval=7200, first=600, name="streak_at_risk")

    # Research auto-update: every 12 hours (self-skips on non-Mondays)
    jq.run_repeating(research_update_job, interval=43200, first=1800, name="research_update")

    # One-time deep content extraction: runs once 5 min after startup, then stops
    jq.run_once(initial_content_extraction_job, when=300, name="initial_content_extraction")

    logger.info("Proactive coaching jobs registered (13 jobs)")


# --- Helpers ---

def _get_tz(user: dict):
    """Get timezone for user, default UTC."""
    try:
        return ZoneInfo(user.get("timezone") or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _generate_briefing(user, tasks, streak, patterns):
    """Generate morning briefing via AI with template fallback."""
    try:
        from bot.ai.brain_v2 import _call_api

        today_d = date.today()
        overdue = [t for t in tasks if t.get("due_date") and t["due_date"] < today_d]
        due_today = [t for t in tasks if t.get("due_date") and t["due_date"] == today_d]

        task_lines = []
        for t in tasks[:10]:
            due_str = ""
            if t.get("due_date"):
                if t["due_date"] < today_d:
                    due_str = f" OVERDUE {(today_d - t['due_date']).days}d"
                elif t["due_date"] == today_d:
                    due_str = " due TODAY"
            task_lines.append(f"- {t['title']} [{t['category']}]{due_str}")

        # Include calendar events if available
        cal_section = ""
        try:
            from bot.services import calendar_service
            events = calendar_service.fetch_upcoming_events(user["id"], days=1)
            if events:
                cal_lines = [f"Calendar today ({len(events)} events):"]
                for e in events[:5]:
                    dt = e["start"]
                    time_str = "all day" if e.get("all_day") else dt.strftime("%I:%M %p")
                    cal_lines.append(f"- {e['title']} at {time_str}")
                cal_section = "\n".join(cal_lines) + "\n\n"
        except Exception:
            pass

        # WHOOP data for morning briefing
        whoop_section = ""
        try:
            from bot.services import whoop_service
            if whoop_service.is_connected(user["id"]):
                try:
                    whoop_service.sync_all(user["id"])
                except Exception:
                    pass
                whoop_data = whoop_service.get_today_recovery(user["id"])
                if whoop_data:
                    recovery = whoop_data.get("recovery_score")
                    zone = whoop_service.get_recovery_zone(recovery)
                    hrv = whoop_data.get("hrv_rmssd")
                    sleep = whoop_data.get("sleep_performance")
                    whoop_section = (
                        f"WHOOP: Recovery {recovery}% ({zone}), "
                        f"HRV {hrv}ms, Sleep {sleep}%\n\n"
                    )
        except Exception:
            pass

        # Fitness data for briefing
        fitness_section = ""
        try:
            from bot.services import fitness_service
            workout_streak = fitness_service.get_workout_streak(user["id"])
            ws = workout_streak.get("current_streak", 0)
            patterns_14d = fitness_service.get_movement_pattern_balance(user["id"], days=14)
            if ws > 0:
                fitness_section += f"Workout streak: {ws} sessions\n"
            if patterns_14d:
                total_push = patterns_14d.get("horizontal_push", 0) + patterns_14d.get("vertical_push", 0)
                total_pull = patterns_14d.get("horizontal_pull", 0) + patterns_14d.get("vertical_pull", 0)
                if total_push > 0 or total_pull > 0:
                    fitness_section += f"Push:Pull ratio (14d): {total_push}:{total_pull}\n"
        except Exception:
            pass

        prompt = (
            f"Generate a morning briefing for {user.get('first_name', 'friend')}.\n\n"
            f"Tasks ({len(tasks)} total, {len(overdue)} overdue, {len(due_today)} due today):\n"
            + "\n".join(task_lines) + "\n\n"
            + cal_section
            + whoop_section
            + fitness_section
            + f"Streak: {streak.get('current_streak', 0)} days (best: {streak.get('longest_streak', 0)})\n"
            f"Most productive: {patterns.get('most_productive_day', 'varies')}\n"
            f"Best time: {patterns.get('preferred_time', 'varies')}\n\n"
            "Write 3-5 lines. If WHOOP data is available, mention recovery zone and suggest training intensity accordingly. "
            "Mention calendar events if any. Say which task to start with and why. "
            "Mention streak if > 0. Be warm and thoughtful, like Zoe. "
            "No markdown formatting. Under 500 chars."
        )

        response, error = _call_api(
            "You are Zoe, a thoughtful and warm productivity companion. Brief, specific, calm. Never say 'I'm an AI'. No markdown formatting. Sign off as Zoe if it feels natural.",
            [{"role": "user", "content": prompt}],
            max_tokens=200,
        )

        if not error and response and response.content:
            return response.content[0].text

    except Exception as e:
        logger.error(f"AI briefing failed: {e}")

    # Template fallback
    return _template_briefing(user, tasks)


def _template_briefing(user, tasks):
    """Simple template briefing when AI is unavailable."""
    today_d = date.today()
    name = user.get("first_name", "friend")
    total = len(tasks)
    overdue = sum(1 for t in tasks if t.get("due_date") and t["due_date"] < today_d)
    due_today = sum(1 for t in tasks if t.get("due_date") and t["due_date"] == today_d)
    high = sum(1 for t in tasks if t.get("priority") == "High")

    lines = [f"Good morning, {name}!\n"]
    lines.append(f"You have {total} active tasks.")
    if overdue:
        lines.append(f"\U0001f534 {overdue} overdue")
    if due_today:
        lines.append(f"\U0001f4c5 {due_today} due today")
    if high:
        lines.append(f"\u26a1 {high} high priority")
    if tasks:
        lines.append(f"\nStart with: {tasks[0]['title']}")
    lines.append("\nWhat are you tackling first?")
    return "\n".join(lines)


def _generate_weekly_insight(user, stats):
    """Generate weekly insight via AI with template fallback."""
    try:
        from bot.ai.brain_v2 import _call_api

        prompt = (
            f"Weekly summary for {user.get('first_name', 'friend')}.\n\n"
            f"This week: {stats['completed_this_week']} completed\n"
            f"Last week: {stats['completed_last_week']} completed\n"
            f"Categories: {stats.get('by_category', {})}\n"
            f"Best day: {stats.get('most_productive_day', 'varies')}\n"
            f"Currently overdue: {stats.get('current_overdue', 0)}\n\n"
            "Write 3-4 lines. Compare weeks. Give ONE actionable tip. "
            "Be encouraging. No markdown formatting. Under 400 chars."
        )

        response, error = _call_api(
            "You are Zoe, a thoughtful and warm productivity companion. Brief, specific, calm. Never say 'I'm an AI'. No markdown formatting. Sign off as Zoe if it feels natural.",
            [{"role": "user", "content": prompt}],
            max_tokens=200,
        )

        if not error and response and response.content:
            return response.content[0].text

    except Exception as e:
        logger.error(f"AI weekly insight failed: {e}")

    # Template fallback
    name = user.get("first_name", "friend")
    this_w = stats["completed_this_week"]
    last_w = stats["completed_last_week"]
    diff = this_w - last_w
    trend = f"up {diff}" if diff > 0 else f"down {abs(diff)}" if diff < 0 else "same as"

    return (
        f"Weekly recap, {name}!\n\n"
        f"You completed {this_w} tasks this week ({trend} last week).\n"
        f"Most productive day: {stats.get('most_productive_day', 'varies')}\n"
        f"Currently overdue: {stats.get('current_overdue', 0)}\n\n"
        "Keep it up!"
    )


def _generate_weekly_fitness_report(user, summary, workouts_this_week):
    """Generate weekly fitness report via AI with template fallback."""
    try:
        from bot.ai.brain_v2 import _call_api

        patterns = summary.get("pattern_balance", {})
        volume = summary.get("volume_trend", {})
        prs = summary.get("recent_prs", [])
        metrics = summary.get("latest_metrics", {})
        streak = summary.get("streak", {})
        profile = summary.get("profile")

        target = profile.get("training_days_per_week", 3) if profile else 3

        # Pattern balance lines
        pattern_labels = {
            "horizontal_push": "Push(H)", "horizontal_pull": "Pull(H)",
            "vertical_push": "Push(V)", "vertical_pull": "Pull(V)",
            "squat": "Squat", "hinge": "Hinge", "carry_rotation": "Carry/Rot",
        }
        pattern_lines = []
        for key, label in pattern_labels.items():
            count = patterns.get(key, 0)
            pattern_lines.append(f"  {label}: {count}")

        # Workout list
        workout_lines = []
        for w in workouts_this_week[:5]:
            ex_names = ", ".join(e["exercise_name"] for e in w.get("exercises", [])[:3])
            dur = f" {w['duration_minutes']}min" if w.get("duration_minutes") else ""
            workout_lines.append(f"  - {w['title']}{dur}: {ex_names}")

        pr_lines = [f"  - {p['exercise']}: {p['new_weight']}kg (was {p['previous_best']}kg)" for p in prs[:5]]

        metric_lines = []
        for mt, data in metrics.items():
            label = mt.replace("_", " ").title()
            unit = data.get("unit", "")
            metric_lines.append(f"  {label}: {data['value']}{unit}")

        prompt = (
            f"Weekly fitness report for {user.get('first_name', 'friend')}.\n\n"
            f"Workouts: {len(workouts_this_week)} (target: {target}/week)\n"
            + "\n".join(workout_lines) + "\n\n"
            f"Pattern balance (14d):\n" + "\n".join(pattern_lines) + "\n\n"
            f"Volume: {volume.get('trend', 'n/a')} ({volume.get('this_week_sets', 0)} sets this week vs {volume.get('last_week_sets', 0)} last week)\n"
            f"Streak: {streak.get('current_streak', 0)} sessions (best: {streak.get('longest_streak', 0)})\n"
            f"PRs:\n" + ("\n".join(pr_lines) if pr_lines else "  None this period") + "\n"
            f"Metrics:\n" + ("\n".join(metric_lines) if metric_lines else "  None logged") + "\n\n"
            "Write 4-6 lines. Summarize the week. Note pattern imbalances. Celebrate PRs. "
            "Give ONE specific suggestion for next week. "
            "No markdown formatting. Under 600 chars."
        )

        response, error = _call_api(
            "You are Zoe, a thoughtful and warm fitness coach. Brief, specific, calm. Never say 'I'm an AI'. No markdown formatting.",
            [{"role": "user", "content": prompt}],
            max_tokens=250,
        )

        if not error and response and response.content:
            return response.content[0].text

    except Exception as e:
        logger.error(f"AI weekly fitness report failed: {e}")

    return _template_weekly_fitness(user, summary, workouts_this_week)


def _template_weekly_fitness(user, summary, workouts_this_week):
    """Template fallback for weekly fitness report."""
    name = user.get("first_name", "friend")
    streak = summary.get("streak", {})
    patterns = summary.get("pattern_balance", {})
    volume = summary.get("volume_trend", {})
    prs = summary.get("recent_prs", [])
    profile = summary.get("profile")
    target = profile.get("training_days_per_week", 3) if profile else 3

    lines = [f"Weekly fitness recap, {name}!\n"]
    lines.append(f"Workouts: {len(workouts_this_week)} this week (target: {target})")

    if streak.get("current_streak", 0) > 0:
        lines.append(f"Streak: {streak['current_streak']} sessions")

    if patterns:
        push = patterns.get("horizontal_push", 0) + patterns.get("vertical_push", 0)
        pull = patterns.get("horizontal_pull", 0) + patterns.get("vertical_pull", 0)
        lines.append(f"Push:Pull (14d): {push}:{pull}")
        if push > pull + 2:
            lines.append("Pull is lagging — add some rows or pullups next week.")
        elif pull > push + 2:
            lines.append("Push is lagging — add pressing next week.")

    trend = volume.get("trend", "")
    if trend == "up":
        lines.append(f"Volume up ({volume.get('this_week_sets', 0)} vs {volume.get('last_week_sets', 0)} sets)")
    elif trend == "down":
        lines.append(f"Volume down ({volume.get('this_week_sets', 0)} vs {volume.get('last_week_sets', 0)} sets)")

    if prs:
        lines.append("\nPRs this period:")
        for p in prs[:3]:
            lines.append(f"  {p['exercise'].title()}: {p['new_weight']}kg (was {p['previous_best']}kg)")

    lines.append("\nKeep building next week!")
    return "\n".join(lines)
