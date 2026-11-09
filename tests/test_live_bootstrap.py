from pathlib import Path

from college_sports_scraper.ncaa.live_state import (
    bootstrap_live_state,
    organization_id_for_team_name,
    parse_team_organization_ids,
)
from college_sports_scraper.ncaa.parse import (
    box_score_waiting_to_initialize,
    parse_linescore_matchup,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "wbb_live_box_score_6656891.html"
PREGAME = Path(__file__).resolve().parents[1] / "fixtures" / "mbb_pregame_waiting_6620219.html"


def test_bootstrap_extracts_on_court_and_faye_auth():
    html = FIXTURE.read_text()
    state = bootstrap_live_state(html, "6656891")

    assert state.sport_code == "WBB"
    assert state.auth is not None
    assert state.box_score_channel in state.auth.tokens_by_channel
    assert state.away and state.home
    assert state.away.name and "Western" in state.away.name
    assert state.home.name and "Chaminade" in state.home.name
    assert state.awaiting_tipoff is False

    away_on = state.away.on_court()
    home_on = state.home.on_court()
    assert len(away_on) == 5
    assert len(home_on) == 5
    assert all(p.on_court for p in away_on)
    assert all(p.on_court for p in home_on)


def test_bootstrap_pregame_waiting_uses_linescore_matchup():
    html = PREGAME.read_text()
    assert box_score_waiting_to_initialize(html) is True

    away, home, competitor_ids = parse_linescore_matchup(html)
    assert "Black Hills" in (away.team_name or "")
    assert "Fla. Southern" in (home.team_name or "") or "Southern" in (home.team_name or "")
    assert competitor_ids == ["8624845", "8624844"]

    state = bootstrap_live_state(html, "6620219")
    assert state.awaiting_tipoff is True
    assert state.sport_code == "MBB"
    assert state.auth is not None
    assert state.box_score_channel in state.auth.tokens_by_channel
    assert state.away is not None and state.home is not None
    assert state.away.competitor_id == "8624845"
    assert state.home.competitor_id == "8624844"
    assert state.away.players == {}
    assert state.home.players == {}

    state.apply_message(state.box_score_channel, {"game_has_initialized": 1})
    assert state.awaiting_tipoff is False


def test_parse_team_organization_ids_from_shot_filter_options():
    html = """
    <select>
      <option value="">Team</option>
      <option value="500075">Albany Pharmacy</option>
      <option value="962">Bard</option>
    </select>
    """
    mapping = parse_team_organization_ids(html)
    assert mapping["albany pharmacy"] == "500075"
    assert mapping["bard"] == "962"
    assert organization_id_for_team_name("Albany Pharmacy", mapping) == "500075"
    assert organization_id_for_team_name("  Bard  ", mapping) == "962"


def test_organization_id_falls_back_to_shot_filter_when_logo_missing():
    from college_sports_scraper.ncaa.live_state import LiveTeam

    team = LiveTeam(
        competitor_id="8667912",
        name="Albany Pharmacy",
        logo_url=None,
        org_id="500075",
        venue="away",
    )
    assert team.organization_id == "500075"
    assert team.organization_id != team.competitor_id


def test_apply_box_score_updates_points_and_active():
    html = FIXTURE.read_text()
    state = bootstrap_live_state(html, "6656891")
    assert state.home is not None
    # Prefer a numeric livestream player id (not the offline name: fallback).
    player_id = next(pid for pid in state.home.players if pid.isdigit())
    competitor = state.home.competitor_id
    assert all(pid.isdigit() for pid in state.away.players)
    assert all(pid.isdigit() for pid in state.home.players)

    state.apply_message(
        state.box_score_channel,
        {
            "box_score": {
                f"{competitor}_{player_id}_sPoints": "99",
                f"{competitor}_{player_id}_active": "*",
            },
            "contest_score": {f"clock_{state.game_id}": "01:11"},
        },
    )
    assert state.home.players[player_id].stats["PTS"] == 99
    assert state.home.players[player_id].on_court is True
    assert state.clock == "01:11"
