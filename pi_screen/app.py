"""Main loop: poll OctoPrint, follow its push events, draw changes to the LCD.

    python -m pi_screen.app                       run on the Pi
    python -m pi_screen.app --fixture printing    draw a saved state instead
    python -m pi_screen.app --fixture idle --png out.png   one frame to a PNG
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from dataclasses import replace
from datetime import tzinfo
from pathlib import Path
from typing import Callable

from .fb import Framebuffer, FramebufferGone
from .gcode import GcodeInfoCache, current_layer
from .history import HistoryStore, PrintTracker, reconcile
from .model import Border, LastPrint, PrintEvent, Snapshot, View
from .parse import parse_flow, parse_snapshot, parse_speed
from .render import Renderer, dirty_boxes
from .view import build_view

log = logging.getLogger("pi_screen")

POLL_SECONDS = 2
QUERY_SECONDS = 15
FRAME_SECONDS = 0.5
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


class Dashboard:
    def __init__(self, client, tracker: PrintTracker, gcode_cache: GcodeInfoCache,
                 uploads_dir: Path, renderer: Renderer, fb, tz: tzinfo | None,
                 clock: Callable[[], float] = time.time) -> None:
        self.client = client
        self.tracker = tracker
        self.gcode_cache = gcode_cache
        self.uploads_dir = uploads_dir
        self.renderer = renderer
        self.fb = fb
        self.tz = tz
        self.clock = clock
        self.snap = parse_snapshot(None, None)
        self.speed: int | None = None
        self.flow: int | None = None
        self.last_view: View | None = None
        self._frame = None
        self._rendered_key = None
        self._last_poll = float("-inf")
        self._last_query = float("-inf")
        # (file path, OctoPrint's last time-left estimate, when it was seen)
        self._estimate: tuple[str | None, int, float] | None = None
        # Push events and log lines arrive on the listener thread.
        self._lock = threading.Lock()

    def on_event(self, event: PrintEvent) -> None:
        with self._lock:
            # The last poll may describe the previous print; refresh first.
            self._poll(self.clock())
            self.tracker.on_event(event, self.snap, self._layer(), self.clock())

    def on_log(self, line: str) -> None:
        speed, flow = parse_speed(line), parse_flow(line)
        with self._lock:
            if speed is not None:
                self.speed = speed
            if flow is not None:
                self.flow = flow

    def tick(self) -> None:
        self.fb.check_same_device()
        with self._lock:
            now = self.clock()
            if now - self._last_poll >= POLL_SECONDS:
                self._poll(now)
            connected = self.snap.octoprint_ok and self.snap.printer_connected
            if connected and now - self._last_query >= QUERY_SECONDS:
                self._last_query = now
                self.client.send_commands(["M220", "M221"])
            info = self._gcode_info()
            view = build_view(
                self.snap, self._last_with_layers(), self._layer(),
                info.build_seconds if info else None,
                self.speed, self.flow, now, self.tz,
            )
        blink_on = int(now) % 2 == 0
        # Rendering costs ~80 ms on the Pi; skip it when nothing visible changed.
        key = (view, blink_on if view.border == Border.RED_BLINK else None)
        self.last_view = view
        if key == self._rendered_key and self._frame is not None:
            return
        self._rendered_key = key
        frame = self.renderer.render(view, blink_on=blink_on)
        boxes = dirty_boxes(self._frame, frame)
        if boxes:
            self.fb.write(frame, boxes)
        self._frame = frame

    def _poll(self, now: float) -> None:
        self._last_poll = now
        job = self.client.get_job()
        printer = self.client.get_printer() if job is not None else None
        self.snap = self._hold_estimate(parse_snapshot(job, printer), now)
        if not self.snap.printer_connected:
            # Values from before a disconnect may no longer be true.
            self.speed = self.flow = None

    def _hold_estimate(self, snap: Snapshot, now: float) -> Snapshot:
        """OctoPrint withdraws its time-left estimate whenever the estimate
        turns negative, which happens repeatedly near the end of a print that
        runs longer than predicted. Once it has given one for this print,
        count down from the last value rather than fall back to the slicer."""
        if snap.state not in ("printing", "paused"):
            self._estimate = None
            return snap
        if snap.print_time_left is not None:
            self._estimate = (snap.file_path, snap.print_time_left, now)
            return snap
        if self._estimate is None or self._estimate[0] != snap.file_path:
            self._estimate = None
            return snap
        _, left, seen_at = self._estimate
        return replace(snap, print_time_left=max(0, int(left - (now - seen_at))))

    def _gcode_info(self):
        if not self.snap.file_path:
            return None
        return self.gcode_cache.get(self.uploads_dir / self.snap.file_path)

    def _last_with_layers(self) -> LastPrint | None:
        """A print recovered from OctoPrint's history has no layer count;
        take the total from the G-code file when it is still uploaded."""
        last = self.tracker.last
        if not last or last.layer_total or not last.file_path:
            return last
        info = self.gcode_cache.get(self.uploads_dir / last.file_path)
        if not info or not info.total_layers:
            return last
        layer = info.total_layers if last.outcome == "done" else last.layer
        return replace(last, layer=layer, layer_total=info.total_layers)

    def _layer(self) -> tuple[int, int] | None:
        info = self._gcode_info()
        if not info or not info.total_layers:
            return None
        return current_layer(info, self.snap.filepos or 0), info.total_layers


def run_loop(dashboard: Dashboard, sleep: Callable[[float], None] = time.sleep) -> int:
    """Draw until the LCD goes away; exit status 3 lets systemd restart us."""
    try:
        while True:
            dashboard.tick()
            sleep(FRAME_SECONDS)
    except FramebufferGone as e:
        log.warning("LCD framebuffer gone, exiting for restart: %s", e)
        return 3


def load_api_key(config_path: Path) -> str:
    """Read ``api: key:`` from OctoPrint's config.yaml without PyYAML, which
    the Pi's system Python does not have."""
    in_api = False
    for line in config_path.read_text().splitlines():
        if line and not line[0].isspace():
            in_api = line.rstrip() == "api:"
            continue
        stripped = line.strip()
        if in_api and line.startswith("  ") and not line.startswith("   ") and stripped.startswith("key:"):
            return stripped[len("key:"):].strip().strip("'\"")
    raise ValueError(f"no api key in {config_path}")


