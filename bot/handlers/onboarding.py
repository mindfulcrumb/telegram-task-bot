"""User onboarding — /start, /help, /settings, /account, /deleteaccount."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes

from bot.services import user_service

logger = logging.getLogger(__name__)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start — create account or welcome back."""
    tg_user = update.effective_user
    user = user_service.get_or_create_user(
        telegram_user_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name,
    )
    context.user_data["db_user"] = user

    # Check if this is a brand new user (created just now)
    is_new = user.get("last_active") is None or user["created_at"] == user["last_active"]

    if is_new:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Add my first task", switch_inline_query_current_chat="add ")],
            [
                InlineKeyboardButton("See all commands", callback_data="show_help"),
                InlineKeyboardButton("Connect calendar", callback_data="show_calendar"),
            ],
            [InlineKeyboardButton("What can you do?", callback_data="show_capabilities")],
        ])
        await update.message.reply_text(
            f"Hey {tg_user.first_name}, I'm Zoe.\n\n"
            "I'm here to bring a little calm to the chaos — "
            "tell me what's on your mind and I'll help you stay on top of it.\n\n"
            "You can talk to me naturally, send a voice note, or use commands. "
            "Whatever feels easiest.\n\n"
            "Try something like:\n"
            '  "Buy groceries tomorrow"\n'
            '  "Remind me about the dentist at 3pm"\n'
            '  "What should I focus on today?"',
            reply_markup=keyboard,
        )

        # Request location for automatic timezone detection
        location_keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton("Share my location", request_location=True)]],
            one_time_keyboard=True,
            resize_keyboard=True,
        )
        await update.message.reply_text(
            "One quick thing — tap the button below so I can set your timezone automatically.\n"
            "This way reminders and briefings arrive at the right time.",
            reply_markup=location_keyboard,
        )
    else:
        from bot.services import task_service
        tasks = task_service.get_tasks(user["id"])
        count = len(tasks)
        overdue = sum(1 for t in tasks if t.get("due_date") and t["due_date"].isoformat() < __import__("datetime").date.today().isoformat())

        status = f"You have {count} active task{'s' if count != 1 else ''}"
        if overdue:
            status += f" ({overdue} overdue)"
        status += "."

        await update.message.reply_text(f"Hey {tg_user.first_name}, welcome back. {status}")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show available commands."""
    await update.message.reply_text(
        "I'm Zoe — your intelligent companion for everyday clarity.\n\n"
        "Just talk to me, send a voice note, or use commands:\n\n"
        "*Manage tasks*\n"
        "/add — Add a task\n"
        "/list — All your tasks\n"
        "/today — Due today\n"
        "/week — This week\n"
        "/overdue — Past due\n"
        "/done — Mark complete\n"
        "/edit — Change a task\n"
        "/delete — Remove a task\n"
        "/undo — Undo last action\n"
        "/streak — Completion streak\n"
        "/analyze — AI task analysis\n\n"
        "*Your account*\n"
        "/calendar — Connect Google Calendar\n"
        "/settings — Timezone & preferences\n"
        "/account — Plan & usage\n"
        "/upgrade — Unlock Zoe Pro\n"
        "/support — Get help\n\n"
        "*Zoe Pro* — morning briefings, evening check-ins, "
        "smart reminders, weekly insights, unlimited everything",
        parse_mode="Markdown"
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show and manage user settings."""
    user = await _ensure_user(update, context)
    if not user:
        return

    await update.message.reply_text(
        f"Your settings:\n\n"
        f"Timezone: {user.get('timezone', 'UTC')}\n"
        f"Daily briefing: {user.get('briefing_hour', 8)}:00\n"
        f"Tier: {user.get('tier', 'free').title()}\n\n"
        "To change timezone: /settings timezone Europe/Lisbon\n"
        "To change briefing hour: /settings briefing 9"
    )

    # Handle setting changes
    args = context.args
    if args and len(args) >= 2:
        if args[0] == "timezone":
            user_service.update_settings(user["id"], timezone=args[1])
            await update.message.reply_text(f"Timezone updated to {args[1]}")
        elif args[0] == "briefing":
            try:
                hour = int(args[1])
                if 0 <= hour <= 23:
                    user_service.update_settings(user["id"], briefing_hour=hour)
                    await update.message.reply_text(f"Daily briefing set to {hour}:00")
            except ValueError:
                pass


