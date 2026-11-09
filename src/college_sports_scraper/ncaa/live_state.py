"""In-memory basketball game state bootstrapped from HTML, updated via Faye."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup

from college_sports_scraper.ncaa.faye import FayeAuth
from college_sports_scraper.ncaa.models import PlayerRow, TeamBox
from college_sports_scraper.ncaa.parse import (
    box_score_waiting_to_initialize,
    parse_basketball_box_score,
    parse_box_score_clock,
    parse_linescore_matchup,
    parse_linescore_season_records,
    parse_linescore_team_page_ids,
    parse_linescore_totals,
)

logger = logging.getLogger(__name__)

STAT_ALIASES = {
    "sMinutes": "MP",
    "sFieldGoalsMade": "FGM",
    "sFieldGoalsAttempted": "FGA",
    "sThreePointersMade": "3FGM",
    "sThreePointersAttempted": "3FGA",
    "sFreeThrowsMade": "FTM",
    "sFreeThrowsAttempted": "FTA",
    "sPoints": "PTS",
    "sReboundsOffensive": "ORebs",
    "sReboundsDefensive": "DRebs",
    "sReboundsTotal": "Rebs",
    "sAssists": "A",
    "sTurnovers": "TO",
    "sSteals": "S",
    "sBlocks": "B",
    "sFoulsPersonal": "F",
    "sFouledOut": "DQ",
    "sFoulsTechnical": "TFoul",
    "active": "On Court",
    "starter": "Starter",
}


@dataclass
class LivePlayer:
    player_id: str
    competitor_id: str
    number: str | None = None
    name: str | None = None
    position: str | None = None
    starter: bool = False
    on_court: bool = False
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        num = f"#{self.number} " if self.number not in (None, "") else ""
        return f"{num}{self.name or 'Unknown'}".strip()

    def to_row(self, team_name: str | None = None, team_logo_url: str | None = None) -> PlayerRow:
        return PlayerRow(
            number=self.number,
            name=self.name,
            position=self.position,
            starter=self.starter,
            on_court=self.on_court,
            stats=dict(self.stats),
            team_name=team_name,
            team_logo_url=team_logo_url,
            is_totals=False,
        )


def organization_id_from_logo(url: str | None) -> str | None:
    """NCAA shot_chart ``color`` is the school org id from the logo path, not competitor id."""
    if not url:
        return None
    match = re.search(r"/(\d+)\.(?:gif|png|jpe?g|svg)(?:\?|$)", url, re.I)
    return match.group(1) if match else None


def parse_team_organization_ids(html: str) -> dict[str, str]:
    """Map team name → school org id from the shot-chart team filter options.

    Some schools ship no logo on the box score; shot_chart.color still uses this
    org id (e.g. Albany Pharmacy → 500075), not the competitor_* table id.
    """
    mapping: dict[str, str] = {}
    for org_id, name in re.findall(
        r'<option\s+value="(\d+)">([^<]+)</option>',
        html or "",
        re.I,
    ):
        key = " ".join(name.split()).casefold()
        if key:
            mapping[key] = org_id
    return mapping


def organization_id_for_team_name(
    name: str | None, by_name: dict[str, str]
) -> str | None:
    if not name or not by_name:
        return None
    key = " ".join(name.split()).casefold()
    return by_name.get(key)


# Page reload seeds the court with one addShot(...) call per attempt (same payload as Faye).
COURT_SVG_W = 940.0
COURT_SVG_H = 500.0
_ADD_SHOT_RE = re.compile(
    r"addShot\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*(\d+)\s*,\s*(true|false)\s*,\s*(\d+)\s*,\s*"
    r"(?:'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")\s*,\s*"
    r"(?:'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")\s*,\s*(true|false)\s*\)",
    re.I,
)


def parse_embedded_shots(html: str) -> list[dict[str, Any]]:
    """Historical shot chart from the box-score HTML (addShot calls, else SVG circles)."""
    shots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _ADD_SHOT_RE.finditer(html or ""):
        play_id = match.group(5)
        if play_id in seen:
            continue
        seen.add(play_id)
        shots.append(
            {
                "x": float(match.group(1)),
                "y": float(match.group(2)),
                "org_id": match.group(3),
                "made": match.group(4).lower() == "true",
                "play_id": play_id,
                "description": "",
            }
        )
    if shots:
        return shots
    return _shots_from_court_svg(html or "")


def _shots_from_court_svg(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    court = soup.find(id="court")
    if court is None:
        return []
    shots: list[dict[str, Any]] = []
    for circle in court.find_all("circle"):
        circle_id = circle.get("id") or ""
        if not circle_id.startswith("play_"):
            continue
        play_id = circle_id[len("play_") :]
        classes = circle.get("class") or []
        if isinstance(classes, str):
            classes = classes.split()
        org_id = ""
        for cls in classes:
            if cls.startswith("team_"):
                org_id = cls[len("team_") :]
                break
        try:
            cx = float(circle.get("cx") or 0)
            cy = float(circle.get("cy") or 0)
        except ValueError:
            continue
        shots.append(
            {
                "x": (cx / COURT_SVG_W) * 100.0,
                "y": (cy / COURT_SVG_H) * 100.0,
                "org_id": org_id,
                "made": "made" in classes and "missed" not in classes,
                "play_id": play_id,
                "description": "",
            }
        )
    return shots


@dataclass
class TeamSeasonRecords:
    """Season-to-date W–L from the NCAA team page."""

    overall: str | None = None
    conference: str | None = None


@dataclass
class CoachSeasonRecords:
    """Head-coach summary records from the NCAA people page."""

    name: str | None = None
    people_id: str | None = None
    school_total: str | None = None
    career_total: str | None = None
    season_total: str | None = None
    # Seasons coached at the current school (Coaches Summary "Seasons" column).
    total_seasons: str | None = None


@dataclass
class LiveTeam:
    competitor_id: str
    name: str | None = None
    logo_url: str | None = None
    # School org id used by shot_chart.color; set when logo is missing.
    org_id: str | None = None
    # NCAA /teams/{id} page id from the linescore header.
    team_page_id: str | None = None
    venue: str = "away"  # away | home
    players: dict[str, LivePlayer] = field(default_factory=dict)
    totals: dict[str, Any] = field(default_factory=dict)
    season_records: TeamSeasonRecords = field(default_factory=TeamSeasonRecords)
    coach: CoachSeasonRecords = field(default_factory=CoachSeasonRecords)

    @property
    def organization_id(self) -> str | None:
        return self.org_id or organization_id_from_logo(self.logo_url)

    def roster(self) -> list[LivePlayer]:
        return list(self.players.values())

    def on_court(self) -> list[LivePlayer]:
        marked = [p for p in self.players.values() if p.on_court]
        if marked:
            return marked[:5]
        starters = [p for p in self.players.values() if p.starter]
        return starters[:5]

    def to_box(self) -> TeamBox:
        rows = [p.to_row(self.name, self.logo_url) for p in self.players.values()]
        if self.totals:
            rows.append(
                PlayerRow(
                    name=self.name,
                    stats=dict(self.totals),
                    team_name=self.name,
                    team_logo_url=self.logo_url,
                    is_totals=True,
                )
            )
        return TeamBox(
            team_name=self.name,
            team_logo_url=self.logo_url,
            players=rows,
            venue=self.venue,
        )


@dataclass
class PlayLine:
    action_number: str
    period: str | None = None
    clock: str | None = None
    text: str | None = None
    score: str | None = None


@dataclass
class BasketballLiveState:
    game_id: str
    sport_code: str = "WBB"
    period: str | None = None
    clock: str | None = None
    away_score: str | None = None
    home_score: str | None = None
    away: LiveTeam | None = None
    home: LiveTeam | None = None
    auth: FayeAuth | None = None
    raw_last_message: dict[str, Any] = field(default_factory=dict)
    plays: dict[str, PlayLine] = field(default_factory=dict)
    shots: list[dict[str, Any]] = field(default_factory=list)
    new_play_ids: list[str] = field(default_factory=list)
    new_shots: list[dict[str, Any]] = field(default_factory=list)
    # True until NCAA publishes game_has_initialized / roster tables appear.
    awaiting_tipoff: bool = False

    @property
    def box_score_channel(self) -> str:
        return f"/{self.sport_code}/box_score/{self.game_id}"

    @property
    def scoreboard_channel(self) -> str:
        return f"/scoreboard/{self.sport_code}"

    def team_for_competitor(self, competitor_id: str) -> LiveTeam | None:
        for team in (self.away, self.home):
            if team and team.competitor_id == competitor_id:
                return team
        return None

    def to_live_snapshot(self) -> dict[str, Any]:
        """Compact resume state. NCAA Faye has no replay cursor; this is our catch-up."""
        def team_blob(team: LiveTeam | None) -> dict[str, Any] | None:
            if team is None:
                return None
            return {
                "competitor_id": team.competitor_id,
                "name": team.name,
                "logo_url": team.logo_url,
                "venue": team.venue,
                "totals": dict(team.totals),
                "players": {
                    pid: {
                        "number": p.number,
                        "name": p.name,
                        "position": p.position,
                        "starter": p.starter,
                        "on_court": p.on_court,
                        "stats": dict(p.stats),
                    }
                    for pid, p in team.players.items()
                },
            }

        return {
            "game_id": self.game_id,
            "sport_code": self.sport_code,
            "period": self.period,
            "clock": self.clock,
            "away_score": self.away_score,
            "home_score": self.home_score,
            "away": team_blob(self.away),
            "home": team_blob(self.home),
            "shots": list(self.shots),
            "plays": {
                aid: {
                    "period": play.period,
                    "clock": play.clock,
                    "text": play.text,
                    "score": play.score,
                }
                for aid, play in self.plays.items()
                if play.text
            },
        }

    def apply_live_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Overlay a same-game snapshot saved on quit (period/clock/score/shots/lineups)."""
        if str(snapshot.get("game_id") or "") != str(self.game_id):
            return
        self.period = snapshot.get("period") or self.period
        self.clock = snapshot.get("clock") or self.clock
        if snapshot.get("away_score") is not None:
            self.away_score = str(snapshot.get("away_score"))
        if snapshot.get("home_score") is not None:
            self.home_score = str(snapshot.get("home_score"))
        shots = snapshot.get("shots")
        if isinstance(shots, list) and shots:
            self.shots = [dict(s) for s in shots if isinstance(s, dict)]
        plays = snapshot.get("plays")
        if isinstance(plays, dict):
            for aid, blob in plays.items():
                if not isinstance(blob, dict):
                    continue
                play = self.plays.get(str(aid))
                if play is None:
                    play = PlayLine(action_number=str(aid))
                    self.plays[str(aid)] = play
                play.period = blob.get("period") or play.period
                play.clock = blob.get("clock") or play.clock
                play.text = blob.get("text") or play.text
                play.score = blob.get("score") or play.score
        for side, team in (("away", self.away), ("home", self.home)):
            blob = snapshot.get(side)
            if not isinstance(blob, dict) or team is None:
                continue
            if blob.get("totals") and isinstance(blob["totals"], dict):
                team.totals.update(blob["totals"])
            players = blob.get("players")
            if not isinstance(players, dict):
                continue
            for pid, pdata in players.items():
                if not isinstance(pdata, dict):
                    continue
                player = team.players.get(str(pid))
                if player is None:
                    player = LivePlayer(player_id=str(pid), competitor_id=team.competitor_id)
                    team.players[str(pid)] = player
                if pdata.get("number") is not None:
                    player.number = None if pdata.get("number") in ("", None) else str(pdata["number"])
                if pdata.get("name"):
                    player.name = str(pdata["name"])
                if pdata.get("position"):
                    player.position = str(pdata["position"])
                if "starter" in pdata:
                    player.starter = bool(pdata["starter"])
                if "on_court" in pdata:
                    player.on_court = bool(pdata["on_court"])
                stats = pdata.get("stats")
                if isinstance(stats, dict):
                    player.stats.update(stats)

    def apply_message(self, channel: str, data: dict[str, Any]) -> None:
        self.raw_last_message = data
        self.new_play_ids = []
        self.new_shots = []
        if "box_score" in data and isinstance(data["box_score"], dict):
            self._apply_box_score(data["box_score"])
        if "contest_score" in data and isinstance(data["contest_score"], dict):
            self._apply_contest_score(data["contest_score"])
        if "shot_chart" in data:
            self._apply_shot_chart(data["shot_chart"])

        # Scoreboard channel publishes { "<game_id>": { clock_*, period_*, score_*, ... } }
        game_payload = data.get(str(self.game_id))
        if isinstance(game_payload, dict):
            self._apply_contest_score(game_payload)

        # NCAA page reloads when this flips to 1; we refresh HTML ourselves.
        if "game_has_initialized" in data:
            value = data.get("game_has_initialized")
            if value in (1, "1", True, "true", "True"):
                self.awaiting_tipoff = False

        # Direct element updates sometimes arrive as top-level keys
        self._ingest_play_keys(data)
        for key, value in data.items():
            if key.startswith("clock_") and str(self.game_id) in key:
                self.clock = str(value)
            elif key.startswith("period_") and str(self.game_id) in key:
                self.period = str(value)
            elif key.startswith("score_") and self.away and key.endswith(self.away.competitor_id):
                self.away_score = str(value)
            elif key.startswith("score_") and self.home and key.endswith(self.home.competitor_id):
                self.home_score = str(value)

    def _apply_contest_score(self, payload: dict[str, Any]) -> None:
        # Payload shapes vary: either nested by contest id or flat element ids.
        if str(self.game_id) in payload and isinstance(payload[str(self.game_id)], dict):
            payload = payload[str(self.game_id)]
        for key, value in payload.items():
            text = "" if value is None else str(value)
            if key.startswith("clock_"):
                self.clock = text
            elif key.startswith("period_"):
                self.period = text
            elif key.startswith("score_") and self.away and key.endswith(self.away.competitor_id):
                self.away_score = text
            elif key.startswith("score_") and self.home and key.endswith(self.home.competitor_id):
                self.home_score = text
            elif key.startswith("linescore_"):
                # linescore_{competitorId}_{period}
                parts = key.split("_")
                if len(parts) >= 3 and parts[-1] in {"T", "total"}:
                    competitor_id = parts[1]
                    if self.away and competitor_id == self.away.competitor_id:
                        self.away_score = text
                    elif self.home and competitor_id == self.home.competitor_id:
                        self.home_score = text

    def _apply_box_score(self, updates: dict[str, Any]) -> None:
        for key, value in updates.items():
            # {competitorId}_{playerId}_{statName}  — playerId may be -100 for totals
            match = re.match(r"^(\d+)_(-?\d+)_(.+)$", key)
            if not match:
                continue
            competitor_id, player_id, stat_name = match.groups()
            team = self.team_for_competitor(competitor_id)
            if team is None:
                continue

            display_stat = STAT_ALIASES.get(stat_name, stat_name)
            normalized = value
            if isinstance(value, str):
                stripped = value.strip()
                if stripped == "":
                    normalized = 0
                elif stripped == "*":
                    normalized = 1
                elif stripped.isdigit():
                    normalized = int(stripped)
                else:
                    normalized = stripped
            elif value == "*":
                normalized = 1

            if player_id == "-100":
                team.totals[display_stat] = normalized
                continue

            player = team.players.get(player_id)
            if player is None:
                player = LivePlayer(player_id=player_id, competitor_id=competitor_id)
                team.players[player_id] = player

            if stat_name == "active":
                player.on_court = value == "*" or value == 1 or value is True
            elif stat_name == "starter":
                player.starter = value == "*" or value == 1 or value is True
            elif stat_name in {"jersey_no", "#"}:
                player.number = None if normalized in (None, "", 0) else str(normalized)
            elif stat_name == "name":
                player.name = None if normalized in (None, "", 0) else str(normalized)
            elif stat_name in {"position", "Pos", "P"}:
                player.position = None if normalized in (None, "", 0) else str(normalized)
            else:
                player.stats[display_stat] = normalized

    def _ingest_play_keys(self, payload: dict[str, Any]) -> None:
        """Collect play_text_N / period_N / clock_N / score_N fragments into PlayLine."""
        for key, value in payload.items():
            for prefix in ("play_text_", "period_", "clock_", "score_"):
                if not key.startswith(prefix):
                    continue
                action = key[len(prefix) :]
                if not action.isdigit() or action == str(self.game_id):
                    continue
                play = self.plays.get(action)
                if play is None:
                    play = PlayLine(action_number=action)
                    self.plays[action] = play
                    self.new_play_ids.append(action)
                text = "" if value is None else str(value)
                if key.startswith("play_text_"):
                    # Faye often sends period/clock before the text. Mark the
                    # play again when text first arrives so subs/FT are not skipped.
                    previous = play.text
                    play.text = text
                    if text and text != previous and action not in self.new_play_ids:
                        self.new_play_ids.append(action)
                elif key.startswith("period_"):
                    previous = play.period
                    play.period = text
                    # Text-before-clock: re-queue so auto-subs can use the real period.
                    if (
                        play.text
                        and text
                        and text != previous
                        and action not in self.new_play_ids
                    ):
                        self.new_play_ids.append(action)
                elif key.startswith("clock_"):
                    previous = play.clock
                    play.clock = text
                    if (
                        play.text
                        and text
                        and text != previous
                        and action not in self.new_play_ids
                    ):
                        self.new_play_ids.append(action)
                elif key.startswith("score_"):
                    play.score = text
        for nest_key in ("contest_plays", "plays"):
            nested = payload.get(nest_key)
            if isinstance(nested, dict):
                self._ingest_play_keys(nested)
        # Live Faye often nests one action as play_471: {play_text_471, clock_471, …}
        for key, value in payload.items():
            if (
                isinstance(value, dict)
                and key.startswith("play_")
                and key[len("play_") :].isdigit()
            ):
                self._ingest_play_keys(value)

    def _apply_shot_chart(self, chart: Any) -> None:
        items: list[Any]
        if isinstance(chart, list):
            items = chart
        elif isinstance(chart, dict):
            # May be {play_id: attrs} or a single shot dict
            if "x" in chart and "y" in chart:
                items = [chart]
            else:
                items = list(chart.values())
        else:
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            if "x" not in item or "y" not in item:
                continue
            shot = {
                "x": float(item.get("x") or 0),
                "y": float(item.get("y") or 0),
                "org_id": str(item.get("color") or item.get("shooting_org") or item.get("org_id") or ""),
                "made": bool(item.get("made")),
                "play_id": str(item.get("play_id") or ""),
                "description": str(item.get("description") or ""),
            }
            # Dedupe by play_id when present
            if shot["play_id"] and any(s.get("play_id") == shot["play_id"] for s in self.shots):
                continue
            self.shots.append(shot)
            self.new_shots.append(shot)


