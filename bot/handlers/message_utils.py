"""Message delivery utilities — chunking, typing simulation, markdown cleanup.

Provides send_chunked() that works for both interactive (update-based)
and proactive (bot + chat_id) contexts. Ensures Zoe never sends
wall-of-text messages.
"""
import asyncio
import logging
import re

from telegram import Bot, InlineKeyboardMarkup
from telegram.constants import ChatAction

logger = logging.getLogger(__name__)


def clean_response(text: str) -> str:
    """Strip markdown formatting characters from AI response.

    The AI is told not to use markdown, but this is a safety net
    to catch any stray *, **, _, `, # characters that slip through.
    """
    if not text:
        return text

    # Remove bold markers: **text** -> text (DOTALL for multi-line spans)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text, flags=re.DOTALL)
    # Remove italic markers: *text* -> text (DOTALL for multi-line spans)
    text = re.sub(r'\*(.+?)\*', r'\1', text, flags=re.DOTALL)
    # Remove underscore emphasis: _text_ -> text
    text = re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'\1', text, flags=re.DOTALL)

    # Remove backtick code formatting: `text` -> text
    text = re.sub(r'`(.+?)`', r'\1', text, flags=re.DOTALL)
    # Remove triple-backtick code blocks
    text = re.sub(r'```[\s\S]*?```', '', text)

    # Remove header markers: ### Header -> Header
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)

    # Convert markdown bullet lists to clean text: "- item" -> "item"
    text = re.sub(r'^[\-\*]\s+', '  ', text, flags=re.MULTILINE)

    # Clean up any leftover stray asterisks at start/end of words
    text = re.sub(r'(?<!\w)\*(\w)', r'\1', text)
    text = re.sub(r'(\w)\*(?!\w)', r'\1', text)

    # Remove stray double asterisks (e.g., orphaned ** at line boundaries)
    text = re.sub(r'\*\*', '', text)

    return text.strip()


def _split_sentences(text: str) -> list[str]:
    """Split a wall-of-text paragraph into sentence groups of 2-3."""
    raw = re.split(r'(?<=[.!?])\s+', text)
    groups = []
    current = []
    current_len = 0
    for sentence in raw:
        if current_len + len(sentence) > 300 and current:
            groups.append(" ".join(current))
            current = [sentence]
            current_len = len(sentence)
        else:
            current.append(sentence)
            current_len += len(sentence) + 1
    if current:
        groups.append(" ".join(current))
    return groups if groups else [text]


def break_into_chunks(text: str, max_chunks: int = 4) -> list[str]:
    """Break text into natural message chunks.

    1. Short text (<=300 chars) → single chunk
    2. Split on \\n\\n, fallback to \\n
    3. Safety net: sentence-level split for chunks >400 chars with no newlines
    4. Merge tiny chunks (<80 chars) with next
    5. Cap at max_chunks
    6. Enforce Telegram's 4096-char limit
    """
    if not text:
        return []

    text = text.strip()

    if len(text) <= 300:
        return [text]

    # Split on double-newlines (paragraph breaks)
    chunks = [c.strip() for c in text.split("\n\n") if c.strip()]

    # If that produced only 1 chunk, try single newlines
    if len(chunks) <= 1:
        chunks = [c.strip() for c in text.split("\n") if c.strip()]

    # Safety net: sentence-level splitting for wall-of-text chunks
    expanded = []
    for chunk in chunks:
        if len(chunk) > 400 and "\n" not in chunk:
            expanded.extend(_split_sentences(chunk))
        else:
            expanded.append(chunk)
    chunks = expanded

    # Merge tiny chunks (<80 chars) with the next one
    merged = []
    buffer = ""
    for chunk in chunks:
        if buffer:
            combined = buffer + "\n\n" + chunk
            if len(combined) <= 600:
                buffer = combined
            else:
                merged.append(buffer)
                buffer = chunk
        else:
            if len(chunk) < 80 and chunk != chunks[-1]:
                buffer = chunk
            else:
                merged.append(chunk)
    if buffer:
        merged.append(buffer)

    # Cap at max_chunks — combine remainder into last chunk
    if len(merged) > max_chunks:
        merged = merged[:max_chunks - 1] + ["\n\n".join(merged[max_chunks - 1:])]

    # Enforce Telegram's 4096 char limit
    result = []
    for chunk in merged:
        if len(chunk) > 4096:
            chunk = chunk[:4096]
        result.append(chunk)

    return result if result else [text[:4096]]


async def send_chunked(
    bot: Bot,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    proactive: bool = False,
) -> None:
    """Send text as natural chunked messages with typing simulation.

    Works for both interactive and proactive contexts.

    Args:
        bot: The Telegram Bot instance.
        chat_id: Target chat ID.
        text: Full text to send (will be cleaned and chunked).
        reply_markup: Optional inline keyboard (attached to last chunk only).
        proactive: If True, uses shorter delays (for batch proactive sends).
    """
    if not text:
        return

    text = clean_response(text)
    chunks = break_into_chunks(text)

    for i, chunk in enumerate(chunks):
        if i > 0:
            # Show typing indicator
            try:
                await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            except Exception:
                pass
            # Delay scales with chunk length
            if proactive:
                delay = min(0.3 + len(chunk) * 0.001, 1.0)
            else:
                delay = min(0.2 + len(chunk) * 0.001, 0.8)  # Reduced from 1.8s max for faster response
            await asyncio.sleep(delay)

        # Attach reply_markup to last chunk only
        markup = reply_markup if (i == len(chunks) - 1) else None

        await bot.send_message(chat_id=chat_id, text=chunk, reply_markup=markup)
