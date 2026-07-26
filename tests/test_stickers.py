import asyncio
from unittest.mock import AsyncMock

import pytest

from app import stickers


def _sticker(emoji: str, file_id: str):
    return type("Sticker", (), {"emoji": emoji, "file_id": file_id})()


def _bot_returning(*sticker_objs) -> AsyncMock:
    bot = AsyncMock()
    bot.get_sticker_set.return_value = type("StickerSet", (), {"stickers": list(sticker_objs)})()
    return bot


@pytest.fixture(autouse=True)
def _reset_cache():
    stickers.processing_sticker_file_id = None
    yield
    stickers.processing_sticker_file_id = None


def test_resolve_finds_gear_emoji_and_caches_file_id():
    bot = _bot_returning(_sticker("🙂", "OTHER"), _sticker("⚙️", "GEAR_ID"))
    file_id = asyncio.run(stickers.resolve_processing_sticker(bot))

    assert file_id == "GEAR_ID"
    assert stickers.processing_sticker_file_id == "GEAR_ID"
    bot.get_sticker_set.assert_awaited_once_with(stickers.STICKER_SET_NAME)


def test_resolve_raises_when_gear_emoji_absent():
    bot = _bot_returning(_sticker("🙂", "OTHER"))
    with pytest.raises(RuntimeError):
        asyncio.run(stickers.resolve_processing_sticker(bot))
    assert stickers.processing_sticker_file_id is None
