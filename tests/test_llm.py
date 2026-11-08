import asyncio
import base64
import json
import traceback

import anthropic
import httpx
import pytest

from app import llm
from app.llm import Source, _content_blocks
from app.models import (Checks, ExtractedRequisites, GateVerdict, PurposeFields, PurposeValidationVerdict,
                        PurposeVerdict, RequisitesVerdict)
from tests.test_pipeline import checks

PDF_BYTES = b"%PDF-1.4 fake"
PNG_BYTES = b"\x89PNG fake"


def test_pdf_source_builds_document_block_before_text():
    blocks = _content_blocks(Source(kind="pdf", data=PDF_BYTES), "instruction")
    assert [b["type"] for b in blocks] == ["document", "text"]
    assert blocks[0]["source"] == {
        "type": "base64",
        "media_type": "application/pdf",
        "data": base64.standard_b64encode(PDF_BYTES).decode("ascii"),
    }
    assert blocks[1]["text"] == "instruction"


def test_image_source_builds_image_block_with_media_type_passthrough():
    blocks = _content_blocks(Source(kind="image", data=PNG_BYTES, media_type="image/png"), "instruction")
    assert [b["type"] for b in blocks] == ["image", "text"]
    assert blocks[0]["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": base64.standard_b64encode(PNG_BYTES).decode("ascii"),
    }


def test_text_source_folds_user_text_into_single_text_block():
    blocks = _content_blocks(Source(kind="text", text="ІБАН UA12..."), "instruction")
    assert [b["type"] for b in blocks] == ["text"]
    assert blocks[0]["text"] == "instruction\n\n<document>\nІБАН UA12...\n</document>"


