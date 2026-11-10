# OctoPrint LCD Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python service on the OctoPi that draws a read-only print status dashboard (layout C) on the 1024×600 MS912x USB LCD.

**Architecture:** Pure, testable core (G-code layer index, OctoPrint JSON parsing, print history, view model, Pillow renderer) wrapped by a thin I/O shell (REST + SockJS client, framebuffer writer, main loop). Develop and test on the Mac against fixtures, deploy to the Pi with rsync, run under systemd as `octopi`.

**Tech Stack:** Python 3.11, Pillow 9.4.0, requests 2.28.1, websocket-client 1.8.0, pytest 8.3.5, systemd.

**Spec:** `docs/superpowers/specs/2026-11-09-octoprint-lcd-dashboard-design.md`

## Global Constraints

- Pi: Python 3.11.2 with system Pillow 9.4.0 and requests 2.28.1 (already installed). Use a venv at `~/pi-screen/.venv` created with `--system-site-packages`; the only pip install is `websocket-client==1.8.0`.
- Mac: `uv venv --python 3.11 .venv` and `uv pip install pillow==9.4.0 requests==2.28.1 websocket-client==1.8.0 pytest==8.3.5`.
- Screen is 1024×600; `/dev/fb0` is 32 bpp, stride 4096, byte order B,G,R,X (Pillow raw mode `"BGRX"`).
- Fonts: `pi_screen/fonts/DejaVuSans.ttf`, `pi_screen/fonts/DejaVuSans-Bold.ttf` (already copied from the Pi, license in `LICENSE-DejaVu`).
- Colours: background `#0b0f14`, text `#e6edf3`, dim `#8b98a5`, accent `#4cc9f0`, warm `#ffb454`, red `#ff4d4f`, yellow `#ffd166`, grey `#5c6773`, ring track `#1c2630`.
- Only G-code ever sent: `M220` and `M221`, no arguments, every 15 s, only while the printer is connected, via `POST /api/printer/command`.
- Unavailable data renders as `—` (U+2014), never as zero or a stale value. Exception: idle with no history renders zeros (spec).
- Clocks: local time zone (Pi is `America/Toronto`), 24-hour `HH:MM`.
- Poll REST every 2 s; blink border at 1 Hz for 5 minutes after a failure/cancel, measured from the recorded end time.
- Resource budget: < 50 MB RSS, negligible idle CPU. No GUI stack, no OctoPrint plugins, no OctoPrint settings changes.
- Commit messages end with `Claude-Session: https://claude.ai/code/session_019YcZCxHTfeXRQRKDf9enzK`; never add Co-Authored-By.

## Review Focus

1. **`printTimeLeft` is `null`** early in a print (OctoPrint has no estimate yet) → remaining comes from the slicer's build time minus elapsed, marked `est.`; with no slicer time either, `—`. Tests in Task 4.
2. **Finish time after midnight** (long prints) → done-at shows `HH:MM +1d`. Test in Task 4.
3. **G-code without `; layer N` comments** (another slicer, SD print, deleted file) → layer shows `— / ?`, no exception. Tests in Tasks 1 and 4.
4. **LCD unplugged / replugged** → a failed write or a replaced `/dev/fb0` raises `FramebufferGone`; the process exits 3 and systemd restarts it, re-running the console helper and redrawing everything. Tests in Tasks 6 and 7.
5. **Long file names** → full-width top bar; font shrinks to 24 px, then middle `…` keeps the start and `.gcode` end. Tests in Task 5.

---

## File Structure

```
pyproject.toml                 project metadata, pytest config
pi_screen/__init__.py
pi_screen/gcode.py             layer index from Simplify3D comments
pi_screen/model.py             dataclasses: Temp, Snapshot, PrintEvent, LastPrint, Border, View
pi_screen/parse.py             OctoPrint JSON / serial line / push event parsing
pi_screen/history.py           persistent LastPrint store, event tracker, metadata reconcile
pi_screen/view.py              Snapshot + history -> View (formatting, state priority)
pi_screen/render.py            View -> 1024x600 Pillow image; dirty-box diff
pi_screen/fb.py                write image regions to /dev/fb0
pi_screen/octoprint.py         REST client and SockJS push listener (I/O only)
pi_screen/app.py               main loop, CLI (--fixture, --png)
pi_screen/fonts/               DejaVu fonts (present)
tests/fixtures/*.json          captured/constructed OctoPrint responses
tests/test_*.py                one test module per core module
deploy/pi-screen.service       systemd unit
deploy/pi-screen-console       fbcon unbind/rebind helper (installed root-owned)
deploy/deploy.sh               rsync + venv + (first run) install unit
```

