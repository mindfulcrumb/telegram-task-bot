"""Photo/document handler — smart image classification + bloodwork, food, supplement, and general vision."""
import asyncio
import base64
import json
import logging
import os
import re
import tempfile

from telegram import Update
from telegram.ext import ContextTypes

from bot.handlers.message_utils import clean_response as _clean_response, send_chunked

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vision prompts
# ---------------------------------------------------------------------------

IMAGE_CLASSIFICATION_PROMPT = """Look at this image and classify it. Return ONLY a JSON object:
{
  "type": "bloodwork",
  "description": "Brief 1-2 sentence description of what you see"
}

type must be one of:
- "bloodwork": Lab results, blood test reports, medical test documents with biomarker values
- "food": Food items, meals, recipes, ingredients, nutrition labels, restaurant dishes, grocery items, cooking photos, inside of a fridge or pantry
- "supplement": Supplement bottles, pill containers, supplement labels, vitamin packaging, supplement facts panels, herbal extract bottles, protein powder containers, any health/wellness product packaging
- "workout": Workout logs, training summaries, exercise records, gym session reports, fitness app exports (e.g. Strava, TrainingPeaks, Garmin), running data, any document listing exercises, sets, reps, weights, or training metrics
- "other": Everything else (screenshots, memes, selfies, documents, etc.)

For the description, describe what you ACTUALLY see. Do not infer or assume items that are not clearly visible.

Return ONLY the JSON object, nothing else."""


WORKOUT_PDF_EXTRACTION_PROMPT = """Analyze this workout/training document and extract all exercise data.

Return a JSON object with this EXACT structure:
{
  "is_workout": true,
  "title": "Workout title or type (e.g. 'Upper Body Push', 'Leg Day', 'Morning Run')",
  "date": "YYYY-MM-DD if visible, otherwise null",
  "duration_minutes": 60,
  "rpe": null,
  "notes": "Any performance notes, how it felt, energy level — null if none visible",
  "exercises": [
    {
      "exercise_name": "Bench Press",
      "movement_pattern": "horizontal_push",
      "sets": 3,
      "reps": "8",
      "weight": 80,
      "weight_unit": "kg",
      "rpe": null
    }
  ]
}

Rules:
1. If this is NOT a workout or training document, return {"is_workout": false}
2. Extract EVERY exercise or movement listed — don't skip any
3. For movement_pattern use one of: squat, hinge, horizontal_push, horizontal_pull, vertical_push, vertical_pull, carry_rotation
4. Infer movement_pattern from the exercise name (e.g. Bench Press → horizontal_push, Deadlift → hinge)
5. If reps vary by set, format as "12,10,8" (comma-separated)
6. If duration is in seconds, convert to minutes (round to nearest integer)
7. For cardio/running: omit exercises array, set title to activity type, include duration_minutes and any pace/distance in notes
8. Weight should be a number — if unit is lbs keep as-is and set weight_unit to "lbs"
9. Return ONLY the JSON object, nothing else"""


FOOD_EXTRACTION_PROMPT = """Look at this image carefully. Identify ONLY the food items you can clearly see.

Return a JSON object:
{
  "scene": "fridge" | "plate" | "ingredients" | "label" | "cooking",
  "items": [
    {"name": "grilled chicken breast", "usda_query": "chicken breast meat only cooked grilled", "grams": 150},
    {"name": "white rice", "usda_query": "rice white medium-grain cooked", "grams": 200},
    {"name": "steamed broccoli", "usda_query": "broccoli cooked boiled", "grams": 100}
  ],
  "meal_description": "Grilled chicken breast with white rice and steamed broccoli"
}

STRICT RULES:
1. ONLY list items you can CLEARLY and UNAMBIGUOUSLY see. Do NOT guess, infer, or add items that might logically be present but are not visible.
   - If you see a container but can't identify the contents, SKIP it entirely.
   - If something MIGHT be eggs, rice, or any other item but you're not sure, DO NOT include it.
   - NEVER add items to "round out" a meal. If you see chicken and vegetables but no rice, don't add rice.
   - Common hallucinations to AVOID: eggs (often confused with other round objects), rice/grains (often confused with crumbs or textures), sauces (don't guess sauces), herbs (only if clearly visible).
2. For each item provide THREE fields:
   - "name": what a person would call it (e.g., "grilled chicken breast")
   - "usda_query": a USDA FoodData Central searchable name — plain English, include cooking method, no brand names (e.g., "chicken breast meat only cooked grilled")
   - "grams": estimated portion weight in grams using visual cues:
     * standard dinner plate = ~26cm diameter
     * fist-sized portion of rice/pasta = ~150g cooked
     * palm-sized meat = ~100-120g
     * cup of vegetables = ~90-100g
     * a whole chicken breast = ~170g
3. For packaged items, read the label if visible. Use the product name for "name" and generic food for "usda_query".
4. For containers where contents are unclear, skip them entirely.
5. Scene types: "fridge" = inside fridge/pantry, "plate" = prepared meal, "ingredients" = raw items, "label" = nutrition label, "cooking" = food being cooked.
6. For FRIDGE/INGREDIENTS scenes: still include items with usda_query but set grams to null (portions unknown).
7. Return ONLY the JSON object, nothing else."""


