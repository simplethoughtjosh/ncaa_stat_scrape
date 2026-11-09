"""Requirements tests for control HTTP API and operator config."""

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from college_sports_scraper.control.runtime import LiveControlRuntime, OperatorConfig
from college_sports_scraper.control.server import ControlServer
from college_sports_scraper.ncaa.live_state import bootstrap_live_state
from college_sports_scraper.newblue.config import load_newblue_config
from college_sports_scraper.newblue.controller import BasketballGraphicsController

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "wbb_live_box_score_6656891.html"
CFG = Path(__file__).resolve().parents[1] / "config" / "newblue_titles.example.yaml"


@pytest.fixture
def control_env(tmp_path):
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    cfg = load_newblue_config(CFG)
    graphics = BasketballGraphicsController(cfg)
    runtime = LiveControlRuntime(
        graphics,
        OperatorConfig(shot_map_dir=str(tmp_path / "shots")),
    )
    holder = {"state": state}
    server = ControlServer(runtime, lambda: holder["state"], host="127.0.0.1", port=0)
    server.start()
    base = f"http://127.0.0.1:{server.port}"
    yield {"base": base, "runtime": runtime, "state": state, "server": server}
    server.stop()


def _get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=2) as resp:
        return json.loads(resp.read().decode())


def _post(base: str, path: str, body: dict):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=2) as resp:
        return json.loads(resp.read().decode())


def test_status_reports_sport_and_config(control_env):
    st = _get(control_env["base"], "/status")
    assert st["ok"] is True
    assert st["sport"]["sport_code"] == "WBB"
    assert "automatic_subs" in st["config"]
    assert st["config"]["automatic_subs"] is True
    assert st["config"]["automatic_fouls"] is True


def test_config_post_toggles_automatic_subs(control_env):
    updated = _post(
        control_env["base"],
        "/config",
        {
            "automatic_subs": True,
            "automatic_fouls": False,
            "automatic_timeouts": True,
            "hold_seconds": 5,
            "foul_hold_seconds": 8,
        },
    )
    assert updated["ok"] is True
    assert updated["config"]["automatic_subs"] is True
    assert updated["config"]["automatic_fouls"] is False
    assert updated["config"]["automatic_timeouts"] is True
    assert updated["config"]["hold_seconds"] == 5.0
    assert updated["config"]["foul_hold_seconds"] == 8.0
    st = _get(control_env["base"], "/status")
    assert st["config"]["automatic_subs"] is True
    assert st["config"]["automatic_fouls"] is False
    assert st["config"]["foul_hold_seconds"] == 8.0


def test_player_cue_endpoint_success_and_failure(control_env):
    state = control_env["state"]
    assert state.home
    player = next(p for p in state.home.roster() if p.number)
    ok = _post(control_env["base"], "/player", {"code": f"H{player.number}"})
    assert ok["ok"] is True
    assert ok["player"]["player_name"] == player.name

    with pytest.raises(urllib.error.HTTPError) as err:
        _post(control_env["base"], "/player", {"code": "H99"})
    assert err.value.code == 400


def test_foul_and_leaders_take_endpoints(control_env):
    foul = _post(control_env["base"], "/foul/take", {})
    assert foul["ok"] is True
    assert "foul" in foul
    assert foul["hold_seconds"] == control_env["runtime"].config.foul_hold_seconds
    leaders = _post(control_env["base"], "/leaders/take", {})
    assert leaders["ok"] is True
    assert "leaders" in leaders


def test_config_clamps_foul_hold_seconds(control_env):
    """Foul hold must stay in 1–60 so a bad panel value cannot hang on air."""
    too_low = _post(control_env["base"], "/config", {"foul_hold_seconds": 0})
    assert too_low["config"]["foul_hold_seconds"] == 1.0
    too_high = _post(control_env["base"], "/config", {"foul_hold_seconds": 999})
    assert too_high["config"]["foul_hold_seconds"] == 60.0
    ok = _post(control_env["base"], "/config", {"foul_hold_seconds": 12})
    assert ok["config"]["foul_hold_seconds"] == 12.0
    foul = _post(control_env["base"], "/foul/take", {})
    assert foul["hold_seconds"] == 12.0


