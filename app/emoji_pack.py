"""Resolves the bot's custom-emoji IDs from its emoji pack, once at startup."""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app import emoji

logger = logging.getLogger(__name__)

EMOJI_SET_NAME = "rahunok_qr_emoji"
# Base emoji whose custom-pack variants are used inline in message text (see app.emoji).
CUSTOM_EMOJIS = ("⚙️", "🧾")


async def resolve_custom_emoji(bot: Bot) -> None:
    """Cache the custom_emoji_id of each CUSTOM_EMOJIS entry from the emoji pack.

    Cosmetic: on any failure or a missing emoji, texts fall back to plain Unicode, so a lookup
    problem must not stop the bot from starting.
    """
    try:
        emoji_set = await bot.get_sticker_set(EMOJI_SET_NAME)
    except TelegramAPIError:
        logger.warning("Could not fetch emoji pack %r; using plain emoji", EMOJI_SET_NAME)
        return
    by_emoji = {sticker.emoji: sticker.custom_emoji_id for sticker in emoji_set.stickers}
    resolved = {base: by_emoji[base] for base in CUSTOM_EMOJIS if by_emoji.get(base)}
    for base in CUSTOM_EMOJIS:
        if base not in resolved:
            logger.warning("Emoji %r not found in pack %r; using plain emoji", base, EMOJI_SET_NAME)
    emoji.set_custom_emoji_ids(resolved)
