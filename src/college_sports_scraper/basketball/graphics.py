"""Build Captivate variable maps from BasketballLiveState."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from college_sports_scraper.basketball.events import SubBatch, SubLine, TimeoutEvent
from college_sports_scraper.basketball.photos import player_photo_path, team_logo_path
from college_sports_scraper.ncaa.live_state import BasketballLiveState, LivePlayer, LiveTeam

FOUL_TROUBLE_THRESHOLD = {"WBB": 3, "MBB": 3}
FOUL_OUT = 5
MAX_FOUL_SLOTS = 5
LEADER_SLOTS = 3


def _s(value: Any, default: str = "") -> str:
    if value is None or value == "":
        return default
    return str(value)


def _stat(player: LivePlayer, *keys: str, default: str = "0") -> str:
    for key in keys:
        if key in player.stats and player.stats[key] not in (None, ""):
            return str(player.stats[key])
    return default


def _stat_int(player: LivePlayer, *keys: str, default: int = 0) -> int:
    for key in keys:
        if key in player.stats and player.stats[key] not in (None, ""):
            try:
                return int(player.stats[key])
            except (TypeError, ValueError):
                try:
                    return int(float(player.stats[key]))
                except (TypeError, ValueError):
                    continue
    return default


def _team_stat(team: LiveTeam, *keys: str, default: str = "0") -> str:
    for key in keys:
        if key in team.totals and team.totals[key] not in (None, ""):
            return str(team.totals[key])
    return default


def _fg_line(made_key: str, att_key: str, stats: dict[str, Any]) -> str:
    made = stats.get(made_key, 0) or 0
    att = stats.get(att_key, 0) or 0
    return f"{made}-{att}"


def _stat_from(stats: dict[str, Any], *keys: str, default: str = "0") -> str:
    for key in keys:
        if key in stats and stats[key] not in (None, ""):
            return str(stats[key])
    return default


def apply_free_throw_outcome(
    stats: dict[str, Any], *, made: bool | None
) -> dict[str, Any]:
    """Copy stats with this FT attempt applied for L3 display only."""
    out = dict(stats)
    if made is None:
        return out

    def _as_int(key: str) -> int:
        value = out.get(key, 0) or 0
        try:
            return int(value)
        except (TypeError, ValueError):
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return 0

    out["FTA"] = _as_int("FTA") + 1
    if made:
        out["FTM"] = _as_int("FTM") + 1
        out["PTS"] = _as_int("PTS") + 1
    return out


def period_label(state: BasketballLiveState) -> str:
    """Human period label. WBB uses quarters; MBB uses halves."""
    raw = (state.period or "").strip()
    if raw:
        return raw
    if state.sport_code == "MBB":
        return "Half"
    return "Quarter"


def sport_config_payload(state: BasketballLiveState) -> dict[str, str]:
    code = (state.sport_code or "WBB").upper()
    is_wbb = code == "WBB"
    return {
        "sport_code": code,
        "sport_label": "Women's" if is_wbb else "Men's",
        "is_wbb": "1" if is_wbb else "0",
        "period_structure": "quarters" if is_wbb else "halves",
        "period_unit": "Quarter" if is_wbb else "Half",
        "foul_trouble_threshold": str(FOUL_TROUBLE_THRESHOLD.get(code, 3)),
        "foul_out": str(FOUL_OUT),
    }


def split_player_name(full: str) -> tuple[str, str]:
    """First word, then every remaining word.

    ``Maris Kate Payseur`` becomes ``("Maris", "Kate Payseur")``.
    A single word fills the first name and leaves the last name blank.
    """
    parts = _s(full).split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def on_court_payload(
    team: LiveTeam,
    *,
    sport_code: str = "WBB",
    photo_root: Path | None = None,
) -> dict[str, str]:
    """Logical payload for one team's five on court (before YAML remapping)."""
    players = list(team.on_court())[:5]
    while len(players) < 5:
        players.append(
            LivePlayer(player_id=f"pad{len(players)}", competitor_id=team.competitor_id)
        )
    out: dict[str, str] = {"team_name": _s(team.name)}
    for i, player in enumerate(players, start=1):
        full_name = _s(player.name)
        first_name, last_name = split_player_name(full_name)
        out[f"player_{i}_name"] = full_name
        out[f"player_{i}_name_first"] = first_name
        out[f"player_{i}_name_last"] = last_name
        out[f"player_{i}_number"] = _s(player.number)
        out[f"player_{i}_pos"] = _s(player.position)
        out[f"player_{i}_photo"] = player_photo_path(
            sport_code,
            team.name or "",
            player.name or "",
            root=photo_root,
        )
    return out


