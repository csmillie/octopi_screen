import json
from pathlib import Path

from pi_screen.model import LastPrint, PrintEvent, Temp
from pi_screen.parse import parse_event, parse_flow, parse_snapshot, parse_speed

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def test_printing_snapshot():
    snap = parse_snapshot(load("job_printing"), load("printer_printing"))
    assert snap.octoprint_ok and snap.printer_connected
    assert snap.state == "printing"
    assert snap.completion == 40.8
    assert snap.print_time == 11200
    assert snap.print_time_left == 16200
    assert snap.filepos == 1830000
    assert snap.file_display == "Snacker2-vert.gcode"
    assert snap.file_path == "Snacker2-vert.gcode"
    assert snap.nozzle == Temp(205.1, 205.0)
    assert snap.bed == Temp(60.0, 60.0)


def test_idle_snapshot():
    snap = parse_snapshot(load("job_idle"), load("printer_idle"))
    assert snap.state == "idle"
    assert snap.printer_connected


def test_paused_snapshot():
    assert parse_snapshot(load("job_paused"), load("printer_printing")).state == "paused"


def test_octoprint_down():
    snap = parse_snapshot(None, None)
    assert not snap.octoprint_ok
    assert not snap.printer_connected
    assert snap.state == "offline"
    assert snap.nozzle is None


def test_printer_disconnected():
    job = load("job_idle")
    job["state"] = "Offline"
    snap = parse_snapshot(job, None)
    assert snap.octoprint_ok
    assert not snap.printer_connected
    assert snap.state == "offline"
    assert snap.nozzle is None and snap.bed is None
    assert snap.file_display == "Snacker2-vert.gcode"


def test_null_time_left_stays_none():
    job = load("job_printing")
    job["progress"]["printTimeLeft"] = None
    assert parse_snapshot(job, load("printer_printing")).print_time_left is None


def test_parse_speed():
    assert parse_speed("Recv: FR:80%") == 80
    assert parse_speed("Recv: ok") is None


def test_parse_flow():
    assert parse_flow("Recv: echo:E0 Flow: 100%") == 100
    assert parse_flow("Send: M221") is None


def test_parse_event_failed_with_cancelled_reason_is_cancelled():
    msg = {"event": {"type": "PrintFailed", "payload": {"path": "a.gcode", "time": 12.5, "reason": "cancelled"}}}
    assert parse_event(msg) == PrintEvent("cancelled", "a.gcode", 12.5)


def test_parse_event_failed_with_error_reason_is_failed():
    msg = {"event": {"type": "PrintFailed", "payload": {"path": "a.gcode", "time": 12.5, "reason": "error"}}}
    assert parse_event(msg).kind == "failed"


def test_parse_event_done():
    msg = {"event": {"type": "PrintDone", "payload": {"path": "a.gcode", "time": 27441.5}}}
    assert parse_event(msg) == PrintEvent("done", "a.gcode", 27441.5)


def test_parse_event_unrelated_is_none():
    assert parse_event({"event": {"type": "ZChange", "payload": {}}}) is None
    assert parse_event({"current": {"logs": []}}) is None


def test_lastprint_round_trip():
    lp = LastPrint(
        file_display="a.gcode", file_path="a.gcode", started_at=1000.0, ended_at=2000.0,
        outcome="failed", completion=40.8, layer=412, layer_total=1010, remaining=16200, duration=1000,
    )
    assert LastPrint.from_dict(lp.to_dict()) == lp
