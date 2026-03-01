"""Interactive peptide protocol cards — wizard, dashboard, dose reminders, quick dose."""
import html
import logging
from datetime import datetime, date, time as dt_time, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

_PARSE = "HTML"


# ══════════════════════════════════════════════════════════════════
# PROTOCOL WIZARD — multi-step guided creation
# Callback prefix: pw:
# State: context.user_data["pw"]
# ══════════════════════════════════════════════════════════════════

# Peptide categories for the reference DB
_CATEGORIES = [
    ("recovery", "Recovery"),
    ("gh", "GH / Growth"),
    ("cognitive", "Cognitive"),
    ("weight_loss", "Weight Loss"),
    ("longevity", "Longevity"),
]

_FREQUENCIES = [
    ("daily", "Daily"),
    ("2x_daily", "2x Daily"),
    ("eod", "Every Other Day"),
    ("3x_weekly", "3x Weekly (MWF)"),
    ("5on2off", "5 on / 2 off"),
]

_ROUTES = [
    ("subcutaneous", "Subcutaneous"),
    ("intramuscular", "IM"),
    ("nasal", "Nasal"),
    ("oral", "Oral"),
]

_MORNING_HOURS = [6, 7, 8, 9, 10]
_EVENING_HOURS = [18, 19, 20, 21, 22]


async def start_protocol_wizard(chat, context, peptide_hint: str = None):
    """Entry point: start the guided protocol creation flow."""
    context.user_data["pw"] = {}

    if peptide_hint:
        # Try to find in reference DB
        match = _lookup_reference(peptide_hint)
        if match:
            context.user_data["pw"]["peptide_slug"] = match["slug"]
            context.user_data["pw"]["peptide_name"] = match["name"]
            context.user_data["pw"]["_ref"] = match
            await _send_dose(chat, context)
            return

    await _send_categories(chat, context)


async def _send_categories(chat, context):
    """Step 1: Show peptide categories."""
    buttons = []
    for code, label in _CATEGORIES:
        buttons.append([InlineKeyboardButton(label, callback_data=f"pw:cat:{code}")])
    buttons.append([InlineKeyboardButton("Custom peptide", callback_data="pw:cat:custom")])

    text = "<b>New Protocol</b>\n\nWhat type of peptide?"
    msg = await chat.send_message(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=_PARSE)
    context.user_data["pw"]["_msg_id"] = msg.message_id


async def _send_peptide_list(query, context, category: str):
    """Step 2: Show peptides from reference DB in this category."""
    from bot.db.database import get_cursor

    category_map = {
        "recovery": ["recovery", "healing"],
        "gh": ["growth-hormone", "gh-secretagogue", "gh"],
        "cognitive": ["cognitive", "nootropic"],
        "weight_loss": ["weight-loss", "metabolic", "glp-1"],
        "longevity": ["longevity", "anti-aging"],
    }
    search_cats = category_map.get(category, [category])

    with get_cursor() as cur:
        cur.execute(
            """SELECT slug, name FROM peptide_reference
               WHERE categories && %s
               ORDER BY name LIMIT 8""",
            (search_cats,)
        )
        peptides = [dict(row) for row in cur.fetchall()]

    if not peptides:
        # No reference data for this category — fall back to custom
        context.user_data["pw"]["awaiting"] = "custom_peptide"
        await query.edit_message_text(
            "<b>New Protocol</b>\n\nType the peptide name:",
            parse_mode=_PARSE,
        )
        return

    buttons = []
    for p in peptides:
        buttons.append([InlineKeyboardButton(
            p["name"], callback_data=f"pw:pep:{p['slug']}"
        )])
    buttons.append([InlineKeyboardButton("Other", callback_data="pw:pep:custom")])

    await query.edit_message_text(
        "<b>New Protocol</b>\n\nSelect peptide:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=_PARSE,
    )


async def _send_dose(chat_or_query, context):
    """Step 3: Dose selection (pre-filled from reference DB if available)."""
    pw = context.user_data["pw"]
    ref = pw.get("_ref")
    name = html.escape(pw.get("peptide_name", "Peptide"))

    buttons = []
    if ref and ref.get("standard_dose"):
        std = ref["standard_dose"]
        buttons.append([InlineKeyboardButton(
            f"Suggested: {std}", callback_data="pw:dose:ref"
        )])
    buttons.append([InlineKeyboardButton("Custom dose", callback_data="pw:dose:custom")])

    text = f"<b>New Protocol</b>\n{name}\n\nDose?"

    if hasattr(chat_or_query, "edit_message_text"):
        await chat_or_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=_PARSE)
    else:
        await chat_or_query.send_message(
            text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=_PARSE)


