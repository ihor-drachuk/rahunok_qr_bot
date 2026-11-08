"""Anthropic API layer: extraction of requisites and payment-purpose details, and their independent validation."""

import base64
import json
from dataclasses import dataclass
from typing import Literal, TypeVar

from anthropic import AsyncAnthropic, transform_schema
from anthropic.types import Message
from pydantic import BaseModel, ValidationError

from app.config import (ANTHROPIC_TIMEOUT_SECONDS, GATE_EFFORT, GATE_MAX_TOKENS, GATE_MODEL, MAX_TOKENS, MODEL_EFFORT,
                        Config)
from app.models import ExtractedRequisites, GateVerdict, PurposeFields, PurposeValidationVerdict, RequisitesVerdict

T = TypeVar("T", bound=BaseModel)

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
- has_invoice_reference: true when the source states the invoice's own number or date \
(рахунок №…, від …), false otherwise.
- If a field is genuinely absent from the source, set it to null — never guess.
- Put ambiguities or concerns into notes."""

_REQUISITE_RULES = """\
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
- A field extracted as null counts as a match only when the source genuinely lacks it."""

VALIDATION_PROMPT = f"""\
You are an independent verifier. You are given a payment document (or image, or text) and a JSON object with \
requisites that another system extracted from it.

For EACH of the five fields (recipient_name, iban, edrpou_rnokpp, amount, payment_purpose) decide whether the \
extracted value faithfully matches the source:
{_REQUISITE_RULES}

Set matches=false when a value is wrong or incomplete.

Extracted JSON:
"""

_PURPOSE_FIELD_RULES = """\
- invoice_number: the invoice's OWN number exactly as written, without the "№" sign and without words like \
"Рахунок". Never the number of a contract, a delivery note, or any other document.
- invoice_date: the invoice's OWN date exactly as written in the source, in its original format.
- goods_kind: "services" when the invoice is for services or work, "goods" otherwise (also for a mix).
- item_name: when the invoice lists exactly ONE item, that item's name exactly as written; null when it lists \
several items or none."""

PURPOSE_PROMPT = f"""\
You read a Ukrainian invoice (рахунок) from the provided document, image, or text and extract the details \
for its payment purpose.

Rules:
- Copy values verbatim from the source. Do not translate, reformat, or invent anything.
{_PURPOSE_FIELD_RULES}
- If a field is genuinely absent from the source, set it to null — never guess."""

PURPOSE_VALIDATION_PROMPT = f"""\
You are an independent verifier. You are given a payment document (or image, or text) and two JSON objects that \
another system extracted from it: the payment requisites and the details for a payment purpose.

In "requisites", for EACH of the five fields (recipient_name, iban, edrpou_rnokpp, amount, payment_purpose) \
decide whether the extracted value faithfully matches the source:
{_REQUISITE_RULES}

In "purpose", for EACH of the four fields (invoice_number, invoice_date, goods_kind, item_name) decide whether \
the extracted value faithfully matches the source by these definitions:
{_PURPOSE_FIELD_RULES}
- A field extracted as null counts as a match only when the source genuinely lacks it, or when the definition \
above asks for null.

Set matches=false when a value is wrong or incomplete.

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


class ModelOutputInvalid(Exception):
    """The reply is not an answer. The message names why and never quotes the output, which may hold requisites:
    a stop reason other than `end_turn`, `no_text_block` or `text_blocks=N`, or the schema error types and field
    paths."""


def _error_kinds(error: ValidationError) -> str:
    return ", ".join(f"{e['type']}@{'.'.join(map(str, e['loc']))}" if e["loc"] else e["type"]
                     for e in error.errors(include_url=False, include_context=False, include_input=False))


def _output_config(output_model: type[BaseModel], effort: str) -> dict:
    return {"effort": effort, "format": {"type": "json_schema", "schema": transform_schema(output_model)}}


def _read_reply(message: Message, output_model: type[T]) -> T:
    # `messages.parse` validates the text before the caller sees `stop_reason`, so a reply cut off or refused
    # mid-JSON would read as a schema error. We read the raw reply instead, stop reason first.
    if message.stop_reason != "end_turn":
        raise ModelOutputInvalid(str(message.stop_reason))
    texts = [block.text for block in message.content if block.type == "text"]
    if len(texts) != 1:
        raise ModelOutputInvalid("no_text_block" if not texts else f"text_blocks={len(texts)}")
    try:
        return output_model.model_validate_json(texts[0])
    except ValidationError as error:
        raise ModelOutputInvalid(_error_kinds(error)) from None  # a `ValidationError` quotes the output


async def _parse(source: Source, instruction: str, output_model: type[T], model: str, max_tokens: int,
                 effort: str) -> T:
    message = await _client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": _content_blocks(source, instruction)}],
        output_config=_output_config(output_model, effort),
    )
    return _read_reply(message, output_model)


async def gate(source: Source) -> GateVerdict:
    return await _parse(source, GATE_PROMPT, GateVerdict, model=GATE_MODEL, max_tokens=GATE_MAX_TOKENS,
                        effort=GATE_EFFORT)


async def extract(source: Source) -> ExtractedRequisites:
    return await _parse(source, EXTRACTION_PROMPT, ExtractedRequisites, model=_model, max_tokens=MAX_TOKENS,
                        effort=MODEL_EFFORT)


async def validate(source: Source, extracted: ExtractedRequisites) -> RequisitesVerdict:
    instruction = VALIDATION_PROMPT + extracted.model_dump_json(indent=2)
    return await _parse(source, instruction, RequisitesVerdict, model=_model, max_tokens=MAX_TOKENS, effort=MODEL_EFFORT)


async def extract_purpose(source: Source) -> PurposeFields:
    return await _parse(source, PURPOSE_PROMPT, PurposeFields, model=_model, max_tokens=MAX_TOKENS, effort=MODEL_EFFORT)


async def validate_purpose(source: Source, requisites: ExtractedRequisites, fields: PurposeFields) -> PurposeValidationVerdict:
    extracted = {"requisites": requisites.model_dump(include=set(RequisitesVerdict.model_fields)), "purpose": fields.model_dump()}
    instruction = PURPOSE_VALIDATION_PROMPT + json.dumps(extracted, ensure_ascii=False, indent=2)
    return await _parse(source, instruction, PurposeValidationVerdict, model=_model, max_tokens=MAX_TOKENS, effort=MODEL_EFFORT)
