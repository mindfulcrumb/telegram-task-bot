"""User onboarding — /start, /help, /settings, /account, /deleteaccount."""
import logging
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
)
from telegram.ext import ContextTypes

from bot.services import user_service
from bot.services import referral_service

logger = logging.getLogger(__name__)

# ── Onboarding mappings ──────────────────────────────────────────────

GOAL_MAP = {
    "muscle": "hypertrophy",
    "strength": "strength",
    "cut": "fat_loss",
    "health": "general_health",
    "athletic": "athletic_performance",
}

GOAL_DISPLAY = {
    "hypertrophy": "building muscle",
    "strength": "getting stronger",
    "fat_loss": "cutting fat",
    "general_health": "staying healthy",
    "athletic_performance": "athletic performance",
}

EXP_MAP = {
    "beg": "beginner",
    "int": "intermediate",
    "adv": "advanced",
}

EQUIP_MAP = {
    "full": "full_gym",
    "home": "home_gym",
    "bw": "bodyweight_only",
    "kb": "kettlebells_dumbbells",
}

EQUIP_DISPLAY = {
    "full_gym": "full gym",
    "home_gym": "home gym setup",
    "bodyweight_only": "bodyweight only",
    "kettlebells_dumbbells": "kettlebells & dumbbells",
}

STYLE_MAP = {
    "power": "powerlifting",
    "bb": "bodybuilding",
    "func": "functional",
    "hybrid": "hybrid",
}

STYLE_DISPLAY = {
    "powerlifting": "powerlifting",
    "bodybuilding": "bodybuilding",
    "functional": "functional training",
    "hybrid": "a mix of everything",
}


# ── /start ────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start — create account, start onboarding or welcome back."""
    tg_user = update.effective_user
    user = user_service.get_or_create_user(
        telegram_user_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name,
    )
    context.user_data["db_user"] = user

    # Handle referral deep link: /start ref_12345
    payload = context.args[0] if context.args else ""
    if payload.startswith("ref_"):
        try:
            referrer_id = int(payload.replace("ref_", ""))
            result = referral_service.track_referral(referrer_id, tg_user.id)
            if result:
                # Notify referrer
                try:
                    await context.bot.send_message(
                        chat_id=referrer_id,
                        text=f"*{tg_user.first_name}* just joined Zoe through your referral link! "
                             f"You earned {referral_service.BONUS_MESSAGES_PER_REFERRAL} bonus messages.",
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass  # Referrer may have blocked the bot
        except (ValueError, Exception) as e:
            logger.warning(f"Invalid referral payload: {payload} — {e}")

    # Returning user who completed onboarding (or existing pre-onboarding user)
    is_existing = (
        user.get("onboarding_completed")
        or (user.get("last_active") is not None
            and user["created_at"] != user["last_active"])
    )

    if is_existing:
        # One-time migration: mark existing users as onboarding complete
        if not user.get("onboarding_completed"):
            user_service.mark_onboarding_complete(user["id"])

        from bot.services import task_service
        tasks = task_service.get_tasks(user["id"])
        count = len(tasks)
        overdue = sum(
            1 for t in tasks
            if t.get("due_date")
            and t["due_date"].isoformat() < __import__("datetime").date.today().isoformat()
        )

        status = f"You have {count} active task{'s' if count != 1 else ''}"
        if overdue:
            status += f" ({overdue} overdue)"
        status += "."

        await update.message.reply_text(
            f"Hey {tg_user.first_name}, welcome back. {status}"
        )
        return

    # ── New user — start conversational onboarding ──
    context.user_data["ob"] = {}

    # Step 1: Welcome + disclaimer + phone verification
    phone_keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("\U0001f4f1 Share phone number", request_contact=True)]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await update.message.reply_text(
        f"Hey {tg_user.first_name}, I'm Zoe.\n\n"
        "Your AI coach for training, tasks, and everything in between.\n\n"
        "Quick note: I'm here to educate and track, not to give medical advice. "
        "Always check with your doctor before starting anything new.\n\n"
        "Tap below to get started.",
        reply_markup=phone_keyboard,
    )


# ── /referral ─────────────────────────────────────────────────────────

