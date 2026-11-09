"""HTTP client for stats.ncaa.org basketball scoreboards and live box scores."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from college_sports_scraper.ncaa.browser_session import BrowserSession, DEFAULT_IMPERSONATE
from college_sports_scraper.ncaa.models import GameSummary, TeamBox
from college_sports_scraper.ncaa.parse import (
    parse_basketball_box_score,
    parse_box_score_clock,
    parse_linescore_totals,
    parse_scoreboard,
)


class NcaaClient:
    def __init__(
        self,
        cookie_path: str | Path | None = None,
        timeout: int = 30,
        impersonate: str = DEFAULT_IMPERSONATE,
        browser: BrowserSession | None = None,
    ) -> None:
        self.browser = browser or BrowserSession(
            cookie_path=cookie_path,
            impersonate=impersonate,
            timeout=timeout,
        )

    @property
    def session(self):
        return self.browser.session

    def get(self, path_or_url: str, params: dict[str, Any] | None = None) -> Any:
        return self.browser.get_document(path_or_url, params=params)

    def list_games(
        self,
        date: str,
        sports_code: str = "WBB",
        division: int = 1,
        conference_id: int | None = None,
    ) -> list[GameSummary]:
        params: dict[str, Any] = {
            "utf8": "✓",
            "sport_code": sports_code,
            "academic_year": date.split("/")[-1],
            "division": division,
            "game_date": date,
            "commit": "Submit",
        }
        if conference_id is not None:
            params["conference_id"] = conference_id
        response = self.get("/contests/livestream_scoreboards", params=params)
        return parse_scoreboard(response.text)

    def find_games_for_team(
        self,
        team_query: str,
        date: str,
        sports_code: str = "WBB",
        division: int = 1,
    ) -> list[GameSummary]:
        games = self.list_games(date=date, sports_code=sports_code, division=division)
        return [g for g in games if g.matches_team(team_query)]

    def live_box_score_url(self, game_id: str | int) -> str:
        return self.browser.absolute(f"/contests/livestream_scoreboards/{game_id}/box_score")

    def get_live_box_score_html(self, game_id: str | int) -> str:
        return self.get(self.live_box_score_url(game_id)).text

    def get_live_box_score(
        self, game_id: str | int
    ) -> tuple[TeamBox, TeamBox, dict[str, str | None]]:
        html = self.get_live_box_score_html(game_id)
        away, home = parse_basketball_box_score(html)
        period, clock = parse_box_score_clock(html)
        away_score, home_score = parse_linescore_totals(html)
        return away, home, {
            "period": period,
            "clock": clock,
            "away_score": away_score,
            "home_score": home_score,
            "game_id": str(game_id),
        }
