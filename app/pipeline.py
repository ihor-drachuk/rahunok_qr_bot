"""Message processing pipeline: gate -> extract -> validate -> retry once -> local checks -> QR."""

from dataclasses import dataclass, field

from app import llm, texts
from app.card import CardText
from app.llm import Source
from app.models import ExtractedRequisites, ValidationVerdict
from app.qr import QrResult, build_nbu_qr
from app.validation import classify_code, is_valid_iban, normalize_code, normalize_iban, parse_amount


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


async def process(source: Source) -> PipelineResult:
    gate_verdict = await llm.gate(source)
    if not gate_verdict.contains_requisites:
        return PipelineResult(ok=False, error=texts.ERR_NOT_PAYMENT)

    extracted = await llm.extract(source)
    verdict = await llm.validate(source, extracted)
    if not _matches(verdict):
        extracted = await llm.extract(source)
        verdict = await llm.validate(source, extracted)
        if not _matches(verdict):
            return PipelineResult(ok=False, requisites=extracted, error=texts.ERR_UNRELIABLE)

    iban = normalize_iban(extracted.iban)
    if iban is None or not is_valid_iban(iban):
        return PipelineResult(ok=False, requisites=extracted, error=texts.ERR_NO_IBAN)

    warnings: list[str] = []
    amount = parse_amount(extracted.amount)
    if amount is None:
        warnings.append(texts.WARN_BAD_AMOUNT if extracted.amount else texts.WARN_NO_AMOUNT)

    code = normalize_code(extracted.edrpou_rnokpp)
    code_kind = classify_code(code)
    if code_kind == "empty":
        warnings.append(texts.WARN_NO_CODE)
    elif code_kind == "invalid":
        warnings.append(texts.WARN_BAD_CODE)

    if not extracted.recipient_name:
        warnings.append(texts.WARN_NO_NAME)
    if not extracted.payment_purpose:
        warnings.append(texts.WARN_NO_PURPOSE)

    qr = build_nbu_qr(extracted.recipient_name, iban, amount, code, extracted.payment_purpose)
    if qr.truncated_purpose:
        warnings.append(texts.WARN_TRUNCATED_PURPOSE)

    card = CardText(
        subtitle=texts.CARD_SUBTITLE,
        call_to_action=texts.CARD_CALL_TO_ACTION,
        recipient=extracted.recipient_name,
        amount=texts.format_card_amount(amount),
    )
    return PipelineResult(ok=True, qr=qr, card=card, requisites=extracted, warnings=warnings)
