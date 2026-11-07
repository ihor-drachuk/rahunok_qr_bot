import asyncio
import html as html_lib
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import AnswerCallbackQuery, CopyMessages, EditMessageReplyMarkup, EditMessageText, SendMessage
from aiogram.types import Chat, InaccessibleMessage, User

from app import feedback, handlers, texts
from app.albums import AlbumMiddleware
from app.feedback import ReportAction, ReportCallback
from tests.telegram_fakes import DEVELOPER_CHAT_ID, USER, bot_message, callback_update, make_bot, message_update

DEVELOPER = User(id=DEVELOPER_CHAT_ID, is_bot=False, first_name="Dev")
PROMPT = texts.contact_prompt(feedback.WAIT_TIMEOUT_MINUTES)
TIMEOUT = timedelta(minutes=feedback.WAIT_TIMEOUT_MINUTES)


@pytest.fixture
def pipeline_calls(monkeypatch):
    calls = []

    async def fake_process_and_reply(message, source, stage_mode):
        calls.append(message.message_id)

    async def fake_handle_media(message, bot, file_id, kind, media_type, stage_mode):
        calls.append(message.message_id)

    monkeypatch.setattr(handlers, "_process_and_reply", fake_process_and_reply)
    monkeypatch.setattr(handlers, "_handle_media", fake_handle_media)
    return calls


def plain(html_text: str) -> str:
    # What Telegram puts into Message.text for a message the bot sent as HTML.
    return html_lib.unescape(re.sub(r"<[^>]+>", "", html_text))


def sent_texts(session, chat_id: int) -> list[str]:
    return [call.text for call in session.calls(SendMessage) if call.chat_id == chat_id]


def copied_batches(session) -> list[list[int]]:
    return [call.message_ids for call in session.calls(CopyMessages)]


def signed_header(bot, user=USER) -> str:
    return texts.format_feedback_header(user, feedback._reply_signature(bot, user.id))


async def feed_album(dispatcher, bot, message_ids, captions=None, user=USER, reply_to=None, videos=()):
    # Polling runs every update as its own task, so album items are handled concurrently.
    captions = captions or {}
    await asyncio.gather(*(dispatcher.feed_update(bot, message_update(
        user=user, message_id=message_id, photo=message_id not in videos, video=message_id in videos,
        media_group_id="album", caption=captions.get(message_id), reply_to=reply_to)) for message_id in message_ids))


async def until_album_collection_starts(dispatcher) -> None:
    middleware = next(item for item in dispatcher.message.outer_middleware if isinstance(item, AlbumMiddleware))
    while not middleware._groups:
        await asyncio.sleep(0)


def is_prompt(method) -> bool:
    return isinstance(method, SendMessage) and method.text == PROMPT


def test_contact_then_message_goes_to_developer_and_not_to_pipeline(dispatcher, pipeline_calls):
    bot, session = make_bot()

    async def scenario():
        await dispatcher.feed_update(bot, message_update(text="/contact"))
        await dispatcher.feed_update(bot, message_update(message_id=10, text="Дякую за бота!"))
        await dispatcher.feed_update(bot, message_update(message_id=11, text="реквізити"))

    asyncio.run(scenario())

    prompt = session.calls(SendMessage)[0]
    assert (prompt.chat_id, prompt.text) == (USER.id, PROMPT)
    assert prompt.reply_markup.inline_keyboard[0][0].callback_data == feedback.CONTACT_CANCEL_DATA

    [copy] = session.calls(CopyMessages)
    assert (copy.chat_id, copy.from_chat_id, copy.message_ids) == (DEVELOPER_CHAT_ID, USER.id, [10])
    [header] = [call for call in session.calls(SendMessage) if call.chat_id == DEVELOPER_CHAT_ID]
    assert header.text == signed_header(bot)
    assert header.reply_parameters.message_id == session.copy_results[0][0]
    assert sent_texts(session, USER.id)[-1] == texts.FEEDBACK_SENT

    [prompt_edit] = session.calls(EditMessageReplyMarkup)
    assert (prompt_edit.message_id, prompt_edit.reply_markup) == (session.sent_messages[0].message_id, None)
    assert pipeline_calls == [11]


