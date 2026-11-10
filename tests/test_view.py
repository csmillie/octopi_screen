from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from pi_screen.model import Border, LastPrint, Snapshot, Temp
from pi_screen.view import build_view, fmt_clock, fmt_duration, select_border

TZ = ZoneInfo("America/Toronto")
NOW = datetime(2026, 11, 9, 14, 0, tzinfo=TZ).timestamp()
DASH = "—"


def at(hour, minute, day=9):
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ).timestamp()


PRINTING = Snapshot(
    octoprint_ok=True, printer_connected=True, state="printing",
    file_display="Snacker2-vert.gcode", file_path="Snacker2-vert.gcode",
    completion=40.8, print_time=11200, print_time_left=16200, filepos=1830000,
    nozzle=Temp(205.1, 205.0), bed=Temp(60.0, 60.0),
)
IDLE = replace(PRINTING, state="idle", completion=100.0, print_time_left=0,
               nozzle=Temp(18.1, 0.0), bed=Temp(19.2, 0.0))
PRINTER_OFFLINE = replace(IDLE, printer_connected=False, state="offline", nozzle=None, bed=None)
OCTOPRINT_DOWN = Snapshot(False, False, "offline", None, None, None, None, None, None, None, None)

RUNNING = LastPrint("Snacker2-vert.gcode", "Snacker2-vert.gcode", at(10, 53), None, "printing",
                    0.0, 0, 1010, None, None)
DONE = LastPrint("Snacker2-vert.gcode", "Snacker2-vert.gcode", at(6, 0), at(13, 37), "done",
                 100.0, 1010, 1010, 0, 27441)
FAILED = replace(DONE, ended_at=NOW - 60, outcome="failed", completion=40.8, layer=412,
                 remaining=16200, duration=11200)


def view(snap=PRINTING, last=RUNNING, layer=(412, 1010), slicer=22680, speed=80, flow=100, now=NOW):
    return build_view(snap, last, layer, slicer, speed, flow, now, TZ)


# Formatting

def test_fmt_duration():
    assert fmt_duration(None) == DASH
    assert fmt_duration(0) == "0:00"
    assert fmt_duration(16200) == "4:30"
    assert fmt_duration(97500) == "27:05"


def test_fmt_clock_same_day_and_none():
    assert fmt_clock(None, NOW, TZ) == DASH
    assert fmt_clock(at(21, 58), NOW, TZ) == "21:58"


def test_done_at_after_midnight_marks_next_day():
    now = at(23, 30)
    assert fmt_clock(now + 2 * 3600, now, TZ) == "01:30 +1d"


# Printing

def test_printing_view():
    v = view()
    assert v.file_line == "Snacker2-vert.gcode"
    assert v.ring_fraction == 0.408
    assert v.percent == "41%"
    assert v.remaining == "4:30"
    assert not v.remaining_is_estimate
    assert v.done_at == "18:30"
    assert v.started == "10:53"
    assert v.layer == "412 / 1010"
    assert v.nozzle == "205° → 205°"
    assert v.bed == "60° → 60°"
    assert v.speed == "80%"
    assert v.flow == "100%"
    assert v.status == ""
    assert v.took is None
    assert v.border == Border.NONE


def test_printing_without_tracker_record_uses_print_time():
    assert view(last=None).started == fmt_clock(NOW - 11200, NOW, TZ)


def test_null_time_left_uses_slicer_estimate():
    snap = replace(PRINTING, print_time_left=None, print_time=600)
    v = view(snap)
    assert v.remaining == "6:08"
    assert v.remaining_is_estimate


def test_octoprint_estimate_replaces_slicer():
    v = view(slicer=22680)
    assert v.remaining == "4:30"
    assert not v.remaining_is_estimate


def test_no_estimate_at_all_shows_dash():
    v = view(replace(PRINTING, print_time_left=None), slicer=None)
    assert v.remaining == DASH
    assert v.done_at == DASH
    assert v.ring_fraction == 0.408


def test_unknown_layers_show_question_mark():
    assert view(layer=None).layer == "— / ?"


