"""Shared Chrome-impersonated session so all NCAA traffic looks like a normal browser user."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from curl_cffi import requests as crequests

from college_sports_scraper.ncaa.access_gate import (
    TERMS_FORM_URL,
    TERMS_OF_SERVICE_URL,
    akamai_interstitial_payload,
    is_access_gate,
    terms_form_payload,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://stats.ncaa.org"
FAYE_HTTP_URL = "https://livestream.ncaa.org/stream"
FAYE_WS_URL = "wss://livestream.ncaa.org/stream"

# Match the browser Faye client advertisement exactly.
FAYE_SUPPORTED_CONNECTION_TYPES = [
    "websocket",
    "eventsource",
    "long-polling",
    "cross-origin-long-polling",
    "callback-polling",
]

# Prefer a current Chrome profile; curl_cffi maps these to JA3/Akamai fingerprints.
DEFAULT_IMPERSONATE = "chrome131"

# Cookies plus the last box score that actually contained live credentials.
DEFAULT_SESSION_PATH = Path.home() / ".college_sports_scraper" / "ncaa_session.json"
# Older builds stored only name → value. Loaded once when the session file is absent.
LEGACY_COOKIE_PATH = Path.home() / ".college_sports_scraper" / "ncaa_cookies.json"

_GAME_URL_RE = re.compile(r"/livestream_scoreboards/(\d+)(?:/|$)")
_GAME_HTML_RE = re.compile(r"'/(?:WBB|MBB)/box_score/(\d+)'")


@dataclass
class DocumentResponse:
    """Minimal response so a Chrome-fetched page looks like a curl response."""

    text: str
    url: str
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"NCAA document failed with HTTP {self.status_code}")


def _is_live_box_score(html: str) -> bool:
    """True when the page carries the Faye credentials a restart can reuse."""
    return (
        "message.ext.userId" in html
        and "tokensByChannel" in html
        and not is_access_gate(html)
    )


def _game_id_from_url(url: str) -> str:
    match = _GAME_URL_RE.search(url or "")
    return match.group(1) if match else ""


def _game_id_from_html(html: str) -> str:
    match = _GAME_HTML_RE.search(html or "")
    return match.group(1) if match else ""


def _export_cookies(session: crequests.Session) -> list[dict[str, str]]:
    """Cookie records with the domain and path a later request needs."""
    records: list[dict[str, str]] = []
    try:
        jar = session.cookies.jar
    except Exception:
        jar = []
    for cookie in jar:
        if not cookie.name:
            continue
        records.append(
            {
                "name": cookie.name,
                "value": cookie.value or "",
                "domain": cookie.domain or "",
                "path": cookie.path or "/",
            }
        )
    if records:
        return records
    try:
        flat = dict(session.cookies.get_dict())
    except Exception:
        flat = {}
    return [
        {"name": name, "value": value, "domain": "stats.ncaa.org", "path": "/"}
        for name, value in flat.items()
    ]


class BrowserSession:
    """One persistent Chrome session for stats.ncaa.org + livestream.ncaa.org."""

    def __init__(
        self,
        cookie_path: str | Path | None = None,
        impersonate: str = DEFAULT_IMPERSONATE,
        timeout: int = 30,
    ) -> None:
        self.timeout = timeout
        self.impersonate = impersonate
        self.session_path = Path(cookie_path) if cookie_path else DEFAULT_SESSION_PATH
        # Kept so callers that still pass cookie_path write the session file.
        self.cookie_path = self.session_path
        # default_headers=True keeps Chrome Accept / sec-ch-ua / sec-fetch-* intact
        self.session = crequests.Session(impersonate=impersonate)
        self._warmed = False
        self._last_document_url = f"{BASE_URL}/"
        self._box_score: dict[str, str] | None = None
        self._live: dict[str, Any] | None = None
        self._load_session()

    def _load_session(self) -> None:
        if self.session_path.exists():
            try:
                data = json.loads(self.session_path.read_text())
            except (OSError, json.JSONDecodeError):
                data = None
            if isinstance(data, dict) and isinstance(data.get("cookies"), list):
                for item in data["cookies"]:
                    if isinstance(item, dict) and item.get("name"):
                        self._apply_cookie(
                            str(item["name"]),
                            str(item.get("value") or ""),
                            str(item.get("domain") or ""),
                            str(item.get("path") or "/"),
                        )
                box = data.get("box_score")
                if isinstance(box, dict) and box.get("html"):
                    self._box_score = {
                        "game_id": str(box.get("game_id") or ""),
                        "url": str(box.get("url") or ""),
                        "html": str(box.get("html") or ""),
                    }
                    if self._box_score["url"]:
                        self._last_document_url = self._box_score["url"]
                        # Already have a real browsing session on disk — do not
                        # hit the homepage again (that churns Akamai cookies and
                        # rewrites Faye's Referer to https://stats.ncaa.org/).
                        self._warmed = True
                live = data.get("live")
                if isinstance(live, dict) and live.get("game_id"):
                    self._live = live
                return
            if isinstance(data, dict):
                self._load_flat_cookies(data)
                return
        legacy = LEGACY_COOKIE_PATH if self.session_path == DEFAULT_SESSION_PATH else None
        if legacy is None or not legacy.exists():
            return
        try:
            data = json.loads(legacy.read_text())
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, dict) and "cookies" not in data:
            self._load_flat_cookies(data)

    def _load_flat_cookies(self, data: dict[str, Any]) -> None:
        """Old name/value file. Pin each cookie to the NCAA hosts so it is sent."""
        for name, value in data.items():
            if not isinstance(name, str) or not isinstance(value, str):
                continue
            for domain in ("stats.ncaa.org", "livestream.ncaa.org"):
                self._apply_cookie(name, value, domain, "/")

    def _apply_cookie(self, name: str, value: str, domain: str, path: str) -> None:
        host = (domain or "").lstrip(".") or "stats.ncaa.org"
        self.session.cookies.set(name, value, domain=host, path=path or "/")

    def save_session(
        self,
        *,
        box_score: dict[str, str] | None = None,
        live: dict[str, Any] | None = None,
    ) -> None:
        """Write cookies, optional box-score HTML, and optional live resume snapshot."""
        if box_score is not None:
            self._box_score = box_score
        if live is not None:
            self._live = live
        payload = {
            "cookies": _export_cookies(self.session),
            "box_score": self._box_score,
            "live": self._live,
        }
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_path.write_text(json.dumps(payload))

    def save_cookies(self) -> None:
        self.save_session()

    def save_live_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Persist period/clock/score/shots/lineups for the next resume."""
        if not snapshot.get("game_id"):
            return
        payload = dict(snapshot)
        payload["saved_at"] = datetime.now(timezone.utc).isoformat()
        self.save_session(live=payload)

    def saved_live_snapshot(self, game_id: str | int) -> dict[str, Any] | None:
        live = self._live
        if not live or str(live.get("game_id") or "") != str(game_id):
            return None
        return live

    def saved_box_score(self, game_id: str | int) -> tuple[str, str] | None:
        """HTML and page URL saved for this game, or None when it is a different game."""
        box = self._box_score
        if not box or str(box.get("game_id") or "") != str(game_id):
            return None
        html = box.get("html") or ""
        if not _is_live_box_score(html):
            return None
        return html, box.get("url") or ""

    def fetch_document_without_chrome(self, url: str) -> DocumentResponse | None:
        """Quiet cookie GET, then curl interstitial/terms — never opens Chrome."""
        quiet = self.fetch_if_session_valid(url)
        if quiet is not None:
            return quiet
        if not self._has_cookies():
            return None
        try:
            response = self._fetch_curl(url)
        except Exception:
            # Soft probe only — callers escalate to Chrome when needed.
            logger.debug("Cookie session could not load %s without Chrome", url)
            return None
        html = getattr(response, "text", "") or ""
        if is_access_gate(html) or not _is_live_box_score(html):
            logger.debug("NCAA still requires a browser check for %s", url)
            return None
        final = str(getattr(response, "url", url) or url)
        return DocumentResponse(
            text=html,
            url=final,
            status_code=int(getattr(response, "status_code", 0) or 200),
        )

    @property
    def referer(self) -> str:
        return self._last_document_url

    def absolute(self, path_or_url: str) -> str:
        if path_or_url.startswith("http"):
            return path_or_url
        return urljoin(BASE_URL + "/", path_or_url.lstrip("/"))

    def warm_up(self) -> None:
        """Hit the homepage like a first visit so cookies/Akamai see a real browsing session."""
        if self._warmed:
            return
        # Keep Faye Origin/Referer on the box-score page across this hop.
        prior_referer = self._last_document_url
        logger.info("Warming browser session against %s", BASE_URL)
        self._warmed = True
        try:
            self._fetch_curl(BASE_URL + "/")
        except Exception:
            logger.info("Homepage warm-up did not finish the NCAA access check")
        finally:
            if _game_id_from_url(prior_referer) or "/box_score" in (prior_referer or ""):
                self._last_document_url = prior_referer

    def pin_document_referer(self, url: str) -> None:
        """Force Faye/document Referer to the live box-score page."""
        if url:
            self._last_document_url = url

    def _drop_named_cookies(self, *names: str) -> None:
        jar = self.session.cookies.jar
        for cookie in list(jar):
            if cookie.name in names:
                jar.clear(cookie.domain, cookie.path, cookie.name)

    def _remember(self, response: Any) -> Any:
        final_url = str(getattr(response, "url", "") or self._last_document_url)
        if final_url:
            self._last_document_url = final_url
        html = getattr(response, "text", "") or ""
        if is_access_gate(html):
            return response
        box = self._box_score_record(final_url, html)
        self.save_session(box_score=box)
        return response

    def _box_score_record(self, url: str, html: str) -> dict[str, str] | None:
        if not _is_live_box_score(html):
            return None
        game_id = _game_id_from_url(url) or _game_id_from_html(html)
        if not game_id:
            return None
        return {"game_id": game_id, "url": url, "html": html}

    def _has_cookies(self) -> bool:
        try:
            return any(True for _ in self.session.cookies.jar)
        except Exception:
            return False

    def _solve_interstitial(self, html: str, referer: str) -> bool:
        # A stale ak_bmsc makes the proof-of-work return "reload" and then challenge again.
        self._drop_named_cookies("ak_bmsc", "AKA_A2")
        fresh = self.session.get(
            referer,
            timeout=self.timeout,
            headers={"Referer": self._last_document_url},
            allow_redirects=True,
        )
        payload = akamai_interstitial_payload(fresh.text) or akamai_interstitial_payload(html)
        if not payload:
            return False
        self.session.post(
            f"{BASE_URL}/_sec/verify?provider=interstitial",
            data=json.dumps(payload, separators=(",", ":")),
            timeout=self.timeout,
            headers={
                "Content-Type": "application/json",
                "Origin": BASE_URL,
                "Referer": str(getattr(fresh, "url", referer) or referer),
            },
        )
        return True

    def _submit_terms(self, html: str, page_url: str) -> Any | None:
        """Open the terms of service, then submit the fresh terms form."""
        self.session.get(
            TERMS_OF_SERVICE_URL,
            timeout=self.timeout,
            headers={"Referer": page_url},
            allow_redirects=True,
        )
        fresh = self.session.get(
            page_url,
            timeout=self.timeout,
            headers={"Referer": TERMS_OF_SERVICE_URL},
            allow_redirects=True,
        )
        payload = terms_form_payload(fresh.text) or terms_form_payload(html)
        if not payload:
            return fresh if not is_access_gate(fresh.text) else None
        posted = self.session.post(
            TERMS_FORM_URL,
            data=payload,
            timeout=self.timeout,
            headers={
                "Referer": TERMS_FORM_URL,
                "Origin": BASE_URL,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            allow_redirects=True,
        )
        return posted

    def _fetch_curl(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        nav_headers = {
            "Referer": self._last_document_url,
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin" if urlparse(url).netloc == "stats.ncaa.org" else "cross-site",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        if headers:
            nav_headers.update(headers)
        response: Any = None
        submitted_terms = False
        for _attempt in range(3):
            response = self.session.get(
                url,
                params=params,
                timeout=self.timeout,
                headers=nav_headers,
                allow_redirects=True,
            )
            html = response.text or ""
            current = str(getattr(response, "url", url) or url)
            if "triggerInterstitialChallenge" in html:
                if self._solve_interstitial(html, current):
                    continue
            elif terms_form_payload(html) and not submitted_terms:
                submitted_terms = True
                posted = self._submit_terms(html, url)
                if posted is not None and not is_access_gate(posted.text or ""):
                    return self._remember(posted)
                continue
            elif terms_form_payload(html):
                break
            elif is_access_gate(html):
                break
            response.raise_for_status()
            return self._remember(response)
        assert response is not None
        return response

    def _fetch_with_chrome(
        self,
        url: str,
        *,
        require_live_box: bool = True,
    ) -> DocumentResponse:
        from college_sports_scraper.ncaa.chrome_gate import fetch_document_html

        html, cookies = fetch_document_html(
            url,
            timeout=self.timeout + 20,
            require_live_box=require_live_box,
        )
        for cookie in cookies:
            self.session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain") or "stats.ncaa.org",
                path=cookie.get("path") or "/",
            )
        self._last_document_url = url
        self.save_session(box_score=self._box_score_record(url, html))
        return DocumentResponse(text=html, url=url)

    @staticmethod
    def _usable_stats_html(html: str, *, status: int = 200) -> bool:
        """True when HTML looks like a real team/coach page (not an access gate)."""
        if status >= 400 or not html:
            return False
        if "Access Denied" in html[:500]:
            return False
        return not is_access_gate(html)

    def fetch_stats_html(self, path_or_url: str) -> str | None:
        """Load a team/coach/stats page with the live cookie jar, then curl, then Chrome.

        Unlike live box-score soft probes, this accepts pages that do not carry
        Faye credentials. Reuses the box-score session: soft GET first, then the
        same ``_fetch_curl`` interstitial/terms ladder as ``get_document`` (no
        homepage warm-up — that can churn Akamai cookies and break Faye). Chrome
        is last resort only.
        """
        url = self.absolute(path_or_url)
        if self._has_cookies():
            try:
                response = self.session.get(
                    url,
                    timeout=self.timeout,
                    headers={
                        "Referer": self._last_document_url or (BASE_URL + "/"),
                        "Sec-Fetch-Dest": "document",
                        "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-Site": "same-origin",
                        "Sec-Fetch-User": "?1",
                        "Upgrade-Insecure-Requests": "1",
                    },
                    allow_redirects=True,
                )
            except Exception:
                response = None
            if response is not None:
                status = int(getattr(response, "status_code", 0) or 0)
                html = getattr(response, "text", "") or ""
                if self._usable_stats_html(html, status=status):
                    self._last_document_url = str(getattr(response, "url", url) or url)
                    return html

        # Same gate solvers as the live box path — keep the shared jar usable.
        try:
            curled = self._fetch_curl(url)
        except Exception:
            curled = None
        if curled is not None:
            html = getattr(curled, "text", "") or ""
            status = int(getattr(curled, "status_code", 0) or 200)
            if self._usable_stats_html(html, status=status):
                final = str(getattr(curled, "url", url) or url)
                self._last_document_url = final
                return html

        try:
            return self._fetch_with_chrome(url, require_live_box=False).text
        except Exception:
            logger.exception("Could not load NCAA stats page %s", url)
            return None

    def fetch_if_session_valid(self, url: str) -> DocumentResponse | None:
        """GET one page with the saved cookies. No homepage visit and no Chrome.

        Success matches the Chrome gate: not an access check, and the HTML carries
        ``message.ext.userId`` plus ``tokensByChannel``.
        """
        if not self._has_cookies():
            return None
        try:
            response = self.session.get(
                url,
                timeout=self.timeout,
                headers={
                    "Referer": self._last_document_url,
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "same-origin",
                    "Sec-Fetch-User": "?1",
                    "Upgrade-Insecure-Requests": "1",
                },
                allow_redirects=True,
            )
        except Exception:
            logger.info("Saved NCAA session could not load %s", url)
            return None
        status = int(getattr(response, "status_code", 0) or 0)
        html = getattr(response, "text", "") or ""
        if status >= 400 or not _is_live_box_score(html):
            # Soft probe only — tip-off / resume paths open Chrome when this returns None.
            logger.debug("Saved NCAA cookies were rejected for %s", url)
            return None
        logger.info("Reused saved NCAA cookies for %s", url)
        self._remember(response)
        final = str(getattr(response, "url", url) or url)
        return DocumentResponse(text=html, url=final, status_code=status or 200)

    def get_document(self, path_or_url: str, params: dict[str, Any] | None = None) -> Any:
        """GET an HTML document, clearing the Akamai interstitial and NCAA terms check."""
        self.warm_up()
        url = self.absolute(path_or_url)
        response = self._fetch_curl(url, params=params)
        if is_access_gate(getattr(response, "text", "") or ""):
            logger.info("NCAA is still showing an access check; opening Chrome to finish it")
            return self._fetch_with_chrome(url)
        return response

    def faye_headers(self, for_websocket: bool = False) -> dict[str, str]:
        """Headers for livestream.ncaa.org calls, as issued from the box-score page."""
        headers = {
            "Origin": BASE_URL,
            "Referer": self._last_document_url,
            "Accept": "application/json, text/plain, */*" if not for_websocket else "*/*",
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Mode": "cors" if not for_websocket else "websocket",
            "Sec-Fetch-Dest": "empty",
        }
        if not for_websocket:
            headers["Content-Type"] = "application/json"
        return headers