async def cmd_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show referral stats and share link."""
    user = await _ensure_user(update, context)
    if not user:
        return

    stats = referral_service.get_referral_stats(user["telegram_user_id"])

    tier_text = ""
    if stats["next_tier"]:
        tier_text = f"{stats['referrals_to_next']} more to earn {stats['next_tier']['reward']}"
    elif stats["current_tier"]:
        tier_text = f"You've earned {stats['current_tier']['reward']}!"

    await update.message.reply_text(
        "*Your Referral Stats*\n\n"
        f"Friends referred: *{stats['total_referrals']}*\n"
        f"Bonus messages earned: *{stats['bonus_messages']}*\n\n"
        f"{tier_text}\n\n"
        f"*Your referral link:*\n"
        f"`{stats['referral_link']}`\n\n"
        "Share this link — you both benefit!",
        parse_mode="Markdown",
    )


# ── Contact handler (phone verification) ─────────────────────────────

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle shared contact — store phone number for verification."""
    user = await _ensure_user(update, context)
    if not user:
        return

    contact = update.message.contact
    if not contact:
        return

    phone = contact.phone_number

    # Anti-abuse: check if phone already linked to another account
    if user_service.phone_number_exists(phone, exclude_user_id=user["id"]):
        await update.message.reply_text(
            "This number is already connected to another account.\n"
            "Reach out to /support if this is an error.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # Store phone number
    user_service.set_phone_number(user["id"], phone)

    await update.message.reply_text(
        "Connected.",
        reply_markup=ReplyKeyboardRemove(),
    )

    # Proceed to segmentation (Step 2)
    await _send_segmentation(update.message, context)


# ── Onboarding step senders ──────────────────────────────────────────

async def _send_segmentation(message, context):
    """Step 2: Ask what brings them here."""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Training & fitness", callback_data="ob:focus:fit")],
        [InlineKeyboardButton("Tasks & productivity", callback_data="ob:focus:tasks")],
        [InlineKeyboardButton("All of it", callback_data="ob:focus:all")],
    ])
    await message.reply_text(
        "What are you most interested in?",
        reply_markup=keyboard,
    )


async def _send_goal(message, context):
    """Step 3: Fitness goal."""
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Build muscle", callback_data="ob:goal:muscle"),
            InlineKeyboardButton("Get stronger", callback_data="ob:goal:strength"),
        ],
        [
            InlineKeyboardButton("Lose fat", callback_data="ob:goal:cut"),
            InlineKeyboardButton("Athletic performance", callback_data="ob:goal:athletic"),
        ],
        [InlineKeyboardButton("Stay healthy", callback_data="ob:goal:health")],
    ])
    await message.reply_text(
        "What's your main goal right now?",
        reply_markup=keyboard,
    )


async def _send_experience(message, context):
    """Step 4: Training experience."""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Under a year", callback_data="ob:exp:beg")],
        [InlineKeyboardButton("1\u20133 years", callback_data="ob:exp:int")],
        [InlineKeyboardButton("3+ years", callback_data="ob:exp:adv")],
    ])
    await message.reply_text(
        "How long have you been training consistently?",
        reply_markup=keyboard,
    )


async def _send_frequency(message, context):
    """Step 5: Training frequency."""
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("2\u20133", callback_data="ob:days:3"),
            InlineKeyboardButton("4", callback_data="ob:days:4"),
            InlineKeyboardButton("5", callback_data="ob:days:5"),
            InlineKeyboardButton("6+", callback_data="ob:days:6"),
        ],
    ])
    await message.reply_text(
        "How many days a week do you usually train?",
        reply_markup=keyboard,
    )


async def _send_equipment(message, context):
    """Step 6: What equipment do they have access to?"""
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Full gym", callback_data="ob:equip:full"),
            InlineKeyboardButton("Home gym", callback_data="ob:equip:home"),
        ],
        [
            InlineKeyboardButton("KBs & dumbbells", callback_data="ob:equip:kb"),
            InlineKeyboardButton("Bodyweight only", callback_data="ob:equip:bw"),
        ],
    ])
    await message.reply_text(
        "What equipment do you have access to?",
        reply_markup=keyboard,
    )


