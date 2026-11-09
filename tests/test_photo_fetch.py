"""Requirements tests for auto roster photo search / confirm / missing detection."""

from pathlib import Path

from college_sports_scraper.basketball import photo_fetch as pf
from college_sports_scraper.ncaa.live_state import BasketballLiveState, LivePlayer, LiveTeam


def _team(name: str, player_names: list[str], *, venue: str = "home") -> LiveTeam:
    team = LiveTeam(competitor_id=venue, name=name, venue=venue)
    for i, pname in enumerate(player_names, start=1):
        team.players[str(i)] = LivePlayer(
            player_id=str(i),
            competitor_id=venue,
            name=pname,
            number=str(i),
        )
    return team


def test_search_queries_match_google_style():
    assert pf.search_queries_for_team("John Carroll", "WBB") == [
        "John Carroll womens basketball roster"
    ]
    assert pf.search_queries_for_team("Calvin", "WBB") == [
        "Calvin womens basketball roster",
        "Calvin University womens basketball roster",
    ]
    assert pf.search_queries_for_team("Rollins", "MBB")[0] == "Rollins mens basketball roster"
    assert pf.search_query_for_team("John Carroll", "WBB") == (
        "John Carroll womens basketball roster"
    )


def test_overlap_threshold_scales_and_caps_at_roster_size():
    assert pf.overlap_threshold(12) == 6
    assert pf.overlap_threshold(8) == 5
    assert pf.overlap_threshold(4) == 4
    assert pf.overlap_threshold(3) == 3


def test_roster_name_overlap_uses_name_key():
    ncaa = ["Alyssa Schuetze", "Trey O'Neil", "Madyn Kassatly"]
    sidearm = ["alyssa schuetze", "trey oneil", "Someone Else"]
    assert pf.roster_name_overlap(ncaa, sidearm) == 2
    assert not pf.overlap_meets_threshold(2, 12)
    assert pf.overlap_meets_threshold(6, 12)


def test_extract_sidearm_roster_urls_from_search_html():
    html = """
    <html><body>
      <a class="result__a" href="https://rollinssports.com/sports/womens-basketball/roster">Rollins</a>
      <a href="https://rollinssports.com/sports/womens-basketball/roster/jane-doe/42">Player</a>
      <a href="https://example.com/sports/mens-basketball/roster">Wrong gender</a>
      <a href="/l/?uddg=https%3A%2F%2Fchaminadesports.com%2Fsports%2Fwomens-basketball%2Froster">DDG</a>
      <a href="https://other.edu/news/roster-preview">Not sidearm</a>
    </body></html>
    """
    urls = pf.extract_sidearm_roster_urls(html, sport_code="WBB")
    assert urls == [
        "https://rollinssports.com/sports/womens-basketball/roster",
        "https://chaminadesports.com/sports/womens-basketball/roster",
    ]


def test_normalize_rejects_wrong_sport_gender():
    assert (
        pf.normalize_sidearm_roster_url(
            "https://school.com/sports/mens-basketball/roster", sport_code="WBB"
        )
        is None
    )
    assert (
        pf.normalize_sidearm_roster_url(
            "https://school.com/sports/womens-basketball/roster/player/1",
            sport_code="WBB",
        )
        == "https://school.com/sports/womens-basketball/roster"
    )


def test_team_needs_photos_empty_folder(tmp_path: Path):
    team = _team(
        "Rollins",
        ["Ada In", "Bea Out", "Cara Mid", "Dee Guard", "Eve Wing"],
    )
    assert pf.team_needs_photos("WBB", team, root=tmp_path) is True


