import math
from dataclasses import replace

from PIL import Image, ImageChops, ImageDraw

from pi_screen.model import Border, View
from pi_screen.render import (
    BACKGROUND, BOLD_FONT, HEIGHT, WIDTH, Renderer, dirty_boxes, fit_text,
)

VIEW = View(
    file_line="Snacker2-vert.gcode", percent="41%", remaining="4:30", status="",
    started="10:53", done_at="18:30", layer="412 / 1010", nozzle="205° → 205°",
    bed="60° → 60°", speed="80%", flow="100%", took=None, remaining_is_estimate=False,
    ring_fraction=0.408, border=Border.NONE,
)
RED = (0xFF, 0x4D, 0x4F)
YELLOW = (0xFF, 0xD1, 0x66)
GREY = (0x5C, 0x67, 0x73)
ACCENT = (0x4C, 0xC9, 0xF0)
TRACK = (0x1C, 0x26, 0x30)

renderer = Renderer()


def ring_point(clock_degrees, radius=159):
    angle = math.radians(clock_degrees)
    return round(230 + radius * math.sin(angle)), round(340 - radius * math.cos(angle))


def test_image_size_and_background():
    img = renderer.render(VIEW, blink_on=True)
    assert img.size == (WIDTH, HEIGHT) == (1024, 600)
    assert img.mode == "RGB"
    assert img.getpixel((500, 590)) == BACKGROUND


def test_border_colours():
    for border, colour in ((Border.RED, RED), (Border.YELLOW, YELLOW), (Border.GREY, GREY)):
        assert renderer.render(replace(VIEW, border=border), True).getpixel((5, 300)) == colour
    blink = replace(VIEW, border=Border.RED_BLINK)
    assert renderer.render(blink, True).getpixel((5, 300)) == RED
    assert renderer.render(blink, False).getpixel((5, 300)) == BACKGROUND
    assert renderer.render(VIEW, True).getpixel((5, 300)) == BACKGROUND


def test_ring_fill_follows_fraction():
    img = renderer.render(replace(VIEW, ring_fraction=0.25), True)
    assert img.getpixel(ring_point(60)) == ACCENT   # 2 o'clock
    assert img.getpixel(ring_point(240)) == TRACK   # 8 o'clock


def test_fit_text_shrinks_before_truncating():
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    name = "Snacker2_PLA_4h27m_fix3_extra_long_name_v2.gcode"
    text, font = fit_text(draw, name, BOLD_FONT, 40, 968, 24)
    assert text == name
    assert 24 <= font.size < 40


def test_fit_text_middle_ellipsis():
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    name = "Very_long_project_name_" * 6 + "_fix3.gcode"
    text, font = fit_text(draw, name, BOLD_FONT, 40, 968, 24)
    assert "…" in text
    assert text.startswith("Very_long")
    assert text.endswith("_fix3.gcode")
    assert font.size == 24
    assert draw.textlength(text, font=font) <= 968

    img = renderer.render(replace(VIEW, file_line=name), True)
    content = _content_bbox(img.crop((0, 24, WIDTH, 84)))
    assert content[0] >= 28 and content[2] <= 996


def _content_bbox(img):
    background = Image.new("RGB", img.size, BACKGROUND)
    return ImageChops.difference(img, background).getbbox()


def test_estimate_label_shown():
    plain = renderer.render(VIEW, True)
    estimate = renderer.render(replace(VIEW, remaining_is_estimate=True), True)
    assert dirty_boxes(plain, estimate)


def test_dirty_boxes_full_on_first_frame():
    img = renderer.render(VIEW, True)
    assert dirty_boxes(None, img) == [(0, 0, WIDTH, HEIGHT)]


def test_dirty_boxes_empty_when_identical():
    assert dirty_boxes(renderer.render(VIEW, True), renderer.render(VIEW, True)) == []


def test_dirty_boxes_only_changed_band():
    old = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    new = old.copy()
    new.putpixel((700, 130), (255, 255, 255))
    assert dirty_boxes(old, new) == [(700, 130, 701, 131)]
