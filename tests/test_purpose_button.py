import asyncio

import aiohttp
import anthropic
import httpx
import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageMedia, EditMessageReplyMarkup, GetFile, SendMessage, SendPhoto
from aiogram.client.default import Default
from aiogram.types import Chat, InaccessibleMessage

from app import card, feedback, handlers, llm, pipeline, texts
from app.llm import Source
from tests.telegram_fakes import USER, callback_update, card_message, make_bot, message_update
from tests.test_handlers import QR
from tests.test_pipeline import CARD_REQUISITES, GENERATED_PURPOSE, PURPOSE, PurposeStub, purpose_verdict

SOURCE_ID = 300
CARD_ID = 301


@pytest.fixture(autouse=True)
def fake_card(monkeypatch):
    monkeypatch.setattr(card, "build_card", lambda image, text, stage_mode: b"\x89PNGfake")


def text_source():
    return message_update(message_id=SOURCE_ID, text="Рахунок № 00007 від 12.10.2026").message


PURPOSE_BUTTON = handlers._purpose_keyboard(texts.PURPOSE_LABEL)


def card_caption(requisites=CARD_REQUISITES) -> str:
    return texts.format_success(requisites, [texts.WARN_NO_PURPOSE], QR)


def card_for(source, reply_markup=PURPOSE_BUTTON):
    return card_message(card_caption(), reply_to=source, reply_markup=reply_markup, message_id=CARD_ID)


def press(dispatcher, bot, message):
    return dispatcher.feed_update(bot, callback_update(handlers.PURPOSE_DATA, message))


def answers(session) -> list[str | None]:
    return [call.text for call in session.calls(AnswerCallbackQuery)]


def button_labels(session) -> list[str | None]:
    return [call.reply_markup.inline_keyboard[0][0].text if call.reply_markup else None
            for call in session.calls(EditMessageReplyMarkup)]


def test_press_replaces_card_with_generated_purpose(dispatcher, monkeypatch):
    bot, session = make_bot()
    stub = PurposeStub(monkeypatch, [PURPOSE], [purpose_verdict()])
    asyncio.run(press(dispatcher, bot, card_for(text_source())))

    [edit] = session.calls(EditMessageMedia)
    assert (edit.chat_id, edit.message_id, edit.reply_markup) == (USER.id, CARD_ID, None)
    assert GENERATED_PURPOSE in edit.media.caption
    assert texts.WARN_NO_PURPOSE not in edit.media.caption
    assert edit.media.parse_mode == Default("parse_mode")  # the bot's HTML, which keeps the caption readable back
    [(validated_requisites, _)] = stub.validated
    assert validated_requisites == CARD_REQUISITES
    assert stub.sources == [Source(kind="text", text="Рахунок № 00007 від 12.10.2026")]
    [in_progress] = session.calls(EditMessageReplyMarkup)
    assert in_progress.reply_markup.inline_keyboard[0][0].callback_data == handlers.PURPOSE_DATA
    assert button_labels(session) == [texts.PURPOSE_IN_PROGRESS_LABEL]
    assert answers(session) == [None]


def test_press_on_in_progress_button_left_by_restart_runs(dispatcher, monkeypatch):
    bot, session = make_bot()
    PurposeStub(monkeypatch, [PURPOSE], [purpose_verdict()])
    left_by_restart = handlers._purpose_keyboard(texts.PURPOSE_IN_PROGRESS_LABEL)
    asyncio.run(press(dispatcher, bot, card_for(text_source(), reply_markup=left_by_restart)))

    assert len(session.calls(EditMessageMedia)) == 1


def test_press_downloads_document_source(dispatcher, monkeypatch):
    bot, session = make_bot()
    stub = PurposeStub(monkeypatch, [PURPOSE], [purpose_verdict()])
    source = message_update(message_id=SOURCE_ID, document_mime="application/pdf").message
    asyncio.run(press(dispatcher, bot, card_for(source)))

    [get_file] = session.calls(GetFile)
    assert get_file.file_id == "document"
    assert stub.sources == [Source(kind="pdf", data=session.file_content)]
    assert len(session.calls(EditMessageMedia)) == 1
    request_types = [type(request) for request in session.requests]
    assert request_types.index(AnswerCallbackQuery) < request_types.index(GetFile)