async def _send_frequency(query, context):
    """Step 4: Frequency selection."""
    pw = context.user_data["pw"]
    name = html.escape(pw.get("peptide_name", "Peptide"))
    dose_str = f"{pw.get('dose_amount', '?')}{pw.get('dose_unit', 'mcg')}"

    buttons = []
    for code, label in _FREQUENCIES:
        buttons.append([InlineKeyboardButton(label, callback_data=f"pw:freq:{code}")])

    text = f"<b>New Protocol</b>\n{name} — {dose_str}\n\nHow often?"

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=_PARSE)


async def _send_route(query, context):
    """Step 5: Administration route."""
    pw = context.user_data["pw"]
    ref = pw.get("_ref")
    name = html.escape(pw.get("peptide_name", "Peptide"))
    dose_str = f"{pw.get('dose_amount', '?')}{pw.get('dose_unit', 'mcg')}"
    freq_label = dict(_FREQUENCIES).get(pw.get("frequency_code", ""), "")

    # Filter to routes available for this peptide
    available_routes = _ROUTES
    if ref and ref.get("routes"):
        ref_routes = [r.lower() for r in ref["routes"]]
        available_routes = [(c, l) for c, l in _ROUTES if c in ref_routes]
        if not available_routes:
            available_routes = _ROUTES

    buttons = []
    for code, label in available_routes:
        buttons.append([InlineKeyboardButton(label, callback_data=f"pw:route:{code}")])

    text = f"<b>New Protocol</b>\n{name} — {dose_str} — {freq_label}\n\nRoute?"

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=_PARSE)


async def _send_time1(query, context):
    """Step 6a: First (or only) time selection."""
    pw = context.user_data["pw"]
    freq = pw.get("frequency_code", "daily")
    is_2x = freq == "2x_daily"

    prompt = "Morning dose time?" if is_2x else "What time?"
    header = _build_header(pw)

    buttons = []
    row = []
    for h in _MORNING_HOURS:
        label = f"{h}:00 AM" if h < 12 else f"{h - 12}:00 PM"
        if h < 12:
            label = f"{h}:00 AM"
        row.append(InlineKeyboardButton(label, callback_data=f"pw:time1:{h}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    text = f"{header}\n\n{prompt}"
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=_PARSE)


async def _send_time2(query, context):
    """Step 6b: Evening time selection (only for 2x daily)."""
    pw = context.user_data["pw"]
    header = _build_header(pw)

    buttons = []
    row = []
    for h in _EVENING_HOURS:
        label = f"{h - 12}:00 PM"
        row.append(InlineKeyboardButton(label, callback_data=f"pw:time2:{h}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    text = f"{header}\n\nEvening dose time?"
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=_PARSE)


async def _send_cycle(query, context):
    """Step 7: Cycle length."""
    pw = context.user_data["pw"]
    ref = pw.get("_ref")
    header = _build_header(pw)

    options = [4, 6, 8, 12]
    buttons = []
    for w in options:
        label = f"{w} weeks"
        if ref and ref.get("standard_duration") and str(w) in ref["standard_duration"]:
            label += " (suggested)"
        buttons.append([InlineKeyboardButton(label, callback_data=f"pw:cycle:{w}")])
    buttons.append([InlineKeyboardButton("Custom", callback_data="pw:cycle:custom")])

    text = f"{header}\n\nCycle length?"
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=_PARSE)


async def _send_confirmation(query, context):
    """Step 8: Show confirmation card."""
    pw = context.user_data["pw"]
    name = html.escape(pw.get("peptide_name", "Peptide"))
    dose = f"{pw.get('dose_amount', '?')} {pw.get('dose_unit', 'mcg')}"
    route = dict(_ROUTES).get(pw.get("route", ""), pw.get("route", ""))
    freq_label = dict(_FREQUENCIES).get(pw.get("frequency_code", ""), "")
    weeks = pw.get("cycle_weeks", "?")

    # Build time display
    times = []
    t1 = pw.get("time1")
    t2 = pw.get("time2")
    if t1 is not None:
        times.append(_format_hour(t1))
    if t2 is not None:
        times.append(_format_hour(t2))
    time_str = " + ".join(times) if times else "not set"

    today = date.today()
    end_date = today + timedelta(weeks=int(weeks)) if str(weeks).isdigit() else "?"
    end_str = end_date.strftime("%b %d") if isinstance(end_date, date) else "?"

    text = (
        f"<b>New Protocol</b>\n\n"
        f"<b>{name}</b>\n"
        f"{dose} {route.lower()}\n"
        f"{freq_label} ({time_str})\n"
        f"{weeks}-week cycle ({today.strftime('%b %d')} — {end_str})\n\n"
        f"Look good?"
    )

    buttons = [
        [
            InlineKeyboardButton("Start Protocol", callback_data="pw:confirm"),
            InlineKeyboardButton("Cancel", callback_data="pw:cancel"),
        ]
    ]

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=_PARSE)