def test_status_exposes_foul_hold_seconds(control_env):
    st = _get(control_env["base"], "/status")
    assert "foul_hold_seconds" in st["config"]
    assert st["config"]["foul_hold_seconds"] == 4.0


def test_sub_take_returns_pairs_without_still_push(control_env, monkeypatch):
    runtime = control_env["runtime"]
    runtime.last_sub = {
        "sub_team": "Chaminade",
        "sub_out": "Maddy Weaver",
        "sub_in": "Alyssa Schuetze",
    }
    runtime.last_sub_pairs = [
        {
            "sub_team": "Chaminade",
            "sub_out": "Maddy Weaver",
            "sub_in": "Alyssa Schuetze",
            "sub_team_logo": "/tmp/logo.png",
        }
    ]
    pushed = []

    def capture(*args, **kwargs):
        pushed.append((args, kwargs))
        return {"ok": True}

    monkeypatch.setattr(runtime.graphics, "push_substitution", capture)
    taken = _post(control_env["base"], "/sub/take", {})
    assert taken["ok"] is True
    assert taken["pairs"][0]["sub_out"] == "Maddy Weaver"
    assert taken["pairs"][0]["sub_in"] == "Alyssa Schuetze"
    assert "hold_seconds" in taken
    assert pushed == []


def test_posted_graphic_name_is_used_on_the_next_still(control_env):
    posted = _post(control_env["base"], "/graphics", {"foul_trouble": "Foul Board"})
    assert posted["ok"] is True
    assert posted["graphics"]["foul_trouble"] == "Foul Board"
    sent = control_env["runtime"].graphics.push_foul_trouble({"foul_1_name": "Ada"}, action="still")
    assert sent["result"]["payload"]["title"] == "Foul Board"
    cleared = _post(control_env["base"], "/graphics", {"foul_trouble": ""})
    assert cleared["graphics"]["foul_trouble"] == ""
    sent_again = control_env["runtime"].graphics.push_foul_trouble({"foul_1_name": "Ada"}, action="still")
    assert sent_again["result"]["payload"]["title"] == ""


def test_startup_pushes_survive_a_closed_captivate():
    from college_sports_scraper.newblue.client import CaptivateConnectionError

    class Graphics:
        def push_sport_config(self, *args, **kwargs):
            raise CaptivateConnectionError("down")

        def push_game_summary(self, *args, **kwargs):
            raise CaptivateConnectionError("down")

        def push_both_on_court(self, *args, **kwargs):
            raise CaptivateConnectionError("down")

    runtime = LiveControlRuntime(Graphics())  # type: ignore[arg-type]
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    assert runtime._try_push(lambda: runtime.graphics.push_sport_config(state, action="still")) is False
    assert runtime._try_push(lambda: runtime.graphics.push_game_summary(state, action="still")) is False
    assert runtime._try_push(lambda: runtime.graphics.push_both_on_court(state, action="still")) is False
    runtime.remember_on_court_photos(state, now=0)
    assert runtime._last_photo_sig


def test_captivate_down_still_pushes_the_other_boards():
    from college_sports_scraper.ncaa.live_state import BasketballLiveState, LivePlayer, LiveTeam
    from college_sports_scraper.newblue.client import CaptivateConnectionError

    class Graphics:
        def __init__(self):
            self.leaders = 0

        def push_both_on_court(self, *args, **kwargs):
            return None

        def push_game_summary(self, *args, **kwargs):
            return None

        def push_sport_config(self, *args, **kwargs):
            return None

        def push_foul_trouble(self, *args, **kwargs):
            raise CaptivateConnectionError("down")

        def push_leaders(self, *args, **kwargs):
            self.leaders += 1

    graphics = Graphics()
    runtime = LiveControlRuntime(graphics)  # type: ignore[arg-type]
    away = LiveTeam(competitor_id="a", name="Away", venue="away")
    away.players["1"] = LivePlayer(
        player_id="1", competitor_id="a", name="Ada", number="1", on_court=True, stats={"F": 4}
    )
    home = LiveTeam(competitor_id="h", name="Home", venue="home")
    state = BasketballLiveState(game_id="1", away=away, home=home, away_score="1", home_score="2")
    runtime.on_live_update(state, {})
    assert graphics.leaders == 1
    assert runtime._last_foul_sig == ""