@pytest.fixture
def api(monkeypatch) -> tuple[list[tuple[str | None, str]], list[dict]]:
    """The real SDK over a scripted transport. Each request takes the next (text, stop_reason) pair from the first
    list, a None text sending no text block and a list sending those content blocks; the second list collects the
    request bodies."""
    answers: list[tuple[str | list | None, str]] = []
    bodies: list[dict] = []

    def answer(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        text, stop_reason = answers.pop(0)
        content = [] if text is None else text if isinstance(text, list) else [{"type": "text", "text": text}]
        return httpx.Response(200, json={"id": "msg_1", "type": "message", "role": "assistant", "model": "m",
                                         "content": content, "stop_reason": stop_reason, "stop_sequence": None,
                                         "usage": {"input_tokens": 1, "output_tokens": 1}})

    client = anthropic.AsyncAnthropic(api_key="x", http_client=httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    monkeypatch.setattr(llm, "_client", client)
    monkeypatch.setattr(llm, "_model", "claude-sonnet-5-5")
    return answers, bodies


def _output_config(model, effort):
    return {"effort": effort, "format": {"type": "json_schema", "schema": anthropic.transform_schema(model)}}


@pytest.mark.parametrize("model", Checks.__subclasses__())
def test_verdict_schema_requires_every_field(model):
    schema = anthropic.transform_schema(model)
    assert schema["required"] == list(model.model_fields)
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["FieldCheck"]["required"] == ["matches"]
    assert all(field == {"$ref": "#/$defs/FieldCheck"} for field in schema["properties"].values())


def test_purpose_verdict_schema_requires_both_groups():
    schema = anthropic.transform_schema(PurposeValidationVerdict)
    assert schema["required"] == ["requisites", "purpose"]
    assert schema["properties"] == {"requisites": {"$ref": "#/$defs/RequisitesVerdict"},
                                    "purpose": {"$ref": "#/$defs/PurposeVerdict"}}


def test_purpose_verdict_reply_missing_field_is_not_answer(api):
    answers, _ = api
    reply = PurposeValidationVerdict(requisites=checks(RequisitesVerdict), purpose=checks(PurposeVerdict)).model_dump()
    del reply["purpose"]["item_name"]
    answers.append((json.dumps(reply), "end_turn"))
    fields = PurposeFields(invoice_number="00007", goods_kind="goods")
    with pytest.raises(llm.ModelOutputInvalid) as raised:
        asyncio.run(llm.validate_purpose(Source(kind="text", text="Рахунок № 00007"), ExtractedRequisites(iban="UA00"), fields))
    assert str(raised.value) == "missing@purpose.item_name"


def test_verdicts_check_every_extracted_field_except_invoice_reference_and_notes():
    assert set(ExtractedRequisites.model_fields) - set(RequisitesVerdict.model_fields) == {"has_invoice_reference", "notes"}
    assert set(RequisitesVerdict.model_fields) <= set(ExtractedRequisites.model_fields)
    assert set(PurposeVerdict.model_fields) == set(PurposeFields.model_fields)


def test_verdict_reply_missing_field_is_not_answer(api):
    answers, _ = api
    reply = checks(RequisitesVerdict).model_dump()
    del reply["payment_purpose"]
    answers.append((json.dumps(reply), "end_turn"))
    with pytest.raises(llm.ModelOutputInvalid) as raised:
        asyncio.run(llm.validate(Source(kind="text", text="IBAN UA00"), ExtractedRequisites(iban="UA00")))
    assert str(raised.value) == "missing@payment_purpose"


def test_gate_runs_on_haiku_at_low_effort(api):
    answers, bodies = api
    answers.append(('{"contains_requisites": true}', "end_turn"))
    assert asyncio.run(llm.gate(Source(kind="text", text="IBAN UA00"))) == GateVerdict(contains_requisites=True)
    [body] = bodies
    assert (body["model"], body["max_tokens"]) == ("claude-haiku-5-5", 1024)
    assert body["output_config"] == _output_config(GateVerdict, "low")


def test_extract_and_validate_run_on_configured_model_at_high_effort(api):
    answers, bodies = api
    verdict = checks(RequisitesVerdict)
    answers += [('{"iban": "UA00"}', "end_turn"), (verdict.model_dump_json(), "end_turn")]
    source = Source(kind="text", text="IBAN UA00")
    extracted = asyncio.run(llm.extract(source))
    assert extracted == ExtractedRequisites(iban="UA00")
    assert asyncio.run(llm.validate(source, extracted)) == verdict
    assert [(b["model"], b["max_tokens"], b["output_config"]) for b in bodies] == [
        ("claude-sonnet-5-5", 16000, _output_config(ExtractedRequisites, "high")),
        ("claude-sonnet-5-5", 16000, _output_config(RequisitesVerdict, "high")),
    ]


def test_purpose_calls_run_on_configured_model_and_validate_only_requisite_fields(api):
    answers, bodies = api
    fields = PurposeFields(invoice_number="00007", invoice_date="12.10.2026", goods_kind="goods")
    verdict = PurposeValidationVerdict(requisites=checks(RequisitesVerdict), purpose=checks(PurposeVerdict))
    answers += [(fields.model_dump_json(), "end_turn"), (verdict.model_dump_json(), "end_turn")]
    source = Source(kind="text", text="Рахунок № 00007 від 12.10.2026")
    requisites = ExtractedRequisites(iban="UA00", has_invoice_reference=True, notes="примітка")
    assert asyncio.run(llm.extract_purpose(source)) == fields
    assert asyncio.run(llm.validate_purpose(source, requisites, fields)) == verdict
    assert [(b["model"], b["output_config"]) for b in bodies] == [
        ("claude-sonnet-5-5", _output_config(PurposeFields, "high")),
        ("claude-sonnet-5-5", _output_config(PurposeValidationVerdict, "high")),
    ]
    instruction = bodies[1]["messages"][0]["content"][0]["text"]
    extracted = json.loads(instruction.removeprefix(llm.PURPOSE_VALIDATION_PROMPT).split("\n\n<document>")[0])
    assert extracted == {"requisites": {"recipient_name": None, "iban": "UA00", "edrpou_rnokpp": None, "amount": None,
                                        "payment_purpose": None},
                         "purpose": fields.model_dump()}


PARTIAL = '{"recipient_name": "ТОВ Ромашка", "iban": "UA7232'
FULL = '{"recipient_name": "ТОВ Ромашка", "iban": "UA723204780000026006000255925"}'
THINKING = {"type": "thinking", "thinking": "The recipient is ...", "signature": "sig"}


def test_reply_with_thinking_before_text_is_read(api):
    answers, _ = api
    answers.append(([THINKING, {"type": "text", "text": FULL}], "end_turn"))
    assert asyncio.run(llm.extract(Source(kind="text", text="IBAN UA00"))).iban == "UA723204780000026006000255925"


@pytest.mark.parametrize("text, stop_reason, kind", [
    (None, "end_turn", "no_text_block"),
    ([THINKING], "end_turn", "no_text_block"),
    ([{"type": "text", "text": FULL}] * 2, "end_turn", "text_blocks=2"),
    (None, "max_tokens", "max_tokens"),
    ([THINKING], "max_tokens", "max_tokens"),
    (PARTIAL, "max_tokens", "max_tokens"),
    (PARTIAL, "refusal", "refusal"),
    (FULL, "refusal", "refusal"),
    (FULL, "stop_sequence", "stop_sequence"),
    (PARTIAL, "end_turn", "json_invalid"),
    ('{"iban": 7}', "end_turn", "string_type@iban"),
])
def test_reply_that_is_not_answer_raises_its_cause_without_output(api, text, stop_reason, kind):
    answers, _ = api
    answers.append((text, stop_reason))
    with pytest.raises(llm.ModelOutputInvalid) as raised:
        asyncio.run(llm.extract(Source(kind="text", text="IBAN UA00")))
    assert str(raised.value) == kind
    logged = "".join(traceback.format_exception(raised.value))
    assert "UA7232" not in logged and "Ромашка" not in logged
