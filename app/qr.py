"""NBU payment QR builder. Pure functions, no I/O."""

import base64
import io
import re
from dataclasses import dataclass
from decimal import Decimal

import qrcode
from PIL import Image
from qrcode.constants import ERROR_CORRECT_H
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.colormasks import RadialGradiantColorMask
from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer, SquareModuleDrawer

NBU_QR_PREFIX = "https://bank.gov.ua/qr/"
MONO_QR_PREFIX = "https://send.monobank.ua/qr/"  # same payload; opens the payment straight in monobank
MAX_PAYLOAD_BYTES = 331  # NBU limit on the encoded payload size

# "ocean" style: rounded modules with a radial blue->teal gradient, square finder eyes.
_GRADIENT_CENTER = (30, 90, 168)  # blue
_GRADIENT_EDGE = (18, 130, 120)  # teal


@dataclass(frozen=True)
class QrResult:
    image: Image.Image  # RGBA QR without any logo/caption overlay
    url: str  # https://bank.gov.ua/qr/... — the value encoded in the QR; opens in any supporting bank app
    truncated_purpose: bool

    @property
    def mono_url(self) -> str:
        # Same base64 payload on monobank's host, so the link targets monobank directly.
        return MONO_QR_PREFIX + self.url.removeprefix(NBU_QR_PREFIX)

    @property
    def png(self) -> bytes:
        buffer = io.BytesIO()
        self.image.convert("RGB").save(buffer, format="PNG")
        return buffer.getvalue()


def to_cp1251_bytes(text: str) -> bytes:
    return text.encode("cp1251", errors="replace")  # unmappable chars become '?', as in the reference


def format_amount(amount: Decimal | None) -> str:
    if amount is None:
        return ""
    if amount == amount.to_integral_value():
        return f"UAH{int(amount)}"
    return f"UAH{amount:.2f}"


def _payload(name: str, iban: str, amount_line: str, code: str, purpose: str) -> str:
    # 14 lines in the fixed NBU order; empty fields stay as empty lines.
    lines = ["BCD", "002", "2", "UCT", "", name, iban, amount_line, code, "", "", purpose, "", ""]
    return "\n".join(lines)


def _single_line(text: str) -> str:
    # Banking apps read the payload positionally, so a field value must not introduce extra lines.
    return re.sub(r"\s+", " ", text).strip()


def build_nbu_qr(name: str | None, iban: str, amount: Decimal | None, code: str | None,
                 purpose: str | None) -> QrResult:
    name = _single_line(name or "")
    code = _single_line(code or "")
    purpose = _single_line(purpose or "")
    amount_line = format_amount(amount)

    # CP1251 is a single-byte encoding (replacement '?' included), so encoded size == character count.
    base_bytes = len(to_cp1251_bytes(_payload(name, iban, amount_line, code, "")))
    purpose_budget = max(0, MAX_PAYLOAD_BYTES - base_bytes)
    truncated = len(purpose) > purpose_budget
    if truncated:
        purpose = purpose[:purpose_budget]

    payload = _payload(name, iban, amount_line, code, purpose)
    b64 = base64.urlsafe_b64encode(to_cp1251_bytes(payload)).decode("ascii").rstrip("=")
    url = NBU_QR_PREFIX + b64

    # Error correction H (30% redundancy) so the centered logo can safely cover part of the modules.
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(
        image_factory=StyledPilImage,
        module_drawer=RoundedModuleDrawer(),
        eye_drawer=SquareModuleDrawer(),
        color_mask=RadialGradiantColorMask(back_color=(255, 255, 255),
                                           center_color=_GRADIENT_CENTER, edge_color=_GRADIENT_EDGE),
    ).convert("RGBA")
    return QrResult(image=image, url=url, truncated_purpose=truncated)
