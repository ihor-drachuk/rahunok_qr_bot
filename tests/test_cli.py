import asyncio
import base64
from types import SimpleNamespace

import pytest

from app import cli, llm, pipeline
from app.card import CardText
from app.cli import _build_parser, _build_source, _decoded_payload_lines
from app.models import ExtractedRequisites
from app.pipeline import PipelineResult
from app.qr import NBU_QR_PREFIX, build_nbu_qr

VALID_IBAN = "UA693000010000000012345678901"


def _args(file=None, text=None, model=None, qr_out=None, card_out=None, stage=None):
    return SimpleNamespace(file=file, text=text, model=model, qr_out=qr_out, card_out=card_out, stage=stage)


def test_text_source(tmp_path):
    src = _build_source(_args(text="реквізити"))
    assert src.kind == "text"
    assert src.text == "реквізити"


def test_pdf_source(tmp_path):
    p = tmp_path / "invoice.PDF"
    p.write_bytes(b"%PDF-1.4 data")
    src = _build_source(_args(file=str(p)))
    assert src.kind == "pdf"
    assert src.data == b"%PDF-1.4 data"
    assert src.media_type is None


@pytest.mark.parametrize("name,media_type", [
    ("shot.png", "image/png"),
    ("shot.PNG", "image/png"),
    ("shot.jpg", "image/jpeg"),
    ("shot.jpeg", "image/jpeg"),
])
def test_image_source(tmp_path, name, media_type):
    p = tmp_path / name
    p.write_bytes(b"\x89PNG")
    src = _build_source(_args(file=str(p)))
    assert src.kind == "image"
    assert src.media_type == media_type
    assert src.data == b"\x89PNG"


def test_unsupported_type_exits(tmp_path):
    p = tmp_path / "doc.docx"
    p.write_bytes(b"x")
    with pytest.raises(SystemExit):
        _build_source(_args(file=str(p)))


def test_parser_stage_flag_defaults_to_none_so_env_decides():
    parser = _build_parser()
    assert parser.parse_args(["--text", "x"]).stage is None
    assert parser.parse_args(["--text", "x", "--stage"]).stage is True


def _stub_pipeline(monkeypatch):
    monkeypatch.setenv("RAHUNOK_QR_BOT_TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("RAHUNOK_QR_BOT_ANTHROPIC_API_KEY", "key")
    monkeypatch.setenv("RAHUNOK_QR_BOT_STAGE", "0")
    monkeypatch.setattr(llm, "init", lambda cfg: None)
    qr = build_nbu_qr("ТОВ", VALID_IBAN, None, None, "оплата")
    card_text = CardText(subtitle="s", call_to_action="c", recipient="ТОВ", amount=None)
    result = PipelineResult(ok=True, qr=qr, card=card_text, requisites=ExtractedRequisites(iban=VALID_IBAN))

    async def fake_process(source, on_stage=None):
        return result

    monkeypatch.setattr(pipeline, "process", fake_process)


def test_run_card_out_renders_card_and_stage_flag_beats_env(monkeypatch, tmp_path):
    _stub_pipeline(monkeypatch)
    plain_path, staged_path = tmp_path / "plain.png", tmp_path / "staged.png"
    asyncio.run(cli._run(_args(text="x", card_out=str(plain_path))))
    asyncio.run(cli._run(_args(text="x", card_out=str(staged_path), stage=True)))
    assert plain_path.read_bytes().startswith(b"\x89PNG")
    assert staged_path.read_bytes().startswith(b"\x89PNG")
    assert staged_path.read_bytes() != plain_path.read_bytes()  # --stage overrode RAHUNOK_QR_BOT_STAGE=0


def test_decoded_payload_round_trip():
    payload = "BCD\n002\n2\nUCT\n\nТОВ «ТЕСТ»\nUA69\n\n\n\n\nоплата\n\n"
    b64 = base64.urlsafe_b64encode(payload.encode("cp1251")).decode("ascii").rstrip("=")
    lines = _decoded_payload_lines(NBU_QR_PREFIX + b64)
    assert lines == payload.split("\n")
