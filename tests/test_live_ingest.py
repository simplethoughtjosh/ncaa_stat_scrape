"""Requirements tests for live Faye-shaped play/shot ingestion and auto cues."""

from pathlib import Path

from college_sports_scraper.control.runtime import LiveControlRuntime, OperatorConfig
from college_sports_scraper.ncaa.live_state import bootstrap_live_state
from college_sports_scraper.newblue.config import load_newblue_config
from college_sports_scraper.newblue.controller import BasketballGraphicsController

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "wbb_live_box_score_6656891.html"
CFG = Path(__file__).resolve().parents[1] / "config" / "newblue_titles.example.yaml"


def _runtime(capsys=None):
    cfg = load_newblue_config(CFG)
    graphics = BasketballGraphicsController(cfg)
    return LiveControlRuntime(graphics, OperatorConfig(automatic_subs=False, automatic_timeouts=False))


def test_ingest_play_text_and_shot_chart_from_faye_message():
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    assert state.away and state.away.organization_id == "2759"
    assert state.home and state.home.organization_id == "133"
    state.apply_message(
        state.box_score_channel,
        {
            "play_text_9001": "Free throw 1 of 2 made by Madyn Kassatly(WCU)",
            "period_9001": "2",
            "clock_9001": "04:00",
            "shot_chart": {
                "x": 10.5,
                "y": 50.0,
                "color": state.away.organization_id,
                "made": True,
                "play_id": "shot9001",
                "description": "made",
            },
        },
    )
    assert "9001" in state.plays
    assert state.plays["9001"].text.startswith("Free throw")
    assert state.plays["9001"].clock == "04:00"
    assert "9001" in state.new_play_ids
    assert len(state.new_shots) == 1
    assert state.shots[-1]["made"] is True
    assert state.shots[-1]["play_id"] == "shot9001"
    assert state.shots[-1]["org_id"] == "2759"


def test_bootstrap_seeds_embedded_shots_and_rebuilds_maps_on_restart(tmp_path, capsys):
    """NCAA HTML includes addShot(...) for the whole game; restart must not wait for a new make."""
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    assert len(state.shots) > 10
    assert any(s.get("made") for s in state.shots)
    assert state.away and state.home
    # Faye uses school org ids; embedded shots must match logo org ids.
    orgs = {s["org_id"] for s in state.shots}
    assert state.away.organization_id in orgs
    assert state.home.organization_id in orgs

    runtime = _runtime()
    runtime.config.shot_map_dir = str(tmp_path)
    pushed: list[dict[str, str]] = []
    original = runtime.graphics.push_shot_maps

    def capture(logical, action=None):
        pushed.append(dict(logical))
        return original(logical, action=action)

    runtime.graphics.push_shot_maps = capture  # type: ignore[method-assign]
    assert runtime.rebuild_shot_maps(state) is True
    away = tmp_path / "away_shot_map.png"
    home = tmp_path / "home_shot_map.png"
    assert away.exists() and home.exists()
    assert pushed and "away_shot_map_" in Path(pushed[0]["away_shot_map"]).name
    assert "home_shot_map_" in Path(pushed[0]["home_shot_map"]).name
    first_paths = (pushed[0]["away_shot_map"], pushed[0]["home_shot_map"])
    assert runtime.rebuild_shot_maps(state) is True
    assert (pushed[1]["away_shot_map"], pushed[1]["home_shot_map"]) != first_paths
    assert len(list(tmp_path.glob("away_shot_map_*.png"))) == 1
    assert len(list(tmp_path.glob("home_shot_map_*.png"))) == 1
    from PIL import Image

    blue = (70, 130, 180)
    with Image.open(away) as a, Image.open(home) as h:
        assert blue in set(a.get_flattened_data())
        assert blue in set(h.get_flattened_data())