BLOODWORK_EXTRACTION_PROMPT = """Analyze this blood test / lab results image and extract ALL biomarkers you can find.

Return a JSON object with this EXACT structure:
{
  "is_blood_test": true,
  "lab_name": "Name of the lab if visible",
  "test_date": "YYYY-MM-DD if visible, otherwise null",
  "markers": [
    {
      "marker_name": "Total Testosterone",
      "value": 650,
      "unit": "ng/dL",
      "reference_low": 300,
      "reference_high": 1000
    }
  ]
}

Rules:
1. If this is NOT a blood test or lab result, return {"is_blood_test": false}
2. Extract EVERY biomarker visible — don't skip any
3. Use standard marker names (e.g., "Total Testosterone" not "TEST")
4. Include reference ranges if shown on the report
5. Values must be numbers (convert "6.5" to 6.5, not "6.5")
6. Include the unit exactly as shown (ng/dL, mg/L, mIU/L, etc.)
7. Return ONLY the JSON object, nothing else"""


SUPPLEMENT_EXTRACTION_PROMPT = """Analyze this supplement product image carefully. Read ALL visible text on the label, bottle, or packaging.

Return a JSON object with this EXACT structure:
{
  "is_supplement": true,
  "products": [
    {
      "product_name": "Full product name as shown on label",
      "brand": "Brand name if visible",
      "supplement_type": "vitamin|mineral|amino_acid|herbal|peptide|protein|probiotic|omega|nootropic|other",
      "serving_size": "e.g. 2 capsules, 1 scoop (30g)",
      "servings_per_container": 60,
      "key_ingredients": [
        {"name": "Magnesium (as Magnesium Glycinate)", "amount": "400", "unit": "mg"},
        {"name": "Vitamin D3", "amount": "5000", "unit": "IU"}
      ],
      "other_ingredients": "Rice flour, gelatin capsule, magnesium stearate",
      "suggested_use": "Take 2 capsules daily with food (if visible on label)",
      "warnings": "Any warnings visible on label"
    }
  ],
  "photo_type": "single_bottle|multiple_bottles|supplement_facts_panel|shelf|haul"
}

Rules:
1. If this is NOT a supplement product, return {"is_supplement": false}
2. Read the ACTUAL label text — do NOT guess or infer ingredients not visible
3. For each ingredient, include the exact form shown (e.g., "Magnesium as Magnesium Glycinate", not just "Magnesium")
4. If amounts are not visible, set amount to null
5. Include ALL products visible — if multiple bottles, list each
6. For Supplement Facts panels, extract EVERY line item
7. If text is partially obscured, extract what you CAN read and skip what you can't
8. Return ONLY the JSON object, nothing else"""


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo uploads — classify, then route to bloodwork/food/supplement/general handler."""
    from bot.services import user_service

    tg = update.effective_user
    user = context.user_data.get("db_user")
    if not user:
        user = user_service.get_or_create_user(tg.id, tg.username, tg.first_name)
        context.user_data["db_user"] = user

    if not user.get("onboarding_completed"):
        await update.message.reply_text(
            "Finish setup first — type /start to pick up where you left off."
        )
        return

    # Check AI message limit
    from bot.services import tier_service
    tier = user.get("tier", "free")
    if tier != "pro" and not user.get("is_admin"):
        allowed, msg = tier_service.check_limit(
            user["id"], "ai_message", tier,
            is_admin=user.get("is_admin", False),
            telegram_user_id=user.get("telegram_user_id"),
        )
        if not allowed:
            from bot.handlers.payments import get_subscribe_keyboard
            keyboard = get_subscribe_keyboard(update.effective_user.id)
            await update.message.reply_text(msg, reply_markup=keyboard)
            return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        await update.message.reply_text("Can't process images right now — try again later.")
        return

    chat_id = update.effective_chat.id

    # Get the photo (highest resolution) or document
    photo_file = None
    file_ext = ".jpg"
    is_pdf = False

    if update.message.photo:
        photo_file = update.message.photo[-1]
        file_ext = ".jpg"
    elif update.message.document:
        doc = update.message.document
        mime = doc.mime_type or ""
        fname = (doc.file_name or "").lower()
        if mime == "application/pdf" or fname.endswith(".pdf"):
            photo_file = doc
            file_ext = ".pdf"
            is_pdf = True
        elif mime.startswith("image/") or fname.endswith((".jpg", ".jpeg", ".png", ".webp")):
            photo_file = doc
            if doc.file_name:
                file_ext = os.path.splitext(doc.file_name)[1] or ".jpg"
        else:
            return

    if not photo_file:
        return

    # Typing indicator
    typing_active = True

    async def _typing_loop():
        while typing_active:
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            except Exception:
                pass
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(_typing_loop())

    tmp_path = None
    try:
        # Download the image
        tg_file = await context.bot.get_file(photo_file.file_id)
        tmp_path = tempfile.mktemp(suffix=file_ext)
        await tg_file.download_to_drive(tmp_path)

        # Convert to base64
        with open(tmp_path, "rb") as f:
            b64_data = base64.standard_b64encode(f.read()).decode("utf-8")

        if is_pdf:
            media_type = "application/pdf"
        else:
            media_types = {
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp",
            }
            media_type = media_types.get(file_ext.lower(), "image/jpeg")

        # Step 0: Try barcode detection first (fast, offline, no API call)
        # Only for images, not PDFs (barcodes don't come in PDFs typically)
        if not is_pdf:
            barcode = await asyncio.to_thread(_try_detect_barcode, tmp_path)
            if barcode:
                caption = update.message.caption or ""
                await _handle_barcode(update, context, user, barcode, caption, chat_id)
                return

        # Step 1: Classify the image
        logger.info(f"{'PDF' if is_pdf else 'Photo'} from user {user['id']} — classifying")
        classification = await asyncio.to_thread(
            _classify_image, b64_data, media_type, api_key, is_pdf
        )

        image_type = classification.get("type", "other")
        description = classification.get("description", "an image")
        caption = update.message.caption or ""
        logger.info(f"Image classified as '{image_type}' for user {user['id']}: {description[:80]}")

        # Step 2: Route based on classification
        if image_type == "bloodwork":
            await _handle_bloodwork(update, context, user, b64_data, media_type, api_key, is_pdf)

        elif image_type == "food":
            # If there's a pending barcode from a previous scan, this might be the nutrition label
            pending_barcode = context.user_data.get("pending_barcode")
            if pending_barcode and description and "label" in description.lower():
                # Pass pending barcode to food handler so it can save the product
                caption = f"[PENDING_BARCODE:{pending_barcode}] {caption}"
                context.user_data.pop("pending_barcode", None)
            await _handle_food(update, context, user, b64_data, media_type, api_key, caption, description, chat_id)

        elif image_type == "supplement":
            await _handle_supplement(update, context, user, b64_data, media_type, api_key, caption, description, chat_id)

        elif image_type == "workout":
            await _handle_workout_pdf(update, context, user, b64_data, media_type, api_key, is_pdf, caption, description, chat_id)

        else:
            await _handle_general(update, context, user, caption, description, chat_id)

    except Exception as e:
        logger.error(f"Photo handling failed: {type(e).__name__}: {e}")
        await update.message.reply_text("Had trouble reading that. Try a clearer photo, or describe what you need.")
    finally:
        typing_active = False
        typing_task.cancel()
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def _handle_workout_pdf(update, context, user, b64_data, media_type, api_key, is_pdf, caption, description, chat_id):
    """Extract workout data from a PDF or photo, pass to brain to call log_workout."""
    extraction = await asyncio.to_thread(
        _extract_workout_pdf_vision, b64_data, media_type, api_key, is_pdf
    )

    if not extraction or not extraction.get("is_workout"):
        # Classifier said workout but extraction disagrees — treat as general
        await _handle_general(update, context, user, caption, description, chat_id)
        return

    exercises = extraction.get("exercises", [])
    title = extraction.get("title", "Workout")
    duration = extraction.get("duration_minutes")
    notes = extraction.get("notes")
    rpe = extraction.get("rpe")
    date = extraction.get("date")

    # Build structured context for brain
    image_context = "[WORKOUT DOCUMENT — EXTRACTED DATA]\n"
    image_context += f"Title: {title}\n"
    if date:
        image_context += f"Date: {date}\n"
    if duration:
        image_context += f"Duration: {duration} minutes\n"
    if rpe:
        image_context += f"RPE: {rpe}/10\n"
    if notes:
        image_context += f"Notes: {notes}\n"

    if exercises:
        image_context += f"\nExercises ({len(exercises)}):\n"
        for ex in exercises:
            parts = [ex.get("exercise_name", "?")]
            if ex.get("sets") and ex.get("reps"):
                parts.append(f"{ex['sets']}x{ex['reps']}")
            if ex.get("weight"):
                unit = ex.get("weight_unit", "kg")
                parts.append(f"@ {ex['weight']}{unit}")
            if ex.get("rpe"):
                parts.append(f"RPE {ex['rpe']}")
            image_context += f"  - {' '.join(parts)}\n"
    else:
        image_context += "(Cardio/no individual exercises extracted)\n"

    image_context += (
        "\n--- INSTRUCTIONS ---\n"
        "The user sent a workout document. "
        "Call log_workout with the extracted data above. "
        f"Use title='{title}'"
        + (f", duration_minutes={duration}" if duration else "")
        + (f", rpe={rpe}" if rpe else "")
        + (f", notes='{notes}'" if notes else "")
        + (", exercises=[<list above>]" if exercises else "")
        + ".\n"
        "After logging, give a brief coaching comment on the session (1-2 lines). "
        "If the date is in the past, acknowledge it was a past session.\n"
    )

    text_for_brain = f"{image_context}\n{caption}" if caption else image_context

    from bot.ai.brain_v2 import ai_brain
    from bot.services import task_service
    tasks = task_service.get_tasks(user["id"])

    async def _keep_typing():
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        response = await asyncio.wait_for(
            ai_brain.process(text_for_brain, user, tasks, typing_callback=_keep_typing),
            timeout=120.0,
        )
    except asyncio.TimeoutError:
        response = "That took too long — try again."

    if response:
        reply_markup = None
        if ai_brain._paywall_hit.get(user["id"], False):
            from bot.handlers.payments import get_subscribe_keyboard
            reply_markup = get_subscribe_keyboard(update.effective_user.id)
        await send_chunked(bot=context.bot, chat_id=chat_id, text=response, reply_markup=reply_markup)

    logger.info(f"Workout doc processed for user {user['id']}: '{title}', {len(exercises)} exercises")


async def _handle_bloodwork(update, context, user, b64_data, media_type, api_key, is_pdf):
    """Extract bloodwork markers and log to database."""
    extraction = await asyncio.to_thread(
        _extract_bloodwork_vision, b64_data, media_type, api_key, is_pdf
    )

    if not extraction or not extraction.get("is_blood_test"):
        # Classifier said bloodwork but extraction disagrees — treat as general
        caption = update.message.caption or ""
        await _handle_general(update, context, user, caption, "a document or image", update.effective_chat.id)
        return

    markers = extraction.get("markers", [])
    if not markers:
        await update.message.reply_text(
            "I can see this is a blood test but couldn't read the values clearly. "
            "Try a clearer photo, or type the numbers and I'll log them."
        )
        return

    # Log to database
    from bot.services import biohacking_service
    from datetime import date as dt_date

    test_date_str = extraction.get("test_date")
    if test_date_str:
        try:
            test_date = dt_date.fromisoformat(test_date_str)
        except (ValueError, TypeError):
            test_date = dt_date.today()
    else:
        test_date = dt_date.today()

    lab_name = extraction.get("lab_name")

    # Clean markers — ensure values are numeric
    clean_markers = []
    for m in markers:
        try:
            val = float(m["value"])
            marker = {
                "marker_name": m["marker_name"],
                "value": val,
                "unit": m.get("unit"),
            }
            if m.get("reference_low") is not None:
                marker["reference_low"] = float(m["reference_low"])
            if m.get("reference_high") is not None:
                marker["reference_high"] = float(m["reference_high"])
            clean_markers.append(marker)
        except (ValueError, TypeError, KeyError):
            continue

    if not clean_markers:
        await update.message.reply_text(
            "Couldn't parse the marker values from this image. "
            "Try a clearer photo, or just type them out."
        )
        return

    panel = biohacking_service.log_bloodwork(
        user_id=user["id"],
        test_date=test_date,
        lab_name=lab_name,
        notes="Extracted from uploaded photo",
        markers=clean_markers,
    )

    # Build response
    out_of_range = []
    for m in clean_markers:
        low = m.get("reference_low")
        high = m.get("reference_high")
        if low is not None and m["value"] < low:
            out_of_range.append(f'{m["marker_name"]}: {m["value"]} {m.get("unit", "")} (below {low})')
        elif high is not None and m["value"] > high:
            out_of_range.append(f'{m["marker_name"]}: {m["value"]} {m.get("unit", "")} (above {high})')

    lines = [f"Got it. {len(clean_markers)} markers logged from your blood test."]
    if lab_name:
        lines[0] = f"Got it. {len(clean_markers)} markers from {lab_name} logged."

    if out_of_range:
        lines.append(f"\n{len(out_of_range)} flagged:")
        for flag in out_of_range[:8]:
            lines.append(f"  {flag}")

    lines.append("\nSay 'analyze my bloodwork' and I'll give you the full breakdown with optimal ranges.")
    await update.message.reply_text("\n".join(lines))
    logger.info(f"Blood test logged for user {user['id']}: {len(clean_markers)} markers, {len(out_of_range)} flagged")


async def _handle_food(update, context, user, b64_data, media_type, api_key, caption, description, chat_id):
    """Extract food items, look up USDA nutrition, pass to brain with verified data."""
    food_data = await asyncio.to_thread(
        _extract_food_vision, b64_data, media_type, api_key
    )

    raw_items = food_data.get("items", [])
    scene = food_data.get("scene", "plate")
    meal_desc = food_data.get("meal_description", description)

    # Items are now dicts: {name, usda_query, grams}
    # Filter out items with unclear names
    items = []
    for item in raw_items:
        if isinstance(item, str):
            # Fallback if model returned old format
            items.append({"name": item, "usda_query": item, "grams": None})
        elif isinstance(item, dict) and item.get("name"):
            name = item["name"]
            if "unclear" not in name.lower() and "unidentified" not in name.lower():
                items.append(item)

    # --- USDA lookup for plated meals / cooking / labels ---
    usda_available = scene in ("plate", "cooking", "label") and items
    enriched_items = []
    usda_failures = []

    if usda_available:
        from bot.services import usda_service
        for item in items:
            query = item.get("usda_query") or item.get("name", "")
            grams = item.get("grams")
            if not query or not grams:
                usda_failures.append(item.get("name", "unknown"))
                continue
            try:
                nutrients = await usda_service.search_and_get_nutrients(query)
                if nutrients:
                    scaled = usda_service.scale_nutrients(nutrients, grams)
                    scaled["name"] = item["name"]
                    scaled["grams"] = grams
                    scaled["usda_description"] = nutrients.get("usda_description", "")
                    enriched_items.append(scaled)
                else:
                    usda_failures.append(item["name"])
            except Exception as e:
                logger.warning(f"USDA lookup failed for '{query}': {type(e).__name__}: {e}")
                usda_failures.append(item.get("name", "unknown"))

    # --- Build context for brain ---
    if enriched_items:
        # USDA-verified nutrition context
        from bot.services import usda_service as _usda
        meal_total = _usda.sum_nutrients(enriched_items)

        image_context = "[FOOD PHOTO — USDA-VERIFIED NUTRITION]\n"
        image_context += "Items:\n"
        for ei in enriched_items:
            image_context += (
                f"  {ei['name']} ({ei['grams']}g): "
                f"{ei.get('calories', '?')} cal, "
                f"{ei.get('protein', '?')}g P, "
                f"{ei.get('carbs', '?')}g C, "
                f"{ei.get('fat', '?')}g F\n"
            )
        image_context += (
            f"Meal total: {meal_total.get('calories', 0):.0f} cal, "
            f"{meal_total.get('protein', 0):.0f}g protein, "
            f"{meal_total.get('carbs', 0):.0f}g carbs, "
            f"{meal_total.get('fat', 0):.0f}g fat, "
            f"{meal_total.get('fiber', 0):.0f}g fiber\n"
        )
        # Micronutrients
        micro_parts = []
        for label, key, unit in [
            ("Vit D", "vitamin_d", "mcg"), ("Mg", "magnesium", "mg"),
            ("Zinc", "zinc", "mg"), ("Iron", "iron", "mg"),
            ("B12", "b12", "mcg"), ("K", "potassium", "mg"),
            ("Vit C", "vitamin_c", "mg"), ("Ca", "calcium", "mg"),
            ("Na", "sodium", "mg"),
        ]:
            val = meal_total.get(key, 0)
            if val:
                micro_parts.append(f"{label}: {val:.1f}{unit}")
        if micro_parts:
            image_context += f"Micros: {', '.join(micro_parts)}\n"
        image_context += "Source: USDA FoodData Central (lab-verified)\n"
        image_context += "Show the user what you see and the estimated nutrition. Ask 'want me to log it?' BEFORE calling log_meal. Do NOT auto-log unless the user's caption explicitly says 'log this' or 'add to my calories'.\n"

        if usda_failures:
            image_context += f"Could not find USDA data for: {', '.join(usda_failures)} — estimate these from your knowledge.\n"

    elif scene == "fridge":
        image_context = "[PHOTO: Inside of a fridge/pantry]\n"
        if items:
            item_names = [i.get("name", "") for i in items if i.get("name")]
            image_context += f"Items clearly visible: {', '.join(item_names)}\n"
        if meal_desc:
            image_context += f"Description: {meal_desc}\n"

    elif scene == "ingredients":
        image_context = "[PHOTO: Ingredients/groceries]\n"
        if items:
            item_names = [i.get("name", "") for i in items if i.get("name")]
            image_context += f"Items clearly visible: {', '.join(item_names)}\n"
        if meal_desc:
            image_context += f"Description: {meal_desc}\n"

    else:
        # Fallback — no USDA data available
        image_context = "[PHOTO: Food image]\n"
        if items:
            item_names = [i.get("name", "") for i in items if i.get("name")]
            image_context += f"Items visible: {', '.join(item_names)}\n"
        if meal_desc:
            image_context += f"Description: {meal_desc}\n"
        image_context += "No USDA data — estimate nutrition from your knowledge if user wants to log.\n"

    # Add daily context if we have USDA data
    if enriched_items:
        try:
            from bot.services import nutrition_service
            daily = nutrition_service.get_daily_intake(user["id"])
            if daily.get("meal_count", 0) > 0:
                image_context += (
                    f"Today so far: {daily['total_calories']:.0f} cal, "
                    f"{daily['total_protein']:.0f}g protein "
                    f"({daily['meal_count']} meal{'s' if daily['meal_count'] != 1 else ''})\n"
                )
            targets = daily.get("targets", {})
            if targets.get("calories"):
                image_context += f"Daily target: {targets['calories']} cal, {targets.get('protein_g', '?')}g protein\n"
        except Exception:
            pass

    # Check for pending barcode in caption (from label photo after failed scan)
    pending_barcode = None
    if caption and caption.startswith("[PENDING_BARCODE:"):
        import re as _re
        bc_match = _re.match(r"\[PENDING_BARCODE:(\d+)\]\s*(.*)", caption)
        if bc_match:
            pending_barcode = bc_match.group(1)
            caption = bc_match.group(2)

    if pending_barcode and scene == "label":
        # This is a nutrition label photo for a previously scanned barcode
        image_context += (
            f"\n[PENDING BARCODE: {pending_barcode}]\n"
            "The user previously scanned this barcode but it wasn't in any database. "
            "Now they're sending the nutrition label. Extract the nutrition data from the label "
            "and call save_custom_product with the barcode and nutrition info (per 100g). "
            "After saving, ask if they want to log it as a meal.\n"
        )

    # Default message based on scene
    if scene == "fridge":
        default_msg = "User sent a photo of their fridge. Suggest recipes with ONLY the visible ingredients."
    elif scene == "ingredients":
        default_msg = "User sent a photo of ingredients. Suggest recipes or ask what they want to make."
    else:
        default_msg = "User sent this food photo."

    text_for_brain = f"{image_context}\n{caption}" if caption else f"{image_context}\n{default_msg}"

    # Pass to brain
    from bot.ai.brain_v2 import ai_brain
    from bot.services import task_service
    tasks = task_service.get_tasks(user["id"])

    async def _keep_typing():
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        response = await asyncio.wait_for(
            ai_brain.process(text_for_brain, user, tasks, typing_callback=_keep_typing),
            timeout=120.0,
        )
    except asyncio.TimeoutError:
        response = "That took too long — try again."

    if response:
        reply_markup = None
        if ai_brain._paywall_hit.get(user["id"], False):
            from bot.handlers.payments import get_subscribe_keyboard
            reply_markup = get_subscribe_keyboard(update.effective_user.id)
        await send_chunked(
            bot=context.bot,
            chat_id=chat_id,
            text=response,
            reply_markup=reply_markup,
        )


def _try_detect_barcode(image_path: str) -> str | None:
    """Try to detect a barcode in the image. Runs in thread."""
    try:
        from bot.services.barcode_service import detect_barcode
        return detect_barcode(image_path)
    except Exception as e:
        logger.debug(f"Barcode detection skipped: {e}")
        return None


async def _handle_barcode(update, context, user, barcode: str, caption: str, chat_id):
    """Look up a scanned barcode and pass nutrition data to the AI brain.

    Lookup order: custom_products DB → Open Food Facts → ask user for label photo.
    """
    from bot.services import openfoodfacts_service, product_service, usda_service
    from bot.ai.brain_v2 import ai_brain
    from bot.services import task_service

    logger.info(f"Barcode scanned by user {user['id']}: {barcode}")

    product = None
    source = None

    # 1. Check custom product DB first (instant, local)
    custom = product_service.get_product_by_barcode(barcode)
    if custom:
        product = product_service.to_nutrition_dict(custom)
        source = "custom_product_db"
        logger.info(f"Custom product found for barcode {barcode}: {product.get('name')}")

    # 2. Check Open Food Facts
    if not product:
        off_result = await asyncio.to_thread(openfoodfacts_service.lookup_barcode, barcode)
        if off_result and off_result.get("name"):
            product = off_result
            source = "openfoodfacts"

    # 3. Check USDA FoodData Central (barcode/GTIN search)
    if not product:
        try:
            usda_result = await usda_service.lookup_barcode(barcode)
            if usda_result and usda_result.get("name"):
                product = usda_result
                source = "usda"
        except Exception as e:
            logger.warning(f"USDA barcode fallback failed for {barcode}: {type(e).__name__}: {e}")

    if product and product.get("name"):
        # Build rich context for the brain
        n = product.get("nutrition_per_100g", {})
        nutri_lines = []
        if n.get("calories") is not None:
            nutri_lines.append(f"Calories: {n['calories']} kcal")
        if n.get("protein_g") is not None:
            nutri_lines.append(f"Protein: {n['protein_g']}g")
        if n.get("carbs_g") is not None:
            nutri_lines.append(f"Carbs: {n['carbs_g']}g")
        if n.get("fat_g") is not None:
            nutri_lines.append(f"Fat: {n['fat_g']}g")
        if n.get("fiber_g") is not None:
            nutri_lines.append(f"Fiber: {n['fiber_g']}g")

        nutri_str = ", ".join(nutri_lines) if nutri_lines else "nutrition data not available"
        nutriscore = f", Nutri-Score: {product['nutriscore'].upper()}" if product.get("nutriscore") else ""
        quantity = f" ({product['quantity']})" if product.get("quantity") else ""

        text_for_brain = (
            f"[BARCODE SCAN: {barcode}]\n"
            f"Product: {product['name']}"
            f"{' by ' + product['brand'] if product.get('brand') else ''}"
            f"{quantity}{nutriscore}\n"
            f"Nutrition per 100g: {nutri_str}\n"
            f"Source: {source}\n\n"
        )

        if caption:
            text_for_brain += f"User's note: {caption}\n\n"

        text_for_brain += (
            "The user scanned a product barcode. Show them the product details and nutrition info. "
            "Ask if they want to log it as a meal. If they say yes or their caption implies it "
            "(e.g., 'log this as lunch'), use the log_meal tool with the nutrition data above. "
            "Estimate a reasonable serving size if the user doesn't specify one."
        )

    else:
        # Product not found anywhere — store pending barcode and ask for label photo
        context.user_data["pending_barcode"] = barcode
        logger.info(f"Barcode {barcode} not found — stored as pending, asking for label photo")

        if caption:
            text_for_brain = (
                f"[BARCODE SCAN: {barcode} — product NOT in any database]\n"
                f"User's note: {caption}\n"
                "The barcode wasn't found in any product database. "
                "Tell the user: 'I don't have this product yet. Take a photo of the nutrition label "
                "and I'll save it — next time you scan it, I'll recognize it instantly.' "
                "If the caption contains the product name, use save_custom_product to save it."
            )
        else:
            text_for_brain = (
                f"[BARCODE SCAN: {barcode} — product NOT in any database]\n"
                "The barcode wasn't found in any product database. "
                "Tell the user: 'I don't have this product yet. Take a photo of the nutrition label "
                "and I'll save it — next time you scan it, I'll recognize it instantly.'"
            )

    tasks = task_service.get_tasks(user["id"])

    async def _keep_typing():
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        response = await asyncio.wait_for(
            ai_brain.process(text_for_brain, user, tasks, typing_callback=_keep_typing),
            timeout=120.0,
        )
    except asyncio.TimeoutError:
        response = "That took too long — try again."

    if response:
        reply_markup = None
        if ai_brain._paywall_hit.get(user["id"], False):
            from bot.handlers.payments import get_subscribe_keyboard
            reply_markup = get_subscribe_keyboard(update.effective_user.id)
        await send_chunked(
            bot=context.bot,
            chat_id=chat_id,
            text=response,
            reply_markup=reply_markup,
        )


async def _handle_supplement(update, context, user, b64_data, media_type, api_key, caption, description, chat_id):
    """Extract supplement details from photo, build rich context, pass to brain."""
    extraction = await asyncio.to_thread(
        _extract_supplement_vision, b64_data, media_type, api_key
    )

    if not extraction or not extraction.get("is_supplement"):
        # Classifier said supplement but extraction disagrees — treat as general
        await _handle_general(update, context, user, caption, description, chat_id)
        return

    products = extraction.get("products", [])
    photo_type = extraction.get("photo_type", "single_bottle")

    if not products:
        # Extraction succeeded but no products parsed — fall back
        await _handle_general(update, context, user, caption, description, chat_id)
        return

    # Build rich context for brain
    image_context = f"[SUPPLEMENT PHOTO — {len(products)} product{'s' if len(products) > 1 else ''} detected]\n"

    for i, product in enumerate(products, 1):
        name = product.get("product_name", "Unknown product")
        brand = product.get("brand", "")
        supp_type = product.get("supplement_type", "")
        serving = product.get("serving_size", "")
        servings_count = product.get("servings_per_container")
        suggested_use = product.get("suggested_use", "")
        warnings = product.get("warnings", "")

        if len(products) > 1:
            image_context += f"\nProduct {i}:\n"

        header = name
        if brand:
            header = f"{brand} — {name}"
        image_context += f"Product: {header}\n"

        if supp_type:
            image_context += f"Type: {supp_type}\n"

        # Key ingredients
        ingredients = product.get("key_ingredients", [])
        if ingredients:
            image_context += "Ingredients per serving:\n"
            for ing in ingredients:
                ing_name = ing.get("name", "?")
                amount = ing.get("amount")
                unit = ing.get("unit", "")
                if amount:
                    image_context += f"  - {ing_name}: {amount}{unit}\n"
                else:
                    image_context += f"  - {ing_name}\n"

        if serving:
            image_context += f"Serving size: {serving}\n"
        if servings_count:
            image_context += f"Servings per container: {servings_count}\n"

        other = product.get("other_ingredients", "")
        if other:
            image_context += f"Other ingredients: {other}\n"

        if suggested_use:
            image_context += f"Suggested use: {suggested_use}\n"
        if warnings:
            image_context += f"Warnings: {warnings}\n"

    # Instructions for brain
    image_context += "\n--- INSTRUCTIONS ---\n"
    image_context += "The user sent a photo of supplement(s). You should:\n"
    image_context += "1. Identify what it is and comment on quality/form (e.g., glycinate vs oxide for magnesium)\n"
    image_context += "2. Check against user's CURRENT supplement stack — flag duplicates or redundancy\n"
    image_context += "3. Check for interactions with user's active peptide protocols\n"
    image_context += "4. Comment on dosing (is it adequate? typical range?)\n"
    image_context += "5. Suggest optimal timing based on the specific supplement(s)\n"
    image_context += "6. If the user seems to want to add it to their stack, use manage_supplement tool\n"
    image_context += "7. If relevant, search_knowledge_base for deeper info on the supplement\n"
    image_context += "Keep it conversational and concise — don't dump all info at once.\n"

    text_for_brain = f"{image_context}\n{caption}" if caption else f"{image_context}\nUser sent this supplement photo."

    from bot.ai.brain_v2 import ai_brain
    from bot.services import task_service
    tasks = task_service.get_tasks(user["id"])

    async def _keep_typing():
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        response = await asyncio.wait_for(
            ai_brain.process(text_for_brain, user, tasks, typing_callback=_keep_typing),
            timeout=120.0,
        )
    except asyncio.TimeoutError:
        response = "That took too long — try again."

    if response:
        reply_markup = None
        if ai_brain._paywall_hit.get(user["id"], False):
            from bot.handlers.payments import get_subscribe_keyboard
            reply_markup = get_subscribe_keyboard(update.effective_user.id)
        await send_chunked(
            bot=context.bot,
            chat_id=chat_id,
            text=response,
            reply_markup=reply_markup,
        )
    logger.info(f"Supplement photo processed for user {user['id']}: {len(products)} products, type={photo_type}")


async def _handle_general(update, context, user, caption, description, chat_id):
    """Pass general image description to brain for natural response."""
    image_context = f"[PHOTO: {description}]\n"
    text_for_brain = f"{image_context}\n{caption}" if caption else f"{image_context}\nUser sent this photo."

    from bot.ai.brain_v2 import ai_brain
    from bot.services import task_service
    tasks = task_service.get_tasks(user["id"])

    async def _keep_typing():
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        response = await asyncio.wait_for(
            ai_brain.process(text_for_brain, user, tasks, typing_callback=_keep_typing),
            timeout=120.0,
        )
    except asyncio.TimeoutError:
        response = "That took too long — try again."

    if response:
        reply_markup = None
        if ai_brain._paywall_hit.get(user["id"], False):
            from bot.handlers.payments import get_subscribe_keyboard
            reply_markup = get_subscribe_keyboard(update.effective_user.id)
        await send_chunked(
            bot=context.bot,
            chat_id=chat_id,
            text=response,
            reply_markup=reply_markup,
        )


# ---------------------------------------------------------------------------
# Vision API calls (run in thread to avoid blocking event loop)
# ---------------------------------------------------------------------------

def _classify_image(b64_data: str, media_type: str, api_key: str, is_pdf: bool = False) -> dict:
    """Classify an image as bloodwork, food, supplement, or other. Cheap Haiku call."""
    from bot.ai.brain_v2 import _get_client  # Reuse singleton client instead of creating new one

    try:
        client = _get_client()

        if is_pdf:
            file_block = {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": b64_data},
            }
        else:
            file_block = {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64_data},
            }

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": [file_block, {"type": "text", "text": IMAGE_CLASSIFICATION_PROMPT}],
            }],
            timeout=30.0,
        )

        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        return json.loads(text)

    except Exception as e:
        logger.error(f"Image classification failed: {type(e).__name__}: {e}")
        return {"type": "other", "description": "an image (classification failed)"}


def _extract_food_vision(b64_data: str, media_type: str, api_key: str) -> dict:
    """Extract food items with USDA-friendly names and portion estimates. Uses Sonnet for accuracy."""
    from bot.ai.brain_v2 import _get_client  # Reuse singleton client

    try:
        client = _get_client()

        file_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64_data},
        }

        response = client.messages.create(
            model="claude-sonnet-4-5-20250514",
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": [file_block, {"type": "text", "text": FOOD_EXTRACTION_PROMPT}],
            }],
            timeout=60.0,
        )

        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        return json.loads(text)

    except Exception as e:
        logger.error(f"Food extraction failed: {type(e).__name__}: {e}")
        return {"items": [], "meal_description": "food (extraction failed)"}


def _extract_bloodwork_vision(b64_data: str, media_type: str, api_key: str, is_pdf: bool = False) -> dict | None:
    """Call Claude to extract bloodwork markers from an image or PDF. Runs in thread."""
    from bot.ai.brain_v2 import _get_client  # Reuse singleton client

    try:
        client = _get_client()

        if is_pdf:
            file_block = {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": b64_data},
            }
        else:
            file_block = {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64_data},
            }

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4000,
            messages=[{
                "role": "user",
                "content": [
                    file_block,
                    {"type": "text", "text": BLOODWORK_EXTRACTION_PROMPT},
                ],
            }],
            timeout=90.0,
        )

        if not response.content:
            return None

        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        return json.loads(text)

    except json.JSONDecodeError as e:
        logger.error(f"Vision JSON parse error: {e}")
        return None
    except Exception as e:
        logger.error(f"Vision extraction failed: {type(e).__name__}: {e}")
        return None


def _extract_workout_pdf_vision(b64_data: str, media_type: str, api_key: str, is_pdf: bool = False) -> dict | None:
    """Extract workout data from a training document or image. Runs in thread."""
    from bot.ai.brain_v2 import _get_client

    try:
        client = _get_client()

        if is_pdf:
            file_block = {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": b64_data},
            }
        else:
            file_block = {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64_data},
            }

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": [file_block, {"type": "text", "text": WORKOUT_PDF_EXTRACTION_PROMPT}],
            }],
            timeout=60.0,
        )

        if not response.content:
            return None

        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        return json.loads(text)

    except json.JSONDecodeError as e:
        logger.error(f"Workout PDF JSON parse error: {e}")
        return None
    except Exception as e:
        logger.error(f"Workout PDF extraction failed: {type(e).__name__}: {e}")
        return None


def _extract_supplement_vision(b64_data: str, media_type: str, api_key: str) -> dict | None:
    """Extract supplement details from a product photo. Uses Sonnet for label accuracy."""
    from bot.ai.brain_v2 import _get_client  # Reuse singleton client

    try:
        client = _get_client()

        file_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64_data},
        }

        response = client.messages.create(
            model="claude-sonnet-4-5-20250514",
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": [file_block, {"type": "text", "text": SUPPLEMENT_EXTRACTION_PROMPT}],
            }],
            timeout=60.0,
        )

        if not response.content:
            return None

        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        return json.loads(text)

    except json.JSONDecodeError as e:
        logger.error(f"Supplement vision JSON parse error: {e}")
        return None
    except Exception as e:
        logger.error(f"Supplement vision extraction failed: {type(e).__name__}: {e}")
        return None