def test_feedback_alias_starts_same_wait(dispatcher, pipeline_calls):
    bot, session = make_bot()

    async def scenario():
        await dispatcher.feed_update(bot, message_update(text="/feedback"))
        await dispatcher.feed_update(bot, message_update(message_id=12, text="Дякую!"))

    asyncio.run(scenario())

    assert copied_batches(session) == [[12]]
    assert pipeline_calls == []


def test_contact_with_text_delivers_that_message_without_prompt(dispatcher, pipeline_calls):
    bot, session = make_bot()

    async def scenario():
        await dispatcher.feed_update(bot, message_update(message_id=20, text="/contact Не працює PDF"))
        await dispatcher.feed_update(bot, message_update(message_id=21, text="реквізити"))

    asyncio.run(scenario())

    assert copied_batches(session) == [[20]]
    assert PROMPT not in sent_texts(session, USER.id)
    assert pipeline_calls == [21]


def test_contact_caption_on_photo_delivers_that_photo(dispatcher, pipeline_calls):
    bot, session = make_bot()
    asyncio.run(dispatcher.feed_update(bot, message_update(message_id=30, photo=True, caption="/contact")))

    assert copied_batches(session) == [[30]]
    assert pipeline_calls == []


def test_contact_caption_on_any_album_item_delivers_whole_album(dispatcher, pipeline_calls):
    bot, session = make_bot()
    asyncio.run(feed_album(dispatcher, bot, [33, 32, 31], captions={32: "/contact"}))

    assert copied_batches(session) == [[31, 32, 33]]
    assert pipeline_calls == []


def test_contact_caption_wins_over_earlier_plain_caption_in_album(dispatcher, pipeline_calls):
    bot, session = make_bot()
    asyncio.run(feed_album(dispatcher, bot, [34, 35], captions={34: "скрін помилки", 35: "/contact"}))

    assert copied_batches(session) == [[34, 35]]
    assert pipeline_calls == []


def test_album_while_waiting_is_delivered_as_one_batch(dispatcher, pipeline_calls):
    bot, session = make_bot()

    async def scenario():
        await dispatcher.feed_update(bot, message_update(text="/contact"))
        await feed_album(dispatcher, bot, [72, 70, 71])
        await dispatcher.feed_update(bot, message_update(message_id=73, text="реквізити"))

    asyncio.run(scenario())

    [album] = session.calls(CopyMessages)
    assert (album.chat_id, album.from_chat_id, album.message_ids) == (DEVELOPER_CHAT_ID, USER.id, [70, 71, 72])
    assert len(sent_texts(session, DEVELOPER_CHAT_ID)) == 1
    assert sent_texts(session, USER.id).count(texts.FEEDBACK_SENT) == 1
    assert pipeline_calls == [73]


def test_album_without_wait_goes_to_pipeline_item_by_item(dispatcher, pipeline_calls):
    bot, session = make_bot()
    asyncio.run(feed_album(dispatcher, bot, [75, 74]))

    assert sorted(pipeline_calls) == [74, 75]
    assert session.calls(CopyMessages) == []


def test_cancel_command_ends_wait(dispatcher, pipeline_calls):
    bot, session = make_bot()

    async def scenario():
        await dispatcher.feed_update(bot, message_update(text="/contact"))
        await dispatcher.feed_update(bot, message_update(text="/cancel"))
        await dispatcher.feed_update(bot, message_update(message_id=40, text="реквізити"))

    asyncio.run(scenario())

    assert sent_texts(session, USER.id)[-1] == texts.CONTACT_CANCELLED
    assert session.calls(CopyMessages) == []
    assert pipeline_calls == [40]


def test_cancel_without_wait_says_nothing_to_cancel(dispatcher, pipeline_calls):
    bot, session = make_bot()
    asyncio.run(dispatcher.feed_update(bot, message_update(text="/cancel")))

    assert sent_texts(session, USER.id) == [texts.CONTACT_NOTHING_TO_CANCEL]
    assert pipeline_calls == []


def test_cancel_button_ends_wait(dispatcher, pipeline_calls):
    bot, session = make_bot()

    async def scenario():
        await dispatcher.feed_update(bot, message_update(text="/contact"))
        await dispatcher.feed_update(bot, callback_update(feedback.CONTACT_CANCEL_DATA, session.sent_messages[0]))
        await dispatcher.feed_update(bot, message_update(message_id=50, text="реквізити"))

    asyncio.run(scenario())

    assert sent_texts(session, USER.id)[-1] == texts.CONTACT_CANCELLED
    assert len(session.calls(AnswerCallbackQuery)) == 1
    [prompt_edit] = session.calls(EditMessageReplyMarkup)
    assert (prompt_edit.message_id, prompt_edit.reply_markup) == (session.sent_messages[0].message_id, None)
    assert pipeline_calls == [50]


