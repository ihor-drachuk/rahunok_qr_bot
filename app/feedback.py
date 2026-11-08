"""Feedback: /contact messages to the feedback chat, developer replies, failed-document reports."""

import hashlib
import hmac
import logging
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyParameters

from app import texts

COOLDOWN_SECONDS = 30.0
# We treat a message as feedback only when the user sends it within this many minutes of `/contact`,
# otherwise a forgotten wait would treat a payment document sent days later as feedback.
WAIT_TIMEOUT_MINUTES = 15
CONTACT_CANCEL_DATA = "contact:cancel"
_SIGNATURE_HEX_DIGITS = 12

logger = logging.getLogger(__name__)
router = Router()


class ReportAction(str, Enum):
    ASK = "ask"
    CONFIRM = "yes"
    DECLINE = "no"


class ReportCallback(CallbackData, prefix="rep"):
    action: ReportAction
    message_id: int  # the user's message that the error message is about
    is_text: bool


class Cooldown:
    def __init__(self) -> None:
        self._last_delivery: dict[int, float] = {}

    def try_take(self, user_id: int) -> bool:
        now = time.monotonic()
        last = self._last_delivery.get(user_id)
        if last is not None and now - last < COOLDOWN_SECONDS:
            return False
        self._last_delivery[user_id] = now
        return True

    def release(self, user_id: int) -> None:
        self._last_delivery.pop(user_id, None)


# A wait for the message, or album, that follows a bare /contact. Handlers take it out of `_waits` with a single
# `pop`, so only one message or button press can end it.
@dataclass(eq=False)
class Wait:
    key: tuple[int, int]  # (chat ID, user ID)
    started_at: datetime  # when the user sent the `/contact`
    prompt_message_id: int | None = None

    # `at` should be the message's send time when Telegram provides one,
    # because after a restart the bot handles messages sent hours apart back to back.
    def is_expired(self, at: datetime) -> bool:
        return at - self.started_at >= timedelta(minutes=WAIT_TIMEOUT_MINUTES)


class _NothingCopied(Exception):
    pass


_cooldown = Cooldown()
_waits: dict[tuple[int, int], Wait] = {}


def _reply_signature(bot: Bot, user_id: int) -> str:
    # The copied user content is also a bot message in the feedback chat, so we sign the header's user ID,
    # otherwise a user could write "ID: <someone else>" and get a developer reply sent to that person.
    digest = hmac.new(bot.token.encode(), f"feedback-reply:{user_id}".encode(), hashlib.sha256).hexdigest()
    return digest[:_SIGNATURE_HEX_DIGITS]


def report_keyboard(message_id: int, is_text: bool) -> InlineKeyboardMarkup:
    label = texts.REPORT_MESSAGE_LABEL if is_text else texts.REPORT_DOCUMENT_LABEL
    data = ReportCallback(action=ReportAction.ASK, message_id=message_id, is_text=is_text).pack()
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=data)]])


def _confirm_keyboard(message_id: int, is_text: bool) -> InlineKeyboardMarkup:
    def button(text: str, action: ReportAction) -> InlineKeyboardButton:
        data = ReportCallback(action=action, message_id=message_id, is_text=is_text).pack()
        return InlineKeyboardButton(text=text, callback_data=data)

    return InlineKeyboardMarkup(inline_keyboard=[[button(texts.REPORT_CONFIRM_LABEL, ReportAction.CONFIRM),
                                                  button(texts.REPORT_DECLINE_LABEL, ReportAction.DECLINE)]])


_CANCEL_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text=texts.CONTACT_CANCEL_LABEL, callback_data=CONTACT_CANCEL_DATA)]])


async def _send_to_developer(bot: Bot, feedback_chat_id: int, from_chat_id: int, message_ids: list[int], header: str) -> None:
    # We send the header after the content, so a header never arrives without its content.
    copied = await bot.copy_messages(feedback_chat_id, from_chat_id, message_ids)
    if not copied:  # `copyMessages` skips messages it cannot find, such as ones the user has deleted
        raise _NothingCopied()
    try:
        await bot.send_message(feedback_chat_id, header, reply_parameters=ReplyParameters(message_id=copied[0].message_id))
    except TelegramAPIError as e:
        # The content is already in the feedback chat, so we count it as delivered,
        # otherwise a retry would copy it again.
        logger.error("Failed to send feedback header: %s", type(e).__name__)


