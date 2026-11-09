"""The control terminal must print a lineup even when a jersey number is missing."""

from college_sports_scraper.cli import LiveStatusLine, _print_on_court, format_live_status
from college_sports_scraper.ncaa.live_state import BasketballLiveState, LivePlayer, LiveTeam


def test_print_on_court_allows_missing_number(capsys):
    away = LiveTeam(competitor_id="a", name="Southern Wesleyan", venue="away")
    away.players["1"] = LivePlayer(
        player_id="1",
        competitor_id="a",
        name="Maris Kate Payseur",
        number=None,
        position="G",
        on_court=True,
    )
    home = LiveTeam(competitor_id="h", name="Furman", venue="home")
    home.players["2"] = LivePlayer(
        player_id="2",
        competitor_id="h",
        name="Alyssa Ervin",
        number="4",
        position="G",
        on_court=True,
    )
    state = BasketballLiveState(game_id="6665904", away=away, home=home, away_score="26", home_score="75")
    _print_on_court(state)
    out = capsys.readouterr().out
    assert "Maris Kate Payseur" in out
    assert "#4" in out


def test_live_status_rewrites_one_line(capsys):
    away = LiveTeam(competitor_id="a", name="Southern Utah", venue="away")
    home = LiveTeam(competitor_id="h", name="New Mexico St.", venue="home")
    state = BasketballLiveState(
        game_id="1",
        away=away,
        home=home,
        away_score="37",
        home_score="31",
        period="3rd",
        clock="9:32",
    )
    text = format_live_status(state, 4)
    assert "Southern Utah 37–31 New Mexico St." in text
    assert "updates 4" in text
    assert "\n" not in text

    line = LiveStatusLine()
    line.redraw(text)
    line.redraw(format_live_status(state, 5))
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert "\x1b[1A" in out
    assert "updates 5" in out