async def _send_style(message, context):
    """Step 7: Preferred training style."""
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Powerlifting", callback_data="ob:style:power"),
            InlineKeyboardButton("Bodybuilding", callback_data="ob:style:bb"),
        ],
        [
            InlineKeyboardButton("Functional", callback_data="ob:style:func"),
            InlineKeyboardButton("Mix of everything", callback_data="ob:style:hybrid"),
        ],
    ])
    await message.reply_text(
        "What kind of training do you prefer?",
        reply_markup=keyboard,
    )


async def _send_injuries(message, context):
    """Step 8: Any injuries or limitations?"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Shoulder issues", callback_data="ob:injury:shoulder")],
        [InlineKeyboardButton("Knee issues", callback_data="ob:injury:knee")],
        [InlineKeyboardButton("Back issues", callback_data="ob:injury:back")],
        [InlineKeyboardButton("Nope, I'm good", callback_data="ob:injury:none")],
    ])
    await message.reply_text(
        "Any injuries or limitations I should know about?",
        reply_markup=keyboard,
    )


async def _send_biohacking(message, context):
    """Step 9: Do they track peptides/supplements/bloodwork?"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Peptides", callback_data="ob:bio:peptides")],
        [InlineKeyboardButton("Supplements", callback_data="ob:bio:supps")],
        [InlineKeyboardButton("Both", callback_data="ob:bio:both")],
        [InlineKeyboardButton("Neither", callback_data="ob:bio:none")],
    ])
    await message.reply_text(
        "Do you track peptides or supplements?",
        reply_markup=keyboard,
    )


async def _send_timezone(message, context):
    """Step 10: Timezone via location share."""
    # Inline skip button
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Skip for now", callback_data="ob:tz:skip")],
    ])
    await message.reply_text(
        "Last thing \u2014 share your location so reminders hit at the right time.\n\n"
        "You can always set it later with /settings.",
        reply_markup=keyboard,
    )

    # Reply keyboard for location share
    location_keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("\U0001f4cd Share my location", request_location=True)]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await message.reply_text(
        "Tap below to share your location.",
        reply_markup=location_keyboard,
    )


