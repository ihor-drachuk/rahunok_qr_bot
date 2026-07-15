import pytest
from PIL import Image, ImageChops

from app import card
from app.card import CardText, build_card

QR = Image.new("RGBA", (100, 100), (255, 255, 255, 255))


def full_text() -> CardText:
    return CardText(subtitle="Telegram-бот", call_to_action="Скануй", recipient='ТОВ «ТЕСТ»', amount="1.00 грн")


def test_build_card_produces_png():
    png = build_card(QR, full_text())
    assert png.startswith(b"\x89PNG")


def test_card_without_recipient_and_amount_still_renders():
    text = CardText(subtitle="s", call_to_action="c", recipient=None, amount=None)
    assert build_card(QR, text).startswith(b"\x89PNG")


def test_card_with_only_amount_renders():
    text = CardText(subtitle="s", call_to_action="c", recipient=None, amount="5.00 грн")
    assert build_card(QR, text).startswith(b"\x89PNG")


def test_card_with_only_recipient_renders():
    text = CardText(subtitle="s", call_to_action="c", recipient="ТОВ", amount=None)
    assert build_card(QR, text).startswith(b"\x89PNG")


def test_stage_mode_changes_rendered_card():
    assert build_card(QR, full_text(), stage_mode=True) != build_card(QR, full_text())


@pytest.mark.parametrize("load_logo", [card._logo_header, card._logo_center])
def test_stage_hammer_overlay_confined_to_its_box(load_logo):
    plain, staged = load_logo(False), load_logo(True)
    bbox = ImageChops.difference(plain, staged).getbbox()
    assert bbox is not None  # the overlay visibly changes the logo
    scale = plain.width / card._LOGO_DESIGN_SIDE
    left, top = round(card._HAMMER_LEFT * scale), round(card._HAMMER_TOP * scale)
    side = round(card._HAMMER_SIDE * scale)
    assert bbox[0] >= left and bbox[1] >= top and bbox[2] <= left + side and bbox[3] <= top + side


def test_stage_title_hammer_suffix_widens_header():
    title, subtitle = card._manrope(25, 800), card._manrope(13, 600)
    handle = card._mono(13, semibold=False)
    plain = card._render_header_text(1500 * card._S, "s", title, subtitle, handle, stage_mode=False)
    staged = card._render_header_text(1500 * card._S, "s", title, subtitle, handle, stage_mode=True)
    assert staged.width > plain.width
    assert staged.height == plain.height


def test_pill_absent_shrinks_card_height():
    with_pill = Image.open(_as_stream(build_card(QR, full_text())))
    without_pill = Image.open(_as_stream(build_card(QR, CardText("s", "c", None, None))))
    assert without_pill.height < with_pill.height
    assert with_pill.width == without_pill.width


def _as_stream(data: bytes):
    import io
    return io.BytesIO(data)