async def _deliver(message: Message, bot: Bot, feedback_chat_id: int, album: list[Message]) -> None:
    user = message.from_user
    if not _cooldown.try_take(user.id):
        await message.answer(texts.FEEDBACK_COOLDOWN)
        return
    header = texts.format_feedback_header(user, _reply_signature(bot, user.id))
    try:
        await _send_to_developer(bot, feedback_chat_id, message.chat.id, [item.message_id for item in album], header)
    except (TelegramAPIError, _NothingCopied) as e:
        _cooldown.release(user.id)
        logger.error("Failed to deliver feedback from user %s: %s", user.id, type(e).__name__)
        await message.answer(texts.FEEDBACK_FAILED)
        return
    logger.info("Feedback delivered from user %s", user.id)
    await message.answer(texts.FEEDBACK_SENT)


def _wait_key(message: Message) -> tuple[int, int]:
    return message.chat.id, message.from_user.id


async def _remove_buttons(bot: Bot, chat_id: int, message_id: int) -> None:
    with suppress(TelegramAPIError):  # a Cancel button left by a failed edit cancels the current wait when pressed
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)


async def _end_prompt(wait: Wait | None, bot: Bot) -> None:
    if wait is not None and wait.prompt_message_id is not None:
        await _remove_buttons(bot, wait.key[0], wait.prompt_message_id)


async def _send_prompt(wait: Wait, message: Message, bot: Bot) -> None:
    prompt = await message.answer(texts.contact_prompt(WAIT_TIMEOUT_MINUTES), reply_markup=_CANCEL_KEYBOARD)
    if _waits.get(wait.key) is wait:
        wait.prompt_message_id = prompt.message_id
    else:  # the wait ended while the prompt was on its way
        await _remove_buttons(bot, prompt.chat.id, prompt.message_id)


async def _is_developer_reply(message: Message, bot: Bot, feedback_chat_id: int) -> bool:
    replied = message.reply_to_message
    return (message.chat.id == feedback_chat_id and replied is not None and replied.from_user is not None
            and replied.from_user.id == bot.id)


@router.message(_is_developer_reply)
async def on_developer_reply(message: Message, album: list[Message], bot: Bot) -> None:
    address = texts.parse_feedback_address(message.reply_to_message.text or "")
    if address is None or not hmac.compare_digest(address[1], _reply_signature(bot, address[0])):
        await message.reply(texts.DEVELOPER_REPLY_HINT)
        return
    user_id = address[0]
    try:
        copied = await bot.copy_messages(user_id, message.chat.id, [item.message_id for item in album])
    except TelegramForbiddenError:
        await message.reply(texts.DEVELOPER_REPLY_BLOCKED)
        return
    except TelegramAPIError as e:
        logger.error("Failed to deliver developer reply to user %s: %s", user_id, type(e).__name__)
        await message.reply(texts.DEVELOPER_REPLY_FAILED)
        return
    if not copied:
        await message.reply(texts.DEVELOPER_REPLY_FAILED)
        return
    # The reply has already reached the user, so a failed intro must not be reported as a failed delivery.
    with suppress(TelegramAPIError):
        await bot.send_message(user_id, texts.DEVELOPER_REPLY_INTRO,
                               reply_parameters=ReplyParameters(message_id=copied[0].message_id))
    await message.reply(texts.DEVELOPER_REPLY_DELIVERED)


@router.message(Command("contact", "feedback"))
async def on_contact(message: Message, command: CommandObject, album: list[Message], bot: Bot, feedback_chat_id: int) -> None:
    previous = _waits.pop(_wait_key(message), None)
    # `Command` also matches media captions, so a captioned photo, file or album is feedback itself,
    # as is "/contact <text>".
    if command.args or message.text is None:
        await _end_prompt(previous, bot)
        await _deliver(message, bot, feedback_chat_id, album)
        return
    wait = _waits[_wait_key(message)] = Wait(key=_wait_key(message), started_at=message.date)
    await _end_prompt(previous, bot)
    await _send_prompt(wait, message, bot)