def test_team_needs_photos_half_present_still_needed(tmp_path: Path):
    team = _team(
        "Rollins",
        ["Ada In", "Bea Out", "Cara Mid", "Dee Guard"],
    )
    folder = tmp_path / "WBB" / "Rollins"
    folder.mkdir(parents=True)
    (folder / "logo.png").write_bytes(b"x")
    (folder / "ada_in.jpg").write_bytes(b"x")
    (folder / "bea_out.jpg").write_bytes(b"x")
    # 2 of 4 with logo → half is not fewer than half; 2*2 == 4 → False
    assert pf.team_needs_photos("WBB", team, root=tmp_path) is False

    # Drop one photo → 1 of 4 → needs fetch
    (folder / "bea_out.jpg").unlink()
    assert pf.team_needs_photos("WBB", team, root=tmp_path) is True


def test_team_needs_photos_missing_logo_triggers(tmp_path: Path):
    team = _team("Rollins", [f"Player {i}" for i in range(10)])
    folder = tmp_path / "WBB" / "Rollins"
    folder.mkdir(parents=True)
    for i in range(10):
        (folder / f"player_{i}.jpg").write_bytes(b"x")
    assert pf.team_needs_photos("WBB", team, root=tmp_path) is True


def test_ensure_team_photos_downloads_when_confirmed(tmp_path: Path):
    names = [f"Player {i}" for i in range(10)]
    home = _team("Home U", names, venue="home")
    state = BasketballLiveState(game_id="1", sport_code="WBB", home=home)
    downloads: list[tuple[str, Path]] = []

    def search(team_name, sport_code):
        return ["https://homeu.edu/sports/womens-basketball/roster"]

    def confirm(url, ncaa_names):
        return True, 8

    def download(url, out_dir: Path):
        downloads.append((url, out_dir))
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "logo.png").write_bytes(b"logo")
        return 0

    summary = pf.ensure_team_photos(
        state,
        root=tmp_path,
        search_fn=search,
        confirm_fn=confirm,
        download_fn=download,
    )
    assert summary["ok"] is True
    assert downloads
    assert downloads[0][1] == tmp_path / "WBB" / "Home U"
    assert summary["teams"][0]["fetched"] is True


def test_ensure_team_photos_fail_soft_on_timeout(tmp_path: Path):
    home = _team("Home U", [f"Player {i}" for i in range(10)], venue="home")
    state = BasketballLiveState(game_id="1", sport_code="WBB", home=home)

    def search(team_name, sport_code):
        raise TimeoutError("budget exhausted")

    summary = pf.ensure_team_photos(
        state,
        root=tmp_path,
        per_team_timeout=1.0,
        search_fn=search,
        confirm_fn=lambda *a, **k: (True, 10),
        download_fn=lambda *a, **k: 0,
    )
    assert summary["ok"] is False
    assert summary["teams"][0]["status"] == "error"


def test_ensure_team_photos_skips_when_package_missing(monkeypatch, tmp_path: Path):
    home = _team("Home U", [f"Player {i}" for i in range(10)], venue="home")
    state = BasketballLiveState(game_id="1", sport_code="WBB", home=home)
    monkeypatch.setattr(pf, "roster_headshots_available", lambda: False)
    summary = pf.ensure_team_photos(state, root=tmp_path)
    assert summary["skipped"] is True
    assert summary["error"] == "roster_headshots_missing"


def test_ensure_team_photos_skips_when_already_present(tmp_path: Path):
    names = ["Ada In", "Bea Out", "Cara Mid", "Dee Guard"]
    home = _team("Rollins", names, venue="home")
    folder = tmp_path / "WBB" / "Rollins"
    folder.mkdir(parents=True)
    (folder / "logo.png").write_bytes(b"x")
    for name in names:
        stem = name.lower().replace(" ", "_")
        (folder / f"{stem}.jpg").write_bytes(b"x")
    state = BasketballLiveState(game_id="1", sport_code="WBB", home=home)
    called = {"n": 0}

    summary = pf.ensure_team_photos(
        state,
        root=tmp_path,
        search_fn=lambda *a, **k: called.__setitem__("n", called["n"] + 1) or [],
        confirm_fn=lambda *a, **k: (False, 0),
        download_fn=lambda *a, **k: 0,
    )
    assert called["n"] == 0
    assert summary["teams"][0]["status"] == "present"