def test_second_press_while_first_runs_is_answered_and_runs_nothing(dispatcher, monkeypatch):
    bot, session = make_bot()
    release = asyncio.Event()
    calls = []

    async def slow_extract_purpose(source):
        calls.append(source)
        await release.wait()
        return PURPOSE

    async def fake_validate_purpose(source, requisites, fields):
        return purpose_verdict()

    monkeypatch.setattr(llm, "extract_purpose", slow_extract_purpose)
    monkeypatch.setattr(llm, "validate_purpose", fake_validate_purpose)
    message = card_for(text_source())

    async def scenario():
        first = asyncio.create_task(press(dispatcher, bot, message))
        while not calls:
            await asyncio.sleep(0)
        await press(dispatcher, bot, message)
        release.set()
        await first

    asyncio.run(scenario())

    assert len(calls) == 1
    assert answers(session) == [None, texts.PURPOSE_ALREADY_RUNNING]
    assert button_labels(session) == [texts.PURPOSE_IN_PROGRESS_LABEL]
    assert len(session.calls(EditMessageMedia)) == 1


def test_press_after_failed_press_runs_again(dispatcher, monkeypatch):
    bot, session = make_bot()
    no_reference = PURPOSE.model_copy(update={"invoice_number": None, "invoice_date": None})
    stub = PurposeStub(monkeypatch, [no_reference, PURPOSE], [purpose_verdict(), purpose_verdict()])
    message = card_for(text_source())

    async def scenario():
        await press(dispatcher, bot, message)
        await press(dispatcher, bot, message)

    asyncio.run(scenario())

    assert len(stub.sources) == 2
    assert len(session.calls(EditMessageMedia)) == 1


def test_press_whose_error_reply_fails_frees_card_for_next_press(dispatcher, monkeypatch):
    bot, session = make_bot()
    no_reference = PURPOSE.model_copy(update={"invoice_number": None, "invoice_date": None})
    stub = PurposeStub(monkeypatch, [no_reference, PURPOSE], [purpose_verdict(), purpose_verdict()])
    error = TelegramBadRequest(method=None, message="Bad Request: message to be replied not found")
    session.fail = lambda method: error if isinstance(method, SendMessage) else None
    message = card_for(text_source())

    async def scenario():
        with pytest.raises(TelegramBadRequest):
            await press(dispatcher, bot, message)
        await press(dispatcher, bot, message)

    asyncio.run(scenario())

    assert len(stub.sources) == 2
    assert len(session.calls(EditMessageMedia)) == 1


def test_press_on_inaccessible_card_says_expired(dispatcher, monkeypatch):
    bot, session = make_bot()
    stub = PurposeStub(monkeypatch, [PURPOSE], [purpose_verdict()])
    old = InaccessibleMessage(chat=Chat(id=USER.id, type="private"), message_id=CARD_ID, date=0)
    asyncio.run(press(dispatcher, bot, old))

    assert answers(session) == [texts.MESSAGE_EXPIRED]
    assert stub.sources == []


def test_press_on_card_without_requisites_says_expired(dispatcher, monkeypatch):
    bot, session = make_bot()
    stub = PurposeStub(monkeypatch, [PURPOSE], [purpose_verdict()])
    message = card_message("🧾 Реквізити платежу:", reply_to=text_source(), reply_markup=PURPOSE_BUTTON)
    asyncio.run(press(dispatcher, bot, message))

    assert answers(session) == [texts.MESSAGE_EXPIRED]
    assert stub.sources == []


def test_late_press_on_card_that_no_longer_shows_button_runs_nothing(dispatcher, monkeypatch):
    bot, session = make_bot()
    stub = PurposeStub(monkeypatch, [PURPOSE], [purpose_verdict()])
    caption = texts.format_success(CARD_REQUISITES.model_copy(update={"payment_purpose": GENERATED_PURPOSE}), [], QR)
    replaced = card_message(caption, reply_to=text_source(), reply_markup=None)
    asyncio.run(press(dispatcher, bot, replaced))

    assert answers(session) == [texts.MESSAGE_EXPIRED]
    assert session.calls(EditMessageReplyMarkup) == []
    assert stub.sources == []


def test_press_without_source_says_source_gone(dispatcher, monkeypatch):
    bot, session = make_bot()
    stub = PurposeStub(monkeypatch, [PURPOSE], [purpose_verdict()])
    asyncio.run(press(dispatcher, bot, card_for(None)))

    assert answers(session) == [texts.SOURCE_GONE]
    assert session.calls(EditMessageReplyMarkup) == []
    assert stub.sources == []


@pytest.mark.parametrize("fail_get_file,download_error", [(True, None),
                                                          (False, aiohttp.ClientPayloadError("connection reset")),
                                                          (False, TimeoutError())])
def test_failed_download_replies_to_card_and_restores_button(dispatcher, monkeypatch, fail_get_file, download_error):
    bot, session = make_bot()
    stub = PurposeStub(monkeypatch, [PURPOSE], [purpose_verdict()])
    error = TelegramBadRequest(method=None, message="Bad Request: file is too big")
    session.fail = lambda method: error if fail_get_file and isinstance(method, GetFile) else None
    session.download_error = download_error
    source = message_update(message_id=SOURCE_ID, photo=True).message
    asyncio.run(press(dispatcher, bot, card_for(source)))

    [reply] = session.calls(SendMessage)
    assert (reply.text, reply.reply_parameters.message_id, reply.reply_markup) == (texts.ERR_TELEGRAM, CARD_ID, None)
    assert answers(session) == [None]
    assert button_labels(session) == [texts.PURPOSE_IN_PROGRESS_LABEL, texts.PURPOSE_LABEL]
    assert stub.sources == []