---

### Task 1: Project setup and G-code info (layers, slicer estimate)

**Files:**
- Create: `pyproject.toml`, `pi_screen/__init__.py`, `pi_screen/gcode.py`, `tests/test_gcode.py`, `tests/fixtures/s3d_sample.gcode`

**Interfaces:**
- Produces:
  - `GcodeInfo` frozen dataclass: `offsets: tuple[int, ...]` (byte offset of each `; layer N, Z` line, file order), `build_seconds: int | None` (slicer estimate); property `total_layers -> int`.
  - `read_gcode_info(path: Path) -> GcodeInfo` — single binary pass. Layers: regex `rb"^; layer \d+, Z"`. Build time: regex `rb"^;\s*Build Time:\s*(?:(\d+) hours?)?\s*(?:(\d+) minutes?)?"` (Simplify3D summary, e.g. `;   Build Time: 6 hours 18 minutes`); last match wins; `None` if absent.
  - `current_layer(info: GcodeInfo, filepos: int) -> int` — number of offsets ≤ `filepos` (`bisect_right`); 0 before the first layer.
  - `GcodeInfoCache().get(path: Path) -> GcodeInfo | None` — cached by `(path, st_mtime_ns)`; `None` if the file does not exist.

- [ ] **Step 1:** Create `.venv` on the Mac (Global Constraints command). Write `pyproject.toml` (`name = "pi-screen"`, `requires-python = ">=3.11"`, `[tool.pytest.ini_options] testpaths = ["tests"]`). Create `tests/fixtures/s3d_sample.gcode`: a 3-line Simplify3D header, then `; layer 1, Z = 0.200` … `; layer 3, Z = 0.600` each followed by two `G1` lines, then `; layer end`, `; Build Summary`, `;   Build Time: 1 hours 5 minutes`.
- [ ] **Step 2: Write failing tests** in `tests/test_gcode.py`:
  - `test_total_counts_layer_comments`: `total_layers == 3`.
  - `test_build_time_parsed`: `build_seconds == 3900`; a file with `;   Build Time: 42 minutes` → 2520.
  - `test_current_layer_before_first_layer_is_zero`: `current_layer(info, 0) == 0`.
  - `test_current_layer_at_and_after_offsets`: `current_layer(info, info.offsets[1]) == 2`; `current_layer(info, 10**9) == 3`.
  - `test_file_without_comments` (Review Focus 3): plain G-code → `total_layers == 0`, `build_seconds is None`, `current_layer(info, 500) == 0`.
  - `test_cache_returns_none_for_missing_file` and `test_cache_rebuilds_after_mtime_change`.
- [ ] **Step 3:** Run `.venv/bin/pytest tests/test_gcode.py -v` → FAIL (module missing).
- [ ] **Step 4:** Implement `pi_screen/gcode.py` per Interfaces.
- [ ] **Step 5:** Run tests → PASS. Then check against the real file: `scp octopi@octopi.local:.octoprint/uploads/Snacker2-vert.gcode /tmp/` → `total_layers == 1010`, `build_seconds == 22680` (6 h 18 m).
- [ ] **Step 6:** Commit `pyproject.toml pi_screen tests` — "Add project setup and G-code info reader".

### Task 2: Models and OctoPrint parsing

**Files:**
- Create: `pi_screen/model.py`, `pi_screen/parse.py`, `tests/test_parse.py`, `tests/fixtures/job_printing.json`, `printer_printing.json`, `job_idle.json`, `printer_idle.json`, `job_paused.json`

