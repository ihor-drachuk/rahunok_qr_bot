import asyncio
import base64
import json
import traceback

import anthropic
import httpx
import pytest

from app import llm
from app.llm import Source, _content_blocks
from app.models import ExtractedRequisites, GateVerdict, ValidationVerdict

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


def test_gate_runs_on_haiku_at_low_effort(api):
    answers, bodies = api
    answers.append(('{"contains_requisites": true}', "end_turn"))
    assert asyncio.run(llm.gate(Source(kind="text", text="IBAN UA00"))) == GateVerdict(contains_requisites=True)
    [body] = bodies
    assert (body["model"], body["max_tokens"]) == ("claude-haiku-5-5", 1024)
    assert body["output_config"] == _output_config(GateVerdict, "low")


def test_extract_and_validate_run_on_configured_model_at_high_effort(api):
    answers, bodies = api
    verdict = ValidationVerdict(fields=[], all_match=True)
    answers += [('{"iban": "UA00"}', "end_turn"), (verdict.model_dump_json(), "end_turn")]
    source = Source(kind="text", text="IBAN UA00")
    extracted = asyncio.run(llm.extract(source))
    assert extracted == ExtractedRequisites(iban="UA00")
    assert asyncio.run(llm.validate(source, extracted)) == verdict
    assert [(b["model"], b["max_tokens"], b["output_config"]) for b in bodies] == [
        ("claude-sonnet-5-5", 16000, _output_config(ExtractedRequisites, "high")),
        ("claude-sonnet-5-5", 16000, _output_config(ValidationVerdict, "high")),
    ]


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