def test_shot_maps_use_organization_id_not_competitor_id(tmp_path, capsys):
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    runtime = _runtime()
    runtime.config.shot_map_dir = str(tmp_path)
    assert state.away and state.home
    assert state.away.organization_id != state.away.competitor_id

    state.apply_message(
        state.box_score_channel,
        {
            "shot_chart": {
                "x": 12.0,
                "y": 48.0,
                "color": state.away.organization_id,
                "made": True,
                "play_id": "a1",
            }
        },
    )
    runtime.on_live_update(state, state.raw_last_message)
    state.apply_message(
        state.box_score_channel,
        {
            "shot_chart": {
                "x": 88.0,
                "y": 52.0,
                "color": state.home.organization_id,
                "made": True,
                "play_id": "h1",
            }
        },
    )
    runtime.on_live_update(state, state.raw_last_message)

    from PIL import Image

    away = tmp_path / "away_shot_map.png"
    home = tmp_path / "home_shot_map.png"
    assert away.exists() and home.exists()
    with Image.open(away) as a, Image.open(home) as h:
        away_px = set(a.get_flattened_data())
        home_px = set(h.get_flattened_data())
    # Both maps use the same blue dots; org_id still keeps each team's makes apart.
    blue = (70, 130, 180)
    assert blue in away_px
    assert blue in home_px
    away_blues = sum(1 for p in away_px if p == blue)
    home_blues = sum(1 for p in home_px if p == blue)
    assert away_blues > 0 and home_blues > 0


def test_shot_maps_infer_org_when_logo_missing(tmp_path, capsys):
    """Teams without a logo still get makes when shot colors identify the school."""
    from college_sports_scraper.ncaa.live_state import BasketballLiveState, LiveTeam

    away = LiveTeam(competitor_id="8667912", name="Albany Pharmacy", logo_url=None, venue="away")
    home = LiveTeam(
        competitor_id="8667913",
        name="Bard",
        logo_url="https://web2.ncaa.org/ncaa_style/img/All_Logos/sm//962.gif",
        org_id="962",
        venue="home",
    )
    state = BasketballLiveState(
        game_id="6642654",
        sport_code="WBB",
        away=away,
        home=home,
        shots=[
            {"x": 10.0, "y": 50.0, "org_id": "500075", "made": True, "play_id": "a1"},
            {"x": 12.0, "y": 48.0, "org_id": "500075", "made": True, "play_id": "a2"},
            {"x": 90.0, "y": 50.0, "org_id": "962", "made": True, "play_id": "h1"},
        ],
    )
    assert away.organization_id is None

    runtime = _runtime()
    runtime.config.shot_map_dir = str(tmp_path)
    assert runtime.rebuild_shot_maps(state) is True
    assert away.organization_id == "500075"

    from PIL import Image

    blue = (70, 130, 180)
    with Image.open(tmp_path / "away_shot_map.png") as a, Image.open(
        tmp_path / "home_shot_map.png"
    ) as h:
        assert sum(1 for p in a.get_flattened_data() if p == blue) > 0
        assert sum(1 for p in h.get_flattened_data() if p == blue) > 0


def test_runtime_auto_fills_player_l3_on_free_throw_play(capsys):
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    runtime = _runtime()
    # Find a real roster name to match FT parser
    assert state.away
    shooter = next(p for p in state.away.roster() if p.name)
    shooter.stats = {"FTM": 0, "FTA": 1, "PTS": 8, "FGM": 3, "FGA": 7}
    state.apply_message(
        state.box_score_channel,
        {
            "play_text_9100": f"Free throw 2 of 2 made by {shooter.name}(WCU)",
            "period_9100": "3",
            "clock_9100": "01:00",
        },
    )
    runtime.on_live_update(state, state.raw_last_message)
    assert runtime.last_player.get("player_name") == shooter.name
    assert runtime.last_player.get("player_ft_attempt") == "2 of 2"
    # Play text arrives before box: optimistic 1-2 / PTS+1 without mutating roster.
    assert runtime.last_player.get("player_ft") == "1-2"
    assert runtime.last_player.get("player_pts") == "9"
    assert shooter.stats.get("FTM") == 0
    assert shooter.stats.get("FTA") == 1
    assert runtime._last_ft_cue is not None
    out = capsys.readouterr().out
    assert "[newblue dry-run]" in out