# --- Wizard callback router ---

async def handle_protocol_wizard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all pw:* callbacks."""
    query = update.callback_query
    await query.answer()

    data = query.data
    parts = data.split(":")
    if len(parts) < 2:
        return

    step = parts[1]
    value = parts[2] if len(parts) > 2 else None
    pw = context.user_data.get("pw")
    if pw is None:
        pw = {}
        context.user_data["pw"] = pw

    # Step 1: Category selected
    if step == "cat":
        if value == "custom":
            pw["awaiting"] = "custom_peptide"
            await query.edit_message_text(
                "<b>New Protocol</b>\n\nType the peptide name:",
                parse_mode=_PARSE,
            )
        else:
            pw["category"] = value
            await _send_peptide_list(query, context, value)

    # Step 2: Peptide selected
    elif step == "pep":
        if value == "custom":
            pw["awaiting"] = "custom_peptide"
            await query.edit_message_text(
                "<b>New Protocol</b>\n\nType the peptide name:",
                parse_mode=_PARSE,
            )
        else:
            ref = _lookup_reference_by_slug(value)
            if ref:
                pw["peptide_slug"] = ref["slug"]
                pw["peptide_name"] = ref["name"]
                pw["_ref"] = ref
            else:
                pw["peptide_name"] = value
            await _send_dose(query, context)

    # Step 3: Dose selected
    elif step == "dose":
        if value == "ref":
            ref = pw.get("_ref", {})
            _parse_standard_dose(pw, ref.get("standard_dose", ""))
            await _send_frequency(query, context)
        elif value == "custom":
            pw["awaiting"] = "custom_dose"
            name = html.escape(pw.get("peptide_name", "Peptide"))
            await query.edit_message_text(
                f"<b>New Protocol</b>\n{name}\n\nType dose (e.g. 250mcg, 5mg, 100IU):",
                parse_mode=_PARSE,
            )

    # Step 4: Frequency selected
    elif step == "freq":
        pw["frequency_code"] = value
        await _send_route(query, context)

    # Step 5: Route selected
    elif step == "route":
        pw["route"] = value
        await _send_time1(query, context)

    # Step 6a: First time selected
    elif step == "time1":
        pw["time1"] = int(value)
        if pw.get("frequency_code") == "2x_daily":
            await _send_time2(query, context)
        else:
            await _send_cycle(query, context)

    # Step 6b: Evening time selected
    elif step == "time2":
        pw["time2"] = int(value)
        await _send_cycle(query, context)

    # Step 7: Cycle length selected
    elif step == "cycle":
        if value == "custom":
            pw["awaiting"] = "custom_cycle"
            header = _build_header(pw)
            await query.edit_message_text(
                f"{header}\n\nType cycle length in weeks (e.g. 10):",
                parse_mode=_PARSE,
            )
        else:
            pw["cycle_weeks"] = int(value)
            await _send_confirmation(query, context)

    # Step 8: Confirm or cancel
    elif step == "confirm":
        await _create_protocol(query, context)

    elif step == "cancel":
        context.user_data.pop("pw", None)
        await query.edit_message_text("Protocol cancelled.", parse_mode=_PARSE)


async def handle_wizard_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Handle text input during wizard (custom peptide name, dose, cycle).
    Returns True if the input was consumed by the wizard."""
    pw = context.user_data.get("pw", {})
    awaiting = pw.get("awaiting")
    if not awaiting:
        return False
    chat = update.effective_chat

    if awaiting == "custom_peptide":
        pw["peptide_name"] = text.strip()
        pw.pop("awaiting", None)
        await _send_dose(chat, context)
        return True

    elif awaiting == "custom_dose":
        parsed = _parse_dose_text(text.strip())
        if parsed:
            pw["dose_amount"] = parsed[0]
            pw["dose_unit"] = parsed[1]
        else:
            pw["dose_amount"] = text.strip()
            pw["dose_unit"] = ""
        pw.pop("awaiting", None)
        # Send frequency as a new message since we can't edit the old one
        buttons = []
        for code, label in _FREQUENCIES:
            buttons.append([InlineKeyboardButton(label, callback_data=f"pw:freq:{code}")])
        name = html.escape(pw.get("peptide_name", "Peptide"))
        dose_str = f"{pw.get('dose_amount', '?')}{pw.get('dose_unit', 'mcg')}"
        await chat.send_message(
            f"<b>New Protocol</b>\n{name} — {dose_str}\n\nHow often?",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=_PARSE,
        )
        return True

    elif awaiting == "custom_cycle":
        try:
            weeks = int(text.strip())
            pw["cycle_weeks"] = weeks
        except ValueError:
            await chat.send_message("Type a number (weeks). E.g. 10")
            return True
        pw.pop("awaiting", None)
        # Build confirmation as a new message
        await _send_confirmation_new_msg(chat, context)
        return True

    return False


