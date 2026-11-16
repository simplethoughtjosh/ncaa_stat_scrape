"""Live basketball session: one HTML bootstrap, then Faye websocket updates."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from college_sports_scraper.ncaa.client import NcaaClient
from college_sports_scraper.ncaa.faye import FayeClient
from college_sports_scraper.ncaa.live_state import BasketballLiveState, bootstrap_live_state
from college_sports_scraper.ncaa.parse import box_score_waiting_to_initialize

logger = logging.getLogger(__name__)

StateListener = Callable[[BasketballLiveState, dict[str, Any]], None]
_TIPOFF_POLL_SECONDS = 20.0


class BasketballLiveSession:
    """Owns bootstrap HTML fetch + continuous Faye box-score stream."""

    def __init__(
        self,
        game_id: str | int,
        client: NcaaClient | None = None,
        on_update: StateListener | None = None,
    ) -> None:
        self.game_id = str(game_id)
        self.client = client or NcaaClient()
        self.on_update = on_update
        self.state: BasketballLiveState | None = None
        self._faye: FayeClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._from_saved_snapshot = False
        self._stats_resync_attempted = False

    def bootstrap(self, html: str | None = None, *, challenge: bool = False) -> BasketballLiveState:
        self._from_saved_snapshot = False
        supplied = html is not None
        if html is None and not challenge:
            html = self._html_from_saved_session()
        if html is None:
            html = self.client.get_live_box_score_html(self.game_id)
        elif supplied:
            # Local fixture still needs a realistic document referer for Faye.
            self.client.browser.warm_up()
            self.client.browser._last_document_url = self.client.live_box_score_url(self.game_id)
        self.state = bootstrap_live_state(html, self.game_id)
        tip = "  (waiting for tip-off)" if self.state.awaiting_tipoff else ""
        logger.info(
            "Bootstrapped game %s (%s) %s @ %s  period=%s clock=%s score=%s-%s%s",
            self.game_id,
            self.state.sport_code,
            self.state.away.name if self.state.away else "?",
            self.state.home.name if self.state.home else "?",
            self.state.period,
            self.state.clock,
            self.state.away_score,
            self.state.home_score,
            tip,
        )
        return self.state

    def _html_from_saved_session(self) -> str | None:
        """Current page via cookies (no Chrome), else the saved box score for this game."""
        url = self.client.live_box_score_url(self.game_id)
        fresh = self.client.browser.fetch_document_without_chrome(url)
        if fresh is not None:
            return fresh.text
        saved = self.client.browser.saved_box_score(self.game_id)
        if saved is None:
            return None
        html, page_url = saved
        if page_url:
            self.client.browser._last_document_url = page_url
        self._from_saved_snapshot = True
        logger.info(
            "Saved NCAA page for game %s was kept; confirming the live stream before skipping the terms check",
            self.game_id,
        )
        return html

    def _apply_saved_live_snapshot(self) -> bool:
        """Restore period/clock/score/shots/lineups saved on the last quit."""
        if self.state is None:
            return False
        snap = self.client.browser.saved_live_snapshot(self.game_id)
        if not snap:
            return False
        self.state.apply_live_snapshot(snap)
        logger.info(
            "Resumed live snapshot for game %s from %s (period=%s clock=%s score=%s-%s, %s shots)",
            self.game_id,
            snap.get("saved_at") or "unknown time",
            self.state.period,
            self.state.clock,
            self.state.away_score,
            self.state.home_score,
            len(self.state.shots),
        )
        return True

    def save_live_snapshot(self) -> None:
        if self.state is None:
            return
        self.client.browser.save_live_snapshot(self.state.to_live_snapshot())

    async def _handle(self, channel: str, data: dict[str, Any]) -> None:
        if self.state is None:
            return
        self.state.apply_message(channel, data)
        if (
            self.state.player_box_stats_stale()
            and not self._stats_resync_attempted
        ):
            self._stats_resync_attempted = True
            logger.warning(
                "Game %s has score %s-%s but player box stats are still zero; "
                "refreshing the box-score page once to resync L3 stats",
                self.game_id,
                self.state.away_score,
                self.state.home_score,
            )
            try:
                # Soft cookie fetch only — Chrome warm-up would kill livestream cookies.
                await asyncio.to_thread(self._refresh_live_html, allow_chrome=False)
            except Exception:
                logger.exception(
                    "Box-score stats resync failed for game %s", self.game_id
                )
        if self.on_update:
            self.on_update(self.state, data)

    def _refresh_live_html(self, *, allow_chrome: bool = True) -> BasketballLiveState:
        """Replace state from a current box-score page (Chrome/terms when needed).

        While Faye is already connected, pass ``allow_chrome=False``. Opening
        Chrome goes through homepage warm-up and can churn Akamai cookies until
        livestream.ncaa.org starts returning 403.
        """
        prior = self.state.to_live_snapshot() if self.state is not None else None
        url = self.client.live_box_score_url(self.game_id)
        soft = self.client.browser.fetch_document_without_chrome(url)
        if soft is not None:
            html = soft.text
        elif allow_chrome:
            html = self.client.get_live_box_score_html(self.game_id)
        else:
            raise RuntimeError(
                "Box-score page needs Chrome; skipped so the live Faye session stays up"
            )
        self.client.browser.pin_document_referer(url)
        self.state = bootstrap_live_state(html, self.game_id)
        self._from_saved_snapshot = False
        # Keep stream-only history the HTML page does not fully rebuild.
        if prior is not None:
            self.state.apply_live_snapshot(
                {
                    "game_id": prior.get("game_id"),
                    "plays": prior.get("plays") or {},
                    "shots": prior.get("shots") or [],
                }
            )
        self.save_live_snapshot()
        logger.info(
            "Refreshed game %s (%s) period=%s clock=%s score=%s-%s",
            self.game_id,
            self.state.sport_code,
            self.state.period,
            self.state.clock,
            self.state.away_score,
            self.state.home_score,
        )
        return self.state

    async def start(self) -> BasketballLiveState:
        if self.state is None:
            await asyncio.to_thread(self.bootstrap)
        used_snapshot = self._from_saved_snapshot

        if used_snapshot:
            # Disk HTML can be old. Prefer the live snapshot from the last quit
            # (NCAA Faye has no replay cursor). Refresh *before* Faye connects —
            # Chrome warm-up mid-stream churns Akamai cookies into livestream 403s.
            resumed = self._apply_saved_live_snapshot()
            needs_html = (not resumed) or (
                self.state is not None and self.state.player_box_stats_stale()
            )
            if needs_html:
                reason = (
                    "snapshot has score but zero player stats"
                    if resumed
                    else "no live snapshot"
                )
                # Soft always. Chrome only when there is nothing to resume — a
                # mid-resume Chrome pass would wipe the quit-time score/clock, and
                # Faye probe failure already opens Chrome when cookies are dead.
                allow_chrome = not resumed
                logger.info(
                    "Refreshing the box score page for game %s (%s, chrome=%s)",
                    self.game_id,
                    reason,
                    allow_chrome,
                )
                try:
                    await asyncio.to_thread(
                        self._refresh_live_html, allow_chrome=allow_chrome
                    )
                    self._stats_resync_attempted = True
                except Exception:
                    logger.exception(
                        "Box score refresh failed; keeping the saved page for game %s",
                        self.game_id,
                    )

        try:
            await self._open_faye(probe=used_snapshot)
        except Exception:
            if not used_snapshot:
                raise
            logger.info("Saved NCAA session was rejected; opening Chrome")
            await self._stop_faye()
            try:
                await asyncio.to_thread(lambda: self.bootstrap(challenge=True))
            except Exception:
                logger.exception(
                    "Chrome could not refresh game %s; retrying the saved page tokens",
                    self.game_id,
                )
                await asyncio.to_thread(self.bootstrap)
            await self._open_faye(probe=False)
            await self._wait_for_tipoff()
            self.save_live_snapshot()
            return self.state

        if used_snapshot and self.on_update and self.state is not None:
            self.on_update(self.state, {})
        self.save_live_snapshot()
        await self._wait_for_tipoff()
        return self.state

    def _apply_tipoff_html(self, html: str) -> bool:
        """Return True once roster tables are present (game initialized)."""
        if box_score_waiting_to_initialize(html):
            return False
        self.state = bootstrap_live_state(html, self.game_id)
        self._from_saved_snapshot = False
        self.save_live_snapshot()
        logger.info(
            "Game %s initialized; loaded box score (%s @ %s)",
            self.game_id,
            self.state.away.name if self.state.away else "?",
            self.state.home.name if self.state.home else "?",
        )
        return not self.state.awaiting_tipoff

    def _try_soft_tipoff_refresh(self) -> bool | None:
        """Soft poll for tip-off HTML.

        Returns True when initialized, False when still waiting, None when cookies/gate failed.
        """
        url = self.client.live_box_score_url(self.game_id)
        fresh = self.client.browser.fetch_document_without_chrome(url)
        if fresh is None:
            return None
        return self._apply_tipoff_html(fresh.text)

    def _chrome_tipoff_refresh(self) -> bool:
        """Open Chrome to renew NCAA access / load current pregame or live HTML."""
        logger.info(
            "Opening Chrome to renew NCAA access for game %s",
            self.game_id,
        )
        self._refresh_live_html()
        return bool(self.state is not None and not self.state.awaiting_tipoff)

    async def _wait_for_tipoff(self) -> None:
        """Stay on Faye until NCAA flips game_has_initialized / roster tables appear."""
        if self.state is None or not self.state.awaiting_tipoff:
            return

        away = self.state.away.name if self.state.away else "Away"
        home = self.state.home.name if self.state.home else "Home"
        logger.info(
            "Game %s is waiting to start (%s @ %s); connected and polling until tip-off",
            self.game_id,
            away,
            home,
        )

        ready = asyncio.Event()
        previous = self.on_update

        def _on_update(state: BasketballLiveState, data: dict[str, Any]) -> None:
            if not state.awaiting_tipoff or data.get("game_has_initialized") in (
                1,
                "1",
                True,
                "true",
                "True",
            ):
                ready.set()
            if previous:
                previous(state, data)

        self.on_update = _on_update

        async def _poll_box_score() -> bool:
            """True when the game has initialized and HTML was applied."""
            try:
                soft = await asyncio.to_thread(self._try_soft_tipoff_refresh)
            except Exception:
                logger.exception(
                    "Soft tip-off refresh failed for game %s; will keep waiting",
                    self.game_id,
                )
                soft = None
            if soft is True:
                return True
            if soft is False:
                return False
            # Cookies rejected / access gate — open Chrome, then keep waiting if needed.
            try:
                return await asyncio.to_thread(self._chrome_tipoff_refresh)
            except Exception:
                logger.exception(
                    "Chrome tip-off refresh failed for game %s; will keep waiting",
                    self.game_id,
                )
                return False

        try:
            # Don't wait a full poll interval before fixing a dead cookie session.
            if await _poll_box_score():
                pass
            while self.state is not None and self.state.awaiting_tipoff:
                try:
                    await asyncio.wait_for(ready.wait(), timeout=_TIPOFF_POLL_SECONDS)
                except asyncio.TimeoutError:
                    pass
                ready.clear()
                if self.state is not None and not self.state.awaiting_tipoff:
                    break
                if await _poll_box_score():
                    break

            # If Faye signaled tip-off before we soft-fetched HTML, load rosters now.
            if self.state is not None and not self.state.awaiting_tipoff:
                has_roster = bool(
                    self.state.away
                    and self.state.home
                    and (self.state.away.players or self.state.home.players)
                )
                if not has_roster:
                    try:
                        soft = await asyncio.to_thread(self._try_soft_tipoff_refresh)
                        if soft is not True:
                            await asyncio.to_thread(self._chrome_tipoff_refresh)
                    except Exception:
                        logger.exception(
                            "Post-tip-off box score refresh failed for game %s; keeping current state",
                            self.game_id,
                        )
                if self.on_update and self.state is not None:
                    self.on_update(self.state, {"game_has_initialized": 1})
        finally:
            self.on_update = previous

    async def stop(self) -> None:
        self.save_live_snapshot()
        await self._stop_faye()

    async def _open_faye(self, *, probe: bool) -> None:
        assert self.state is not None and self.state.auth is not None
        channels = [self.state.box_score_channel]
        # Scoreboard channel is optional; only subscribe when token present.
        if self.state.auth.token_for(self.state.scoreboard_channel):
            channels.append(self.state.scoreboard_channel)

        # Faye must look like it was opened from the box-score tab, not the homepage.
        self.client.browser.pin_document_referer(self.client.live_box_score_url(self.game_id))

        # Reuse the same Chrome session/cookies/referer as the box-score page.
        self._faye = FayeClient(
            auth=self.state.auth,
            browser=self.client.browser,
            reconnect=not probe,
        )
        if probe:
            # One subscribe attempt. After it succeeds, later drops reconnect.
            self._faye.promote_after_subscribe = True
        self._faye.on(self.state.box_score_channel, self._handle)
        if len(channels) > 1:
            self._faye.on(self.state.scoreboard_channel, self._handle)

        self._task = asyncio.create_task(self._faye.run(channels), name=f"faye-{self.game_id}")
        try:
            if probe:
                await self._faye.wait_subscribed()
            else:
                await self._faye.wait_connected()
        except Exception:
            await self._stop_faye()
            raise

    async def _stop_faye(self) -> None:
        faye, task = self._faye, self._task
        self._faye = None
        self._task = None
        if faye:
            faye.stop()
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
