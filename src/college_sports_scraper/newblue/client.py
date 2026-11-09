"""Captivate Advanced API client (HTTP / TCP / dry-run).

Matches cookbook / developers.newbluefx.com shapes:

  POST /api/  {"action": "...", "input": "...", "title": "...", "variables": {...}}
  TCP 9025    same JSON + CRLF after welcome BEGIN DATA
"""

from __future__ import annotations

import json
import logging
import socket
import time
from typing import Any

import requests

from college_sports_scraper.newblue.config import NewBlueConfig

logger = logging.getLogger(__name__)


class CaptivateConnectionError(ConnectionError):
    """Captivate's API port refused the update. The live feed can keep running."""


class CaptivateClient:
    def __init__(self, config: NewBlueConfig) -> None:
        self.config = config
        self._http = requests.Session()
        self._tcp: socket.socket | None = None
        self._down_logged_at = 0.0

    def close(self) -> None:
        if self._tcp is not None:
            try:
                self._tcp.close()
            except OSError:
                pass
            self._tcp = None

    def schedule_action(
        self,
        action: str | None = None,
        *,
        input_name: str = "",
        title: str = "",
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "action": action or self.config.default_action,
            "input": input_name,
            "title": title,
            "variables": variables or {},
        }
        return self._send(payload)

    def schedule_command(
        self,
        command: str,
        parameters: dict[str, Any] | None = None,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "command": command,
            "parameters": parameters or {},
            "variables": variables or {},
        }
        return self._send(payload)

    def _send(self, payload: dict[str, Any]) -> dict[str, Any]:
        transport = (self.config.transport or "http").lower()
        if transport in {"dry-run", "dryrun", "none"}:
            logger.info("Captivate dry-run: %s", json.dumps(payload, ensure_ascii=False))
            print(f"[newblue dry-run] {json.dumps(payload, ensure_ascii=False)}")
            return {"result": {"success": True, "dry_run": True, "payload": payload}}
        if transport == "tcp":
            return self._send_tcp(payload)
        return self._send_http(payload)

    def _send_http(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._http.post(
                self.config.http_url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=(2, 10),
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            self._warn_captivate_down()
            raise CaptivateConnectionError(
                f"Captivate is not accepting updates at {self.config.http_url}"
            ) from exc
        response.raise_for_status()
        if not response.text.strip():
            return {"result": {"success": True}}
        try:
            return response.json()
        except json.JSONDecodeError:
            return {"result": {"raw": response.text}}

    def _warn_captivate_down(self) -> None:
        now = time.time()
        if now - self._down_logged_at < 30:
            return
        self._down_logged_at = now
        logger.warning(
            "Captivate is not accepting updates at %s. Live stats keep running; "
            "graphics catch up when Titler is open.",
            self.config.http_url,
        )

    def _ensure_tcp(self) -> socket.socket:
        if self._tcp is not None:
            return self._tcp
        sock = socket.create_connection((self.config.host, self.config.tcp_port), timeout=10)
        sock.settimeout(10)
        # Read welcome until BEGIN DATA
        buf = b""
        while b"BEGIN DATA" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        # Consume trailing CRLFs after BEGIN DATA if present
        self._tcp = sock
        return sock

    def _send_tcp(self, payload: dict[str, Any]) -> dict[str, Any]:
        sock = self._ensure_tcp()
        message = json.dumps(payload) + "\r\n"
        sock.sendall(message.encode("utf-8"))
        # Read one CRLF-terminated JSON reply when available
        buf = b""
        while b"\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        line = buf.split(b"\r\n", 1)[0]
        if not line:
            return {"result": {"success": True}}
        try:
            return json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            return {"result": {"raw": line.decode("utf-8", errors="replace")}}
