from decimal import Decimal

from app.validation import classify_code, is_valid_iban, normalize_code, normalize_iban, parse_amount

VALID_IBAN = "UA693000010000000012345678901"


def test_poc_iban_is_valid():
    assert is_valid_iban(VALID_IBAN)


def test_iban_with_flipped_digit_fails_checksum():
    assert not is_valid_iban("UA693000010000000012345678902")


def test_iban_wrong_length_is_invalid():
    assert not is_valid_iban(VALID_IBAN[:-1])
    assert not is_valid_iban(VALID_IBAN + "0")


def test_iban_wrong_country_is_invalid():
    assert not is_valid_iban("DE" + VALID_IBAN[2:])


def test_normalize_iban_strips_spaces_and_uppercases():
    assert normalize_iban("ua69 3000 0100 0000 0012 3456 78901") == VALID_IBAN
    assert is_valid_iban(normalize_iban("ua69 3000 0100 0000 0012 3456 78901"))


def test_normalize_iban_none_and_blank():
    assert normalize_iban(None) is None
    assert normalize_iban("   ") is None


def test_classify_code_edrpou():
    assert classify_code("12345678") == "edrpou"


def test_classify_code_rnokpp():
    assert classify_code("1234567890") == "rnokpp"


def test_classify_code_invalid():
    assert classify_code("123456789") == "invalid"
    assert classify_code("1234567A") == "invalid"


def test_classify_code_empty():
    assert classify_code(None) == "empty"
    assert classify_code("") == "empty"


def test_normalize_code_strips_spaces():
    assert normalize_code(" 1234 5678 ") == "12345678"
    assert normalize_code(None) is None
    assert normalize_code("  ") is None


def test_parse_amount_plain_integer():
    assert parse_amount("13727") == Decimal("13727")


def test_parse_amount_comma_and_spaces():
    assert parse_amount("13 727,50") == Decimal("13727.50")


def test_parse_amount_nbsp_thousands_separator():
    assert parse_amount("13\u00a0727,50") == Decimal("13727.50")


def test_parse_amount_uah_prefix():
    assert parse_amount("UAH13727.50") == Decimal("13727.50")


def test_parse_amount_garbage_is_none():
    assert parse_amount("abc") is None


def test_parse_amount_none_is_none():
    assert parse_amount(None) is None


def test_parse_amount_zero_and_negative_are_none():
    assert parse_amount("0") is None
    assert parse_amount("-5") is None


def test_parse_amount_more_than_two_decimals_is_none():
    assert parse_amount("10.999") is None


def test_parse_amount_absurdly_large_value_is_none():
    assert parse_amount("1E+50") is None
