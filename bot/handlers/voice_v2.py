"""Voice message handler v2 — transcribe via Groq Whisper, feed into AI brain."""
import logging
import os
import tempfile
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


def is_voice_configured() -> bool:
    """Check if voice transcription is available."""
    return bool(os.environ.get("GROQ_API_KEY"))


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages: transcribe then process as text through AI brain."""
    from bot.services import user_service, task_service
    from bot.ai.brain_v2 import ai_brain

    tg = update.effective_user
    user = context.user_data.get("db_user")
    if not user:
        user = user_service.get_or_create_user(tg.id, tg.username, tg.first_name)
        context.user_data["db_user"] = user

    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        await update.message.reply_text("Voice messages aren't set up yet — type it out for now.")
        return

    voice = update.message.voice or update.message.audio
    if not voice:
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    tmp_path = None
    try:
        voice_file = await context.bot.get_file(voice.file_id)
        tmp_path = tempfile.mktemp(suffix=".ogg")
        await voice_file.download_to_drive(tmp_path)

        logger.info(f"Voice message from user {user['id']} ({voice.duration}s)")

        text = await _transcribe(tmp_path, groq_key)

        if not text or not text.strip():
            await update.message.reply_text("Couldn't catch that \u2014 try again?")
            return

        text = text.strip()

        # Show user what we heard
        await update.message.reply_text(f"\U0001f399\ufe0f _{text}_", parse_mode="Markdown")

        # Feed into AI brain (same path as text messages)
        tasks = task_service.get_tasks(user["id"])
        response = await ai_brain.process(text, user, tasks)

        if response:
            if len(response) <= 4096:
                await update.message.reply_text(response)
            else:
                for i in range(0, len(response), 4096):
                    await update.message.reply_text(response[i:i + 4096])

    except Exception as e:
        logger.error(f"Voice handling failed: {type(e).__name__}: {e}")
        await update.message.reply_text("Had trouble with that voice message. Try again or type it out.")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


async def _transcribe(file_path: str, api_key: str) -> str:
    """Transcribe audio via Groq Whisper API."""
    import httpx

    with open(file_path, "rb") as audio_file:
        response = httpx.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": ("voice.ogg", audio_file, "audio/ogg")},
            data={"model": "whisper-large-v3"},
            timeout=30.0,
        )

    if response.status_code != 200:
        logger.error(f"Groq Whisper error {response.status_code}: {response.text[:200]}")
        raise Exception(f"Transcription failed: {response.status_code}")

    return response.json().get("text", "")
