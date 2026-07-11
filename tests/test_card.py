from PIL import Image

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


def test_pill_absent_shrinks_card_height():
    with_pill = Image.open(_as_stream(build_card(QR, full_text())))
    without_pill = Image.open(_as_stream(build_card(QR, CardText("s", "c", None, None))))
    assert without_pill.height < with_pill.height
    assert with_pill.width == without_pill.width


def _as_stream(data: bytes):
    import io
    return io.BytesIO(data)
