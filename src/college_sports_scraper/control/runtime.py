"""Orchestrate live Faye → Captivate pushes, play parsers, and operator cues."""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from college_sports_scraper.basketball.events import (
    SubBatch,
    SubBatcher,
    clock_has_counted_down,
    parse_free_throw,
    parse_substitution,
    parse_timeout,
    period_opening_seconds,
)
from college_sports_scraper.basketball.graphics import (
    apply_free_throw_outcome,
    apply_substitution_line,
    find_player_by_name,
    foul_trouble_payload,
    leaders_payload,
    on_court_payload,
    player_l3_payload,
    resolve_player_code,
    sport_config_payload,
    substitution_pair_payloads,
    substitution_payload,
    timeout_payload,
)
from college_sports_scraper.basketball.photos import team_logo_path
from college_sports_scraper.basketball.shot_map import ShotPoint, render_home_away_maps
from college_sports_scraper.ncaa.live_state import BasketballLiveState
from college_sports_scraper.newblue.client import CaptivateConnectionError
from college_sports_scraper.newblue.controller import BasketballGraphicsController

logger = logging.getLogger(__name__)

# How often to look again for a headshot that was missing at the last lineup push.
PHOTO_REFRESH_SECONDS = 5.0


@dataclass
class OperatorConfig:
    automatic_subs: bool = True
    automatic_fouls: bool = True
    automatic_timeouts: bool = False
    hold_seconds: float = 4.0
    # Foul trouble auto/Take hold. Operator sets this on the Captivate panel.
    foul_hold_seconds: float = 4.0
    control_port: int = 8765
    shot_map_dir: str = "output/shot_maps"


