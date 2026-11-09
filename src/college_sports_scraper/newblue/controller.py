"""High-level basketball graphic pushes into Captivate."""

from __future__ import annotations

import logging
from typing import Any

from college_sports_scraper.basketball.graphics import (
    game_summary_payload,
    on_court_payload,
    player_l3_payload,
    sport_config_payload,
)
from college_sports_scraper.ncaa.live_state import BasketballLiveState, LivePlayer, LiveTeam
from college_sports_scraper.newblue.client import CaptivateClient
from college_sports_scraper.newblue.config import NewBlueConfig, remap_variables

logger = logging.getLogger(__name__)

# Fallback input name when a title mapping omits input
DEFAULT_INPUT = "STP_NCAA_Basketball: Main"

# Purposes an operator can point at a project graphic. Sport settings are not a graphic.
GRAPHIC_TITLE_KEYS = (
    "away_on_court",
    "home_on_court",
    "player_l3",
    "substitution",
    "foul_trouble",
    "leaders",
    "timeout",
    "game_summary",
    "shot_maps",
)


class BasketballGraphicsController:
    def __init__(self, config: NewBlueConfig, client: CaptivateClient | None = None) -> None:
        self.config = config
        self.client = client or CaptivateClient(config)
        self.title_overrides: dict[str, str] = {}

    def set_title_overrides(self, updates: dict[str, str]) -> dict[str, str]:
        """Remember panel graphic picks. A blank value clears that purpose."""
        for key, value in updates.items():
            if key not in GRAPHIC_TITLE_KEYS:
                continue
            text = str(value or "").strip()
            if text:
                self.title_overrides[key] = text
            else:
                self.title_overrides.pop(key, None)
        return {key: self.title_overrides.get(key, "") for key in GRAPHIC_TITLE_KEYS}

    def close(self) -> None:
        self.client.close()

    def _push(
        self,
        mapping_key: str,
        logical: dict[str, str],
        action: str | None = None,
        *,
        allow_missing: bool = False,
    ) -> dict[str, Any]:
        mapping = self.config.titles.get(mapping_key)
        override = self.title_overrides.get(mapping_key, "").strip()
        if mapping is None:
            if allow_missing or override:
                # A picked graphic gets the update. Otherwise linked titles on the input do.
                return self.client.schedule_action(
                    action,
                    input_name=DEFAULT_INPUT,
                    title=override,
                    variables=dict(logical),
                )
            raise KeyError(f"No title mapping for {mapping_key!r} in NewBlue config")
        variables = remap_variables(logical, mapping)
        return self.client.schedule_action(
            action,
            input_name=mapping.input or DEFAULT_INPUT,
            title=override or mapping.title,
            variables=variables,
        )

    def push_home_on_court(self, state: BasketballLiveState, action: str | None = None) -> dict[str, Any]:
        assert state.home
        return self._push(
            "home_on_court",
            on_court_payload(state.home, sport_code=state.sport_code),
            action,
        )

    def push_away_on_court(self, state: BasketballLiveState, action: str | None = None) -> dict[str, Any]:
        assert state.away
        return self._push(
            "away_on_court",
            on_court_payload(state.away, sport_code=state.sport_code),
            action,
        )

    def push_both_on_court(self, state: BasketballLiveState, action: str | None = None) -> list[dict[str, Any]]:
        return [
            self.push_away_on_court(state, action),
            self.push_home_on_court(state, action),
        ]

    def push_free_throw(
        self,
        team: LiveTeam,
        player: LivePlayer,
        action: str | None = None,
    ) -> dict[str, Any]:
        return self._push("player_l3", player_l3_payload(team, player), action, allow_missing=True)

    def push_player_l3(self, logical: dict[str, str], action: str | None = None) -> dict[str, Any]:
        # Prefer player_l3 mapping; fall back to legacy free_throw key
        if "player_l3" in self.config.titles:
            return self._push("player_l3", logical, action)
        if "free_throw" in self.config.titles:
            return self._push("free_throw", logical, action)
        return self._push("player_l3", logical, action, allow_missing=True)

    def push_game_summary(self, state: BasketballLiveState, action: str | None = None) -> dict[str, Any]:
        return self._push("game_summary", game_summary_payload(state), action)

    def push_sport_config(self, state: BasketballLiveState, action: str | None = None) -> dict[str, Any]:
        return self._push("sport", sport_config_payload(state), action, allow_missing=True)

    def push_foul_trouble(self, logical: dict[str, str], action: str | None = None) -> dict[str, Any]:
        return self._push("foul_trouble", logical, action, allow_missing=True)

    def push_leaders(self, logical: dict[str, str], action: str | None = None) -> dict[str, Any]:
        return self._push("leaders", logical, action, allow_missing=True)

    def push_substitution(self, logical: dict[str, str], action: str | None = None) -> dict[str, Any]:
        return self._push("substitution", logical, action, allow_missing=True)

    def push_timeout(self, logical: dict[str, str], action: str | None = None) -> dict[str, Any]:
        return self._push("timeout", logical, action, allow_missing=True)

    def push_shot_maps(self, logical: dict[str, str], action: str | None = None) -> dict[str, Any]:
        return self._push("shot_maps", logical, action, allow_missing=True)
