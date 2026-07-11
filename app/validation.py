"""Deterministic checks for extracted requisites. Pure functions, no I/O."""

import re
from decimal import Decimal, InvalidOperation
from typing import Literal

_IBAN_RE = re.compile(r"UA\d{27}")


def normalize_iban(raw: str | None) -> str | None:
    if raw is None:
        return None
    iban = re.sub(r"\s+", "", raw).upper()
    return iban or None


def is_valid_iban(iban: str) -> bool:
    if not _IBAN_RE.fullmatch(iban):
        return False
    # ISO 13616 MOD-97 checksum; letters map to 10..35 (U=30, A=10)
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def normalize_code(raw: str | None) -> str | None:
    if raw is None:
        return None
    code = re.sub(r"\s+", "", raw)
    return code or None


CodeKind = Literal["edrpou", "rnokpp", "invalid", "empty"]


def classify_code(code: str | None) -> CodeKind:
    if not code:
        return "empty"
    if re.fullmatch(r"\d{8}", code):
        return "edrpou"
    if re.fullmatch(r"\d{10}", code):
        return "rnokpp"
    return "invalid"


def parse_amount(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    cleaned = raw.strip().upper().removeprefix("UAH")
    cleaned = re.sub(r"\s", "", cleaned).replace(",", ".")
    try:
        value = Decimal(cleaned)
        if value <= 0 or value != value.quantize(Decimal("0.01")):  # more than 2 decimals = extraction error
            return None
    except InvalidOperation:  # also raised by quantize on absurdly large values
        return None
    return value
