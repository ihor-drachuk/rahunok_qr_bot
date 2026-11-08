"""Telegram handlers: content routing, download, replies, error messaging, the payment purpose button."""

import asyncio
import html as html_lib
import io
import logging
import re
from contextlib import suppress

import aiohttp
import anthropic
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           InputMediaPhoto, LinkPreviewOptions, Message)

from app import card, feedback, pipeline, texts
from app.config import TELEGRAM_MAX_DOWNLOAD_BYTES
from app.llm import Source
from app.models import ExtractedRequisites
from app.pipeline import PipelineResult

# Telegram caps a photo caption at 1024, counting only the visible text (UTF-16 units): HTML tags and the
# URLs inside <a href> don't count, entities like &amp; count as the single character they render to.
TELEGRAM_PHOTO_CAPTION_LIMIT = 1024
NO_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)
PURPOSE_DATA = "purpose:generate"

_HTML_TAG = re.compile(r"<[^>]+>")


def _caption_len(html_text: str) -> int:
    visible = html_lib.unescape(_HTML_TAG.sub("", html_text))
    return len(visible.encode("utf-16-le")) // 2  # Telegram counts UTF-16 code units

logger = logging.getLogger(__name__)
router = Router()

# Cards with a press of the purpose button in progress. Presses run as concurrent tasks, so we check and add a key
# with no await in between, otherwise a double tap would run the model calls twice.
_purpose_in_progress: set[tuple[int, int]] = set()  # (chat ID, card message ID)


class _SourceUnavailable(Exception):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text  # the reply that tells the user why


@router.message(CommandStart())
@router.message(Command("help"))
async def on_start(message: Message) -> None:
    await message.answer(texts.HELP)


@router.message(F.media_group_id)
async def on_album(message: Message, album: list[Message], bot: Bot, stage_mode: bool) -> None:
    supported = [item for item in album if item.document or item.photo]
    if len(supported) < len(album):
        await message.answer(texts.ERR_UNSUPPORTED_TYPE)
    await asyncio.gather(*(on_content(item, bot, stage_mode) for item in supported))


@router.message(F.document | F.photo | F.text)
async def on_content(message: Message, bot: Bot, stage_mode: bool) -> None:
    try:
        source = await _load_source(message, bot)
    except _SourceUnavailable as error:
        await message.answer(error.text)
        return
    await _process_and_reply(message, source, stage_mode)


@router.message()
async def on_unsupported(message: Message) -> None:
    await message.answer(texts.ERR_UNSUPPORTED_TYPE)


async def _load_source(message: Message, bot: Bot) -> Source:
    if message.text is not None:
        return Source(kind="text", text=message.text)
    if message.document:
        document = message.document
        if document.mime_type == "application/pdf":
            kind, media_type = "pdf", None
        elif document.mime_type in ("image/png", "image/jpeg"):
            kind, media_type = "image", document.mime_type
        else:
            raise _SourceUnavailable(texts.ERR_UNSUPPORTED_TYPE)
        if document.file_size and document.file_size > TELEGRAM_MAX_DOWNLOAD_BYTES:
            raise _SourceUnavailable(texts.ERR_FILE_TOO_BIG)
        file_id = document.file_id
    elif message.photo:
        # `photo[-1]` is the largest resolution, and Telegram photos are always JPEG.
        kind, media_type, file_id = "image", "image/jpeg", message.photo[-1].file_id
    else:
        raise _SourceUnavailable(texts.ERR_UNSUPPORTED_TYPE)
    buffer = io.BytesIO()
    try:
        await bot.download(file_id, destination=buffer)
    # aiogram does not turn errors of the file download itself into `TelegramAPIError`.
    except (TelegramAPIError, aiohttp.ClientError, TimeoutError):
        logger.exception("Failed to download file %s", file_id)
        raise _SourceUnavailable(texts.ERR_TELEGRAM) from None
    return Source(kind=kind, data=buffer.getvalue(), media_type=media_type)


def _failure_reply(error: Exception) -> tuple[str, bool]:
    """The reply to processing that failed, and whether the source may be at fault. We offer the report button only
    when it may be, and not for errors of the services we call: rate limit, network, Anthropic API, Telegram."""
    if isinstance(error, anthropic.RateLimitError):
        return texts.ERR_RATE_LIMIT, False
    if isinstance(error, anthropic.APIConnectionError):
        return texts.ERR_NETWORK, False
    if isinstance(error, anthropic.APIStatusError):
        logger.error("Anthropic API error: status=%s request_id=%s", error.status_code, error.request_id)
        return texts.ERR_API, False
    if isinstance(error, TelegramAPIError):
        logger.error("Telegram error during processing: %s", type(error).__name__)
        return texts.ERR_TELEGRAM, False
    logger.error("Unexpected error during processing", exc_info=error)
    return texts.ERR_UNEXPECTED, True


async def _process_and_reply(message: Message, source: Source, stage_mode: bool) -> None:
    status: Message | None = None

    async def on_stage(text: str) -> None:
        # Status is cosmetic: a failed send/edit must not abort the processing itself.
        nonlocal status
        with suppress(TelegramAPIError):
            if status is None:
                status = await message.answer(text)
            else:
                await status.edit_text(text)

    report_button = feedback.report_keyboard(message.message_id, is_text=source.kind == "text")
    try:
        result = await pipeline.process(source, on_stage)
        if result.ok:
            await _send_card(message, result, stage_mode)
        else:
            await message.answer(texts.format_error(result.error, result.requisites), reply_markup=report_button)
    except Exception as error:
        reply, source_may_be_at_fault = _failure_reply(error)
        await message.answer(reply, reply_markup=report_button if source_may_be_at_fault else None)
    finally:
        # Deleted last so the status message stays visible until the reply has been sent.
        if status is not None:
            with suppress(TelegramAPIError):
                await status.delete()


