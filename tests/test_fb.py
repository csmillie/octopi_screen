import pytest
from PIL import Image

from pi_screen.fb import Framebuffer, FramebufferGone

STRIDE, WIDTH, HEIGHT = 4096, 1024, 600


@pytest.fixture
def device(tmp_path):
    path = tmp_path / "fb0"
    path.write_bytes(bytes(STRIDE * HEIGHT))
    return path


def open_fb(path):
    fb = Framebuffer(str(path), WIDTH, HEIGHT, STRIDE)
    fb.open()
    return fb


def test_write_full_frame_bytes(device):
    img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    img.putpixel((1, 0), (255, 0, 0))
    img.putpixel((0, 1), (0x11, 0x22, 0x33))
    fb = open_fb(device)
    fb.write(img, [(0, 0, WIDTH, HEIGHT)])
    fb.close()
    data = device.read_bytes()
    assert data[4:8] == b"\x00\x00\xff\x00"            # B, G, R, X
    assert data[STRIDE:STRIDE + 4] == b"\x33\x22\x11\x00"


def test_write_box_only_touches_box(device):
    img = Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255))
    fb = open_fb(device)
    fb.write(img, [(10, 20, 12, 21)])
    fb.close()
    data = device.read_bytes()
    row = 20 * STRIDE
    assert data[row + 40:row + 48] == b"\xff\xff\xff\x00" * 2
    assert data[row + 36:row + 40] == bytes(4)
    assert data[row + 48:row + 52] == bytes(4)
    assert data.count(b"\xff") == 6


def test_missing_device_raises_framebuffer_gone(tmp_path):
    with pytest.raises(FramebufferGone):
        open_fb(tmp_path / "missing")


def test_replaced_device_detected(device):
    fb = open_fb(device)
    fb.check_same_device()
    device.unlink()
    device.write_bytes(bytes(STRIDE * HEIGHT))
    with pytest.raises(FramebufferGone):
        fb.check_same_device()


def test_removed_device_detected(device):
    fb = open_fb(device)
    device.unlink()
    with pytest.raises(FramebufferGone):
        fb.check_same_device()
