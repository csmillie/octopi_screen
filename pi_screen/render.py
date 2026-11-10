"""Draw a View as a 1024x600 image (layout C: file bar, progress ring, list)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

from .model import Border, View

WIDTH, HEIGHT = 1024, 600

FONTS = Path(__file__).parent / "fonts"
REGULAR_FONT = FONTS / "DejaVuSans.ttf"
BOLD_FONT = FONTS / "DejaVuSans-Bold.ttf"

BACKGROUND = (0x0B, 0x0F, 0x14)
TEXT = (0xE6, 0xED, 0xF3)
DIM = (0x8B, 0x98, 0xA5)
ACCENT = (0x4C, 0xC9, 0xF0)
WARM = (0xFF, 0xB4, 0x54)
RED = (0xFF, 0x4D, 0x4F)
YELLOW = (0xFF, 0xD1, 0x66)
GREY = (0x5C, 0x67, 0x73)
TRACK = (0x1C, 0x26, 0x30)

BORDER_WIDTH = 12
BORDER_COLOURS = {Border.RED: RED, Border.RED_BLINK: RED, Border.YELLOW: YELLOW, Border.GREY: GREY}

FILE_BAR = (28, 24, 996, 84)
RING_CENTRE = (230, 340)
RING_RADIUS = 170
RING_THICKNESS = 22
LIST_LABEL_X = 460
LIST_VALUE_X = 640
LIST_TOP = 110
LIST_PITCH = 66


@lru_cache(maxsize=None)
def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Path,
    max_px: int,
    max_width: int,
    min_px: int,
) -> tuple[str, ImageFont.FreeTypeFont]:
    """Shrink the font in 2 px steps until ``text`` fits ``max_width``; at
    ``min_px``, cut characters from the middle and join with "…" so both the
    start and the end of the text stay visible."""
    size = max_px
    while size > min_px and draw.textlength(text, font=_font(font_path, size)) > max_width:
        size = max(min_px, size - 2)
    font = _font(font_path, size)
    if draw.textlength(text, font=font) <= max_width:
        return text, font

    head, tail = len(text) // 2, len(text) - len(text) // 2
    while head > 1 or tail > 1:
        if head >= tail:
            head -= 1
        else:
            tail -= 1
        candidate = text[:head] + "…" + text[len(text) - tail:]
        if draw.textlength(candidate, font=font) <= max_width:
            return candidate, font
    return "…", font


class Renderer:
    def __init__(self, fonts_dir: Path = FONTS) -> None:
        self.regular = fonts_dir / REGULAR_FONT.name
        self.bold = fonts_dir / BOLD_FONT.name

    def render(self, view: View, blink_on: bool) -> Image.Image:
        img = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
        draw = ImageDraw.Draw(img)
        self._border(draw, view.border, blink_on)
        self._file_bar(draw, view.file_line)
        self._ring(draw, view)
        self._list(draw, view)
        return img

    def _border(self, draw: ImageDraw.ImageDraw, border: Border, blink_on: bool) -> None:
        colour = BORDER_COLOURS.get(border)
        if colour is None or (border == Border.RED_BLINK and not blink_on):
            return
        draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), outline=colour, width=BORDER_WIDTH)

    def _file_bar(self, draw: ImageDraw.ImageDraw, name: str) -> None:
        left, top, right, bottom = FILE_BAR
        text, font = fit_text(draw, name, self.bold, 40, right - left, 24)
        draw.text((left, (top + bottom) // 2), text, font=font, fill=TEXT, anchor="lm")

    def _ring(self, draw: ImageDraw.ImageDraw, view: View) -> None:
        cx, cy = RING_CENTRE
        box = (cx - RING_RADIUS, cy - RING_RADIUS, cx + RING_RADIUS, cy + RING_RADIUS)
        draw.arc(box, 0, 360, fill=TRACK, width=RING_THICKNESS)
        fraction = min(max(view.ring_fraction, 0.0), 1.0)
        if fraction > 0:
            # Pillow measures angles clockwise from 3 o'clock; start at 12.
            draw.arc(box, -90, -90 + 360 * fraction, fill=ACCENT, width=RING_THICKNESS)

        inner = 2 * (RING_RADIUS - RING_THICKNESS) - 40
        remaining, font = fit_text(draw, view.remaining, self.bold, 92, inner, 56)
        draw.text((cx, cy - 22), remaining, font=font, fill=TEXT, anchor="mm")
        label = "est. remaining" if view.remaining_is_estimate else "remaining"
        draw.text((cx, cy + 40), label, font=_font(self.regular, 22), fill=DIM, anchor="mm")
        draw.text((cx, cy + 82), view.percent, font=_font(self.bold, 30), fill=ACCENT, anchor="mm")

        if view.status:
            colour = BORDER_COLOURS.get(view.border, TEXT)
            # Below the ring: "FAILED · PRINTER OFFLINE" does not fit inside it.
            status, font = fit_text(draw, view.status, self.bold, 26, 2 * RING_RADIUS + 40, 16)
            draw.text((cx, cy + RING_RADIUS + 26), status, font=font, fill=colour, anchor="mm")

    def _list(self, draw: ImageDraw.ImageDraw, view: View) -> None:
        rows = (
            ("Started", view.started, TEXT),
            ("Done at", view.done_at, ACCENT),
            ("Layer", view.layer, TEXT),
            ("Nozzle", view.nozzle, WARM),
            ("Bed", view.bed, WARM),
            ("Speed", view.speed, TEXT),
            ("Flow", view.flow, TEXT),
        )
        label_font = _font(self.regular, 30)
        value_font = _font(self.regular, 38)
        for index, (label, value, colour) in enumerate(rows):
            y = LIST_TOP + index * LIST_PITCH + LIST_PITCH // 2
            draw.text((LIST_LABEL_X, y), label, font=label_font, fill=DIM, anchor="lm")
            fill = DIM if value == "—" else colour
            draw.text((LIST_VALUE_X, y), value, font=value_font, fill=fill, anchor="lm")
            if label == "Done at" and view.took:
                end = LIST_VALUE_X + draw.textlength(value, font=value_font)
                draw.text((end + 18, y), view.took, font=_font(self.regular, 24), fill=DIM, anchor="lm")


def dirty_boxes(
    old: Image.Image | None, new: Image.Image, band: int = 40
) -> list[tuple[int, int, int, int]]:
    """Screen rectangles that differ between two frames, one per changed
    horizontal band, so only those regions are written to the LCD."""
    if old is None:
        return [(0, 0, new.width, new.height)]
    boxes = []
    for top in range(0, new.height, band):
        bottom = min(top + band, new.height)
        area = (0, top, new.width, bottom)
        bbox = ImageChops.difference(old.crop(area), new.crop(area)).getbbox()
        if bbox:
            left, y0, right, y1 = bbox
            boxes.append((left, top + y0, right, top + y1))
    return boxes
