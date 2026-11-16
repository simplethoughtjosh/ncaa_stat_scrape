"""Linescore totals must survive empty period cells on one side."""

from college_sports_scraper.ncaa.parse import parse_linescore_totals
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "wbb_live_box_score_6656891.html"


def test_parse_linescore_totals_from_live_fixture():
    away, home = parse_linescore_totals(FIXTURE.read_text())
    assert away == "36"
    assert home == "51"


def test_parse_linescore_totals_skips_empty_period_and_keeps_other_team():
    """MBB mid-half: period-2 cells are blank; clock can sit on the away row.

    Old logic required cells[-1].isdigit() on every team row, so one messy row
    dropped both scores. Prefer score_{id} and keep moving to the other team.
    """
    html = """
    <table id="linescore_6648766_table">
      <tr>
        <td></td>
        <td>Fla. Memorial</td>
        <td id="linescore_8679988_1">0</td>
        <td id="linescore_8679988_2"></td>
        <td id="score_8679988">7</td>
        <td>1st H11:19:20</td>
      </tr>
      <tr>
        <td></td>
        <td>Rollins</td>
        <td id="linescore_8679989_1">0</td>
        <td id="linescore_8679989_2"></td>
        <td id="score_8679989">24</td>
      </tr>
    </table>
    """
    assert parse_linescore_totals(html) == ("7", "24")


def test_parse_linescore_totals_keeps_home_when_away_score_cell_empty():
    html = """
    <table id="linescore_1_table">
      <tr>
        <td id="linescore_1_1"></td>
        <td id="score_1"></td>
      </tr>
      <tr>
        <td id="linescore_2_1">10</td>
        <td id="score_2">18</td>
      </tr>
    </table>
    """
    assert parse_linescore_totals(html) == (None, "18")
