"""Write image regions to the LCD's Linux framebuffer (/dev/fb0).

The ms912x driver exposes 32 bpp XRGB8888, stored little-endian as
B, G, R, X. Only the rows of each changed box are written, which keeps the
driver's per-pixel conversion and USB traffic small.

Writes go through mmap. On this kernel (6.6, DRM fbdev emulation) a plain
write() to /dev/fb0 updates the shadow buffer but never reaches the panel;
the mmap path's page-fault dirty tracking is what triggers the flush.
"""

from __future__ import annotations

import errno
import mmap
import os

from PIL import Image

_GONE = {errno.ENODEV, errno.ENOENT, errno.EIO, errno.ENXIO}


class FramebufferGone(Exception):
    """The LCD was unplugged, or /dev/fb0 now belongs to a different device."""


class Framebuffer:
    def __init__(self, path: str = "/dev/fb0", width: int = 1024, height: int = 600,
                 stride: int = 4096) -> None:
        self.path = path
        self.width = width
        self.height = height
        self.stride = stride
        self._fd: int | None = None
        self._map: mmap.mmap | None = None
        self._identity: tuple[int, int] | None = None

    def open(self) -> None:
        try:
            self._fd = os.open(self.path, os.O_RDWR)
            st = os.fstat(self._fd)
            self._map = mmap.mmap(self._fd, self.stride * self.height, mmap.MAP_SHARED,
                                  mmap.PROT_READ | mmap.PROT_WRITE)
        except (OSError, ValueError) as e:
            self.close()
            raise FramebufferGone(f"{self.path}: {e}") from e
        self._identity = (st.st_ino, st.st_rdev)

    def close(self) -> None:
        if self._map is not None:
            self._map.close()
            self._map = None
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def check_same_device(self) -> None:
        """Raise if the path is gone or now points at a new device node,
        which is what a replugged LCD looks like."""
        try:
            st = os.stat(self.path)
        except OSError as e:
            raise FramebufferGone(f"{self.path}: {e}") from e
        if (st.st_ino, st.st_rdev) != self._identity:
            raise FramebufferGone(f"{self.path} was replaced")

    def write(self, img: Image.Image, boxes: list[tuple[int, int, int, int]]) -> None:
        if self._map is None:
            raise FramebufferGone(f"{self.path} is not open")
        # A device that vanishes under the mapping can SIGBUS instead of
        # raising; tick() calls check_same_device() first to make that rare,
        # and systemd restarts the service if it happens anyway.
        try:
            for left, top, right, bottom in boxes:
                data = img.crop((left, top, right, bottom)).tobytes("raw", "BGRX")
                row_bytes = (right - left) * 4
                for row in range(bottom - top):
                    offset = (top + row) * self.stride + left * 4
                    self._map[offset:offset + row_bytes] = data[row * row_bytes:(row + 1) * row_bytes]
        except OSError as e:
            if e.errno in _GONE:
                self.close()
                raise FramebufferGone(f"{self.path}: {e}") from e
            raise
