import asyncio

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteWebhook, GetChat, SetMyCommands

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


def test_startup_keeps_updates_sent_while_bot_was_down(monkeypatch):
    cfg = BotConfig(telegram_bot_token="42:TEST", anthropic_api_key="key", model="claude-opus-5-5", stage_mode=False,
                    feedback_chat_id=DEVELOPER_CHAT_ID)
    bot, session = make_bot()
    webhook_deletes_before_polling = []

    class FakeDispatcher:
        async def start_polling(self, polling_bot):
            webhook_deletes_before_polling.append(len(session.calls(DeleteWebhook)))

    async def no_emoji(polling_bot):
        pass

    monkeypatch.setattr(main, "load_bot_config", lambda: cfg)
    monkeypatch.setattr(main.llm, "init", lambda config: None)
    monkeypatch.setattr(main, "Bot", lambda token, default: bot)
    monkeypatch.setattr(main, "build_dispatcher", lambda stage_mode, feedback_chat_id: FakeDispatcher())
    monkeypatch.setattr(main.emoji_pack, "resolve_custom_emoji", no_emoji)
    asyncio.run(main.main())

    [delete_webhook] = session.calls(DeleteWebhook)
    assert delete_webhook.drop_pending_updates is False
    assert webhook_deletes_before_polling == [1]


def test_command_menu_holds_only_help_and_contact():
    bot, session = make_bot()
    asyncio.run(main._set_commands(bot))

    [call] = session.calls(SetMyCommands)
    assert [command.command for command in call.commands] == ["help", "contact"]


def test_reachable_feedback_chat_passes_startup_check():
    bot, session = make_bot()
    asyncio.run(main._check_feedback_chat(bot, DEVELOPER_CHAT_ID))
    [call] = session.calls(GetChat)
    assert call.chat_id == DEVELOPER_CHAT_ID
