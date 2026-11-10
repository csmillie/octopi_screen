"""Plain data passed between the parsing, history, view and rendering layers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Literal

State = Literal["printing", "paused", "idle", "offline"]
Outcome = Literal["printing", "done", "failed", "cancelled"]
EventKind = Literal["started", "done", "failed", "cancelled", "paused", "resumed"]


@dataclass(frozen=True)
class Temp:
    actual: float
    target: float


@dataclass(frozen=True)
class Snapshot:
    """What OctoPrint reported on one poll."""

    octoprint_ok: bool
    printer_connected: bool
    state: State
    file_display: str | None
    file_path: str | None
    completion: float | None
    print_time: int | None
    print_time_left: int | None
    filepos: int | None
    nozzle: Temp | None
    bed: Temp | None


@dataclass(frozen=True)
class PrintEvent:
    kind: EventKind
    path: str | None
    # OctoPrint's "time" payload: seconds the print ran.
    duration: float | None


@dataclass(frozen=True)
class LastPrint:
    """The current or most recent print, as persisted across restarts."""

    file_display: str
    file_path: str | None
    started_at: float
    ended_at: float | None
    outcome: Outcome
    completion: float | None
    layer: int | None
    layer_total: int | None
    remaining: int | None
    duration: int | None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> LastPrint:
        return cls(**{name: data.get(name) for name in cls.__dataclass_fields__})


class Border(Enum):
    NONE = "none"
    RED_BLINK = "red_blink"
    RED = "red"
    YELLOW = "yellow"
    GREY = "grey"


@dataclass(frozen=True)
class View:
    """Every string and value the renderer draws, already formatted."""

    file_line: str
    percent: str
    remaining: str
    status: str
    started: str
    done_at: str
    layer: str
    nozzle: str
    bed: str
    speed: str
    flow: str
    took: str | None
    remaining_is_estimate: bool
    ring_fraction: float
    border: Border