**Interfaces:**
- Produces (`model.py`):
  - `Temp(actual: float, target: float)`
  - `Snapshot(octoprint_ok: bool, printer_connected: bool, state: Literal["printing","paused","idle","offline"], file_display: str|None, file_path: str|None, completion: float|None, print_time: int|None, print_time_left: int|None, filepos: int|None, nozzle: Temp|None, bed: Temp|None)`
  - `PrintEvent(kind: Literal["started","done","failed","cancelled","paused","resumed"], path: str|None, duration: float|None)`
  - `LastPrint(file_display: str, file_path: str|None, started_at: float, ended_at: float|None, outcome: Literal["printing","done","failed","cancelled"], completion: float|None, layer: int|None, layer_total: int|None, remaining: int|None, duration: int|None)` with `to_dict()` / `from_dict()`.
  - `Border` enum: `NONE, RED_BLINK, RED, YELLOW, GREY`.
  - `View(file_line, percent, remaining, status, started, done_at, layer, nozzle, bed, speed, flow: str; took: str|None; remaining_is_estimate: bool; ring_fraction: float; border: Border)`.
- Produces (`parse.py`):
  - `parse_snapshot(job: dict|None, printer: dict|None) -> Snapshot` — `job is None` ⇒ `octoprint_ok=False, state="offline"`; `printer is None` (HTTP 409 "Printer is not operational") ⇒ `printer_connected=False`, temps `None`.
  - State mapping from `job["state"]`: `Printing`, `Cancelling`, `Finishing`, `Starting` → `printing`; `Paused`, `Pausing`, `Resuming` → `paused`; `Operational` → `idle`; anything else (`Offline`, `Error`, `Offline after error`, `Closed`, …) → `offline`.
  - `parse_speed(line: str) -> int|None` and `parse_flow(line: str) -> int|None` — regexes `FR:(\d+)%` and `Flow:\s*(\d+)%`.
  - `parse_event(msg: dict) -> PrintEvent|None` — from `{"event": {"type": ..., "payload": ...}}`: `PrintStarted`→started, `PrintDone`→done, `PrintCancelled`→cancelled, `PrintFailed`→failed unless `payload["reason"] == "cancelled"` (then cancelled), `PrintPaused`→paused, `PrintResumed`→resumed; `path = payload.get("path")`, `duration = payload.get("time")`; anything else → `None`.

- [ ] **Step 1:** Create fixtures. `job_idle.json`/`printer_idle.json`: copy the real responses captured during brainstorming (spec Data sources; `state: "Operational"`, file `Snacker2-vert.gcode`, completion 100, temps 18.12/19.22 with targets 0). `job_printing.json`: same file, `state: "Printing"`, completion 40.8, printTime 11200, printTimeLeft 16200, filepos 1830000; `printer_printing.json`: tool0 205.1/205, bed 60.0/60. `job_paused.json`: `state: "Paused"`.
- [ ] **Step 2: Write failing tests** (`tests/test_parse.py`):
  - `test_printing_snapshot`: state `printing`, completion 40.8, `print_time_left == 16200`, `nozzle == Temp(205.1, 205.0)`.
  - `test_idle_snapshot`: state `idle`, `printer_connected`.
  - `test_paused_snapshot`: state `paused`.
  - `test_octoprint_down`: `parse_snapshot(None, None)` → `octoprint_ok False`, state `offline`.
  - `test_printer_disconnected`: job with `state "Offline"`, printer `None` → `printer_connected False`, `nozzle is None`.
  - `test_null_time_left_stays_none` (Review Focus 1): `printTimeLeft: null` → `print_time_left is None`.
  - `test_parse_speed`: `"Recv: FR:80%"` → 80; `"Recv: ok"` → None.
  - `test_parse_flow`: `"Recv: echo:E0 Flow: 100%"` → 100.
  - `test_parse_event_failed_with_cancelled_reason_is_cancelled`, `test_parse_event_done`, `test_parse_event_unrelated_is_none`.
  - `test_lastprint_round_trip`: `LastPrint.from_dict(lp.to_dict()) == lp`.
- [ ] **Step 3:** Run → FAIL. **Step 4:** Implement `model.py` and `parse.py`. **Step 5:** Run → PASS.
- [ ] **Step 6:** Commit — "Add models and OctoPrint response parsing".

### Task 3: Print history

**Files:**
- Create: `pi_screen/history.py`, `tests/test_history.py`

