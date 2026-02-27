"""User onboarding — /start, /help, /settings, /account, /deleteaccount, /memory."""
import asyncio
import logging
import re
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
)
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.services import user_service
from bot.services import referral_service

logger = logging.getLogger(__name__)


async def _typing_pause(chat, seconds: float = 0.8):
    """Show typing indicator and pause — makes the bot feel human."""
    await chat.send_action(ChatAction.TYPING)
    await asyncio.sleep(seconds)



async def handle_otp_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input during onboarding — no longer used for OTP, kept as no-op."""
    pass


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
                        text=f"{tg_user.first_name} just joined through your referral link. "
                             f"You earned {referral_service.BONUS_MESSAGES_PER_REFERRAL} bonus messages.",
                    )
                except Exception:
                    pass  # Referrer may have blocked the bot
        except (ValueError, Exception) as e:
            logger.warning(f"Invalid referral payload: {payload} — {e}")

    # Returning user who completed onboarding
    if not user.get("onboarding_completed"):
        # Not verified yet — (re)start onboarding below
        pass
    else:

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
    chat = update.message.chat

    # Step 1: Welcome — split into 2 messages with typing pause
    await update.message.reply_text(
        f"Hey {tg_user.first_name}, I'm Zoe."
    )

    await _typing_pause(chat, 1.0)

    await _typing_pause(chat, 0.8)
    await update.message.reply_text(
        "I'm here to bring a little calm to the chaos \u2014 training, "
        "tasks, health tracking, all of it."
    )

    await _typing_pause(chat, 0.7)
    await update.message.reply_text(
        "Quick heads up \u2014 I educate and track, but I'm not a doctor. "
        "Always check with yours before starting something new."
    )

    await _typing_pause(chat, 0.6)

    # Telegram contact sharing (one tap, no OTP needed)
    phone_keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("\U0001f4f1 Share phone number", request_contact=True)]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "Share your number so I know who you are.",
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

    await _typing_pause(update.message.chat, 0.6)
    await update.message.reply_text(
        f"Friends referred: {stats['total_referrals']}\n"
        f"Bonus messages earned: {stats['bonus_messages']}\n\n"
        f"{tier_text}\n\n"
        f"Your link:\n"
        f"{stats['referral_link']}\n\n"
        "Share it around — you both get something out of it.",
    )


# ── /memory ──────────────────────────────────────────────────────────

CATEGORY_LABELS = {
    "preference": "Preferences",
    "personal": "Personal",
    "fitness": "Fitness",
    "health": "Health",
    "coaching": "Coaching",
    "goal": "Goals",
    "general": "Notes",
}


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show what Zoe remembers about the user. /memory clear to wipe all."""
    user = await _ensure_user(update, context)
    if not user:
        return

    from bot.services import memory_service

    args = context.args

    # /memory clear — wipe all memories
    if args and args[0].lower() == "clear":
        memories = memory_service.get_memories(user["id"], limit=200)
        if not memories:
            await update.message.reply_text("I don't have any memories about you yet.")
            return

        # Require confirmation
        if context.user_data.get("confirm_memory_clear"):
            count = 0
            for m in memories:
                memory_service.forget_memory(user["id"], m["id"])
                count += 1
            context.user_data.pop("confirm_memory_clear", None)
            await update.message.reply_text(
                f"Done. Cleared {count} memories. Starting fresh."
            )
            return
        else:
            context.user_data["confirm_memory_clear"] = True
            await update.message.reply_text(
                f"This will delete all {len(memories)} things I remember about you.\n\n"
                "Send /memory clear again to confirm."
            )
            return

    # /memory forget <text> — delete specific memories matching text
    if args and args[0].lower() == "forget" and len(args) > 1:
        search = " ".join(args[1:])
        deleted = memory_service.forget_by_content(user["id"], search)
        if deleted:
            await update.message.reply_text(
                f"Forgot {deleted} memory{'s' if deleted > 1 else ''} matching \"{search}\"."
            )
        else:
            await update.message.reply_text(
                f"No memories found matching \"{search}\"."
            )
        return

    # Default: show all memories
    memories = memory_service.get_memories(user["id"], limit=100)
    if not memories:
        await update.message.reply_text(
            "I don't know anything about you yet.\n\n"
            "The more we talk, the more I'll learn. I pick up on your goals, "
            "preferences, training details, and health info automatically."
        )
        return

    # Group by category
    by_category = {}
    for m in memories:
        cat = m["category"]
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(m["content"])

    lines = [f"Here's what I know about you ({len(memories)} memories)\n"]
    for cat, items in by_category.items():
        label = CATEGORY_LABELS.get(cat, cat.title())
        lines.append(f"{label}:")
        for item in items:
            lines.append(f"  {item}")
        lines.append("")

    lines.append(
        "To forget something: /memory forget <text>\n"
        "To clear everything: /memory clear"
    )

    await _typing_pause(update.message.chat, 0.8)
    await update.message.reply_text("\n".join(lines))


