from dataclasses import dataclass
from decimal import Decimal

from app.models import ExtractedRequisites
from app.texts import (
    format_card_amount,
    format_error,
    format_pay_links,
    format_requisites,
    format_success,
)

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
    assert "⚠️" not in out


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