**Interfaces:**
- Consumes: `LastPrint`, `PrintEvent`, `Snapshot` (Task 2).
- Produces:
  - `HistoryStore(path: Path)`: `load() -> LastPrint|None` (missing or corrupt file → `None`), `save(lp: LastPrint) -> None` (write `path.with_suffix(".tmp")`, `fsync`, `os.replace`; create parent dirs).
  - `PrintTracker(store: HistoryStore)`: attribute `last: LastPrint|None` (loaded at init); `on_event(ev: PrintEvent, snap: Snapshot, layer: tuple[int,int]|None, now: float) -> None`.
    - `started` → `LastPrint(outcome="printing", started_at=now, ended_at=None, file_display=snap.file_display or basename(ev.path), …)`, save.
    - `done` → outcome `done`, `ended_at=now`, completion 100, layer = `(total, total)`, remaining 0, `duration = int(ev.duration)` if given else `now - started_at`; save.
    - `failed` / `cancelled` → that outcome, `ended_at=now`, completion/layer/remaining frozen from `snap` and `layer` at that moment, duration as above; save.
    - `paused` / `resumed` → no change.
  - `reconcile(stored: LastPrint|None, metadata: dict|None) -> LastPrint|None` — `metadata` is `GET /api/files/local/<path>` JSON. If `metadata["prints"]["last"]["date"]` is more than 5 s newer than `stored.ended_at` (or `stored` is `None`), return a new `LastPrint` with outcome `done` if `success` else `failed`, `ended_at = date`, `duration = int(printTime)`, `started_at = date - printTime`, completion 100 if success else `None`, layer `None`. Otherwise return `stored`.

- [ ] **Step 1: Write failing tests:** `test_save_load_round_trip`, `test_load_corrupt_file_returns_none`, `test_started_then_done_records_done_with_full_layers`, `test_failed_freezes_progress_and_layer` (completion 40.8, layer (412, 1010) preserved), `test_tracker_survives_restart` (new tracker on same path sees `last`), `test_reconcile_prefers_newer_metadata` (use the real metadata shape: `{"prints": {"last": {"date": 1794273531.79, "printTime": 27441.49, "success": true}}}`), `test_reconcile_keeps_stored_when_not_newer`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit — "Add persistent print history and event tracker".

### Task 4: View model

**Files:**
- Create: `pi_screen/view.py`, `tests/test_view.py`

**Interfaces:**
- Consumes: `Snapshot`, `LastPrint`, `View`, `Border` (Task 2).
- Produces:
  - `fmt_duration(seconds: int|None) -> str` — `None` → `—`; else `H:MM` (hours unbounded, e.g. `27:05`).
  - `fmt_clock(ts: float|None, now: float, tz: tzinfo) -> str` — `None` → `—`; `HH:MM`, plus ` +1d` / ` +2d` when the local date is after `now`'s.
  - `select_border(snap: Snapshot, last: LastPrint|None, now: float) -> Border` — priority: last outcome failed/cancelled and no newer print running → `RED_BLINK` if `now - ended_at < 300` else `RED`; else `snap.state == "paused"` → `YELLOW`; else not `octoprint_ok` or not `printer_connected` → `GREY`; else `NONE`.
  - `build_view(snap, last, layer: tuple[int,int]|None, slicer_seconds: int|None, speed: int|None, flow: int|None, now: float, tz) -> View`. `layer` is `None` when the G-code has no layer comments.

Rules `build_view` must follow (each is a test):
- Printing: ring = completion/100; remaining = `fmt_duration(left)` where `left = print_time_left` if not `None`, else `max(0, slicer_seconds - print_time)` if both are known (then `remaining_is_estimate = True`), else `None`; done_at = `fmt_clock(now + left)`; started = `fmt_clock(last.started_at)` if the tracker has a `printing` record, else `now - print_time`; layer `N / T`, or `— / ?` when `layer is None`; status `""`.
- Paused: as printing; status `PAUSED`.
- Idle with `last.outcome == "done"`: ring 1.0, percent `100%`, remaining `0:00`, done_at = `fmt_clock(ended_at)`, layer from record, `took = "took " + fmt_duration(duration)`, status `COMPLETE`.
- Failed / cancelled (`last` outcome, no print running): ring/layer/remaining frozen from `last`; status `FAILED` / `CANCELLED`.
- Idle with no history: file_line `No print yet`, percent `0%`, remaining `0:00`, started/done_at `00:00`, layer `0 / 0`, ring 0.0.
- Temps: `"{actual:.0f}° → {target:.0f}°"` when connected, else `—`. Speed/flow `"{n}%"` or `—`.
- Offline states: status gets ` · PRINTER OFFLINE` / ` · OCTOPRINT OFFLINE` appended to a higher state's word, or stands alone.

