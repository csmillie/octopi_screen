"""The last print, kept on disk so a failure or a finished print survives a
reboot of the Pi or a restart of the dashboard."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath

from .model import LastPrint, PrintEvent, Snapshot

# Metadata must be this much newer than the stored end time to count as a
# print the dashboard missed; OctoPrint's and our timestamps differ slightly.
_RECONCILE_SLACK = 5.0


class HistoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> LastPrint | None:
        try:
            return LastPrint.from_dict(json.loads(self.path.read_text()))
        except (OSError, ValueError, TypeError):
            return None

    def save(self, last: LastPrint) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(last.to_dict(), f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)


class PrintTracker:
    """Updates and persists the last print as OctoPrint reports events."""

    def __init__(self, store: HistoryStore) -> None:
        self.store = store
        self.last = store.load()

    def on_event(
        self,
        event: PrintEvent,
        snap: Snapshot,
        layer: tuple[int, int] | None,
        now: float,
    ) -> None:
        if event.kind in ("paused", "resumed"):
            return

        total = layer[1] if layer else None
        if event.kind == "started":
            self._save(LastPrint(
                file_display=_display_name(snap, event),
                file_path=event.path or snap.file_path,
                started_at=now, ended_at=None, outcome="printing",
                completion=0.0, layer=0 if layer else None, layer_total=total,
                remaining=snap.print_time_left, duration=None,
            ))
            return

        started_at = self._started_at(event, snap, now)
        duration = int(event.duration) if event.duration is not None else int(now - started_at)
        if event.kind == "done":
            ended = LastPrint(
                file_display=_display_name(snap, event),
                file_path=event.path or snap.file_path,
                started_at=started_at, ended_at=now, outcome="done",
                completion=100.0, layer=total, layer_total=total,
                remaining=0, duration=duration,
            )
        else:
            ended = LastPrint(
                file_display=_display_name(snap, event),
                file_path=event.path or snap.file_path,
                started_at=started_at, ended_at=now, outcome=event.kind,
                completion=snap.completion,
                layer=layer[0] if layer else None, layer_total=total,
                remaining=snap.print_time_left, duration=duration,
            )
        self._save(ended)

    def _started_at(self, event: PrintEvent, snap: Snapshot, now: float) -> float:
        if self.last and self.last.outcome == "printing":
            return self.last.started_at
        if event.duration is not None:
            return now - event.duration
        if snap.print_time is not None:
            return now - snap.print_time
        return now

    def _save(self, last: LastPrint) -> None:
        self.last = last
        self.store.save(last)


def _display_name(snap: Snapshot, event: PrintEvent) -> str:
    if snap.file_display:
        return snap.file_display
    if event.path:
        return PurePosixPath(event.path).name
    return ""


def reconcile(stored: LastPrint | None, metadata: dict | None) -> LastPrint | None:
    """Prefer OctoPrint's file metadata when it records a print that ended
    after the stored one, e.g. one that finished while the dashboard was down.

    ``metadata`` is the response of ``GET /api/files/local/<path>``. It cannot
    tell a cancelled print from a failed one, so both become ``failed``."""
    last = ((metadata or {}).get("prints") or {}).get("last")
    if not last or last.get("date") is None:
        return stored
    ended_at = float(last["date"])
    if stored and stored.ended_at is not None and ended_at <= stored.ended_at + _RECONCILE_SLACK:
        return stored
    # A print that is still running beats any print that ended before it began.
    if stored and stored.ended_at is None and ended_at <= stored.started_at:
        return stored

    print_time = float(last.get("printTime") or 0.0)
    success = bool(last.get("success"))
    reconciled = LastPrint(
        file_display=metadata.get("display") or PurePosixPath(metadata.get("path") or "").name,
        file_path=metadata.get("path"),
        started_at=ended_at - print_time, ended_at=ended_at,
        outcome="done" if success else "failed",
        completion=100.0 if success else None,
        layer=None, layer_total=None,
        remaining=0 if success else None,
        duration=int(print_time),
    )
    return reconciled
