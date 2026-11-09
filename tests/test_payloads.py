"""Requirements tests for Captivate variable payloads (player L3, foul, leaders, sub, sport)."""

from pathlib import Path

from college_sports_scraper.basketball.events import SubBatch, parse_timeout
from college_sports_scraper.basketball.graphics import (
    FOUL_TROUBLE_THRESHOLD,
    apply_free_throw_outcome,
    foul_trouble_payload,
    game_summary_payload,
    leaders_payload,
    player_l3_payload,
    resolve_player_code,
    sport_config_payload,
    substitution_pair_payloads,
    substitution_payload,
    timeout_payload,
)
from college_sports_scraper.ncaa.live_state import bootstrap_live_state

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "wbb_live_box_score_6656891.html"


def _state():
    return bootstrap_live_state(FIXTURE.read_text(), "6656891")


def test_sport_config_wbb_from_box_score():
    state = _state()
    sport = sport_config_payload(state)
    assert sport["sport_code"] == "WBB"
    assert sport["sport_label"] == "Women's"
    assert sport["is_wbb"] == "1"
    assert sport["period_structure"] == "quarters"
    assert sport["period_unit"] == "Quarter"
    assert sport["foul_trouble_threshold"] == str(FOUL_TROUBLE_THRESHOLD["WBB"])
    assert sport["foul_out"] == "5"


def test_sport_config_mbb_thresholds_when_sport_code_forced():
    state = _state()
    state.sport_code = "MBB"
    sport = sport_config_payload(state)
    assert sport["sport_code"] == "MBB"
    assert sport["sport_label"] == "Men's"
    assert sport["is_wbb"] == "0"
    assert sport["period_structure"] == "halves"
    assert sport["foul_trouble_threshold"] == "3"


def test_optimistic_ft_overlay_bumps_ft_and_pts_for_display_only():
    state = _state()
    assert state.home
    player = next(p for p in state.home.roster() if p.name and p.number)
    player.stats = {"FTM": 0, "FTA": 1, "PTS": 4, "FGM": 2, "FGA": 5}
    prior = dict(player.stats)
    overlay = apply_free_throw_outcome(player.stats, made=True)
    payload = player_l3_payload(
        state.home,
        player,
        side="home",
        code=f"H{player.number}",
        ft_attempt="2 of 2",
        stats_override=overlay,
    )
    assert payload["player_ft"] == "1-2"
    assert payload["player_pts"] == "5"
    assert payload["ft"] == "1-2"
    assert payload["pts"] == "5"
    # Roster / live box stats must stay untouched.
    assert player.stats == prior

    miss = apply_free_throw_outcome(player.stats, made=False)
    miss_payload = player_l3_payload(
        state.home, player, stats_override=miss, ft_attempt="1 of 2"
    )
    assert miss_payload["player_ft"] == "0-2"
    assert miss_payload["player_pts"] == "4"


def test_player_l3_includes_popular_broadcast_stats():
    state = _state()
    assert state.home
    player = next(p for p in state.home.roster() if p.name and p.number)
    payload = player_l3_payload(
        state.home, player, side="home", code=f"H{player.number}", ft_attempt="1 of 2"
    )
    required = [
        "player_team",
        "player_team_side",
        "player_name",
        "player_number",
        "player_pos",
        "player_photo",
        "player_team_logo",
        "player_pts",
        "player_fg",
        "player_3pt",
        "player_ft",
        "player_reb",
        "player_ast",
        "player_stl",
        "player_blk",
        "player_to",
        "player_pf",
        "player_min",
        "player_ft_attempt",
        "player_code",
    ]
    for key in required:
        assert key in payload, f"missing {key}"
    assert payload["player_ft_attempt"] == "1 of 2"
    assert payload["player_code"].startswith("H")
    assert "-" in payload["player_fg"]  # made-att form


def test_resolve_player_code_home_and_away():
    state = _state()
    assert state.home and state.away
    home_p = next(p for p in state.home.roster() if p.number not in (None, ""))
    away_p = next(p for p in state.away.roster() if p.number not in (None, ""))

    home = resolve_player_code(state, f"H{home_p.number}")
    assert home is not None
    assert home[2] == "home"
    assert home[1].player_id == home_p.player_id

    away = resolve_player_code(state, f"A{away_p.number}")
    assert away is not None
    assert away[2] == "away"
    assert away[1].player_id == away_p.player_id

    assert resolve_player_code(state, "H99") is None


def test_foul_trouble_uses_wbb_threshold_of_three(tmp_path):
    state = _state()
    assert state.home and state.away
    for team in (state.away, state.home):
        logo_dir = tmp_path / "WBB" / (team.name or team.venue)
        logo_dir.mkdir(parents=True)
        (logo_dir / "logo.png").write_bytes(b"png")
        for p in team.roster():
            p.stats["F"] = 0
    marked = [p for p in state.home.roster() if p.name][:2]
    marked[0].stats["F"] = 2  # below WBB threshold
    marked[1].stats["F"] = 3  # at threshold
    foul = foul_trouble_payload(state, photo_root=tmp_path)
    names = [foul[f"foul_{i}_name"] for i in range(1, 6) if foul[f"foul_{i}_name"]]
    assert marked[1].name in names
    assert marked[0].name not in names
    assert foul["foul_1_name"] == marked[1].name
    assert foul["foul_1_logo"].endswith("logo.png")
    assert "foul_6_name" not in foul


