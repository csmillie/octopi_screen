"""Decide what the screen says: formats every value and picks the border.

Live data (temperatures, speed, flow, an in-progress print) comes from the
current snapshot and becomes "—" when it is not available. A finished,
failed or cancelled print comes from the dashboard's own history file, so it
stays on screen even when OctoPrint or the printer is offline.
"""

from __future__ import annotations

from datetime import datetime, tzinfo

from .model import Border, LastPrint, Snapshot, Temp, View

DASH = "—"
BLINK_SECONDS = 300

_ENDED = ("done", "failed", "cancelled")
_STATUS = {"done": "COMPLETE", "failed": "FAILED", "cancelled": "CANCELLED"}


def fmt_duration(seconds: int | float | None) -> str:
    if seconds is None:
        return DASH
    minutes = int(seconds) // 60
    return f"{minutes // 60}:{minutes % 60:02d}"


def fmt_clock(ts: float | None, now: float, tz: tzinfo) -> str:
    if ts is None:
        return DASH
    when = datetime.fromtimestamp(ts, tz)
    days = (when.date() - datetime.fromtimestamp(now, tz).date()).days
    text = when.strftime("%H:%M")
    return f"{text} +{days}d" if days > 0 else text


def _is_live(snap: Snapshot) -> bool:
    return snap.octoprint_ok and snap.state in ("printing", "paused")


def select_border(snap: Snapshot, last: LastPrint | None, now: float) -> Border:
    if not _is_live(snap) and last and last.outcome in ("failed", "cancelled"):
        if last.ended_at is not None and now - last.ended_at < BLINK_SECONDS:
            return Border.RED_BLINK
        return Border.RED
    if snap.state == "paused":
        return Border.YELLOW
    if not snap.octoprint_ok or not snap.printer_connected:
        return Border.GREY
    return Border.NONE


def _offline_word(snap: Snapshot) -> str:
    if not snap.octoprint_ok:
        return "OCTOPRINT OFFLINE"
    if not snap.printer_connected:
        return "PRINTER OFFLINE"
    return ""


def _status(word: str, snap: Snapshot) -> str:
    return " · ".join(part for part in (word, _offline_word(snap)) if part)


def _temp(temp: Temp | None, connected: bool) -> str:
    if not connected or temp is None:
        return DASH
    return f"{temp.actual:.0f}° → {temp.target:.0f}°"


def _percent(completion: float | None) -> str:
    return DASH if completion is None else f"{completion:.0f}%"


def _layer(current: int | None, total: int | None) -> str:
    if not total:
        return "— / ?"
    return f"{DASH if current is None else current} / {total}"


def build_view(
    snap: Snapshot,
    last: LastPrint | None,
    layer: tuple[int, int] | None,
    slicer_seconds: int | None,
    speed: int | None,
    flow: int | None,
    now: float,
    tz: tzinfo,
) -> View:
    connected = snap.octoprint_ok and snap.printer_connected
    common = dict(
        nozzle=_temp(snap.nozzle, connected),
        bed=_temp(snap.bed, connected),
        speed=f"{speed}%" if connected and speed is not None else DASH,
        flow=f"{flow}%" if connected and flow is not None else DASH,
        border=select_border(snap, last, now),
    )

    if _is_live(snap):
        left = snap.print_time_left
        estimate = False
        if left is None and slicer_seconds is not None and snap.print_time is not None:
            left = max(0, slicer_seconds - snap.print_time)
            estimate = True
        if last and last.outcome == "printing":
            started_at = last.started_at
        elif snap.print_time is not None:
            started_at = now - snap.print_time
        else:
            started_at = None
        completion = snap.completion
        return View(
            file_line=snap.file_display or DASH,
            percent=_percent(completion),
            remaining=fmt_duration(left),
            status=_status("PAUSED" if snap.state == "paused" else "", snap),
            started=fmt_clock(started_at, now, tz),
            done_at=fmt_clock(None if left is None else now + left, now, tz),
            layer=_layer(*layer) if layer else _layer(None, None),
            took=None,
            remaining_is_estimate=estimate,
            ring_fraction=(completion or 0.0) / 100,
            **common,
        )

    if last and last.outcome in _ENDED:
        return View(
            file_line=last.file_display or DASH,
            percent=_percent(last.completion),
            remaining=fmt_duration(last.remaining),
            status=_status(_STATUS[last.outcome], snap),
            started=fmt_clock(last.started_at, now, tz),
            done_at=fmt_clock(last.ended_at, now, tz),
            layer=_layer(last.layer, last.layer_total),
            took=f"took {fmt_duration(last.duration)}" if last.outcome == "done" and last.duration is not None else None,
            remaining_is_estimate=False,
            ring_fraction=(last.completion or 0.0) / 100,
            **common,
        )

    if last and last.outcome == "printing":
        # A print was running when OctoPrint or the printer went away.
        return View(
            file_line=last.file_display or DASH,
            percent=DASH,
            remaining=DASH,
            status=_status("", snap),
            started=fmt_clock(last.started_at, now, tz),
            done_at=DASH,
            layer=_layer(None, last.layer_total),
            took=None,
            remaining_is_estimate=False,
            ring_fraction=0.0,
            **common,
        )

    if not snap.octoprint_ok:
        return View(
            file_line=DASH, percent=DASH, remaining=DASH, status=_status("", snap),
            started=DASH, done_at=DASH, layer=_layer(None, None), took=None,
            remaining_is_estimate=False, ring_fraction=0.0, **common,
        )

    return View(
        file_line="No print yet", percent="0%", remaining="0:00", status=_status("", snap),
        started="00:00", done_at="00:00", layer="0 / 0", took=None,
        remaining_is_estimate=False, ring_fraction=0.0, **common,
    )