def test_runtime_reconciles_ft_l3_when_box_ft_stats_catch_up(capsys):
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    runtime = _runtime()
    assert state.away
    shooter = next(p for p in state.away.roster() if p.name)
    shooter.stats = {"FTM": 0, "FTA": 1, "PTS": 8}

    state.apply_message(
        state.box_score_channel,
        {
            "play_text_9101": f"Free throw 2 of 2 made by {shooter.name}(WCU)",
            "period_9101": "3",
            "clock_9101": "01:00",
        },
    )
    runtime.on_live_update(state, state.raw_last_message)
    assert runtime.last_player.get("player_ft") == "1-2"
    assert runtime.last_player.get("player_ft_attempt") == "2 of 2"

    # Box score catches up to the same totals the optimistic overlay showed.
    shooter.stats = {"FTM": 1, "FTA": 2, "PTS": 9}
    state.new_play_ids.clear()
    runtime.on_live_update(state, {})
    assert runtime.last_player.get("player_name") == shooter.name
    assert runtime.last_player.get("player_ft") == "1-2"
    assert runtime.last_player.get("player_pts") == "9"
    assert runtime.last_player.get("player_ft_attempt") == "2 of 2"
    assert runtime._last_ft_box_sig == "1|2|9"
    out = capsys.readouterr().out
    assert "[newblue dry-run]" in out


def test_substitution_lines_update_who_is_on_court(capsys):
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    runtime = _runtime()
    assert state.home
    weaver = next(p for p in state.home.roster() if p.name == "Maddy Weaver")
    schuetze = next(p for p in state.home.roster() if p.name == "Alyssa Schuetze")
    assert weaver.on_court is True
    assert schuetze.on_court is False
    before = {p.player_id for p in state.home.on_court()}

    for i, text in enumerate(
        [
            "Substitution CUH: out Maddy Weaver",
            "Substitution CUH: in Alyssa Schuetze",
        ],
        start=9600,
    ):
        state.apply_message(
            state.box_score_channel,
            {"play_text_" + str(i): text, "period_" + str(i): "2", "clock_" + str(i): "10:00"},
        )
        runtime.on_live_update(state, state.raw_last_message)

    assert weaver.on_court is False
    assert schuetze.on_court is True
    after = {p.player_id for p in state.home.on_court()}
    assert weaver.player_id not in after
    assert schuetze.player_id in after
    assert after != before
    assert runtime._last_on_court_sig


def test_full_team_name_sub_line_updates_on_court():
    from college_sports_scraper.basketball.events import SubLine
    from college_sports_scraper.basketball.graphics import apply_substitution_line
    from college_sports_scraper.ncaa.live_state import BasketballLiveState, LivePlayer, LiveTeam

    away = LiveTeam(competitor_id="a", name="Westminster (UT)", venue="away")
    away.players["1"] = LivePlayer(
        player_id="1", competitor_id="a", name="Ellie Mitchell", number="1", on_court=True
    )
    away.players["2"] = LivePlayer(
        player_id="2", competitor_id="a", name="Nisa Ellis", number="23", on_court=False
    )
    state = BasketballLiveState(game_id="1", sport_code="WBB", away=away)
    assert apply_substitution_line(
        state, SubLine("Westminster (UT)", "out", "Ellie Mitchell", "")
    )
    assert apply_substitution_line(state, SubLine("Westminster (UT)", "in", "Nisa Ellis", ""))
    assert away.players["1"].on_court is False
    assert away.players["2"].on_court is True
    assert [p.name for p in away.on_court()] == ["Nisa Ellis"]


