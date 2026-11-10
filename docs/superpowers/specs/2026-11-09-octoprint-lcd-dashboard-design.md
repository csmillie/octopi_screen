# OctoPrint LCD Dashboard — Design

Date: 2026-11-09
Status: Draft for review

## Intent

A 1024×600 USB LCD sits next to an Ender 3 driven by OctoPi on a Raspberry Pi 4. The owner wants to glance at it from across the room and know, without opening a browser: how long the current print has left, when it will finish, how far through it is, and whether anything needs attention.

The screen is a read-only status display. It does not control the printer.

## Hardware and platform

| Item | Value |
|---|---|
| Host | Raspberry Pi 4, **1 GB RAM** (868 MB usable), 4 cores, Raspberry Pi OS Bookworm **32-bit**, kernel `6.6.51+rpt-rpi-v7l` |
| SSH | `octopi@octopi.local` |
| OctoPrint | 1.11.8 in `~/oprint` (Python venv), API at `http://127.0.0.1:5000`. API key in `~/.octoprint/config.yaml` under `api.key` |
| Printer | Ender 3, Marlin, `/dev/ttyACM0` at 115200. Printer profile `_default` ("Ender3") |
| Slicer | Simplify3D 5.1.2 |
| LCD | MS912x USB display, 1024×600, driver `ms912x` (DKMS `ms912x/1.1`). Exposed as DRM card (currently `/dev/dri/card2`, connector `DPI-1`) and as fbdev `/dev/fb0` (`ms912xdrmfb`, 32 bpp XRGB8888, pitch 4096) |
| Fonts | DejaVu Sans is installed |

### LCD driver behaviour the dashboard must respect

These were established by testing on 2026-11-09 and are handled in the driver, but they shape the dashboard:

- The driver converts every changed pixel from XRGB8888 to YUV422 on the CPU and sends it over USB. A full-screen update is 1.2 MB and costs CPU on a Pi that is also running a print. **Redraw only regions that changed.**
- The driver sends a 20-byte keepalive every second when the screen is static, so a static screen is safe. The dashboard does not need to redraw to keep the display alive.
- fbcon currently owns `/dev/fb0` and draws a login console. The dashboard must stop fbcon from drawing over it: blank the cursor and unbind or hide the console while the dashboard runs, and restore it on exit.

## Goals

1. Show, on one screen, while a print is running:
   - print file name
   - progress (percentage and a ring)
   - time remaining
   - start time
   - estimated finish time ("done at")
   - current layer / total layers
   - nozzle temperature (actual → target)
   - bed temperature (actual → target)
   - speed percentage (printer feed-rate override, `M220`)
   - flow percentage (printer extrusion multiplier, `M221`)
2. When nothing is printing, show the last finished print. If there is none, show zeros.
3. Make problems visible from across the room: blinking red border for a failed or cancelled print, yellow for paused.
4. Start automatically at boot and recover on its own from OctoPrint restarts, printer disconnects and LCD replugs.

## Non-goals

- Touch input, buttons or any control of the printer or OctoPrint.
- Webcam video or snapshots on the LCD.
- Showing more than one printer.
- A browser-based UI. A kiosk browser was considered and rejected: it needs ~300–500 MB of RAM on a 1 GB Pi and repaints large areas, each of which the LCD driver converts on the CPU.

## Anti-goals (failure even if it "works")

- Anything that can disturb a running print: high CPU or USB load, crashes inside OctoPrint, or G-code other than the two report commands below.
- Changing OctoPrint settings or installing OctoPrint plugins.
- Showing stale numbers as if they were live. When data is unavailable, show `—`, not the last value and not zero.

## Layout (chosen: "C · Progress ring + list")

The approved mockup is in `.superpowers/brainstorm/*/content/layout.html`, option C. Summary, in 1024×600 pixels:

