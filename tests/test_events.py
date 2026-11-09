"""Requirements tests for PBP parsers (FT, sub, timeout) and sub batching."""

from college_sports_scraper.basketball.events import (
    clock_has_counted_down,
    SubBatch,
    SubBatcher,
    SubLine,
    parse_free_throw,
    parse_player_code,
    parse_substitution,
    parse_timeout,
)


def test_player_code_home_away_and_reject_invalid():
    assert parse_player_code("H21") == ("home", "21")
    assert parse_player_code("a7") == ("away", "7")
    assert parse_player_code("H00") == ("home", "00")
    assert parse_player_code("") is None
    assert parse_player_code("21") is None
    assert parse_player_code("HX") is None


def test_free_throw_parser_captures_attempt_and_shooter():
    ft = parse_free_throw(
        "Free throw 1 of 2 (fast break) made by Madyn Kassatly(WCU)"
    )
    assert ft is not None
    assert ft.attempt == 1
    assert ft.of_total == 2
    assert ft.attempt_label == "1 of 2"
    assert ft.player_name == "Madyn Kassatly"
    assert ft.made is True

    missed = parse_free_throw(
        "Free throw 2 of 2 missed by Breckyn Schilling(WCU), rebound defensive Holly Dolny(CUH)"
    )
    assert missed is not None
    assert missed.attempt == 2
    assert "Breckyn Schilling" == missed.player_name
    assert missed.made is False


def test_free_throw_parser_ignores_non_ft_lines():
    assert parse_free_throw("2pt layup made by Maddy Weaver(CUH)") is None
    assert parse_free_throw("Substitution WCU: in Finley Blackmore") is None


def test_substitution_parser_in_and_out():
    out_line = parse_substitution("Substitution CUH: out Holly Dolny")
    assert out_line is not None
    assert out_line.team_abbr == "CUH"
    assert out_line.direction == "out"
    assert out_line.player_name == "Holly Dolny"

    in_line = parse_substitution("Substitution WCU: in Finley Blackmore")
    assert in_line is not None
    assert in_line.direction == "in"
    assert in_line.team_abbr == "WCU"

    full = parse_substitution("Substitution Westminster (UT): out Ellie Mitchell")
    assert full is not None
    assert full.team_abbr == "Westminster (UT)"
    assert full.direction == "out"
    assert full.player_name == "Ellie Mitchell"

    dotted = parse_substitution("Substitution Idaho St.: in Maggie Hutka")
    assert dotted is not None
    assert dotted.team_abbr == "Idaho St."
    assert dotted.direction == "in"
    assert dotted.player_name == "Maggie Hutka"


def test_timeout_short_and_media():
    short = parse_timeout("Timeout short WCU")
    assert short is not None
    assert short.team_abbr == "WCU"
    assert short.is_media is False
    assert "Timeout" in short.label

    media = parse_timeout("Media Timeout")
    assert media is not None
    assert media.is_media is True
    assert media.label == "Media Timeout"

    assert parse_timeout("2pt jump shot made by Ledjan Pahukoa(CUH)") is None


def test_sub_batcher_groups_same_team_then_flushes_after_quiet():
    batcher = SubBatcher(quiet_seconds=0.5, max_slots=5)
    assert batcher.add(SubLine("CUH", "out", "A", ""), period="1", clock="1:00", now=1.0) == []
    assert batcher.add(SubLine("CUH", "out", "B", ""), period="1", clock="1:00", now=1.1) == []
    assert batcher.add(SubLine("CUH", "in", "C", ""), period="1", clock="1:00", now=1.2) == []
    assert batcher.add(SubLine("CUH", "in", "D", ""), period="1", clock="1:00", now=1.3) == []

    ready = batcher.flush_ready(2.0)
    assert len(ready) == 1
    assert ready[0].outs == ["A", "B"]
    assert ready[0].ins == ["C", "D"]


def test_sub_batcher_caps_at_five_and_holds_unequal_until_match_timeout():
    batcher = SubBatcher(quiet_seconds=0.1, unmatched_hold_seconds=1.0, max_slots=5)
    for i in range(6):
        batcher.add(
            SubLine("WCU", "out", f"Out{i}", ""),
            period="2",
            clock="5:00",
            now=10.0 + i * 0.01,
        )
    batcher.add(SubLine("WCU", "in", "OnlyIn", ""), period="2", clock="5:00", now=10.1)
    # Quiet enough for a balanced board, but still unmatched — hold.
    assert batcher.flush_ready(10.5) == []
    ready = batcher.flush_ready(11.2)
    assert len(ready) == 1
    assert len(ready[0].outs) == 5
    assert ready[0].ins == ["OnlyIn"]


def test_sub_batcher_waits_for_same_team_match_before_quiet_flush():
    batcher = SubBatcher(quiet_seconds=0.5, unmatched_hold_seconds=5.0, max_slots=5)
    batcher.add(SubLine("CUH", "out", "A", ""), period="1", clock="1:00", now=1.0)
    assert batcher.flush_ready(2.0) == []  # quiet passed, but lonely out
    batcher.add(SubLine("CUH", "in", "B", ""), period="1", clock="1:00", now=2.1)
    assert batcher.flush_ready(2.4) == []  # pair complete, still in quiet window
    ready = batcher.flush_ready(2.7)
    assert len(ready) == 1
    assert ready[0].outs == ["A"]
    assert ready[0].ins == ["B"]


def test_pairs_follow_arrival_order_and_blank_the_short_side():
    batch = SubBatch(team_abbr="CUH", outs=["A", "B"], ins=["C"])
    assert batch.pairs() == [("A", "C"), ("B", "")]
    assert SubBatch(team_abbr="CUH", outs=["A", "B"], ins=["C", "D"]).pairs() == [
        ("A", "C"),
        ("B", "D"),
    ]


def test_clock_has_counted_down_only_after_the_opening_time():
    assert clock_has_counted_down("10:00", sport_code="WBB", period="1st") is False
    assert clock_has_counted_down("10:00:00", sport_code="WBB", period="1") is False
    assert clock_has_counted_down("9:59", sport_code="WBB", period="1") is True
    assert clock_has_counted_down("20:00", sport_code="MBB", period="1st") is False
    assert clock_has_counted_down("19:59", sport_code="MBB", period="1st") is True
    assert clock_has_counted_down("5:00", sport_code="WBB", period="OT") is False
    assert clock_has_counted_down("5:00:00", sport_code="MBB", period="OT") is False
    assert clock_has_counted_down("4:59", sport_code="MBB", period="2nd OT") is True
    assert clock_has_counted_down("", sport_code="WBB", period="1") is False
    assert clock_has_counted_down(None, sport_code="WBB", period="1") is False


def test_sub_batcher_holds_incomplete_when_other_team_starts():
    batcher = SubBatcher(quiet_seconds=0.2, unmatched_hold_seconds=5.0, max_slots=5)
    batcher.add(SubLine("CUH", "out", "X", ""), period="1", clock="2:00", now=1.0)
    # Quiet for a balanced board, but CUH is still a lonely out — keep it.
    flushed = batcher.add(SubLine("WCU", "in", "Y", ""), period="1", clock="2:00", now=1.5)
    assert flushed == []
    assert batcher.has_open_batch("CUH")
    # Matching CUH in arrives; after quiet the pair publishes.
    batcher.add(SubLine("CUH", "in", "Z", ""), period="1", clock="2:00", now=1.6)
    ready = batcher.flush_ready(1.9)
    assert len(ready) == 1
    assert ready[0].team_abbr == "CUH"
    assert ready[0].outs == ["X"]
    assert ready[0].ins == ["Z"]