async def _create_protocol(query, context):
    """Create the protocol from wizard state."""
    from bot.services import biohacking_service, user_service

    pw = context.user_data.get("pw", {})
    user = context.user_data.get("db_user")
    if not user:
        tg = query.from_user
        user = user_service.get_or_create_user(tg.id, tg.username, tg.first_name)
        context.user_data["db_user"] = user

    # Build schedule times
    schedule_times = []
    t1 = pw.get("time1")
    t2 = pw.get("time2")
    if t1 is not None:
        schedule_times.append(f"{t1:02d}:00")
    if t2 is not None:
        schedule_times.append(f"{t2:02d}:00")
    if not schedule_times:
        schedule_times = ["08:00"]  # Default

    try:
        protocol = biohacking_service.add_protocol_with_schedule(
            user_id=user["id"],
            peptide_name=pw.get("peptide_name", "Unknown"),
            dose_amount=float(pw.get("dose_amount", 0)),
            dose_unit=pw.get("dose_unit", "mcg"),
            frequency_code=pw.get("frequency_code", "daily"),
            route=pw.get("route", "subcutaneous"),
            schedule_times=schedule_times,
            cycle_weeks=int(pw.get("cycle_weeks", 8)),
            notes=None,
        )

        name = html.escape(protocol["peptide_name"])
        cycle_days = int(pw.get("cycle_weeks", 8)) * 7
        end_date = (date.today() + timedelta(days=cycle_days)).strftime("%b %d")

        text = (
            f"Protocol started\n\n"
            f"<b>{name}</b>\n"
            f"{protocol.get('dose_amount', '?')} {protocol.get('dose_unit', 'mcg')} "
            f"{protocol.get('route', 'subq').lower()}\n"
            f"{protocol.get('frequency', 'daily')} until {end_date}\n\n"
            f"I'll remind you at your scheduled times. Say 'took my {pw.get('peptide_name', '')}' anytime to log a dose."
        )

        await query.edit_message_text(text, parse_mode=_PARSE)

    except Exception as e:
        logger.error(f"Protocol creation failed: {e}")
        await query.edit_message_text("Something went wrong creating the protocol. Try again?")

    context.user_data.pop("pw", None)


# ══════════════════════════════════════════════════════════════════
# PROTOCOL DASHBOARD — interactive card for /protocols
# Callback prefix: pd:
# ══════════════════════════════════════════════════════════════════

def render_protocol_dashboard(protocols: list, user_id: int, selected_idx: int = 0) -> tuple:
    """Render the protocol dashboard card. Returns (text, InlineKeyboardMarkup)."""
    from bot.services import biohacking_service

    if not protocols:
        text = "No active protocols.\n\nStart one anytime — just tell me what you want to run."
        buttons = [[InlineKeyboardButton("+ New Protocol", callback_data="pd:new")]]
        return text, InlineKeyboardMarkup(buttons)

    # Clamp selected index
    selected_idx = max(0, min(selected_idx, len(protocols) - 1))
    p = protocols[selected_idx]

    name = html.escape(p.get("peptide_name", "Unknown"))
    dose = f"{p.get('dose_amount', '?')} {p.get('dose_unit', 'mcg')}"
    route_short = (p.get("route", "subq") or "subq")[:4].upper()

    # Cycle progress
    cycle_day = 0
    cycle_total = 1
    if p.get("cycle_start") and p.get("cycle_end"):
        cycle_total = max(1, (p["cycle_end"] - p["cycle_start"]).days)
        cycle_day = max(0, (date.today() - p["cycle_start"]).days)

    progress_bar = _render_progress_bar(cycle_day, cycle_total)
    pct = min(100, round(cycle_day / cycle_total * 100))

    # Today's dose status
    todays = biohacking_service.get_todays_scheduled_doses(user_id)
    protocol_doses = [d for d in todays if d["protocol_id"] == p["id"]]
    today_visual = _render_today_doses(protocol_doses)

    # 7-day adherence
    adherence_7d = biohacking_service.get_protocol_adherence(p["id"], days=7)
    visual_7d = _render_7day_visual(adherence_7d)
    adherence_stats = biohacking_service.get_adherence(p["id"], days=7)
    rate = adherence_stats.get("rate", 0)

    # Build text
    lines = [
        f"<b>{name}</b> — {dose} {route_short}",
        f"Day {cycle_day} of {cycle_total}  {progress_bar} {pct}%",
    ]
    if today_visual:
        lines.append(f"Today: {today_visual}")
    if visual_7d:
        lines.append(f"7d: {visual_7d} {rate}%")

    # Nav indicator if multiple protocols
    if len(protocols) > 1:
        lines.insert(0, f"<i>{selected_idx + 1} of {len(protocols)}</i>")

    text = "\n".join(lines)

    # Buttons
    pid = p["id"]
    row1 = []

    # Quick log for pending doses
    pending = [d for d in protocol_doses if d["status"] == "pending"]
    if pending:
        row1.append(InlineKeyboardButton("Log Dose", callback_data=f"pd:log:{pid}"))

    row1.append(InlineKeyboardButton("+ New", callback_data="pd:new"))

    row2 = []
    if len(protocols) > 1:
        if selected_idx > 0:
            row2.append(InlineKeyboardButton("< Prev", callback_data=f"pd:nav:{selected_idx - 1}"))
        if selected_idx < len(protocols) - 1:
            row2.append(InlineKeyboardButton("Next >", callback_data=f"pd:nav:{selected_idx + 1}"))

    status = p.get("status", "active")
    if status == "active":
        row2.append(InlineKeyboardButton("Pause", callback_data=f"pd:pause:{pid}"))
    elif status == "paused":
        row2.append(InlineKeyboardButton("Resume", callback_data=f"pd:resume:{pid}"))

    buttons = [row1]
    if row2:
        buttons.append(row2)

    return text, InlineKeyboardMarkup(buttons)