def test_play_text_arriving_after_clock_still_auto_airs_sub(capsys):
    """NCAA often publishes period/clock before play_text on the same action id."""
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    runtime = _runtime()
    runtime.config.automatic_subs = True
    runtime._sub_batcher.quiet_seconds = 0.01

    state.apply_message(
        state.box_score_channel,
        {"period_8801": "2", "clock_8801": "00:50.40"},
    )
    runtime.on_live_update(state, state.raw_last_message)
    assert runtime.last_sub == {}

    state.apply_message(
        state.box_score_channel,
        {
            "play_text_8800": "Substitution CUH: out Maddy Weaver",
            "period_8800": "2",
            "clock_8800": "00:50.40",
            "play_text_8801": "Substitution CUH: in Alyssa Schuetze",
            "period_8801": "2",
            "clock_8801": "00:50.40",
        },
    )
    assert "8800" in state.new_play_ids and "8801" in state.new_play_ids
    runtime.on_live_update(state, state.raw_last_message)

    import time

    time.sleep(0.05)
    runtime._flush_subs(state)
    assert runtime.last_sub.get("sub_in") == "Alyssa Schuetze"
    actions = runtime.pop_auto_actions()
    assert any(a["kind"] == "sub" and a["pairs"][0]["sub_in"] == "Alyssa Schuetze" for a in actions)


def test_nested_play_N_envelope_still_ingests_and_auto_airs_sub(capsys):
    """Live Faye wraps each action as play_471: {play_text_471, clock_471, …}."""
    import time

    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    runtime = _runtime()
    runtime.config.automatic_subs = True
    runtime._sub_batcher.quiet_seconds = 0.01

    state.apply_message(
        state.box_score_channel,
        {
            "play_8819": {
                "play_text_8819": "Substitution CUH: out Maddy Weaver",
                "period_8819": "1",
                "clock_8819": "04:47",
            },
            "play_8820": {
                "play_text_8820": "Substitution CUH: in Alyssa Schuetze",
                "period_8820": "1",
                "clock_8820": "04:47",
            },
        },
    )
    assert "8820" in state.new_play_ids
    assert state.plays["8820"].text.startswith("Substitution")
    assert state.plays["8820"].clock == "04:47"
    runtime.on_live_update(state, state.raw_last_message)
    time.sleep(0.05)
    runtime._flush_subs(state)
    assert runtime.last_sub.get("sub_in") == "Alyssa Schuetze"
    actions = runtime.pop_auto_actions()
    assert any(a["kind"] == "sub" for a in actions)


def test_clock_arriving_after_play_text_still_auto_airs_sub(capsys):
    """Text-before-clock must refresh the open batch so auto-subs are not stuck on period opening."""
    import time

    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    runtime = _runtime()
    runtime.config.automatic_subs = True
    runtime._sub_batcher.quiet_seconds = 0.01
    state.period = "1"
    state.clock = "10:00"

    state.apply_message(
        state.box_score_channel,
        {
            "play_text_8802": "Substitution CUH: out Maddy Weaver",
            "play_text_8803": "Substitution CUH: in Alyssa Schuetze",
        },
    )
    assert "8802" in state.new_play_ids and "8803" in state.new_play_ids
    runtime.on_live_update(state, state.raw_last_message)
    # Keep the same-team pair open until real clocks arrive (do not flush yet).
    assert runtime.pop_auto_actions() == []

    state.apply_message(
        state.box_score_channel,
        {
            "period_8802": "1",
            "clock_8802": "07:41",
            "period_8803": "1",
            "clock_8803": "07:41",
        },
    )
    assert "8802" in state.new_play_ids and "8803" in state.new_play_ids
    runtime.on_live_update(state, state.raw_last_message)
    time.sleep(0.05)
    runtime._flush_subs(state)
    actions = runtime.pop_auto_actions()
    assert any(a["kind"] == "sub" and a["pairs"][0]["sub_in"] == "Alyssa Schuetze" for a in actions)


def test_clock_after_text_does_not_double_queue_timeout(capsys):
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    runtime = _runtime()
    runtime.config.automatic_timeouts = True

    state.apply_message(
        state.box_score_channel,
        {"play_text_8803": "Timeout short WCU"},
    )
    runtime.on_live_update(state, state.raw_last_message)
    assert runtime.last_timeout.get("timeout_team_abbr") == "WCU"
    assert len([a for a in runtime.pop_auto_actions() if a["kind"] == "timeout"]) == 1

    state.apply_message(
        state.box_score_channel,
        {"period_8803": "1", "clock_8803": "05:21"},
    )
    assert "8803" in state.new_play_ids
    runtime.on_live_update(state, state.raw_last_message)
    assert runtime.pop_auto_actions() == []


