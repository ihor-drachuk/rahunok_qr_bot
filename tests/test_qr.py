import base64
from decimal import Decimal

from app.qr import (
    MAX_PAYLOAD_BYTES,
    MONO_QR_PREFIX,
    NBU_QR_PREFIX,
    build_nbu_qr,
    format_amount,
    to_cp1251_bytes,
)

POC_NAME = 'ТОВ "ОРІОН-ПЛЮС"'
POC_IBAN = "UA693000010000000012345678901"
POC_CODE = "12345678"
POC_AMOUNT = Decimal("13727")
POC_PURPOSE = "Рахунок на оплату № 1024 від 30 червня 2026 р."

POC_EXPECTED_PAYLOAD = (
    "BCD\n002\n2\nUCT\n\n"
    'ТОВ "ОРІОН-ПЛЮС"\n'
    "UA693000010000000012345678901\n"
    "UAH13727\n"
    "12345678\n\n\n"
    "Рахунок на оплату № 1024 від 30 червня 2026 р.\n\n"
)


def decode_payload(url: str) -> str:
    b64 = url.removeprefix(NBU_QR_PREFIX)
    padded = b64 + "=" * (-len(b64) % 4)
    return base64.urlsafe_b64decode(padded).decode("cp1251")


def test_poc_example_payload_matches_reference():
    result = build_nbu_qr(POC_NAME, POC_IBAN, POC_AMOUNT, POC_CODE, POC_PURPOSE)
    assert decode_payload(result.url) == POC_EXPECTED_PAYLOAD
    assert not result.truncated_purpose


def test_url_shape():
    result = build_nbu_qr(POC_NAME, POC_IBAN, POC_AMOUNT, POC_CODE, POC_PURPOSE)
    assert result.url.startswith(NBU_QR_PREFIX)
    b64 = result.url.removeprefix(NBU_QR_PREFIX)
    assert "=" not in b64
    assert "+" not in b64
    assert "/" not in b64


def test_mono_url_shares_payload_on_mono_host():
    result = build_nbu_qr(POC_NAME, POC_IBAN, POC_AMOUNT, POC_CODE, POC_PURPOSE)
    payload = result.url.removeprefix(NBU_QR_PREFIX)
    assert result.mono_url == MONO_QR_PREFIX + payload
    assert decode_payload(result.url) == decode_payload(result.mono_url.replace(MONO_QR_PREFIX, NBU_QR_PREFIX))


def test_png_produced():
    result = build_nbu_qr(POC_NAME, POC_IBAN, POC_AMOUNT, POC_CODE, POC_PURPOSE)
    assert result.png.startswith(b"\x89PNG")


def test_image_is_rgba_square():
    result = build_nbu_qr(POC_NAME, POC_IBAN, POC_AMOUNT, POC_CODE, POC_PURPOSE)
    assert result.image.mode == "RGBA"
    assert result.image.width == result.image.height


def test_payload_has_14_lines_with_empty_optional_fields():
    result = build_nbu_qr(None, POC_IBAN, None, None, None)
    lines = decode_payload(result.url).split("\n")
    assert lines == ["BCD", "002", "2", "UCT", "", "", POC_IBAN, "", "", "", "", "", "", ""]


def test_format_amount_integer_has_no_decimals():
    assert format_amount(Decimal("13727")) == "UAH13727"
    assert format_amount(Decimal("13727.00")) == "UAH13727"


def test_format_amount_fractional_uses_dot_and_two_decimals():
    assert format_amount(Decimal("13727.5")) == "UAH13727.50"


def test_format_amount_none_is_empty():
    assert format_amount(None) == ""


def test_cp1251_encoding_of_ukrainian_letters():
    assert to_cp1251_bytes("Єє Іі Її Ґґ №") == b"\xaa\xba \xb2\xb3 \xaf\xbf \xa5\xb4 \xb9"


def test_cp1251_unmappable_char_becomes_question_mark():
    assert to_cp1251_bytes("оплата 💳") == to_cp1251_bytes("оплата ") + b"?"


def test_long_purpose_truncated_to_fit_limit():
    long_purpose = "Оплата за товар згідно рахунку № 12345 " * 20
    result = build_nbu_qr(POC_NAME, POC_IBAN, POC_AMOUNT, POC_CODE, long_purpose)
    assert result.truncated_purpose
    payload = decode_payload(result.url)
    assert len(to_cp1251_bytes(payload)) == MAX_PAYLOAD_BYTES
    lines = payload.split("\n")
    assert len(lines) == 14
    assert lines[5] == POC_NAME
    assert lines[6] == POC_IBAN
    assert lines[7] == "UAH13727"
    assert lines[8] == POC_CODE
    assert long_purpose.startswith(lines[11])


def test_short_purpose_not_truncated():
    result = build_nbu_qr(POC_NAME, POC_IBAN, POC_AMOUNT, POC_CODE, POC_PURPOSE)
    payload = decode_payload(result.url)
    assert len(to_cp1251_bytes(payload)) <= MAX_PAYLOAD_BYTES
    assert not result.truncated_purpose


def test_multiline_purpose_collapsed_to_keep_14_payload_lines():
    result = build_nbu_qr(POC_NAME, POC_IBAN, POC_AMOUNT, POC_CODE, "Оплата за товар\nзгідно рахунку\r\n№ 123")
    lines = decode_payload(result.url).split("\n")
    assert len(lines) == 14
    assert lines[11] == "Оплата за товар згідно рахунку № 123"
    assert not result.truncated_purpose


def test_multiline_name_and_code_collapsed():
    result = build_nbu_qr('ТОВ\n"НАЗВА"', POC_IBAN, None, "4135\n0399", None)
    lines = decode_payload(result.url).split("\n")
    assert len(lines) == 14
    assert lines[5] == 'ТОВ "НАЗВА"'
    assert lines[8] == "4135 0399"


def test_oversized_base_fields_with_empty_purpose_not_flagged_as_truncated():
    huge_name = "А" * 400
    result = build_nbu_qr(huge_name, POC_IBAN, POC_AMOUNT, POC_CODE, None)
    assert not result.truncated_purpose