async def send_protocol_dashboard(chat, context, user_id: int, selected_idx: int = 0):
    """Send the protocol dashboard card."""
    from bot.services import biohacking_service

    # Ensure today's doses are generated
    biohacking_service.generate_daily_doses(user_id)
    biohacking_service.mark_overdue_doses_missed(user_id)

    protocols = biohacking_service.get_active_protocols(user_id)
    text, markup = render_protocol_dashboard(protocols, user_id, selected_idx)
    await chat.send_message(text, reply_markup=markup, parse_mode=_PARSE)


async def handle_protocol_dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pd:* callbacks."""
    query = update.callback_query
    await query.answer()

    from bot.services import biohacking_service, user_service

    user = context.user_data.get("db_user")
    if not user:
        tg = query.from_user
        user = user_service.get_or_create_user(tg.id, tg.username, tg.first_name)
        context.user_data["db_user"] = user

    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    value = parts[2] if len(parts) > 2 else ""

    if action == "new":
        context.user_data["pw"] = {}
        await _send_categories_edit(query, context)

    elif action == "nav":
        idx = int(value)
        protocols = biohacking_service.get_active_protocols(user["id"])
        text, markup = render_protocol_dashboard(protocols, user["id"], idx)
        await query.edit_message_text(text, reply_markup=markup, parse_mode=_PARSE)

    elif action == "log":
        protocol_id = int(value)
        # Find first pending dose for this protocol today
        pending = biohacking_service.get_pending_doses(user["id"])
        dose = next((d for d in pending if d["protocol_id"] == protocol_id), None)
        if dose:
            result = biohacking_service.mark_dose_taken(dose["id"], user["id"])
            if result:
                await query.answer(f"Logged {result.get('peptide_name', 'dose')}")
        else:
            await query.answer("No pending doses for this protocol")

        # Refresh dashboard
        protocols = biohacking_service.get_active_protocols(user["id"])
        # Find index of this protocol
        idx = next((i for i, p in enumerate(protocols) if p["id"] == protocol_id), 0)
        text, markup = render_protocol_dashboard(protocols, user["id"], idx)
        try:
            await query.edit_message_text(text, reply_markup=markup, parse_mode=_PARSE)
        except Exception:
            pass

    elif action == "pause":
        protocol_id = int(value)
        biohacking_service.update_protocol_status(protocol_id, "paused")
        await query.answer("Protocol paused")
        protocols = biohacking_service.get_active_protocols(user["id"])
        text, markup = render_protocol_dashboard(protocols, user["id"])
        try:
            await query.edit_message_text(text, reply_markup=markup, parse_mode=_PARSE)
        except Exception:
            pass

    elif action == "resume":
        protocol_id = int(value)
        biohacking_service.update_protocol_status(protocol_id, "active")
        await query.answer("Protocol resumed")
        protocols = biohacking_service.get_active_protocols(user["id"])
        text, markup = render_protocol_dashboard(protocols, user["id"])
        try:
            await query.edit_message_text(text, reply_markup=markup, parse_mode=_PARSE)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════
# DOSE REMINDER CARDS — interactive reminders
# Callback prefix: dr:
# ══════════════════════════════════════════════════════════════════

def render_dose_reminder_card(pending_doses: list, user_name: str, hour: int) -> tuple:
    """Render an interactive dose reminder. Returns (text, InlineKeyboardMarkup)."""
    if hour < 12:
        greeting = f"Morning doses, {html.escape(user_name)}"
    elif hour < 18:
        greeting = f"Afternoon dose, {html.escape(user_name)}"
    else:
        greeting = f"Evening doses, {html.escape(user_name)}"

    lines = [f"<b>{greeting}</b>\n"]
    for d in pending_doses:
        name = html.escape(d.get("peptide_name", "?"))
        dose = f"{d.get('dose_amount', '?')} {d.get('dose_unit', 'mcg')}"
        route = (d.get("route", "") or "")[:4].lower()
        status_icon = "✅" if d.get("status") == "taken" else "⬜"
        lines.append(f"{status_icon} {name} — {dose} {route}")

    text = "\n".join(lines)

    # Buttons
    buttons = []

    # Individual dose buttons (only for pending)
    still_pending = [d for d in pending_doses if d.get("status") == "pending"]
    if len(still_pending) > 1:
        buttons.append([InlineKeyboardButton(
            "All taken", callback_data=f"dr:all:{still_pending[0].get('user_id', 0)}"
        )])

    for d in still_pending:
        name = d.get("peptide_name", "?")
        if len(name) > 15:
            name = name[:14] + "."
        buttons.append([InlineKeyboardButton(
            f"Took {name}", callback_data=f"dr:taken:{d['id']}"
        )])

    if still_pending:
        buttons.append([InlineKeyboardButton(
            "Snooze 30min", callback_data=f"dr:snooze:{still_pending[0].get('user_id', 0)}"
        )])

    if not still_pending:
        text += "\n\nAll done"
        buttons = []

    return text, InlineKeyboardMarkup(buttons) if buttons else None


async def handle_dose_reminder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle dr:* callbacks."""
    query = update.callback_query

    from bot.services import biohacking_service, user_service

    user = context.user_data.get("db_user")
    if not user:
        tg = query.from_user
        user = user_service.get_or_create_user(tg.id, tg.username, tg.first_name)
        context.user_data["db_user"] = user

    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    value = parts[2] if len(parts) > 2 else ""

    if action == "taken":
        dose_id = int(value)
        result = biohacking_service.mark_dose_taken(dose_id, user["id"])
        if result:
            await query.answer(f"Logged {result.get('peptide_name', 'dose')}")
        else:
            await query.answer("Already logged")

        # Refresh the reminder card in place
        await _refresh_reminder_card(query, user)

    elif action == "all":
        pending = biohacking_service.get_pending_doses(user["id"])
        for d in pending:
            biohacking_service.mark_dose_taken(d["id"], user["id"])
        await query.answer("All doses logged")
        await _refresh_reminder_card(query, user)

    elif action == "snooze":
        await query.answer("Reminder in 30 min")
        # Schedule a re-send in 30 minutes
        chat_id = query.message.chat_id
        context.job_queue.run_once(
            _snooze_callback,
            when=1800,
            data={"user_id": user["id"], "chat_id": chat_id},
            name=f"snooze_{user['id']}",
        )
        # Remove buttons from current card
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

    elif action == "skip":
        dose_id = int(value)
        biohacking_service.mark_dose_skipped(dose_id, user["id"])
        await query.answer("Dose skipped")
        await _refresh_reminder_card(query, user)