def test_multiple_free_throws_in_one_tick_keep_distinct_payloads(capsys):
    """Lambda closures must bind each FT payload, not the last loop variable."""
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    runtime = _runtime()
    assert state.away and state.home
    away_shooter = next(p for p in state.away.roster() if p.name)
    home_shooter = next(p for p in state.home.roster() if p.name)
    pushed: list[dict] = []
    original = runtime.graphics.push_player_l3

    def capture(payload, *args, **kwargs):
        pushed.append(dict(payload))
        return original(payload, *args, **kwargs)

    runtime.graphics.push_player_l3 = capture  # type: ignore[method-assign]
    state.apply_message(
        state.box_score_channel,
        {
            "play_text_8810": f"Free throw 1 of 2 made by {away_shooter.name}(WCU)",
            "period_8810": "2",
            "clock_8810": "04:00",
            "play_text_8811": f"Free throw 2 of 2 made by {home_shooter.name}(CUH)",
            "period_8811": "2",
            "clock_8811": "03:58",
        },
    )
    assert state.new_play_ids == ["8810", "8811"] or set(state.new_play_ids) >= {"8810", "8811"}
    runtime.on_live_update(state, state.raw_last_message)
    assert len(pushed) == 2
    assert pushed[0]["player_name"] == away_shooter.name
    assert pushed[0]["player_ft_attempt"] == "1 of 2"
    assert pushed[1]["player_name"] == home_shooter.name
    assert pushed[1]["player_ft_attempt"] == "2 of 2"
    assert runtime.last_player.get("player_name") == home_shooter.name


def test_other_team_sub_does_not_break_up_an_incomplete_pair(capsys):
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    runtime = _runtime()
    runtime.config.automatic_subs = True
    runtime._sub_batcher.quiet_seconds = 0.2
    runtime._sub_batcher.unmatched_hold_seconds = 5.0

    state.apply_message(
        state.box_score_channel,
        {
            "play_text_9700": "Substitution CUH: out Maddy Weaver",
            "period_9700": "2",
            "clock_9700": "07:41",
        },
    )
    runtime.on_live_update(state, state.raw_last_message)
    assert runtime.pop_auto_actions() == []

    import time

    time.sleep(0.25)
    # Another team's line must not force the lonely CUH out on air alone.
    state.apply_message(
        state.box_score_channel,
        {
            "play_text_9701": "Substitution WCU: in Finley Blackmore",
            "period_9701": "2",
            "clock_9701": "07:40",
        },
    )
    runtime.on_live_update(state, state.raw_last_message)
    assert runtime.pop_auto_actions() == []

    state.apply_message(
        state.box_score_channel,
        {
            "play_text_9702": "Substitution CUH: in Alyssa Schuetze",
            "period_9702": "2",
            "clock_9702": "07:40",
        },
    )
    runtime.on_live_update(state, state.raw_last_message)
    time.sleep(0.25)
    runtime._flush_subs(state)
    actions = runtime.pop_auto_actions()
    cuh = next(
        a
        for a in actions
        if a["kind"] == "sub" and a["pairs"][0].get("sub_out") == "Maddy Weaver"
    )
    assert cuh["pairs"][0]["sub_in"] == "Alyssa Schuetze"