def test_cancel_button_without_wait_removes_itself(dispatcher, pipeline_calls):
    bot, session = make_bot()
    prompt = bot_message(USER.id, PROMPT)
    asyncio.run(dispatcher.feed_update(bot, callback_update(feedback.CONTACT_CANCEL_DATA, prompt)))

    [edit] = session.calls(EditMessageReplyMarkup)
    assert (edit.message_id, edit.reply_markup) == (prompt.message_id, None)
    assert sent_texts(session, USER.id) == []


def test_old_cancel_button_cancels_current_wait_and_removes_itself(dispatcher, pipeline_calls):
    bot, session = make_bot()

    async def scenario():
        await dispatcher.feed_update(bot, message_update(text="/contact"))
        await dispatcher.feed_update(bot, message_update(text="/contact"))
        old_prompt, new_prompt = session.sent_messages
        await dispatcher.feed_update(bot, callback_update(feedback.CONTACT_CANCEL_DATA, old_prompt))
        await dispatcher.feed_update(bot, message_update(message_id=51, text="реквізити"))
        return old_prompt, new_prompt

    old_prompt, new_prompt = asyncio.run(scenario())

    edited = {call.message_id for call in session.calls(EditMessageReplyMarkup)}
    assert edited == {old_prompt.message_id, new_prompt.message_id}
    assert sent_texts(session, USER.id)[-1] == texts.CONTACT_CANCELLED
    assert pipeline_calls == [51]


def test_repeated_contact_replaces_wait_and_removes_old_prompt_button(dispatcher, pipeline_calls):
    bot, session = make_bot()

    async def scenario():
        await dispatcher.feed_update(bot, message_update(text="/contact"))
        await dispatcher.feed_update(bot, message_update(message_id=52, text="/contact привіт"))
        await dispatcher.feed_update(bot, message_update(message_id=53, text="реквізити"))

    asyncio.run(scenario())

    [prompt_edit] = session.calls(EditMessageReplyMarkup)
    assert prompt_edit.message_id == session.sent_messages[0].message_id
    assert copied_batches(session) == [[52]]
    assert pipeline_calls == [53]


def test_other_command_while_waiting_ends_wait_and_runs_command(dispatcher, pipeline_calls):
    bot, session = make_bot()

    async def scenario():
        await dispatcher.feed_update(bot, message_update(text="/contact"))
        await dispatcher.feed_update(bot, message_update(text="/help"))
        await dispatcher.feed_update(bot, message_update(message_id=60, text="реквізити"))

    asyncio.run(scenario())

    assert texts.HELP in sent_texts(session, USER.id)
    assert session.calls(CopyMessages) == []
    assert pipeline_calls == [60]


def test_second_delivery_within_cooldown_is_rejected(dispatcher, pipeline_calls):
    bot, session = make_bot()

    async def scenario():
        await dispatcher.feed_update(bot, message_update(text="/contact перше"))
        await dispatcher.feed_update(bot, message_update(text="/contact друге"))

    asyncio.run(scenario())

    assert len(session.calls(CopyMessages)) == 1
    assert sent_texts(session, USER.id) == [texts.FEEDBACK_SENT, texts.FEEDBACK_COOLDOWN]


def test_failed_copy_frees_cooldown_for_next_contact(dispatcher, pipeline_calls):
    bot, session = make_bot()
    failures = iter([TelegramBadRequest(method=None, message="message can't be copied")])
    session.fail = lambda method: next(failures, None) if isinstance(method, CopyMessages) else None

    async def scenario():
        await dispatcher.feed_update(bot, message_update(message_id=80, text="/contact перше"))
        await dispatcher.feed_update(bot, message_update(message_id=81, text="/contact друге"))

    asyncio.run(scenario())

    assert copied_batches(session) == [[80], [81]]
    assert sent_texts(session, USER.id) == [texts.FEEDBACK_FAILED, texts.FEEDBACK_SENT]
    assert len(sent_texts(session, DEVELOPER_CHAT_ID)) == 1


