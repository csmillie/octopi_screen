from pi_screen.history import HistoryStore, PrintTracker, reconcile
from pi_screen.model import LastPrint, PrintEvent, Snapshot, Temp


def snapshot(completion=40.8, left=16200, state="printing"):
    return Snapshot(
        octoprint_ok=True, printer_connected=True, state=state,
        file_display="Snacker2-vert.gcode", file_path="Snacker2-vert.gcode",
        completion=completion, print_time=11200, print_time_left=left, filepos=1830000,
        nozzle=Temp(205.0, 205.0), bed=Temp(60.0, 60.0),
    )


def record(**overrides):
    values = dict(
        file_display="a.gcode", file_path="a.gcode", started_at=1000.0, ended_at=2000.0,
        outcome="done", completion=100.0, layer=10, layer_total=10, remaining=0, duration=1000,
    )
    values.update(overrides)
    return LastPrint(**values)


def test_save_load_round_trip(tmp_path):
    store = HistoryStore(tmp_path / "state" / "last-print.json")
    store.save(record())
    assert store.load() == record()


def test_load_missing_file_returns_none(tmp_path):
    assert HistoryStore(tmp_path / "none.json").load() is None


def test_load_corrupt_file_returns_none(tmp_path):
    path = tmp_path / "last-print.json"
    path.write_text("{not json")
    assert HistoryStore(path).load() is None


def test_started_then_done_records_done_with_full_layers(tmp_path):
    tracker = PrintTracker(HistoryStore(tmp_path / "s.json"))
    tracker.on_event(PrintEvent("started", "Snacker2-vert.gcode", None), snapshot(0, None), (0, 1010), now=1000.0)
    assert tracker.last.outcome == "printing"
    assert tracker.last.started_at == 1000.0

    tracker.on_event(PrintEvent("done", "Snacker2-vert.gcode", 27441.5), snapshot(100, 0, "idle"), (1010, 1010), now=28441.5)
    last = tracker.last
    assert last.outcome == "done"
    assert last.ended_at == 28441.5
    assert last.completion == 100
    assert (last.layer, last.layer_total) == (1010, 1010)
    assert last.remaining == 0
    assert last.duration == 27441


def test_failed_freezes_progress_and_layer(tmp_path):
    tracker = PrintTracker(HistoryStore(tmp_path / "s.json"))
    tracker.on_event(PrintEvent("started", "Snacker2-vert.gcode", None), snapshot(0, None), (0, 1010), now=1000.0)
    tracker.on_event(PrintEvent("failed", "Snacker2-vert.gcode", None), snapshot(), (412, 1010), now=12200.0)
    last = tracker.last
    assert last.outcome == "failed"
    assert last.completion == 40.8
    assert (last.layer, last.layer_total) == (412, 1010)
    assert last.remaining == 16200
    assert last.duration == 11200


def test_cancelled_without_layer_info(tmp_path):
    tracker = PrintTracker(HistoryStore(tmp_path / "s.json"))
    tracker.on_event(PrintEvent("cancelled", "a.gcode", 30.0), snapshot(), None, now=500.0)
    assert tracker.last.outcome == "cancelled"
    assert tracker.last.layer is None and tracker.last.layer_total is None


def test_pause_does_not_change_record(tmp_path):
    tracker = PrintTracker(HistoryStore(tmp_path / "s.json"))
    tracker.on_event(PrintEvent("started", "a.gcode", None), snapshot(0, None), None, now=1000.0)
    before = tracker.last
    tracker.on_event(PrintEvent("paused", "a.gcode", None), snapshot(state="paused"), None, now=1100.0)
    assert tracker.last == before


def test_tracker_survives_restart(tmp_path):
    path = tmp_path / "s.json"
    PrintTracker(HistoryStore(path)).on_event(PrintEvent("failed", "a.gcode", 60.0), snapshot(), (5, 10), now=500.0)
    assert PrintTracker(HistoryStore(path)).last.outcome == "failed"


REAL_METADATA = {"display": "Snacker2-vert.gcode", "path": "Snacker2-vert.gcode",
                 "prints": {"failure": 0, "success": 1,
                            "last": {"date": 1794273531.79, "printTime": 27441.49, "success": True}}}


def test_reconcile_prefers_newer_metadata():
    result = reconcile(record(ended_at=1000.0), REAL_METADATA)
    assert result.outcome == "done"
    assert result.ended_at == 1794273531.79
    assert result.duration == 27441
    assert result.started_at == 1794273531.79 - 27441.49
    assert result.completion == 100
    assert result.file_display == "Snacker2-vert.gcode"


def test_reconcile_failed_metadata():
    metadata = {"display": "a.gcode", "path": "a.gcode",
                "prints": {"last": {"date": 5000.0, "printTime": 60.0, "success": False}}}
    result = reconcile(None, metadata)
    assert result.outcome == "failed"
    assert result.completion is None


def test_reconcile_keeps_stored_when_not_newer():
    stored = record(ended_at=1794273531.0)
    assert reconcile(stored, REAL_METADATA) is stored


def test_reconcile_without_metadata_keeps_stored():
    stored = record()
    assert reconcile(stored, None) is stored
    assert reconcile(stored, {"display": "a.gcode", "prints": {}}) is stored


def test_reconcile_keeps_running_print_over_older_metadata():
    running = record(started_at=1794280000.0, ended_at=None, outcome="printing", completion=0.0)
    assert reconcile(running, REAL_METADATA) is running
