import html as html_lib
import re
from dataclasses import dataclass
from decimal import Decimal

import pytest
from aiogram.types import User

from app import emoji, texts
from app.models import ExtractedRequisites, PurposeFields
from app.texts import (
    format_card_amount,
    format_error,
    format_pay_links,
    format_requisites,
    format_success,
)
from tests.telegram_fakes import rendered

VALID_IBAN = "UA693000010000000012345678901"


@dataclass
class _StubQr:
    url: str = "https://bank.gov.ua/qr/PAYLOAD"
    mono_url: str = "https://send.monobank.ua/qr/PAYLOAD"


QR = _StubQr()


def test_format_card_amount_groups_thousands_and_appends_currency():
    assert format_card_amount(Decimal("13727")) == "13 727.00 грн"
    assert format_card_amount(Decimal("13727.5")) == "13 727.50 грн"
    assert format_card_amount(Decimal("50")) == "50.00 грн"


def test_format_card_amount_none():
    assert format_card_amount(None) is None


def test_format_requisites_escapes_html_metacharacters():
    requisites = ExtractedRequisites(recipient_name='ТОВ "A&B" <X>', iban=VALID_IBAN)
    out = format_requisites(requisites)
    assert "&amp;" in out
    assert "&lt;X&gt;" in out
    assert "<X>" not in out
    assert out.count("<code>") == 2
    assert out.count("</code>") == 2


def test_format_requisites_skips_missing_fields():
    out = format_requisites(ExtractedRequisites(iban=VALID_IBAN))
    assert out == f"IBAN: <code>{VALID_IBAN}</code>"


def test_amount_is_suffixed_with_currency():
    out = format_requisites(ExtractedRequisites(iban=VALID_IBAN, amount="13727.00"))
    assert "Сума: <code>13727.00</code> грн" in out


def test_format_success_escapes_values_and_lists_warnings():
    requisites = ExtractedRequisites(iban=VALID_IBAN, payment_purpose="Оплата <рахунок> & ПДВ")
    out = format_success(requisites, ["перше", "друге"], QR)
    assert "&lt;рахунок&gt; &amp; ПДВ" in out
    assert "• перше" in out
    assert "• друге" in out


def test_format_success_without_warnings_has_no_warning_block():
    out = format_success(ExtractedRequisites(iban=VALID_IBAN), [], QR)
    assert "Що варто перевірити" not in out


def test_format_success_ends_with_share_footer():
    from app.texts import SUCCESS_FOOTER
    out = format_success(ExtractedRequisites(iban=VALID_IBAN), ["перше"], QR)
    assert out.endswith("\n\n" + SUCCESS_FOOTER)
    assert "@rahunok_qr_bot" in SUCCESS_FOOTER


def test_format_success_includes_both_pay_links():
    out = format_success(ExtractedRequisites(iban=VALID_IBAN), [], QR)
    assert f'<a href="{QR.url}">' in out
    assert f'<a href="{QR.mono_url}">' in out


def test_format_pay_links_labels_and_targets():
    from app.texts import PAY_INTRO
    out = format_pay_links(QR)
    assert out == (
        f"{PAY_INTRO}\n"
        f'<a href="{QR.url}">💳 Сплатити</a>'
        "   ·   "
        f'<a href="{QR.mono_url}">🐱 Монобанк</a>'
    )


def test_format_error_appends_found_fields():
    requisites = ExtractedRequisites(recipient_name="ТОВ <A&B>")
    out = format_error("помилка", requisites)
    assert out.startswith("помилка")
    assert "&lt;A&amp;B&gt;" in out


def test_format_error_without_requisites_is_bare_error():
    assert format_error("помилка", None) == "помилка"
    assert format_error("помилка", ExtractedRequisites()) == "помилка"


@pytest.fixture
def _resolved_gear():
    emoji.set_custom_emoji_ids({"⚙️": "GEAR_EID"})
    yield
    emoji.set_custom_emoji_ids({})


def test_every_status_line_uses_the_gear_custom_emoji(_resolved_gear):
    tag = '<tg-emoji emoji-id="GEAR_EID">⚙️</tg-emoji>'
    for status in (texts.status_searching, texts.status_extracting,
                   texts.status_validating, texts.status_retrying):
        assert status().startswith(tag + " ")


def test_status_lines_fall_back_to_plain_gear_when_unresolved():
    for status in (texts.status_searching, texts.status_extracting,
                   texts.status_validating, texts.status_retrying):
        assert status().startswith("⚙️ ")


def test_success_header_falls_back_to_plain_receipt_when_unresolved():
    out = format_success(ExtractedRequisites(iban=VALID_IBAN), [], QR)
    assert out.startswith("🧾 Реквізити платежу:\n")


def test_success_header_uses_receipt_custom_emoji_when_resolved():
    emoji.set_custom_emoji_ids({"🧾": "RECEIPT_EID"})
    try:
        out = format_success(ExtractedRequisites(iban=VALID_IBAN), [], QR)
        assert out.startswith('<tg-emoji emoji-id="RECEIPT_EID">🧾</tg-emoji> Реквізити платежу:\n')
    finally:
        emoji.set_custom_emoji_ids({})