def test_failed_header_still_counts_as_delivered(dispatcher, pipeline_calls):
    bot, session = make_bot()
    error = TelegramBadRequest(method=None, message="Bad Request: message to be replied not found")
    session.fail = lambda method: error if isinstance(method, SendMessage) and method.chat_id == DEVELOPER_CHAT_ID else None

    async def scenario():
        await dispatcher.feed_update(bot, message_update(text="/contact перше"))
        await dispatcher.feed_update(bot, message_update(text="/contact друге"))

    asyncio.run(scenario())

    assert len(session.calls(CopyMessages)) == 1
    assert sent_texts(session, USER.id) == [texts.FEEDBACK_SENT, texts.FEEDBACK_COOLDOWN]


def test_nothing_copied_is_reported_as_failure(dispatcher, pipeline_calls):
    bot, session = make_bot()
    session.deleted_message_ids = {82}
    asyncio.run(dispatcher.feed_update(bot, message_update(message_id=82, text="/contact видалене")))

    assert sent_texts(session, USER.id) == [texts.FEEDBACK_FAILED]
    assert sent_texts(session, DEVELOPER_CHAT_ID) == []


def developer_reply(replied_text: str, message_id: int = 90):
    replied = bot_message(DEVELOPER_CHAT_ID, replied_text)
    return message_update(user=DEVELOPER, message_id=message_id, text="Дякую, виправлю", reply_to=replied)


def test_developer_reply_to_header_reaches_user(dispatcher, pipeline_calls):
    bot, session = make_bot()
    asyncio.run(dispatcher.feed_update(bot, developer_reply(plain(signed_header(bot)))))

    [copy] = session.calls(CopyMessages)
    assert (copy.chat_id, copy.from_chat_id, copy.message_ids) == (USER.id, DEVELOPER_CHAT_ID, [90])
    [intro] = [call for call in session.calls(SendMessage) if call.chat_id == USER.id]
    assert intro.text == texts.DEVELOPER_REPLY_INTRO
    assert intro.reply_parameters.message_id == session.copy_results[0][0]
    assert session.requests.index(copy) < session.requests.index(intro)
    assert sent_texts(session, DEVELOPER_CHAT_ID) == [texts.DEVELOPER_REPLY_DELIVERED]
    assert pipeline_calls == []


def test_developer_reply_as_album_reaches_user_whole(dispatcher, pipeline_calls):
    bot, session = make_bot()
    header = bot_message(DEVELOPER_CHAT_ID, plain(signed_header(bot)))
    asyncio.run(feed_album(dispatcher, bot, [92, 91], user=DEVELOPER, reply_to=header))

    assert copied_batches(session) == [[91, 92]]
    assert sent_texts(session, DEVELOPER_CHAT_ID) == [texts.DEVELOPER_REPLY_DELIVERED]
    assert pipeline_calls == []


def test_developer_reply_to_copied_text_posing_as_header_gets_hint(dispatcher, pipeline_calls):
    bot, session = make_bot()
    victim = User(id=2002, is_bot=False, first_name="Інша")
    forged = plain(texts.format_feedback_header(victim, "0" * 12))
    asyncio.run(dispatcher.feed_update(bot, developer_reply(forged)))

    assert session.calls(CopyMessages) == []
    assert sent_texts(session, DEVELOPER_CHAT_ID) == [texts.DEVELOPER_REPLY_HINT]


def test_developer_reply_to_other_bot_message_gets_hint(dispatcher, pipeline_calls):
    bot, session = make_bot()
    asyncio.run(dispatcher.feed_update(bot, developer_reply("Дякую за бота!")))

    assert session.calls(CopyMessages) == []
    assert sent_texts(session, DEVELOPER_CHAT_ID) == [texts.DEVELOPER_REPLY_HINT]
    assert pipeline_calls == []


def test_developer_reply_to_user_who_blocked_bot_is_reported(dispatcher, pipeline_calls):
    bot, session = make_bot()
    blocked = TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user")
    session.fail = lambda method: blocked if getattr(method, "chat_id", None) == USER.id else None
    asyncio.run(dispatcher.feed_update(bot, developer_reply(plain(signed_header(bot)))))

    assert sent_texts(session, DEVELOPER_CHAT_ID) == [texts.DEVELOPER_REPLY_BLOCKED]


