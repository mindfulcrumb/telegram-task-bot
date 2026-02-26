"""Photo/document handler — blood test uploads via Claude Vision (images + PDFs)."""
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


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo uploads — detect blood tests and extract biomarkers."""
    from bot.services import user_service

    tg = update.effective_user
    user = context.user_data.get("db_user")
    if not user:
        user = user_service.get_or_create_user(tg.id, tg.username, tg.first_name)
        context.user_data["db_user"] = user

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
        # Photos come in multiple sizes — grab the largest
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

        logger.info(f"{'PDF' if is_pdf else 'Photo'} from user {user['id']} — processing for blood test detection")

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

        # Call Claude Vision to extract bloodwork
        extraction = await asyncio.to_thread(
            _extract_bloodwork_vision, b64_data, media_type, api_key, is_pdf
        )

        if not extraction or not extraction.get("is_blood_test"):
            # Not a blood test — pass to AI brain with the image context
            caption = update.message.caption or ""
            if caption:
                # User sent a photo with a caption — process caption as text
                from bot.ai.brain_v2 import ai_brain
                from bot.services import task_service
                tasks = task_service.get_tasks(user["id"])

                async def _keep_typing():
                    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

                try:
                    response = await asyncio.wait_for(
                        ai_brain.process(caption, user, tasks, typing_callback=_keep_typing),
                        timeout=120.0,
                    )
                except asyncio.TimeoutError:
                    response = "That took too long — try again."

                if response:
                    # If paywall was hit, attach subscribe button
                    reply_markup = None
                    if ai_brain._paywall_hit:
                        from bot.handlers.payments import get_subscribe_keyboard
                        reply_markup = get_subscribe_keyboard(update.effective_user.id)
                    await send_chunked(
                        bot=context.bot,
                        chat_id=update.effective_chat.id,
                        text=response,
                        reply_markup=reply_markup,
                    )
            # No caption and not a blood test — just ignore silently
            return

        # It's a blood test — log the markers
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
        flagged = [m for m in clean_markers if m.get("reference_low") is not None or m.get("reference_high") is not None]
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

    except Exception as e:
        logger.error(f"Photo handling failed: {type(e).__name__}: {e}")
        await update.message.reply_text("Had trouble reading that. Try a clearer photo or PDF, or type the values.")
    finally:
        typing_active = False
        typing_task.cancel()
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _extract_bloodwork_vision(b64_data: str, media_type: str, api_key: str, is_pdf: bool = False) -> dict | None:
    """Call Claude to extract bloodwork markers from an image or PDF. Runs in thread."""
    import anthropic

    try:
        client = anthropic.Anthropic(api_key=api_key)

        # PDFs use "document" content block, images use "image"
        if is_pdf:
            file_block = {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": b64_data,
                },
            }
        else:
            file_block = {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": b64_data,
                },
            }

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4000,
            messages=[{
                "role": "user",
                "content": [
                    file_block,
                    {
                        "type": "text",
                        "text": BLOODWORK_EXTRACTION_PROMPT,
                    },
                ],
            }],
            timeout=90.0,
        )

        if not response.content:
            return None

        text = response.content[0].text.strip()

        # Parse JSON (handle markdown code blocks)
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