def test_new_caption_over_limit_sends_new_card_and_removes_old_button(dispatcher, monkeypatch):
    bot, session = make_bot()
    PurposeStub(monkeypatch, [PURPOSE], [purpose_verdict()])
    # The card without a purpose fits the caption limit, and the card with the generated purpose does not.
    monkeypatch.setattr(handlers, "TELEGRAM_PHOTO_CAPTION_LIMIT", handlers._caption_len(card_caption()))
    asyncio.run(press(dispatcher, bot, card_for(text_source())))

    assert session.calls(EditMessageMedia) == []
    [photo] = session.calls(SendPhoto)
    [text] = session.calls(SendMessage)
    assert photo.caption is None
    assert GENERATED_PURPOSE in text.text
    assert photo.reply_parameters.message_id == text.reply_parameters.message_id == SOURCE_ID
    assert button_labels(session) == [texts.PURPOSE_IN_PROGRESS_LABEL, None]


def test_pipeline_error_replies_to_card_with_report_button_and_restores_button(dispatcher, monkeypatch):
    bot, session = make_bot()
    no_reference = PURPOSE.model_copy(update={"invoice_number": None, "invoice_date": None})
    PurposeStub(monkeypatch, [no_reference], [purpose_verdict()])
    asyncio.run(press(dispatcher, bot, card_for(text_source())))

    [reply] = session.calls(SendMessage)
    assert reply.text == texts.ERR_NO_INVOICE_REFERENCE
    assert reply.reply_parameters.message_id == CARD_ID
    assert reply.reply_markup == feedback.report_keyboard(SOURCE_ID, is_text=True)
    assert button_labels(session) == [texts.PURPOSE_IN_PROGRESS_LABEL, texts.PURPOSE_LABEL]
    assert answers(session) == [None]
    assert session.calls(EditMessageMedia) == []


def test_transient_error_replies_to_card_without_report_button(dispatcher, monkeypatch):
    bot, session = make_bot()

    async def failing_add_purpose(source, requisites):
        raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))

    monkeypatch.setattr(pipeline, "add_purpose", failing_add_purpose)
    asyncio.run(press(dispatcher, bot, card_for(text_source())))

    [reply] = session.calls(SendMessage)
    assert (reply.text, reply.reply_markup) == (texts.ERR_NETWORK, None)
    assert button_labels(session)[-1] == texts.PURPOSE_LABEL


def test_unexpected_error_on_document_replies_with_document_report_button(dispatcher, monkeypatch):
    bot, session = make_bot()

    async def failing_add_purpose(source, requisites):
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "add_purpose", failing_add_purpose)
    source = message_update(message_id=SOURCE_ID, document_mime="application/pdf").message
    asyncio.run(press(dispatcher, bot, card_for(source)))

    [reply] = session.calls(SendMessage)
    assert reply.text == texts.ERR_UNEXPECTED
    assert reply.reply_markup == feedback.report_keyboard(SOURCE_ID, is_text=False)
    assert button_labels(session)[-1] == texts.PURPOSE_LABEL


def test_failed_card_edit_sends_new_card_to_source_and_removes_old_button(dispatcher, monkeypatch):
    bot, session = make_bot()
    PurposeStub(monkeypatch, [PURPOSE], [purpose_verdict()])
    error = TelegramBadRequest(method=None, message="Bad Request: message can't be edited")
    session.fail = lambda method: error if isinstance(method, EditMessageMedia) else None
    asyncio.run(press(dispatcher, bot, card_for(text_source())))

    [photo] = session.calls(SendPhoto)
    assert GENERATED_PURPOSE in photo.caption
    assert photo.reply_parameters.message_id == SOURCE_ID
    assert photo.reply_markup is None
    assert button_labels(session) == [texts.PURPOSE_IN_PROGRESS_LABEL, None]
    assert session.calls(SendMessage) == []


def test_press_finishes_when_old_press_cannot_be_answered(dispatcher, monkeypatch):
    bot, session = make_bot()
    PurposeStub(monkeypatch, [PURPOSE], [purpose_verdict()])
    too_old = TelegramBadRequest(method=None, message="Bad Request: query is too old and response timeout expired")
    session.fail = lambda method: too_old if isinstance(method, AnswerCallbackQuery) else None
    asyncio.run(press(dispatcher, bot, card_for(text_source())))

    assert len(session.calls(EditMessageMedia)) == 1