async def _complete_onboarding(message, context, user):
    """Final step: Save all data, seed memories, send personalized done message."""
    ob = context.user_data.get("ob", {})
    user_id = user["id"]
    first_name = user.get("first_name", "friend")
    focus = ob.get("focus", "all")

    # Save fitness profile if fitness track
    if focus in ("fit", "all") and ob.get("goal"):
        from bot.services import fitness_service
        fitness_service.update_fitness_profile(
            user_id,
            fitness_goal=ob.get("goal"),
            experience_level=ob.get("experience", "intermediate"),
            training_days_per_week=ob.get("days", 3),
            equipment=ob.get("equipment"),
            preferred_style=ob.get("style"),
            limitations=ob.get("injury"),
        )

    # Mark onboarding complete
    user_service.mark_onboarding_complete(user_id)
    user["onboarding_completed"] = True
    context.user_data["db_user"] = user

    # ── Seed initial memories from onboarding data ──
    try:
        from bot.services import memory_service
        memories = []

        # Fitness-related memories
        if focus in ("fit", "all"):
            goal_text = GOAL_DISPLAY.get(ob.get("goal"), "general fitness")
            exp = ob.get("experience", "intermediate")
            days = ob.get("days", 3)
            memories.append((f"Main goal: {goal_text}", "goal"))
            memories.append((f"Experience level: {exp}, trains {days}x/week", "fitness"))

            equip = ob.get("equipment")
            if equip:
                equip_text = EQUIP_DISPLAY.get(equip, equip)
                memories.append((f"Equipment access: {equip_text}", "fitness"))

            style = ob.get("style")
            if style:
                style_text = STYLE_DISPLAY.get(style, style)
                memories.append((f"Preferred training style: {style_text}", "preference"))

            injury = ob.get("injury")
            if injury and injury != "none":
                memories.append((f"Has {injury} issues — program around this", "health"))

        # Biohacking detection
        bio = ob.get("biohacking")
        if bio and bio != "none":
            if bio == "peptides":
                memories.append(("Tracks peptide protocols", "health"))
            elif bio == "supps":
                memories.append(("Tracks supplements", "health"))
            elif bio == "both":
                memories.append(("Tracks both peptides and supplements", "health"))

        # Focus area
        if focus == "tasks":
            memories.append(("Primarily uses Zoe for task management", "preference"))
        elif focus == "fit":
            memories.append(("Primarily uses Zoe for fitness coaching", "preference"))
        elif focus == "all":
            memories.append(("Uses Zoe for both fitness and task management", "preference"))

        for content, category in memories:
            memory_service.save_memory(
                user_id=user_id,
                content=content,
                category=category,
                source="onboarding",
                confidence=1.0,
            )
        if memories:
            logger.info(f"Seeded {len(memories)} memories from onboarding for user {user_id}")

    except Exception as e:
        logger.warning(f"Failed to seed onboarding memories for user {user_id}: {e}")

    # ── Build personalized done message ──
    if focus in ("fit", "all"):
        goal_text = GOAL_DISPLAY.get(ob.get("goal"), "getting stronger")
        days = ob.get("days", 3)
        exp = ob.get("experience", "intermediate")
        equip = EQUIP_DISPLAY.get(ob.get("equipment"), "")
        style = STYLE_DISPLAY.get(ob.get("style"), "")

        profile_line = f"I've got you down for {goal_text}, {days}x a week, {exp} level."
        if equip:
            profile_line += f"\n{equip.title()} access."
        if style:
            profile_line += f" {style.title()} style."

        bio = ob.get("biohacking")
        bio_line = ""
        if bio == "peptides":
            bio_line = "\n\nI can track your peptide protocols and doses too \u2014 just tell me what you're running."
        elif bio == "supps":
            bio_line = "\n\nI can track your supplement stack \u2014 just tell me what you take."
        elif bio == "both":
            bio_line = "\n\nI'll track your peptides and supplements \u2014 just tell me what you're running."

        injury = ob.get("injury")
        injury_line = ""
        if injury and injury != "none":
            injury_line = f"\n\nI'll program around your {injury} \u2014 every session will account for it."

        text = (
            f"You're all set, {first_name}.\n\n"
            f"{profile_line}{injury_line}{bio_line}\n\n"
            "Try \"What should I train today?\" or "
            "\"I did bench 4x8 at 80kg\" and I'll take it from there."
        )
        if focus == "all":
            text += (
                "\n\nFor tasks, just tell me naturally \u2014 "
                "\"Buy groceries tomorrow\" or \"Remind me about X at 3pm.\""
            )
    else:
        text = (
            f"You're all set, {first_name}.\n\n"
            "Try \"Buy groceries tomorrow\" or "
            "\"What should I focus on today?\" and I'll handle it."
        )

    await message.reply_text(text, reply_markup=ReplyKeyboardRemove())

    # Clean up transient state
    context.user_data.pop("ob", None)


# ── Callback handler ─────────────────────────────────────────────────

