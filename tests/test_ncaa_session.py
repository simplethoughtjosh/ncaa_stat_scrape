"""Restart reuses a saved NCAA session before opening Chrome."""

import asyncio
import json

import pytest

from college_sports_scraper.ncaa import browser_session as browser_mod
from college_sports_scraper.ncaa.browser_session import BASE_URL, BrowserSession
from college_sports_scraper.ncaa.client import NcaaClient
from college_sports_scraper.ncaa.faye import FayeAuth, FayeClient
from college_sports_scraper.ncaa.live_state import BasketballLiveState
from college_sports_scraper.ncaa.session import BasketballLiveSession

GAME_ID = "6656891"
OTHER_GAME = "111"


def _live_html(game_id: str, marker: str = "") -> str:
    return (
        f"<html>{marker}<script>"
        f"var tokensByChannel = {{'/WBB/box_score/{game_id}': 'tok'}};"
        "message.ext.userId = 'user-1';"
        "</script></html>"
    )


def _response(url: str, html: str, status: int = 200):
    class Response:
        text = html
        status_code = status

        def __init__(self):
            self.url = url

    return Response()


class _FakeFaye:
    fail_probe = False
    created: list["_FakeFaye"] = []

    def __init__(self, auth, browser=None, transport="auto", reconnect=True):
        self.auth = auth
        self.browser = browser
        self.reconnect = reconnect
        self.promote_after_subscribe = False
        self.waited_subscribed = False
        self.waited_connected = False
        self.stopped = False
        _FakeFaye.created.append(self)

    def on(self, *args, **kwargs):
        return None

    async def run(self, channels):
        self.channels = list(channels)
        if type(self).fail_probe and not self.reconnect:
            raise RuntimeError("Subscribe failed: token")
        await asyncio.Event().wait()

    async def wait_subscribed(self, timeout=30):
        self.waited_subscribed = True
        if type(self).fail_probe:
            raise RuntimeError("Subscribe failed: token")
        if self.promote_after_subscribe:
            self.reconnect = True

    async def wait_connected(self, timeout=30):
        self.waited_connected = True

    def stop(self):
        self.stopped = True


def _install_fakes(monkeypatch):
    _FakeFaye.fail_probe = False
    _FakeFaye.created = []
    seen: list[str] = []

    def fake_bootstrap(html, game_id):
        seen.append(html)
        return BasketballLiveState(
            game_id=str(game_id),
            auth=FayeAuth("user-1", {f"/WBB/box_score/{game_id}": "tok"}),
        )

    monkeypatch.setattr(
        "college_sports_scraper.ncaa.session.bootstrap_live_state",
        fake_bootstrap,
    )
    monkeypatch.setattr("college_sports_scraper.ncaa.session.FayeClient", _FakeFaye)
    return seen


def _browser(tmp_path, *, game_id: str | None, html: str | None, cookies: bool = True):
    path = tmp_path / "ncaa_session.json"
    browser = BrowserSession(cookie_path=path, timeout=5)
    if cookies:
        browser.session.cookies.set("cf_clearance", "abc", domain="stats.ncaa.org", path="/")
    box = None
    if game_id and html:
        box = {
            "game_id": game_id,
            "url": f"{BASE_URL}/contests/livestream_scoreboards/{game_id}/box_score",
            "html": html,
        }
    browser.save_session(box_score=box)
    return BrowserSession(cookie_path=path, timeout=5)


def _session(browser: BrowserSession, game_id: str, html_by_url: dict[str, str], requested: list[str], chrome: list[str]):
    def get(url, **kwargs):
        requested.append(url)
        return _response(url, html_by_url.get(url, "<html>Access Denied</html>"))

    browser.session.get = get

    def challenge(game_id_arg):
        chrome.append(str(game_id_arg))
        return _live_html(str(game_id_arg), marker="FROM-CHROME")

    client = NcaaClient(browser=browser)
    client.get_live_box_score_html = challenge
    return BasketballLiveSession(game_id, client=client)


