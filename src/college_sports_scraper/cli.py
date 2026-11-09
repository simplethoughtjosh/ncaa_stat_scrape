"""Interactive / command-line entrypoints for college basketball live stats."""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
import threading
from datetime import date, datetime
from pathlib import Path

from college_sports_scraper.basketball.photo_fetch import ensure_team_photos
from college_sports_scraper.basketball.scoreboard_export import (
    ScoreboardExportError,
    export_scoreboard_for_state,
    export_scoreboard_pngs,
)
from college_sports_scraper.control.runtime import (
    PHOTO_REFRESH_SECONDS,
    LiveControlRuntime,
    OperatorConfig,
)
from college_sports_scraper.control.server import ControlServer
from college_sports_scraper.ncaa.client import NcaaClient
from college_sports_scraper.ncaa.live_state import BasketballLiveState, LivePlayer, LiveTeam
from college_sports_scraper.ncaa.session import BasketballLiveSession
from college_sports_scraper.ncaa.team_season import enrich_team_season_stats
from college_sports_scraper.newblue.client import CaptivateConnectionError
from college_sports_scraper.newblue.config import load_newblue_config
from college_sports_scraper.newblue.controller import BasketballGraphicsController


def _today_ncaa() -> str:
    return date.today().strftime("%m/%d/%Y")