async def handle_onboarding_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks from onboarding (ob:*) and legacy."""
    query = update.callback_query
    await query.answer()

    # ── Onboarding flow callbacks ──
    if query.data.startswith("ob:"):
        ob = context.user_data.get("ob")
        if not ob and ob != {}:
            # Stale button after onboarding completed
            return

        # Ensure we have a user
        user = context.user_data.get("db_user")
        if not user:
            tg = update.effective_user
            user = user_service.get_or_create_user(tg.id, tg.username, tg.first_name)
            context.user_data["db_user"] = user

        parts = query.data.split(":")
        if len(parts) < 3:
            return

        step = parts[1]
        value = parts[2]

        if step == "phone":
            # Phone skip
            await query.message.reply_text(
                "No problem \u2014 you can always add it later.",
                reply_markup=ReplyKeyboardRemove(),
            )
            await _send_segmentation(query.message, context)

        elif step == "focus":
            ob["focus"] = value
            if value == "tasks":
                # Tasks-only track — skip fitness questions, go to timezone
                await _send_timezone(query.message, context)
            else:
                # Fitness track (fit or all) — start fitness questions
                await _send_goal(query.message, context)

        elif step == "goal":
            ob["goal"] = GOAL_MAP.get(value, "general_health")
            await _send_experience(query.message, context)

        elif step == "exp":
            ob["experience"] = EXP_MAP.get(value, "intermediate")
            await _send_frequency(query.message, context)

        elif step == "days":
            ob["days"] = int(value)
            await _send_equipment(query.message, context)

        elif step == "equip":
            ob["equipment"] = EQUIP_MAP.get(value, "full_gym")
            await _send_style(query.message, context)

        elif step == "style":
            ob["style"] = STYLE_MAP.get(value, "hybrid")
            await _send_injuries(query.message, context)

        elif step == "injury":
            ob["injury"] = value if value != "none" else None
            await _send_biohacking(query.message, context)

        elif step == "bio":
            ob["biohacking"] = value if value != "none" else None
            await _send_timezone(query.message, context)

        elif step == "tz":
            # Timezone skip
            await _complete_onboarding(query.message, context, user)

        return

    # ── Legacy callbacks ──

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
        user["timezone"] = tz_value
        context.user_data["db_user"] = user
        short_name = tz_value.split("/")[-1].replace("_", " ")
        await query.message.edit_text(f"Timezone set to {short_name}. You're all set!")
        return

    if query.data == "show_help":
        await query.message.reply_text(
            "Just talk to me, send a voice note, or use commands:\n\n"
            "*Tasks*\n"
            "/add \u2014 Add a task\n"
            "/list \u2014 All your tasks\n"
            "/today \u2014 Due today\n"
            "/done \u2014 Mark complete\n\n"
            "*Fitness & Biohacking*\n"
            "/workout \u2014 Log a workout\n"
            "/gains \u2014 Streak, PRs & patterns\n"
            "/protocols \u2014 Peptide protocols\n"
            "/supplements \u2014 Supplement stack\n"
            "/recovery \u2014 WHOOP recovery score\n\n"
            "*Account*\n"
            "/settings \u2014 Timezone & preferences\n"
            "/upgrade \u2014 Unlock Zoe Pro\n\n"
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
            "Here's what I can do:\n\n"
            "*Tasks & Productivity*\n"
            "- Manage tasks naturally (just tell me)\n"
            "- Set reminders, recurring tasks, scheduling\n"
            "- Voice messages, Google Calendar sync\n\n"
            "*Fitness & Training*\n"
            "- Log workouts with sets/reps/weight\n"
            "- Track movement pattern balance (push/pull/squat/hinge)\n"
            "- Progressive overload, PR detection, workout streaks\n"
            "- Program your training based on your history\n\n"
            "*Biohacking (Pro)*\n"
            "- Peptide protocol tracking & dose logging\n"
            "- Supplement stack management & adherence\n"
            "- Bloodwork logging with biomarker trends\n\n"
            "*WHOOP Integration (Pro)*\n"
            "- Recovery-based training recommendations\n"
            "- HRV, sleep, and strain tracking\n"
            "- Recovery + protocols + bloodwork connected\n\n"
            "*With Zoe Pro:*\n"
            "- AI workout programming & fitness coaching\n"
            "- Peptide, supplement & bloodwork intelligence\n"
            "- WHOOP-powered recovery coaching\n"
            "- Morning briefings & weekly reports\n"
            "- Unlimited everything\n\n"
            "Just start talking to me \u2014 I'll figure out the rest.",
            parse_mode="Markdown"
        )


# ── /help ─────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show available commands."""
    await update.message.reply_text(
        "I'm Zoe \u2014 your AI performance coach.\n\n"
        "Talk to me naturally, send a voice note, or use commands:\n\n"
        "*Tasks*\n"
        "/add \u2014 Add a task\n"
        "/list \u2014 All your tasks\n"
        "/today \u2014 Due today\n"
        "/done \u2014 Mark complete\n"
        "/streak \u2014 Completion streak\n\n"
        "*Fitness*\n"
        "/workout \u2014 Log a workout\n"
        "/gains \u2014 Streak, PRs & pattern balance\n"
        "/metrics \u2014 Body metrics\n\n"
        "*Biohacking*\n"
        "/protocols \u2014 Active peptide protocols\n"
        "/supplements \u2014 Supplement stack\n"
        "/bloodwork \u2014 Latest bloodwork\n"
        "/dose \u2014 Log a peptide dose\n\n"
        "*WHOOP*\n"
        "/connect\\_whoop \u2014 Link your WHOOP\n"
        "/recovery \u2014 Today's recovery score\n"
        "/whoop \u2014 Full WHOOP dashboard\n\n"
        "Just tell me naturally:\n"
        '  "Did bench 4x8 at 75kg, rows 4x10"\n'
        '  "Took my BPC-157"\n'
        '  "What should I train today?"\n'
        '  "My testosterone came back at 650"\n\n'
        "*Account*\n"
        "/settings \u2014 Timezone & preferences\n"
        "/upgrade \u2014 Unlock Zoe Pro\n"
        "/support \u2014 Get help\n\n"
        "*Zoe Pro* \u2014 AI fitness coaching, peptide tracking, "
        "WHOOP integration, bloodwork intelligence, morning briefings, unlimited everything",
        parse_mode="Markdown"
    )