def test_set_custom_emoji_ids_replaces_rather_than_merges():
    emoji.set_custom_emoji_ids({"⚙️": "OLD_GEAR"})
    emoji.set_custom_emoji_ids({"🧾": "NEW_RECEIPT"})
    try:
        assert emoji.render("⚙️") == "⚙️"  # the earlier entry is gone, not merged
        assert emoji.render("🧾") == '<tg-emoji emoji-id="NEW_RECEIPT">🧾</tg-emoji>'
    finally:
        emoji.set_custom_emoji_ids({})


@pytest.mark.parametrize("requisites", [
    ExtractedRequisites(recipient_name='ТОВ "A&B" <X>', iban="ua69 3000 0100 0000 0012 3456 78901",
                        edrpou_rnokpp="12345", amount="тринадцять тисяч", payment_purpose="Оплата: рахунок № 1"),
    ExtractedRequisites(iban=VALID_IBAN, amount="13727.00"),
])
def test_parse_requisites_reads_back_what_format_success_wrote(requisites):
    emoji.set_custom_emoji_ids({"🧾": "RECEIPT_EID"})  # a custom emoji adds an entity that `parse_requisites` must skip
    try:
        text, entities = rendered(format_success(requisites, ["IBAN: перевірте"], QR))
    finally:
        emoji.set_custom_emoji_ids({})
    assert texts.parse_requisites(text, entities) == requisites


def test_parse_requisites_without_iban_is_none():
    text, entities = rendered(format_success(ExtractedRequisites(recipient_name="ТОВ"), [], QR))
    assert texts.parse_requisites(text, entities) is None


@pytest.mark.parametrize("fields,purpose", [
    (PurposeFields(invoice_number="00007", invoice_date="12.10.2026", goods_kind="goods", item_name="Блок живлення TR 15V 100A"),
     'Оплата за "Блок живлення TR 15V 100A" згідно рахунку №00007 від 12.10.2026'),
    (PurposeFields(invoice_number="СФ-12", invoice_date="12 жовтня 2026 р.", goods_kind="goods"),
     "Оплата за товар згідно рахунку №СФ-12 від 12 жовтня 2026 р."),
    (PurposeFields(invoice_number="7", invoice_date="12.10.2026", goods_kind="services"),
     "Оплата за послуги згідно рахунку №7 від 12.10.2026"),
    (PurposeFields(invoice_number="7", goods_kind="goods"), "Оплата за товар згідно рахунку №7"),
    (PurposeFields(invoice_date="12.10.2026", goods_kind="services"), "Оплата за послуги згідно рахунку від 12.10.2026"),
    (PurposeFields(invoice_number="", invoice_date="12.10.2026", goods_kind="goods", item_name=""),
     "Оплата за товар згідно рахунку від 12.10.2026"),
    (PurposeFields(invoice_number="7", invoice_date="", goods_kind="goods"), "Оплата за товар згідно рахунку №7"),
])
def test_format_generated_purpose_fills_template_and_drops_missing_part(fields, purpose):
    assert texts.format_generated_purpose(fields) == purpose


FEEDBACK_USER = User(id=1001, is_bot=False, first_name="Олена <b>", last_name="&Ко", username="olena")


def _as_telegram_text(html_text: str) -> str:
    # What Telegram puts into Message.text for a message the bot sent as HTML.
    return html_lib.unescape(re.sub(r"<[^>]+>", "", html_text))


def test_feedback_header_escapes_name_and_shows_username():
    header = texts.format_feedback_header(FEEDBACK_USER, "abc123")
    assert "Від: Олена &lt;b&gt; &amp;Ко (@olena)" in header
    assert "ID: <code>1001</code> · <code>abc123</code>" in header


def test_feedback_header_address_round_trips():
    header = _as_telegram_text(texts.format_feedback_header(FEEDBACK_USER, "abc123"))
    assert texts.parse_feedback_address(header) == (1001, "abc123")


def test_report_header_address_wins_over_id_in_error_text():
    header = _as_telegram_text(texts.format_report_header(FEEDBACK_USER, "abc123", "ID: 9 · ffff", is_text=False))
    assert "Помилка: ID: 9 · ffff" in header
    assert texts.parse_feedback_address(header) == (1001, "abc123")


@pytest.mark.parametrize("is_text,title", [(True, "📨 Повідомлення, яке бот не розпізнав"),
                                           (False, "📨 Документ, який бот не розпізнав")])
def test_report_header_title_follows_source_kind(is_text, title):
    header = texts.format_report_header(FEEDBACK_USER, "abc123", "", is_text=is_text)
    assert header.startswith(title + "\n")
    assert "Помилка: \n" in header


@pytest.mark.parametrize("minutes", [15, 7])
def test_contact_prompt_states_time_limit(minutes):
    assert f"протягом {minutes} хв." in texts.contact_prompt(minutes)


def test_parse_feedback_address_without_id_line_is_none():
    assert texts.parse_feedback_address("Дякую за бота! ID: 5 · abc") is None
    assert texts.parse_feedback_address("ID: 5") is None
