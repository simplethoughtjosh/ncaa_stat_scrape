"""Tiny localhost HTTP API for the Captivate controller panel."""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlparse

from college_sports_scraper.control.runtime import LiveControlRuntime
from college_sports_scraper.ncaa.live_state import BasketballLiveState

logger = logging.getLogger(__name__)

StateProvider = Callable[[], BasketballLiveState | None]


class ControlServer:
    def __init__(
        self,
        runtime: LiveControlRuntime,
        state_provider: StateProvider,
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        self.runtime = runtime
        self.state_provider = state_provider
        self.host = host
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        runtime = self.runtime
        state_provider = self.state_provider

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                logger.debug("control http: " + fmt, *args)

            def _cors(self) -> None:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")

            def _json(self, code: int, body: dict[str, Any]) -> None:
                data = json.dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self._cors()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _read_json(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    return {}
                raw = self.rfile.read(length)
                try:
                    return json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    # Plain text body e.g. H21
                    text = raw.decode("utf-8").strip()
                    return {"code": text} if text else {}

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(204)
                self._cors()
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                path = urlparse(self.path).path
                state = state_provider()
                if path == "/status":
                    self._json(200, runtime.status(state))
                    return
                if path == "/config":
                    self._json(200, runtime.update_config({}))
                    return
                if path == "/auto":
                    self._json(200, {"actions": runtime.pop_auto_actions()})
                    return
                self._json(404, {"ok": False, "error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                path = urlparse(self.path).path
                body = self._read_json()
                if path == "/graphics":
                    self._json(200, {"ok": True, "graphics": runtime.set_graphics(body)})
                    return
                state = state_provider()
                if state is None:
                    self._json(503, {"ok": False, "error": "no live state"})
                    return
                if path == "/config":
                    self._json(200, {"ok": True, "config": runtime.update_config(body)})
                    return
                if path in {"/player", "/ft"}:
                    code = str(body.get("code") or body.get("player") or "").strip()
                    result = runtime.cue_player(state, code)
                    self._json(200 if result.get("ok") else 400, result)
                    return
                if path == "/sub/take":
                    self._json(200, runtime.push_sub_take(state))
                    return
                if path == "/timeout/take":
                    self._json(200, runtime.push_timeout_take(state))
                    return
                if path == "/foul/take":
                    self._json(200, runtime.push_foul_take(state))
                    return
                if path == "/leaders/take":
                    self._json(200, runtime.push_leaders_take(state))
                    return
                self._json(404, {"ok": False, "error": "not found"})

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        # Port 0 → OS assigns an ephemeral port (useful in tests)
        self.port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True, name="control-http")
        self._thread.start()
        logger.info("Control HTTP listening on http://%s:%s", self.host, self.port)

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        self._thread = None