@router.message(Command("cancel"))
async def on_cancel(message: Message, bot: Bot) -> None:
    wait = _waits.pop(_wait_key(message), None)
    await _end_prompt(wait, bot)
    if wait is None or wait.is_expired(message.date):
        await message.answer(texts.CONTACT_NOTHING_TO_CANCEL)
        return
    await message.answer(texts.CONTACT_CANCELLED)


# This handler, last in this router, ends the wait for any message:
# a command, or a message sent `WAIT_TIMEOUT_MINUTES` or more after the `/contact`, goes to `handlers.router`,
# and anything else is the feedback.
@router.message()
async def on_message_while_waiting(message: Message, album: list[Message], bot: Bot, feedback_chat_id: int) -> None:
    wait = _waits.pop(_wait_key(message), None)
    if wait is None:
        raise SkipHandler()
    await _end_prompt(wait, bot)
    if wait.is_expired(message.date) or (message.text or message.caption or "").startswith("/"):
        raise SkipHandler()
    await _deliver(message, bot, feedback_chat_id, album)


async def answer_callback(callback: CallbackQuery, text: str | None = None) -> None:
    # A callback answer is cosmetic, and Telegram rejects one for an old press, for example from before a restart,
    # so we never let its failure stop the handler.
    with suppress(TelegramAPIError):
        await callback.answer(text)


@router.callback_query(F.data == CONTACT_CANCEL_DATA)
async def on_cancel_button(callback: CallbackQuery, bot: Bot) -> None:
    chat_id, pressed_message_id = callback.message.chat.id, callback.message.message_id
    wait = _waits.pop((chat_id, callback.from_user.id), None)
    await answer_callback(callback)
    await _end_prompt(wait, bot)
    if wait is not None and not wait.is_expired(datetime.now(UTC)):  # a button press carries no send time
        await bot.send_message(chat_id, texts.CONTACT_CANCELLED)
    if wait is None or wait.prompt_message_id != pressed_message_id:
        await _remove_buttons(bot, chat_id, pressed_message_id)


@router.callback_query(ReportCallback.filter())
async def on_report_button(callback: CallbackQuery, callback_data: ReportCallback, bot: Bot, feedback_chat_id: int) -> None:
    reply = callback.message
    if not isinstance(reply, Message):  # an `InaccessibleMessage` cannot be edited
        await answer_callback(callback, texts.MESSAGE_EXPIRED)
        return
    if callback_data.action is ReportAction.CONFIRM:
        await _confirm_report(callback, callback_data, reply, bot, feedback_chat_id)
        return
    await answer_callback(callback)
    if callback_data.action is ReportAction.ASK:
        markup = _confirm_keyboard(callback_data.message_id, callback_data.is_text)
    else:
        markup = report_keyboard(callback_data.message_id, callback_data.is_text)
    with suppress(TelegramBadRequest):  # a second tap made before the first one shows on screen edits nothing
        await reply.edit_reply_markup(reply_markup=markup)


async def _confirm_report(callback: CallbackQuery, callback_data: ReportCallback, reply: Message, bot: Bot,
                          feedback_chat_id: int) -> None:
    user = callback.from_user
    if not _cooldown.try_take(user.id):
        await answer_callback(callback, texts.REPORT_COOLDOWN)
        return
    error_line = (reply.text or "").split("\n", 1)[0]
    header = texts.format_report_header(user, _reply_signature(bot, user.id), error_line, callback_data.is_text)
    try:
        await _send_to_developer(bot, feedback_chat_id, reply.chat.id, [callback_data.message_id], header)
    except (TelegramAPIError, _NothingCopied) as e:
        _cooldown.release(user.id)
        logger.error("Failed to deliver report from user %s: %s", user.id, type(e).__name__)
        # A copy of a deleted message yields nothing, so we name that cause.
        await answer_callback(callback, texts.SOURCE_GONE if isinstance(e, _NothingCopied) else texts.REPORT_FAILED)
        return
    logger.info("Report delivered from user %s", user.id)
    await answer_callback(callback)
    # The report is already delivered, so we ignore a failed edit and accept that the buttons stay,
    # and Confirm can send the report again once the cooldown is over.
    with suppress(TelegramAPIError):
        await reply.edit_text(texts.format_report_sent(reply.html_text), reply_markup=None)