# ── Contact handler (phone verification) ─────────────────────────────

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle shared contact — store phone number during onboarding."""
    # Don't use _ensure_user — this is called DURING onboarding before completion
    user = context.user_data.get("db_user")
    if not user:
        tg_user = update.effective_user
        user = user_service.get_or_create_user(
            telegram_user_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
        )
        context.user_data["db_user"] = user

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

    await _typing_pause(update.message.chat, 0.6)
    await update.message.reply_text(
        "Got it.",
        reply_markup=ReplyKeyboardRemove(),
    )

    # Proceed to segmentation (Step 2)
    await _send_segmentation(update.message, context)


# ── Onboarding step senders ──────────────────────────────────────────

async def _send_segmentation(message, context):
    """Step 2: Ask what brings them here."""
    await _typing_pause(message.chat, 0.8)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Training & fitness", callback_data="ob:focus:fit")],
        [InlineKeyboardButton("Tasks & reminders", callback_data="ob:focus:tasks")],
        [InlineKeyboardButton("All of it", callback_data="ob:focus:all")],
    ])
    await message.reply_text(
        "What are you here for?",
        reply_markup=keyboard,
    )


async def _send_goal(message, context):
    """Step 3: Fitness goal."""
    await _typing_pause(message.chat, 0.7)
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
        "Nice. What's the main thing you're working towards?",
        reply_markup=keyboard,
    )


async def _send_experience(message, context):
    """Step 4: Training experience."""
    await _typing_pause(message.chat, 0.6)
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
    await _typing_pause(message.chat, 0.6)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("2\u20133", callback_data="ob:days:3"),
            InlineKeyboardButton("4", callback_data="ob:days:4"),
            InlineKeyboardButton("5", callback_data="ob:days:5"),
            InlineKeyboardButton("6+", callback_data="ob:days:6"),
        ],
    ])
    await message.reply_text(
        "How many days a week do you usually hit it?",
        reply_markup=keyboard,
    )


async def _send_equipment(message, context):
    """Step 6: What equipment do they have access to?"""
    await _typing_pause(message.chat, 0.7)
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
        "What are you working with equipment-wise?",
        reply_markup=keyboard,
    )


async def _send_style(message, context):
    """Step 7: Preferred training style."""
    await _typing_pause(message.chat, 0.6)
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
        "What kind of training are you into?",
        reply_markup=keyboard,
    )


async def _send_injuries(message, context):
    """Step 8: Any injuries or limitations?"""
    await _typing_pause(message.chat, 0.7)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Shoulder stuff", callback_data="ob:injury:shoulder")],
        [InlineKeyboardButton("Knee stuff", callback_data="ob:injury:knee")],
        [InlineKeyboardButton("Back stuff", callback_data="ob:injury:back")],
        [InlineKeyboardButton("Nah, I'm good", callback_data="ob:injury:none")],
    ])
    await message.reply_text(
        "Anything I should work around? Injuries, tight spots, old stuff that flares up?",
        reply_markup=keyboard,
    )


async def _send_biohacking(message, context):
    """Step 9: Do they track peptides/supplements/bloodwork?"""
    await _typing_pause(message.chat, 0.8)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Peptides", callback_data="ob:bio:peptides")],
        [InlineKeyboardButton("Supplements", callback_data="ob:bio:supps")],
        [InlineKeyboardButton("Both", callback_data="ob:bio:both")],
        [InlineKeyboardButton("Neither", callback_data="ob:bio:none")],
    ])
    await message.reply_text(
        "Last one on the health side — are you running any peptides or supplements?",
        reply_markup=keyboard,
    )


async def _send_timezone(message, context):
    """Step 10: Timezone via location share."""
    await _typing_pause(message.chat, 0.8)

    # Inline skip button
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Skip for now", callback_data="ob:tz:skip")],
    ])
    await message.reply_text(
        "Almost done \u2014 share your location so I can time your reminders right. "
        "You can always change it later.",
        reply_markup=keyboard,
    )

    await _typing_pause(message.chat, 0.5)

    # Reply keyboard for location share
    location_keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("\U0001f4cd Share my location", request_location=True)]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await message.reply_text(
        "Tap below.",
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

    # ── Build personalized done message — delivered in 2-3 messages ──
    chat = message.chat

    await _typing_pause(chat, 1.0)

    if focus in ("fit", "all"):
        goal_text = GOAL_DISPLAY.get(ob.get("goal"), "getting stronger")
        days = ob.get("days", 3)
        exp = ob.get("experience", "intermediate")
        equip = EQUIP_DISPLAY.get(ob.get("equipment"), "")
        style = STYLE_DISPLAY.get(ob.get("style"), "")

        # Message 1: confirmation
        profile_parts = [f"{goal_text}, {days}x a week, {exp}"]
        if equip:
            profile_parts.append(f"{equip}")
        if style:
            profile_parts.append(f"{style}")

        await message.reply_text(
            f"Got it, {first_name}. {', '.join(profile_parts)}.",
            reply_markup=ReplyKeyboardRemove(),
        )

        # Message 2: injury/bio acknowledgments
        extra_lines = []
        injury = ob.get("injury")
        if injury and injury != "none":
            extra_lines.append(f"I'll work around your {injury} \u2014 every session I program will account for it.")

        bio = ob.get("biohacking")
        if bio == "peptides":
            extra_lines.append("Tell me what peptides you're running and I'll track everything.")
        elif bio == "supps":
            extra_lines.append("Tell me what supplements you take and I'll track your stack.")
        elif bio == "both":
            extra_lines.append("Tell me what you're running \u2014 peptides, supplements, all of it. I'll track everything.")

        if extra_lines:
            await _typing_pause(chat, 0.8)
            await message.reply_text("\n\n".join(extra_lines))

        # Message 3: what to do next
        await _typing_pause(chat, 1.0)
        next_text = "Try \"what should I train today?\" or just tell me what you did \u2014 \"bench 4x8 at 80kg\" and I'll take it from there."
        if focus == "all":
            next_text += "\n\nFor tasks and reminders, just talk \u2014 \"buy groceries tomorrow\" or \"remind me to call the clinic at 3pm.\""
        await message.reply_text(next_text)

    else:
        # Tasks-only track
        await message.reply_text(
            f"You're good to go, {first_name}.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await _typing_pause(chat, 0.8)
        await message.reply_text(
            "Just talk to me \u2014 \"buy groceries tomorrow\", "
            "\"remind me to call the clinic at 3pm\", or "
            "\"what should I focus on today?\" and I'll handle it."
        )

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
            # Phone skip (fallback mode only, when Twilio not configured)
            await _typing_pause(query.message.chat, 0.5)
            await query.message.reply_text(
                "No worries.",
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

    # ── Settings callbacks ──
    if query.data.startswith("settings:"):
        user = context.user_data.get("db_user")
        if not user:
            tg = update.effective_user
            user = user_service.get_or_create_user(tg.id, tg.username, tg.first_name)
            context.user_data["db_user"] = user

        setting = query.data.split(":")[1]

        if setting == "briefing":
            # Show morning briefing time options
            buttons = []
            for h in [5, 6, 7, 8, 9, 10]:
                buttons.append(InlineKeyboardButton(_format_hour(h), callback_data=f"settime:briefing:{h}"))
            keyboard = InlineKeyboardMarkup([buttons[:3], buttons[3:]])
            await query.message.edit_text(
                "What time do you want your morning briefing?",
                reply_markup=keyboard,
            )
            return

        elif setting == "checkin":
            # Show evening check-in time options
            buttons = []
            for h in [18, 19, 20, 21, 22, 23]:
                buttons.append(InlineKeyboardButton(_format_hour(h), callback_data=f"settime:checkin:{h}"))
            keyboard = InlineKeyboardMarkup([buttons[:3], buttons[3:]])
            await query.message.edit_text(
                "What time do you want your evening check-in?",
                reply_markup=keyboard,
            )
            return

        elif setting == "timezone":
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("London", callback_data="tz:Europe/London"),
                    InlineKeyboardButton("Lisbon", callback_data="tz:Europe/Lisbon"),
                    InlineKeyboardButton("Paris", callback_data="tz:Europe/Paris"),
                ],
                [
                    InlineKeyboardButton("New York", callback_data="tz:America/New_York"),
                    InlineKeyboardButton("Chicago", callback_data="tz:America/Chicago"),
                    InlineKeyboardButton("LA", callback_data="tz:America/Los_Angeles"),
                ],
                [
                    InlineKeyboardButton("Dubai", callback_data="tz:Asia/Dubai"),
                    InlineKeyboardButton("Tokyo", callback_data="tz:Asia/Tokyo"),
                    InlineKeyboardButton("Sydney", callback_data="tz:Australia/Sydney"),
                ],
            ])
            await query.message.edit_text(
                "Pick your timezone:",
                reply_markup=keyboard,
            )
            return

    # Time setting callbacks (from settings)
    if query.data.startswith("settime:"):
        user = context.user_data.get("db_user")
        if not user:
            tg = update.effective_user
            user = user_service.get_or_create_user(tg.id, tg.username, tg.first_name)
            context.user_data["db_user"] = user

        parts = query.data.split(":")
        setting_type = parts[1]
        hour = int(parts[2])

        if setting_type == "briefing":
            user_service.update_settings(user["id"], briefing_hour=hour)
            user["briefing_hour"] = hour
            context.user_data["db_user"] = user
            await query.message.edit_text(f"Morning briefing set to {_format_hour(hour)}.")
        elif setting_type == "checkin":
            user_service.update_settings(user["id"], check_in_hour=hour)
            user["check_in_hour"] = hour
            context.user_data["db_user"] = user
            await query.message.edit_text(f"Evening check-in set to {_format_hour(hour)}.")
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
        await query.message.edit_text(f"Timezone set to {short_name}. Good to go.")
        return

    if query.data == "show_help":
        await _typing_pause(query.message.chat, 0.6)
        await query.message.reply_text(
            "Just talk to me, send a voice note, or use commands.\n\n"
            "TASKS\n"
            "/add \u2014 add a task\n"
            "/list \u2014 all your tasks\n"
            "/today \u2014 due today\n"
            "/done \u2014 mark complete\n\n"
            "FITNESS & BIOHACKING\n"
            "/workout \u2014 log a workout\n"
            "/gains \u2014 streak, PRs & patterns\n"
            "/protocols \u2014 peptide protocols\n"
            "/supplements \u2014 supplement stack\n"
            "/recovery \u2014 WHOOP recovery score\n\n"
            "ACCOUNT\n"
            "/settings \u2014 timezone & preferences\n"
            "/upgrade \u2014 unlock Zoe Pro\n\n"
            "Type /help for the full list.",
        )
    elif query.data == "show_calendar":
        await _typing_pause(query.message.chat, 0.5)
        await query.message.reply_text(
            "Connect your Google Calendar so I can see your schedule.\n\n"
            "1. Open Google Calendar on desktop\n"
            "2. Settings (gear icon) > your calendar name\n"
            "3. Scroll to 'Secret address in iCal format'\n"
            "4. Copy the URL and send it to me:\n\n"
            "/calendar https://calendar.google.com/calendar/ical/..."
        )
    elif query.data == "show_capabilities":
        await _typing_pause(query.message.chat, 0.6)
        await query.message.reply_text(
            "Short version \u2014 I handle fitness, tasks, and biohacking.\n\n"
            "TASKS: manage tasks, set reminders, sync Google Calendar, voice messages\n\n"
            "FITNESS: log workouts, track movement patterns, detect PRs, program training based on your history\n\n"
            "BIOHACKING (Pro): peptide protocols, supplement tracking, bloodwork with biomarker trends\n\n"
            "WHOOP (Pro): recovery-based training, HRV + sleep + strain tracking, connects everything together\n\n"
            "Just talk to me and I'll figure out the rest.",
        )


# ── /help ─────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show available commands."""
    await _typing_pause(update.message.chat, 0.7)
    await update.message.reply_text(
        "I'm Zoe \u2014 your personal coach.\n\n"
        "Talk to me naturally, send a voice note, or use commands.\n\n"
        "TASKS\n"
        "/add \u2014 add a task\n"
        "/list \u2014 all your tasks\n"
        "/today \u2014 due today\n"
        "/done \u2014 mark complete\n"
        "/streak \u2014 completion streak\n\n"
        "FITNESS\n"
        "/workout \u2014 log a workout\n"
        "/gains \u2014 streak, PRs & pattern balance\n"
        "/metrics \u2014 body metrics\n\n"
        "BIOHACKING\n"
        "/protocols \u2014 active peptide protocols\n"
        "/supplements \u2014 supplement stack\n"
        "/bloodwork \u2014 latest bloodwork\n"
        "/dose \u2014 log a peptide dose\n\n"
        "WHOOP\n"
        "/connect_whoop \u2014 link your WHOOP\n"
        "/recovery \u2014 today's recovery score\n"
        "/whoop \u2014 full dashboard\n\n"
        "Or just tell me naturally:\n"
        '  "bench 4x8 at 75kg, rows 4x10"\n'
        '  "took my BPC-157"\n'
        '  "what should I train today?"\n\n'
        "ACCOUNT\n"
        "/memory \u2014 what I know about you\n"
        "/settings \u2014 timezone & preferences\n"
        "/upgrade \u2014 unlock Zoe Pro\n"
        "/support \u2014 get help",
    )