def test_developer_reply_failing_otherwise_is_reported(dispatcher, pipeline_calls):
    bot, session = make_bot()
    error = TelegramBadRequest(method=None, message="Bad Request: message can't be copied")
    session.fail = lambda method: error if isinstance(method, CopyMessages) else None
    asyncio.run(dispatcher.feed_update(bot, developer_reply(plain(signed_header(bot)))))

    assert sent_texts(session, DEVELOPER_CHAT_ID) == [texts.DEVELOPER_REPLY_FAILED]


def test_developer_message_without_reply_goes_to_pipeline(dispatcher, pipeline_calls):
    bot, session = make_bot()
    asyncio.run(dispatcher.feed_update(bot, message_update(user=DEVELOPER, message_id=95, text="реквізити")))

    assert pipeline_calls == [95]


def report_data(action: ReportAction, message_id: int = 100, is_text: bool = False) -> str:
    return ReportCallback(action=action, message_id=message_id, is_text=is_text).pack()


def error_reply():
    return bot_message(USER.id, texts.format_error(texts.ERR_NO_IBAN, None), message_id=101)


def test_report_ask_shows_confirm_and_decline(dispatcher):
    bot, session = make_bot()
    asyncio.run(dispatcher.feed_update(bot, callback_update(report_data(ReportAction.ASK), error_reply())))

    [edit] = session.calls(EditMessageReplyMarkup)
    buttons = edit.reply_markup.inline_keyboard[0]
    assert [button.text for button in buttons] == [texts.REPORT_CONFIRM_LABEL, texts.REPORT_DECLINE_LABEL]
    assert [button.callback_data for button in buttons] == [report_data(ReportAction.CONFIRM), report_data(ReportAction.DECLINE)]
    assert session.calls(CopyMessages) == []


def test_report_second_tap_on_same_button_is_ignored(dispatcher):
    bot, session = make_bot()
    not_modified = TelegramBadRequest(method=None, message="Bad Request: message is not modified")
    session.fail = lambda method: not_modified if isinstance(method, EditMessageReplyMarkup) else None
    asyncio.run(dispatcher.feed_update(bot, callback_update(report_data(ReportAction.ASK), error_reply())))

    assert len(session.calls(AnswerCallbackQuery)) == 1


def test_report_decline_restores_ask_button(dispatcher):
    bot, session = make_bot()
    asyncio.run(dispatcher.feed_update(bot, callback_update(report_data(ReportAction.DECLINE), error_reply())))

    [edit] = session.calls(EditMessageReplyMarkup)
    assert edit.reply_markup == feedback.report_keyboard(100, is_text=False)
    assert session.calls(CopyMessages) == []


def test_report_confirm_sends_source_to_developer(dispatcher):
    bot, session = make_bot()
    asyncio.run(dispatcher.feed_update(bot, callback_update(report_data(ReportAction.CONFIRM), error_reply())))

    [copy] = session.calls(CopyMessages)
    assert (copy.chat_id, copy.from_chat_id, copy.message_ids) == (DEVELOPER_CHAT_ID, USER.id, [100])
    first_error_line = texts.ERR_NO_IBAN.split("\n", 1)[0]
    signature = feedback._reply_signature(bot, USER.id)
    expected_header = texts.format_report_header(USER, signature, first_error_line, is_text=False)
    assert sent_texts(session, DEVELOPER_CHAT_ID) == [expected_header]
    [edit] = session.calls(EditMessageText)
    assert edit.text.endswith(texts.REPORT_SENT)
    assert edit.reply_markup is None


def test_report_confirm_within_cooldown_is_rejected(dispatcher):
    bot, session = make_bot()

    async def scenario():
        await dispatcher.feed_update(bot, callback_update(report_data(ReportAction.CONFIRM), error_reply()))
        await dispatcher.feed_update(bot, callback_update(report_data(ReportAction.CONFIRM), error_reply()))

    asyncio.run(scenario())

    assert len(session.calls(CopyMessages)) == 1
    assert session.calls(AnswerCallbackQuery)[-1].text == texts.REPORT_COOLDOWN