class FixtureClient:
    """Serves saved OctoPrint responses for --fixture."""

    def __init__(self, name: str) -> None:
        self.job = json.loads((FIXTURES / f"job_{name}.json").read_text())
        printer = FIXTURES / f"printer_{name}.json"
        if not printer.exists():
            printer = FIXTURES / "printer_printing.json"
        self.printer = json.loads(printer.read_text())

    def get_job(self):
        return self.job

    def get_printer(self):
        return self.printer

    def get_file(self, path):
        return None

    def send_commands(self, commands):
        return True


def main(argv: list[str] | None = None) -> int:
    home = Path.home()
    parser = argparse.ArgumentParser(prog="pi_screen")
    parser.add_argument("--octoprint", default="http://127.0.0.1:5000")
    parser.add_argument("--config", type=Path, default=home / ".octoprint" / "config.yaml")
    parser.add_argument("--state", type=Path, default=home / ".local" / "state" / "pi-screen" / "last-print.json")
    parser.add_argument("--uploads", type=Path, default=home / ".octoprint" / "uploads")
    parser.add_argument("--fb", default="/dev/fb0")
    parser.add_argument("--fixture", help="draw tests/fixtures/job_<NAME>.json instead of polling")
    parser.add_argument("--png", type=Path, help="render one frame to this PNG and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    store = HistoryStore(args.state)
    tracker = PrintTracker(store)
    if args.fixture:
        client = FixtureClient(args.fixture)
    else:
        from .octoprint import OctoPrintClient
        client = OctoPrintClient(args.octoprint, load_api_key(args.config))
        job = client.get_job()
        path = ((job or {}).get("job") or {}).get("file", {}).get("path")
        if path:
            reconciled = reconcile(tracker.last, client.get_file(path))
            if reconciled is not tracker.last:
                tracker.last = reconciled
                store.save(reconciled)

    if args.png:
        class _NoFramebuffer:
            def check_same_device(self):
                pass

            def write(self, img, boxes):
                img.save(args.png)

        dashboard = Dashboard(client, tracker, GcodeInfoCache(), args.uploads, Renderer(), _NoFramebuffer(), None)
        dashboard.tick()
        return 0

    fb = Framebuffer(args.fb)
    try:
        fb.open()
    except FramebufferGone as e:
        log.warning("LCD not available: %s", e)
        return 3
    dashboard = Dashboard(client, tracker, GcodeInfoCache(), args.uploads, Renderer(), fb, None)

    if not args.fixture:
        from .octoprint import PushListener
        ws_url = args.octoprint.replace("http", "ws", 1).rstrip("/") + "/sockjs/websocket"
        PushListener(ws_url, client, dashboard.on_event, dashboard.on_log).start()

    try:
        return run_loop(dashboard)
    except KeyboardInterrupt:
        return 0
    finally:
        fb.close()


if __name__ == "__main__":
    sys.exit(main())
