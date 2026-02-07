"""Voice message handler - transcribes voice notes via OpenAI Whisper."""
import logging
import os
import tempfile
from telegram import Update
from telegram.ext import ContextTypes
import config

logger = logging.getLogger(__name__)


def is_voice_configured() -> bool:
    """Check if voice transcription is available."""
    return bool(config.OPENAI_API_KEY)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages: transcribe with Whisper, then process as text."""
    from bot.handlers.tasks import is_authorized, handle_ai_message, handle_message

    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Sorry, you're not authorized to use this bot.")
        return

    if not config.OPENAI_API_KEY:
        await update.message.reply_text("Voice messages aren't configured yet. Set OPENAI_API_KEY to enable.")
        return

    voice = update.message.voice or update.message.audio
    if not voice:
        return

    # Show typing indicator while transcribing
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    tmp_path = None
    try:
        # Download the voice file
        voice_file = await context.bot.get_file(voice.file_id)
        tmp_path = tempfile.mktemp(suffix=".ogg")
        await voice_file.download_to_drive(tmp_path)

        logger.info(f"Voice message downloaded ({voice.duration}s, {voice.file_size} bytes)")

        # Transcribe with Whisper
        text = await _transcribe(tmp_path)

        if not text or not text.strip():
            await update.message.reply_text("Couldn't catch that — try again?")
            return

        logger.info(f"Transcribed voice: {text[:100]}...")

        # Show the user what we heard
        await update.message.reply_text(f"🎙️ _{text}_", parse_mode="Markdown")

        # Process transcribed text through the normal message pipeline
        # Temporarily set the message text so handle_message can use it
        update.message.text = text
        await handle_message(update, context)

    except Exception as e:
        logger.error(f"Voice handling failed: {type(e).__name__}: {e}")
        await update.message.reply_text("Had trouble processing that voice message. Try again or type it out.")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


async def _transcribe(file_path: str) -> str:
    """Transcribe audio file using OpenAI Whisper API."""
    from openai import OpenAI

    client = OpenAI(api_key=config.OPENAI_API_KEY)

    with open(file_path, "rb") as audio_file:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file
        )

    return response.text
