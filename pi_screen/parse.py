"""Turn OctoPrint REST responses, serial log lines and push events into models."""

from __future__ import annotations

import re

from .model import PrintEvent, Snapshot, State, Temp

_PRINTING = {"Printing", "Cancelling", "Finishing", "Starting"}
_PAUSED = {"Paused", "Pausing", "Resuming"}

_SPEED = re.compile(r"FR:(\d+)%")
_FLOW = re.compile(r"Flow:\s*(\d+)%")

_EVENTS = {
    "PrintStarted": "started",
    "PrintDone": "done",
    "PrintCancelled": "cancelled",
    "PrintFailed": "failed",
    "PrintPaused": "paused",
    "PrintResumed": "resumed",
}


def _state(text: str | None) -> State:
    if text in _PRINTING:
        return "printing"
    if text in _PAUSED:
        return "paused"
    if text == "Operational":
        return "idle"
    return "offline"


def _temp(temperature: dict, name: str) -> Temp | None:
    entry = temperature.get(name)
    if not entry or entry.get("actual") is None:
        return None
    return Temp(float(entry["actual"]), float(entry.get("target") or 0.0))


def _int(value) -> int | None:
    return None if value is None else int(value)


def parse_snapshot(job: dict | None, printer: dict | None) -> Snapshot:
    """``job`` is /api/job; ``printer`` is /api/printer, or None when it
    answered 409 (printer not operational) or could not be reached."""
    if job is None:
        return Snapshot(False, False, "offline", None, None, None, None, None, None, None, None)

    file = (job.get("job") or {}).get("file") or {}
    progress = job.get("progress") or {}
    temperature = (printer or {}).get("temperature") or {}
    return Snapshot(
        octoprint_ok=True,
        printer_connected=printer is not None,
        state=_state(job.get("state")),
        file_display=file.get("display") or file.get("name"),
        file_path=file.get("path"),
        completion=progress.get("completion"),
        print_time=_int(progress.get("printTime")),
        print_time_left=_int(progress.get("printTimeLeft")),
        filepos=_int(progress.get("filepos")),
        nozzle=_temp(temperature, "tool0"),
        bed=_temp(temperature, "bed"),
    )


def parse_speed(line: str) -> int | None:
    match = _SPEED.search(line)
    return int(match.group(1)) if match else None


def parse_flow(line: str) -> int | None:
    match = _FLOW.search(line)
    return int(match.group(1)) if match else None


def parse_event(msg: dict) -> PrintEvent | None:
    event = msg.get("event")
    if not isinstance(event, dict):
        return None
    kind = _EVENTS.get(event.get("type"))
    if kind is None:
        return None
    payload = event.get("payload") or {}
    # OctoPrint follows PrintCancelled with PrintFailed(reason="cancelled").
    if kind == "failed" and payload.get("reason") == "cancelled":
        kind = "cancelled"
    return PrintEvent(kind, payload.get("path"), payload.get("time"))
