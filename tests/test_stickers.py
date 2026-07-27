import asyncio
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramAPIError

from app import emoji, stickers, texts
from app.models import ExtractedRequisites


def _sticker(emoji: str, file_id: str):
    return type("Sticker", (), {"emoji": emoji, "file_id": file_id})()


def _emoji_sticker(emoji: str, custom_emoji_id: str):
    return type("Sticker", (), {"emoji": emoji, "custom_emoji_id": custom_emoji_id})()


def _bot_returning(*sticker_objs) -> AsyncMock:
    bot = AsyncMock()
    bot.get_sticker_set.return_value = type("StickerSet", (), {"stickers": list(sticker_objs)})()
    return bot


@pytest.fixture(autouse=True)
def _reset_cache():
    stickers.processing_sticker_file_id = None
    emoji.set_custom_emoji_ids({})
    yield
    stickers.processing_sticker_file_id = None
    emoji.set_custom_emoji_ids({})


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


def test_resolve_custom_emoji_caches_every_base_id():
    bot = _bot_returning(_emoji_sticker("⚙️", "GEAR_EID"), _emoji_sticker("🧾", "RECEIPT_EID"))
    asyncio.run(stickers.resolve_custom_emoji(bot))

    assert emoji.render("⚙️") == '<tg-emoji emoji-id="GEAR_EID">⚙️</tg-emoji>'
    assert emoji.render("🧾") == '<tg-emoji emoji-id="RECEIPT_EID">🧾</tg-emoji>'
    bot.get_sticker_set.assert_awaited_once_with(stickers.EMOJI_SET_NAME)


def test_resolve_custom_emoji_leaves_missing_base_as_plain():
    bot = _bot_returning(_emoji_sticker("⚙️", "GEAR_EID"))
    asyncio.run(stickers.resolve_custom_emoji(bot))

    assert emoji.render("⚙️") == '<tg-emoji emoji-id="GEAR_EID">⚙️</tg-emoji>'
    assert emoji.render("🧾") == "🧾"


def test_resolve_custom_emoji_falls_back_to_plain_on_api_error():
    bot = AsyncMock()
    bot.get_sticker_set.side_effect = TelegramAPIError(method=None, message="boom")
    asyncio.run(stickers.resolve_custom_emoji(bot))

    assert emoji.render("⚙️") == "⚙️"
    assert emoji.render("🧾") == "🧾"


def test_resolved_emoji_reach_the_rendered_status_and_success_text():
    # Pins the cache-key contract between resolve (writer) and texts (reader), untested by the per-seam tests.
    bot = _bot_returning(_emoji_sticker("⚙️", "GEAR_EID"), _emoji_sticker("🧾", "RECEIPT_EID"))
    asyncio.run(stickers.resolve_custom_emoji(bot))

    assert texts.status_extracting().startswith('<tg-emoji emoji-id="GEAR_EID">⚙️</tg-emoji> ')
    qr = type("Qr", (), {"url": "https://bank.gov.ua/qr/x", "mono_url": "https://send.monobank.ua/qr/x"})()
    success = texts.format_success(ExtractedRequisites(iban="UA693000010000000012345678901"), [], qr)
    assert success.startswith('<tg-emoji emoji-id="RECEIPT_EID">🧾</tg-emoji> ')
