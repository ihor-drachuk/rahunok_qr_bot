"""Renders the "5C" reply card around a QR image (dark branded card, flag accents). Pure, no I/O."""

import io
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
_LOGO_HEADER_PATH = _ASSETS / "logo-header.png"  # thin single ring, for the header
_LOGO_CENTER_PATH = _ASSETS / "logo-center.png"  # ring + white gap, sits over the QR modules
_MANROPE_PATH = _ASSETS / "fonts" / "Manrope.ttf"
_MONO_MEDIUM_PATH = _ASSETS / "fonts" / "IBMPlexMono-Medium.ttf"
_MONO_SEMIBOLD_PATH = _ASSETS / "fonts" / "IBMPlexMono-SemiBold.ttf"

_S = 3  # supersampling factor; all design pixels below are 1x, multiplied by _S at render time

# Colors from the 5C design.
_CARD_BG = (12, 39, 51)
_WHITE = (255, 255, 255)
_TITLE = (255, 255, 255)
_SUBTITLE = (143, 182, 194)  # #8fb6c2
_HANDLE = (111, 198, 224)  # #6fc6e0, the @nickname line
_CALL_TEXT = (234, 244, 247)
_FLAG_BLUE = (0, 91, 187)
_FLAG_YELLOW = (255, 213, 0)
_PILL_BG = (255, 255, 255, 14)  # rgba(255,255,255,.055)
_PILL_DIVIDER = (255, 255, 255, 41)  # rgba(255,255,255,.16)
_PILL_NAME = (157, 184, 194)
_PILL_AMOUNT = (255, 213, 0)

_CARD_W = 520
_PAD = 40
_ACCENT_H = 5
_HEADER_GAP = 16
_QR_PANEL_RADIUS = 22
_QR_PANEL_PAD = 22
_CENTER_LOGO_FRACTION = 0.27  # ringed-logo diameter as a fraction of the QR panel inner size

_BRAND_TITLE = "Рахунок → QR"
_BRAND_HANDLE = "@rahunok_qr_bot"


@dataclass(frozen=True)
class CardText:
    subtitle: str
    call_to_action: str
    recipient: str | None
    amount: str | None


@lru_cache(maxsize=8)
def _font(path: str, size: int, weight: int | None) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(path, size)
    if weight is not None:
        font.set_variation_by_axes([weight])
    return font


def _manrope(size: int, weight: int) -> ImageFont.FreeTypeFont:
    return _font(str(_MANROPE_PATH), size * _S, weight)


def _mono(size: int, semibold: bool) -> ImageFont.FreeTypeFont:
    path = _MONO_SEMIBOLD_PATH if semibold else _MONO_MEDIUM_PATH
    return _font(str(path), size * _S, None)


@lru_cache(maxsize=1)
def _logo_header() -> Image.Image:
    return Image.open(_LOGO_HEADER_PATH).convert("RGBA")


@lru_cache(maxsize=1)
def _logo_center() -> Image.Image:
    return Image.open(_LOGO_CENTER_PATH).convert("RGBA")