def test_saved_cookies_load_the_box_score_without_chrome_or_homepage(tmp_path, monkeypatch):
    seen = _install_fakes(monkeypatch)
    html = _live_html(GAME_ID, marker="FROM-GET")
    browser = _browser(tmp_path, game_id=GAME_ID, html=_live_html(GAME_ID, marker="FROM-DISK"))
    url = f"{BASE_URL}/contests/livestream_scoreboards/{GAME_ID}/box_score"
    requested: list[str] = []
    chrome: list[str] = []
    session = _session(browser, GAME_ID, {url: html}, requested, chrome)

    async def run():
        await session.start()
        await session.stop()

    asyncio.run(run())

    assert requested == [url]
    assert f"{BASE_URL}/" not in requested
    assert chrome == []
    assert browser._warmed is False
    assert seen == [html]
    assert _FakeFaye.created[0].waited_connected is True
    assert _FakeFaye.created[0].waited_subscribed is False


def test_saved_snapshot_without_live_state_refreshes_via_chrome(tmp_path, monkeypatch):
    seen = _install_fakes(monkeypatch)
    saved = _live_html(GAME_ID, marker="FROM-DISK")
    browser = _browser(tmp_path, game_id=GAME_ID, html=saved)
    url = f"{BASE_URL}/contests/livestream_scoreboards/{GAME_ID}/box_score"
    requested: list[str] = []
    chrome: list[str] = []
    session = _session(browser, GAME_ID, {url: "<html>Access Denied</html>"}, requested, chrome)

    async def run():
        await session.start()
        await session.stop()

    asyncio.run(run())

    assert url in requested
    assert chrome == [GAME_ID]
    assert seen[0] == saved
    assert "FROM-CHROME" in seen[-1]
    assert session._from_saved_snapshot is False
    assert _FakeFaye.created[0].waited_subscribed is True
    assert _FakeFaye.created[0].reconnect is True
    assert len(_FakeFaye.created) == 1


def test_chrome_refresh_failure_keeps_working_faye_session(tmp_path, monkeypatch):
    seen = _install_fakes(monkeypatch)
    saved = _live_html(GAME_ID, marker="FROM-DISK")
    browser = _browser(tmp_path, game_id=GAME_ID, html=saved)
    url = f"{BASE_URL}/contests/livestream_scoreboards/{GAME_ID}/box_score"
    requested: list[str] = []
    chrome: list[str] = []
    session = _session(browser, GAME_ID, {url: "<html>Access Denied</html>"}, requested, chrome)

    def boom(_game_id):
        chrome.append("boom")
        raise RuntimeError("Chrome reached the NCAA terms check")

    session.client.get_live_box_score_html = boom

    async def run():
        await session.start()
        assert session.state is not None
        await session.stop()

    asyncio.run(run())

    assert chrome == ["boom"]
    assert seen == [saved]
    assert _FakeFaye.created[0].waited_subscribed is True


def test_saved_live_snapshot_resumes_without_chrome(tmp_path, monkeypatch):
    """NCAA Faye has no replay sequence; quit-time live state is how we catch up."""
    seen = _install_fakes(monkeypatch)
    saved = _live_html(GAME_ID, marker="FROM-DISK")
    browser = _browser(tmp_path, game_id=GAME_ID, html=saved)
    browser.save_live_snapshot(
        {
            "game_id": GAME_ID,
            "period": "2nd P",
            "clock": "05:12",
            "away_score": "18",
            "home_score": "21",
            "shots": [
                {"x": 10.0, "y": 50.0, "org_id": "294", "made": True, "play_id": "1"}
            ],
        }
    )
    url = f"{BASE_URL}/contests/livestream_scoreboards/{GAME_ID}/box_score"
    requested: list[str] = []
    chrome: list[str] = []
    session = _session(browser, GAME_ID, {url: "<html>Access Denied</html>"}, requested, chrome)

    async def run():
        await session.start()
        assert session.state is not None
        assert session.state.period == "2nd P"
        assert session.state.clock == "05:12"
        assert session.state.away_score == "18"
        assert session.state.home_score == "21"
        assert len(session.state.shots) == 1
        await session.stop()

    asyncio.run(run())

    assert chrome == []
    assert seen == [saved]
    assert _FakeFaye.created[0].waited_subscribed is True
    stored = json.loads((tmp_path / "ncaa_session.json").read_text())
    assert stored["live"]["game_id"] == GAME_ID
    assert stored["live"]["period"] == "2nd P"


