import asyncio
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
from aiogram.methods import GetFile, SendMessage
from aiogram.types import ReplyParameters
from nbu_payment_qr import QrResult
from PIL import Image

from app import card, feedback, handlers, pipeline, texts
from app.card import CardText
from app.config import TELEGRAM_MAX_DOWNLOAD_BYTES
from app.llm import Source
from app.models import ExtractedRequisites
from app.pipeline import PipelineResult
from tests.telegram_fakes import make_bot, message_update

VALID_IBAN = "UA693000010000000012345678901"
TEXT_SOURCE = Source(kind="text", text="реквізити")

QR = QrResult(image=Image.new("RGBA", (4, 4)), url="https://bank.gov.ua/qr/x", payload="", truncated_purpose=False)
CARD = CardText(subtitle="s", call_to_action="c", recipient="ТОВ", amount="1.00 грн")


SOURCE_MESSAGE_ID = 7
REPLY_TO_SOURCE = ReplyParameters(message_id=SOURCE_MESSAGE_ID, allow_sending_without_reply=True)


def make_message() -> AsyncMock:
    message = AsyncMock()
    message.message_id = SOURCE_MESSAGE_ID
    message.answer.return_value = AsyncMock()  # the temporary status message, deleted in finally
    message.as_reply_parameters = Mock(return_value=REPLY_TO_SOURCE)
    return message


def run_with_result(result: PipelineResult, monkeypatch,
                    stages: tuple[str, ...] | None = None,
                    message: AsyncMock | None = None) -> AsyncMock:
    if stages is None:
        stages = (texts.status_searching(),)
    async def fake_process(source, on_stage=None):
        for stage in stages:
            await on_stage(stage)
        return result

    monkeypatch.setattr(pipeline, "process", fake_process)
    monkeypatch.setattr(card, "build_card", lambda image, text, stage_mode: b"\x89PNGfake")
    if message is None:
        message = make_message()
    asyncio.run(handlers._process_and_reply(message, TEXT_SOURCE, False))
    return message


def test_short_reply_is_photo_with_caption_in_one_message(monkeypatch):
    requisites = ExtractedRequisites(iban=VALID_IBAN)
    result = PipelineResult(ok=True, qr=QR, card=CARD, requisites=requisites)
    message = run_with_result(result, monkeypatch)

    message.answer_photo.assert_awaited_once()
    caption = message.answer_photo.await_args.kwargs["caption"]
    assert caption == texts.format_success(requisites, [], QR)
    assert message.answer_photo.await_args.kwargs["link_preview_options"].is_disabled
    assert message.answer_photo.await_args.kwargs["reply_parameters"] == REPLY_TO_SOURCE
    message.as_reply_parameters.assert_called_once_with(allow_sending_without_reply=True)
    assert message.answer.await_count == 1  # only the status message; no separate text
    assert message.answer.await_args_list[0].args == (texts.status_searching(),)


def test_long_reply_falls_back_to_photo_then_text(monkeypatch):
    requisites = ExtractedRequisites(iban=VALID_IBAN, payment_purpose="дуже довге призначення " * 100)
    result = PipelineResult(ok=True, qr=QR, card=CARD, requisites=requisites)
    message = run_with_result(result, monkeypatch)

    message.answer_photo.assert_awaited_once()
    assert "caption" not in message.answer_photo.await_args.kwargs  # too long for a caption
    assert message.answer_photo.await_args.kwargs["reply_parameters"] == REPLY_TO_SOURCE
    assert message.answer.await_count == 2  # status message + the requisites text
    assert message.answer.await_args_list[1].args == (texts.format_success(requisites, [], QR),)
    assert message.answer.await_args_list[1].kwargs["link_preview_options"].is_disabled
    assert message.answer.await_args_list[1].kwargs["reply_parameters"] == REPLY_TO_SOURCE


@pytest.mark.parametrize("purpose,has_reference,offered", [(None, True, True), (None, False, False), ("Оплата за товар", True, False)])
def test_card_offers_purpose_only_without_purpose_and_with_invoice_reference(monkeypatch, purpose, has_reference, offered):
    requisites = ExtractedRequisites(iban=VALID_IBAN, payment_purpose=purpose, has_invoice_reference=has_reference)
    message = run_with_result(PipelineResult(ok=True, qr=QR, card=CARD, requisites=requisites), monkeypatch)

    markup = message.answer_photo.await_args.kwargs["reply_markup"]
    if offered:
        [[button]] = markup.inline_keyboard
        assert (button.text, button.callback_data) == (texts.PURPOSE_LABEL, handlers.PURPOSE_DATA)
    else:
        assert markup is None


