import base64

from app.llm import Source, _content_blocks

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
