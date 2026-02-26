"""Voice message handler v2 — transcribe via Groq Whisper, feed into AI brain."""
import asyncio
import logging
import os
import re
import tempfile
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


def _clean_response(text: str) -> str:
    """Strip markdown formatting characters from AI response."""
    if not text:
        return text
    # Remove bold/italic markers
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'\1', text)
    # Remove backtick code formatting
    text = re.sub(r'`(.+?)`', r'\1', text)
    # Remove header markers
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Convert markdown bullet lists to clean text
    text = re.sub(r'^[\-\*]\s+', '', text, flags=re.MULTILINE)
    # Clean leftover stray asterisks
    text = re.sub(r'(?<!\w)\*(\w)', r'\1', text)
    text = re.sub(r'(\w)\*(?!\w)', r'\1', text)
    return text.strip()


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

    chat_id = update.effective_chat.id

    # Keep typing dots alive the entire time
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
        voice_file = await context.bot.get_file(voice.file_id)
        tmp_path = tempfile.mktemp(suffix=".ogg")
        await voice_file.download_to_drive(tmp_path)

        logger.info(f"Voice message from user {user['id']} ({voice.duration}s)")

        text = await _transcribe(tmp_path, groq_key)

        if not text or not text.strip():
            await update.message.reply_text("Couldn't catch that \u2014 try again?")
            return

        text = text.strip()

        # Feed into AI brain (same path as text messages)
        async def _keep_typing():
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        tasks = task_service.get_tasks(user["id"])

        # Timeout safety: if brain takes >120s, return a fallback instead of hanging
        try:
            response = await asyncio.wait_for(
                ai_brain.process(text, user, tasks, typing_callback=_keep_typing),
                timeout=120.0,
            )
        except asyncio.TimeoutError:
            logger.error(f"Voice brain processing timed out for user {user['id']}")
            await update.message.reply_text("That took too long to process. Try sending it again or type it out.")
            return

        if response:
            # Strip any markdown formatting the AI snuck in (same as text handler)
            response = _clean_response(response)

            # If paywall was hit, attach subscribe button
            reply_markup = None
            if ai_brain._paywall_hit:
                from bot.handlers.payments import get_subscribe_keyboard
                reply_markup = get_subscribe_keyboard(update.effective_user.id)

            if len(response) <= 4096:
                await update.message.reply_text(response, reply_markup=reply_markup)
            else:
                chunks = [response[i:i + 4096] for i in range(0, len(response), 4096)]
                for i, chunk in enumerate(chunks):
                    # Attach button to last chunk only
                    markup = reply_markup if i == len(chunks) - 1 else None
                    await update.message.reply_text(chunk, reply_markup=markup)
        else:
            await update.message.reply_text("Something went wrong processing that. Try again or type it out.")

    except Exception as e:
        logger.error(f"Voice handling failed: {type(e).__name__}: {e}")
        await update.message.reply_text("Had trouble with that voice message. Try again or type it out.")
    finally:
        typing_active = False
        typing_task.cancel()
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


async def _transcribe(file_path: str, api_key: str) -> str:
    """Transcribe audio via Groq Whisper API (async to avoid blocking event loop)."""
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        with open(file_path, "rb") as audio_file:
            response = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("voice.ogg", audio_file, "audio/ogg")},
                data={"model": "whisper-large-v3"},
            )

    if response.status_code != 200:
        logger.error(f"Groq Whisper error {response.status_code}: {response.text[:200]}")
        raise Exception(f"Transcription failed: {response.status_code}")

    return response.json().get("text", "")
