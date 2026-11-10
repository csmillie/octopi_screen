"""Layer positions and the slicer's time estimate, read from a G-code file.

Simplify3D marks each layer with a ``; layer N, Z = ...`` comment and ends
the file with a build summary that includes ``;   Build Time: 6 hours 18
minutes``. One pass over the file collects both.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

_LAYER = re.compile(rb"^; layer \d+, Z")
_BUILD_TIME = re.compile(
    rb"^;\s*Build Time:\s*(?:(\d+) hours?)?\s*(?:(\d+) minutes?)?"
)


@dataclass(frozen=True)
class GcodeInfo:
    # Byte offset of each layer comment, in file order.
    offsets: tuple[int, ...]
    build_seconds: int | None

    @property
    def total_layers(self) -> int:
        return len(self.offsets)


def read_gcode_info(path: Path) -> GcodeInfo:
    offsets = []
    build_seconds = None
    position = 0
    with open(path, "rb") as f:
        for line in f:
            if _LAYER.match(line):
                offsets.append(position)
            elif line.startswith(b";"):
                match = _BUILD_TIME.match(line)
                if match and (match.group(1) or match.group(2)):
                    hours = int(match.group(1) or 0)
                    minutes = int(match.group(2) or 0)
                    build_seconds = hours * 3600 + minutes * 60
            position += len(line)
    return GcodeInfo(tuple(offsets), build_seconds)


def current_layer(info: GcodeInfo, filepos: int) -> int:
    """Number of layers started at or before ``filepos``; 0 before the first."""
    return bisect_right(info.offsets, filepos)


class GcodeInfoCache:
    """Reads each file once, and again only if its modification time changes."""

    def __init__(self) -> None:
        self._entries: dict[Path, tuple[int, GcodeInfo]] = {}

    def get(self, path: Path) -> GcodeInfo | None:
        try:
            mtime = path.stat().st_mtime_ns
        except FileNotFoundError:
            self._entries.pop(path, None)
            return None
        cached = self._entries.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        info = read_gcode_info(path)
        self._entries[path] = (mtime, info)
        return info