def extract_faye_auth(html: str) -> FayeAuth:
    user_match = re.search(r"message\.ext\.userId\s*=\s*'([^']+)'", html)
    if not user_match:
        raise ValueError("Could not find Faye userId in box score HTML")
    tokens: dict[str, str] = {}
    block = re.search(r"tokensByChannel\s*=\s*\{(.*?)\};", html, re.S)
    if not block:
        raise ValueError("Could not find Faye tokensByChannel in box score HTML")
    for channel, token in re.findall(r"'([^']+)'\s*:\s*'([^']+)'", block.group(1)):
        tokens[channel] = token
    return FayeAuth(user_id=user_match.group(1), tokens_by_channel=tokens)


def extract_sport_code(html: str, game_id: str) -> str:
    match = re.search(rf"'/(WBB|MBB)/box_score/{re.escape(str(game_id))}'", html)
    if match:
        return match.group(1)
    if "/MBB/" in html:
        return "MBB"
    return "WBB"


def extract_player_ids_from_dom(html: str) -> dict[str, dict[str, str]]:
    """Map competitor_id -> {jersey_or_name hint -> player_id} via player_data_* ids."""
    soup = BeautifulSoup(html, "lxml")
    mapping: dict[str, dict[str, str]] = {}
    for el in soup.select("[id^=player_data_]"):
        # player_data_{competitor}_{player}_{stat}[_displayGroup]
        parts = el["id"].split("_")
        if len(parts) < 4:
            continue
        competitor_id, player_id = parts[2], parts[3]
        if player_id == "-100":
            continue
        mapping.setdefault(competitor_id, {})[player_id] = player_id
    return mapping


