import asyncio
import base64

import pytest

from app import llm, pipeline, texts
from app.llm import Source
from app.models import ExtractedRequisites, FieldVerdict, GateVerdict, ValidationVerdict

VALID_IBAN = "UA693000010000000012345678901"

GOOD_REQUISITES = ExtractedRequisites(
    recipient_name='ТОВ "ОРІОН-ПЛЮС"',
    iban=VALID_IBAN,
    edrpou_rnokpp="12345678",
    amount="13727",
    payment_purpose="Рахунок на оплату № 1024 від 30 червня 2026 р.",
)

TEXT_SOURCE = Source(kind="text", text="реквізити")

FIELD_NAMES = ["recipient_name", "iban", "edrpou_rnokpp", "amount", "payment_purpose"]


def stage_collector() -> tuple[list[str], pipeline.OnStage]:
    stages: list[str] = []

    async def on_stage(status: str) -> None:
        stages.append(status)

    return stages, on_stage


def verdict(all_match: bool) -> ValidationVerdict:
    return ValidationVerdict(
        fields=[FieldVerdict(field=name, matches=all_match) for name in FIELD_NAMES],
        all_match=all_match,
    )


class LlmStub:
    def __init__(self, monkeypatch, extracted: ExtractedRequisites, verdicts: list[ValidationVerdict],
                 gate_passes: bool = True):
        self.gate_calls = 0
        self.extract_calls = 0
        self.validate_calls = 0
        self._extracted = extracted
        self._verdicts = verdicts
        self._gate_passes = gate_passes

        async def fake_gate(source):
            self.gate_calls += 1
            return GateVerdict(contains_requisites=self._gate_passes)

        async def fake_extract(source):
            self.extract_calls += 1
            return self._extracted

        async def fake_validate(source, extracted):
            self.validate_calls += 1
            return self._verdicts[self.validate_calls - 1]

        monkeypatch.setattr(llm, "gate", fake_gate)
        monkeypatch.setattr(llm, "extract", fake_extract)
        monkeypatch.setattr(llm, "validate", fake_validate)


def test_happy_path_runs_each_llm_call_exactly_once(monkeypatch):
    stub = LlmStub(monkeypatch, GOOD_REQUISITES, [verdict(True)])
    result = asyncio.run(pipeline.process(TEXT_SOURCE))
    assert result.ok
    assert result.qr is not None
    assert result.warnings == []
    assert stub.gate_calls == 1
    assert stub.extract_calls == 1
    assert stub.validate_calls == 1


def test_gate_rejection_skips_extraction_entirely(monkeypatch):
    stub = LlmStub(monkeypatch, GOOD_REQUISITES, [verdict(True)], gate_passes=False)
    result = asyncio.run(pipeline.process(TEXT_SOURCE))
    assert not result.ok
    assert result.qr is None
    assert result.requisites is None
    assert result.error == texts.ERR_NOT_PAYMENT
    assert stub.gate_calls == 1
    assert stub.extract_calls == 0
    assert stub.validate_calls == 0


def test_first_mismatch_retries_extraction_once_and_succeeds(monkeypatch):
    stub = LlmStub(monkeypatch, GOOD_REQUISITES, [verdict(False), verdict(True)])
    result = asyncio.run(pipeline.process(TEXT_SOURCE))
    assert result.ok
    assert stub.extract_calls == 2
    assert stub.validate_calls == 2


def test_second_mismatch_fails_without_further_retries(monkeypatch):
    stub = LlmStub(monkeypatch, GOOD_REQUISITES, [verdict(False), verdict(False)])
    result = asyncio.run(pipeline.process(TEXT_SOURCE))
    assert not result.ok
    assert result.qr is None
    assert result.error == texts.ERR_UNRELIABLE
    assert stub.extract_calls == 2
    assert stub.validate_calls == 2


def test_single_field_mismatch_counts_as_mismatch_despite_all_match_flag(monkeypatch):
    lying_verdict = ValidationVerdict(
        fields=[FieldVerdict(field="iban", matches=False, corrected_value="UA00")],
        all_match=True,
    )
    stub = LlmStub(monkeypatch, GOOD_REQUISITES, [lying_verdict, verdict(True)])
    result = asyncio.run(pipeline.process(TEXT_SOURCE))
    assert result.ok
    assert stub.extract_calls == 2


def test_missing_iban_yields_error_and_no_qr(monkeypatch):
    requisites = GOOD_REQUISITES.model_copy(update={"iban": None})
    LlmStub(monkeypatch, requisites, [verdict(True)])
    result = asyncio.run(pipeline.process(TEXT_SOURCE))
    assert not result.ok
    assert result.qr is None
    assert result.error == texts.ERR_NO_IBAN
    assert result.requisites is requisites


