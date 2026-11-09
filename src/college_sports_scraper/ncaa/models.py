from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GameSummary:
    game_id: str
    match_time: str | None = None
    match_period: str | None = None
    match_clock: str | None = None
    attendance: str | None = None
    away_name: str | None = None
    away_id: str | None = None
    away_logo_url: str | None = None
    home_name: str | None = None
    home_id: str | None = None
    home_logo_url: str | None = None
    away_final_score: str | None = None
    home_final_score: str | None = None
    period_scores: dict[str, str | None] = field(default_factory=dict)
    box_score_url: str | None = None

    @property
    def label(self) -> str:
        away = self.away_name or "Away"
        home = self.home_name or "Home"
        score = ""
        if self.away_final_score or self.home_final_score:
            score = f"  {self.away_final_score or '-'}–{self.home_final_score or '-'}"
        period = ""
        if self.match_period or self.match_clock:
            period = f"  [{self.match_period or ''} {self.match_clock or ''}]".rstrip()
        when = f"  {self.match_time}" if self.match_time else ""
        return f"{away} @ {home}{score}{period}{when}  (id={self.game_id})"

    def matches_team(self, query: str) -> bool:
        q = query.casefold().strip()
        if not q:
            return True
        hay = " ".join(
            filter(
                None,
                [
                    self.away_name,
                    self.home_name,
                    self.away_id,
                    self.home_id,
                ],
            )
        ).casefold()
        return q in hay


@dataclass
class PlayerRow:
    """One player (or team totals) row from a box / individual stats table."""

    number: str | None = None
    name: str | None = None
    position: str | None = None
    starter: bool = False
    on_court: bool = False
    stats: dict[str, Any] = field(default_factory=dict)
    team_name: str | None = None
    team_logo_url: str | None = None
    is_totals: bool = False

    @property
    def display_name(self) -> str:
        num = f"#{self.number} " if self.number not in (None, "", "None") else ""
        name = self.name or "Unknown"
        return f"{num}{name}".strip()

    def get(self, *keys: str, default: Any = "") -> Any:
        for key in keys:
            if key in self.stats and self.stats[key] not in (None, ""):
                return self.stats[key]
            # case-insensitive fallback
            for existing, value in self.stats.items():
                if existing.casefold() == key.casefold() and value not in (None, ""):
                    return value
        return default


@dataclass
class TeamBox:
    team_name: str | None
    team_logo_url: str | None
    players: list[PlayerRow]
    venue: str  # "away" | "home"

    @property
    def roster(self) -> list[PlayerRow]:
        return [p for p in self.players if not p.is_totals]

    @property
    def totals(self) -> PlayerRow | None:
        for player in self.players:
            if player.is_totals:
                return player
        return None

    def on_court_players(self) -> list[PlayerRow]:
        marked = [p for p in self.roster if p.on_court]
        if marked:
            return marked[:5]
        starters = [p for p in self.roster if p.starter]
        if len(starters) >= 5:
            return starters[:5]
        return self.roster[:5]
