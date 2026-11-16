"""Faye/Bayeux client matching the NCAA box-score page behavior.

The browser Faye client:
  new Faye.Client('https://livestream.ncaa.org/stream', {retry: 5, timeout:120})
negotiates transports and settles on WebSocket when available.

All traffic reuses the shared Chrome-impersonated BrowserSession (same cookies,
TLS fingerprint, Origin/Referer as a tab on stats.ncaa.org).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from college_sports_scraper.ncaa.browser_session import (
    FAYE_HTTP_URL,
    FAYE_SUPPORTED_CONNECTION_TYPES,
    FAYE_WS_URL,
    BrowserSession,
)

logger = logging.getLogger(__name__)

MessageHandler = Callable[[str, dict[str, Any]], Awaitable[None] | None]
Transport = Literal["websocket", "long-polling", "auto"]


def _is_faye_access_denied(exc: BaseException) -> bool:
    """True when Akamai/WAF rejected livestream.ncaa.org (cookies need Chrome)."""
    text = str(exc).lower()
    if "access denied" in text:
        return True
    if "403" in text and (
        "http error" in text
        or "refused websocket" in text
        or "websocket upgrade" in text
        or "status" in text
    ):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    try:
        return int(status) == 403
    except (TypeError, ValueError):
        return False


@dataclass
class FayeAuth:
    """Per-page credentials embedded in the NCAA box-score HTML."""

    user_id: str
    tokens_by_channel: dict[str, str] = field(default_factory=dict)

    def token_for(self, channel: str) -> str | None:
        return self.tokens_by_channel.get(channel)


class FayeClient:
    """Subscribe to NCAA Faye channels the way the live box-score page does."""

    def __init__(
        self,
        auth: FayeAuth,
        browser: BrowserSession | None = None,
        transport: Transport = "auto",
        reconnect: bool = True,
    ) -> None:
        self.auth = auth
        self.browser = browser or BrowserSession()
        self.transport = transport
        self.reconnect = reconnect
        self._handlers: dict[str, list[MessageHandler]] = {}
        self._client_id: str | None = None
        self._msg_id = 0
        self._stop = asyncio.Event()
        self._connected = asyncio.Event()
        self._subscribed = asyncio.Event()
        self._failed = asyncio.Event()
        self._run_error: Exception | None = None
        self._pending_channels: set[str] = set()
        self._subscribed_channels: set[str] = set()
        self.promote_after_subscribe = False
        self._ws: Any | None = None

    def on(self, channel: str, handler: MessageHandler) -> None:
        self._handlers.setdefault(channel, []).append(handler)

    def _next_id(self) -> str:
        self._msg_id += 1
        return str(self._msg_id)

    async def _dispatch(self, channel: str, data: dict[str, Any]) -> None:
        for handler in self._handlers.get(channel, []):
            result = handler(channel, data)
            if asyncio.iscoroutine(result):
                await result
        for handler in self._handlers.get("*", []):
            result = handler(channel, data)
            if asyncio.iscoroutine(result):
                await result

    async def _handle_messages(self, messages: list[dict[str, Any]]) -> None:
        for msg in messages:
            channel = msg.get("channel") or ""
            if channel.startswith("/meta/"):
                if channel == "/meta/subscribe":
                    logger.info(
                        "Faye subscribe %s successful=%s error=%s",
                        msg.get("subscription"),
                        msg.get("successful"),
                        msg.get("error"),
                    )
                    if not msg.get("successful"):
                        raise RuntimeError(f"Subscribe failed: {msg}")
                    self._subscribed_channels.add(str(msg.get("subscription") or ""))
                    if self._pending_channels and self._pending_channels <= self._subscribed_channels:
                        self._mark_subscribed()
                continue
            data = msg.get("data")
            if isinstance(data, dict):
                await self._dispatch(channel, data)

    def _mark_subscribed(self) -> None:
        """Subscribe replies arrived. A one-shot probe can keep reconnecting after this."""
        if self.promote_after_subscribe:
            self.reconnect = True
        self._subscribed.set()

    def _http_post(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        response = self.browser.session.post(
            FAYE_HTTP_URL,
            data=json.dumps(messages),
            headers=self.browser.faye_headers(for_websocket=False),
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else [payload]

    def _subscribe_message(self, client_id: str, channel: str) -> dict[str, Any]:
        token = self.auth.token_for(channel)
        if not token:
            raise ValueError(f"No Faye token for {channel}")
        return {
            "channel": "/meta/subscribe",
            "clientId": client_id,
            "subscription": channel,
            "id": self._next_id(),
            "ext": {"userId": self.auth.user_id, "token": token},
        }

    # --- WebSocket (browser's preferred settled transport) -------------------

    async def _run_websocket(self, channels: list[str]) -> None:
        session = self.browser.session

        def connect_ws():
            return session.ws_connect(
                FAYE_WS_URL,
                headers=self.browser.faye_headers(for_websocket=True),
                timeout=30,
            )

        ws = await asyncio.to_thread(connect_ws)
        self._ws = ws
        try:
            def send(msg: dict[str, Any]) -> None:
                ws.send_str(json.dumps([msg]))

            def recv_json() -> list[dict[str, Any]]:
                raw = ws.recv()
                # curl_cffi may return str or bytes-like frame
                if isinstance(raw, tuple):
                    raw = raw[0]
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                data = json.loads(raw)
                return data if isinstance(data, list) else [data]

            await asyncio.to_thread(
                send,
                {
                    "channel": "/meta/handshake",
                    "version": "1.0",
                    "supportedConnectionTypes": list(FAYE_SUPPORTED_CONNECTION_TYPES),
                    "id": self._next_id(),
                },
            )
            handshake = await asyncio.to_thread(recv_json)
            client_id = None
            for msg in handshake:
                if msg.get("channel") == "/meta/handshake" and msg.get("successful"):
                    client_id = msg["clientId"]
            if not client_id:
                raise RuntimeError(f"Faye WS handshake failed: {handshake}")
            self._client_id = client_id
            logger.info("Faye WebSocket handshake ok clientId=%s", client_id)

            await asyncio.to_thread(
                send,
                {
                    "channel": "/meta/connect",
                    "clientId": client_id,
                    "connectionType": "websocket",
                    "id": self._next_id(),
                },
            )
            for channel in channels:
                await asyncio.to_thread(send, self._subscribe_message(client_id, channel))

            self._connected.set()
            while not self._stop.is_set():
                messages = await asyncio.to_thread(recv_json)
                await self._handle_messages(messages)
                # Renew /meta/connect periodically the way Faye clients do
                advice_connect = any(
                    m.get("channel") == "/meta/connect" and m.get("successful") for m in messages
                )
                if advice_connect:
                    await asyncio.to_thread(
                        send,
                        {
                            "channel": "/meta/connect",
                            "clientId": client_id,
                            "connectionType": "websocket",
                            "id": self._next_id(),
                        },
                    )
        finally:
            self._ws = None
            try:
                await asyncio.to_thread(ws.close)
            except Exception:
                pass

    # --- HTTP long-polling fallback ------------------------------------------

    async def _run_long_polling(self, channels: list[str]) -> None:
        handshake = await asyncio.to_thread(
            self._http_post,
            [
                {
                    "channel": "/meta/handshake",
                    "version": "1.0",
                    "supportedConnectionTypes": list(FAYE_SUPPORTED_CONNECTION_TYPES),
                    "id": self._next_id(),
                }
            ],
        )
        client_id = None
        for msg in handshake:
            if msg.get("channel") == "/meta/handshake" and msg.get("successful"):
                client_id = msg["clientId"]
        if not client_id:
            raise RuntimeError(f"Faye HTTP handshake failed: {handshake}")
        self._client_id = client_id
        logger.info("Faye long-polling handshake ok clientId=%s", client_id)

        for channel in channels:
            sub = await asyncio.to_thread(
                self._http_post,
                [self._subscribe_message(client_id, channel)],
            )
            await self._handle_messages(sub)

        self._connected.set()
        while not self._stop.is_set():
            messages = await asyncio.to_thread(
                self._http_post,
                [
                    {
                        "channel": "/meta/connect",
                        "clientId": client_id,
                        "connectionType": "long-polling",
                        "id": self._next_id(),
                    }
                ],
            )
            await self._handle_messages(messages)

    async def run(self, channels: list[str]) -> None:
        self._pending_channels = set(channels)
        try:
            await self._run_loop(channels)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._run_error = exc
            self._failed.set()
            raise

    async def _run_loop(self, channels: list[str]) -> None:
        self._stop.clear()
        if self.transport == "auto":
            order: list[Transport] = ["websocket", "long-polling"]
        else:
            order = [self.transport]

        while not self._stop.is_set():
            last_error: Exception | None = None
            for mode in order:
                if self._stop.is_set():
                    break
                self._subscribed_channels.clear()
                try:
                    logger.info(
                        "Connecting Faye via %s (referer=%s)",
                        mode,
                        self.browser.referer,
                    )
                    if mode == "websocket":
                        await self._run_websocket(channels)
                    else:
                        await self._run_long_polling(channels)
                    last_error = None
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    last_error = exc
                    self._connected.clear()
                    logger.warning("Faye %s failed: %s", mode, exc)
                    # Akamai 403 will not clear by retrying — escalate for Chrome.
                    if _is_faye_access_denied(exc):
                        raise
            if self._stop.is_set():
                break
            if not self.reconnect:
                if last_error:
                    raise last_error
                break
            if last_error is not None and _is_faye_access_denied(last_error):
                raise last_error
            logger.info("Faye reconnecting in 2s")
            await asyncio.sleep(2)

    async def wait_connected(self, timeout: float = 30.0) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout=timeout)

    async def wait_subscribed(self, timeout: float = 30.0) -> None:
        """Return only after every channel's subscribe reply succeeded.

        The websocket marks itself connected before those replies arrive, so a
        saved-session probe has to wait here instead of on ``wait_connected``.
        """
        subscribed = asyncio.create_task(self._subscribed.wait())
        failed = asyncio.create_task(self._failed.wait())
        try:
            await asyncio.wait(
                {subscribed, failed},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (subscribed, failed):
                if not task.done():
                    task.cancel()
        if self._subscribed.is_set():
            return
        if self._run_error is not None:
            raise self._run_error
        raise TimeoutError("Faye subscribe timed out")

    def stop(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