async def cmd_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show account and subscription info."""
    user = await _ensure_user(update, context)
    if not user:
        return

    from bot.services import task_service, tier_service
    task_count = task_service.count_active_tasks(user["id"])
    ai_used = tier_service.get_usage_today(user["id"], "ai_message")
    tier = user.get("tier", "free")
    limits = tier_service.LIMITS.get(tier, tier_service.LIMITS["free"])

    task_limit = limits["max_tasks"]
    ai_limit = limits["max_ai_messages_per_day"]

    task_str = f"{task_count}/{task_limit}" if task_limit else f"{task_count} (unlimited)"
    ai_str = f"{ai_used}/{ai_limit}" if ai_limit else f"{ai_used} (unlimited)"

    text = (
        f"Account: {user.get('first_name', 'User')}\n"
        f"Tier: {tier.title()}\n"
        f"Active tasks: {task_str}\n"
        f"AI messages today: {ai_str}\n"
        f"Member since: {user['created_at'].strftime('%b %d, %Y')}"
    )

    if tier == "free":
        text += "\n\nWant unlimited tasks and AI? /upgrade"

    await update.message.reply_text(text)


async def cmd_delete_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete user account and all data (GDPR)."""
    user = await _ensure_user(update, context)
    if not user:
        return

    # Require confirmation
    if context.user_data.get("confirm_delete"):
        user_service.delete_user(user["id"])
        context.user_data.clear()
        await update.message.reply_text(
            "Your account and all data have been permanently deleted. "
            "If you ever want to come back, just /start again."
        )
    else:
        context.user_data["confirm_delete"] = True
        await update.message.reply_text(
            "This will permanently delete your account, all tasks, "
            "conversation history, and usage data.\n\n"
            "Send /deleteaccount again to confirm."
        )