- [ ] **Step 1: Write failing tests** for every rule above, plus:
  - `test_null_time_left_uses_slicer_estimate` (Review Focus 1): `print_time_left None`, `print_time 600`, `slicer_seconds 22680` → remaining `6:08`, `remaining_is_estimate True`.
  - `test_octoprint_estimate_replaces_slicer`: both present → uses `print_time_left`, `remaining_is_estimate False`.
  - `test_no_estimate_at_all_shows_dash`: both `None` → remaining and done_at `—`, ring still 0.408.
  - `test_unknown_layers_show_question_mark`: `layer=None` → `— / ?`.
  - `test_done_at_after_midnight_marks_next_day` (Review Focus 2): now = 23:30 local, left = 2 h → `01:30 +1d`.
  - `test_failed_plus_printer_offline_is_red_with_both_words`: border `RED_BLINK`, status `FAILED · PRINTER OFFLINE`.
  - `test_blink_stops_after_five_minutes`: `ended_at = now - 301` → `RED`.
  - `test_new_print_clears_red`: last failed, snapshot printing → border `NONE`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit — "Add view model with state priority and formatting".

### Task 5: Renderer

**Files:**
- Create: `pi_screen/render.py`, `tests/test_render.py`

**Interfaces:**
- Consumes: `View`, `Border`.
- Produces:
  - `WIDTH, HEIGHT = 1024, 600`
  - `Renderer(fonts_dir: Path = <package>/fonts)`; `render(view: View, blink_on: bool) -> PIL.Image.Image` (mode `RGB`, 1024×600).
  - `dirty_boxes(old: Image|None, new: Image, band: int = 40) -> list[tuple[int,int,int,int]]` — `old is None` → `[(0,0,1024,600)]`; else per horizontal band of `band` rows, `ImageChops.difference(...).getbbox()` offset into screen coordinates; empty list if identical.