def bootstrap_live_state(html: str, game_id: str) -> BasketballLiveState:
    """Parse initial HTML snapshot + Faye credentials into live state."""
    auth = extract_faye_auth(html)
    sport_code = extract_sport_code(html, game_id)
    period, clock = parse_box_score_clock(html)
    away_score, home_score = parse_linescore_totals(html)
    awaiting_tipoff = box_score_waiting_to_initialize(html)

    soup = BeautifulSoup(html, "lxml")
    competitor_ids: list[str] = []
    away_box: TeamBox
    home_box: TeamBox
    try:
        away_box, home_box = parse_basketball_box_score(html)
        boards = soup.find_all("div", class_="col p-2")
        for board in boards:
            table = board.find("table", id=re.compile(r"^competitor_"))
            if table and table.get("id"):
                # competitor_{id}_{displayGroup}_data_table_dt
                m = re.match(r"competitor_(\d+)_", table["id"])
                if m:
                    competitor_ids.append(m.group(1))
        if len(competitor_ids) < 2:
            for table in soup.find_all("table", id=re.compile(r"^competitor_")):
                m = re.match(r"competitor_(\d+)_", table.get("id", ""))
                if m and m.group(1) not in competitor_ids:
                    competitor_ids.append(m.group(1))
        if len(competitor_ids) < 2:
            raise ValueError("Could not determine away/home competitor ids")
        awaiting_tipoff = False
    except ValueError:
        # Pregame: NCAA shows linescore + "Waiting for game to initialize"
        # before competitor roster tables exist.
        away_box, home_box, competitor_ids = parse_linescore_matchup(html)
        awaiting_tipoff = True
        logger.info(
            "Game %s has not initialized yet (%s @ %s); waiting for tip-off",
            game_id,
            away_box.team_name or "Away",
            home_box.team_name or "Home",
        )

    org_by_name = parse_team_organization_ids(html)
    team_page_ids = parse_linescore_team_page_ids(html)
    linescore_records = parse_linescore_season_records(html)

    def build_team(
        box: TeamBox,
        competitor_id: str,
        venue: str,
        *,
        team_page_id: str | None = None,
        season_overall: str | None = None,
        season_conference: str | None = None,
    ) -> LiveTeam:
        logo_org = organization_id_from_logo(box.team_logo_url)
        name_org = organization_id_for_team_name(box.team_name, org_by_name)
        team = LiveTeam(
            competitor_id=competitor_id,
            name=box.team_name,
            logo_url=box.team_logo_url,
            org_id=logo_org or name_org,
            team_page_id=team_page_id,
            venue=venue,
            season_records=TeamSeasonRecords(
                overall=season_overall,
                conference=season_conference,
            ),
        )
        # Attach player ids from cell ids in this competitor's table.
        # Prefer jersey number match; fall back to name match.
        table = soup.find("table", id=re.compile(rf"^competitor_{competitor_id}_"))
        id_by_number: dict[str, str] = {}
        id_by_name: dict[str, str] = {}
        if table:
            for row in table.find_all("tr"):
                marker = row.find(
                    id=re.compile(rf"^player_data_{competitor_id}_\d+_(?:active|starter)$")
                )
                if not marker:
                    # Any player_data cell in the row is enough to recover the id.
                    marker = row.find(id=re.compile(rf"^player_data_{competitor_id}_\d+_"))
                if not marker:
                    continue
                m = re.match(
                    rf"player_data_{competitor_id}_(\d+)_",
                    marker.get("id", ""),
                )
                if not m:
                    continue
                player_id = m.group(1)
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if not cells:
                    continue
                jersey = cells[0]
                name = cells[1] if len(cells) > 1 else ""
                id_by_number[jersey] = player_id
                if name:
                    id_by_name[name.casefold()] = player_id

        for player in box.roster:
            player_id = None
            if player.number is not None and str(player.number) in id_by_number:
                player_id = id_by_number[str(player.number)]
            elif player.name and player.name.casefold() in id_by_name:
                player_id = id_by_name[player.name.casefold()]
            if not player_id:
                # Fallback synthetic id so state still works offline
                player_id = f"name:{player.name}"
            team.players[player_id] = LivePlayer(
                player_id=player_id,
                competitor_id=competitor_id,
                number=player.number,
                name=player.name,
                position=player.position,
                starter=player.starter,
                on_court=player.on_court,
                stats=dict(player.stats),
            )
        if box.totals:
            team.totals = dict(box.totals.stats)
        return team

    away_page_id = team_page_ids[0] if len(team_page_ids) > 0 else None
    home_page_id = team_page_ids[1] if len(team_page_ids) > 1 else None
    away_rec = linescore_records[0] if len(linescore_records) > 0 else (None, None)
    home_rec = linescore_records[1] if len(linescore_records) > 1 else (None, None)
    state = BasketballLiveState(
        game_id=str(game_id),
        sport_code=sport_code,
        period=period,
        clock=clock,
        away_score=away_score,
        home_score=home_score,
        away=build_team(
            away_box,
            competitor_ids[0],
            "away",
            team_page_id=away_page_id,
            season_overall=away_rec[0],
            season_conference=away_rec[1],
        ),
        home=build_team(
            home_box,
            competitor_ids[1],
            "home",
            team_page_id=home_page_id,
            season_overall=home_rec[0],
            season_conference=home_rec[1],
        ),
        auth=auth,
        shots=parse_embedded_shots(html),
        awaiting_tipoff=awaiting_tipoff,
    )
    return state