def test_foul_trouble_mbb_threshold_of_three(tmp_path):
    state = _state()
    state.sport_code = "MBB"
    assert state.away
    logo_dir = tmp_path / "MBB" / (state.away.name or "Away")
    logo_dir.mkdir(parents=True)
    logo = logo_dir / "logo.png"
    logo.write_bytes(b"png")
    for p in list(state.away.roster()) + list(state.home.roster()):
        p.stats["F"] = 0
    player = next(p for p in state.away.roster() if p.name)
    player.stats["F"] = 2  # below shared threshold
    assert foul_trouble_payload(state, photo_root=tmp_path)["foul_count"] == "0"
    player.stats["F"] = 3
    foul = foul_trouble_payload(state, photo_root=tmp_path)
    assert foul["foul_count"] == "1"
    assert foul["foul_1_name"] == player.name
    assert foul["foul_1_pf"] == "3"
    assert foul["foul_1_logo"] == str(logo.resolve())


def test_foul_trouble_fouled_out_shows_out_and_sorts_first(tmp_path):
    state = _state()
    assert state.home and state.away
    for team in (state.away, state.home):
        logo_dir = tmp_path / "WBB" / (team.name or team.venue)
        logo_dir.mkdir(parents=True)
        (logo_dir / "logo.png").write_bytes(b"png")
        for p in team.roster():
            p.stats["F"] = 0
    home_named = [p for p in state.home.roster() if p.name]
    away_named = [p for p in state.away.roster() if p.name]
    assert len(home_named) >= 2 and away_named
    fouled_out = home_named[0]
    four_fouls = home_named[1]
    three_fouls = away_named[0]
    fouled_out.stats["F"] = 5
    four_fouls.stats["F"] = 4
    three_fouls.stats["F"] = 3
    foul = foul_trouble_payload(state, photo_root=tmp_path)
    assert foul["foul_1_name"] == fouled_out.name
    assert foul["foul_1_pf"] == "OUT"
    assert foul["foul_2_name"] == four_fouls.name
    assert foul["foul_2_pf"] == "4"
    assert foul["foul_3_name"] == three_fouls.name
    assert foul["foul_3_pf"] == "3"



def test_leaders_top_three_pts_reb_ast():
    state = _state()
    leaders = leaders_payload(state)
    for cat in ("pts", "reb", "ast"):
        for i in (1, 2, 3):
            assert f"lead_{cat}_{i}_name" in leaders
            assert f"lead_{cat}_{i}_value" in leaders
            assert f"lead_{cat}_{i}_team" in leaders
            assert f"lead_{cat}_{i}_number" in leaders


def test_substitution_payload_is_one_out_and_one_in_name():
    state = _state()
    batch = SubBatch(
        team_abbr="CUH",
        outs=["Maddy Weaver", "Holly Dolny"],
        ins=["Alyssa Schuetze"],
        period="1",
        clock="07:41",
    )
    payload = substitution_payload(state, batch)
    assert payload["sub_out"] == "Maddy Weaver"
    assert payload["sub_in"] == "Alyssa Schuetze"
    assert "sub_out_1_name" not in payload
    assert "sub_out_count" not in payload


def test_substitution_pairs_use_sub_out_and_sub_in():
    state = _state()
    batch = SubBatch(
        team_abbr="CUH",
        outs=["Maddy Weaver", "Holly Dolny"],
        ins=["Alyssa Schuetze", "Ada In"],
        period="2",
        clock="07:41",
    )
    pairs = substitution_pair_payloads(state, batch)
    assert len(pairs) == 2
    assert pairs[0]["sub_out"] == "Maddy Weaver"
    assert pairs[0]["sub_in"] == "Alyssa Schuetze"
    assert pairs[1]["sub_out"] == "Holly Dolny"
    assert pairs[1]["sub_in"] == "Ada In"

    extra = SubBatch(
        team_abbr="CUH",
        outs=["Maddy Weaver", "Holly Dolny"],
        ins=["Alyssa Schuetze"],
        period="2",
        clock="07:41",
    )
    leftover = substitution_pair_payloads(state, extra)
    assert leftover[1]["sub_out"] == "Holly Dolny"
    assert leftover[1]["sub_in"] == ""


def test_timeout_payload_includes_clock_and_label():
    state = _state()
    state.clock = "05:00"
    state.period = "2"
    event = parse_timeout("Timeout short WCU")
    payload = timeout_payload(state, event)
    assert payload["timeout_label"]
    assert payload["timeout_clock"] == "05:00"
    assert payload["timeout_team_abbr"] == "WCU"


def test_game_summary_includes_sport_fields():
    state = _state()
    summary = game_summary_payload(state)
    assert summary["sport_code"] == "WBB"
    assert summary["away_score"] is not None
    assert summary["home_fg"]
