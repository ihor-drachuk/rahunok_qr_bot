import asyncio

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetChat

from app import main
from app.config import BotConfig
from tests.telegram_fakes import DEVELOPER_CHAT_ID, make_bot


def test_unreachable_feedback_chat_stops_startup():
    bot, session = make_bot()
    session.fail = lambda method: TelegramBadRequest(method=method, message="Bad Request: chat not found")
    with pytest.raises(RuntimeError, match="RAHUNOK_QR_BOT_FEEDBACK_CHAT_ID"):
        asyncio.run(main._check_feedback_chat(bot, DEVELOPER_CHAT_ID))


def test_startup_stops_before_polling_when_feedback_chat_check_fails(monkeypatch):
    cfg = BotConfig(telegram_bot_token="42:TEST", anthropic_api_key="key", model="claude-opus-5-5", stage_mode=False,
                    feedback_chat_id=DEVELOPER_CHAT_ID)
    dispatchers = []

    async def failing_check(bot, feedback_chat_id):
        raise RuntimeError("Feedback chat is unreachable")

    monkeypatch.setattr(main, "load_bot_config", lambda: cfg)
    monkeypatch.setattr(main.llm, "init", lambda config: None)
    monkeypatch.setattr(main, "_check_feedback_chat", failing_check)
    monkeypatch.setattr(main, "build_dispatcher", lambda *args: dispatchers.append(args))
    with pytest.raises(RuntimeError, match="unreachable"):
        asyncio.run(main.main())
    assert dispatchers == []


def test_startup_uses_bot_config_that_requires_feedback_chat(monkeypatch):
    monkeypatch.setenv("RAHUNOK_QR_BOT_TELEGRAM_TOKEN", "42:TEST")
    monkeypatch.setenv("RAHUNOK_QR_BOT_ANTHROPIC_API_KEY", "key")
    monkeypatch.delenv("RAHUNOK_QR_BOT_FEEDBACK_CHAT_ID", raising=False)
    with pytest.raises(RuntimeError, match="RAHUNOK_QR_BOT_FEEDBACK_CHAT_ID"):
        asyncio.run(main.main())


def test_reachable_feedback_chat_passes_startup_check():
    bot, session = make_bot()
    asyncio.run(main._check_feedback_chat(bot, DEVELOPER_CHAT_ID))
    [call] = session.calls(GetChat)
    assert call.chat_id == DEVELOPER_CHAT_ID
