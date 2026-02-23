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
                parse_mode="Markdown",
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

            lines = ["\U0001f916 **Quick nudge:**\n"]
            for t, ntype in nudges:
                if ntype == "overdue":
                    days = (today_d - t["due_date"]).days
                    lines.append(f"\U0001f534 \"{t['title']}\" is {days} days overdue")
                elif ntype == "no_due_date":
                    lines.append(f"\u26a1 \"{t['title']}\" is high priority but has no due date")
            lines.append("\n_Need help with any of these?_")

            await context.bot.send_message(
                chat_id=user["telegram_user_id"],
                text="\n".join(lines),
                parse_mode="Markdown",
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
                parse_mode="Markdown",
            )
            coaching_service.record_nudge(user["id"], 0, "weekly_insight")
            logger.info(f"Weekly insight sent to user {user['id']}")

        except Exception as e:
            logger.error(f"Weekly insight failed for user {user.get('id')}: {e}")


# --- Job Registration ---

def setup_proactive_jobs(application):
    """Register all proactive coaching jobs."""
    jq = application.job_queue

    # Morning briefing scanner: every 15 min
    jq.run_repeating(morning_briefing_job, interval=900, first=60, name="morning_briefing")

    # Evening check-in scanner: every 15 min
    jq.run_repeating(evening_check_in_job, interval=900, first=120, name="evening_check_in")

    # Smart nudges: every 2 hours
    jq.run_repeating(smart_nudge_job, interval=7200, first=300, name="smart_nudges")

    # Weekly insights: every 6 hours (self-skips on non-Sundays)
    jq.run_repeating(weekly_insights_job, interval=21600, first=600, name="weekly_insights")

    logger.info("Proactive coaching jobs registered")


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

        prompt = (
            f"Generate a morning briefing for {user.get('first_name', 'friend')}.\n\n"
            f"Tasks ({len(tasks)} total, {len(overdue)} overdue, {len(due_today)} due today):\n"
            + "\n".join(task_lines) + "\n\n"
            f"Streak: {streak.get('current_streak', 0)} days (best: {streak.get('longest_streak', 0)})\n"
            f"Most productive: {patterns.get('most_productive_day', 'varies')}\n"
            f"Best time: {patterns.get('preferred_time', 'varies')}\n\n"
            "Write 3-5 lines. Say which task to start with and why. "
            "Mention streak if > 0. Be casual like a friend texting. "
            "Use markdown bold. Under 500 chars."
        )

        response, error = _call_api(
            system_prompt="You are a supportive productivity coach. Brief, specific, casual.",
            messages=[{"role": "user", "content": prompt}],
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
    lines.append(f"You have **{total}** active tasks.")
    if overdue:
        lines.append(f"\U0001f534 {overdue} overdue")
    if due_today:
        lines.append(f"\U0001f4c5 {due_today} due today")
    if high:
        lines.append(f"\u26a1 {high} high priority")
    if tasks:
        lines.append(f"\nStart with: **{tasks[0]['title']}**")
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
            "Be encouraging. Markdown bold. Under 400 chars."
        )

        response, error = _call_api(
            system_prompt="You are a supportive productivity coach. Brief, specific, casual.",
            messages=[{"role": "user", "content": prompt}],
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
        f"**Weekly recap, {name}!**\n\n"
        f"You completed **{this_w}** tasks this week ({trend} last week).\n"
        f"Most productive day: {stats.get('most_productive_day', 'varies')}\n"
        f"Currently overdue: {stats.get('current_overdue', 0)}\n\n"
        "Keep it up!"
    )