def player_l3_payload(
    team: LiveTeam,
    player: LivePlayer,
    *,
    side: str = "",
    code: str = "",
    ft_attempt: str = "",
    sport_code: str = "WBB",
    photo_root: Path | None = None,
    stats_override: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Unified player lower-third / free-throw stats block."""
    stats = stats_override if stats_override is not None else player.stats
    return {
        "player_team": _s(team.name),
        "player_team_side": side or _s(team.venue),
        "player_name": _s(player.name),
        "player_number": _s(player.number),
        "player_pos": _s(player.position),
        "player_photo": player_photo_path(
            sport_code,
            team.name or "",
            player.name or "",
            root=photo_root,
        ),
        "player_team_logo": team_logo_path(
            sport_code, team.name or "", root=photo_root
        ),
        "player_pts": _stat_from(stats, "PTS"),
        "player_fg": _fg_line("FGM", "FGA", stats),
        "player_3pt": _fg_line("3FGM", "3FGA", stats),
        "player_ft": _fg_line("FTM", "FTA", stats),
        "player_reb": _stat_from(stats, "Rebs", "TotReb"),
        "player_ast": _stat_from(stats, "A", "AST"),
        "player_stl": _stat_from(stats, "S", "STL"),
        "player_blk": _stat_from(stats, "B", "BLK"),
        "player_to": _stat_from(stats, "TO"),
        "player_pf": _stat_from(stats, "F", "PF"),
        "player_min": _stat_from(stats, "MP", default=""),
        "player_ft_attempt": ft_attempt or "",
        "player_code": code or "",
        # Back-compat aliases used by older free_throw YAML mappings
        "team_name": _s(team.name),
        "player_name_legacy": _s(player.name),
        "pts": _stat_from(stats, "PTS"),
        "fg": _fg_line("FGM", "FGA", stats),
        "three_pt": _fg_line("3FGM", "3FGA", stats),
        "ft": _fg_line("FTM", "FTA", stats),
        "reb": _stat_from(stats, "Rebs", "TotReb"),
        "ast": _stat_from(stats, "A", "AST"),
        "stl": _stat_from(stats, "S", "STL"),
        "blk": _stat_from(stats, "B", "BLK"),
        "to": _stat_from(stats, "TO"),
        "pf": _stat_from(stats, "F", "PF"),
        "min": _stat_from(stats, "MP", default=""),
    }


def free_throw_payload(team: LiveTeam, player: LivePlayer) -> dict[str, str]:
    """Deprecated alias — prefer player_l3_payload."""
    return player_l3_payload(team, player, side=team.venue)


def game_summary_payload(
    state: BasketballLiveState,
    *,
    photo_root: Path | None = None,
) -> dict[str, str]:
    assert state.away and state.home
    away, home = state.away, state.home
    sport = sport_config_payload(state)
    return {
        "away_team": _s(away.name),
        "home_team": _s(home.name),
        "away_team_logo": team_logo_path(
            state.sport_code, away.name or "", root=photo_root
        ),
        "home_team_logo": team_logo_path(
            state.sport_code, home.name or "", root=photo_root
        ),
        "away_score": _s(state.away_score, "0"),
        "home_score": _s(state.home_score, "0"),
        "period_label": period_label(state),
        "clock": _s(state.clock),
        "away_fg": _fg_line("FGM", "FGA", away.totals),
        "home_fg": _fg_line("FGM", "FGA", home.totals),
        "away_3pt": _fg_line("3FGM", "3FGA", away.totals),
        "home_3pt": _fg_line("3FGM", "3FGA", home.totals),
        "away_ft": _fg_line("FTM", "FTA", away.totals),
        "home_ft": _fg_line("FTM", "FTA", home.totals),
        "away_reb": _team_stat(away, "Rebs", "TotReb"),
        "home_reb": _team_stat(home, "Rebs", "TotReb"),
        "away_ast": _team_stat(away, "A", "AST"),
        "home_ast": _team_stat(home, "A", "AST"),
        "away_to": _team_stat(away, "TO"),
        "home_to": _team_stat(home, "TO"),
        "away_stl": _team_stat(away, "S", "STL"),
        "home_stl": _team_stat(home, "S", "STL"),
        "away_blk": _team_stat(away, "B", "BLK"),
        "home_blk": _team_stat(home, "B", "BLK"),
        "away_pf": _team_stat(away, "F", "PF"),
        "home_pf": _team_stat(home, "F", "PF"),
        "away_stats_overall": _s(away.season_records.overall),
        "home_stats_overall": _s(home.season_records.overall),
        "away_stats_conference": _s(away.season_records.conference),
        "home_stats_conference": _s(home.season_records.conference),
        "away_coach_name": _s(away.coach.name),
        "home_coach_name": _s(home.coach.name),
        "away_coach_photo": player_photo_path(
            state.sport_code,
            away.name or "",
            away.coach.name or "",
            root=photo_root,
        ),
        "home_coach_photo": player_photo_path(
            state.sport_code,
            home.name or "",
            home.coach.name or "",
            root=photo_root,
        ),
        "away_coach_school_total": _s(away.coach.school_total),
        "home_coach_school_total": _s(home.coach.school_total),
        "away_coach_career_total": _s(away.coach.career_total),
        "home_coach_career_total": _s(home.coach.career_total),
        "away_coach_season_total": _s(away.coach.season_total),
        "home_coach_season_total": _s(home.coach.season_total),
        "away_coach_total_seasons": _s(away.coach.total_seasons),
        "home_coach_total_seasons": _s(home.coach.total_seasons),
        **sport,
    }


def find_player_by_jersey(team: LiveTeam, jersey: str) -> LivePlayer | None:
    needle = str(jersey).lstrip("0") or "0"
    for player in team.roster():
        num = str(player.number or "").lstrip("0") or "0"
        if num == needle:
            return player
    return None


def find_player_by_name(state: BasketballLiveState, name: str) -> tuple[LiveTeam, LivePlayer] | None:
    needle = (name or "").strip().lower()
    if not needle:
        return None
    for team in (state.away, state.home):
        if not team:
            continue
        for player in team.roster():
            if (player.name or "").strip().lower() == needle:
                return team, player
        for player in team.roster():
            pname = (player.name or "").strip().lower()
            if needle in pname or pname in needle:
                return team, player
    return None


def resolve_player_code(
    state: BasketballLiveState, code: str
) -> tuple[LiveTeam, LivePlayer, str, str] | None:
    """Resolve H21/A14 → (team, player, side, normalized_code)."""
    from college_sports_scraper.basketball.events import parse_player_code

    parsed = parse_player_code(code)
    if not parsed or not state.away or not state.home:
        return None
    side, jersey = parsed
    team = state.home if side == "home" else state.away
    player = find_player_by_jersey(team, jersey)
    if player is None:
        return None
    normalized = f"{'H' if side == 'home' else 'A'}{jersey}"
    return team, player, side, normalized


def foul_trouble_payload(
    state: BasketballLiveState, *, photo_root: Path | None = None
) -> dict[str, str]:
    code = (state.sport_code or "WBB").upper()
    threshold = FOUL_TROUBLE_THRESHOLD.get(code, 3)
    rows: list[tuple[int, str, LivePlayer, LiveTeam]] = []
    for team in (state.away, state.home):
        if not team:
            continue
        for player in team.roster():
            if not player.name:
                continue
            pf = _stat_int(player, "F", "PF")
            if pf >= threshold:
                rows.append((pf, player.name or "", player, team))
    # Fouled-out first, then most fouls → least (name tiebreak).
    rows.sort(key=lambda r: (0 if r[0] >= FOUL_OUT else 1, -r[0], r[1]))
    rows = rows[:MAX_FOUL_SLOTS]
    out: dict[str, str] = {
        "foul_count": str(len(rows)),
        "foul_threshold": str(threshold),
    }
    for i in range(1, MAX_FOUL_SLOTS + 1):
        if i <= len(rows):
            pf, _, player, team = rows[i - 1]
            team_name = _s(team.name)
            out[f"foul_{i}_team"] = team_name
            out[f"foul_{i}_side"] = _s(team.venue)
            out[f"foul_{i}_name"] = _s(player.name)
            out[f"foul_{i}_number"] = _s(player.number)
            out[f"foul_{i}_pf"] = "OUT" if pf >= FOUL_OUT else str(pf)
            out[f"foul_{i}_logo"] = team_logo_path(
                state.sport_code, team_name, root=photo_root
            )
        else:
            out[f"foul_{i}_team"] = ""
            out[f"foul_{i}_side"] = ""
            out[f"foul_{i}_name"] = ""
            out[f"foul_{i}_number"] = ""
            out[f"foul_{i}_pf"] = ""
            out[f"foul_{i}_logo"] = ""
    return out


def _leader_rows(
    state: BasketballLiveState, *stat_keys: str
) -> list[tuple[int, str, LivePlayer, LiveTeam]]:
    rows: list[tuple[int, str, LivePlayer, LiveTeam]] = []
    for team in (state.away, state.home):
        if not team:
            continue
        for player in team.roster():
            if not player.name:
                continue
            value = _stat_int(player, *stat_keys)
            if value <= 0:
                continue
            rows.append((value, player.name or "", player, team))
    rows.sort(key=lambda r: (-r[0], r[1]))
    return rows[:LEADER_SLOTS]


def leaders_payload(state: BasketballLiveState) -> dict[str, str]:
    out: dict[str, str] = {}
    categories = (
        ("pts", ("PTS",)),
        ("reb", ("Rebs", "TotReb")),
        ("ast", ("A", "AST")),
    )
    for prefix, keys in categories:
        rows = _leader_rows(state, *keys)
        for i in range(1, LEADER_SLOTS + 1):
            if i <= len(rows):
                value, _, player, team = rows[i - 1]
                out[f"lead_{prefix}_{i}_name"] = _s(player.name)
                out[f"lead_{prefix}_{i}_number"] = _s(player.number)
                out[f"lead_{prefix}_{i}_team"] = _s(team.name)
                out[f"lead_{prefix}_{i}_side"] = _s(team.venue)
                out[f"lead_{prefix}_{i}_value"] = str(value)
            else:
                out[f"lead_{prefix}_{i}_name"] = ""
                out[f"lead_{prefix}_{i}_number"] = ""
                out[f"lead_{prefix}_{i}_team"] = ""
                out[f"lead_{prefix}_{i}_side"] = ""
                out[f"lead_{prefix}_{i}_value"] = ""
    return out


def _team_side_for_abbr(state: BasketballLiveState, abbr: str) -> tuple[LiveTeam | None, str]:
    abbr_u = (abbr or "").upper()
    for team in (state.away, state.home):
        if not team or not team.name:
            continue
        # Match last token / common abbr heuristics
        name_u = team.name.upper()
        if abbr_u and (abbr_u in name_u.replace(".", "") or name_u.startswith(abbr_u)):
            return team, team.venue
    # Fallback: compare against known short names from scoresheet (WCU/CUH style)
    return None, ""


def _find_player_on_team(team: LiveTeam, name: str) -> LivePlayer | None:
    needle = (name or "").strip().lower()
    if not needle:
        return None
    exact = next((p for p in team.roster() if (p.name or "").strip().lower() == needle), None)
    if exact:
        return exact
    return next(
        (
            p
            for p in team.roster()
            if needle in (p.name or "").lower() or (p.name or "").lower() in needle
        ),
        None,
    )


def apply_substitution_line(state: BasketballLiveState, line: SubLine) -> bool:
    """Flip on-court from a play-by-play sub. Faye often lags the Active column."""
    team, _ = _team_side_for_abbr(state, line.team_abbr)
    if team is None:
        found = find_player_by_name(state, line.player_name)
        if not found:
            return False
        team, player = found
    else:
        player = _find_player_on_team(team, line.player_name)
        if player is None:
            return False
    if line.direction == "out":
        player.on_court = False
    else:
        player.on_court = True
    return True


def _resolve_sub_name(team: LiveTeam | None, name: str) -> str:
    """Prefer the roster spelling when the play-by-play name matches a player."""
    if not name:
        return ""
    if team is None:
        return name
    exact = next((p for p in team.roster() if (p.name or "").lower() == name.lower()), None)
    if exact and exact.name:
        return _s(exact.name)
    needle = name.lower()
    fuzzy = next(
        (
            p
            for p in team.roster()
            if needle in (p.name or "").lower() or (p.name or "").lower() in needle
        ),
        None,
    )
    if fuzzy and fuzzy.name:
        return _s(fuzzy.name)
    return name


def substitution_payload(
    state: BasketballLiveState,
    batch: SubBatch,
    *,
    out_name: str | None = None,
    in_name: str | None = None,
    photo_root: Path | None = None,
) -> dict[str, str]:
    """One out/in pair: names plus the team logo from the photo folder."""
    team, side = _team_side_for_abbr(state, batch.team_abbr)
    team_name = _s(team.name) if team else batch.team_abbr
    if not side and team:
        side = team.venue
    if out_name is None or in_name is None:
        pairs = batch.pairs()
        first = pairs[0] if pairs else ("", "")
        if out_name is None:
            out_name = first[0]
        if in_name is None:
            in_name = first[1]
    return {
        "sub_team": team_name,
        "sub_team_side": side,
        "sub_team_abbr": batch.team_abbr,
        "sub_team_logo": team_logo_path(state.sport_code, team_name, root=photo_root),
        "sub_out": _resolve_sub_name(team, out_name or ""),
        "sub_in": _resolve_sub_name(team, in_name or ""),
        "sub_period": _s(batch.period),
        "sub_clock": _s(batch.clock),
    }


def substitution_pair_payloads(
    state: BasketballLiveState,
    batch: SubBatch,
    *,
    photo_root: Path | None = None,
) -> list[dict[str, str]]:
    """One payload per FIFO out/in pair so the graphic can update names in place."""
    return [
        substitution_payload(
            state, batch, out_name=out_name, in_name=in_name, photo_root=photo_root
        )
        for out_name, in_name in batch.pairs()
    ]


def timeout_payload(
    state: BasketballLiveState,
    event: TimeoutEvent | None = None,
    *,
    label: str = "",
    team_abbr: str = "",
) -> dict[str, str]:
    if event:
        label = event.label
        team_abbr = event.team_abbr
    team, side = _team_side_for_abbr(state, team_abbr)
    return {
        "timeout_label": label or "Timeout",
        "timeout_team": _s(team.name) if team else team_abbr,
        "timeout_team_side": side,
        "timeout_team_abbr": team_abbr,
        "timeout_clock": _s(state.clock),
        "timeout_period": period_label(state),
        "timeout_is_media": "1" if (event and event.is_media) else "0",
    }
