"""Finish the NCAA terms check in real Chrome when the HTTP client is still gated.

stats.ncaa.org accepts the terms form after the same browser has opened
https://www.ncaa.org/terms-of-service/. Chrome's own script solves the Akamai
interstitial; this module only drives that navigation and the terms button.
"""

from __future__ import annotations

import logging
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from curl_cffi import requests as crequests

from college_sports_scraper.ncaa.access_gate import TERMS_OF_SERVICE_URL

logger = logging.getLogger(__name__)

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)


class ChromeGateError(RuntimeError):
    """Chrome could not return a live box score."""


def chrome_executable() -> str | None:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    found = shutil.which("google-chrome") or shutil.which("chromium")
    return found


def fetch_document_html(
    url: str,
    timeout: float = 50,
    *,
    require_live_box: bool = True,
) -> tuple[str, list[dict[str, Any]]]:
    """Open ``url`` in Chrome, accept the terms check, and return HTML plus cookies.

    Live box scores require Faye credentials (``require_live_box=True``). Team and
    coach pages only need the terms check cleared (``require_live_box=False``).
    """
    executable = chrome_executable()
    if not executable:
        raise ChromeGateError(
            "Google Chrome is not installed, so the NCAA terms check could not be completed."
        )

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    with tempfile.TemporaryDirectory(prefix="ncaa-chrome-") as profile:
        chrome = subprocess.Popen(
            [
                executable,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-sync",
                "--window-size=900,700",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            ws_url = _debugger_websocket(port, timeout=15)
            session = crequests.Session()
            ws = session.ws_connect(ws_url)
            try:
                client = _Cdp(ws)
                client.call("Page.enable")
                logger.info("Opening Chrome to review NCAA terms and load %s", url)
                ready = (
                    _on_requested_page(url)
                    if require_live_box
                    else _on_requested_stats_page(url)
                )
                state = _open(client, url, lambda current: ready(current) or _box_or_terms(current), timeout)
                logger.info("NCAA page: %s", _public_state(state))
                if not ready(state):
                    _open(client, TERMS_OF_SERVICE_URL, _on_terms_site, timeout)
                    time.sleep(2)
                    state = _open(client, url, lambda current: ready(current) or _box_or_terms(current), timeout)
                    logger.info("NCAA page after terms review: %s", _public_state(state))
                if not ready(state) and state.get("terms"):
                    _submit_terms(client)
                    deadline = time.time() + 15
                    while time.time() < deadline:
                        time.sleep(1)
                        state = _state(client)
                        logger.info("NCAA page after continue: %s", _public_state(state))
                        if ready(state):
                            break
                        if state.get("review"):
                            break
                    if not ready(state):
                        state = _open(client, url, ready, 20)
                        logger.info("NCAA page retry: %s", _public_state(state))
                if not ready(state):
                    kind = "live box score" if require_live_box else "stats page"
                    raise ChromeGateError(
                        f"Chrome reached the NCAA terms check but did not get a {kind} "
                        f"({_public_state(state)})."
                    )
                client.call("Network.enable")
                html = client.eval("document.documentElement.outerHTML")
                cookies = client.cookies()
            finally:
                ws.close()
        finally:
            chrome.terminate()
            try:
                chrome.wait(timeout=5)
            except subprocess.TimeoutExpired:
                chrome.kill()

    if require_live_box and (
        "message.ext.userId" not in html or "tokensByChannel" not in html
    ):
        raise ChromeGateError("Chrome returned a page without live box score credentials.")
    return html, cookies


def _debugger_websocket(port: int, timeout: float) -> str:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            tabs = crequests.get(f"http://127.0.0.1:{port}/json", timeout=1).json()
        except Exception as exc:  # connection refused until Chrome binds the port
            last_error = exc
            time.sleep(0.25)
            continue
        for tab in tabs:
            if tab.get("type") == "page" and tab.get("webSocketDebuggerUrl"):
                return str(tab["webSocketDebuggerUrl"])
        time.sleep(0.25)
    raise ChromeGateError(f"Chrome debugging port did not come up ({last_error})")


def _on_requested_page(url: str):
    target = url.split("?", 1)[0].rstrip("/")

    def ready(state: dict[str, Any]) -> bool:
        href = str(state.get("href") or "").split("?", 1)[0].rstrip("/")
        return href == target and bool(state.get("user")) and not state.get("terms")

    return ready


def _on_requested_stats_page(url: str):
    """Team/coach pages have no Faye userId; wait until the target URL has real content."""
    target = url.split("?", 1)[0].rstrip("/")

    def ready(state: dict[str, Any]) -> bool:
        href = str(state.get("href") or "").split("?", 1)[0].rstrip("/")
        body_len = int(state.get("body_len") or 0)
        return (
            href == target
            and not state.get("terms")
            and not state.get("review")
            and body_len >= 2000
        )

    return ready


def _box_or_terms(state: dict[str, Any]) -> bool:
    return bool(state.get("terms"))


def _on_terms_site(state: dict[str, Any]) -> bool:
    return "ncaa.org/terms-of-service" in str(state.get("href") or "")


def _public_state(state: dict[str, Any]) -> str:
    href = str(state.get("href") or "")
    if len(href) > 120:
        href = href[:120]
    return f"href={href} user={bool(state.get('user'))} terms={bool(state.get('terms'))} review={bool(state.get('review'))}"


def _open(client: _Cdp, url: str, ready, timeout: float) -> dict[str, Any]:
    client.call("Page.navigate", {"url": url})
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        client.drain(0.3)
        last = _state(client)
        if ready(last):
            return last
    return last


def _submit_terms(client: _Cdp) -> None:
    client.eval(
        """(() => {
            const box = document.querySelector('#terms_accepted');
            const button = document.getElementById('stats-access-button');
            if (!box || !button) return 'missing';
            box.checked = true;
            box.dispatchEvent(new Event('change', {bubbles: true}));
            button.disabled = false;
            button.click();
            return 'clicked';
        })()"""
    )


def _state(client: _Cdp) -> dict[str, Any]:
    raw = client.eval(
        """JSON.stringify({
            href: location.href,
            user: document.documentElement.innerHTML.includes('message.ext.userId'),
            terms: !!document.querySelector('#terms_accepted'),
            review: !!(document.body && document.body.innerText.includes('Please review')),
            body_len: (document.body && document.body.innerHTML) ? document.body.innerHTML.length : 0
        })"""
    )
    if not isinstance(raw, str):
        return {}
    import json

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


class _Cdp:
    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self._id = 0

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 20) -> dict[str, Any]:
        self._id += 1
        mine = self._id
        self.ws.send_json({"id": mine, "method": method, "params": params or {}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = self.ws.recv_json(timeout=max(0.2, deadline - time.time()))
            if message.get("id") == mine:
                return message
        raise ChromeGateError(f"Chrome did not answer {method}")

    def drain(self, timeout: float) -> None:
        try:
            self.ws.recv_json(timeout=timeout)
        except Exception:
            return

    def eval(self, expression: str) -> Any:
        message = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
            timeout=20,
        )
        result = (message.get("result") or {}).get("result") or {}
        if "value" not in result:
            return None
        return result["value"]

    def cookies(self) -> list[dict[str, Any]]:
        message = self.call(
            "Network.getCookies",
            {"urls": ["https://stats.ncaa.org/", "https://www.ncaa.org/"]},
        )
        raw = ((message.get("result") or {}).get("cookies")) or []
        cookies: list[dict[str, Any]] = []
        for item in raw:
            name = item.get("name")
            if not name:
                continue
            cookies.append(
                {
                    "name": name,
                    "value": item.get("value") or "",
                    "domain": item.get("domain") or "stats.ncaa.org",
                    "path": item.get("path") or "/",
                }
            )
        return cookies
