import base64
from decimal import Decimal

from nbu_payment_qr import NBU_QR_PREFIX, QrStyle, build_nbu_qr
from qrcode.constants import ERROR_CORRECT_H
from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer, SquareModuleDrawer

from app.pipeline import RAHUNOK_STYLE

POC_EXPECTED_PAYLOAD = (
    "BCD\n002\n2\nUCT\n\n"
    'ТОВ "ОРІОН-ПЛЮС"\n'
    "UA693000010000000012345678901\n"
    "UAH13727\n"
    "12345678\n\n\n"
    "Рахунок на оплату № 1024 від 30 червня 2026 р.\n\n"
)


def decode_payload(url: str) -> str:
    b64 = url.removeprefix(NBU_QR_PREFIX)
    padded = b64 + "=" * (-len(b64) % 4)
    return base64.urlsafe_b64decode(padded).decode("cp1251")


def test_poc_example_payload_matches_reference():
    result = build_nbu_qr(name='ТОВ "ОРІОН-ПЛЮС"', iban="UA693000010000000012345678901", amount=Decimal("13727"),
                          code="12345678", purpose="Рахунок на оплату № 1024 від 30 червня 2026 р.",
                          style=RAHUNOK_STYLE)
    assert decode_payload(result.url) == POC_EXPECTED_PAYLOAD
    assert not result.truncated_purpose


def test_style_matches_bot_look_and_survives_centered_logo():
    assert RAHUNOK_STYLE == QrStyle(error_correction=ERROR_CORRECT_H, center_color=(30, 90, 168),
                                    edge_color=(18, 130, 120), back_color=(255, 255, 255), box_size=10, border=4,
                                    module_drawer=RoundedModuleDrawer, eye_drawer=SquareModuleDrawer)