def _court_state():
    from college_sports_scraper.ncaa.live_state import BasketballLiveState, LivePlayer, LiveTeam

    away = LiveTeam(competitor_id="a", name="Away", venue="away")
    away.players["1"] = LivePlayer(
        player_id="1", competitor_id="a", name="Ada", number="1", on_court=True
    )
    home = LiveTeam(competitor_id="h", name="Home", venue="home")
    home.players["2"] = LivePlayer(
        player_id="2", competitor_id="h", name="Bea", number="2", on_court=True
    )
    return BasketballLiveState(game_id="1", sport_code="WBB", away=away, home=home)


def _photo_graphics():
    class Graphics:
        def __init__(self):
            self.pushes = 0

        def push_both_on_court(self, *args, **kwargs):
            self.pushes += 1

        def push_game_summary(self, *args, **kwargs):
            return None

        def push_sport_config(self, *args, **kwargs):
            return None

        def push_foul_trouble(self, *args, **kwargs):
            return None

        def push_leaders(self, *args, **kwargs):
            return None

    return Graphics()


def _patch_photos(monkeypatch, paths: dict[str, str], disk: dict[str, int]):
    from college_sports_scraper.control import runtime as runtime_mod

    def payload(team, **kwargs):
        disk["n"] += 1
        out = {f"player_{i}_photo": "" for i in range(1, 6)}
        out["player_1_photo"] = paths["photo"]
        out["team_name"] = team.name or ""
        return out

    monkeypatch.setattr(runtime_mod, "on_court_payload", payload)


def test_new_photo_replaces_the_silhouette(monkeypatch):
    paths = {"photo": "/sil.png"}
    disk = {"n": 0}
    _patch_photos(monkeypatch, paths, disk)
    graphics = _photo_graphics()
    runtime = LiveControlRuntime(graphics)  # type: ignore[arg-type]
    state = _court_state()
    runtime.remember_on_court_photos(state, now=0)
    assert graphics.pushes == 0

    paths["photo"] = "/ada.jpg"
    assert runtime.refresh_missing_photos(state, now=5) is True
    assert graphics.pushes == 1
    assert runtime.refresh_missing_photos(state, now=10) is False
    assert graphics.pushes == 1


def test_unchanged_silhouette_is_not_pushed(monkeypatch):
    paths = {"photo": "/sil.png"}
    disk = {"n": 0}
    _patch_photos(monkeypatch, paths, disk)
    graphics = _photo_graphics()
    runtime = LiveControlRuntime(graphics)  # type: ignore[arg-type]
    state = _court_state()
    runtime.remember_on_court_photos(state, now=0)
    assert runtime.refresh_missing_photos(state, now=5) is False
    assert graphics.pushes == 0


def test_photo_check_skips_the_disk_inside_the_window(monkeypatch):
    paths = {"photo": "/sil.png"}
    disk = {"n": 0}
    _patch_photos(monkeypatch, paths, disk)
    graphics = _photo_graphics()
    runtime = LiveControlRuntime(graphics)  # type: ignore[arg-type]
    state = _court_state()
    runtime.refresh_missing_photos(state, now=10)
    seen = disk["n"]
    assert seen > 0
    runtime.refresh_missing_photos(state, now=12)
    assert disk["n"] == seen


def test_auto_endpoint_drains_queue(control_env):
    runtime: LiveControlRuntime = control_env["runtime"]
    runtime._queue_auto("sub", {"sub_team": "Test"})
    payload = _get(control_env["base"], "/auto")
    assert len(payload["actions"]) == 1
    assert payload["actions"][0]["kind"] == "sub"
    assert _get(control_env["base"], "/auto")["actions"] == []