def test_foul_trouble_auto_airs_only_for_new_entrant(capsys):
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    runtime = _runtime()
    runtime.config.automatic_fouls = True
    runtime.config.automatic_subs = False
    assert state.home and state.away
    for p in list(state.away.roster()) + list(state.home.roster()):
        p.stats["F"] = 0

    # Prime: nobody in trouble yet.
    runtime.on_live_update(state, {})
    assert runtime.pop_auto_actions() == []

    first = next(p for p in state.home.roster() if p.name)
    first.stats["F"] = 3
    runtime.on_live_update(state, {})
    actions = runtime.pop_auto_actions()
    assert len(actions) == 1
    assert actions[0]["kind"] == "foul"
    assert actions[0]["variables"]["foul_1_name"] == first.name
    assert "foul_1_logo" in actions[0]["variables"]
    assert actions[0]["variables"]["foul_1_pf"] == "3"
    assert actions[0]["hold_seconds"] == runtime.config.foul_hold_seconds

    # Same player picks up another foul — still-update only, no re-queue.
    first.stats["F"] = 4
    runtime.on_live_update(state, {})
    assert runtime.pop_auto_actions() == []
    assert runtime.last_foul.get("foul_1_pf") == "4"

    second = next(p for p in state.away.roster() if p.name)
    second.stats["F"] = 3
    runtime.on_live_update(state, {})
    actions = runtime.pop_auto_actions()
    assert len(actions) == 1
    assert actions[0]["kind"] == "foul"
    assert actions[0]["hold_seconds"] == runtime.config.foul_hold_seconds
    names = {
        actions[0]["variables"].get(f"foul_{i}_name")
        for i in range(1, 6)
        if actions[0]["variables"].get(f"foul_{i}_name")
    }
    assert first.name in names and second.name in names

    # Operator-configured hold applies even as the board grows.
    runtime.config.foul_hold_seconds = 7.0
    home_rest = [p for p in state.home.roster() if p.name and p is not first]
    home_rest[0].stats["F"] = 3
    runtime.on_live_update(state, {})
    actions = runtime.pop_auto_actions()
    assert actions[0]["hold_seconds"] == 7.0
    assert actions[0]["variables"]["foul_count"] == "3"

    # Take Foul Trouble also uses the configured hold (not board-size scaling).
    taken = runtime.push_foul_take(state)
    assert taken["ok"] is True
    assert taken["hold_seconds"] == 7.0


def test_runtime_batches_subs_and_queues_auto_when_enabled(capsys):
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    runtime = _runtime()
    runtime.config.automatic_subs = True
    runtime._sub_batcher.quiet_seconds = 0.01

    for i, text in enumerate(
        [
            "Substitution CUH: out Maddy Weaver",
            "Substitution CUH: in Alyssa Schuetze",
        ],
        start=9200,
    ):
        state.apply_message(
            state.box_score_channel,
            {"play_text_" + str(i): text, "period_" + str(i): "1", "clock_" + str(i): "07:41"},
        )
        runtime.on_live_update(state, state.raw_last_message)

    import time

    time.sleep(0.05)
    runtime._flush_subs(state)
    assert runtime.last_sub.get("sub_out") == "Maddy Weaver"
    assert runtime.last_sub.get("sub_in") == "Alyssa Schuetze"
    actions = runtime.pop_auto_actions()
    assert len(actions) == 1
    assert actions[0]["kind"] == "sub"
    assert len(actions[0]["pairs"]) == 1
    assert actions[0]["pairs"][0]["sub_out"] == "Maddy Weaver"
    assert actions[0]["pairs"][0]["sub_in"] == "Alyssa Schuetze"


def _queue_subs(clock: str, period: str, sport: str, lines: list[str]):
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    state.sport_code = sport
    runtime = _runtime()
    runtime.config.automatic_subs = True
    runtime._sub_batcher.quiet_seconds = 0.01
    for i, text in enumerate(lines, start=9400):
        state.apply_message(
            state.box_score_channel,
            {
                "play_text_" + str(i): text,
                "period_" + str(i): period,
                "clock_" + str(i): clock,
            },
        )
        runtime.on_live_update(state, state.raw_last_message)
    import time

    time.sleep(0.05)
    runtime._flush_subs(state)
    return runtime


def test_opening_clock_does_not_auto_air_subs(capsys):
    lines = [
        "Substitution CUH: out Maddy Weaver",
        "Substitution CUH: in Alyssa Schuetze",
    ]
    for sport, period, clock in (
        ("WBB", "1st", "10:00"),
        ("MBB", "1st", "20:00"),
        ("WBB", "OT", "5:00"),
    ):
        runtime = _queue_subs(clock, period, sport, lines)
        assert runtime.last_sub.get("sub_out") == "Maddy Weaver"
        assert runtime.pop_auto_actions() == []