def _flag_bar(width: int, height: int) -> Image.Image:
    # Left half blue, right half yellow, with fully rounded ends.
    bar = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(bar)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=height // 2, fill=_FLAG_YELLOW)
    draw.rounded_rectangle((0, 0, width // 2 + height, height - 1), radius=height // 2, fill=_FLAG_BLUE)
    draw.rectangle((height // 2, 0, width // 2, height - 1), fill=_FLAG_BLUE)
    return bar


def _center_logo(panel_inner: int) -> Image.Image:
    # The ringed logo asset (flag ring + white gap + logo) is inserted as-is, sized to the QR panel.
    logo_d = round(panel_inner * _CENTER_LOGO_FRACTION)
    return _logo_center().resize((logo_d, logo_d), Image.LANCZOS)


def _render_header_text(max_w: int, subtitle: str, title_font, subtitle_font, handle_font) -> Image.Image:
    # Title / description / @handle stacked on baselines like a CSS line box (gap = upper descender
    # + margin + lower ascender, so the "_" in the handle doesn't distort the rhythm), then cropped
    # to its visible pixels so callers can size and center it precisely.
    lines = [
        (_BRAND_TITLE, title_font, _TITLE),
        (subtitle, subtitle_font, _SUBTITLE),
        (_BRAND_HANDLE, handle_font, _HANDLE),
    ]
    margins = [2 * _S, 2 * _S]  # gap after title, gap after description
    metrics = [f.getmetrics() for _, f, _ in lines]
    layer_h = sum(a + de for a, de in metrics) + sum(margins)
    layer = Image.new("RGBA", (max_w, layer_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    baseline = metrics[0][0]
    for i, (t, f, color) in enumerate(lines):
        draw.text((0, baseline), t, font=f, fill=color, anchor="ls")
        if i < len(margins):
            baseline += metrics[i][1] + margins[i] + metrics[i + 1][0]
    return layer.crop(layer.getbbox())


def _draw_call_to_action(draw: ImageDraw.ImageDraw, center_x: int, top_y: int, text: str, font) -> None:
    # The "→" is drawn in the flag yellow; the rest in the default call color. Kept as one centered line.
    runs = [(seg, _FLAG_YELLOW if seg == "→" else _CALL_TEXT)
            for seg in re.split(r"(→)", text) if seg]
    total_w = sum(draw.textlength(seg, font=font) for seg, _ in runs)
    x = center_x - total_w / 2
    for seg, color in runs:
        draw.text((x, top_y), seg, font=font, fill=color, anchor="la")
        x += draw.textlength(seg, font=font)


def _truncate_to_width(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "…"
    while text and draw.textlength(text + ellipsis, font=font) > max_width:
        text = text[:-1]
    return (text + ellipsis) if text else ellipsis


def _draw_pill(card: Image.Image, draw: ImageDraw.ImageDraw, center_x: int, top_y: int,
               text: CardText, max_pill_w: int) -> None:
    parts = _pill_parts(text)
    if not parts:
        return

    name_font = _mono(10, semibold=False)
    pad_x, gap = 16 * _S, 12 * _S
    divider_w = max(1, _S)
    divider_h = 16 * _S

    # Truncate the recipient name (always the first part) so the pill fits max_pill_w.
    slots = 2 * pad_x + (gap + divider_w + gap) * (len(parts) - 1)
    fixed_w = slots + sum(round(draw.textlength(t, font=f)) for f, _, t in parts if f is not name_font)
    if text.recipient and fixed_w + round(draw.textlength(parts[0][2], font=name_font)) > max_pill_w:
        truncated = _truncate_to_width(draw, parts[0][2], name_font, max_pill_w - fixed_w)
        parts[0] = (name_font, _PILL_NAME, truncated)

    text_widths = [round(draw.textlength(t, font=f)) for f, _, t in parts]
    pill_h = _pill_height(parts)

    # Content width: text zones, plus one gap + divider + gap around a divider between two parts.
    inner_w = sum(text_widths)
    if len(parts) > 1:
        inner_w += (gap + divider_w + gap) * (len(parts) - 1)
    pill_w = inner_w + 2 * pad_x

    pill = Image.new("RGBA", (pill_w, pill_h), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(pill)
    pdraw.rounded_rectangle((0, 0, pill_w - 1, pill_h - 1), radius=12 * _S, fill=_PILL_BG)

    cx = pad_x
    mid_y = pill_h // 2
    for i, ((font, color, t), w) in enumerate(zip(parts, text_widths)):
        pdraw.text((cx + w // 2, mid_y), t, font=font, fill=color, anchor="mm")
        cx += w
        if i < len(parts) - 1:
            line_x = cx + gap + divider_w // 2
            pdraw.line((line_x, mid_y - divider_h // 2, line_x, mid_y + divider_h // 2),
                       fill=_PILL_DIVIDER, width=divider_w)
            cx += gap + divider_w + gap

    card.alpha_composite(pill, (center_x - pill_w // 2, top_y))


def build_card(qr_image: Image.Image, text: CardText) -> bytes:
    s = _S
    card_w = _CARD_W * s
    pad = _PAD * s
    inner_w = card_w - 2 * pad

    draw_probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

    # --- measure the vertical layout before allocating the card ---
    title_font = _manrope(25, 800)
    subtitle_font = _manrope(13, 600)
    handle_font = _mono(13, semibold=False)
    call_font = _manrope(16, 700)

    accent_h = _ACCENT_H * s

    # Build the header text block (title / description / @handle) now, so its visible height can
    # drive both the header row height and the logo size (the logo matches the text block height).
    header_text = _render_header_text(inner_w, text.subtitle, title_font, subtitle_font, handle_font)
    header_h = header_text.height
    logo_side = header_h  # logo occupies the full height of the text block

    panel_pad = _QR_PANEL_PAD * s
    panel_inner = inner_w - 2 * panel_pad
    panel_h = panel_inner + 2 * panel_pad

    call_bbox = draw_probe.textbbox((0, 0), text.call_to_action, font=call_font)
    call_h = call_bbox[3] - call_bbox[1]

    gap_accent_header = 30 * s
    gap_header_panel = 30 * s
    gap_panel_call = 26 * s
    gap_call_pill = 16 * s

    y = pad
    accent_y = y
    y += accent_h + gap_accent_header
    header_y = y
    y += header_h + gap_header_panel
    panel_y = y
    y += panel_h + gap_panel_call
    call_y = y
    y += call_h + gap_call_pill
    pill_y = y

    # measure pill height to finish the card
    pill_probe = _pill_height(_pill_parts(text))
    card_h = pill_y + pill_probe + pad if pill_probe else (call_y + call_h + pad)

    # --- render ---
    card = Image.new("RGBA", (card_w, card_h), (*_CARD_BG, 255))
    draw = ImageDraw.Draw(card)

    card.alpha_composite(_flag_bar(inner_w, accent_h), (pad, accent_y))

    # Logo sized to the text block height; both share the header row's top since header_h == both.
    logo = _logo_header().resize((logo_side, logo_side), Image.LANCZOS)
    card.alpha_composite(logo, (pad, header_y))
    text_x = pad + logo_side + _HEADER_GAP * s
    card.alpha_composite(header_text, (text_x, header_y))

    # white QR panel + QR + centered flag-ring logo
    panel = Image.new("RGBA", (inner_w, panel_h), (0, 0, 0, 0))
    ImageDraw.Draw(panel).rounded_rectangle((0, 0, inner_w - 1, panel_h - 1),
                                            radius=_QR_PANEL_RADIUS * s, fill=_WHITE)
    qr_scaled = qr_image.convert("RGBA").resize((panel_inner, panel_inner), Image.LANCZOS)
    panel.alpha_composite(qr_scaled, (panel_pad, panel_pad))
    ring = _center_logo(panel_inner)
    rx = panel_pad + (panel_inner - ring.width) // 2
    panel.alpha_composite(ring, (rx, rx))
    card.alpha_composite(panel, (pad, panel_y))

    _draw_call_to_action(draw, card_w // 2, call_y, text.call_to_action, call_font)

    _draw_pill(card, draw, card_w // 2, pill_y, text, max_pill_w=inner_w)

    buffer = io.BytesIO()
    card.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def _pill_parts(text: CardText) -> list[tuple]:
    # (font, color, rendered_text) for each present field; empty when nothing to show.
    parts = []
    if text.recipient:
        parts.append((_mono(10, semibold=False), _PILL_NAME, text.recipient.upper()))
    if text.amount:
        parts.append((_mono(14, semibold=True), _PILL_AMOUNT, text.amount))
    return parts


def _pill_height(parts: list[tuple]) -> int:
    # Kept in sync with _draw_pill: same padding, divider height, and per-part text measurement.
    if not parts:
        return 0
    pad_y, divider_h = 10 * _S, 16 * _S
    text_h = max(f.getbbox(t)[3] - f.getbbox(t)[1] for f, _, t in parts)
    inner_h = max(text_h, divider_h if len(parts) > 1 else 0)
    return inner_h + 2 * pad_y