def test_report_confirm_failing_frees_cooldown(dispatcher):
    bot, session = make_bot()
    failures = iter([TelegramBadRequest(method=None, message="Bad Request: chat not found")])
    session.fail = lambda method: next(failures, None) if isinstance(method, CopyMessages) else None

    async def scenario():
        await dispatcher.feed_update(bot, callback_update(report_data(ReportAction.CONFIRM), error_reply()))
        await dispatcher.feed_update(bot, callback_update(report_data(ReportAction.CONFIRM), error_reply()))

    asyncio.run(scenario())

    assert session.calls(AnswerCallbackQuery)[0].text == texts.REPORT_FAILED
    assert len(session.calls(CopyMessages)) == 2
    assert len(sent_texts(session, DEVELOPER_CHAT_ID)) == 1


def test_report_confirm_for_deleted_source_says_so(dispatcher):
    bot, session = make_bot()
    session.deleted_message_ids = {100}
    asyncio.run(dispatcher.feed_update(bot, callback_update(report_data(ReportAction.CONFIRM), error_reply())))

    [answer] = session.calls(AnswerCallbackQuery)
    assert answer.text == texts.REPORT_SOURCE_GONE
    assert sent_texts(session, DEVELOPER_CHAT_ID) == []
    assert session.calls(EditMessageText) == []


def test_report_on_inaccessible_message_says_expired(dispatcher):
    bot, session = make_bot()
    old = InaccessibleMessage(chat=Chat(id=USER.id, type="private"), message_id=101, date=0)
    asyncio.run(dispatcher.feed_update(bot, callback_update(report_data(ReportAction.CONFIRM), old)))

    [answer] = session.calls(AnswerCallbackQuery)
    assert answer.text == texts.REPORT_EXPIRED
    assert session.calls(CopyMessages) == []


def test_report_confirm_for_text_source_titles_header_as_message(dispatcher):
    bot, session = make_bot()
    data = report_data(ReportAction.CONFIRM, is_text=True)
    asyncio.run(dispatcher.feed_update(bot, callback_update(data, error_reply())))

    first_error_line = texts.ERR_NO_IBAN.split("\n", 1)[0]
    signature = feedback._reply_signature(bot, USER.id)
    expected_header = texts.format_report_header(USER, signature, first_error_line, is_text=True)
    assert sent_texts(session, DEVELOPER_CHAT_ID) == [expected_header]


def test_report_decline_for_text_source_restores_message_label(dispatcher):
    bot, session = make_bot()
    data = report_data(ReportAction.DECLINE, is_text=True)
    asyncio.run(dispatcher.feed_update(bot, callback_update(data, error_reply())))

    [edit] = session.calls(EditMessageReplyMarkup)
    assert edit.reply_markup.inline_keyboard[0][0].text == texts.REPORT_MESSAGE_LABEL


def test_failed_edit_after_delivered_report_is_ignored(dispatcher):
    bot, session = make_bot()
    error = TelegramBadRequest(method=None, message="Bad Request: message can't be edited")
    session.fail = lambda method: error if isinstance(method, EditMessageText) else None
    asyncio.run(dispatcher.feed_update(bot, callback_update(report_data(ReportAction.CONFIRM), error_reply())))

    assert len(sent_texts(session, DEVELOPER_CHAT_ID)) == 1
    assert len(session.calls(AnswerCallbackQuery)) == 1


def test_second_bare_contact_removes_old_prompt_button_at_once(dispatcher, pipeline_calls):
    bot, session = make_bot()

    async def scenario():
        await dispatcher.feed_update(bot, message_update(text="/contact"))
        await dispatcher.feed_update(bot, message_update(text="/contact"))

    asyncio.run(scenario())

    old_prompt = session.sent_messages[0]
    assert [call.message_id for call in session.calls(EditMessageReplyMarkup)] == [old_prompt.message_id]


def test_wait_ended_while_prompt_is_sent_leaves_no_cancel_button(dispatcher, pipeline_calls):
    bot, session = make_bot()
    prompt_gate = asyncio.Event()
    session.hold = lambda method: prompt_gate if is_prompt(method) else None

    async def scenario():
        contact = asyncio.create_task(dispatcher.feed_update(bot, message_update(text="/contact")))
        await asyncio.sleep(0.01)
        await dispatcher.feed_update(bot, message_update(message_id=13, text="Дякую!"))
        prompt_gate.set()
        await contact

    asyncio.run(scenario())

    assert copied_batches(session) == [[13]]
    prompt = next(message for message in session.sent_messages if message.text == PROMPT)
    [edit] = session.calls(EditMessageReplyMarkup)
    assert (edit.message_id, edit.reply_markup) == (prompt.message_id, None)