def test_counted_down_clock_queues_each_pair(capsys):
    runtime = _queue_subs(
        "9:59",
        "2",
        "WBB",
        [
            "Substitution CUH: out Maddy Weaver",
            "Substitution CUH: out Holly Dolny",
            "Substitution CUH: in Alyssa Schuetze",
            "Substitution CUH: in Ada In",
        ],
    )
    actions = runtime.pop_auto_actions()
    assert len(actions) == 1
    pairs = actions[0]["pairs"]
    assert len(pairs) == 2
    assert pairs[0]["sub_out"] == "Maddy Weaver"
    assert pairs[0]["sub_in"] == "Alyssa Schuetze"
    assert pairs[1]["sub_out"] == "Holly Dolny"
    assert pairs[1]["sub_in"] == "Ada In"


def test_runtime_timeout_auto_queue_respects_config(capsys):
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    runtime = _runtime()
    runtime.config.automatic_timeouts = False
    state.apply_message(
        state.box_score_channel,
        {"play_text_9300": "Timeout short WCU", "period_9300": "1", "clock_9300": "05:21"},
    )
    runtime.on_live_update(state, state.raw_last_message)
    assert runtime.last_timeout.get("timeout_team_abbr") == "WCU"
    assert runtime.pop_auto_actions() == []

    runtime.config.automatic_timeouts = True
    state.apply_message(
        state.box_score_channel,
        {"play_text_9301": "Media Timeout", "period_9301": "1", "clock_9301": "05:00"},
    )
    runtime.on_live_update(state, state.raw_last_message)
    actions = runtime.pop_auto_actions()
    assert any(a["kind"] == "timeout" for a in actions)


def test_auto_subs_do_not_still_push_so_a_second_team_logo_can_animate(capsys):
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    runtime = _runtime()
    runtime.config.automatic_subs = True
    runtime._sub_batcher.quiet_seconds = 0.01
    pushes = {"n": 0}
    original = runtime.graphics.push_substitution

    def counting_push(*args, **kwargs):
        pushes["n"] += 1
        return original(*args, **kwargs)

    runtime.graphics.push_substitution = counting_push  # type: ignore[method-assign]
    for i, text in enumerate(
        [
            "Substitution CUH: out Maddy Weaver",
            "Substitution CUH: in Alyssa Schuetze",
        ],
        start=9500,
    ):
        state.apply_message(
            state.box_score_channel,
            {"play_text_" + str(i): text, "period_" + str(i): "1", "clock_" + str(i): "07:41"},
        )
        runtime.on_live_update(state, state.raw_last_message)
    import time

    time.sleep(0.05)
    runtime._flush_subs(state)
    assert pushes["n"] == 0
    actions = runtime.pop_auto_actions()
    assert len(actions) == 1
    assert "sub_team_logo" in actions[0]["pairs"][0]


def test_simulate_substitution_queues_random_roster_pairs(capsys):
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    runtime = _runtime()
    result = runtime.simulate_substitution(state, pair_count=2)
    assert result["ok"] is True
    assert result["side"] in {"home", "away"}
    assert len(result["pairs"]) == 2
    assert all(p["out"] and p["in"] for p in result["pairs"])
    actions = runtime.pop_auto_actions()
    assert len(actions) == 1
    assert actions[0]["kind"] == "sub"
    assert len(actions[0]["pairs"]) == 2
    assert runtime.last_sub.get("sub_team") == result["team"]


def test_manual_player_cue_clears_ft_attempt_by_default(capsys):
    state = bootstrap_live_state(FIXTURE.read_text(), "6656891")
    runtime = _runtime()
    assert state.home
    player = next(p for p in state.home.roster() if p.number)
    result = runtime.cue_player(state, f"H{player.number}")
    assert result["ok"] is True
    assert result["player"]["player_ft_attempt"] == ""
    assert result["player"]["player_name"] == player.name