def test_invalid_iban_checksum_yields_error_and_no_qr(monkeypatch):
    requisites = GOOD_REQUISITES.model_copy(update={"iban": "UA693000010000000012345678902"})
    LlmStub(monkeypatch, requisites, [verdict(True)])
    result = asyncio.run(pipeline.process(TEXT_SOURCE))
    assert not result.ok
    assert result.error == texts.ERR_NO_IBAN


def test_missing_amount_builds_qr_with_warning(monkeypatch):
    requisites = GOOD_REQUISITES.model_copy(update={"amount": None})
    LlmStub(monkeypatch, requisites, [verdict(True)])
    result = asyncio.run(pipeline.process(TEXT_SOURCE))
    assert result.ok
    assert result.warnings == [texts.WARN_NO_AMOUNT]


def test_unparseable_amount_builds_qr_with_warning(monkeypatch):
    requisites = GOOD_REQUISITES.model_copy(update={"amount": "тринадцять тисяч"})
    LlmStub(monkeypatch, requisites, [verdict(True)])
    result = asyncio.run(pipeline.process(TEXT_SOURCE))
    assert result.ok
    assert result.warnings == [texts.WARN_BAD_AMOUNT]


def test_missing_optional_fields_build_qr_with_warnings(monkeypatch):
    requisites = ExtractedRequisites(iban=VALID_IBAN)
    LlmStub(monkeypatch, requisites, [verdict(True)])
    result = asyncio.run(pipeline.process(TEXT_SOURCE))
    assert result.ok
    assert result.qr is not None
    assert set(result.warnings) == {texts.WARN_NO_AMOUNT, texts.WARN_NO_CODE, texts.WARN_NO_NAME,
                                    texts.WARN_NO_PURPOSE}


def test_invalid_code_shape_warns_but_builds_qr(monkeypatch):
    requisites = GOOD_REQUISITES.model_copy(update={"edrpou_rnokpp": "12345"})
    LlmStub(monkeypatch, requisites, [verdict(True)])
    result = asyncio.run(pipeline.process(TEXT_SOURCE))
    assert result.ok
    assert result.warnings == [texts.WARN_BAD_CODE]


def test_truncated_purpose_adds_warning(monkeypatch):
    requisites = GOOD_REQUISITES.model_copy(update={"payment_purpose": "оплата за рахунком " * 30})
    LlmStub(monkeypatch, requisites, [verdict(True)])
    result = asyncio.run(pipeline.process(TEXT_SOURCE))
    assert result.ok
    assert result.warnings == [texts.WARN_TRUNCATED_PURPOSE]
    assert result.qr.truncated_purpose


def test_happy_path_emits_stages_in_order_without_retry(monkeypatch):
    LlmStub(monkeypatch, GOOD_REQUISITES, [verdict(True)])
    stages, on_stage = stage_collector()
    result = asyncio.run(pipeline.process(TEXT_SOURCE, on_stage))
    assert result.ok
    assert stages == [texts.STATUS_SEARCHING, texts.STATUS_EXTRACTING, texts.STATUS_VALIDATING]


def test_gate_rejection_emits_only_searching_stage(monkeypatch):
    LlmStub(monkeypatch, GOOD_REQUISITES, [verdict(True)], gate_passes=False)
    stages, on_stage = stage_collector()
    asyncio.run(pipeline.process(TEXT_SOURCE, on_stage))
    assert stages == [texts.STATUS_SEARCHING]


def test_retry_emits_retrying_stage_exactly_once(monkeypatch):
    LlmStub(monkeypatch, GOOD_REQUISITES, [verdict(False), verdict(False)])
    stages, on_stage = stage_collector()
    asyncio.run(pipeline.process(TEXT_SOURCE, on_stage))
    assert stages == [texts.STATUS_SEARCHING, texts.STATUS_EXTRACTING, texts.STATUS_VALIDATING, texts.STATUS_RETRYING]


@pytest.mark.parametrize("raw_iban", ["ua69 3000 0100 0000 0012 3456 78901", " UA693000010000000012345678901 "])
def test_iban_normalized_before_qr(monkeypatch, raw_iban):
    requisites = GOOD_REQUISITES.model_copy(update={"iban": raw_iban})
    LlmStub(monkeypatch, requisites, [verdict(True)])
    result = asyncio.run(pipeline.process(TEXT_SOURCE))
    assert result.ok
    b64 = result.qr.url.removeprefix("https://bank.gov.ua/qr/")
    payload = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)).decode("cp1251")
    assert payload.split("\n")[6] == VALID_IBAN
