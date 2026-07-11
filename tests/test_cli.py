import base64
from types import SimpleNamespace

import pytest

from app.cli import _build_source, _decoded_payload_lines
from app.qr import NBU_QR_PREFIX


def _args(file=None, text=None):
    return SimpleNamespace(file=file, text=text)


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


def test_decoded_payload_round_trip():
    payload = "BCD\n002\n2\nUCT\n\nТОВ «ТЕСТ»\nUA69\n\n\n\n\nоплата\n\n"
    b64 = base64.urlsafe_b64encode(payload.encode("cp1251")).decode("ascii").rstrip("=")
    lines = _decoded_payload_lines(NBU_QR_PREFIX + b64)
    assert lines == payload.split("\n")
