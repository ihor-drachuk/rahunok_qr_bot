"""Resolves the bot's pack assets at startup: the processing sticker's file_id and the custom-emoji IDs."""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app import emoji

logger = logging.getLogger(__name__)

STICKER_SET_NAME = "rahunok_qr_bot"
PROCESSING_STICKER_EMOJI = "⚙️"

EMOJI_SET_NAME = "rahunok_qr_emoji"
# Base emoji whose custom-pack variants are used inline in message text (see app.emoji).
CUSTOM_EMOJIS = ("⚙️", "🧾")

# Populated by resolve_processing_sticker() at startup, then read by handlers.
processing_sticker_file_id: str | None = None


async def resolve_processing_sticker(bot: Bot) -> str:
    """Look up the ⚙️ sticker in the pack and cache its file_id. Raises if absent."""
    global processing_sticker_file_id
    sticker_set = await bot.get_sticker_set(STICKER_SET_NAME)
    for sticker in sticker_set.stickers:
        if sticker.emoji == PROCESSING_STICKER_EMOJI:
            processing_sticker_file_id = sticker.file_id
            return sticker.file_id
    raise RuntimeError(
        f"Sticker {PROCESSING_STICKER_EMOJI!r} not found in pack {STICKER_SET_NAME!r}")


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
