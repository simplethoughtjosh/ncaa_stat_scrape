"""Team/coach pages reuse the live cookie jar; curl clears gates before Chrome."""

from college_sports_scraper.ncaa.browser_session import BASE_URL, BrowserSession, DocumentResponse

TEAM_HTML = "<html><body>" + ("team page content " * 80) + "</body></html>"
GATE_HTML = '<html><script>function triggerInterstitialChallenge(){}</script></html>'


def _response(url: str, html: str, status: int = 200):
    class Response:
        text = html
        status_code = status

        def __init__(self):
            self.url = url

    return Response()


def test_fetch_stats_html_soft_get_success_skips_curl_and_chrome(tmp_path, monkeypatch):
    browser = BrowserSession(cookie_path=tmp_path / "ncaa_session.json", timeout=5)
    browser.session.cookies.set("cf_clearance", "abc", domain="stats.ncaa.org", path="/")
    url = f"{BASE_URL}/teams/627738"
    curl_calls: list[str] = []
    chrome_calls: list[tuple[str, bool]] = []

    def soft_get(request_url, **kwargs):
        return _response(request_url, TEAM_HTML)

    monkeypatch.setattr(browser.session, "get", soft_get)
    monkeypatch.setattr(
        browser,
        "_fetch_curl",
        lambda u, params=None, headers=None: curl_calls.append(u) or _response(u, GATE_HTML),
    )
    monkeypatch.setattr(
        browser,
        "_fetch_with_chrome",
        lambda u, require_live_box=True: chrome_calls.append((u, require_live_box))
        or DocumentResponse(text=TEAM_HTML, url=u),
    )

    assert browser.fetch_stats_html("/teams/627738") == TEAM_HTML
    assert curl_calls == []
    assert chrome_calls == []
    assert browser.referer == url


def test_fetch_stats_html_curl_clears_gate_without_chrome(tmp_path, monkeypatch):
    browser = BrowserSession(cookie_path=tmp_path / "ncaa_session.json", timeout=5)
    browser.session.cookies.set("cf_clearance", "abc", domain="stats.ncaa.org", path="/")
    url = f"{BASE_URL}/teams/627738"
    curl_calls: list[str] = []
    chrome_calls: list[tuple[str, bool]] = []

    def soft_get(request_url, **kwargs):
        return _response(request_url, GATE_HTML)

    monkeypatch.setattr(browser.session, "get", soft_get)
    monkeypatch.setattr(
        browser,
        "_fetch_curl",
        lambda u, params=None, headers=None: curl_calls.append(u) or _response(u, TEAM_HTML),
    )
    monkeypatch.setattr(
        browser,
        "_fetch_with_chrome",
        lambda u, require_live_box=True: chrome_calls.append((u, require_live_box))
        or DocumentResponse(text=TEAM_HTML, url=u),
    )

    assert browser.fetch_stats_html(url) == TEAM_HTML
    assert curl_calls == [url]
    assert chrome_calls == []


def test_fetch_stats_html_chrome_last_resort_when_curl_still_gated(tmp_path, monkeypatch):
    browser = BrowserSession(cookie_path=tmp_path / "ncaa_session.json", timeout=5)
    browser.session.cookies.set("cf_clearance", "abc", domain="stats.ncaa.org", path="/")
    url = f"{BASE_URL}/people/15295"
    chrome_calls: list[tuple[str, bool]] = []

    monkeypatch.setattr(
        browser.session,
        "get",
        lambda request_url, **kwargs: _response(request_url, "Access Denied"),
    )
    monkeypatch.setattr(
        browser,
        "_fetch_curl",
        lambda u, params=None, headers=None: _response(u, GATE_HTML),
    )
    monkeypatch.setattr(
        browser,
        "_fetch_with_chrome",
        lambda u, require_live_box=True: chrome_calls.append((u, require_live_box))
        or DocumentResponse(text=TEAM_HTML, url=u),
    )

    assert browser.fetch_stats_html(url) == TEAM_HTML
    assert chrome_calls == [(url, False)]