def test_two_concurrent_messages_end_wait_only_once(dispatcher, pipeline_calls):
    bot, session = make_bot()
    edit_gate = asyncio.Event()
    session.hold = lambda method: edit_gate if isinstance(method, EditMessageReplyMarkup) else None

    async def scenario():
        await dispatcher.feed_update(bot, message_update(text="/contact"))
        first = asyncio.create_task(dispatcher.feed_update(bot, message_update(message_id=14, text="перше")))
        second = asyncio.create_task(dispatcher.feed_update(bot, message_update(message_id=15, text="реквізити")))
        await asyncio.sleep(0.01)
        edit_gate.set()
        await asyncio.gather(first, second)

    asyncio.run(scenario())

    assert copied_batches(session) == [[14]]
    assert pipeline_calls == [15]


def test_cancel_while_album_settles_sends_album_to_pipeline(dispatcher, pipeline_calls):
    bot, session = make_bot()

    async def scenario():
        await dispatcher.feed_update(bot, message_update(text="/contact"))
        album = asyncio.create_task(feed_album(dispatcher, bot, [86, 87]))
        await until_album_collection_starts(dispatcher)
        await dispatcher.feed_update(bot, message_update(text="/cancel"))
        await album

    asyncio.run(scenario())

    assert session.calls(CopyMessages) == []
    assert sorted(pipeline_calls) == [86, 87]


def test_text_while_album_settles_takes_wait_and_album_goes_to_pipeline(dispatcher, pipeline_calls):
    bot, session = make_bot()

    async def scenario():
        await dispatcher.feed_update(bot, message_update(text="/contact"))
        album = asyncio.create_task(feed_album(dispatcher, bot, [88, 89]))
        await until_album_collection_starts(dispatcher)
        await dispatcher.feed_update(bot, message_update(message_id=90, text="ось"))
        await album

    asyncio.run(scenario())

    assert copied_batches(session) == [[90]]
    assert sorted(pipeline_calls) == [88, 89]


def test_album_with_unsupported_item_answers_once_and_processes_rest(dispatcher, pipeline_calls):
    bot, session = make_bot()
    asyncio.run(feed_album(dispatcher, bot, [96, 97, 98], videos={97, 98}))

    assert sent_texts(session, USER.id) == [texts.ERR_UNSUPPORTED_TYPE]
    assert pipeline_calls == [96]


def test_cooldown_ends_after_its_time(dispatcher, pipeline_calls, monkeypatch):
    bot, session = make_bot()
    clock = [1000.0]
    monkeypatch.setattr(feedback, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    async def scenario():
        await dispatcher.feed_update(bot, message_update(text="/contact перше"))
        clock[0] += feedback.COOLDOWN_SECONDS + 1
        await dispatcher.feed_update(bot, message_update(text="/contact друге"))

    asyncio.run(scenario())

    assert len(session.calls(CopyMessages)) == 2
    assert sent_texts(session, USER.id) == [texts.FEEDBACK_SENT, texts.FEEDBACK_SENT]


@pytest.mark.parametrize("elapsed,delivered", [(TIMEOUT - timedelta(seconds=1), True), (TIMEOUT, False)])
def test_message_sent_after_wait_timeout_goes_to_pipeline(dispatcher, pipeline_calls, elapsed, delivered):
    bot, session = make_bot()
    contact_sent = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)

    async def scenario():
        # After a restart `/contact` and the next message arrive back to back, so only send times show the gap.
        await dispatcher.feed_update(bot, message_update(text="/contact", date=contact_sent))
        await dispatcher.feed_update(bot, message_update(message_id=110, text="реквізити", date=contact_sent + elapsed))
        await dispatcher.feed_update(bot, message_update(message_id=111, text="ще реквізити"))

    asyncio.run(scenario())

    assert copied_batches(session) == ([[110]] if delivered else [])
    assert pipeline_calls == ([111] if delivered else [110, 111])
    [prompt_edit] = session.calls(EditMessageReplyMarkup)
    assert prompt_edit.message_id == session.sent_messages[0].message_id


