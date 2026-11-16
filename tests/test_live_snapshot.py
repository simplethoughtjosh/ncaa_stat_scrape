"""Quit/resume live snapshot round-trip (NCAA Faye has no replay sequence)."""

from pathlib import Path

from college_sports_scraper.ncaa.live_state import bootstrap_live_state

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "wbb_live_box_score_6656891.html"


def test_live_snapshot_round_trip_restores_clock_score_shots_and_on_court():
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    assert state.away and state.home
    state.period = "2nd P"
    state.clock = "03:21"
    state.away_score = "22"
    state.home_score = "30"
    player = next(p for p in state.away.roster() if p.name)
    player.on_court = False
    state.shots = [
        {"x": 11.0, "y": 40.0, "org_id": state.away.organization_id, "made": True, "play_id": "abc"}
    ]

    snap = state.to_live_snapshot()
    restored = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    restored.period = "1st P"
    restored.clock = "10:00"
    restored.away_score = None
    restored.home_score = None
    restored.apply_live_snapshot(snap)

    assert restored.period == "2nd P"
    assert restored.clock == "03:21"
    assert restored.away_score == "22"
    assert restored.home_score == "30"
    assert len(restored.shots) == 1
    assert restored.shots[0]["play_id"] == "abc"
    assert restored.away is not None
    again = restored.away.players[player.player_id]
    assert again.on_court is False


def test_player_box_stats_stale_when_score_exists_but_everyone_has_zero_points():
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    assert state.away and state.home
    for player in list(state.away.roster()) + list(state.home.roster()):
        player.stats["PTS"] = 0
    state.away.totals["PTS"] = 0
    state.home.totals["PTS"] = 0
    state.away_score = "5"
    state.home_score = "21"
    assert state.player_box_stats_stale() is True

    player = next(p for p in state.home.roster() if p.name)
    player.stats["PTS"] = 4
    assert state.player_box_stats_stale() is False


def test_apply_box_score_accepts_display_group_suffix_on_stat_keys():
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    assert state.home
    player = next(p for p in state.home.roster() if p.name)
    competitor = state.home.competitor_id
    state.apply_message(
        state.box_score_channel,
        {
            "box_score": {
                f"{competitor}_{player.player_id}_sPoints_10120": "8",
                f"{competitor}_{player.player_id}_sFreeThrowsMade_10120": "2",
                f"{competitor}_{player.player_id}_sFreeThrowsAttempted_10120": "3",
            }
        },
    )
    assert player.stats.get("PTS") == 8
    assert player.stats.get("FTM") == 2
    assert player.stats.get("FTA") == 3