async def _refresh_reminder_card(query, user):
    """Refresh a dose reminder card in place after a dose action."""
    from bot.services import biohacking_service

    todays = biohacking_service.get_todays_scheduled_doses(user["id"])
    # Filter to doses that were part of this reminder window
    name = user.get("first_name", "friend")
    now_hour = datetime.now().hour

    text, markup = render_dose_reminder_card(todays, name, now_hour)
    try:
        await query.edit_message_text(text, reply_markup=markup, parse_mode=_PARSE)
    except Exception:
        pass


async def _snooze_callback(context: ContextTypes.DEFAULT_TYPE):
    """Fired 30 min after snooze — re-send reminder if doses still pending."""
    data = context.job.data
    user_id = data["user_id"]
    chat_id = data["chat_id"]

    from bot.services import biohacking_service, user_service

    pending = biohacking_service.get_pending_doses(user_id)
    if not pending:
        return

    user = user_service.get_user_by_id(user_id)
    name = user.get("first_name", "friend") if user else "friend"

    text, markup = render_dose_reminder_card(pending, name, datetime.now().hour)
    if markup:
        msg = await context.bot.send_message(
            chat_id=chat_id, text=text, reply_markup=markup, parse_mode=_PARSE)
        for d in pending:
            biohacking_service.set_dose_reminder_message_id(d["id"], msg.message_id)


