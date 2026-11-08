"""Message processing pipeline: gate -> extract -> validate -> retry once -> local checks -> QR,
and the payment purpose made on request for a result that has none."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from nbu_payment_qr import (
    MAX_AMOUNT,
    PayloadOverflowError,
    QrResult,
    QrStyle,
    build_nbu_qr,
    classify_code,
    is_valid_iban,
    normalize_code,
    normalize_iban,
    parse_amount,
)
from qrcode.constants import ERROR_CORRECT_H

from app import llm, texts
from app.card import CardText
from app.llm import Source
from app.models import FIELD_NAMES, PURPOSE_FIELD_NAMES, ExtractedRequisites, FieldVerdict, PurposeValidationVerdict, ValidationVerdict

# Blue to teal radial gradient. Error correction H keeps the QR readable under the card's centered logo.
RAHUNOK_STYLE = QrStyle(error_correction=ERROR_CORRECT_H, center_color=(30, 90, 168), edge_color=(18, 130, 120))


@dataclass
class PipelineResult:
    ok: bool
    qr: QrResult | None = None
    card: CardText | None = None
    requisites: ExtractedRequisites | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def _matches(verdict: ValidationVerdict) -> bool:
    return verdict.all_match and all(f.matches for f in verdict.fields)


OnStage = Callable[[str], Awaitable[None]]


async def process(source: Source, on_stage: OnStage | None = None) -> PipelineResult:
    async def stage(status: str) -> None:
        if on_stage is not None:
            await on_stage(status)

    await stage(texts.status_searching())
    gate_verdict = await llm.gate(source)
    if not gate_verdict.contains_requisites:
        return PipelineResult(ok=False, error=texts.ERR_NOT_PAYMENT)

    await stage(texts.status_extracting())
    extracted = await llm.extract(source)
    await stage(texts.status_validating())
    verdict = await llm.validate(source, extracted)
    if not _matches(verdict):
        await stage(texts.status_retrying())
        extracted = await llm.extract(source)
        verdict = await llm.validate(source, extracted)
        if not _matches(verdict):
            return PipelineResult(ok=False, requisites=extracted, error=texts.ERR_UNRELIABLE)
    return _build(extracted)


def _all_fields_match(verdicts: list[FieldVerdict], names: tuple[str, ...]) -> bool:
    # We count a field that a verdict leaves out as a mismatch, because the verdict has not checked it.
    return all(v.matches for v in verdicts) and {v.field for v in verdicts} >= set(names)


def _purpose_mismatch(verdict: PurposeValidationVerdict) -> str | None:
    if not _all_fields_match(verdict.requisites, FIELD_NAMES):
        return texts.ERR_REQUISITES_CHANGED
    if not _all_fields_match(verdict.purpose, PURPOSE_FIELD_NAMES):
        return texts.ERR_PURPOSE_UNRELIABLE
    return None


async def add_purpose(source: Source, requisites: ExtractedRequisites) -> PipelineResult:
    """Builds the result for `requisites` with a payment purpose made from the invoice number or date in `source`.
    We check `requisites` against `source` again, because the user may have edited the source since the card was made."""
    for _ in range(2):
        fields = await llm.extract_purpose(source)
        error = _purpose_mismatch(await llm.validate_purpose(source, requisites, fields))
        if error is None:
            if not fields.invoice_number and not fields.invoice_date:
                return PipelineResult(ok=False, error=texts.ERR_NO_INVOICE_REFERENCE)
            return _build(requisites.model_copy(update={"payment_purpose": texts.format_generated_purpose(fields)}))
    return PipelineResult(ok=False, error=error)


def _build(extracted: ExtractedRequisites) -> PipelineResult:
    iban = normalize_iban(extracted.iban)
    if iban is None or not is_valid_iban(iban):
        return PipelineResult(ok=False, requisites=extracted, error=texts.ERR_NO_IBAN)

    warnings: list[str] = []
    amount = parse_amount(extracted.amount)
    if amount is not None and amount > MAX_AMOUNT:
        amount = None
        warnings.append(texts.WARN_AMOUNT_TOO_LARGE)
    elif amount is None:
        warnings.append(texts.WARN_BAD_AMOUNT if extracted.amount else texts.WARN_NO_AMOUNT)

    code = normalize_code(extracted.edrpou_rnokpp)
    code_kind = classify_code(code)
    if code_kind == "empty":
        warnings.append(texts.WARN_NO_CODE)
    elif code_kind == "invalid":
        warnings.append(texts.WARN_BAD_CODE)
        code = None

    if not extracted.recipient_name:
        warnings.append(texts.WARN_NO_NAME)
    if not extracted.payment_purpose:
        warnings.append(texts.WARN_NO_PURPOSE)

    try:
        qr = build_nbu_qr(name=extracted.recipient_name, iban=iban, amount=amount, code=code,
                          purpose=extracted.payment_purpose, style=RAHUNOK_STYLE)
    except PayloadOverflowError:  # IBAN, amount and code are validated above, so only the name can overflow
        return PipelineResult(ok=False, requisites=extracted, error=texts.ERR_NAME_TOO_LONG)
    if qr.truncated_purpose:
        warnings.append(texts.WARN_TRUNCATED_PURPOSE)

    card = CardText(
        subtitle=texts.CARD_SUBTITLE,
        call_to_action=texts.CARD_CALL_TO_ACTION,
        recipient=extracted.recipient_name,
        amount=texts.format_card_amount(amount),
    )
    return PipelineResult(ok=True, qr=qr, card=card, requisites=extracted, warnings=warnings)
