"""Shared utilities — typing pauses, message helpers."""
import asyncio
from telegram.constants import ChatAction


async def typing_pause(chat, seconds: float = 0.8):
    """Show typing indicator and pause — makes the bot feel human.

    Works with a Chat object (update.message.chat) that has send_action().
    """
    await chat.send_action(ChatAction.TYPING)
    await asyncio.sleep(seconds)


async def typing_pause_bot(bot, chat_id: int, seconds: float = 0.8):
    """Show typing indicator via bot instance — for scheduled/proactive messages.

    Use when you have context.bot + chat_id instead of a Chat object.
    """
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    await asyncio.sleep(seconds)