@dataclass
class LiveControlRuntime:
    graphics: BasketballGraphicsController
    config: OperatorConfig = field(default_factory=OperatorConfig)
    last_sub: dict[str, str] = field(default_factory=dict)
    last_sub_pairs: list[dict[str, str]] = field(default_factory=list)
    last_timeout: dict[str, str] = field(default_factory=dict)
    last_player: dict[str, str] = field(default_factory=dict)
    last_foul: dict[str, str] = field(default_factory=dict)
    last_error: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _sub_batcher: SubBatcher = field(default_factory=SubBatcher)
    _last_on_court_sig: str = ""
    _last_photo_sig: str = ""
    _last_photo_check: float | None = None
    _last_summary_sig: str = ""
    _last_foul_sig: str = ""
    _foul_player_keys: set[str] = field(default_factory=set)
    _last_leaders_sig: str = ""
    _last_shot_count: int = 0
    _pending_auto: list[dict[str, Any]] = field(default_factory=list)
    _handled_play_ids: set[str] = field(default_factory=set)
    _subs_waiting_clock: set[str] = field(default_factory=set)
    # Last FT cue for box-score reconcile: (team_name, player_name, code, ft_attempt)
    _last_ft_cue: tuple[str, str, str, str] | None = None
    _last_ft_box_sig: str = ""

    def status(self, state: BasketballLiveState | None) -> dict[str, Any]:
        sport = sport_config_payload(state) if state else {}
        return {
            "ok": True,
            "game_id": state.game_id if state else None,
            "sport": sport,
            "config": {
                "automatic_subs": self.config.automatic_subs,
                "automatic_fouls": self.config.automatic_fouls,
                "automatic_timeouts": self.config.automatic_timeouts,
                "hold_seconds": self.config.hold_seconds,
                "foul_hold_seconds": self.config.foul_hold_seconds,
            },
            "score": {
                "away": state.away_score if state else None,
                "home": state.home_score if state else None,
                "period": state.period if state else None,
                "clock": state.clock if state else None,
            },
            "last_sub": self.last_sub,
            "last_timeout": self.last_timeout,
            "last_player": self.last_player,
            "last_foul": self.last_foul,
            "last_error": self.last_error,
            "pending_auto": list(self._pending_auto),
            "graphics": self.graphics.title_overrides.copy(),
        }

    def update_config(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if "automatic_subs" in body:
                self.config.automatic_subs = bool(body["automatic_subs"])
            if "automatic_fouls" in body:
                self.config.automatic_fouls = bool(body["automatic_fouls"])
            if "automatic_timeouts" in body:
                self.config.automatic_timeouts = bool(body["automatic_timeouts"])
            if "hold_seconds" in body:
                self.config.hold_seconds = float(body["hold_seconds"])
            if "foul_hold_seconds" in body:
                try:
                    hold = float(body["foul_hold_seconds"])
                except (TypeError, ValueError):
                    hold = self.config.foul_hold_seconds
                self.config.foul_hold_seconds = max(1.0, min(60.0, hold))
        return {
            "automatic_subs": self.config.automatic_subs,
            "automatic_fouls": self.config.automatic_fouls,
            "automatic_timeouts": self.config.automatic_timeouts,
            "hold_seconds": self.config.hold_seconds,
            "foul_hold_seconds": self.config.foul_hold_seconds,
        }

    def set_graphics(self, body: dict[str, Any]) -> dict[str, str]:
        """Store graphic names chosen on the controller panel."""
        updates = {str(key): str(value or "") for key, value in body.items()}
        with self._lock:
            return self.graphics.set_title_overrides(updates)

    def pop_auto_actions(self) -> list[dict[str, Any]]:
        with self._lock:
            actions = list(self._pending_auto)
            self._pending_auto.clear()
            return actions

    def _queue_auto(
        self,
        kind: str,
        variables: dict[str, str] | None = None,
        *,
        pairs: list[dict[str, str]] | None = None,
        hold_seconds: float | None = None,
    ) -> None:
        """Controller polls these to animateIn/hold/animateOut when Automatic * is on."""
        action: dict[str, Any] = {
            "kind": kind,
            "hold_seconds": (
                float(hold_seconds)
                if hold_seconds is not None
                else self.config.hold_seconds
            ),
            "variables": variables or {},
            "ts": time.time(),
        }
        if pairs:
            action["pairs"] = pairs
        with self._lock:
            self._pending_auto.append(action)

    def cue_player(
        self, state: BasketballLiveState, code: str, *, ft_attempt: str = ""
    ) -> dict[str, Any]:
        resolved = resolve_player_code(state, code)
        if resolved is None:
            self.last_error = f"Unknown player code {code!r}"
            return {"ok": False, "error": self.last_error}
        team, player, side, normalized = resolved
        payload = player_l3_payload(
            team,
            player,
            side=side,
            code=normalized,
            ft_attempt=ft_attempt,
            sport_code=state.sport_code,
        )
        self.graphics.push_player_l3(payload)
        self.last_player = payload
        self.last_error = ""
        return {"ok": True, "player": payload}

    def on_live_update(self, state: BasketballLiveState, data: dict[str, Any]) -> None:
        try:
            # Plays first so sub in/out lines can flip on-court before the lineup push.
            self._handle_new_plays(state)
            self._flush_subs(state)
            self._push_fast_diffs(state)
            self._handle_new_shots(state)
        except Exception:
            logger.exception("Live control update failed")

    def _push_fast_diffs(self, state: BasketballLiveState) -> None:
        if not state.away or not state.home:
            return
        on_court_sig = "|".join(
            [
                *(f"a:{p.player_id}:{p.number}" for p in state.away.on_court()),
                *(f"h:{p.player_id}:{p.number}" for p in state.home.on_court()),
            ]
        )
        if on_court_sig != self._last_on_court_sig:
            if self._try_push(lambda: self.graphics.push_both_on_court(state, action="still")):
                self._last_on_court_sig = on_court_sig
                self._store_photo_sig(state)

        summary_sig = f"{state.away_score}-{state.home_score}|{state.period}|{state.clock}"
        if summary_sig != self._last_summary_sig:
            sent = self._try_push(lambda: self.graphics.push_game_summary(state, action="still"))
            sent = self._try_push(lambda: self.graphics.push_sport_config(state, action="still")) and sent
            if sent:
                self._last_summary_sig = summary_sig

        foul = foul_trouble_payload(state)
        foul_sig = foul.get("foul_count", "") + "|" + "|".join(
            foul.get(f"foul_{i}_name", "")
            + foul.get(f"foul_{i}_pf", "")
            + foul.get(f"foul_{i}_logo", "")
            for i in range(1, 6)
        )
        if foul_sig != self._last_foul_sig:
            self._publish_foul_board(state, foul, foul_sig)

        leaders = leaders_payload(state)
        leaders_sig = "|".join(
            leaders.get(f"lead_pts_{i}_name", "") + leaders.get(f"lead_pts_{i}_value", "")
            for i in range(1, 4)
        )
        if leaders_sig != self._last_leaders_sig:
            if self._try_push(lambda: self.graphics.push_leaders(leaders, action="still")):
                self._last_leaders_sig = leaders_sig

        self._reconcile_last_ft_l3(state)

    @staticmethod
    def _ft_box_sig(player) -> str:
        stats = player.stats or {}
        return f"{stats.get('FTM', 0)}|{stats.get('FTA', 0)}|{stats.get('PTS', 0)}"

    def _reconcile_last_ft_l3(self, state: BasketballLiveState) -> None:
        """When box FTM/FTA/PTS catch up for the last FT cue, still-refresh L3."""
        cue = self._last_ft_cue
        if not cue:
            return
        _team_name, player_name, code, ft_attempt = cue
        found = find_player_by_name(state, player_name)
        if not found:
            return
        team, player = found
        sig = self._ft_box_sig(player)
        if sig == self._last_ft_box_sig:
            return
        side = team.venue or ""
        payload = player_l3_payload(
            team,
            player,
            side=side,
            code=code,
            ft_attempt=ft_attempt,
            sport_code=state.sport_code,
        )
        if self._try_push(
            lambda p=payload: self.graphics.push_player_l3(p, action="still")
        ):
            self.last_player = payload
            self._last_ft_box_sig = sig

    def remember_on_court_photos(self, state: BasketballLiveState, *, now: float | None = None) -> None:
        """Record the photo paths from a lineup push that already happened."""
        self._store_photo_sig(state)
        self._last_photo_check = time.monotonic() if now is None else now

    def refresh_missing_photos(self, state: BasketballLiveState, *, now: float | None = None) -> bool:
        """Push the lineup again when a missing headshot file has appeared.

        The disk is checked at most once every PHOTO_REFRESH_SECONDS. A path that
        matches the last push is left alone.
        """
        if not state.away or not state.home:
            return False
        moment = time.monotonic() if now is None else now
        if (
            self._last_photo_check is not None
            and (moment - self._last_photo_check) < PHOTO_REFRESH_SECONDS
        ):
            return False
        self._last_photo_check = moment
        signature = self._photo_signature(state)
        if signature == self._last_photo_sig:
            return False
        if self._try_push(lambda: self.graphics.push_both_on_court(state, action="still")):
            self._last_photo_sig = signature
            return True
        return False

    def _photo_signature(self, state: BasketballLiveState) -> str:
        assert state.away and state.home
        parts: list[str] = []
        for team in (state.away, state.home):
            payload = on_court_payload(team, sport_code=state.sport_code)
            parts.extend(payload.get(f"player_{i}_photo", "") for i in range(1, 6))
        return "|".join(parts)

    def _store_photo_sig(self, state: BasketballLiveState) -> None:
        if not state.away or not state.home:
            return
        self._last_photo_sig = self._photo_signature(state)

    def _try_push(self, send) -> bool:
        """Send one graphic update. A closed Captivate does not stop the rest of the tick."""
        try:
            send()
        except CaptivateConnectionError:
            return False
        return True

    def _handle_new_plays(self, state: BasketballLiveState) -> None:
        now = time.time()
        for action_id in state.new_play_ids:
            play = state.plays.get(action_id)
            if not play or not play.text:
                continue
            text = play.text
            already = action_id in self._handled_play_ids
            if already:
                # Late clock/period after text: refresh sub batch / retry once for auto.
                self._rehandle_sub_after_clock(state, action_id, play, text, now=now)
                continue

            self._handled_play_ids.add(action_id)

            ft = parse_free_throw(text)
            if ft:
                found = find_player_by_name(state, ft.player_name)
                if found:
                    team, player = found
                    side = team.venue
                    code = f"{'H' if side == 'home' else 'A'}{player.number or ''}"
                    display_stats = apply_free_throw_outcome(
                        player.stats, made=ft.made
                    )
                    payload = player_l3_payload(
                        team,
                        player,
                        side=side,
                        code=code,
                        ft_attempt=ft.attempt_label,
                        sport_code=state.sport_code,
                        stats_override=display_stats,
                    )
                    self._last_ft_cue = (
                        team.name or "",
                        player.name or "",
                        code,
                        ft.attempt_label,
                    )
                    self._last_ft_box_sig = self._ft_box_sig(player)
                    if self._try_push(
                        lambda p=payload: self.graphics.push_player_l3(p, action="still")
                    ):
                        self.last_player = payload

            sub = parse_substitution(text)
            if sub:
                apply_substitution_line(state, sub)
                period = play.period or state.period
                clock = play.clock or state.clock
                if not play.clock and not clock_has_counted_down(
                    clock, sport_code=state.sport_code, period=period
                ):
                    self._subs_waiting_clock.add(action_id)
                for ready in self._sub_batcher.add(
                    sub,
                    period=period,
                    clock=clock,
                    now=now,
                ):
                    self._publish_sub_batch(state, ready)
            elif "substitution" in text.lower():
                logger.warning("Substitution play was not parsed: %s", text)

            timeout = parse_timeout(text)
            if timeout:
                payload = timeout_payload(state, timeout)
                if self._try_push(
                    lambda p=payload: self.graphics.push_timeout(p, action="still")
                ):
                    self.last_timeout = payload
                    if self.config.automatic_timeouts:
                        self._queue_auto("timeout", payload)

    def _rehandle_sub_after_clock(
        self,
        state: BasketballLiveState,
        action_id: str,
        play,
        text: str,
        *,
        now: float,
    ) -> None:
        """Re-run substitution only when clock/period arrives after text."""
        if action_id not in self._subs_waiting_clock:
            return
        if not play.clock:
            return
        sub = parse_substitution(text)
        if not sub:
            return
        period = play.period or state.period
        clock = play.clock
        if self._sub_batcher.refresh_meta(sub.team_abbr, period=period, clock=clock):
            self._subs_waiting_clock.discard(action_id)
            return
        # Batch already flushed as still (opening clock); retry with real clock once.
        self._subs_waiting_clock.discard(action_id)
        for ready in self._sub_batcher.add(
            sub,
            period=period,
            clock=clock,
            now=now,
        ):
            self._publish_sub_batch(state, ready)

    def _flush_subs(self, state: BasketballLiveState) -> None:
        for batch in self._sub_batcher.flush_ready(time.time()):
            self._publish_sub_batch(state, batch)

    def _publish_sub_batch(self, state: BasketballLiveState, batch) -> None:
        payload = substitution_payload(state, batch)
        pair_payloads = substitution_pair_payloads(state, batch)
        self.last_sub = payload
        self.last_sub_pairs = list(pair_payloads)
        auto = (
            self.config.automatic_subs
            and clock_has_counted_down(
                batch.clock,
                sport_code=state.sport_code,
                period=batch.period or state.period,
            )
            and bool(pair_payloads)
        )
        if auto:
            # Do not still-push here. A second team's logo/names would land
            # without animation while the first pair is still on air; the
            # controller's animateIn/update carries the variables instead.
            self._queue_auto("sub", payload, pairs=pair_payloads)
            logger.info(
                "Automatic sub queued for %s (%s pair(s)) at %s",
                batch.team_abbr,
                len(pair_payloads),
                batch.clock,
            )
        else:
            if self.config.automatic_subs and not clock_has_counted_down(
                batch.clock,
                sport_code=state.sport_code,
                period=batch.period or state.period,
            ):
                logger.info(
                    "Sub board for %s at %s kept off-air until the clock leaves the period opening",
                    batch.team_abbr,
                    batch.clock,
                )
            self._try_push(lambda: self.graphics.push_substitution(payload, action="still"))

    def push_sub_take(self, state: BasketballLiveState) -> dict[str, Any]:
        if not self.last_sub:
            return {"ok": False, "error": "No substitution board ready"}
        # Do not still-push here — animateIn/update must carry the names so
        # Take Sub plays the same name animation as Automatic Subs.
        pairs = list(self.last_sub_pairs) if self.last_sub_pairs else [dict(self.last_sub)]
        return {
            "ok": True,
            "sub": self.last_sub,
            "pairs": pairs,
            "hold_seconds": self.config.hold_seconds,
        }

    def simulate_substitution(
        self, state: BasketballLiveState, *, pair_count: int = 1
    ) -> dict[str, Any]:
        """Fake a live sub from roster names so the graphic can be tested without waiting."""
        if not state.away or not state.home:
            return {"ok": False, "error": "No teams loaded"}
        team = random.choice([state.away, state.home])
        named = [p for p in team.roster() if (p.name or "").strip()]
        need = max(2, pair_count * 2)
        if len(named) < 2:
            return {"ok": False, "error": f"Need at least 2 named players on {team.name}"}
        pair_count = max(1, min(int(pair_count), 5, len(named) // 2 or 1))
        picks = random.sample(named, k=min(need, len(named)))
        while len(picks) < pair_count * 2:
            picks.append(random.choice(named))
        outs = [p.name or "" for p in picks[:pair_count]]
        ins = [p.name or "" for p in picks[pair_count : pair_count * 2]]
        opening = period_opening_seconds(state.sport_code, state.period)
        remaining = max(0, opening - 1)
        clock = f"{remaining // 60}:{remaining % 60:02d}"
        batch = SubBatch(
            team_abbr=team.name or "TEAM",
            outs=outs,
            ins=ins,
            period=state.period,
            clock=clock,
        )
        payload = substitution_payload(state, batch)
        pair_payloads = substitution_pair_payloads(state, batch)
        self.last_sub = payload
        self.last_sub_pairs = list(pair_payloads)
        # Controller animateIn/update owns the on-air vars so a second team
        # logo change can play the update animation instead of a silent still.
        self._queue_auto("sub", payload, pairs=pair_payloads)
        return {
            "ok": True,
            "team": team.name,
            "side": team.venue,
            "clock": clock,
            "pairs": [{"out": o, "in": i} for o, i in batch.pairs()],
            "sub": payload,
        }

    def push_timeout_take(self, state: BasketballLiveState) -> dict[str, Any]:
        if not self.last_timeout:
            # Manual blank timeout bumper with current clock
            payload = timeout_payload(state, label="Timeout")
            self.last_timeout = payload
        self.graphics.push_timeout(self.last_timeout, action="still")
        self._queue_auto("timeout", self.last_timeout)
        return {"ok": True, "timeout": self.last_timeout}

    def _keys_from_foul_payload(self, foul: dict[str, str]) -> set[str]:
        keys: set[str] = set()
        for i in range(1, 6):
            name = (foul.get(f"foul_{i}_name") or "").strip()
            if not name:
                continue
            team = (foul.get(f"foul_{i}_team") or "").strip()
            keys.add(f"{team}|{name}")
        return keys

    def _publish_foul_board(
        self, state: BasketballLiveState, foul: dict[str, str], foul_sig: str
    ) -> None:
        """Still-update the board; auto-air only when a new player crosses threshold."""
        self.last_foul = foul
        current_keys = self._keys_from_foul_payload(foul)
        # First observation after start: learn who's already in trouble, don't auto-air.
        primed = self._last_foul_sig == "" and not self._foul_player_keys
        new_entrants = set() if primed else (current_keys - self._foul_player_keys)
        auto = self.config.automatic_fouls and bool(new_entrants)
        if auto:
            # animateIn carries variables; avoid a silent still that races the take.
            hold = float(self.config.foul_hold_seconds)
            self._queue_auto("foul", foul, hold_seconds=hold)
            logger.info(
                "Automatic foul trouble queued (%s new: %s, hold %.0fs)",
                len(new_entrants),
                ", ".join(sorted(new_entrants)),
                hold,
            )
            self._last_foul_sig = foul_sig
            self._foul_player_keys = current_keys
            return
        if self._try_push(lambda: self.graphics.push_foul_trouble(foul, action="still")):
            self._last_foul_sig = foul_sig
            self._foul_player_keys = current_keys

    def push_foul_take(self, state: BasketballLiveState) -> dict[str, Any]:
        payload = foul_trouble_payload(state)
        self.last_foul = payload
        # Controller animateIn/hold/out owns the take — do not still-push or
        # queue auto here (that used to race Take and leave boards stuck).
        return {
            "ok": True,
            "foul": payload,
            "hold_seconds": float(self.config.foul_hold_seconds),
        }

    def push_leaders_take(self, state: BasketballLiveState) -> dict[str, Any]:
        payload = leaders_payload(state)
        self.graphics.push_leaders(payload, action="still")
        return {"ok": True, "leaders": payload}

    @staticmethod
    def _resolve_shot_org(team, other, shots: list[dict[str, Any]]) -> str:
        """School org id for shot filtering; never prefer competitor id over colors."""
        known = team.organization_id
        if known:
            return str(known)
        other_org = str(other.organization_id or "")
        candidates = {
            str(s.get("org_id") or "")
            for s in shots
            if s.get("org_id") not in (None, "")
        }
        candidates.discard("")
        if other_org:
            candidates.discard(other_org)
        if len(candidates) == 1:
            inferred = next(iter(candidates))
            # Stamp so later cues / rebuilds don't re-infer.
            team.org_id = inferred
            return inferred
        return str(team.competitor_id)

    def rebuild_shot_maps(self, state: BasketballLiveState) -> bool:
        """Draw home/away made-shot PNGs from every shot currently in state."""
        if not state.away or not state.home:
            return False
        points = [
            ShotPoint(
                x=float(s["x"]),
                y=float(s["y"]),
                org_id=str(s["org_id"]),
                made=bool(s["made"]),
                play_id=str(s.get("play_id") or ""),
            )
            for s in state.shots
        ]
        out_dir = Path(self.config.shot_map_dir)
        # shot_chart.color is the school org id (logo …/30209.gif), not competitor_*.
        away_org = self._resolve_shot_org(state.away, state.home, state.shots)
        home_org = self._resolve_shot_org(state.home, state.away, state.shots)
        away_path, home_path = render_home_away_maps(
            points,
            away_org_id=away_org,
            home_org_id=home_org,
            output_dir=out_dir,
            away_logo_path=team_logo_path(state.sport_code, state.away.name or ""),
            home_logo_path=team_logo_path(state.sport_code, state.home.name or ""),
        )
        sent = self._try_push(
            lambda: self.graphics.push_shot_maps(
                {
                    "away_shot_map": str(away_path.resolve()),
                    "home_shot_map": str(home_path.resolve()),
                },
                action="still",
            )
        )
        if sent:
            self._last_shot_count = len(state.shots)
        return sent

    def _handle_new_shots(self, state: BasketballLiveState) -> None:
        if not state.away or not state.home:
            return
        if not state.new_shots and len(state.shots) == self._last_shot_count:
            return
        # Rebuild when any new made shot arrives (or on first populate from HTML).
        if (
            state.new_shots
            and not any(s.get("made") for s in state.new_shots)
            and self._last_shot_count
        ):
            return
        if not state.new_shots and not any(s.get("made") for s in state.shots):
            return
        self.rebuild_shot_maps(state)