def _purpose_keyboard(label: str) -> InlineKeyboardMarkup:
    # One purpose button with `label`. Every label gets `PURPOSE_DATA`, otherwise a card left with the
    # `PURPOSE_IN_PROGRESS_LABEL` button by a restart or a failed label edit would have a button that does nothing.
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=PURPOSE_DATA)]])


def _card_photo(result: PipelineResult, stage_mode: bool) -> BufferedInputFile:
    return BufferedInputFile(card.build_card(result.qr.image, result.card, stage_mode), filename="payment_qr.png")


async def _send_card(source_message: Message, result: PipelineResult, stage_mode: bool) -> None:
    # We send the card as a reply to its source, because a press of its purpose button finds the source only in
    # `reply_to_message`. We allow sending without the source, so that a deleted source does not fail the card.
    reply_to = source_message.as_reply_parameters(allow_sending_without_reply=True)
    text = texts.format_success(result.requisites, result.warnings, result.qr)
    photo = _card_photo(result, stage_mode)
    if _caption_len(text) <= TELEGRAM_PHOTO_CAPTION_LIMIT:
        # Common case: one message — the card with the text as its caption.
        requisites = result.requisites
        offers_purpose = not requisites.payment_purpose and requisites.has_invoice_reference
        markup = _purpose_keyboard(texts.PURPOSE_LABEL) if offers_purpose else None
        await source_message.answer_photo(photo, caption=text, reply_parameters=reply_to, reply_markup=markup,
                                          link_preview_options=NO_LINK_PREVIEW)
    else:
        # Fallback when the text overflows the caption limit: image, then text separately.
        await source_message.answer_photo(photo, reply_parameters=reply_to)
        await source_message.answer(text, reply_parameters=reply_to, link_preview_options=NO_LINK_PREVIEW)


async def _set_purpose_button(card_message: Message, label: str) -> None:
    with suppress(TelegramAPIError):  # a failed label change must not stop the press
        await card_message.edit_reply_markup(reply_markup=_purpose_keyboard(label))


def _shows_purpose_button(card_message: Message) -> bool:
    markup = card_message.reply_markup
    rows = [] if markup is None else markup.inline_keyboard
    return any(button.callback_data == PURPOSE_DATA for row in rows for button in row)


@router.callback_query(F.data == PURPOSE_DATA)
async def on_purpose_button(callback: CallbackQuery, bot: Bot, stage_mode: bool) -> None:
    card_message = callback.message
    # A press can arrive after the card was replaced, from a client that still shows the old button. We check the
    # card's current button, otherwise such a press would make the purpose again.
    if not isinstance(card_message, Message) or not _shows_purpose_button(card_message):
        await feedback.answer_callback(callback, texts.MESSAGE_EXPIRED)
        return
    requisites = texts.parse_requisites(card_message.caption or "", card_message.caption_entities or [])
    if requisites is None:
        await feedback.answer_callback(callback, texts.MESSAGE_EXPIRED)
        return
    source_message = card_message.reply_to_message
    if source_message is None:
        await feedback.answer_callback(callback, texts.SOURCE_GONE)
        return
    key = (card_message.chat.id, card_message.message_id)
    if key in _purpose_in_progress:
        await feedback.answer_callback(callback, texts.PURPOSE_ALREADY_RUNNING)
        return
    _purpose_in_progress.add(key)
    try:
        await _add_purpose(callback, card_message, source_message, requisites, bot, stage_mode)
    finally:
        _purpose_in_progress.discard(key)


async def _add_purpose(callback: CallbackQuery, card_message: Message, source_message: Message,
                       requisites: ExtractedRequisites, bot: Bot, stage_mode: bool) -> None:
    # Telegram rejects `answerCallbackQuery` soon after the press, and the download and the model calls can take
    # longer, so we answer at once and report a failed generation in a reply to the card.
    await feedback.answer_callback(callback)
    await _set_purpose_button(card_message, texts.PURPOSE_IN_PROGRESS_LABEL)
    try:
        source = await _load_source(source_message, bot)
        result = await pipeline.add_purpose(source, requisites)
        if result.ok:
            await _replace_card(card_message, source_message, result, stage_mode)
            return
        reply, source_may_be_at_fault = result.error, True
    except _SourceUnavailable as error:
        reply, source_may_be_at_fault = error.text, False
    except Exception as error:
        reply, source_may_be_at_fault = _failure_reply(error)
    await _set_purpose_button(card_message, texts.PURPOSE_LABEL)
    report_button = feedback.report_keyboard(source_message.message_id, is_text=source_message.text is not None)
    await card_message.reply(reply, reply_markup=report_button if source_may_be_at_fault else None)


async def _replace_card(card_message: Message, source_message: Message, result: PipelineResult, stage_mode: bool) -> None:
    text = texts.format_success(result.requisites, result.warnings, result.qr)
    if _caption_len(text) <= TELEGRAM_PHOTO_CAPTION_LIMIT:
        try:
            media = InputMediaPhoto(media=_card_photo(result, stage_mode), caption=text)
            await card_message.edit_media(media, reply_markup=None)
            return
        except TelegramAPIError as error:
            logger.error("Failed to replace card: %s", type(error).__name__)
    await _send_card(source_message, result, stage_mode)
    # If removing the old card's button fails, a press of that button makes one more card, which we accept.
    with suppress(TelegramAPIError):
        await card_message.edit_reply_markup(reply_markup=None)
