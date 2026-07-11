"""Anthropic API layer: requisites extraction and independent validation."""

import base64
from dataclasses import dataclass
from typing import Literal

from anthropic import AsyncAnthropic

from app.config import ANTHROPIC_TIMEOUT_SECONDS, GATE_MAX_TOKENS, GATE_MODEL, MAX_TOKENS, Config
from app.models import ExtractedRequisites, GateVerdict, ValidationVerdict

GATE_PROMPT = """\
You are a gatekeeper for a payment-QR bot. Decide whether the provided document, image, or text plausibly \
contains Ukrainian bank payment requisites.

Set contains_requisites=true if the content includes at least a partial set of actual payment details: an IBAN, \
an invoice or receipt, an account number together with recipient details, or similar.
Set contains_requisites=false for everything else: casual conversation, questions, general talk about money or \
finances without a single actual requisite, unrelated documents or images."""

EXTRACTION_PROMPT = """\
You extract Ukrainian bank payment requisites from the provided document, image, or text.

The document may mention TWO parties: the payer (платник — who pays) and the payment recipient \
(отримувач/одержувач/постачальник — who must RECEIVE the money). Extract ONLY the recipient's details: \
the account the money should be sent TO. In invoices that is normally the supplier (постачальник) whose \
IBAN is given for payment. Never take the name, IBAN, or code from the payer's requisites.

Rules:
- Copy values verbatim from the source. Do not translate, reformat, or invent anything.
- iban: the RECIPIENT's account — "UA" followed by 27 digits, uppercase, no spaces.
- edrpou_rnokpp: the RECIPIENT's code — EDRPOU (8 digits) or RNOKPP (10 digits), whichever the source has.
- amount: the payment amount as a plain number with "." as the decimal separator, without currency symbols \
or thousands separators.
- payment_purpose: read the ENTIRE "призначення платежу" field. It frequently appears visually truncated or \
shorter than it really is — capture the complete text, including invoice numbers and dates.
- recipient_name: the RECIPIENT's name with the legal form ABBREVIATED (ТОВАРИСТВО З ОБМЕЖЕНОЮ \
ВІДПОВІДАЛЬНІСТЮ -> ТОВ, ПРИВАТНЕ ПІДПРИЄМСТВО -> ПП, ФІЗИЧНА ОСОБА-ПІДПРИЄМЕЦЬ -> ФОП, etc.). \
The proper name itself must stay EXACTLY as written in the source, preserving its letter case: \
ТОВАРИСТВО З ОБМЕЖЕНОЮ ВІДПОВІДАЛЬНІСТЮ "ОРІОН-ПЛЮС" -> ТОВ "ОРІОН-ПЛЮС" (not ТОВ "Оріон-Плюс").
- If a field is genuinely absent from the source, set it to null — never guess.
- Put ambiguities or concerns into notes."""

VALIDATION_PROMPT = """\
You are an independent verifier. You are given a payment document (or image, or text) and a JSON object with \
requisites that another system extracted from it.

For EACH of the five fields (recipient_name, iban, edrpou_rnokpp, amount, payment_purpose) decide whether the \
extracted value faithfully matches the source:
- recipient_name, iban, edrpou_rnokpp must belong to the payment RECIPIENT (отримувач/одержувач/постачальник — \
the party the money is sent TO), not to the payer (платник). Flag a mismatch if a value was taken from the \
payer's requisites.
- recipient_name: an ABBREVIATED legal form is correct, not a mismatch (ТОВАРИСТВО З ОБМЕЖЕНОЮ \
ВІДПОВІДАЛЬНІСТЮ "ОРІОН-ПЛЮС" extracted as ТОВ "ОРІОН-ПЛЮС" matches). The proper name itself must \
match the source letter-for-letter INCLUDING letter case — flag a mismatch if its case was changed.
- iban: flag a mismatch only if the country code or digits differ from the source, not for spacing or case.
- amount: must equal the amount in the source as a numeric value.
- payment_purpose: must be the COMPLETE purpose text from the source; flag a mismatch if it is truncated \
or incomplete.
- A field extracted as null counts as a match only when the source genuinely lacks it.

Set matches=false and provide corrected_value when a value is wrong or incomplete. \
Set all_match=true only if every field matches.

Extracted JSON:
"""


@dataclass(frozen=True)
class Source:
    kind: Literal["pdf", "image", "text"]
    data: bytes | None = None
    media_type: str | None = None
    text: str | None = None


_client: AsyncAnthropic | None = None
_model: str = ""


def init(cfg: Config) -> None:
    global _client, _model
    _client = AsyncAnthropic(api_key=cfg.anthropic_api_key, timeout=ANTHROPIC_TIMEOUT_SECONDS)
    _model = cfg.model


def _content_blocks(source: Source, instruction: str) -> list[dict]:
    data_b64 = base64.standard_b64encode(source.data).decode("ascii") if source.data is not None else None
    if source.kind == "pdf":
        media = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data_b64}}
        return [media, {"type": "text", "text": instruction}]
    if source.kind == "image":
        media = {"type": "image", "source": {"type": "base64", "media_type": source.media_type, "data": data_b64}}
        return [media, {"type": "text", "text": instruction}]
    return [{"type": "text", "text": f"{instruction}\n\n<document>\n{source.text}\n</document>"}]


async def _parse(source: Source, instruction: str, output_model, model: str, max_tokens: int):
    response = await _client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": _content_blocks(source, instruction)}],
        output_format=output_model,
    )
    if response.parsed_output is None:
        raise RuntimeError("Model response did not match the expected schema")
    return response.parsed_output


async def gate(source: Source) -> GateVerdict:
    return await _parse(source, GATE_PROMPT, GateVerdict, model=GATE_MODEL, max_tokens=GATE_MAX_TOKENS)


async def extract(source: Source) -> ExtractedRequisites:
    return await _parse(source, EXTRACTION_PROMPT, ExtractedRequisites, model=_model, max_tokens=MAX_TOKENS)


async def validate(source: Source, extracted: ExtractedRequisites) -> ValidationVerdict:
    instruction = VALIDATION_PROMPT + extracted.model_dump_json(indent=2)
    return await _parse(source, instruction, ValidationVerdict, model=_model, max_tokens=MAX_TOKENS)