def test_saved_snapshot_for_a_different_game_is_not_used(tmp_path, monkeypatch):
    seen = _install_fakes(monkeypatch)
    browser = _browser(tmp_path, game_id=OTHER_GAME, html=_live_html(OTHER_GAME, marker="FROM-DISK"))
    url = f"{BASE_URL}/contests/livestream_scoreboards/{GAME_ID}/box_score"
    requested: list[str] = []
    chrome: list[str] = []
    session = _session(browser, GAME_ID, {url: "<html>Access Denied</html>"}, requested, chrome)

    session.bootstrap()

    assert chrome == [GAME_ID]
    assert len(seen) == 1
    assert "FROM-CHROME" in seen[0]
    assert "FROM-DISK" not in seen[0]
    assert session._from_saved_snapshot is False


def test_failed_subscribe_falls_through_to_chrome(tmp_path, monkeypatch):
    seen = _install_fakes(monkeypatch)
    _FakeFaye.fail_probe = True
    saved = _live_html(GAME_ID, marker="FROM-DISK")
    browser = _browser(tmp_path, game_id=GAME_ID, html=saved)
    url = f"{BASE_URL}/contests/livestream_scoreboards/{GAME_ID}/box_score"
    requested: list[str] = []
    chrome: list[str] = []
    session = _session(browser, GAME_ID, {url: "<html>Access Denied</html>"}, requested, chrome)

    async def run():
        await session.start()
        await session.stop()

    asyncio.run(run())

    assert chrome == [GAME_ID]
    assert seen[0] == saved
    assert "FROM-CHROME" in seen[-1]
    assert _FakeFaye.created[0].waited_subscribed is True
    assert _FakeFaye.created[1].waited_connected is True
    assert _FakeFaye.created[1].reconnect is True


def test_saved_cookies_keep_domain_and_path(tmp_path):
    path = tmp_path / "ncaa_session.json"
    browser = BrowserSession(cookie_path=path, timeout=5)
    browser.session.cookies.set("bm_sv", "abc", domain="stats.ncaa.org", path="/")
    browser.save_session()

    stored = json.loads(path.read_text())
    assert stored["cookies"] == [
        {"name": "bm_sv", "value": "abc", "domain": "stats.ncaa.org", "path": "/"}
    ]
    reloaded = BrowserSession(cookie_path=path, timeout=5)
    assert reloaded.session.cookies.get_dict("stats.ncaa.org")["bm_sv"] == "abc"
    found = [cookie for cookie in reloaded.session.cookies.jar if cookie.name == "bm_sv"]
    assert found[0].domain == "stats.ncaa.org"
    assert found[0].path == "/"


def test_legacy_cookie_file_is_sent_to_ncaa(tmp_path, monkeypatch):
    session_path = tmp_path / "ncaa_session.json"
    legacy = tmp_path / "ncaa_cookies.json"
    legacy.write_text(json.dumps({"ak_bmsc": "legacy-token"}))
    monkeypatch.setattr(browser_mod, "DEFAULT_SESSION_PATH", session_path)
    monkeypatch.setattr(browser_mod, "LEGACY_COOKIE_PATH", legacy)

    browser = BrowserSession(timeout=5)

    assert browser.session.cookies.get_dict("stats.ncaa.org")["ak_bmsc"] == "legacy-token"
    assert browser.session.cookies.get_dict("livestream.ncaa.org")["ak_bmsc"] == "legacy-token"


def test_subscribe_reply_is_required_before_the_session_counts(tmp_path):
    browser = BrowserSession(cookie_path=tmp_path / "ncaa_session.json", timeout=5)
    client = FayeClient(
        auth=FayeAuth("user-1", {"/WBB/box_score/1": "tok"}),
        browser=browser,
        reconnect=False,
    )
    client.promote_after_subscribe = True
    client._pending_channels = {"/WBB/box_score/1"}
    client._connected.set()
    assert not client._subscribed.is_set()
    assert client.reconnect is False

    async def rejected():
        with pytest.raises(RuntimeError):
            await client._handle_messages(
                [
                    {
                        "channel": "/meta/subscribe",
                        "subscription": "/WBB/box_score/1",
                        "successful": False,
                        "error": "403",
                    }
                ]
            )

    asyncio.run(rejected())
    assert not client._subscribed.is_set()
    assert client.reconnect is False

    async def accepted():
        await client._handle_messages(
            [
                {
                    "channel": "/meta/subscribe",
                    "subscription": "/WBB/box_score/1",
                    "successful": True,
                }
            ]
        )

    asyncio.run(accepted())
    assert client._subscribed.is_set()
    assert client.reconnect is True
