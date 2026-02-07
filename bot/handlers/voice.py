"""Voice message handler - transcribes voice notes via Groq Whisper."""
import logging
import os
import tempfile
from telegram import Update
from telegram.ext import ContextTypes
import config

logger = logging.getLogger(__name__)


def is_voice_configured() -> bool:
    """Check if voice transcription is available."""
    return bool(config.GROQ_API_KEY)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages: transcribe with Whisper via Groq, then process as text."""
    from bot.handlers.tasks import is_authorized
    from bot.handlers.reminders import register_chat_id

    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Sorry, you're not authorized to use this bot.")
        return

    if not config.GROQ_API_KEY:
        await update.message.reply_text("Voice messages aren't configured yet. Set GROQ_API_KEY to enable.")
        return

    voice = update.message.voice or update.message.audio
    if not voice:
        return

    register_chat_id(update.effective_chat.id)

    # Show typing indicator while transcribing
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    tmp_path = None
    try:
        # Download the voice file
        voice_file = await context.bot.get_file(voice.file_id)
        tmp_path = tempfile.mktemp(suffix=".ogg")
        await voice_file.download_to_drive(tmp_path)

        logger.info(f"Voice message downloaded ({voice.duration}s, {voice.file_size} bytes)")

        # Transcribe with Whisper via Groq (no content filtering)
        text = await _transcribe(tmp_path)

        if not text or not text.strip():
            await update.message.reply_text("Couldn't catch that \u2014 try again?")
            return

        text = text.strip()
        logger.info(f"Transcribed voice: {text[:100]}...")

        # Show the user what we heard
        await update.message.reply_text(f"\U0001f399\ufe0f _{text}_", parse_mode="Markdown")

        # Process transcribed text directly (can't set text on frozen Message object)
        await _process_transcribed_text(update, context, text)

    except Exception as e:
        logger.error(f"Voice handling failed: {type(e).__name__}: {e}")
        await update.message.reply_text("Had trouble processing that voice message. Try again or type it out.")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


async def _process_transcribed_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Process transcribed text through the AI or rule-based pipeline."""
    from bot.handlers.tasks import handle_ai_message, detect_intent, handle_done, handle_delete, handle_list
    from bot.services.notion import notion_service
    from bot.services.classifier import parse_task_input

    # Try AI mode first
    if config.AI_MODE == "smart" and config.ANTHROPIC_API_KEY:
        handled = await handle_ai_message(update, context, text)
        if handled:
            return

    # Fallback to rule-based detection
    intent = detect_intent(text)

    if intent["action"] == "delete":
        await handle_delete(update, intent["task_num"])
    elif intent["action"] == "done":
        await handle_done(update, intent["task_num"])
    elif intent["action"] == "list":
        await handle_list(update, intent.get("category"))
    else:
        # Default: create a task
        task_info = parse_task_input(text)
        notion_service.add_task(
            title=task_info["title"],
            category=task_info.get("category", "Personal"),
            due_date=task_info.get("due_date"),
            priority=task_info.get("priority", "Medium")
        )
        await update.message.reply_text(f"\u2705 Added: {task_info['title']}")


async def _transcribe(file_path: str) -> str:
    """Transcribe audio file using Whisper via Groq API (no content filtering)."""
    import httpx

    with open(file_path, "rb") as audio_file:
        response = httpx.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            files={"file": ("voice.ogg", audio_file, "audio/ogg")},
            data={"model": "whisper-large-v3"},
            timeout=30.0
        )

    if response.status_code != 200:
        logger.error(f"Groq API error {response.status_code}: {response.text[:200]}")
        raise Exception(f"Transcription failed: {response.status_code}")

    return response.json().get("text", "")
