"""Telegram handlers: content routing, download, replies, error messaging."""

import html as html_lib
import io
import logging
import re
from contextlib import suppress

import anthropic
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, LinkPreviewOptions, Message

from app import card, pipeline, texts
from app.config import TELEGRAM_MAX_DOWNLOAD_BYTES
from app.llm import Source

# Telegram caps a photo caption at 1024, counting only the visible text (UTF-16 units): HTML tags and the
# URLs inside <a href> don't count, entities like &amp; count as the single character they render to.
TELEGRAM_PHOTO_CAPTION_LIMIT = 1024
NO_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)

_HTML_TAG = re.compile(r"<[^>]+>")


def _caption_len(html_text: str) -> int:
    visible = html_lib.unescape(_HTML_TAG.sub("", html_text))
    return len(visible.encode("utf-16-le")) // 2  # Telegram counts UTF-16 code units

logger = logging.getLogger(__name__)
router = Router()


@router.message(CommandStart())
@router.message(Command("help"))
async def on_start(message: Message) -> None:
    await message.answer(texts.HELP)


@router.message(F.document)
async def on_document(message: Message, bot: Bot) -> None:
    document = message.document
    if document.mime_type == "application/pdf":
        kind, media_type = "pdf", None
    elif document.mime_type in ("image/png", "image/jpeg"):
        kind, media_type = "image", document.mime_type
    else:
        await message.answer(texts.ERR_UNSUPPORTED_TYPE)
        return
    if document.file_size and document.file_size > TELEGRAM_MAX_DOWNLOAD_BYTES:
        await message.answer(texts.ERR_FILE_TOO_BIG)
        return
    await _handle_media(message, bot, document.file_id, kind, media_type)


@router.message(F.photo)
async def on_photo(message: Message, bot: Bot) -> None:
    photo = message.photo[-1]  # largest resolution; Telegram photos are always JPEG
    await _handle_media(message, bot, photo.file_id, "image", "image/jpeg")


@router.message(F.text)
async def on_text(message: Message) -> None:
    await _process_and_reply(message, Source(kind="text", text=message.text))


@router.message()
async def on_unsupported(message: Message) -> None:
    await message.answer(texts.ERR_UNSUPPORTED_TYPE)


async def _handle_media(message: Message, bot: Bot, file_id: str, kind: str, media_type: str | None) -> None:
    buffer = io.BytesIO()
    try:
        await bot.download(file_id, destination=buffer)
    except TelegramAPIError:
        logger.exception("Failed to download file %s", file_id)
        await message.answer(texts.ERR_TELEGRAM)
        return
    await _process_and_reply(message, Source(kind=kind, data=buffer.getvalue(), media_type=media_type))


async def _process_and_reply(message: Message, source: Source) -> None:
    status = await message.answer(texts.PROCESSING)
    try:
        result = await pipeline.process(source)
        if not result.ok:
            await message.answer(texts.format_error(result.error, result.requisites))
            return
        text = texts.format_success(result.requisites, result.warnings, result.qr)
        card_png = card.build_card(result.qr.image, result.card)
        photo = BufferedInputFile(card_png, filename="payment_qr.png")
        if _caption_len(text) <= TELEGRAM_PHOTO_CAPTION_LIMIT:
            # Common case: one message — the card with the text as its caption.
            await message.answer_photo(photo, caption=text, link_preview_options=NO_LINK_PREVIEW)
        else:
            # Fallback when the text overflows the caption limit: image, then text separately.
            await message.answer_photo(photo)
            await message.answer(text, link_preview_options=NO_LINK_PREVIEW)
    except anthropic.RateLimitError:
        await message.answer(texts.ERR_RATE_LIMIT)
    except anthropic.APIConnectionError:
        await message.answer(texts.ERR_NETWORK)
    except anthropic.APIStatusError as e:
        logger.error("Anthropic API error: status=%s request_id=%s", e.status_code, e.request_id)
        await message.answer(texts.ERR_API)
    except TelegramAPIError as e:
        logger.error("Telegram error during processing: %s", type(e).__name__)
        await message.answer(texts.ERR_TELEGRAM)
    except Exception:
        logger.exception("Unexpected error during processing")
        await message.answer(texts.ERR_UNEXPECTED)
    finally:
        # Deleted last so the status message stays visible until the reply has been sent.
        with suppress(TelegramAPIError):
            await status.delete()
