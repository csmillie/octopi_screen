"""OctoPrint REST client and push-socket listener. I/O only; parsing lives in
parse.py."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable
from urllib.parse import quote

import requests
import websocket

from .model import PrintEvent
from .parse import parse_event

log = logging.getLogger(__name__)


class OctoPrintClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 3) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["X-Api-Key"] = api_key

    def _get(self, path: str) -> dict | None:
        try:
            response = self.session.get(self.base_url + path, timeout=self.timeout)
        except requests.RequestException:
            return None
        if response.status_code != 200:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    def get_job(self) -> dict | None:
        return self._get("/api/job")

    def get_printer(self) -> dict | None:
        # 409 means the printer is not connected.
        return self._get("/api/printer")

    def get_file(self, path: str) -> dict | None:
        return self._get("/api/files/local/" + quote(path))

    def send_commands(self, commands: list[str]) -> bool:
        try:
            response = self.session.post(
                self.base_url + "/api/printer/command",
                json={"commands": commands}, timeout=self.timeout,
            )
        except requests.RequestException:
            return False
        return response.status_code == 204

    def passive_login(self) -> str | None:
        try:
            response = self.session.post(
                self.base_url + "/api/login", json={"passive": True}, timeout=self.timeout,
            )
            data = response.json()
        except (requests.RequestException, ValueError):
            return None
        if response.status_code != 200 or "session" not in data:
            return None
        return f"{data['name']}:{data['session']}"


class PushListener(threading.Thread):
    """Follows OctoPrint's push socket for print events and serial log lines
    (the replies to M220/M221 only arrive here)."""

    def __init__(
        self,
        ws_url: str,
        client: OctoPrintClient,
        on_event: Callable[[PrintEvent], None],
        on_log: Callable[[str], None],
    ) -> None:
        super().__init__(name="octoprint-push", daemon=True)
        self.ws_url = ws_url
        self.client = client
        self.on_event = on_event
        self.on_log = on_log

    def run(self) -> None:
        backoff = 2
        while True:
            auth = self.client.passive_login()
            if auth:
                connected_at = time.monotonic()
                app = websocket.WebSocketApp(
                    self.ws_url,
                    on_open=lambda ws, auth=auth: self._subscribe(ws, auth),
                    on_message=lambda ws, message: self._handle(message),
                )
                app.run_forever(ping_interval=30, ping_timeout=10)
                if time.monotonic() - connected_at > 60:
                    backoff = 2
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)

    @staticmethod
    def _subscribe(ws, auth: str) -> None:
        ws.send(json.dumps({"auth": auth}))
        # Status updates every 2 s instead of 0.5 s. Events are not throttled
        # and log lines are batched, not dropped.
        ws.send(json.dumps({"throttle": 4}))

    def _handle(self, message: str) -> None:
        try:
            data = json.loads(message)
        except ValueError:
            return
        if not isinstance(data, dict):
            return
        try:
            event = parse_event(data)
            if event:
                self.on_event(event)
            for key in ("current", "history"):
                for line in (data.get(key) or {}).get("logs") or []:
                    self.on_log(line)
        except Exception:
            log.exception("push message handler failed")