# ══════════════════════════════════════════════════════════════════
# QUICK DOSE — one-tap logging
# Callback prefix: qd:
# ══════════════════════════════════════════════════════════════════

async def send_quick_dose_buttons(chat, context, user_id: int) -> bool:
    """Send one-tap dose buttons for all active protocols. Returns True if buttons were sent."""
    from bot.services import biohacking_service

    protocols = biohacking_service.get_active_protocols(user_id)
    if not protocols:
        return False

    buttons = []
    for p in protocols:
        name = p.get("peptide_name", "?")
        dose = f"{p.get('dose_amount', '?')}{p.get('dose_unit', 'mcg')}"
        label = f"{name} ({dose})"
        if len(label) > 30:
            label = f"{name[:20]} ({dose})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"qd:log:{p['id']}")])

    await chat.send_message(
        "Which one?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return True


async def handle_quick_dose_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle qd:* callbacks — one-tap dose logging."""
    query = update.callback_query

    from bot.services import biohacking_service, user_service

    user = context.user_data.get("db_user")
    if not user:
        tg = query.from_user
        user = user_service.get_or_create_user(tg.id, tg.username, tg.first_name)
        context.user_data["db_user"] = user

    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    protocol_id = int(parts[2]) if len(parts) > 2 else 0

    if action == "log":
        # Try to mark a scheduled dose first
        pending = biohacking_service.get_pending_doses(user["id"])
        scheduled = next((d for d in pending if d["protocol_id"] == protocol_id), None)

        if scheduled:
            result = biohacking_service.mark_dose_taken(scheduled["id"], user["id"])
            pname = result.get("peptide_name", "dose") if result else "dose"
        else:
            # No scheduled dose — log directly (backwards compat)
            protocol = biohacking_service.get_protocol_by_id(protocol_id)
            if protocol:
                biohacking_service.log_dose(
                    user["id"], protocol_id,
                    dose_amount=protocol.get("dose_amount"),
                )
                pname = protocol.get("peptide_name", "dose")
            else:
                await query.answer("Protocol not found")
                return

        await query.answer(f"Logged {pname}")

        # Update the message to show confirmation
        try:
            await query.edit_message_text(f"Logged {pname}.")
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════

def _build_header(pw: dict) -> str:
    """Build the card header from accumulated wizard state."""
    name = html.escape(pw.get("peptide_name", "Peptide"))
    parts = [f"<b>New Protocol</b>\n{name}"]

    dose = pw.get("dose_amount")
    unit = pw.get("dose_unit", "mcg")
    if dose:
        parts[0] += f" — {dose}{unit}"

    freq = pw.get("frequency_code")
    if freq:
        label = dict(_FREQUENCIES).get(freq, freq)
        parts[0] += f" — {label}"

    route = pw.get("route")
    if route:
        parts[0] += f" — {dict(_ROUTES).get(route, route)}"

    t1 = pw.get("time1")
    t2 = pw.get("time2")
    times = []
    if t1 is not None:
        times.append(_format_hour(t1))
    if t2 is not None:
        times.append(_format_hour(t2))
    if times:
        parts[0] += f" — {' + '.join(times)}"

    return parts[0]


def _format_hour(h: int) -> str:
    """Format 24h hour as 12h string."""
    if h == 0:
        return "12 AM"
    elif h < 12:
        return f"{h} AM"
    elif h == 12:
        return "12 PM"
    else:
        return f"{h - 12} PM"


def _render_progress_bar(current: int, total: int, width: int = 12) -> str:
    """Render a text progress bar."""
    if total <= 0:
        return ""
    filled = min(width, round(current / total * width))
    return "\u2588" * filled + "\u2591" * (width - filled)


def _render_today_doses(doses: list) -> str:
    """Render today's dose status line."""
    if not doses:
        return ""
    parts = []
    for d in doses:
        t = d.get("scheduled_time")
        if t:
            if isinstance(t, dt_time):
                h = t.hour
            else:
                h = int(str(t).split(":")[0])
            time_label = _format_hour(h)
        else:
            time_label = "?"
        icon = "\u2705" if d.get("status") == "taken" else "\u2B1C"
        if d.get("status") == "skipped":
            icon = "\u23ED"
        parts.append(f"{icon} {time_label}")
    return "  ".join(parts)


def _render_7day_visual(daily_adherence: list) -> str:
    """Render 7-day adherence as emoji blocks."""
    if not daily_adherence:
        return ""
    icons = []
    for day in daily_adherence:
        if day["status"] == "full":
            icons.append("\u2705")
        elif day["status"] == "partial":
            icons.append("\U0001F7E8")  # yellow square
        else:
            icons.append("\u2B1C")
    return "".join(icons)


def _lookup_reference(name_hint: str) -> dict | None:
    """Look up a peptide in the reference DB by name (fuzzy)."""
    from bot.db.database import get_cursor
    try:
        with get_cursor() as cur:
            # Exact match first
            cur.execute(
                "SELECT * FROM peptide_reference WHERE LOWER(name) = LOWER(%s) LIMIT 1",
                (name_hint,)
            )
            row = cur.fetchone()
            if row:
                return dict(row)

            # Contains match
            cur.execute(
                "SELECT * FROM peptide_reference WHERE LOWER(name) LIKE LOWER(%s) LIMIT 1",
                (f"%{name_hint}%",)
            )
            row = cur.fetchone()
            if row:
                return dict(row)

            # Slug match
            slug = name_hint.lower().replace(" ", "-")
            cur.execute(
                "SELECT * FROM peptide_reference WHERE slug = %s LIMIT 1",
                (slug,)
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def _lookup_reference_by_slug(slug: str) -> dict | None:
    """Look up a peptide by slug."""
    from bot.db.database import get_cursor
    try:
        with get_cursor() as cur:
            cur.execute("SELECT * FROM peptide_reference WHERE slug = %s LIMIT 1", (slug,))
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def _parse_standard_dose(pw: dict, dose_str: str):
    """Parse a reference DB standard_dose string like '250-500mcg' into amount + unit."""
    if not dose_str:
        pw["dose_amount"] = 250
        pw["dose_unit"] = "mcg"
        return

    import re
    # Try to extract first number + unit
    match = re.search(r"([\d.]+)\s*([a-zA-Z]+)", dose_str)
    if match:
        pw["dose_amount"] = float(match.group(1))
        pw["dose_unit"] = match.group(2).lower()
    else:
        pw["dose_amount"] = dose_str
        pw["dose_unit"] = ""


def _parse_dose_text(text: str) -> tuple | None:
    """Parse user-typed dose like '250mcg' or '5 mg' into (amount, unit)."""
    import re
    match = re.match(r"([\d.]+)\s*([a-zA-Z]*)", text)
    if match and match.group(1):
        amount = float(match.group(1))
        unit = match.group(2).lower() or "mcg"
        return (amount, unit)
    return None


async def _send_categories_edit(query, context):
    """Send categories by editing the existing message (from dashboard)."""
    buttons = []
    for code, label in _CATEGORIES:
        buttons.append([InlineKeyboardButton(label, callback_data=f"pw:cat:{code}")])
    buttons.append([InlineKeyboardButton("Custom peptide", callback_data="pw:cat:custom")])

    await query.edit_message_text(
        "<b>New Protocol</b>\n\nWhat type of peptide?",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=_PARSE,
    )


async def _send_confirmation_new_msg(chat, context):
    """Send confirmation as a new message (after text input)."""
    pw = context.user_data.get("pw", {})
    name = html.escape(pw.get("peptide_name", "Peptide"))
    dose = f"{pw.get('dose_amount', '?')} {pw.get('dose_unit', 'mcg')}"
    route = dict(_ROUTES).get(pw.get("route", ""), pw.get("route", ""))
    freq_label = dict(_FREQUENCIES).get(pw.get("frequency_code", ""), "")
    weeks = pw.get("cycle_weeks", "?")

    times = []
    t1 = pw.get("time1")
    t2 = pw.get("time2")
    if t1 is not None:
        times.append(_format_hour(t1))
    if t2 is not None:
        times.append(_format_hour(t2))
    time_str = " + ".join(times) if times else "not set"

    today = date.today()
    end_date = today + timedelta(weeks=int(weeks)) if str(weeks).isdigit() else "?"
    end_str = end_date.strftime("%b %d") if isinstance(end_date, date) else "?"

    text = (
        f"<b>New Protocol</b>\n\n"
        f"<b>{name}</b>\n"
        f"{dose} {route.lower()}\n"
        f"{freq_label} ({time_str})\n"
        f"{weeks}-week cycle ({today.strftime('%b %d')} — {end_str})\n\n"
        f"Look good?"
    )

    buttons = [
        [
            InlineKeyboardButton("Start Protocol", callback_data="pw:confirm"),
            InlineKeyboardButton("Cancel", callback_data="pw:cancel"),
        ]
    ]

    await chat.send_message(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=_PARSE)
