"""Resolves the processing sticker's file_id from the bot's own pack, once at startup."""

from aiogram import Bot

STICKER_SET_NAME = "rahunok_qr_bot"
PROCESSING_STICKER_EMOJI = "⚙️"

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