def test_cancel_sent_after_wait_timeout_says_nothing_to_cancel(dispatcher, pipeline_calls):
    bot, session = make_bot()
    contact_sent = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)

    async def scenario():
        await dispatcher.feed_update(bot, message_update(text="/contact", date=contact_sent))
        await dispatcher.feed_update(bot, message_update(text="/cancel", date=contact_sent + TIMEOUT))

    asyncio.run(scenario())

    assert sent_texts(session, USER.id)[-1] == texts.CONTACT_NOTHING_TO_CANCEL
    assert len(session.calls(EditMessageReplyMarkup)) == 1


def test_cancel_button_after_wait_timeout_only_removes_itself(dispatcher, pipeline_calls):
    bot, session = make_bot()

    async def scenario():
        contact_sent = datetime.now(UTC) - TIMEOUT - timedelta(minutes=1)
        await dispatcher.feed_update(bot, message_update(text="/contact", date=contact_sent))
        await dispatcher.feed_update(bot, callback_update(feedback.CONTACT_CANCEL_DATA, session.sent_messages[0]))

    asyncio.run(scenario())

    assert sent_texts(session, USER.id) == [PROMPT]
    [edit] = session.calls(EditMessageReplyMarkup)
    assert (edit.message_id, edit.reply_markup) == (session.sent_messages[0].message_id, None)


def test_nothing_copied_frees_cooldown_for_retry(dispatcher, pipeline_calls):
    bot, session = make_bot()
    session.deleted_message_ids = {83}

    async def scenario():
        await dispatcher.feed_update(bot, message_update(message_id=83, text="/contact видалене"))
        await dispatcher.feed_update(bot, message_update(message_id=84, text="/contact ще раз"))

    asyncio.run(scenario())

    assert sent_texts(session, USER.id) == [texts.FEEDBACK_FAILED, texts.FEEDBACK_SENT]


def test_signature_of_another_user_does_not_open_reply(dispatcher, pipeline_calls):
    bot, session = make_bot()
    victim = User(id=2002, is_bot=False, first_name="Інша")
    moved = plain(texts.format_feedback_header(victim, feedback._reply_signature(bot, USER.id)))
    asyncio.run(dispatcher.feed_update(bot, developer_reply(moved)))

    assert session.calls(CopyMessages) == []
    assert sent_texts(session, DEVELOPER_CHAT_ID) == [texts.DEVELOPER_REPLY_HINT]


def test_developer_reply_with_nothing_copied_is_reported(dispatcher, pipeline_calls):
    bot, session = make_bot()
    session.deleted_message_ids = {90}
    asyncio.run(dispatcher.feed_update(bot, developer_reply(plain(signed_header(bot)))))

    assert sent_texts(session, DEVELOPER_CHAT_ID) == [texts.DEVELOPER_REPLY_FAILED]
    assert sent_texts(session, USER.id) == []


def test_developer_reply_with_failed_intro_still_reports_delivered(dispatcher, pipeline_calls):
    bot, session = make_bot()
    error = TelegramBadRequest(method=None, message="Bad Request: message to be replied not found")
    session.fail = lambda method: error if isinstance(method, SendMessage) and method.chat_id == USER.id else None
    asyncio.run(dispatcher.feed_update(bot, developer_reply(plain(signed_header(bot)))))

    assert copied_batches(session) == [[90]]
    assert sent_texts(session, DEVELOPER_CHAT_ID) == [texts.DEVELOPER_REPLY_DELIVERED]


def test_user_reply_to_bot_message_outside_feedback_chat_goes_to_pipeline(dispatcher, pipeline_calls):
    bot, session = make_bot()
    replied = bot_message(USER.id, "🧾 Реквізити платежу")
    asyncio.run(dispatcher.feed_update(bot, message_update(message_id=99, text="реквізити", reply_to=replied)))

    assert pipeline_calls == [99]
    assert sent_texts(session, USER.id) == []


def test_developer_reply_to_own_message_goes_to_pipeline(dispatcher, pipeline_calls):
    bot, session = make_bot()
    own = message_update(user=DEVELOPER, text="чернетка").message
    asyncio.run(dispatcher.feed_update(bot, message_update(user=DEVELOPER, message_id=93, text="реквізити",
                                                           reply_to=own)))

    assert pipeline_calls == [93]