# ── /settings ─────────────────────────────────────────────────────────

def _format_hour(h: int) -> str:
    """Format hour int to human-readable (e.g. 8 -> '8:00 AM', 20 -> '8:00 PM')."""
    if h == 0:
        return "12:00 AM"
    elif h < 12:
        return f"{h}:00 AM"
    elif h == 12:
        return "12:00 PM"
    else:
        return f"{h - 12}:00 PM"


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show and manage user settings."""
    user = await _ensure_user(update, context)
    if not user:
        return

    # Handle text-based setting changes first
    args = context.args
    if args and len(args) >= 2:
        if args[0] == "timezone":
            user_service.update_settings(user["id"], timezone=args[1])
            await update.message.reply_text(f"Timezone updated to {args[1]}")
            return
        elif args[0] == "briefing":
            try:
                hour = int(args[1])
                if 0 <= hour <= 23:
                    user_service.update_settings(user["id"], briefing_hour=hour)
                    await update.message.reply_text(f"Morning briefing set to {_format_hour(hour)}")
                    return
            except ValueError:
                pass

    tz = user.get("timezone", "UTC")
    tz_short = tz.split("/")[-1].replace("_", " ")
    briefing = user.get("briefing_hour", 8)
    checkin = user.get("check_in_hour", 20)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Morning briefing: {_format_hour(briefing)}", callback_data="settings:briefing")],
        [InlineKeyboardButton(f"Evening check-in: {_format_hour(checkin)}", callback_data="settings:checkin")],
        [InlineKeyboardButton(f"Timezone: {tz_short}", callback_data="settings:timezone")],
    ])

    await _typing_pause(update.message.chat, 0.6)
    await update.message.reply_text(
        "Your settings — tap to change:",
        reply_markup=keyboard,
    )


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
        f"Messages today: {ai_str}\n"
        f"Member since: {user['created_at'].strftime('%b %d, %Y')}"
    )

    if tier == "free":
        text += "\n\nWant unlimited everything? /upgrade"

    await _typing_pause(update.message.chat, 0.7)
    await update.message.reply_text(text)


# ── /deleteaccount ────────────────────────────────────────────────────

async def cmd_delete_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete user account and all data (GDPR). Bypasses onboarding check."""
    # Don't use _ensure_user — users must be able to delete even if stuck in onboarding
    user = context.user_data.get("db_user")
    if not user:
        tg_user = update.effective_user
        user = user_service.get_or_create_user(
            telegram_user_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
        )
        context.user_data["db_user"] = user

    # Require confirmation
    if context.user_data.get("confirm_delete"):
        user_service.delete_user(user["id"])
        context.user_data.clear()
        await _typing_pause(update.message.chat, 0.5)
        await update.message.reply_text(
            "Done. Everything's been wiped \u2014 account, tasks, all of it. "
            "If you ever wanna come back, just /start again."
        )
    else:
        context.user_data["confirm_delete"] = True
        await update.message.reply_text(
            "This will permanently delete your account, all tasks, "
            "conversation history, and usage data.\n\n"
            "Send /deleteaccount again to confirm."
        )


