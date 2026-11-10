import copy
import json
from pathlib import Path

from pi_screen.app import Dashboard, load_api_key, run_loop
from pi_screen.fb import FramebufferGone
from pi_screen.gcode import GcodeInfoCache
from pi_screen.history import HistoryStore, PrintTracker
from pi_screen.model import Border, PrintEvent
from pi_screen.render import Renderer

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


class FakeClient:
    def __init__(self, job="job_idle", printer="printer_idle"):
        self.job = load(job)
        self.printer = load(printer) if printer else None
        self.polls = 0
        self.commands = []

    def get_job(self):
        self.polls += 1
        return copy.deepcopy(self.job)

    def get_printer(self):
        return copy.deepcopy(self.printer)

    def get_file(self, path):
        return None

    def send_commands(self, commands):
        self.commands.append(list(commands))
        return True


class FakeFramebuffer:
    def __init__(self):
        self.writes = []
        self.gone = False

    def check_same_device(self):
        if self.gone:
            raise FramebufferGone("replugged")

    def write(self, img, boxes):
        self.writes.append(boxes)


class Clock:
    def __init__(self):
        self.now = 1_794_280_000.0

    def __call__(self):
        return self.now


def make(tmp_path, client=None, clock=None):
    client = client or FakeClient()
    fb = FakeFramebuffer()
    clock = clock or Clock()
    dash = Dashboard(
        client=client,
        tracker=PrintTracker(HistoryStore(tmp_path / "last.json")),
        gcode_cache=GcodeInfoCache(),
        uploads_dir=tmp_path,
        renderer=Renderer(),
        fb=fb,
        tz=None,
        clock=clock,
    )
    return dash, client, fb, clock


def test_tick_polls_every_two_seconds(tmp_path):
    dash, client, _, clock = make(tmp_path)
    dash.tick()
    clock.now += 1
    dash.tick()
    assert client.polls == 1
    clock.now += 1
    dash.tick()
    assert client.polls == 2


def test_speed_flow_queries_only_when_connected_every_15s(tmp_path):
    dash, client, _, clock = make(tmp_path)
    for step in (0, 5, 5, 5):
        clock.now += step
        dash.tick()
    assert client.commands == [["M220", "M221"], ["M220", "M221"]]

    offline, offline_client, _, _ = make(tmp_path, FakeClient(printer=None))
    offline.tick()
    assert offline_client.commands == []


def test_log_lines_update_speed_and_flow(tmp_path):
    dash, _, _, _ = make(tmp_path)
    dash.on_log("Recv: FR:80%")
    dash.on_log("Recv: echo:E0 Flow: 95%")
    dash.tick()
    assert dash.last_view.speed == "80%"
    assert dash.last_view.flow == "95%"


def test_static_screen_writes_nothing_after_first_frame(tmp_path):
    dash, _, fb, clock = make(tmp_path)
    dash.tick()
    assert fb.writes == [[(0, 0, 1024, 600)]]
    clock.now += 2
    dash.tick()
    assert len(fb.writes) == 1


def test_failed_event_turns_border_red(tmp_path):
    dash, _, _, _ = make(tmp_path, FakeClient("job_printing", "printer_printing"))
    dash.tick()
    dash.client.job["state"] = "Operational"
    dash.on_event(PrintEvent("failed", "Snacker2-vert.gcode", 11200.0))
    dash.tick()
    assert dash.last_view.border == Border.RED_BLINK
    assert dash.last_view.status == "FAILED"


def test_started_event_uses_fresh_job(tmp_path):
    dash, client, _, _ = make(tmp_path)
    dash.tick()
    client.job = load("job_printing")
    client.job["job"]["file"].update(display="next.gcode", path="next.gcode")
    dash.on_event(PrintEvent("started", "next.gcode", None))
    assert dash.tracker.last.file_display == "next.gcode"


def test_layers_come_from_uploaded_file(tmp_path):
    (tmp_path / "Snacker2-vert.gcode").write_bytes((FIXTURES / "s3d_sample.gcode").read_bytes())
    client = FakeClient("job_printing", "printer_printing")
    client.job["progress"]["filepos"] = 10**9
    dash, _, _, _ = make(tmp_path, client)
    dash.tick()
    assert dash.last_view.layer == "3 / 3"


def test_framebuffer_gone_exits_with_status_3(tmp_path):
    dash, _, fb, _ = make(tmp_path)
    fb.gone = True
    assert run_loop(dash, sleep=lambda s: None) == 3


def test_load_api_key(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "accessControl:\n  salt: xyz\napi:\n  allowCrossOrigin: false\n  key: ABC123\n"
        "plugins:\n  key: not-this-one\n"
    )
    assert load_api_key(config) == "ABC123"


def test_reconciled_history_gets_layer_total_from_file(tmp_path):
    from pi_screen.model import LastPrint
    (tmp_path / "Snacker2-vert.gcode").write_bytes((FIXTURES / "s3d_sample.gcode").read_bytes())
    dash, _, _, _ = make(tmp_path)
    dash.tracker.last = LastPrint("Snacker2-vert.gcode", "Snacker2-vert.gcode", 1000.0, 2000.0,
                                  "done", 100.0, None, None, 0, 1000)
    dash.tick()
    assert dash.last_view.layer == "3 / 3"


class CountingRenderer(Renderer):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def render(self, view, blink_on):
        self.calls += 1
        return super().render(view, blink_on)


def test_unchanged_view_is_not_rendered_again(tmp_path):
    dash, _, _, clock = make(tmp_path)
    dash.renderer = CountingRenderer()
    for _ in range(4):
        dash.tick()
        clock.now += 0.5
    assert dash.renderer.calls == 1


def test_blinking_border_renders_each_phase(tmp_path):
    dash, _, fb, clock = make(tmp_path, FakeClient("job_printing", "printer_printing"))
    dash.tick()
    dash.client.job["state"] = "Operational"
    dash.on_event(PrintEvent("failed", "Snacker2-vert.gcode", 11200.0))
    dash.renderer = CountingRenderer()
    clock.now = float(int(clock.now)) + 0.1
    dash.tick()
    clock.now += 1
    dash.tick()
    assert dash.renderer.calls == 2
