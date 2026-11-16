"""Parsers for NCAA livestream scoreboard and basketball box-score HTML."""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup
from bs4.element import Tag

from college_sports_scraper.ncaa.models import GameSummary, PlayerRow, TeamBox

_STAR_TRUE = {"*", "1", "true", "yes", "y"}


def _text(element: Tag | None) -> str | None:
    if element is None:
        return None
    value = element.get_text(" ", strip=True)
    return value or None


def _href_part(element: Tag | None, index: int) -> str | None:
    if element is None:
        return None
    href = element.get("href") or ""
    parts = [p for p in href.split("/") if p]
    if abs(index) > len(parts):
        return None
    return parts[index]


def _is_star(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return str(value).strip().casefold() in {s.casefold() for s in _STAR_TRUE}


def normalize_stat_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped == "":
        return 0
    if stripped == "*":
        return 1
    if stripped.isdigit():
        return int(stripped)
    return stripped


def parse_scoreboard(html: str) -> list[GameSummary]:
    """Parse livestream_scoreboards HTML into game summaries."""
    soup = BeautifulSoup(html, "lxml")
    games: list[GameSummary] = []

    for table in soup.find_all("table"):
        period_scores_table = table.find("table", id=re.compile(r"^linescore_"))
        if period_scores_table is None:
            continue

        box_info = table.find("a", target=re.compile(r"^box_score_"), class_="skipMask")
        live_box = table.find("a", target="LIVE_BOX_SCORE", class_="skipMask")
        if live_box:
            box_info = live_box

        game_id = _href_part(box_info, -2)
        if not game_id:
            # /contests/{id}/box_score → last-but-one path segment may differ
            game_id = _href_part(box_info, -2) or _href_part(box_info, -1)
        if not game_id or not str(game_id).isdigit():
            href = (box_info.get("href") if box_info else "") or ""
            match = re.search(r"/(\d+)(?:/box_score)?/?$", href)
            game_id = match.group(1) if match else None
        if not game_id:
            continue

        time_info = table.find("div", class_="col-6 p-0")
        period_info = table.find("span", id=re.compile(r"^period_"))
        clock_info = table.find("span", id=re.compile(r"^clock_"))
        attendance_info = table.find("div", class_="col p-0 text-right")
        attendance = None
        if attendance_info and "Attend:" in attendance_info.get_text():
            attendance = attendance_info.get_text().replace("Attend:", "").strip()

        box_url = None
        if box_info and box_info.get("href"):
            href = box_info["href"]
            box_url = href if href.startswith("http") else f"https://stats.ncaa.org{href}"

        data: dict[str, Any] = {
            "game_id": str(game_id),
            "match_time": _text(time_info),
            "match_period": _text(period_info),
            "match_clock": _text(clock_info),
            "attendance": attendance,
            "box_score_url": box_url,
            "period_scores": {},
        }

        period_cells = period_scores_table.find_all("td")
        rows = table.find_all("tr", id=re.compile(r"^contest_"))
        for index, row in enumerate(rows):
            venue = "away" if index == 0 else "home"
            team_img = row.find("img")
            team_info = row.find("a", class_="skipMask")
            final_score_info = row.find("div", id=re.compile(r"^score_"))

            # WBB: 4 quarters; MBB linescores often still expose 4 slots (H1/H2 + empty)
            start_index = index * 4
            for i in range(4):
                cell_index = start_index + i
                value = (
                    period_cells[cell_index].get_text(strip=True)
                    if cell_index < len(period_cells)
                    else None
                )
                data["period_scores"][f"{venue}_period_{i + 1}_score"] = value or None

            data[f"{venue}_name"] = _text(team_info)
            data[f"{venue}_id"] = _href_part(team_info, -1)
            data[f"{venue}_logo_url"] = team_img.get("src") if team_img else None
            data[f"{venue}_final_score"] = _text(final_score_info)

        games.append(GameSummary(**{k: v for k, v in data.items() if k != "period_scores"}, period_scores=data["period_scores"]))

    return games


def _header_key(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.strip())


def _find_header(headers: list[str], *candidates: str) -> str | None:
    lowered = {h.casefold(): h for h in headers}
    for candidate in candidates:
        if candidate.casefold() in lowered:
            return lowered[candidate.casefold()]
    for candidate in candidates:
        for header in headers:
            if candidate.casefold() in header.casefold():
                return header
    return None


def parse_player_row(headers: list[str], cells: list[str], team_name: str | None, team_logo_url: str | None) -> PlayerRow:
    """Map one table row into a PlayerRow.

    Live basketball tables use:
    #, Name, Pos, Starter, On Court, MP, FGM, FGA, 3FGM, 3FGA, FTM, FTA, PTS, ...
    Totals rows omit #/Pos/Starter/On Court and start with the team name.
    """
    # Pad short totals rows the same way the original ncaa_stats script did.
    values = list(cells)
    if 0 < len(values) < len(headers):
        # Insert placeholders after the first cell (team/player name position variants)
        # Live basketball totals look like: [Team, MP, FGM, ...] vs full player width.
        missing = len(headers) - len(values)
        # Prefer inserting after Name-equivalent first column for totals.
        insert_at = 1
        for _ in range(missing):
            values.insert(insert_at, "")
            insert_at += 1

    mapped = {
        _header_key(header): normalize_stat_value(value)
        for header, value in zip(headers, values)
    }

    name_key = _find_header(list(mapped.keys()), "Name", "Player")
    number_key = _find_header(list(mapped.keys()), "#", "No", "Number")
    pos_key = _find_header(list(mapped.keys()), "Pos", "P", "Position")
    starter_key = _find_header(list(mapped.keys()), "Starter")
    on_court_key = _find_header(list(mapped.keys()), "On Court", "OnCourt", "On Field")

    name = mapped.get(name_key) if name_key else None
    if isinstance(name, int):
        name = str(name)
    name = None if name in (None, "", 0) else str(name)

    number = mapped.get(number_key) if number_key else None
    if number in (None, "", 0):
        # Totals rows often put team name in the # column when columns shift
        number = None
    else:
        number = str(number)

    # Detect totals: name equals team, or first cell is team with no jersey #
    is_totals = False
    if name and team_name and name.casefold() == team_name.casefold():
        is_totals = True
    if not number and name and re.search(r"team|totals?", name, re.I):
        is_totals = True
    # Live WBB totals row: first cell is team name (was padded into Name)
    first_raw = cells[0].strip() if cells else ""
    if team_name and first_raw.casefold() == team_name.casefold():
        is_totals = True
        name = team_name
        number = None

    starter = _is_star(mapped.get(starter_key)) if starter_key else False
    on_court = _is_star(mapped.get(on_court_key)) if on_court_key else False

    # Keep identity fields out of the generic stats bag where possible
    stats = dict(mapped)
    for key in (name_key, number_key, pos_key, starter_key, on_court_key):
        if key:
            stats.pop(key, None)

    position = mapped.get(pos_key) if pos_key else None
    if position in (None, "", 0):
        position = None
    else:
        position = str(position)

    return PlayerRow(
        number=number,
        name=name,
        position=position,
        starter=starter,
        on_court=on_court,
        stats=stats,
        team_name=team_name,
        team_logo_url=team_logo_url,
        is_totals=is_totals,
    )


def parse_basketball_box_score(html: str) -> tuple[TeamBox, TeamBox]:
    """Parse a live basketball box score page into (away, home) team boxes."""
    soup = BeautifulSoup(html, "lxml")
    boards = soup.find_all("div", class_="col p-2")
    teams: list[TeamBox] = []

    for index, board in enumerate(boards):
        team_info = board.find("span", class_="d-none d-sm-block") or board.find("span")
        team_img = board.find("img")
        table = board.find("table", id=re.compile(r"^competitor_"))
        if table is None:
            continue

        team_name = _text(team_info)
        team_logo = team_img.get("src") if team_img else None
        headers = [_header_key(th.get_text()) for th in table.find_all("th")]
        players: list[PlayerRow] = []
        for row in table.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
            if not cells:
                continue
            players.append(parse_player_row(headers, cells, team_name, team_logo))

        venue = "away" if index == 0 else "home"
        teams.append(
            TeamBox(
                team_name=team_name,
                team_logo_url=team_logo,
                players=players,
                venue=venue,
            )
        )

    if len(teams) < 2:
        # Fixture / partial HTML may only include competitor tables without wrappers
        competitor_tables = soup.find_all("table", id=re.compile(r"^competitor_"))
        if len(competitor_tables) >= 2 and len(teams) < 2:
            teams = []
            for index, table in enumerate(competitor_tables[:2]):
                headers = [_header_key(th.get_text()) for th in table.find_all("th")]
                # Infer team name from totals row when wrapper is missing
                players = []
                inferred_name = None
                for row in table.find_all("tr"):
                    cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                    if not cells:
                        continue
                    if len(cells) < len(headers) and cells[0] and not cells[0].isdigit():
                        inferred_name = cells[0]
                    players.append(parse_player_row(headers, cells, inferred_name, None))
                teams.append(
                    TeamBox(
                        team_name=inferred_name or ("Away" if index == 0 else "Home"),
                        team_logo_url=None,
                        players=players,
                        venue="away" if index == 0 else "home",
                    )
                )

    if len(teams) < 2:
        raise ValueError("Could not find away/home competitor tables in box score HTML")

    return teams[0], teams[1]


def box_score_waiting_to_initialize(html: str) -> bool:
    """True when NCAA shows the pre-tip page (no roster tables yet)."""
    soup = BeautifulSoup(html, "lxml")
    if re.search(r"Waiting for game to initialize", soup.get_text(" ", strip=True), re.I):
        return True
    return len(soup.find_all("table", id=re.compile(r"^competitor_"))) < 2


def parse_linescore_team_page_ids(html: str) -> list[str]:
    """Return [away, home] NCAA team-page ids from linescore `/teams/{id}` links."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id=re.compile(r"^linescore_"))
    if table is None:
        return []
    ids: list[str] = []
    for row in table.find_all("tr"):
        if row.find(id=re.compile(r"^score_\d+$")) is None:
            continue
        team_id = None
        for link in row.find_all("a", href=re.compile(r"/teams/\d+")):
            m = re.search(r"/teams/(\d+)", link.get("href") or "")
            if m:
                team_id = m.group(1)
                break
        if team_id:
            ids.append(team_id)
        if len(ids) >= 2:
            break
    return ids


_LINESCORE_RECORD_RE = re.compile(
    r"(\d+\s*-\s*\d+)\s*,\s*Conf(?:erence)?\s+(\d+\s*-\s*\d+)",
    re.I,
)


def parse_linescore_season_records(html: str) -> list[tuple[str | None, str | None]]:
    """Return [(overall, conference), ...] for away then home from linescore blurbs."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id=re.compile(r"^linescore_"))
    if table is None:
        return []
    records: list[tuple[str | None, str | None]] = []
    for row in table.find_all("tr"):
        if row.find(id=re.compile(r"^score_\d+$")) is None:
            continue
        text = row.get_text(" ", strip=True)
        match = _LINESCORE_RECORD_RE.search(text)
        if match:
            overall = re.sub(r"\s+", "", match.group(1))
            conference = re.sub(r"\s+", "", match.group(2))
            records.append((overall, conference))
        else:
            records.append((None, None))
        if len(records) >= 2:
            break
    return records


def parse_linescore_matchup(html: str) -> tuple[TeamBox, TeamBox, list[str]]:
    """Away/home teams + competitor ids from the linescore header (pregame-safe)."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id=re.compile(r"^linescore_"))
    if table is None:
        raise ValueError("Could not find linescore table in box score HTML")

    teams: list[TeamBox] = []
    competitor_ids: list[str] = []
    for row in table.find_all("tr"):
        score_cell = row.find(id=re.compile(r"^score_\d+$"))
        if score_cell is None:
            continue
        m = re.match(r"^score_(\d+)$", score_cell.get("id", ""))
        if not m:
            continue
        competitor_id = m.group(1)
        img = row.find("img")
        team_name = None
        for link in row.find_all("a", class_="skipMask", href=re.compile(r"/teams/")):
            label = _text(link)
            if label:
                team_name = label
                break
        if team_name is None and img is not None:
            team_name = img.get("alt") or None
        team_logo = img.get("src") if img else None
        venue = "away" if len(teams) == 0 else "home"
        teams.append(
            TeamBox(
                team_name=team_name,
                team_logo_url=team_logo,
                players=[],
                venue=venue,
            )
        )
        competitor_ids.append(competitor_id)
        if len(teams) >= 2:
            break

    if len(teams) < 2 or len(competitor_ids) < 2:
        raise ValueError("Could not find away/home teams in linescore HTML")
    return teams[0], teams[1], competitor_ids


def parse_box_score_clock(html: str) -> tuple[str | None, str | None]:
    soup = BeautifulSoup(html, "lxml")
    period = _text(soup.find("span", id=re.compile(r"^period_")))
    clock = _text(soup.find("span", id=re.compile(r"^clock_")))
    return period, clock


def parse_linescore_totals(html: str) -> tuple[str | None, str | None]:
    """Return (away_total, home_total) from the linescore table when present.

    Prefer each team's ``score_{competitorId}`` cell. Mid-game period slots are
    often blank (and the clock can land on the away row), so requiring
    ``cells[-1].isdigit()`` on every row used to drop *both* scores when only
    one side looked empty. An empty total still advances to the other team.
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id=re.compile(r"^linescore_"))
    if table is None:
        return None, None

    totals: list[str | None] = []
    for row in table.find_all("tr"):
        score_cell = row.find(id=re.compile(r"^score_\d+$"))
        if score_cell is None:
            continue
        text = score_cell.get_text(strip=True)
        # Blank score cell still counts as that side so home stays aligned.
        totals.append(text if text.isdigit() else None)
        if len(totals) >= 2:
            return totals[0], totals[1]

    # Older / partial HTML without score_* ids: last numeric cell per team row.
    fallback: list[str | None] = []
    for row in table.find_all("tr"):
        cells = [c.get_text(strip=True) for c in row.find_all("td")]
        digit_cells = [c for c in cells if c.isdigit()]
        if digit_cells:
            fallback.append(digit_cells[-1])
        if len(fallback) >= 2:
            return fallback[0], fallback[1]
    return None, None