def test_overflowing_card_without_purpose_offers_no_button(monkeypatch):
    requisites = ExtractedRequisites(iban=VALID_IBAN, recipient_name="ТОВ " + "Д" * 1000, has_invoice_reference=True)
    message = run_with_result(PipelineResult(ok=True, qr=QR, card=CARD, requisites=requisites), monkeypatch)

    assert "reply_markup" not in message.answer_photo.await_args.kwargs
    assert "reply_markup" not in message.answer.await_args_list[1].kwargs


def test_long_pay_link_urls_do_not_force_the_fallback(monkeypatch):
    # Long base64 pay-link URLs inflate the raw HTML but not the visible caption length.
    long_url = "https://bank.gov.ua/qr/" + "A" * 500
    qr = QrResult(image=Image.new("RGBA", (4, 4)), url=long_url, payload="", truncated_purpose=False)
    requisites = ExtractedRequisites(iban=VALID_IBAN)
    result = PipelineResult(ok=True, qr=qr, card=CARD, requisites=requisites)
    message = run_with_result(result, monkeypatch)

    message.answer_photo.assert_awaited_once()
    assert "caption" in message.answer_photo.await_args.kwargs  # stays a single message
    assert message.answer.await_count == 1


def test_pipeline_failure_sends_error_and_no_photo(monkeypatch):
    requisites = ExtractedRequisites(recipient_name="ТОВ")
    result = PipelineResult(ok=False, requisites=requisites, error=texts.ERR_NO_IBAN)
    message = run_with_result(result, monkeypatch)

    message.answer_photo.assert_not_awaited()
    assert message.answer.await_count == 2  # status message + error text
    assert message.answer.await_args_list[1].args == (texts.format_error(texts.ERR_NO_IBAN, requisites),)
    report_button = message.answer.await_args_list[1].kwargs["reply_markup"]
    assert report_button == feedback.report_keyboard(SOURCE_MESSAGE_ID, is_text=True)


def test_pipeline_failure_on_document_gets_document_report_button(monkeypatch):
    result = PipelineResult(ok=False, requisites=None, error=texts.ERR_NOT_PAYMENT)
    message = make_message()

    async def fake_process(source, on_stage=None):
        return result

    monkeypatch.setattr(pipeline, "process", fake_process)
    asyncio.run(handlers._process_and_reply(message, Source(kind="pdf", data=b"%PDF"), False))

    report_button = message.answer.await_args_list[-1].kwargs["reply_markup"]
    assert report_button == feedback.report_keyboard(SOURCE_MESSAGE_ID, is_text=False)


def test_stage_updates_edit_the_single_status_message(monkeypatch):
    result = PipelineResult(ok=True, qr=QR, card=CARD, requisites=ExtractedRequisites(iban=VALID_IBAN))
    stages = (texts.status_searching(), texts.status_extracting(), texts.status_validating())
    message = run_with_result(result, monkeypatch, stages=stages)

    assert message.answer.await_count == 1
    status = message.answer.return_value
    assert [call.args for call in status.edit_text.await_args_list] == [(texts.status_extracting(),),
                                                                        (texts.status_validating(),)]
    status.delete.assert_awaited_once()
    message.answer_photo.assert_awaited_once()


def test_failed_status_edit_does_not_abort_processing(monkeypatch):
    from aiogram.exceptions import TelegramAPIError

    result = PipelineResult(ok=True, qr=QR, card=CARD, requisites=ExtractedRequisites(iban=VALID_IBAN))
    message = make_message()
    message.answer.return_value.edit_text.side_effect = TelegramAPIError(method=None, message="edit failed")

    async def fake_process(source, on_stage=None):
        await on_stage(texts.status_searching())
        await on_stage(texts.status_extracting())
        return result

    monkeypatch.setattr(pipeline, "process", fake_process)
    monkeypatch.setattr(card, "build_card", lambda image, text, stage_mode: b"\x89PNGfake")
    asyncio.run(handlers._process_and_reply(message, TEXT_SOURCE, False))

    message.answer_photo.assert_awaited_once()
    message.answer.return_value.delete.assert_awaited_once()


def test_failed_status_send_does_not_abort_processing_and_skips_delete(monkeypatch):
    from aiogram.exceptions import TelegramAPIError

    result = PipelineResult(ok=True, qr=QR, card=CARD, requisites=ExtractedRequisites(iban=VALID_IBAN))
    message = make_message()
    message.answer.side_effect = TelegramAPIError(method=None, message="send failed")

    async def fake_process(source, on_stage=None):
        await on_stage(texts.status_searching())
        return result

    monkeypatch.setattr(pipeline, "process", fake_process)
    monkeypatch.setattr(card, "build_card", lambda image, text, stage_mode: b"\x89PNGfake")
    asyncio.run(handlers._process_and_reply(message, TEXT_SOURCE, False))

    message.answer_photo.assert_awaited_once()
    message.answer.return_value.delete.assert_not_awaited()  # no status message was ever created


