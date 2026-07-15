import asyncio
from unittest.mock import AsyncMock

from PIL import Image

from app import card, handlers, pipeline, texts
from app.card import CardText
from app.llm import Source
from app.models import ExtractedRequisites
from app.pipeline import PipelineResult
from app.qr import QrResult

VALID_IBAN = "UA693000010000000012345678901"
TEXT_SOURCE = Source(kind="text", text="реквізити")

QR = QrResult(image=Image.new("RGBA", (4, 4)), url="https://bank.gov.ua/qr/x", truncated_purpose=False)
CARD = CardText(subtitle="s", call_to_action="c", recipient="ТОВ", amount="1.00 грн")


def make_message() -> AsyncMock:
    message = AsyncMock()
    message.answer.return_value = AsyncMock()  # the temporary status message, deleted in finally
    return message


def run_with_result(result: PipelineResult, monkeypatch,
                    stages: tuple[str, ...] = (texts.STATUS_SEARCHING,)) -> AsyncMock:
    async def fake_process(source, on_stage=None):
        for stage in stages:
            await on_stage(stage)
        return result

    monkeypatch.setattr(pipeline, "process", fake_process)
    monkeypatch.setattr(card, "build_card", lambda image, text: b"\x89PNGfake")
    message = make_message()
    asyncio.run(handlers._process_and_reply(message, TEXT_SOURCE))
    return message


def test_short_reply_is_photo_with_caption_in_one_message(monkeypatch):
    requisites = ExtractedRequisites(iban=VALID_IBAN)
    result = PipelineResult(ok=True, qr=QR, card=CARD, requisites=requisites)
    message = run_with_result(result, monkeypatch)

    message.answer_photo.assert_awaited_once()
    caption = message.answer_photo.await_args.kwargs["caption"]
    assert caption == texts.format_success(requisites, [], QR)
    assert message.answer_photo.await_args.kwargs["link_preview_options"].is_disabled
    assert message.answer.await_count == 1  # only the status message; no separate text
    assert message.answer.await_args_list[0].args == (texts.STATUS_SEARCHING,)


def test_long_reply_falls_back_to_photo_then_text(monkeypatch):
    requisites = ExtractedRequisites(iban=VALID_IBAN, payment_purpose="дуже довге призначення " * 100)
    result = PipelineResult(ok=True, qr=QR, card=CARD, requisites=requisites)
    message = run_with_result(result, monkeypatch)

    message.answer_photo.assert_awaited_once()
    assert "caption" not in message.answer_photo.await_args.kwargs  # too long for a caption
    assert message.answer.await_count == 2  # status message + the requisites text
    assert message.answer.await_args_list[1].args == (texts.format_success(requisites, [], QR),)
    assert message.answer.await_args_list[1].kwargs["link_preview_options"].is_disabled


def test_long_pay_link_urls_do_not_force_the_fallback(monkeypatch):
    # Long base64 pay-link URLs inflate the raw HTML but not the visible caption length.
    long_url = "https://bank.gov.ua/qr/" + "A" * 500
    qr = QrResult(image=Image.new("RGBA", (4, 4)), url=long_url, truncated_purpose=False)
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


def test_stage_updates_edit_the_single_status_message(monkeypatch):
    result = PipelineResult(ok=True, qr=QR, card=CARD, requisites=ExtractedRequisites(iban=VALID_IBAN))
    stages = (texts.STATUS_SEARCHING, texts.STATUS_EXTRACTING, texts.STATUS_VALIDATING)
    message = run_with_result(result, monkeypatch, stages=stages)

    assert message.answer.await_count == 1
    status = message.answer.return_value
    assert [call.args for call in status.edit_text.await_args_list] == [(texts.STATUS_EXTRACTING,),
                                                                        (texts.STATUS_VALIDATING,)]
    status.delete.assert_awaited_once()
    message.answer_photo.assert_awaited_once()


def test_failed_status_edit_does_not_abort_processing(monkeypatch):
    from aiogram.exceptions import TelegramAPIError

    result = PipelineResult(ok=True, qr=QR, card=CARD, requisites=ExtractedRequisites(iban=VALID_IBAN))
    message = make_message()
    message.answer.return_value.edit_text.side_effect = TelegramAPIError(method=None, message="edit failed")

    async def fake_process(source, on_stage=None):
        await on_stage(texts.STATUS_SEARCHING)
        await on_stage(texts.STATUS_EXTRACTING)
        return result

    monkeypatch.setattr(pipeline, "process", fake_process)
    monkeypatch.setattr(card, "build_card", lambda image, text: b"\x89PNGfake")
    asyncio.run(handlers._process_and_reply(message, TEXT_SOURCE))

    message.answer_photo.assert_awaited_once()
    message.answer.return_value.delete.assert_awaited_once()


def test_failed_status_send_does_not_abort_processing_and_skips_delete(monkeypatch):
    from aiogram.exceptions import TelegramAPIError

    result = PipelineResult(ok=True, qr=QR, card=CARD, requisites=ExtractedRequisites(iban=VALID_IBAN))
    message = make_message()
    message.answer.side_effect = TelegramAPIError(method=None, message="send failed")

    async def fake_process(source, on_stage=None):
        await on_stage(texts.STATUS_SEARCHING)
        return result

    monkeypatch.setattr(pipeline, "process", fake_process)
    monkeypatch.setattr(card, "build_card", lambda image, text: b"\x89PNGfake")
    asyncio.run(handlers._process_and_reply(message, TEXT_SOURCE))

    message.answer_photo.assert_awaited_once()
    message.answer.return_value.delete.assert_not_awaited()  # no status message was ever created


def test_real_pipeline_drives_status_through_actual_stage_sequence(monkeypatch):
    # End-to-end through the real handler->pipeline callback seam; only the llm layer is stubbed.
    from tests.test_pipeline import GOOD_REQUISITES, LlmStub, verdict

    LlmStub(monkeypatch, GOOD_REQUISITES, [verdict(True)])
    monkeypatch.setattr(card, "build_card", lambda image, text: b"\x89PNGfake")
    message = make_message()
    asyncio.run(handlers._process_and_reply(message, TEXT_SOURCE))

    assert message.answer.await_count == 1
    assert message.answer.await_args_list[0].args == (texts.STATUS_SEARCHING,)
    status = message.answer.return_value
    assert [call.args for call in status.edit_text.await_args_list] == [(texts.STATUS_EXTRACTING,),
                                                                        (texts.STATUS_VALIDATING,)]
    status.delete.assert_awaited_once()
    message.answer_photo.assert_awaited_once()


def test_anthropic_error_reported_and_status_deleted(monkeypatch):
    import anthropic
    import httpx

    async def failing_process(source, on_stage=None):
        await on_stage(texts.STATUS_SEARCHING)
        raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))

    monkeypatch.setattr(pipeline, "process", failing_process)
    message = make_message()
    asyncio.run(handlers._process_and_reply(message, TEXT_SOURCE))

    assert message.answer.await_count == 2  # status message + network error text
    assert message.answer.await_args_list[1].args == (texts.ERR_NETWORK,)
    message.answer.return_value.delete.assert_awaited_once()
    message.answer_photo.assert_not_awaited()