# ── /settings ─────────────────────────────────────────────────────────

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


# ── /account ──────────────────────────────────────────────────────────

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


# ── /deleteaccount ────────────────────────────────────────────────────

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


# ── /calendar ─────────────────────────────────────────────────────────

async def cmd_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Connect or disconnect Google Calendar."""
    user = await _ensure_user(update, context)
    if not user:
        return

    from bot.services import calendar_service

    args = context.args

    # Disconnect
    if args and args[0].lower() == "disconnect":
        calendar_service.revoke_access(user["id"])
        await update.message.reply_text("Calendar disconnected.")
        return

    # Legacy iCal URL support (if OAuth not configured)
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
                lines.append(f"  {e['title']} \u2014 {time_str}")
            await update.message.reply_text("\n".join(lines))
        else:
            await update.message.reply_text(
                "Connected! No upcoming events in the next 3 days, "
                "but I'll check your calendar when planning your day."
            )
        return

    # Already connected — show status
    if calendar_service.is_connected(user["id"]):
        events = calendar_service.fetch_upcoming_events(user["id"], days=3)
        if events:
            lines = ["Your Google Calendar is connected.\n"]
            for e in events[:5]:
                dt = e["start"]
                time_str = dt.strftime("%b %d") if e.get("all_day") else dt.strftime("%b %d %I:%M %p")
                lines.append(f"  {e['title']} \u2014 {time_str}")
            lines.append("\nTo disconnect: /calendar disconnect")
            await update.message.reply_text("\n".join(lines))
        else:
            await update.message.reply_text(
                "Your Google Calendar is connected.\n\n"
                "No upcoming events in the next 3 days.\n\n"
                "To disconnect: /calendar disconnect"
            )
        return

    # Not connected — show OAuth button or iCal instructions
    if calendar_service.is_configured():
        url = calendar_service.get_auth_url(user["id"])
        if url:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Connect Google Calendar", url=url)]
            ])
            await update.message.reply_text(
                "Connect your Google Calendar so I can see your schedule "
                "for morning briefings and planning.\n\n"
                "Tap below, sign in with Google, and authorize.",
                reply_markup=keyboard,
            )
            return

    # Fallback: iCal instructions
    await update.message.reply_text(
        "Connect your Google Calendar so I can see your schedule.\n\n"
        "1. Open Google Calendar on desktop\n"
        "2. Settings (gear icon) > your calendar name\n"
        "3. Scroll to 'Secret address in iCal format'\n"
        "4. Copy the URL and send it to me:\n\n"
        "/calendar https://calendar.google.com/calendar/ical/..."
    )


# ── Location handler ──────────────────────────────────────────────────

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

    # Check if this is during onboarding
    ob = context.user_data.get("ob")
    if ob is not None:
        await update.message.reply_text(
            f"Timezone set to {short_name}.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await _complete_onboarding(update.message, context, user)
        return

    # Normal (non-onboarding) location handling
    await update.message.reply_text(
        f"Got it! Timezone set to {short_name} ({tz}).\n"
        "Reminders and briefings will use this timezone.",
        reply_markup=ReplyKeyboardRemove(),
    )


# ── Timezone helper ───────────────────────────────────────────────────

def _timezone_from_coords(lat: float, lon: float) -> str:
    """Best-effort timezone from coordinates using known city regions."""
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

    # Fallback: estimate from longitude
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


# ── Helper ────────────────────────────────────────────────────────────

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
