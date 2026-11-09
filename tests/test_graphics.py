from pathlib import Path

from college_sports_scraper.basketball.events import (
    SubBatch,
    SubBatcher,
    SubLine,
    parse_free_throw,
    parse_player_code,
    parse_substitution,
    parse_timeout,
)
from college_sports_scraper.basketball.graphics import (
    foul_trouble_payload,
    free_throw_payload,
    game_summary_payload,
    leaders_payload,
    on_court_payload,
    period_label,
    split_player_name,
    player_l3_payload,
    resolve_player_code,
    sport_config_payload,
    substitution_payload,
    timeout_payload,
)
from college_sports_scraper.basketball.shot_map import ShotPoint, render_made_shot_png
from college_sports_scraper.ncaa.live_state import bootstrap_live_state
from college_sports_scraper.newblue.config import load_newblue_config, remap_variables
from college_sports_scraper.newblue.controller import BasketballGraphicsController

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "wbb_live_box_score_6656891.html"
CFG = Path(__file__).resolve().parents[1] / "config" / "newblue_titles.example.yaml"


def test_on_court_and_summary_payloads():
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    assert state.away and state.home
    away = on_court_payload(state.away)
    home = on_court_payload(state.home)
    assert away["team_name"]
    assert home["player_1_name"]
    assert period_label(state)
    summary = game_summary_payload(state)
    assert summary["away_team"] and summary["home_team"]
    assert "away_fg" in summary
    sport = sport_config_payload(state)
    assert sport["sport_code"] == "WBB"
    assert sport["foul_trouble_threshold"] == "3"


def test_split_player_name_keeps_everything_after_the_first_word():
    assert split_player_name("Alyssa Ervin") == ("Alyssa", "Ervin")
    assert split_player_name("Maris Kate Payseur") == ("Maris", "Kate Payseur")
    assert split_player_name("Ryan van Eyck") == ("Ryan", "van Eyck")
    assert split_player_name("Luke Reilly Jr") == ("Luke", "Reilly Jr")
    assert split_player_name("Morning Grace Spotted Bear") == ("Morning", "Grace Spotted Bear")
    assert split_player_name("Madonna") == ("Madonna", "")
    assert split_player_name("") == ("", "")

    xml = (
        Path(__file__).resolve().parents[1]
        / "captivate_controller"
        / "STP_NCAA_Basketball"
        / "STP_NCAA_BasketballMain.xml"
    ).read_text()
    assert '<variable name="away_p1_name_first" type="text" />' in xml
    assert '<variable name="home_p5_name_last" type="text" />' in xml


def test_player_l3_and_resolve():
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    assert state.home
    player = next(p for p in state.home.roster() if p.on_court and p.number)
    payload = player_l3_payload(state.home, player, side="home", code=f"H{player.number}")
    assert payload["player_name"] == player.name
    assert payload["player_pts"]
    assert payload["player_reb"]
    parsed = parse_player_code(f"H{player.number}")
    assert parsed is not None and parsed[0] == "home"
    resolved = resolve_player_code(state, f"H{player.number}")
    assert resolved is not None
    assert resolved[1].player_id == player.player_id


def test_event_parsers_and_sub_batch():
    ft = parse_free_throw("Free throw 1 of 2 made by Madyn Kassatly(WCU)")
    assert ft and ft.attempt == 1 and ft.of_total == 2
    assert "Madyn" in ft.player_name
    assert ft.made is True

    sub = parse_substitution("Substitution CUH: out Holly Dolny")
    assert sub and sub.direction == "out" and sub.team_abbr == "CUH"

    to = parse_timeout("Timeout short WCU")
    assert to and to.team_abbr == "WCU" and not to.is_media

    batcher = SubBatcher(quiet_seconds=0.01)
    batcher.add(SubLine("CUH", "out", "Holly Dolny", ""), period="1", clock="06:58", now=1.0)
    batcher.add(SubLine("CUH", "out", "Jordyn Luna", ""), period="1", clock="06:58", now=1.1)
    batcher.add(SubLine("CUH", "in", "Cayden Parado", ""), period="1", clock="06:58", now=1.2)
    batcher.add(SubLine("CUH", "in", "Julien Parado", ""), period="1", clock="06:58", now=1.3)
    ready = batcher.flush_ready(3.0)
    assert len(ready) == 1
    assert ready[0].outs == ["Holly Dolny", "Jordyn Luna"]
    assert ready[0].ins == ["Cayden Parado", "Julien Parado"]


def test_foul_leaders_sub_timeout_payloads():
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    assert state.away and state.home
    # Force foul trouble for test
    for p in state.home.roster():
        if p.name:
            p.stats["F"] = 4
            break
    foul = foul_trouble_payload(state)
    assert int(foul["foul_count"]) >= 1

    leaders = leaders_payload(state)
    assert "lead_pts_1_name" in leaders

    sub = substitution_payload(
        state, SubBatch(team_abbr="CUH", outs=["Maddy Weaver"], ins=["Alyssa Schuetze"])
    )
    assert sub["sub_out"] == "Maddy Weaver"
    assert sub["sub_in"] == "Alyssa Schuetze"

    timeout = timeout_payload(state, parse_timeout("Timeout short WCU"))
    assert timeout["timeout_label"]


def test_shot_map_png(tmp_path):
    shots = [
        ShotPoint(x=10.0, y=50.0, org_id="133", made=True, play_id="1"),
        ShotPoint(x=90.0, y=50.0, org_id="133", made=False, play_id="2"),
        ShotPoint(x=85.0, y=40.0, org_id="2759", made=True, play_id="3"),
    ]
    out = tmp_path / "home_shot_map.png"
    path = render_made_shot_png(shots, org_id="133", out_path=out)
    assert path.exists()
    assert path.stat().st_size > 1000


def test_dry_run_controller(capsys):
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    assert state.home
    player = next(p for p in state.home.roster() if p.on_court)
    ft = free_throw_payload(state.home, player)
    assert ft["player_name"] == player.name or ft.get("player_name")

    cfg = load_newblue_config(CFG)
    assert cfg.transport == "dry-run"
    ctrl = BasketballGraphicsController(cfg)
    ctrl.push_both_on_court(state)
    ctrl.push_player_l3(player_l3_payload(state.home, player, side="home", code="H1"))
    ctrl.push_game_summary(state)
    ctrl.push_sport_config(state)
    out = capsys.readouterr().out
    assert "[newblue dry-run]" in out
    assert "STP_NCAA_Basketball: Main" in out

    mapping = cfg.titles["player_l3"]
    remapped = remap_variables(player_l3_payload(state.home, player), mapping)
    assert "player_name" in remapped