- Dark background (`#0b0f14`), light text (`#e6edf3`), dim labels (`#8b98a5`), accent cyan (`#4cc9f0`) for progress and finish time, warm amber (`#ffb454`) for temperatures.
- **Top bar, full width:** the file name. Long names must work (owner's request): shrink the font to fit, down to a readable minimum, then shorten with `…` in the middle so the start of the name and the `.gcode` end both stay visible (middle ellipsis is my call). This replaces the mockup's file name at the top of the right column.
- **Left ~42% of width, below the bar:** a progress ring filled clockwise by completion. Inside the ring: time remaining as large bold `H:MM`, the label "remaining", and the percentage.
- **Right side, below the bar:** a two-column list:
  - Started — `HH:MM`
  - Done at — `HH:MM`
  - Layer — `N / Total`
  - Nozzle — `actual° → target°`
  - Bed — `actual° → target°`
  - Speed — `N%`
  - Flow — `N%`
- **Status word** under the ring percentage when not simply printing: `PAUSED`, `FAILED`, `CANCELLED`, `COMPLETE`, `PRINTER OFFLINE`, `OCTOPRINT OFFLINE`.
- **Border:** a frame around the whole screen. Thickness is the builder's call; it must be visible from a few metres.

Text must be readable from a few metres. The mockup's proportions are the guide; exact pixel sizes are the builder's call.

## States

| State | How detected | Ring / numbers | Border |
|---|---|---|---|
| Printing | OctoPrint state `Printing` | live | none |
| Paused | state `Paused` / `Pausing` | live (frozen progress, live temps) | **solid yellow** (owner said yellow; solid rather than blinking is my call, since pauses are usually deliberate) |
| Failed | last print ended with failure | frozen at the point it stopped | **blinking red for 5 minutes**, then **solid red** until the next print starts (the solid-red hold is my call) |
| Cancelled | last print was cancelled | frozen at the point it stopped | same as Failed (treating cancelled like failed is my call) |
| Idle after success | operational, last print succeeded | last print: ring 100%, remaining `0:00`, "Done at" = actual finish, layer `Total / Total`, plus actual duration (e.g. "took 7:37") — duration line is my call | none |
| Idle, no history | no previous print known | all zeros; file line reads "No print yet" (wording is my call) | none |
| Printer disconnected | OctoPrint up, printer state `Offline`/closed | print fields as last known; temps, speed, flow `—` | **grey**, status `PRINTER OFFLINE` (my call) |
| OctoPrint unreachable | API request fails | everything `—` | **grey**, status `OCTOPRINT OFFLINE` (my call) |

Temperatures stay live in every state where the printer is connected, so the owner can watch the hotend and bed cool after a print.

### When several states apply (my call)

The border shows the most important state, in this order:

1. Failed / Cancelled — red (blinking for 5 minutes, then solid)
2. Paused — yellow
3. Printer offline / OctoPrint offline — grey
4. Normal — no border

The status word lists both when an offline condition coincides with a higher state, e.g. `FAILED · PRINTER OFFLINE`. The likely real case: the USB serial link drops mid-print, OctoPrint reports the print failed and the printer offline together. The screen must show red, not grey.

### Persistence (my call)

The dashboard keeps a small JSON state file on the Pi (e.g. `~/.local/state/pi-screen/last-print.json`). It records the last print's file, start time, end time, outcome (`done` / `failed` / `cancelled`), and the progress, layer and remaining time at the moment it stopped. It is written only on print start, end, failure and cancel events (a handful of writes per print, which is fine for the SD card), using an atomic write (write temp file, rename).

On startup the dashboard loads this file so a failure or the last finished print survives a reboot, including the solid-red hold. If OctoPrint's file metadata shows a newer print than the state file (an event was missed while the dashboard was down), use the metadata: it gives `success` true/false and print time but cannot tell cancelled from failed, so show `FAILED` in that case. The 5-minute blink is measured from the recorded end time, so it does not restart after a reboot.

## Data sources

| Field | Source |
|---|---|
| File name | `GET /api/job` → `job.file.display` |
| Progress % | `/api/job` → `progress.completion` |
| Time remaining | `/api/job` → `progress.printTimeLeft` (OctoPrint's live estimate) whenever OctoPrint has one. **Until it does** (it is `null` early in a print), use the slicer's estimate from the G-code: Simplify3D writes `;   Build Time: 6 hours 18 minutes` in its end-of-file summary; remaining = slicer total − elapsed print time, never below 0. Switch to OctoPrint's figure as soon as it appears and keep following its updates (owner's decision). Mark a slicer-based figure with a small dim `est.` under the time (my call). If neither exists, show `—`. On this machine both estimates undershot a real 7:37 print (Simplify3D 6:18, OctoPrint initial 5:49), which is why OctoPrint's self-correcting figure takes over. |
| Start time | now − `progress.printTime` at print start, or from the `PrintStarted` event if the builder uses the push API |
| Done at | now + `printTimeLeft` |
| Nozzle / bed | `GET /api/printer` → `temperature.tool0` / `temperature.bed`, `actual` and `target` |
| State | `/api/printer` → `state.flags`, `/api/job` → `state` |
| Layer total | Count lines matching `^; layer [0-9]+, Z` in the G-code file (Simplify3D writes one per layer; the last print had 1010). Read the file from `~/.octoprint/uploads/<path>` once per print and cache by path + mtime. |
| Current layer | The highest `; layer N` whose byte offset is ≤ `progress.filepos`. Build an offset table during the same single pass. |
| Layers unknown | If the file has no layer comments (another slicer, an SD-card print, a deleted file), show `— / ?` so it is clear the total is unknown (owner's decision). |
| Last print (idle) | OctoPrint's file metadata history for the selected file, and `/api/job` after completion (`lastPrintTime`). The builder decides the exact source, but it must survive an OctoPrint restart and a Pi reboot. |
| Failed vs cancelled vs success | OctoPrint print history (`success: true/false`) and/or events `PrintFailed`, `PrintCancelled`, `PrintDone`. |
| Speed % | Send `M220` (no arguments). **Verified 2026-11-09** on this printer (Marlin 2.1.2.9, "Ender-3 BMG"): replies `FR:80%`. |
| Flow % | Send `M221` (no arguments). **Verified:** replies `echo:E0 Flow: 100%`. |

### Speed and flow queries

- Sent through OctoPrint's API (`POST /api/printer/command`), never by opening the serial port directly.
- Every 15 seconds, only while the printer is connected.
- Responses arrive on OctoPrint's serial stream, not in the command's HTTP response, so the dashboard **must** subscribe to OctoPrint's SockJS push API (`/sockjs/websocket`, authenticated with `{"auth": "<name>:<session>"}` from `POST /api/login {"passive": true}` using the API key). This was tested on the Pi: the `current.logs` entries contain `Recv: FR:80%` and `Recv: echo:E0 Flow: 100%`. The same socket also delivers print events (`PrintStarted`, `PrintDone`, `PrintFailed`, `PrintCancelled`, `PrintPaused`, `PrintResumed`). `tornado` is available in OctoPrint's venv; a websocket client in the dashboard's own environment is the builder's choice.
- These are the **only** G-code commands the dashboard may send.
- If no reply is parsed within a reasonable window, show `—`.

## Rendering

- Python 3 with Pillow, drawing into an in-memory 1024×600 image and writing changed regions to `/dev/fb0`. Writing to the framebuffer through fbdev produces damage tracking in the DRM fbdev emulation, so only touched rows/regions reach USB.
- Update cadence: poll status every 2 seconds. Redraw only regions whose content changed (the ring, each value, the border).
- The blinking border toggles about once per second and redraws only the four border strips.
- Clocks use the Pi's local time zone, 24-hour `HH:MM` (confirmed by owner). A finish time on a later day gets ` +1d`.

## Process and deployment

- One Python program, run as a **systemd service** as user `octopi`, started at boot after OctoPrint and restarted on failure. It runs separately from OctoPrint so a crash cannot affect a print.
- System Python is 3.11.2 with Pillow 9.4.0 and requests 2.28.1 already installed (verified). Prefer these; add a small venv only if a websocket client is needed. Avoid anything that pulls in a GUI stack.
- Needs access to `/dev/fb0`: it is `root:video 0660`, and `octopi` is in `video` (verified).
- Must cope with: OctoPrint restarting, the printer disconnecting and reconnecting, the LCD being unplugged and replugged, and the Pi rebooting.
- **LCD unplug/replug resets the service and the display (owner's decision).** When the framebuffer write fails, `/dev/fb0` disappears, or `/dev/fb0` is replaced by a new device (different inode/device number, checked every tick), the process exits with an error. systemd restarts it after 5 s, re-running the console helper (a replugged LCD gets a fresh fbcon binding that must be removed again) and redrawing the full screen. If `/dev/fb0` is missing at startup, the process exits and systemd keeps retrying every 5 s with no start limit.
- **Privileges:** the dashboard runs as `octopi` (owner asked). Root is used only (a) once, to install the unit and the console helper, and (b) by systemd for the helper's `ExecStartPre=+` / `ExecStopPost=+` lines. The helper is root-owned in `/usr/local/sbin` so `octopi` cannot change what runs as root.
- Resource budget: under ~50 MB RSS, and negligible CPU when idle.
- Source lives in this repo (`~/WWW/pi_screen` on the owner's Mac) and is deployed to the Pi over SSH. The deploy mechanism is the builder's call (rsync + a small script is enough).

## Testing

- Rendering must be testable on the Mac without the Pi: render each state to a PNG from fixture JSON (captured `/api/job`, `/api/printer` responses for printing, paused, failed, idle, offline).
- Layer parsing tested against a real Simplify3D file (`Snacker2-vert.gcode` on the Pi, 1010 layers).
- On the Pi, verify each state on the LCD using the webcam snapshot at `http://octopi.local/webcam/?action=snapshot`.

## Left to the builder

- Exact pixel sizes, font weights and border width, within the approved layout.
- How to split work between REST polling and the SockJS push API (SockJS itself is required, see above).
- How the fbcon console is suppressed and restored.
- Exact source of last-print history.
- Project structure, packaging and deploy script.