def test_unknown_speed_and_flow_show_dash():
    v = view(speed=None, flow=None)
    assert v.speed == DASH and v.flow == DASH


def test_paused_view():
    v = view(replace(PRINTING, state="paused"))
    assert v.status == "PAUSED"
    assert v.border == Border.YELLOW
    assert v.remaining == "4:30"


# After a print

def test_idle_after_success_shows_last_print():
    v = view(IDLE, DONE, layer=(1010, 1010))
    assert v.file_line == "Snacker2-vert.gcode"
    assert v.ring_fraction == 1.0
    assert v.percent == "100%"
    assert v.remaining == "0:00"
    assert v.started == "06:00"
    assert v.done_at == "13:37"
    assert v.took == "took 7:37"
    assert v.layer == "1010 / 1010"
    assert v.status == "COMPLETE"
    assert v.nozzle == "18° → 0°"
    assert v.border == Border.NONE


def test_failed_view_is_frozen():
    v = view(IDLE, FAILED, layer=(1010, 1010))
    assert v.ring_fraction == 0.408
    assert v.layer == "412 / 1010"
    assert v.remaining == "4:30"
    assert v.status == "FAILED"
    assert v.border == Border.RED_BLINK
    assert v.took is None


def test_cancelled_view():
    v = view(IDLE, replace(FAILED, outcome="cancelled"))
    assert v.status == "CANCELLED"
    assert v.border == Border.RED_BLINK


def test_failed_without_layer_info():
    v = view(IDLE, replace(FAILED, layer=None, layer_total=None))
    assert v.layer == "— / ?"


def test_idle_no_history_shows_zeros():
    v = view(IDLE, None, layer=None, speed=100, flow=100)
    assert v.file_line == "No print yet"
    assert v.percent == "0%"
    assert v.remaining == "0:00"
    assert v.started == "00:00"
    assert v.done_at == "00:00"
    assert v.layer == "0 / 0"
    assert v.ring_fraction == 0.0
    assert v.status == ""


# Offline

def test_printer_offline_dashes_live_fields():
    v = view(PRINTER_OFFLINE, DONE, speed=80, flow=100)
    assert v.nozzle == DASH and v.bed == DASH
    assert v.speed == DASH and v.flow == DASH
    assert v.status == "COMPLETE · PRINTER OFFLINE"
    assert v.border == Border.GREY
    assert v.layer == "1010 / 1010"


def test_octoprint_offline():
    v = view(OCTOPRINT_DOWN, DONE)
    assert v.status == "COMPLETE · OCTOPRINT OFFLINE"
    assert v.nozzle == DASH and v.speed == DASH
    assert v.border == Border.GREY


def test_octoprint_offline_no_history_is_dashes():
    v = view(OCTOPRINT_DOWN, None, layer=None)
    assert v.file_line == DASH
    assert v.percent == DASH and v.remaining == DASH
    assert v.status == "OCTOPRINT OFFLINE"


def test_octoprint_offline_mid_print_dashes_progress():
    v = view(OCTOPRINT_DOWN, RUNNING)
    assert v.file_line == "Snacker2-vert.gcode"
    assert v.started == "10:53"
    assert v.remaining == DASH and v.percent == DASH and v.done_at == DASH


def test_failed_plus_printer_offline_is_red_with_both_words():
    v = view(PRINTER_OFFLINE, FAILED)
    assert v.border == Border.RED_BLINK
    assert v.status == "FAILED · PRINTER OFFLINE"


# Border timing and priority

def test_blink_stops_after_five_minutes():
    assert select_border(IDLE, replace(FAILED, ended_at=NOW - 301), NOW) == Border.RED
    assert select_border(IDLE, replace(FAILED, ended_at=NOW - 299), NOW) == Border.RED_BLINK


def test_new_print_clears_red():
    assert select_border(PRINTING, FAILED, NOW) == Border.NONE


def test_paused_beats_offline():
    snap = replace(PRINTING, state="paused", printer_connected=False)
    assert select_border(snap, RUNNING, NOW) == Border.YELLOW
