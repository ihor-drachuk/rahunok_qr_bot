"""Offline Telegram doubles: a Bot API session that records calls, and builders for incoming updates."""

import asyncio
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from itertools import count
from typing import Any

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import CopyMessages, GetChat, SendMessage, TelegramMethod
from aiogram.types import CallbackQuery, Chat, ChatFullInfo, Message, MessageId, PhotoSize, Update, User, Video

BOT_ID = 42
BOT_USER = User(id=BOT_ID, is_bot=True, first_name="rahunok_qr_bot")
DEVELOPER_CHAT_ID = 777
USER = User(id=1001, is_bot=False, first_name="Олена", last_name="Коваль", username="olena")

_ids = count(1)


class RecordingSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod] = []
        self.sent_messages: list[Message] = []  # what each SendMessage returned, in order
        # Returns an exception to raise for a request, or None to answer it normally.
        self.fail: Callable[[TelegramMethod], Exception | None] = lambda method: None
        # Returns an event that the request waits for before it is answered, or None to answer it at once.
        self.hold: Callable[[TelegramMethod], asyncio.Event | None] = lambda method: None
        self.deleted_message_ids: set[int] = set()  # CopyMessages skips these, as Telegram does for deleted ones
        self.copy_results: list[list[int]] = []  # the new message IDs each CopyMessages returned, in order
        self._message_ids = count(5000)

    def calls(self, method_type: type[TelegramMethod]) -> list[Any]:
        return [request for request in self.requests if isinstance(request, method_type)]

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None) -> Any:
        self.requests.append(method)
        await asyncio.sleep(0)  # a real request always suspends, which lets other updates run in between
        if (gate := self.hold(method)) is not None:
            await gate.wait()
        if (error := self.fail(method)) is not None:
            raise error
        if isinstance(method, SendMessage):
            chat = Chat(id=method.chat_id, type="private")
            sent = Message(message_id=next(self._message_ids), date=datetime.now(UTC), chat=chat, from_user=BOT_USER,
                           text=method.text).as_(bot)
            self.sent_messages.append(sent)
            return sent
        if isinstance(method, CopyMessages):
            copies = [MessageId(message_id=next(self._message_ids)) for message_id in method.message_ids
                      if message_id not in self.deleted_message_ids]
            self.copy_results.append([copy.message_id for copy in copies])
            return copies
        if isinstance(method, GetChat):
            return ChatFullInfo(id=method.chat_id, type="private", accent_color_id=0, max_reaction_count=0,
                                accepted_gift_types={"unlimited_gifts": True, "limited_gifts": True,
                                                     "unique_gifts": True, "premium_subscription": True,
                                                     "gifts_from_channels": True})
        return True

    async def stream_content(self, url: str, headers: dict[str, Any] | None = None, timeout: int = 30,
                             chunk_size: int = 65536, raise_for_status: bool = True) -> AsyncGenerator[bytes, None]:
        raise NotImplementedError
        yield b""

    async def close(self) -> None:
        pass


def make_bot() -> tuple[Bot, RecordingSession]:
    session = RecordingSession()
    bot = Bot(f"{BOT_ID}:TEST", session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    return bot, session


def message_update(*, user: User = USER, chat_id: int | None = None, message_id: int | None = None,
                   text: str | None = None, caption: str | None = None, photo: bool = False, video: bool = False,
                   media_group_id: str | None = None, reply_to: Message | None = None, date: datetime | None = None) -> Update:
    message = Message(
        message_id=next(_ids) if message_id is None else message_id,
        date=datetime.now(UTC) if date is None else date,
        chat=Chat(id=user.id if chat_id is None else chat_id, type="private"),
        from_user=user,
        text=text,
        caption=caption,
        photo=[PhotoSize(file_id="photo", file_unique_id="photo", width=1, height=1)] if photo else None,
        video=Video(file_id="video", file_unique_id="video", width=1, height=1, duration=1) if video else None,
        media_group_id=media_group_id,
        reply_to_message=reply_to,
    )
    return Update(update_id=next(_ids), message=message)


def bot_message(chat_id: int, text: str, message_id: int | None = None) -> Message:
    return Message(message_id=next(_ids) if message_id is None else message_id, date=datetime.now(UTC),
                   chat=Chat(id=chat_id, type="private"), from_user=BOT_USER, text=text)


def callback_update(data: str, message: Message, user: User = USER) -> Update:
    callback = CallbackQuery(id=str(next(_ids)), from_user=user, chat_instance="ci", data=data, message=message)
    return Update(update_id=next(_ids), callback_query=callback)