# ── /google ──────────────────────────────────────────────────────────

async def cmd_google(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Connect, view, or disconnect Google Workspace."""
    user = await _ensure_user(update, context)
    if not user:
        return

    from bot.services import google_auth

    args = context.args

    # Disconnect
    if args and args[0].lower() == "disconnect":
        google_auth.revoke_access(user["id"])
        await _typing_pause(update.message.chat, 0.5)
        await update.message.reply_text("Google disconnected.")
        return

    # Switch account — disconnect + reconnect
    if args and args[0].lower() == "switch":
        google_auth.revoke_access(user["id"])
        if google_auth.is_configured():
            url = google_auth.get_auth_url(user["id"])
            if url:
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("Connect a different Google account", url=url)]
                ])
                await _typing_pause(update.message.chat, 0.5)
                await update.message.reply_text(
                    "Previous account disconnected.\n\n"
                    "Tap below to connect a different Google account.",
                    reply_markup=keyboard,
                )
                return
        await update.message.reply_text("Previous account disconnected.")
        return

    if not google_auth.is_configured():
        await update.message.reply_text(
            "Google integration isn't set up yet."
        )
        return

    # Already connected — check scope level
    if google_auth.is_connected(user["id"]):
        full_scopes = [
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/tasks",
            "https://www.googleapis.com/auth/documents",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
        has_full = google_auth.has_scopes(user["id"], full_scopes)

        if has_full:
            await _typing_pause(update.message.chat, 0.5)
            await update.message.reply_text(
                "Google Workspace is connected.\n"
                "Calendar, Gmail, Drive, Tasks, Docs — all good.\n\n"
                "Switch account: /google switch\n"
                "Disconnect: /google disconnect"
            )
        else:
            # Connected with limited scopes — prompt upgrade
            url = google_auth.get_auth_url(user["id"])
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Upgrade Google permissions", url=url)]
            ])
            await _typing_pause(update.message.chat, 0.6)
            await update.message.reply_text(
                "Google Calendar is connected, but with limited access.\n\n"
                "Upgrade to also use Gmail, Drive, Tasks, and Docs.",
                reply_markup=keyboard,
            )
        return

    # Not connected — show connect button
    url = google_auth.get_auth_url(user["id"])
    if url:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Connect Google Workspace", url=url)]
        ])
        await _typing_pause(update.message.chat, 0.6)
        await update.message.reply_text(
            "Connect Google so I can access your Calendar, Gmail, "
            "Drive, Tasks, and Docs.\n\n"
            "Tap below, sign in, and authorize.",
            reply_markup=keyboard,
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

    # Switch account — disconnect + reconnect
    if args and args[0].lower() == "switch":
        calendar_service.revoke_access(user["id"])
        if calendar_service.is_configured():
            url = calendar_service.get_auth_url(user["id"])
            if url:
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("Connect a different Google account", url=url)]
                ])
                await _typing_pause(update.message.chat, 0.5)
                await update.message.reply_text(
                    "Previous calendar disconnected.\n\n"
                    "Tap below to connect a different Google account.",
                    reply_markup=keyboard,
                )
                return
        await update.message.reply_text("Previous calendar disconnected.")
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
            lines.append("\nSwitch account: /calendar switch")
            lines.append("Disconnect: /calendar disconnect")
            await update.message.reply_text("\n".join(lines))
        else:
            await update.message.reply_text(
                "Your Google Calendar is connected.\n\n"
                "No upcoming events in the next 3 days.\n\n"
                "Switch account: /calendar switch\n"
                "Disconnect: /calendar disconnect"
            )
        return

    # Not connected — show OAuth button or iCal instructions
    if calendar_service.is_configured():
        url = calendar_service.get_auth_url(user["id"])
        if url:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Connect Google Calendar", url=url)]
            ])
            await _typing_pause(update.message.chat, 0.6)
            await update.message.reply_text(
                "Connect your Google Calendar so I can see your schedule "
                "for morning briefings and planning.\n\n"
                "Tap below, sign in with Google, and authorize.",
                reply_markup=keyboard,
            )
            return

    # Fallback: iCal instructions
    await _typing_pause(update.message.chat, 0.6)
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
    # Don't use _ensure_user — this may be called DURING onboarding (timezone step)
    user = context.user_data.get("db_user")
    if not user:
        tg_user = update.effective_user
        user = user_service.get_or_create_user(
            telegram_user_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
        )
        context.user_data["db_user"] = user

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
    await _typing_pause(update.message.chat, 0.4)
    await update.message.reply_text(
        f"Timezone set to {short_name}. I'll use that for reminders and briefings.",
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
    """Get the DB user from context or create one.

    Returns None (and sends a message) if onboarding isn't complete.
    """
    user = context.user_data.get("db_user")
    if not user:
        tg_user = update.effective_user
        user = user_service.get_or_create_user(
            telegram_user_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
        )
        context.user_data["db_user"] = user

    if not user.get("onboarding_completed"):
        await update.message.reply_text(
            "You need to verify your phone number first. Type /start to begin."
        )
        return None
    return user