Layout (from approved mockup C, with the file name moved to a full-width top bar; exact sizes are the implementer's call within this):
- Border: 12 px frame in the border colour; `RED_BLINK` draws red when `blink_on`, background otherwise.
- Top bar: y 24–84, x 28–996, file name in Bold, starting at 40 px and shrinking in 2 px steps to a 24 px minimum to fit; if it still doesn't fit at 24 px, keep the start and end and replace the middle with `…` (`fit_text(draw, text, font_path, max_px, max_width, min_px) -> tuple[str, ImageFont]`).
- Ring: centre (230, 340), outer radius 170, thickness 22, track `#1c2630`, arc in accent from 12 o'clock clockwise by `ring_fraction`. Inside: remaining in Bold ~92 px, `remaining` label ~22 px dim (`est. remaining` when `remaining_is_estimate`), percent ~30 px accent, status word ~26 px (red/yellow/grey to match border, text colour otherwise).
- Right column from x = 460, y from 110: rows label (dim ~28 px) / value (~34 px) at a fixed row pitch: Started, Done at (accent), Layer, Nozzle (warm), Bed (warm), Speed, Flow; `took …` dim beside Done at when present.

- [ ] **Step 1: Write failing tests:**
  - `test_image_size_and_background`: pixel (500, 590) == `#0b0f14` with `Border.NONE`.
  - `test_border_colours`: pixel (5, 300) is red for `RED`, yellow for `YELLOW`, grey for `GREY`; for `RED_BLINK`, red when `blink_on=True`, background when `False`.
  - `test_ring_fill_follows_fraction`: at fraction 0.25 a point on the ring at 2 o'clock is accent and at 8 o'clock is track colour.
  - `test_fit_text_shrinks_before_truncating`: a 50-char name fits at a size between 24 and 40 with no `…`.
  - `test_fit_text_middle_ellipsis` (Review Focus 5): a 160-char name ending `_fix3.gcode` returns text containing `…`, starting with the name's first characters and ending with `_fix3.gcode`; rendered, the non-background pixels in the top bar stay within x 28–996.
  - `test_dirty_boxes_full_on_first_frame`, `test_dirty_boxes_empty_when_identical`, `test_dirty_boxes_only_changed_band`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Render the five fixture states to PNG (`app.py` doesn't exist yet: a throwaway snippet in the test session is fine) and look at them; compare against mockup C. Adjust sizes.
- [ ] **Step 6:** Commit — "Add Pillow renderer and dirty-region diff".

### Task 6: Framebuffer writer and on-device spike

**Files:**
- Create: `pi_screen/fb.py`, `tests/test_fb.py`, `deploy/pi-screen-console`

**Interfaces:**
- Produces:
  - `class FramebufferGone(Exception)`
  - `Framebuffer(path: str = "/dev/fb0", width=1024, height=600, stride=4096)`: `open()` (missing device → `FramebufferGone`; records `(st_ino, st_rdev)` of the path), `close()`, `write(img: Image, boxes: list[box]) -> None` — for each box, `img.crop(box).tobytes("raw", "BGRX")`, then `os.pwrite` each row at `y*stride + x0*4`. `OSError` with `ENODEV`/`ENOENT`/`EIO` → close and raise `FramebufferGone`. `check_same_device() -> None` — `stat(path)` fails or `(st_ino, st_rdev)` differs from open time → raise `FramebufferGone` (catches a replug while the screen is static and nothing is being written).
  - `deploy/pi-screen-console off|on` (bash, run as root): finds the `/sys/class/vtconsole/vtcon*` whose `name` contains `frame buffer device` and writes `0` / `1` to its `bind`.

- [ ] **Step 1: Write failing tests** with a regular temp file standing in for the device (size `stride*height`): `test_write_full_frame_bytes` (pixel (1,0) red → bytes at offset 4 are `00 00 ff 00`), `test_write_box_only_touches_box`, `test_missing_device_raises_framebuffer_gone` (Review Focus 4: path that doesn't exist → `FramebufferGone`), `test_replaced_device_detected` (delete and recreate the temp file → `check_same_device()` raises `FramebufferGone`).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5: On-device spike** (needs the user's `sudo -v` for `pi-screen-console`): copy `pi_screen/` to `~/pi-screen-spike` on the Pi, run `sudo deploy/pi-screen-console off`, then a one-off script rendering the `job_printing` fixture through `Renderer` + `Framebuffer`. Verify with `capture.sh`-style webcam snapshot: dashboard visible, LED green, no console cursor drawing over it after 30 s; DRM state still `active=1`. Then `sudo deploy/pi-screen-console on` and confirm the console returns. Record the result in the commit message. If unbinding fbcon disables the CRTC, stop and report — the fallback is drawing through a DRM dumb buffer instead, which changes Task 6.
- [ ] **Step 6:** Commit — "Add framebuffer writer and console helper".

### Task 7: OctoPrint client and main loop

**Files:**
- Create: `pi_screen/octoprint.py`, `pi_screen/app.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `OctoPrintClient(base_url: str, api_key: str, timeout: float = 3)`: `get_job() -> dict|None`, `get_printer() -> dict|None` (409 or error → `None`), `get_file(path: str) -> dict|None`, `send_commands(cmds: list[str]) -> bool`, `passive_login() -> str|None` (returns `"name:session"`).
  - `PushListener(ws_url: str, client: OctoPrintClient, on_event: Callable[[PrintEvent], None], on_log: Callable[[str], None])` — daemon thread using `websocket.WebSocketApp`; sends `{"auth": ...}` on open; feeds `parse_event` results to `on_event` and each `current.logs` / `history.logs` line to `on_log`; reconnects with backoff 2 s doubling to 30 s.
  - `load_api_key(config_path: Path) -> str` — `yaml` is **not** installed system-wide; read the `api:` → `key:` value with a small line parser (two-space-indented `key:` under top-level `api:`), and test it.
  - `Dashboard(client, tracker, gcode_cache: GcodeInfoCache, uploads_dir: Path, renderer, fb, tz, clock=time.time)`: `on_event`, `on_log` (updates speed/flow via `parse_speed`/`parse_flow`), `tick() -> None` (`fb.check_same_device()`, poll if 2 s elapsed, query M220/M221 if 15 s elapsed and connected, build view with layer and `slicer_seconds` from the cached `GcodeInfo`, render with `blink_on = int(now) % 2 == 0`, write dirty boxes). `FramebufferGone` is **not** handled in the loop: it propagates, `main()` logs it and exits with status 3, and systemd restarts the service (spec: replug resets the service and the display).
  - CLI `python -m pi_screen.app [--octoprint URL] [--config PATH] [--state PATH] [--uploads PATH] [--fb PATH] [--fixture NAME] [--png OUT]`. Defaults: `http://127.0.0.1:5000`, `~/.octoprint/config.yaml`, `~/.local/state/pi-screen/last-print.json`, `~/.octoprint/uploads`, `/dev/fb0`. `--fixture NAME` renders `tests/fixtures/` data instead of polling; `--png OUT` writes one frame to a PNG and exits.
  - On startup: `tracker.last = reconcile(store.load(), client.get_file(job path))`.

- [ ] **Step 1: Write failing tests** with a fake client and a fake framebuffer (records boxes), fake clock:
  - `test_tick_polls_every_two_seconds`.
  - `test_speed_flow_queries_only_when_connected_every_15s`: commands sent are exactly `["M220", "M221"]`.
  - `test_log_lines_update_speed_and_flow` (`"Recv: FR:80%"` → view speed `80%`).
  - `test_static_screen_writes_nothing_after_first_frame`.
  - `test_framebuffer_gone_exits_with_status_3` (Review Focus 4): fake fb raises `FramebufferGone` from `check_same_device()` → `main()` returns 3.
  - `test_failed_event_turns_border_red`.
  - `test_load_api_key` (fixture YAML with `api:\n  key: ABC123\n` among other sections).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS. Run the whole suite.
- [ ] **Step 5:** `python -m pi_screen.app --fixture job_printing --png /tmp/printing.png` and inspect the PNG.
- [ ] **Step 6:** Commit — "Add OctoPrint client and main loop".

### Task 8: Deploy and verify on the LCD

**Files:**
- Create: `deploy/pi-screen.service`, `deploy/deploy.sh`, `README.md`

**Interfaces:**
- `pi-screen.service`: `After=octoprint.service network-online.target`, `User=octopi`, `ExecStartPre=+/usr/local/sbin/pi-screen-console off`, `ExecStart=/home/octopi/pi-screen/.venv/bin/python -m pi_screen.app`, `WorkingDirectory=/home/octopi/pi-screen`, `ExecStopPost=+/usr/local/sbin/pi-screen-console on`, `Restart=always`, `RestartSec=5`, `StartLimitIntervalSec=0` (in `[Unit]`, so a long unplug never exhausts restarts), `Nice=10`, `MemoryMax=80M`, `WantedBy=multi-user.target`.
  - The console helper is installed **root-owned in `/usr/local/sbin`**, never run from the user-writable checkout: `ExecStartPre=+` runs as root.
- `deploy.sh [--install]`: `rsync -a --delete --exclude .venv --exclude .git ./ octopi@octopi.local:pi-screen/`; create the venv if missing (`python3 -m venv --system-site-packages`) and `pip install websocket-client==1.8.0`; `systemctl restart pi-screen` if installed. `--install` additionally (needs the user's `sudo -v`): `install -m 0755 -o root deploy/pi-screen-console /usr/local/sbin/`, `install -m 0644 deploy/pi-screen.service /etc/systemd/system/`, `systemctl daemon-reload`, `systemctl enable --now pi-screen`.

- [ ] **Step 1:** Write the unit, `deploy.sh`, and a short `README.md` (what it is, deploy, rollback: `sudo systemctl disable --now pi-screen`).
- [ ] **Step 2:** Run `deploy/deploy.sh --install` (ask the user for `sudo -v` first).
- [ ] **Step 3: Verify on the LCD** with webcam snapshots: idle screen shows the last print (Snacker2-vert, 100%, `took 7:37`, live temps, speed `80%`). Then, by stopping the service and running `--fixture` for `job_printing`, `job_paused`, and a failed-history fixture, check each state's border and status word on the real panel.
- [ ] **Step 4:** Verify resilience: `sudo systemctl restart octoprint` → grey `OCTOPRINT OFFLINE`, then recovers. `rmmod ms912x && modprobe ms912x` → dashboard returns within ~10 s. Reboot is optional and the user's call.
- [ ] **Step 5:** Check resources: `systemctl status pi-screen` memory < 50 MB; `top` CPU near 0 when idle.
- [ ] **Step 6:** Commit — "Add systemd deployment".