async def handle_onboarding_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks from onboarding."""
    query = update.callback_query
    await query.answer()

    # Timezone selection
    if query.data.startswith("tz:"):
        tz_value = query.data[3:]
        if tz_value == "request_location":
            await query.message.reply_text(
                "Send me your location and I'll figure out your timezone.\n\n"
                "Tap the paperclip (attach) > Location > Send My Current Location.\n\n"
                "Or type it manually: /settings timezone Europe/Lisbon"
            )
            return
        user = context.user_data.get("db_user")
        if not user:
            tg = update.effective_user
            user = user_service.get_or_create_user(tg.id, tg.username, tg.first_name)
            context.user_data["db_user"] = user
        user_service.update_settings(user["id"], timezone=tz_value)
        # Update cached user
        user["timezone"] = tz_value
        context.user_data["db_user"] = user
        short_name = tz_value.split("/")[-1].replace("_", " ")
        await query.message.edit_text(f"Timezone set to {short_name}. You're all set!")
        return

    if query.data == "show_help":
        await query.message.reply_text(
            "Just talk to me, send a voice note, or use commands:\n\n"
            "*Manage tasks*\n"
            "/add — Add a task\n"
            "/list — All your tasks\n"
            "/today — Due today\n"
            "/done — Mark complete\n"
            "/streak — Completion streak\n\n"
            "*Your account*\n"
            "/calendar — Connect Google Calendar\n"
            "/settings — Timezone & preferences\n"
            "/upgrade — Unlock Zoe Pro\n\n"
            "Type /help for the full list.",
            parse_mode="Markdown"
        )
    elif query.data == "show_calendar":
        await query.message.reply_text(
            "Connect your Google Calendar so I can see your schedule.\n\n"
            "1. Open Google Calendar on desktop\n"
            "2. Settings (gear icon) > your calendar name\n"
            "3. Scroll to 'Secret address in iCal format'\n"
            "4. Copy the URL and send it to me:\n\n"
            "/calendar https://calendar.google.com/calendar/ical/..."
        )
    elif query.data == "show_capabilities":
        await query.message.reply_text(
            "Here's what I can do for you:\n\n"
            "- Manage your tasks naturally (just tell me what to do)\n"
            "- Set reminders ('remind me at 3pm')\n"
            "- Handle recurring tasks ('every Monday submit report')\n"
            "- Reschedule things ('move dentist to Friday')\n"
            "- Understand voice messages\n"
            "- Connect to your Google Calendar\n"
            "- Track your completion streaks\n\n"
            "*With Zoe Pro:*\n"
            "- Personalized morning briefings\n"
            "- Evening accountability check-ins\n"
            "- Smart nudges when things slip\n"
            "- Weekly performance insights\n\n"
            "Just start talking to me — I'll figure out the rest.",
            parse_mode="Markdown"
        )


async def cmd_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Connect or disconnect Google Calendar via iCal URL."""
    user = await _ensure_user(update, context)
    if not user:
        return

    from bot.services import calendar_service

    args = context.args
    if args and args[0].lower() == "disconnect":
        calendar_service.remove_calendar_url(user["id"])
        await update.message.reply_text("Calendar disconnected.")
        return

    if args and args[0].startswith("http"):
        url = args[0]
        if "calendar.google.com" not in url and ".ics" not in url:
            await update.message.reply_text(
                "That doesn't look like a Google Calendar URL.\n\n"
                "Make sure it ends in .ics or comes from calendar.google.com"
            )
            return

        calendar_service.save_calendar_url(user["id"], url)
        events = calendar_service.fetch_upcoming_events(user["id"], days=3)

        if events:
            lines = [f"Connected! I can see {len(events)} upcoming events:\n"]
            for e in events[:5]:
                dt = e["start"]
                time_str = dt.strftime("%b %d") if e.get("all_day") else dt.strftime("%b %d %I:%M %p")
                lines.append(f"  {e['title']} — {time_str}")
            await update.message.reply_text("\n".join(lines))
        else:
            await update.message.reply_text(
                "Connected! No upcoming events in the next 3 days, "
                "but I'll check your calendar when planning your day."
            )
        return

    # No args — show instructions
    current = calendar_service.get_calendar_url(user["id"])
    if current:
        await update.message.reply_text(
            "Your Google Calendar is connected.\n\n"
            "I check it for your morning briefings and when you ask about your schedule.\n\n"
            "To disconnect: /calendar disconnect"
        )
    else:
        await update.message.reply_text(
            "Connect your Google Calendar so I can see your schedule.\n\n"
            "1. Open Google Calendar on desktop\n"
            "2. Settings (gear icon) > your calendar name\n"
            "3. Scroll to 'Secret address in iCal format'\n"
            "4. Copy the URL and send it to me:\n\n"
            "/calendar https://calendar.google.com/calendar/ical/..."
        )


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle shared location — detect timezone automatically."""
    user = await _ensure_user(update, context)
    if not user:
        return

    loc = update.message.location
    if not loc:
        return

    tz = _timezone_from_coords(loc.latitude, loc.longitude)
    user_service.update_settings(user["id"], timezone=tz)
    user["timezone"] = tz
    context.user_data["db_user"] = user

    short_name = tz.split("/")[-1].replace("_", " ")
    await update.message.reply_text(
        f"Got it! Timezone set to {short_name} ({tz}).\n"
        "Reminders and briefings will use this timezone.",
        reply_markup=ReplyKeyboardRemove(),
    )


def _timezone_from_coords(lat: float, lon: float) -> str:
    """Best-effort timezone from coordinates using known city regions."""
    # Major timezone regions by rough lat/lon bounding boxes
    zones = [
        # Americas
        (24, 50, -130, -115, "America/Los_Angeles"),
        (31, 49, -115, -102, "America/Denver"),
        (25, 49, -102, -87, "America/Chicago"),
        (25, 49, -87, -67, "America/New_York"),
        (-5, 12, -83, -60, "America/Bogota"),
        (-35, -5, -74, -35, "America/Sao_Paulo"),
        (-56, -22, -74, -53, "America/Argentina/Buenos_Aires"),
        (14, 33, -118, -86, "America/Mexico_City"),
        (43, 84, -141, -52, "America/Toronto"),
        # Europe
        (50, 61, -8, 2, "Europe/London"),
        (36, 44, -10, 0, "Europe/Lisbon"),
        (42, 51, -2, 8, "Europe/Paris"),
        (47, 55, 6, 15, "Europe/Berlin"),
        (36, 47, 6, 19, "Europe/Rome"),
        (35, 42, 19, 30, "Europe/Athens"),
        (55, 71, 20, 32, "Europe/Helsinki"),
        (46, 62, 30, 45, "Europe/Moscow"),
        # Middle East / Africa
        (29, 38, 34, 40, "Asia/Jerusalem"),
        (21, 32, 39, 56, "Asia/Dubai"),
        # Asia
        (8, 37, 68, 90, "Asia/Kolkata"),
        (18, 54, 97, 106, "Asia/Bangkok"),
        (1, 7, 100, 120, "Asia/Singapore"),
        (18, 54, 108, 135, "Asia/Shanghai"),
        (30, 46, 129, 146, "Asia/Tokyo"),
        # Oceania
        (-45, -10, 113, 154, "Australia/Sydney"),
        (-47, -34, 166, 179, "Pacific/Auckland"),
    ]

    for lat_min, lat_max, lon_min, lon_max, tz in zones:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return tz

    # Fallback: estimate from longitude (15 degrees per hour)
    offset = round(lon / 15)
    fallback_map = {
        -12: "Pacific/Baker_Island", -11: "Pacific/Midway",
        -10: "Pacific/Honolulu", -9: "America/Anchorage",
        -8: "America/Los_Angeles", -7: "America/Denver",
        -6: "America/Chicago", -5: "America/New_York",
        -4: "America/Halifax", -3: "America/Sao_Paulo",
        -2: "Atlantic/South_Georgia", -1: "Atlantic/Azores",
        0: "Europe/London", 1: "Europe/Paris",
        2: "Europe/Athens", 3: "Europe/Moscow",
        4: "Asia/Dubai", 5: "Asia/Karachi",
        6: "Asia/Dhaka", 7: "Asia/Bangkok",
        8: "Asia/Shanghai", 9: "Asia/Tokyo",
        10: "Australia/Sydney", 11: "Pacific/Noumea",
        12: "Pacific/Auckland",
    }
    return fallback_map.get(offset, "UTC")


async def _ensure_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    """Get the DB user from context or create one. Returns user dict."""
    user = context.user_data.get("db_user")
    if user:
        return user

    tg_user = update.effective_user
    user = user_service.get_or_create_user(
        telegram_user_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name,
    )
    context.user_data["db_user"] = user
    return user
