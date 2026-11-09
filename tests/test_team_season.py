"""Team page + coach summary parsers and season enrich."""

from pathlib import Path
from types import SimpleNamespace

from college_sports_scraper.basketball.graphics import game_summary_payload
from college_sports_scraper.ncaa.live_state import (
    BasketballLiveState,
    CoachSeasonRecords,
    LiveTeam,
    TeamSeasonRecords,
    bootstrap_live_state,
)
from college_sports_scraper.ncaa.parse import (
    parse_linescore_season_records,
    parse_linescore_team_page_ids,
)
from college_sports_scraper.ncaa.team_season import (
    enrich_team_season_stats,
    parse_coach_summary_page,
    parse_team_season_page,
)

ROOT = Path(__file__).resolve().parents[1]
PREGAME = ROOT / "fixtures" / "mbb_pregame_waiting_6620219.html"
TEAM = ROOT / "fixtures" / "mbb_team_fla_southern.html"
COACH = ROOT / "fixtures" / "mbb_coach_fla_southern.html"
WBB = ROOT / "fixtures" / "wbb_live_box_score_6656891.html"


def test_parse_linescore_team_page_ids_and_records():
    html = PREGAME.read_text()
    assert parse_linescore_team_page_ids(html) == ["627648", "627738"]
    assert parse_linescore_season_records(html) == [("0-1", "0-0"), ("1-0", "0-0")]


def test_bootstrap_seeds_team_page_id_and_linescore_records():
    state = bootstrap_live_state(PREGAME.read_text(), "6620219")
    assert state.away is not None and state.home is not None
    assert state.away.team_page_id == "627648"
    assert state.home.team_page_id == "627738"
    assert state.away.season_records.overall == "0-1"
    assert state.away.season_records.conference == "0-0"
    assert state.home.season_records.overall == "1-0"
    assert state.home.season_records.conference == "0-0"


def test_bootstrap_live_box_also_captures_team_page_ids():
    state = bootstrap_live_state(WBB.read_text(), "6656891")
    assert state.away is not None and state.home is not None
    assert state.away.team_page_id == "629243"
    assert state.home.team_page_id == "629097"


def test_parse_team_season_page():
    parsed = parse_team_season_page(TEAM.read_text())
    assert parsed.overall == "1-0"
    assert parsed.conference == "0-0"
    assert parsed.coach_name == "Mike Donnelly"
    assert parsed.coach_people_id == "15295"


def test_parse_coach_summary_page():
    parsed = parse_coach_summary_page(
        COACH.read_text(),
        school_hint="Fla. Southern Moccasins",
    )
    assert parsed.name == "Mike Donnelly"
    assert parsed.school_total == "210-116 (.644)"
    assert parsed.total_seasons == "12"
    assert parsed.career_total == "405-268 (.602)"
    assert parsed.season_total == "1-0"


def test_game_summary_payload_includes_season_and_coach_vars():
    state = BasketballLiveState(
        game_id="1",
        sport_code="MBB",
        away=LiveTeam(
            competitor_id="1",
            name="Away U",
            season_records=TeamSeasonRecords(overall="2-1", conference="0-0"),
            coach=CoachSeasonRecords(
                name="Away Coach",
                school_total="10-5",
                career_total="100-50",
                season_total="2-1",
                total_seasons="4",
            ),
        ),
        home=LiveTeam(
            competitor_id="2",
            name="Home U",
            season_records=TeamSeasonRecords(overall="3-0", conference="1-0"),
            coach=CoachSeasonRecords(
                name="Home Coach",
                school_total="20-8",
                career_total="200-90",
                season_total="3-0",
                total_seasons="12",
            ),
        ),
    )
    payload = game_summary_payload(state)
    assert payload["away_stats_overall"] == "2-1"
    assert payload["away_stats_conference"] == "0-0"
    assert payload["home_stats_overall"] == "3-0"
    assert payload["home_stats_conference"] == "1-0"
    assert payload["away_coach_name"] == "Away Coach"
    assert payload["home_coach_name"] == "Home Coach"
    assert "away_team_logo" in payload
    assert "home_team_logo" in payload
    assert "away_coach_photo" in payload
    assert "home_coach_photo" in payload
    assert payload["away_coach_photo"]  # silhouette or file path
    assert payload["home_coach_photo"]
    assert payload["away_coach_school_total"] == "10-5"
    assert payload["home_coach_career_total"] == "200-90"
    assert payload["home_coach_season_total"] == "3-0"
    assert payload["away_coach_total_seasons"] == "4"
    assert payload["home_coach_total_seasons"] == "12"


def test_enrich_soft_fails_without_team_page_id():
    state = BasketballLiveState(
        game_id="1",
        sport_code="MBB",
        away=LiveTeam(competitor_id="1", name="Away U"),
        home=LiveTeam(competitor_id="2", name="Home U", team_page_id="627738"),
    )

    class BoomClient:
        browser = SimpleNamespace(fetch_stats_html=lambda path: None)

    summary = enrich_team_season_stats(BoomClient(), state)  # type: ignore[arg-type]
    assert summary["ok"] is False
    assert state.home.coach.name is None
    assert state.away.season_records.overall is None


def test_enrich_applies_team_and_coach_pages():
    state = BasketballLiveState(
        game_id="1",
        sport_code="MBB",
        home=LiveTeam(
            competitor_id="2",
            name="Fla. Southern Moccasins",
            team_page_id="627738",
        ),
    )
    team_html = TEAM.read_text()
    coach_html = COACH.read_text()

    class FakeClient:
        def __init__(self):
            self.browser = SimpleNamespace(fetch_stats_html=self._fetch)

        def _fetch(self, path):
            if path.startswith("/teams/"):
                return team_html
            if path.startswith("/people/"):
                return coach_html
            raise AssertionError(path)

    summary = enrich_team_season_stats(FakeClient(), state)  # type: ignore[arg-type]
    assert summary["ok"] is True
    assert state.home is not None
    assert state.home.season_records.overall == "1-0"
    assert state.home.season_records.conference == "0-0"
    assert state.home.coach.name == "Mike Donnelly"
    assert state.home.coach.people_id == "15295"
    assert state.home.coach.school_total == "210-116 (.644)"
    assert state.home.coach.total_seasons == "12"
    assert state.home.coach.career_total == "405-268 (.602)"
    assert state.home.coach.season_total == "1-0"