def test_status_deleted_even_when_processing_raises(monkeypatch):
    message = make_message()

    async def failing_process(source, on_stage=None):
        await on_stage(texts.status_searching())
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "process", failing_process)
    asyncio.run(handlers._process_and_reply(message, TEXT_SOURCE, False))

    assert message.answer.await_args_list[1].args == (texts.ERR_UNEXPECTED,)
    assert message.answer.await_args_list[1].kwargs["reply_markup"] == feedback.report_keyboard(SOURCE_MESSAGE_ID, True)
    message.answer.return_value.delete.assert_awaited_once()


def test_stage_mode_is_forwarded_to_card_builder(monkeypatch):
    result = PipelineResult(ok=True, qr=QR, card=CARD, requisites=ExtractedRequisites(iban=VALID_IBAN))
    captured = []

    async def fake_process(source, on_stage=None):
        return result

    def fake_build_card(image, text, stage_mode):
        captured.append(stage_mode)
        return b"\x89PNGfake"

    monkeypatch.setattr(pipeline, "process", fake_process)
    monkeypatch.setattr(card, "build_card", fake_build_card)
    message = make_message()
    asyncio.run(handlers._process_and_reply(message, TEXT_SOURCE, True))
    assert captured == [True]


def test_dispatcher_injects_stage_mode_into_handlers(dispatcher, monkeypatch):
    # The conftest dispatcher is built with stage_mode=True.
    captured = []

    async def fake_process_and_reply(message, source, stage_mode):
        captured.append(stage_mode)

    monkeypatch.setattr(handlers, "_process_and_reply", fake_process_and_reply)
    bot, _ = make_bot()
    asyncio.run(dispatcher.feed_update(bot, message_update(text="реквізити")))
    assert captured == [True]


@pytest.fixture
def processed(monkeypatch):
    calls = []

    async def fake_process_and_reply(message, source, stage_mode):
        calls.append(source)

    monkeypatch.setattr(handlers, "_process_and_reply", fake_process_and_reply)
    return calls


@pytest.mark.parametrize("download_error", [aiohttp.ClientPayloadError("connection reset"), TimeoutError()])
def test_photo_whose_download_fails_is_answered_once(dispatcher, processed, download_error):
    bot, session = make_bot()
    session.download_error = download_error
    asyncio.run(dispatcher.feed_update(bot, message_update(photo=True)))

    assert [call.text for call in session.calls(SendMessage)] == [texts.ERR_TELEGRAM]
    assert processed == []


@pytest.mark.parametrize("mime,size,reply", [("application/pdf", TELEGRAM_MAX_DOWNLOAD_BYTES + 1, texts.ERR_FILE_TOO_BIG),
                                             ("application/zip", 100, texts.ERR_UNSUPPORTED_TYPE)])
def test_unusable_document_is_answered_without_download(dispatcher, processed, mime, size, reply):
    bot, session = make_bot()
    asyncio.run(dispatcher.feed_update(bot, message_update(document_mime=mime, document_size=size)))

    assert [call.text for call in session.calls(SendMessage)] == [reply]
    assert session.calls(GetFile) == []
    assert processed == []


def test_document_is_downloaded_and_processed(dispatcher, processed):
    bot, session = make_bot()
    asyncio.run(dispatcher.feed_update(bot, message_update(document_mime="application/pdf")))

    assert processed == [Source(kind="pdf", data=session.file_content)]


def test_real_pipeline_drives_status_through_actual_stage_sequence(monkeypatch):
    # End-to-end through the real handler->pipeline callback seam; only the llm layer is stubbed.
    from tests.test_pipeline import GOOD_REQUISITES, LlmStub, verdict

    LlmStub(monkeypatch, GOOD_REQUISITES, [verdict(True)])
    monkeypatch.setattr(card, "build_card", lambda image, text, stage_mode: b"\x89PNGfake")
    message = make_message()
    asyncio.run(handlers._process_and_reply(message, TEXT_SOURCE, False))

    assert message.answer.await_count == 1
    assert message.answer.await_args_list[0].args == (texts.status_searching(),)
    status = message.answer.return_value
    assert [call.args for call in status.edit_text.await_args_list] == [(texts.status_extracting(),),
                                                                        (texts.status_validating(),)]
    status.delete.assert_awaited_once()
    message.answer_photo.assert_awaited_once()


def test_anthropic_error_reported_and_status_deleted(monkeypatch):
    import anthropic
    import httpx

    async def failing_process(source, on_stage=None):
        await on_stage(texts.status_searching())
        raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))

    monkeypatch.setattr(pipeline, "process", failing_process)
    message = make_message()
    asyncio.run(handlers._process_and_reply(message, TEXT_SOURCE, False))

    assert message.answer.await_count == 2  # status message + network error text
    assert message.answer.await_args_list[1].args == (texts.ERR_NETWORK,)
    assert message.answer.await_args_list[1].kwargs.get("reply_markup") is None
    message.answer.return_value.delete.assert_awaited_once()
    message.answer_photo.assert_not_awaited()