class LiveStatusLine:
    """One terminal line that is rewritten as live messages arrive."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._parked = False

    def park(self, text: str) -> None:
        """Put the status on its own line, directly above the next prompt."""
        with self._lock:
            sys.stdout.write(_fit_terminal(text) + "\n")
            sys.stdout.flush()
            self._parked = True

    def redraw(self, text: str) -> None:
        """Replace the parked status line. Does not add another line."""
        with self._lock:
            text = _fit_terminal(text)
            if not self._parked:
                sys.stdout.write(text + "\n")
                sys.stdout.flush()
                self._parked = True
                return
            # Save the cursor (on the prompt), rewrite the line above, then restore.
            sys.stdout.write(f"\x1b7\x1b[1A\r{text}\x1b[K\x1b8")
            sys.stdout.flush()

    def suspend(self) -> None:
        """A command is about to print. The next redraw starts a fresh line."""
        with self._lock:
            self._parked = False


def _fit_terminal(text: str) -> str:
    width = shutil.get_terminal_size(fallback=(100, 24)).columns
    text = " ".join(text.split())
    if width > 1 and len(text) >= width:
        return text[: width - 1]
    return text


def format_live_status(state: BasketballLiveState, updates: int) -> str:
    """One line: when the last message arrived, the score, and how many messages."""
    away = state.away.name if state.away else "Away"
    home = state.home.name if state.home else "Home"
    when = datetime.now().strftime("%H:%M:%S")
    return (
        f"Live {when}  {away} {state.away_score or '–'}–{state.home_score or '–'} {home}  "
        f"{state.period or ''} {state.clock or ''}  updates {updates}"
    ).rstrip()


def _print_on_court(state: BasketballLiveState) -> None:
    assert state.away and state.home
    print(f"\n{state.away.name} (away) on court:")
    for p in state.away.on_court():
        pts = p.stats.get("PTS", "")
        print(f"  #{p.number or '':<3} {p.name or '':<24} {p.position or '':<2}  PTS={pts}")
    print(f"\n{state.home.name} (home) on court:")
    for p in state.home.on_court():
        pts = p.stats.get("PTS", "")
        print(f"  #{p.number or '':<3} {p.name or '':<24} {p.position or '':<2}  PTS={pts}")
    print(
        f"\nScore {state.away_score or '-'}–{state.home_score or '-'}  "
        f"{state.period or ''} {state.clock or ''}  [{state.sport_code}]".rstrip()
    )


def _print_roster(team: LiveTeam, label: str) -> list[LivePlayer]:
    roster = [p for p in team.roster() if p.name]
    print(f"\n{label} — {team.name}:")
    for i, p in enumerate(roster, 1):
        mark = "*" if p.on_court else " "
        print(f"  {i:2}.{mark} #{p.number or '':<3} {p.name or '':<24} {p.position or '':<2}  PTS={p.stats.get('PTS', 0)}")
    return roster


def _pick_player(state: BasketballLiveState) -> tuple[LiveTeam, LivePlayer] | None:
    assert state.away and state.home
    side = input("Team [a]way / [h]ome: ").strip().lower() or "h"
    team = state.away if side.startswith("a") else state.home
    roster = _print_roster(team, "away" if team is state.away else "home")
    if not roster:
        print("No players")
        return None
    choice = input("Player number: ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(roster)):
        print("Invalid player")
        return None
    return team, roster[int(choice) - 1]


def _run_scoreboard_export(
    state: BasketballLiveState,
    *,
    home_color: str | None = None,
    away_color: str | None = None,
) -> dict[str, str]:
    print("Generating scoreboard team PNGs via SSC Scoreboard Automation …")
    result = export_scoreboard_for_state(
        state,
        home_color=home_color,
        away_color=away_color,
    )
    home_note = "existing" if result.get("home_skipped") == "1" else result["home_color"]
    away_note = "existing" if result.get("away_skipped") == "1" else result["away_color"]
    print(f"Home scoreboard: {result['home']} ({home_note}) → {result['home_png']}")
    print(f"Away scoreboard: {result['away']} ({away_note}) → {result['away_png']}")
    return result


async def _control_loop(
    game_id: str,
    *,
    html_path: str | None,
    config_path: str | None,
    listen_seconds: float | None,
    control_port: int = 8765,
    export_scoreboard: bool = True,
    require_scoreboard: bool = False,
    home_color: str | None = None,
    away_color: str | None = None,
) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_newblue_config(config_path)
    graphics = BasketballGraphicsController(cfg)
    runtime = LiveControlRuntime(
        graphics,
        OperatorConfig(control_port=control_port, shot_map_dir="output/shot_maps"),
    )
    live_status = LiveStatusLine()
    live_updates = {"n": 0}

    def on_update(state: BasketballLiveState, data: dict) -> None:
        live_updates["n"] += 1
        runtime.on_live_update(state, data)
        live_status.redraw(format_live_status(state, live_updates["n"]))

    session = BasketballLiveSession(game_id=game_id, on_update=on_update)

    html = Path(html_path).read_text() if html_path else None
    if html is not None:
        session.bootstrap(html=html)
    else:
        session.bootstrap()

    assert session.state is not None
    print(f"\nNewBlue transport={cfg.transport} action={cfg.default_action}")
    print(f"Control HTTP http://127.0.0.1:{control_port}  (Captivate panel polls this)")
    print(f"Sport auto-detected: {session.state.sport_code}")
    if session.state.awaiting_tipoff:
        away = session.state.away.name if session.state.away else "Away"
        home = session.state.home.name if session.state.home else "Home"
        print(f"Game has not started yet ({away} @ {home}); waiting for tip-off …")
    print(f"Connecting Faye {session.state.box_score_channel} …")
    # Connect (and refresh a saved page) before the first Titler push so the
    # controller does not briefly show an earlier period/clock from disk.
    await session.start()
    if session.state and not session.state.awaiting_tipoff:
        away = session.state.away.name if session.state.away else "Away"
        home = session.state.home.name if session.state.home else "Home"
        print(f"Live: {away} @ {home}")

    # Control HTTP + Faye must stay responsive while photos download. A sync
    # ensure_team_photos() call previously froze the event loop and dropped
    # early-game auto-subs (e.g. ~7:16) that arrived during the fetch.
    server = ControlServer(runtime, lambda: session.state, port=control_port)
    server.start()

    async def _watch_photos() -> None:
        while True:
            await asyncio.sleep(PHOTO_REFRESH_SECONDS)
            state = session.state
            if state is None:
                continue
            try:
                runtime.refresh_missing_photos(state)
            except Exception:
                logging.exception("Photo refresh failed")

    async def _watch_subs() -> None:
        # Keep flushing while input() blocks, so a quiet sub batch still airs.
        while True:
            await asyncio.sleep(0.25)
            state = session.state
            if state is None:
                continue
            try:
                runtime._flush_subs(state)
                runtime._push_fast_diffs(state)
            except Exception:
                logging.exception("Substitution flush failed")

    async def _watch_live_snapshot() -> None:
        # Keep quit/crash resume close to wall clock without a Faye replay cursor.
        while True:
            await asyncio.sleep(15)
            try:
                session.save_live_snapshot()
            except Exception:
                logging.exception("Live snapshot save failed")

    # Flush auto-subs during the photo wait — Faye is already live.
    sub_task = asyncio.create_task(_watch_subs())
    snapshot_task = asyncio.create_task(_watch_live_snapshot())

    # Photos + team/coach season pages off the event loop (Faye stays live).
    photo_task = asyncio.create_task(
        asyncio.to_thread(ensure_team_photos, session.state)
    )
    season_task = asyncio.create_task(
        asyncio.to_thread(enrich_team_season_stats, session.client, session.state)
    )
    try:
        await photo_task
    except Exception:
        logging.exception("Team photo fetch failed")
    try:
        await season_task
    except Exception:
        logging.exception("Team/coach season stats enrich failed")

    if export_scoreboard:
        try:
            await asyncio.to_thread(
                _run_scoreboard_export,
                session.state,
                home_color=home_color,
                away_color=away_color,
            )
        except ScoreboardExportError as exc:
            logging.error("%s", exc)
            print(f"Scoreboard export failed: {exc}", file=sys.stderr)
            if require_scoreboard:
                server.stop()
                await session.stop()
                graphics.close()
                for task in (sub_task, snapshot_task):
                    task.cancel()
                return 1
        except Exception:
            logging.exception("Scoreboard export crashed")
            print("Scoreboard export crashed; see log above", file=sys.stderr)
            if require_scoreboard:
                server.stop()
                await session.stop()
                graphics.close()
                for task in (sub_task, snapshot_task):
                    task.cancel()
                return 1

    # Titler may still be closed. Live stats continue; graphics catch up when it opens.
    runtime._try_push(lambda: graphics.push_sport_config(session.state, action="still"))
    runtime._try_push(lambda: graphics.push_game_summary(session.state, action="still"))
    runtime._try_push(lambda: graphics.push_both_on_court(session.state, action="still"))
    runtime.remember_on_court_photos(session.state)
    # Box-score HTML already has every make/miss as addShot(...); rebuild now so a
    # mid-game restart does not wait for the next basket.
    runtime.rebuild_shot_maps(session.state)
    made = sum(1 for s in session.state.shots if s.get("made"))
    if made:
        print(f"Shot maps rebuilt from page ({made} makes / {len(session.state.shots)} attempts)")
    _print_on_court(session.state)

    print(
        "\nCommands: status | away | home | both | player H21 | ft | summary | "
        "foul | leaders | sub [pairs] | scoreboard | listen [sec] | quit"
    )
    live_status.park(format_live_status(session.state, live_updates["n"]))

    photo_task = asyncio.create_task(_watch_photos())
    try:
        while True:
            await asyncio.sleep(0.05)
            try:
                line = await asyncio.to_thread(input, "> ")
            except EOFError:
                break
            cmd = (line or "").strip()
            if not cmd:
                continue
            low = cmd.lower()
            state = session.state
            assert state and state.away and state.home

            live_status.suspend()
            if low in {"q", "quit", "exit"}:
                break
            if low in {"status", "s"}:
                _print_on_court(state)
            elif low == "away":
                if runtime._try_push(lambda: graphics.push_away_on_court(state)):
                    print("Pushed away on-court")
                else:
                    print("Captivate is closed; away on-court will send when Titler is open")
            elif low == "home":
                if runtime._try_push(lambda: graphics.push_home_on_court(state)):
                    print("Pushed home on-court")
                else:
                    print("Captivate is closed; home on-court will send when Titler is open")
            elif low == "both":
                if runtime._try_push(lambda: graphics.push_both_on_court(state)):
                    runtime.remember_on_court_photos(state)
                    print("Pushed both on-court")
                else:
                    print("Captivate is closed; both on-court will send when Titler is open")
            elif low.startswith("player ") or low.startswith("p "):
                code = cmd.split(None, 1)[1]
                try:
                    result = runtime.cue_player(state, code)
                except CaptivateConnectionError:
                    print("Captivate is closed; player cue kept for when Titler is open")
                else:
                    print(result)
            elif low == "ft":
                picked = _pick_player(state)
                if picked:
                    team, player = picked
                    side = "H" if team is state.home else "A"
                    try:
                        result = runtime.cue_player(state, f"{side}{player.number or ''}")
                    except CaptivateConnectionError:
                        print("Captivate is closed; player cue kept for when Titler is open")
                    else:
                        print(result)
            elif low in {"summary", "sum"}:
                if runtime._try_push(lambda: graphics.push_game_summary(state)):
                    print("Pushed game summary")
                else:
                    print("Captivate is closed; summary will send when Titler is open")
            elif low == "foul":
                try:
                    print(runtime.push_foul_take(state))
                except CaptivateConnectionError:
                    print("Captivate is closed; foul board will send when Titler is open")
            elif low in {"scoreboard", "sb"}:
                try:
                    await asyncio.to_thread(
                        _run_scoreboard_export,
                        state,
                        home_color=home_color,
                        away_color=away_color,
                    )
                except ScoreboardExportError as exc:
                    print(f"Scoreboard export failed: {exc}")
            elif low == "leaders":
                try:
                    print(runtime.push_leaders_take(state))
                except CaptivateConnectionError:
                    print("Captivate is closed; leaders will send when Titler is open")
            elif low == "sub" or low.startswith("sub "):
                parts = low.split()
                pairs = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
                result = runtime.simulate_substitution(state, pair_count=pairs)
                if result.get("ok"):
                    print(
                        f"Simulated {result['side']} {result['team']} @ {result['clock']}: "
                        + " | ".join(f"OUT {p['out']} / IN {p['in']}" for p in result["pairs"])
                    )
                else:
                    print(result)
            elif low.startswith("listen"):
                parts = low.split()
                secs = float(parts[1]) if len(parts) > 1 else (listen_seconds or 20.0)
                print(f"Listening {secs}s …")
                await asyncio.sleep(secs)
                _print_on_court(state)
            else:
                print("Unknown command")
            live_status.park(format_live_status(state, live_updates["n"]))
    finally:
        for task in (photo_task, sub_task, snapshot_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        server.stop()
        await session.stop()
        graphics.close()
    return 0


async def _listen_live(game_id: str, seconds: float, html_path: str | None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    updates = {"n": 0}

    def on_update(state, data):
        updates["n"] += 1
        keys = [
            k
            for k in data.keys()
            if not k.startswith("message")
            and not k.startswith("failure")
            and not k.startswith("last_")
        ]
        print(
            f"[update {updates['n']}] keys={keys}  "
            f"score={state.away_score}-{state.home_score}  "
            f"{state.period} {state.clock}"
        )
        if "box_score" in data:
            _print_on_court(state)

    session = BasketballLiveSession(game_id=game_id, on_update=on_update)
    html = Path(html_path).read_text() if html_path else None
    if html is not None:
        session.bootstrap(html=html)
    else:
        session.bootstrap()

    _print_on_court(session.state)
    print(f"\nConnecting to Faye channel {session.state.box_score_channel} …")
    await session.start()
    try:
        await asyncio.sleep(seconds)
    finally:
        await session.stop()
    print(f"Done. Received {updates['n']} live messages.")
    return 0


def _cmd_find_games(args: argparse.Namespace) -> int:
    client = NcaaClient()
    games = client.find_games_for_team(
        team_query=args.team,
        date=args.date,
        sports_code=args.sport,
        division=args.division,
    )
    if not games:
        print(f"No games matched team={args.team!r} on {args.date} ({args.sport}).")
        return 1
    for i, game in enumerate(games, 1):
        print(f"{i}. {game.label}")
    return 0


def _select_game_interactive(args: argparse.Namespace) -> str | None:
    sport = input("Sport [WBB/MBB] (default WBB): ").strip().upper() or "WBB"
    if sport not in {"WBB", "MBB"}:
        print("Sport must be WBB or MBB")
        return None
    game_date = input(f"Date MM/DD/YYYY (default {_today_ncaa()}): ").strip() or _today_ncaa()
    team = input("Team name to search for: ").strip()
    if not team:
        print("Team is required")
        return None

    client = NcaaClient()
    print(f"Searching {sport} games on {game_date} for {team!r}…")
    try:
        games = client.find_games_for_team(team, game_date, sports_code=sport, division=args.division)
    except Exception as exc:
        print(f"Scoreboard fetch failed ({exc}). You can still paste a live game id.")
        games = []

    if games:
        for i, game in enumerate(games, 1):
            print(f"{i}. {game.label}")
        choice = input("Select game number (or paste game id): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(games):
            return games[int(choice) - 1].game_id
        if choice.isdigit():
            return choice
        return None
    return input("Live game id: ").strip() or None


def _interactive(args: argparse.Namespace) -> int:
    game_id = _select_game_interactive(args)
    if not game_id:
        print("No game selected")
        return 1
    mode = input("Mode [c]ontrol graphics / [l]isten only (default c): ").strip().lower() or "c"
    if mode.startswith("l"):
        seconds = float(input("Listen seconds [30]: ").strip() or "30")
        return asyncio.run(_listen_live(game_id, seconds, html_path=None))
    return asyncio.run(
        _control_loop(
            game_id,
            html_path=None,
            config_path=getattr(args, "config", None),
            listen_seconds=30.0,
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="College basketball stats + NewBlue controller")
    parser.add_argument("--division", type=int, default=1)
    parser.add_argument(
        "--config",
        default=None,
        help="Path to newblue_titles.yaml (default: config/newblue_titles.yaml)",
    )
    sub = parser.add_subparsers(dest="command")

    find_p = sub.add_parser("find-games", help="Find games for a team on a date")
    find_p.add_argument("--team", required=True)
    find_p.add_argument("--date", default=_today_ncaa())
    find_p.add_argument("--sport", choices=["WBB", "MBB"], default="WBB")

    live_p = sub.add_parser("listen", help="Bootstrap once, then stream Faye live updates")
    live_p.add_argument("--game-id", required=True)
    live_p.add_argument("--seconds", type=float, default=30.0)
    live_p.add_argument("--html", help="Optional local box-score HTML for bootstrap")

    ctrl_p = sub.add_parser("control", help="Live Faye + Captivate pushes + control HTTP")
    ctrl_p.add_argument("--game-id", required=True)
    ctrl_p.add_argument("--html", help="Optional local box-score HTML for bootstrap")
    ctrl_p.add_argument("--config", default=None, help="NewBlue YAML config path")
    ctrl_p.add_argument("--port", type=int, default=8765, help="Local control HTTP port")
    ctrl_p.add_argument(
        "--skip-scoreboard",
        action="store_true",
        help="Do not call SSC Scoreboard Automation for home/away team PNGs",
    )
    ctrl_p.add_argument(
        "--require-scoreboard",
        action="store_true",
        help="Exit if Photoshop scoreboard export fails",
    )
    ctrl_p.add_argument("--home-color", default=None, help="Override home hex color, e.g. #C8102E")
    ctrl_p.add_argument("--away-color", default=None, help="Override away hex color, e.g. #1A3A6B")

    sb_p = sub.add_parser(
        "scoreboard",
        help="Generate home/away scoreboard PNGs via SSC Scoreboard Automation",
    )
    sb_p.add_argument("--home", required=True, help="Home team display name")
    sb_p.add_argument("--home-color", required=True, help="Home hex color, e.g. #C8102E")
    sb_p.add_argument("--away", required=True, help="Away team display name")
    sb_p.add_argument("--away-color", required=True, help="Away hex color, e.g. #1A3A6B")
    sb_p.add_argument(
        "--no-trim",
        action="store_true",
        help="Export full 1920×1080 canvas without cropping",
    )

    sub.add_parser("interactive", help="Ask for team/game then control or listen")

    args = parser.parse_args(argv)
    if args.command == "find-games":
        return _cmd_find_games(args)
    if args.command == "listen":
        return asyncio.run(_listen_live(args.game_id, args.seconds, args.html))
    if args.command == "control":
        return asyncio.run(
            _control_loop(
                args.game_id,
                html_path=args.html,
                config_path=args.config or getattr(args, "config", None),
                listen_seconds=30.0,
                control_port=args.port,
                export_scoreboard=not args.skip_scoreboard,
                require_scoreboard=args.require_scoreboard,
                home_color=args.home_color,
                away_color=args.away_color,
            )
        )
    if args.command == "scoreboard":
        try:
            home_png, away_png = export_scoreboard_pngs(
                home=args.home,
                home_color=args.home_color,
                away=args.away,
                away_color=args.away_color,
                trim=not args.no_trim,
            )
        except ScoreboardExportError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"Home PNG: {home_png}")
        print(f"Away PNG: {away_png}")
        return 0
    if args.command == "interactive" or args.command is None:
        return _interactive(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
