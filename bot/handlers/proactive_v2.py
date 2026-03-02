"""Proactive AI Coach v2 — briefings, check-ins, nudges, weekly insights."""
import logging
from datetime import datetime, date
from zoneinfo import ZoneInfo

from telegram.ext import ContextTypes

from bot.services import user_service, task_service
from bot.services import coaching_service
from bot.utils import typing_pause_bot
from bot.handlers.message_utils import send_chunked

logger = logging.getLogger(__name__)


def _get_language_directive(user: dict) -> str:
    """Return a language directive string for AI system prompts.

    Reads preferred_language from user dict. Returns empty string for English
    (default), or an explicit directive for other languages.
    """
    lang_code = user.get("preferred_language") or "en"
    if lang_code == "en":
        return ""
    from bot.services.language_service import get_language_name
    return f" Respond ENTIRELY in {get_language_name(lang_code)}."


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

            await send_chunked(
                bot=context.bot,
                chat_id=user["telegram_user_id"],
                text=text,
                proactive=True,
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
            lines = [f"Hey {name}, quick check-in.\n"]
            for i, t in enumerate(pending[:5], 1):
                lines.append(f"{i}. {t['title']} \u2014 done?")
            lines.append("\nReply with the numbers you knocked out, or just say 'none'.")
            text = "\n".join(lines)

            await typing_pause_bot(context.bot, user["telegram_user_id"], 0.8)
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


# --- End-of-Day Assessment ---

async def daily_assessment_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every 15 min. Sends AI-generated daily review to Pro users at assessment_hour."""
    users = user_service.get_all_active_users()
    for user in users:
        try:
            if user.get("tier") != "pro":
                continue

            tz = _get_tz(user)
            now_user = datetime.now(tz)
            if now_user.hour != user.get("assessment_hour", 22):
                continue

            if coaching_service.was_nudged_today(user["id"], 0, "assessment"):
                continue

            text = _generate_daily_assessment(user)

            await send_chunked(
                bot=context.bot,
                chat_id=user["telegram_user_id"],
                text=text,
                proactive=True,
            )
            coaching_service.record_nudge(user["id"], 0, "assessment")
            logger.info(f"Daily assessment sent to user {user['id']}")

        except Exception as e:
            logger.error(f"Assessment failed for user {user.get('id')}: {e}")


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

            # Build a short summary for the AI to rephrase naturally
            task_bullets = []
            for t, ntype in nudges:
                if ntype == "overdue":
                    days = (today_d - t["due_date"]).days
                    task_bullets.append(f"- \"{t['title']}\" ({days} days overdue)")
                elif ntype == "no_due_date":
                    task_bullets.append(f"- \"{t['title']}\" (high priority, no due date)")

            lang_dir = _get_language_directive(user)
            nudge_prompt = (
                "You're a personal assistant nudging the user about overdue tasks. "
                "Rephrase these task titles in a short, casual, human way (2-4 sentences max). "
                "If a title looks technical (env vars, API keys, config), describe it simply — "
                "e.g. 'that Railway config setup' instead of raw variable names. "
                "End with an offer to help. No markdown, no bullet points."
                + lang_dir + "\n\n"
                + "\n".join(task_bullets)
            )

            from bot.ai.brain_v2 import ai_brain
            nudge_text = await ai_brain.quick_generate(nudge_prompt, max_tokens=200)
            if not nudge_text:
                # Fallback to simple format if AI fails
                lines = ["Hey, these are still hanging:\n"]
                for b in task_bullets:
                    lines.append(b.lstrip("- "))
                lines.append("\nNeed a hand knocking any out?")
                nudge_text = "\n".join(lines)

            await typing_pause_bot(context.bot, user["telegram_user_id"], 0.7)
            await send_chunked(
                bot=context.bot,
                chat_id=user["telegram_user_id"],
                text=nudge_text,
                proactive=True,
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

            await send_chunked(
                bot=context.bot,
                chat_id=user["telegram_user_id"],
                text=text,
                proactive=True,
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


# --- Conversation Summarization + Pruning ---

async def summarize_conversations_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every 2 hours. Summarizes conversations for users inactive 30+ min,
    then prunes raw history older than 7 days. Summaries persist permanently."""
    try:
        from bot.ai import memory_pg
        from bot.services import memory_service
        from bot.ai.brain_v2 import _call_api

        users = user_service.get_all_active_users()
        summarized = 0

        for user in users:
            try:
                uid = user["id"]
                # Get today's conversation history
                history = memory_pg.get_history(uid, limit=20)
                if len(history) < 4:  # Need at least 2 exchanges to summarize
                    continue

                # Check if last message is old enough (30+ min = conversation ended)
                from bot.ai.memory_pg import get_last_message_time
                last_msg = get_last_message_time(uid)
                if not last_msg:
                    continue
                from datetime import datetime, timezone, timedelta
                if datetime.now(timezone.utc) - last_msg < timedelta(minutes=30):
                    continue

                # Check if we already summarized today
                existing = memory_service.get_recent_summaries(uid, limit=1)
                if existing and str(existing[0]["conversation_date"]) == str(date.today()):
                    continue

                # Build conversation text for summarization
                convo_text = ""
                for msg in history[-20:]:
                    role = msg.get("role", "?").upper()
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        content = " ".join(
                            b.get("text", "") for b in content if isinstance(b, dict)
                        )
                    convo_text += f"{role}: {content[:200]}\n"

                if len(convo_text) < 50:
                    continue

                prompt = (
                    "Summarize this conversation in 2-3 sentences. "
                    "Focus on: what the user asked about, decisions made, "
                    "and any notable events (PRs, health changes, new goals).\n"
                    "Also return up to 3 key events as a JSON array.\n\n"
                    f"Conversation:\n{convo_text[:2000]}\n\n"
                    'Format: {"summary": "...", "topics": ["training","nutrition",...], '
                    '"key_events": ["hit squat PR","started creatine",...]}'
                )

                response, error = _call_api(
                    system="Summarize conversations concisely. Return valid JSON.",
                    messages=[{"role": "user", "content": prompt}],
                    model="claude-haiku-4-5-20251001",
                    max_tokens=250,
                )

                if error or not response or not response.content:
                    continue

                text = response.content[0].text.strip()
                # Strip code fences
                if "```" in text:
                    for seg in text.split("```"):
                        seg = seg.strip()
                        if seg.startswith("json"):
                            seg = seg[4:].strip()
                        if seg.startswith("{"):
                            text = seg
                            break

                import json
                data = json.loads(text)
                summary = data.get("summary", text[:300])
                topics = data.get("topics", [])
                key_events = data.get("key_events", [])

                memory_service.save_conversation_summary(
                    uid, summary, topics=topics, key_events=key_events,
                )
                summarized += 1

            except Exception as e:
                logger.debug(f"Conversation summary failed for user {user.get('id')}: {e}")

        if summarized > 0:
            logger.info(f"Summarized {summarized} conversations")

    except Exception as e:
        logger.error(f"Conversation summarization job failed: {e}")

    # Also prune old raw history (summaries persist, raw messages expire)
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
    """Runs every 15 min. Sends interactive dose reminder cards for pending scheduled doses.
    Falls back to plain text for protocols without schedules."""
    users = user_service.get_all_active_users()
    for user in users:
        try:
            if user.get("tier") != "pro":
                continue

            tz = _get_tz(user)
            now_user = datetime.now(tz)
            hour = now_user.hour

            # Only remind during waking hours (7am-10pm)
            if hour < 7 or hour > 22:
                continue

            from bot.services import biohacking_service

            # Generate today's scheduled doses (idempotent)
            try:
                biohacking_service.generate_daily_doses(user["id"], now_user.date())
            except Exception as e:
                logger.warning(f"Daily dose generation failed for user {user['id']}: {e}")

            # Check for pending scheduled doses in a 30-min window around now
            pending_doses = []
            try:
                pending_doses = biohacking_service.get_pending_doses_in_window(
                    user["id"], now_user.time(), window_minutes=30
                )
            except Exception:
                pass

            if pending_doses:
                # Dedup: use the earliest dose time as key
                dose_key = f"dose_card_{pending_doses[0].get('scheduled_time', hour)}"
                if coaching_service.was_nudged_today(user["id"], 0, dose_key):
                    continue

                # Send interactive dose reminder card
                try:
                    from bot.handlers.protocol_cards import render_dose_reminder_card
                    text, markup = render_dose_reminder_card(pending_doses, user.get("first_name", "friend"), hour)
                    msg = await context.bot.send_message(
                        chat_id=user["telegram_user_id"],
                        text=text,
                        reply_markup=markup,
                    )
                    # Store message ID on the doses so card can be edited in place
                    for d in pending_doses:
                        try:
                            biohacking_service.set_dose_reminder_message_id(d["id"], msg.message_id)
                        except Exception:
                            pass
                    coaching_service.record_nudge(user["id"], 0, dose_key)
                    logger.info(f"Interactive dose reminder sent to user {user['id']} ({len(pending_doses)} doses)")
                except Exception as e:
                    logger.error(f"Interactive dose reminder failed for user {user['id']}: {e}")
                continue

            # Fallback: check protocols without schedules (legacy plain text reminders)
            # Only at key hours (8am, 20pm)
            if hour not in (8, 20):
                continue

            if coaching_service.was_nudged_today(user["id"], 0, f"dose_{hour}"):
                continue

            reminders = []
            try:
                protocols = biohacking_service.get_active_protocols(user["id"])
                for p in protocols:
                    # Skip protocols that have schedules (they get interactive cards)
                    try:
                        schedules = biohacking_service.get_schedules(p["id"])
                        if schedules:
                            continue
                    except Exception:
                        pass
                    freq = (p.get("frequency") or "").lower()
                    if hour == 8 and any(w in freq for w in ["morning", "am", "daily", "2x", "twice"]):
                        reminders.append(f"{p['peptide_name']} ({p.get('dose_amount', '?')}{p.get('dose_unit', 'mcg')})")
                    elif hour == 20 and any(w in freq for w in ["evening", "pm", "night", "bed", "daily", "2x", "twice"]):
                        reminders.append(f"{p['peptide_name']} ({p.get('dose_amount', '?')}{p.get('dose_unit', 'mcg')})")
            except Exception:
                pass

            if not reminders:
                continue

            name = user.get("first_name", "friend")
            if hour == 8:
                text = f"Morning, {name}. Time for:\n" + "\n".join(f"  {r}" for r in reminders)
            else:
                text = f"Evening doses, {name}:\n" + "\n".join(f"  {r}" for r in reminders)
            text += "\n\nJust say 'took my BPC' or 'took supplements' to log it."

            await typing_pause_bot(context.bot, user["telegram_user_id"], 0.7)
            await context.bot.send_message(
                chat_id=user["telegram_user_id"],
                text=text,
            )
            coaching_service.record_nudge(user["id"], 0, f"dose_{hour}")
            logger.info(f"Dose reminder (plain) sent to user {user['id']} ({len(reminders)} items)")

        except Exception as e:
            logger.error(f"Dose reminder failed for user {user.get('id')}: {e}")


async def generate_daily_doses_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs daily at midnight. Generates scheduled dose rows for today + marks yesterday's missed."""
    users = user_service.get_all_active_users()
    for user in users:
        try:
            if user.get("tier") != "pro":
                continue

            tz = _get_tz(user)
            now_user = datetime.now(tz)
            today = now_user.date()

            from bot.services import biohacking_service

            # Generate today's doses
            biohacking_service.generate_daily_doses(user["id"], today)

            # Mark yesterday's overdue doses as missed
            biohacking_service.mark_overdue_doses_missed(user["id"])

        except Exception as e:
            logger.error(f"Daily dose generation failed for user {user.get('id')}: {e}")


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

            await typing_pause_bot(context.bot, user["telegram_user_id"], 0.8)
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

            await send_chunked(
                bot=context.bot,
                chat_id=user["telegram_user_id"],
                text=text,
                proactive=True,
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

            await typing_pause_bot(context.bot, user["telegram_user_id"], 0.7)
            await context.bot.send_message(
                chat_id=user["telegram_user_id"],
                text=text,
            )
            coaching_service.record_nudge(user["id"], 0, "streak_risk")
            logger.info(f"Streak risk alert sent to user {user['id']} (streak: {current})")

        except Exception as e:
            logger.error(f"Streak risk alert failed for user {user.get('id')}: {e}")


# --- Research Auto-Update ---

def _run_research_update_sync():
    """Synchronous research update — runs in a thread to avoid blocking the event loop."""
    from bot.services.research_service import (
        check_new_episodes, check_pubmed_updates, check_clinical_trials,
        check_youtube_transcripts, check_pubmed_full_abstracts, check_jay_campbell,
    )
    total = 0

    added = check_new_episodes()
    total += added

    pubmed_added = check_pubmed_updates()
    total += pubmed_added

    ct_added = check_clinical_trials()
    total += ct_added

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
    return total


async def research_update_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs weekly (Mondays). Runs all crawlers in a background thread so the bot stays responsive."""
    import asyncio
    if datetime.now().weekday() != 0:  # Monday = 0
        return

    try:
        await asyncio.to_thread(_run_research_update_sync)
    except Exception as e:
        logger.error(f"Research update job failed: {e}")


# --- One-Time Deep Content Extraction ---

def _run_content_extraction_sync():
    """Synchronous content extraction — runs in a thread to avoid blocking the event loop."""
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
    return total


async def initial_content_extraction_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs once after startup. Runs extraction in a background thread so the bot stays responsive."""
    import asyncio
    try:
        await asyncio.to_thread(_run_content_extraction_sync)
    except Exception as e:
        logger.error(f"Initial content extraction job failed: {e}")


# --- Gmail Alert Check ---

# In-memory dedup set (capped at 1000) to avoid re-notifying same messages
_notified_gmail_ids: set[str] = set()


async def gmail_check_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every 120s. Checks for new inbox emails and notifies user via Telegram."""
    users = user_service.get_all_active_users()
    for user in users:
        try:
            uid = user["id"]
            tg_id = user["telegram_user_id"]

            # Only check users with Gmail scope connected
            from bot.services.google_auth import is_connected, has_scopes
            if not is_connected(uid):
                continue
            if not has_scopes(uid, ["https://www.googleapis.com/auth/gmail.readonly"]):
                continue

            from bot.services import google_workspace
            from bot.db.database import get_cursor

            # Get stored history ID
            with get_cursor() as cur:
                cur.execute(
                    "SELECT gmail_history_id FROM google_calendar_tokens WHERE user_id = %s",
                    (uid,),
                )
                row = cur.fetchone()

            stored_history_id = row.get("gmail_history_id") if row else None

            if not stored_history_id:
                # First run — just store the current history ID, don't notify
                current_id = google_workspace.get_profile_history_id(uid)
                if current_id:
                    with get_cursor() as cur:
                        cur.execute(
                            "UPDATE google_calendar_tokens SET gmail_history_id = %s WHERE user_id = %s",
                            (current_id, uid),
                        )
                continue

            # Check for new messages since last history ID
            new_messages, new_history_id = google_workspace.get_history_changes(
                uid, stored_history_id
            )

            # Update stored history ID
            if new_history_id and new_history_id != stored_history_id:
                with get_cursor() as cur:
                    cur.execute(
                        "UPDATE google_calendar_tokens SET gmail_history_id = %s WHERE user_id = %s",
                        (new_history_id, uid),
                    )

            if not new_messages:
                continue

            # Dedup and notify
            for msg in new_messages[:5]:
                msg_id = msg.get("id", "")
                if msg_id in _notified_gmail_ids:
                    continue
                _notified_gmail_ids.add(msg_id)

                sender = msg.get("from", "Unknown")
                subject = msg.get("subject", "(no subject)")
                snippet = msg.get("snippet", "")[:100]

                notification = (
                    f"New email from {sender}\n"
                    f"{subject}\n\n"
                    f"{snippet}"
                )
                try:
                    await context.bot.send_message(chat_id=tg_id, text=notification)
                except Exception as e:
                    logger.warning(f"Failed to send gmail notification to {tg_id}: {e}")

            # Cap dedup set
            if len(_notified_gmail_ids) > 1000:
                # Remove oldest half
                to_remove = list(_notified_gmail_ids)[:500]
                for item in to_remove:
                    _notified_gmail_ids.discard(item)

        except Exception as e:
            logger.error(f"Gmail check failed for user {user.get('id')}: {e}")


# --- Pain / Mobility Follow-Up ---

async def pain_followup_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every 6 hours. Follows up on active pain reports after 3+ days."""
    users = user_service.get_all_active_users()
    for user in users:
        try:
            if user.get("tier") != "pro":
                continue

            tz = _get_tz(user)
            now_user = datetime.now(tz)
            # Only check during reasonable hours (9am-9pm)
            if now_user.hour < 9 or now_user.hour > 21:
                continue

            if coaching_service.was_nudged_today(user["id"], 0, "pain_followup"):
                continue

            # Check for active pain reports 3+ days old
            try:
                from bot.db.database import get_cursor
                with get_cursor() as cur:
                    cur.execute(
                        """SELECT id, location, severity, pain_type,
                                  upstream_cause, prescription,
                                  created_at
                           FROM pain_reports
                           WHERE user_id = %s AND status = 'active'
                             AND created_at < NOW() - INTERVAL '3 days'
                           ORDER BY severity DESC LIMIT 3""",
                        (user["id"],)
                    )
                    rows = cur.fetchall()
                    if not rows:
                        continue
                    cols = [d[0] for d in cur.description]
                    reports = [dict(zip(cols, r)) for r in rows]
            except Exception:
                continue

            name = user.get("first_name", "friend")
            if len(reports) == 1:
                r = reports[0]
                loc = r["location"]
                days_ago = (datetime.now() - r["created_at"]).days if r.get("created_at") else 3
                text = (
                    f"Hey {name}, checking in on that {loc} pain you reported "
                    f"{days_ago} days ago. How's it feeling?\n\n"
                )
                if r.get("prescription"):
                    text += f"Have you been doing the mobility work? ({r['prescription'][:80]}...)\n\n"
                text += "If it's better, say 'resolved'. If it's the same or worse, let me know."
            else:
                locations = [r["location"] for r in reports]
                text = (
                    f"Hey {name}, you've got {len(reports)} active pain reports "
                    f"({', '.join(locations)}). Quick check-in — how are they feeling?\n\n"
                    "Tell me what's improved and I'll mark it resolved."
                )

            await typing_pause_bot(context.bot, user["telegram_user_id"], 0.7)
            await context.bot.send_message(
                chat_id=user["telegram_user_id"],
                text=text,
            )
            coaching_service.record_nudge(user["id"], 0, "pain_followup")
            logger.info(f"Pain follow-up sent to user {user['id']} ({len(reports)} reports)")

        except Exception as e:
            logger.error(f"Pain follow-up failed for user {user.get('id')}: {e}")


async def strava_sync_job(context):
    """Sync Strava activities for all connected users (every 2 hours)."""
    try:
        from bot.db.database import get_cursor
        from bot.services import strava_service

        with get_cursor() as cur:
            cur.execute("SELECT user_id FROM strava_tokens")
            rows = cur.fetchall()

        for row in rows:
            try:
                strava_service.sync_recent_activities(row["user_id"], days=3)
            except Exception as e:
                logger.warning(f"Strava sync failed for user {row['user_id']}: {e}")

        if rows:
            logger.info(f"Strava sync completed for {len(rows)} users")
    except Exception as e:
        logger.error(f"Strava sync job failed: {e}")


# --- Referral milestone notifications ---

async def referral_milestone_job(context):
    """Send pending referral milestone notifications (runs every hour)."""
    try:
        from bot.services import referral_service
        milestones = referral_service.get_pending_milestones()
        for m in milestones:
            try:
                await context.bot.send_message(
                    chat_id=m["referrer_telegram_id"],
                    text=f"{m['milestone_type']} of Pro — earned from your referrals. "
                         "Check /referral for your stats.",
                )
                referral_service.mark_milestone_sent(m["id"])
                logger.info(f"Milestone notification sent to {m['referrer_telegram_id']}: {m['milestone_type']}")
            except Exception as e:
                logger.warning(f"Failed to send milestone to {m['referrer_telegram_id']}: {e}")
    except Exception as e:
        logger.error(f"Referral milestone job failed: {e}")


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

    # End-of-day assessment: every 15 min
    jq.run_repeating(daily_assessment_job, interval=900, first=180, name="daily_assessment")

    # Smart nudges: every 2 hours
    jq.run_repeating(smart_nudge_job, interval=7200, first=300, name="smart_nudges")

    # Weekly insights: every 6 hours (self-skips on non-Sundays)
    jq.run_repeating(weekly_insights_job, interval=21600, first=600, name="weekly_insights")

    # Conversation summarization + pruning: every 2 hours
    jq.run_repeating(summarize_conversations_job, interval=7200, first=3600, name="summarize_conversations")

    # Stale session cleanup: every 2 hours
    jq.run_repeating(cleanup_sessions_job, interval=7200, first=600, name="cleanup_sessions")

    # Dose reminders: every 15 minutes (schedule-based with interactive cards)
    jq.run_repeating(dose_reminder_job, interval=900, first=120, name="dose_reminders")

    # Daily dose generation + missed dose marking: every 6 hours
    jq.run_repeating(generate_daily_doses_job, interval=21600, first=60, name="daily_dose_gen")

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

    # Gmail new email alerts: every 2 minutes
    jq.run_repeating(gmail_check_job, interval=120, first=90, name="gmail_alerts")

    # Pain/mobility follow-up: every 6 hours
    jq.run_repeating(pain_followup_job, interval=21600, first=1200, name="pain_followup")

    # Strava activity sync: every 2 hours
    jq.run_repeating(strava_sync_job, interval=7200, first=900, name="strava_sync")

    # Referral milestone notifications: every hour
    jq.run_repeating(referral_milestone_job, interval=3600, first=600, name="referral_milestones")

    logger.info("Proactive coaching jobs registered (19 jobs)")


# --- Helpers ---

def _get_tz(user: dict):
    """Get timezone for user, default UTC."""
    try:
        return ZoneInfo(user.get("timezone") or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _get_briefing_hint(user: dict) -> str:
    """Return a feature hint to append to the morning briefing prompt.

    Only returns a hint ~30% of mornings, and only for features the user
    hasn't tried yet. Prevents the briefing from feeling like an ad.
    """
    import random
    from bot.db.database import get_cursor

    # Only hint on ~30% of briefings
    if random.random() > 0.30:
        return ""

    user_id = user.get("id", 0)

    with get_cursor() as cur:
        # Don't hint if we already hinted today (via brain or previous briefing)
        cur.execute("""
            SELECT COUNT(*) as c FROM hint_log
            WHERE user_id = %s AND shown_at > NOW() - INTERVAL '1 day'
        """, (user_id,))
        if cur.fetchone()["c"] > 0:
            return ""

        # Build list of unused features
        hints = []

        cur.execute("SELECT COUNT(*) as c FROM whoop_tokens WHERE user_id = %s", (user_id,))
        if cur.fetchone()["c"] == 0:
            hints.append(("whoop", "If they wear a WHOOP, remind them they can connect it for recovery-based training adjustments — /connect_whoop"))

        cur.execute("SELECT COUNT(*) as c FROM habits WHERE user_id = %s", (user_id,))
        if cur.fetchone()["c"] == 0:
            hints.append(("habits", "Suggest they could track a daily habit (meditation, cold plunge, reading) and you'll track streaks"))

        cur.execute("SELECT COUNT(*) as c FROM meal_logs WHERE user_id = %s AND photo_analysis IS NOT NULL", (user_id,))
        if cur.fetchone()["c"] == 0:
            hints.append(("meal_photo", "Mention that if they snap a photo of breakfast, you'll log the macros automatically"))

        cur.execute("SELECT COUNT(*) as c FROM workout_sessions WHERE user_id = %s", (user_id,))
        if cur.fetchone()["c"] == 0:
            cur.execute("SELECT COUNT(*) as c FROM fitness_profiles WHERE user_id = %s", (user_id,))
            if cur.fetchone()["c"] > 0:
                hints.append(("workout_cards", "If there's a workout scheduled, mention you can load it up as interactive cards with rest timers"))

        try:
            from bot.services import google_auth
            if not google_auth.is_connected(user_id):
                hints.append(("google", "Suggest connecting Google (/google) so you can weave their calendar into the schedule"))
        except Exception:
            pass

        if not hints:
            return ""

        # Filter out hints already shown
        cur.execute("SELECT hint_key FROM hint_log WHERE user_id = %s", (user_id,))
        shown = {r["hint_key"] for r in cur.fetchall()}
        fresh = [(k, t) for k, t in hints if k not in shown]

        if not fresh:
            return ""

        hint_key, hint_text = random.choice(fresh)

        # Record the hint
        cur.execute("INSERT INTO hint_log (user_id, hint_key) VALUES (%s, %s)", (user_id, hint_key))

    return (
        f"\n\nOPTIONAL NUDGE (weave naturally into the briefing as a casual aside, one sentence max): "
        f"{hint_text}. Don't make it sound like a feature announcement — just a passing tip."
    )


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

        # Cross-domain intelligence data
        cross_domain = ""
        try:
            # Gmail unread count
            from bot.services.google_auth import is_connected, has_scopes
            if is_connected(user["id"]) and has_scopes(user["id"], ["https://www.googleapis.com/auth/gmail.readonly"]):
                from bot.services import google_workspace
                unread = google_workspace.get_unread_count(user["id"])
                if unread > 0:
                    cross_domain += f"Gmail: {unread} unread emails\n"
        except Exception:
            pass
        try:
            # Habit streaks
            from bot.services import habit_service
            habits = habit_service.get_habits(user["id"])
            if habits:
                done = sum(1 for h in habits if h.get("done_today"))
                active_streaks = [h for h in habits if (h.get("current_streak") or 0) >= 3]
                cross_domain += f"Habits: {done}/{len(habits)} done today"
                if active_streaks:
                    streak_names = ", ".join(f"{h['name']} ({h['current_streak']}d)" for h in active_streaks[:3])
                    cross_domain += f" | Active streaks: {streak_names}"
                cross_domain += "\n"
        except Exception:
            pass
        try:
            # Yesterday's spending
            from bot.services import expense_service
            from datetime import timedelta as td
            yesterday_total = expense_service.get_daily_total(user["id"], date.today() - td(days=1))
            if yesterday_total > 0:
                cross_domain += f"Yesterday's spending: \u20ac{yesterday_total:.0f}\n"
        except Exception:
            pass

        # Determine user's timezone for time-blocked schedule
        tz = _get_tz(user)
        now_local = datetime.now(tz)
        current_time = now_local.strftime("%I:%M %p")

        # Supplement/peptide data
        dose_section = ""
        try:
            from bot.services import biohacking_service
            protocols = biohacking_service.get_active_protocols(user["id"])
            supplements = biohacking_service.get_active_supplements(user["id"])
            dose_lines = []
            if protocols:
                dose_lines.append("Active peptide protocols:")
                for p in protocols[:5]:
                    dose_str = f"{p.get('dose_amount', '?')} {p.get('dose_unit', 'mcg')}" if p.get("dose_amount") else ""
                    freq = p.get("frequency", "")
                    dose_lines.append(f"- {p.get('peptide_name', 'Unknown')} {dose_str} ({freq})")
            if supplements:
                dose_lines.append("Active supplements:")
                for s in supplements[:5]:
                    dose_str = f"{s.get('dose_amount', '')}{s.get('dose_unit', '')}" if s.get("dose_amount") else ""
                    timing = s.get("timing", "")
                    dose_lines.append(f"- {s.get('supplement_name', 'Unknown')} {dose_str} ({timing})")
            if dose_lines:
                dose_section = "\n".join(dose_lines) + "\n\n"
        except Exception:
            pass

        # Optional: feature discovery hint for the briefing
        discovery_hint = ""
        try:
            discovery_hint = _get_briefing_hint(user)
        except Exception:
            pass

        prompt = (
            f"Generate a DETAILED morning routine and daily plan for {user.get('first_name', 'friend')}.\n"
            f"Current time: {current_time}\n\n"
            f"Tasks ({len(tasks)} total, {len(overdue)} overdue, {len(due_today)} due today):\n"
            + "\n".join(task_lines) + "\n\n"
            + cal_section
            + whoop_section
            + fitness_section
            + dose_section
            + cross_domain
            + f"Task streak: {streak.get('current_streak', 0)} days (best: {streak.get('longest_streak', 0)})\n"
            f"Most productive: {patterns.get('most_productive_day', 'varies')}\n"
            f"Best time: {patterns.get('preferred_time', 'varies')}\n\n"
            "FORMAT: Create a TIME-BLOCKED daily routine. Structure it like this:\n"
            "1. Start with a one-line greeting + WHOOP recovery status if available\n"
            "2. Then a time-blocked schedule from now through the day:\n"
            "   - Use actual times (8:00 AM, 9:30 AM, etc.)\n"
            "   - Include: supplements/doses, workouts, tasks, calendar events, meals/breaks\n"
            "   - Group related items into time blocks\n"
            "   - Adapt training intensity to WHOOP recovery if available\n"
            "3. End with one motivational line\n\n"
            "CROSS-DOMAIN INTELLIGENCE:\n"
            "- If WHOOP recovery < 40%: suggest lighter training, recovery-focused session\n"
            "- If recovery > 80%: suggest pushing harder, good day for PRs\n"
            "- If 5+ overdue tasks: front-load the most important ones\n"
            "- If habit streaks active: weave them into the schedule naturally\n"
            "- If calendar events exist: schedule tasks around them\n\n"
            "RULES:\n"
            "- Be specific with supplement names and doses\n"
            "- Be specific with task names (what to work on)\n"
            "- Use plain text, no markdown, no asterisks, no bullet hyphens\n"
            "- Separate sections with blank lines\n"
            "- Keep it under 800 chars total — concise but detailed\n"
            "- If a task title looks technical (env vars, API keys), describe it simply"
            + discovery_hint
        )

        lang_dir = _get_language_directive(user)
        response, error = _call_api(
            "You are Zoe, a personal performance coach. You create structured daily plans "
            "that are specific, actionable, and time-blocked. You know the user's supplements, "
            "workouts, tasks, and recovery data. Write like a real coach texting their client — "
            "warm but direct. Never say 'I'm an AI'. No markdown formatting." + lang_dir,
            [{"role": "user", "content": prompt}],
            max_tokens=350,
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

    lines = [f"Morning, {name}.\n"]
    lines.append(f"{total} active tasks.")
    if overdue:
        lines.append(f"{overdue} overdue")
    if due_today:
        lines.append(f"{due_today} due today")
    if high:
        lines.append(f"{high} high priority")
    if tasks:
        lines.append(f"\nI'd start with: {tasks[0]['title']}")
    lines.append("\nWhat are you tackling first?")
    return "\n".join(lines)


def _generate_daily_assessment(user):
    """Generate end-of-day assessment via AI with template fallback."""
    try:
        from bot.ai.brain_v2 import _call_api

        name = user.get("first_name", "friend")
        daily = coaching_service.get_daily_summary(user["id"])
        streak = coaching_service.get_streak(user["id"])

        # Fitness data
        fitness_section = ""
        try:
            from bot.services import fitness_service
            if fitness_service.has_workout_today(user["id"]):
                workouts = fitness_service.get_recent_workouts(user["id"], days=1)
                if workouts:
                    w = workouts[0]
                    dur = f" ({w['duration_minutes']}min)" if w.get("duration_minutes") else ""
                    fitness_section = f"Workout: {w['title']}{dur}\n"
            else:
                fitness_section = "No workout today\n"
            ws = fitness_service.get_workout_streak(user["id"])
            if ws.get("current_streak", 0) > 0:
                fitness_section += f"Workout streak: {ws['current_streak']} sessions\n"
        except Exception:
            pass

        # WHOOP recovery & sleep
        whoop_section = ""
        try:
            from bot.services import whoop_service
            if whoop_service.is_connected(user["id"]):
                recovery = whoop_service.get_today_recovery(user["id"])
                if recovery:
                    score = recovery.get("recovery_score", "?")
                    sleep = recovery.get("sleep_performance", "?")
                    hrv = recovery.get("hrv_rmssd", "?")
                    whoop_section = f"WHOOP: Recovery {score}%, Sleep {sleep}%, HRV {hrv}ms\n"
        except Exception:
            pass

        # Supplement adherence
        supplement_section = ""
        try:
            from bot.services import biohacking_service
            adherence = biohacking_service.get_supplement_adherence(user["id"], days=1)
            if adherence.get("overall_rate", 0) > 0:
                supplement_section = f"Supplement adherence today: {adherence['overall_rate']}%\n"
        except Exception:
            pass

        # Protocol doses
        protocol_section = ""
        try:
            from bot.services import biohacking_service as bh
            protocols = bh.get_protocol_summary(user["id"])
            if protocols:
                names = [p["peptide_name"] for p in protocols[:3]]
                protocol_section = f"Active protocols: {', '.join(names)}\n"
        except Exception:
            pass

        # Habit completion
        habit_section = ""
        try:
            from bot.services import habit_service
            habits = habit_service.get_habits(user["id"])
            if habits:
                done = sum(1 for h in habits if h.get("done_today"))
                habit_section = f"Habits: {done}/{len(habits)} completed today\n"
                incomplete = [h["name"] for h in habits if not h.get("done_today")]
                if incomplete:
                    habit_section += f"Missed: {', '.join(incomplete[:3])}\n"
        except Exception:
            pass

        # Today's spending
        spending_section = ""
        try:
            from bot.services import expense_service
            today_total = expense_service.get_daily_total(user["id"])
            if today_total > 0:
                spending_section = f"Spending today: \u20ac{today_total:.0f}\n"
        except Exception:
            pass

        # Build data block for AI
        data_block = (
            f"Tasks completed today: {daily['completed_today']}\n"
            f"Due today: {daily['due_today_done']}/{daily['due_today_total']} done\n"
            f"Overdue tasks: {daily['overdue']}\n"
            f"Task streak: {streak.get('current_streak', 0)} days\n"
            + fitness_section
            + whoop_section
            + supplement_section
            + protocol_section
            + habit_section
            + spending_section
        )

        prompt = (
            f"Generate an end-of-day assessment for {name}.\n\n"
            f"Today's data:\n{data_block}\n"
            "Write 3-4 SHORT paragraphs separated by blank lines (double newline).\n"
            "Each paragraph is 1-2 sentences MAX. Under 15 words per sentence.\n"
            "Total under 400 chars.\n\n"
            "Start with how the day went. Acknowledge what went well.\n"
            "If tasks were missed or overdue grew, address it gently.\n"
            "Mention habits completed/missed. Mention spending if logged.\n"
            "Correlate: recovery + training + sleep + productivity = overall readiness.\n"
            "End with ONE suggestion for tomorrow.\n\n"
            "CRITICAL: Use blank lines between paragraphs. NEVER write one continuous block of text.\n"
            "No markdown formatting. No asterisks, no hyphens as bullets."
        )

        lang_dir = _get_language_directive(user)
        response, error = _call_api(
            "You are Zoe, a thoughtful coach. End-of-day reviews: honest, warm, brief. "
            "Structure responses as short paragraphs separated by blank lines. "
            "Each paragraph is 1-2 sentences. Never write one continuous block. "
            "Never say 'I'm an AI'. No markdown formatting." + lang_dir,
            [{"role": "user", "content": prompt}],
            max_tokens=250,
        )

        if not error and response and response.content:
            return response.content[0].text

    except Exception as e:
        logger.error(f"AI daily assessment failed: {e}")

    # Template fallback
    return _template_daily_assessment(user)


def _template_daily_assessment(user):
    """Simple template assessment when AI is unavailable."""
    name = user.get("first_name", "friend")
    daily = coaching_service.get_daily_summary(user["id"])

    lines = [f"Day wrap, {name}.\n"]
    completed = daily["completed_today"]
    if completed > 0:
        lines.append(f"{completed} task{'s' if completed != 1 else ''} completed")
    else:
        lines.append("No tasks completed today.")

    due_total = daily["due_today_total"]
    due_done = daily["due_today_done"]
    if due_total > 0:
        lines.append(f"{due_done}/{due_total} due-today tasks done")

    overdue = daily["overdue"]
    if overdue > 0:
        lines.append(f"{overdue} still overdue")

    try:
        from bot.services import fitness_service
        if fitness_service.has_workout_today(user["id"]):
            lines.append("Got a workout in too.")
    except Exception:
        pass

    lines.append("\nRest up. Tomorrow's a fresh start.")
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
            "Write 2-3 SHORT paragraphs separated by blank lines (double newline).\n"
            "Each paragraph is 1-2 sentences. Under 15 words per sentence.\n"
            "Compare weeks. Give ONE actionable tip. Under 350 chars total.\n\n"
            "CRITICAL: Use blank lines between paragraphs. NEVER write one continuous block.\n"
            "No markdown formatting. No asterisks, no hyphens as bullets."
        )

        lang_dir = _get_language_directive(user)
        response, error = _call_api(
            "You are Zoe, a warm productivity companion. Brief, specific, calm. "
            "Structure responses as short paragraphs separated by blank lines. "
            "Each paragraph is 1-2 sentences. Never write one continuous block. "
            "Never say 'I'm an AI'. No markdown formatting." + lang_dir,
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
        f"Weekly recap, {name}.\n\n"
        f"{this_w} tasks completed ({trend} last week).\n"
        f"Most productive day: {stats.get('most_productive_day', 'varies')}\n"
        f"Currently overdue: {stats.get('current_overdue', 0)}\n\n"
        "Solid week. Let's build on it."
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
            "Write 3-4 SHORT paragraphs separated by blank lines (double newline).\n"
            "Each paragraph is 1-2 sentences. Under 15 words per sentence.\n"
            "Summarize the week. Note pattern imbalances. Celebrate PRs.\n"
            "Give ONE specific suggestion for next week. Under 500 chars total.\n\n"
            "CRITICAL: Use blank lines between paragraphs. NEVER write one continuous block.\n"
            "No markdown formatting. No asterisks, no hyphens as bullets."
        )

        lang_dir = _get_language_directive(user)
        response, error = _call_api(
            "You are Zoe, a warm fitness coach. Brief, specific, calm. "
            "Structure responses as short paragraphs separated by blank lines. "
            "Each paragraph is 1-2 sentences. Never write one continuous block. "
            "Never say 'I'm an AI'. No markdown formatting." + lang_dir,
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

    lines = [f"Weekly fitness recap, {name}.\n"]
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

    lines.append("\nLet's build on this next week.")
    return "\n".join(lines)
