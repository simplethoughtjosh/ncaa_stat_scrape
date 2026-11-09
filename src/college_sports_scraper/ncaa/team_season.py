"""Fetch NCAA team + head-coach season records for Captivate game summary."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup

from college_sports_scraper.ncaa.client import NcaaClient
from college_sports_scraper.ncaa.live_state import BasketballLiveState, LiveTeam

logger = logging.getLogger(__name__)

_RECORD_CELL_RE = re.compile(r"^\d+\s*-\s*\d+")
_SEASON_YEAR_RE = re.compile(r"^\d{4}-\d{2}$")


@dataclass
class ParsedTeamSeasonPage:
    overall: str | None = None
    conference: str | None = None
    coach_name: str | None = None
    coach_people_id: str | None = None


@dataclass
class ParsedCoachSummary:
    name: str | None = None
    school_total: str | None = None
    career_total: str | None = None
    season_total: str | None = None
    total_seasons: str | None = None


def _clean_record(text: str | None) -> str | None:
    if not text:
        return None
    value = " ".join(str(text).split())
    return value or None


def _wl_only(record: str | None) -> str | None:
    """Prefer bare W-L when NCAA includes a winning percentage."""
    cleaned = _clean_record(record)
    if not cleaned:
        return None
    match = re.match(r"(\d+\s*-\s*\d+)", cleaned)
    if match:
        return re.sub(r"\s+", "", match.group(1))
    return cleaned


def parse_team_season_page(html: str) -> ParsedTeamSeasonPage:
    """Parse Season-to-date Records + head coach link from an NCAA team page."""
    soup = BeautifulSoup(html, "lxml")
    overall = None
    conference = None

    # Preferred: labeled Overall / Conference rows near "Season-to-date".
    labels = {
        "overall": "overall",
        "conference": "conference",
        "conf": "conference",
    }
    for row in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"])]
        if len(cells) < 2:
            continue
        key = cells[0].casefold().rstrip(":")
        kind = labels.get(key)
        if not kind:
            continue
        value = _wl_only(cells[1]) or _clean_record(cells[1])
        if kind == "overall":
            overall = value
        elif kind == "conference":
            conference = value

    if overall is None or conference is None:
        # Fallback: scan nearby text blocks for "Overall 1-0" style pairs.
        text = soup.get_text("\n", strip=True)
        if overall is None:
            m = re.search(r"Overall\s*[:=\s]\s*(\d+\s*-\s*\d+)", text, re.I)
            if m:
                overall = re.sub(r"\s+", "", m.group(1))
        if conference is None:
            m = re.search(
                r"Conference\s*[:=\s]\s*(\d+\s*-\s*\d+)",
                text,
                re.I,
            )
            if m:
                conference = re.sub(r"\s+", "", m.group(1))

    coach_name = None
    coach_people_id = None
    for link in soup.find_all("a", href=re.compile(r"/people/\d+")):
        href = link.get("href") or ""
        m = re.search(r"/people/(\d+)", href)
        if not m:
            continue
        label = link.get_text(" ", strip=True)
        if not label or label.casefold() in {"coach", "coaches"}:
            continue
        # Prefer the first person link whose label looks like a person name.
        if len(label.split()) >= 2 or coach_people_id is None:
            coach_people_id = m.group(1)
            coach_name = label
            if len(label.split()) >= 2:
                break

    return ParsedTeamSeasonPage(
        overall=overall,
        conference=conference,
        coach_name=coach_name,
        coach_people_id=coach_people_id,
    )


def parse_coach_summary_page(html: str, *, school_hint: str | None = None) -> ParsedCoachSummary:
    """Parse Coaches Summary + current-season W–L from an NCAA people page."""
    soup = BeautifulSoup(html, "lxml")

    name = None
    for dt in soup.find_all("dt"):
        if dt.get_text(" ", strip=True).casefold().rstrip(":") == "name":
            dd = dt.find_next_sibling("dd")
            if dd:
                name = dd.get_text(" ", strip=True) or None
            break
    if not name:
        for el in soup.find_all(string=True):
            text = " ".join(str(el).split())
            if text and re.fullmatch(r"[A-Za-z.'\-]+(?:\s+[A-Za-z.'\-]+){1,3}", text):
                parent = getattr(el, "parent", None)
                classes = " ".join(parent.get("class", [])) if parent is not None else ""
                if "col-sm-9" in classes:
                    name = text
                    break

    school_total = None
    career_total = None
    total_seasons = None
    for table in soup.find_all("table"):
        headers = [
            th.get_text(" ", strip=True).casefold()
            for th in table.find_all("th")
        ]
        if "institution" not in headers or "record" not in headers:
            continue
        record_idx = headers.index("record")
        inst_idx = headers.index("institution")
        seasons_idx = headers.index("seasons") if "seasons" in headers else None
        body_rows: list[tuple[str, str, str | None]] = []
        for row in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
            needed = max(record_idx, inst_idx, seasons_idx or 0)
            if len(cells) <= needed:
                continue
            label = cells[inst_idx]
            record = _clean_record(cells[record_idx])
            seasons = (
                _clean_record(cells[seasons_idx])
                if seasons_idx is not None and len(cells) > seasons_idx
                else None
            )
            if not record or not _RECORD_CELL_RE.match(record):
                continue
            if label.casefold() == "career":
                career_total = record
            else:
                body_rows.append((label, record, seasons))
        if body_rows:
            chosen: tuple[str, str, str | None] | None = None
            if school_hint:
                hint = school_hint.casefold()
                for row in reversed(body_rows):
                    label = row[0]
                    if hint in label.casefold() or label.casefold() in hint:
                        chosen = row
                        break
            if chosen is None:
                chosen = body_rows[-1]
            school_total = chosen[1]
            total_seasons = chosen[2]
        break

    season_total = None
    history = soup.find("table", id=re.compile(r"coach_history", re.I))
    if history is not None:
        # History is oldest→newest; last YYYY-YY row is the current season.
        year_rows: list[list[str]] = []
        for row in history.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
            if len(cells) >= 5 and _SEASON_YEAR_RE.match(cells[0]):
                year_rows.append(cells)
        if year_rows:
            wins, losses = year_rows[-1][3], year_rows[-1][4]
            if wins.isdigit() and losses.isdigit():
                season_total = f"{wins}-{losses}"

    return ParsedCoachSummary(
        name=name,
        school_total=_clean_record(school_total),
        career_total=_clean_record(career_total),
        season_total=_clean_record(season_total),
        total_seasons=_clean_record(total_seasons),
    )


def _apply_team_page(team: LiveTeam, parsed: ParsedTeamSeasonPage) -> None:
    if parsed.overall:
        team.season_records.overall = parsed.overall
    if parsed.conference:
        team.season_records.conference = parsed.conference
    if parsed.coach_name:
        team.coach.name = parsed.coach_name
    if parsed.coach_people_id:
        team.coach.people_id = parsed.coach_people_id


def _apply_coach_page(team: LiveTeam, parsed: ParsedCoachSummary) -> None:
    if parsed.name:
        team.coach.name = parsed.name
    if parsed.school_total:
        team.coach.school_total = parsed.school_total
    if parsed.career_total:
        team.coach.career_total = parsed.career_total
    if parsed.season_total:
        team.coach.season_total = parsed.season_total
    if parsed.total_seasons:
        team.coach.total_seasons = parsed.total_seasons


def _fetch_html(client: NcaaClient, path_or_url: str) -> str | None:
    """Fetch team/coach HTML without requiring a live box-score Faye page."""
    html = client.browser.fetch_stats_html(path_or_url)
    if not html:
        logger.info("NCAA page unavailable for %s", path_or_url)
        return None
    return html


def enrich_team_season_stats(
    client: NcaaClient,
    state: BasketballLiveState,
) -> dict[str, Any]:
    """Fetch team + coach pages once and attach records onto live teams.

    Soft-fails per side so live control continues when a page is gated.
    """
    summary: dict[str, Any] = {"ok": True, "teams": []}
    for team in (state.away, state.home):
        if team is None:
            continue
        entry: dict[str, Any] = {
            "team": team.name,
            "team_page_id": team.team_page_id,
            "ok": False,
        }
        if not team.team_page_id:
            entry["error"] = "missing_team_page_id"
            summary["ok"] = False
            summary["teams"].append(entry)
            continue

        team_html = _fetch_html(client, f"/teams/{team.team_page_id}")
        if team_html:
            parsed_team = parse_team_season_page(team_html)
            _apply_team_page(team, parsed_team)
            entry["overall"] = team.season_records.overall
            entry["conference"] = team.season_records.conference
            entry["coach_people_id"] = team.coach.people_id
        else:
            entry["error"] = "team_page_unavailable"
            summary["ok"] = False

        people_id = team.coach.people_id
        if people_id:
            coach_html = _fetch_html(
                client,
                f"/people/{people_id}?sport_code={state.sport_code}",
            )
            if coach_html:
                parsed_coach = parse_coach_summary_page(
                    coach_html,
                    school_hint=team.name,
                )
                _apply_coach_page(team, parsed_coach)
                entry["coach_name"] = team.coach.name
                entry["school_total"] = team.coach.school_total
                entry["career_total"] = team.coach.career_total
                entry["season_total"] = team.coach.season_total
                entry["total_seasons"] = team.coach.total_seasons
            else:
                entry["coach_error"] = "coach_page_unavailable"
                summary["ok"] = False

        entry["ok"] = bool(
            team.season_records.overall
            or team.season_records.conference
            or team.coach.career_total
            or team.coach.season_total
            or team.coach.school_total
        )
        if not entry["ok"]:
            summary["ok"] = False
        summary["teams"].append(entry)
        logger.info(
            "Season stats for %s: overall=%s conf=%s coach=%s school=%s seasons=%s career=%s season=%s",
            team.name,
            team.season_records.overall,
            team.season_records.conference,
            team.coach.name,
            team.coach.school_total,
            team.coach.total_seasons,
            team.coach.career_total,
            team.coach.season_total,
        )
    return summary
