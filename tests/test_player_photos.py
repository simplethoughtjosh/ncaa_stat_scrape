"""Starting-five headshots resolve from ~/SSC/<sport>/<team>/<player>.jpg."""

from pathlib import Path

from college_sports_scraper.basketball.events import SubBatch
from college_sports_scraper.basketball.graphics import (
    on_court_payload,
    player_l3_payload,
    substitution_payload,
)
from college_sports_scraper.basketball.photos import player_photo_path, silhouette_path, team_logo_path
from college_sports_scraper.ncaa.live_state import BasketballLiveState, LivePlayer, LiveTeam

XML = (
    Path(__file__).resolve().parents[1]
    / "captivate_controller"
    / "STP_NCAA_Basketball"
    / "STP_NCAA_BasketballMain.xml"
)


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"jpg")


def test_photo_path_matches_team_and_player_file(tmp_path: Path):
    furman = tmp_path / "WBB" / "Furman" / "alyssa_ervin.jpg"
    other_sport = tmp_path / "MBB" / "Furman" / "alyssa_ervin.jpg"
    accented = tmp_path / "MBB" / "Florida Tech" / "tin_tikveša.jpg"
    apostrophe = tmp_path / "MBB" / "Rollins" / "trey_oneil.jpg"
    _touch(furman)
    _touch(other_sport)
    _touch(accented)
    _touch(apostrophe)
    _touch(tmp_path / "WBB" / "Florida Southern" / "jane_doe.jpg")
    _touch(tmp_path / "WBB" / "Palm Beach Atlantic" / "ann_lee.jpg")
    _touch(tmp_path / "WBB" / "Saint Leo" / "pat_roy.jpg")
    _touch(tmp_path / "WBB" / "Florida Tech" / "sam_ray.jpg")

    found = player_photo_path("WBB", "Furman", "Alyssa Ervin", root=tmp_path)
    assert found == str(furman.resolve())
    assert player_photo_path("MBB", "Furman", "Alyssa Ervin", root=tmp_path) == str(other_sport.resolve())
    assert player_photo_path("MBB", "Florida Tech", "Tin Tikveša", root=tmp_path) == str(accented.resolve())
    assert player_photo_path("MBB", "Rollins", "Trey O'Neil", root=tmp_path) == str(apostrophe.resolve())
    assert "Florida Southern" in player_photo_path("WBB", "Fla. Southern", "Jane Doe", root=tmp_path)
    assert "Palm Beach Atlantic" in player_photo_path("wbb", "Palm Beach Atl.", "Ann Lee", root=tmp_path)
    assert "Saint Leo" in player_photo_path("WBB", "St. Leo", "Pat Roy", root=tmp_path)
    keita = tmp_path / "WBB" / "New Mexico State" / "aissatou_keita.jpg"
    _touch(keita)
    assert player_photo_path("WBB", "New Mexico St.", "Aissatou Keita", root=tmp_path) == str(keita.resolve())
    # A shorter school name must not grab a different school that shares a word.
    assert player_photo_path("WBB", "Florida", "Sam Ray", root=tmp_path) == silhouette_path(tmp_path)
    placeholder = tmp_path / "Headshot.png"
    _touch(placeholder)
    assert player_photo_path("WBB", "Furman", "Missing Player", root=tmp_path) == str(placeholder.resolve())
    assert player_photo_path("WBB", "Furman", "Alyssa Ervin", root=tmp_path) == str(furman.resolve())


def test_on_court_payload_includes_photo_paths(tmp_path: Path):
    photo = tmp_path / "WBB" / "Southern Wesleyan" / "maris_kate_payseur.jpg"
    _touch(photo)
    team = LiveTeam(competitor_id="1", name="Southern Wesleyan", venue="away")
    team.players["1"] = LivePlayer(
        player_id="1",
        competitor_id="1",
        name="Maris Kate Payseur",
        number="4",
        position="G",
        on_court=True,
    )
    payload = on_court_payload(team, sport_code="WBB", photo_root=tmp_path)
    assert payload["player_1_photo"] == str(photo.resolve())
    assert payload["player_1_name"] == "Maris Kate Payseur"
    assert payload["player_2_photo"] == silhouette_path(tmp_path, "WBB")


def test_missing_photo_uses_the_silhouette_for_that_sport(tmp_path: Path):
    female = tmp_path / "Headshot-WBB.png"
    male = tmp_path / "Headshot-MBB.png"
    _touch(female)
    _touch(male)
    assert player_photo_path("WBB", "New Mexico St.", "Missing Player", root=tmp_path) == str(female.resolve())
    assert player_photo_path("MBB", "New Mexico St.", "Missing Player", root=tmp_path) == str(male.resolve())


def test_team_logo_path_finds_logo_png(tmp_path: Path):
    logo = tmp_path / "WBB" / "New Mexico State" / "logo.png"
    _touch(logo)
    assert team_logo_path("WBB", "New Mexico St.", root=tmp_path) == str(logo.resolve())
    assert team_logo_path("WBB", "Missing Team", root=tmp_path) == ""


def test_player_l3_payload_includes_team_logo(tmp_path: Path):
    logo = tmp_path / "WBB" / "Campbell" / "logo.png"
    _touch(logo)
    team = LiveTeam(competitor_id="1", name="Campbell", venue="home")
    player = LivePlayer(
        player_id="1", competitor_id="1", name="Maddy Weaver", number="1", on_court=True
    )
    team.players["1"] = player
    payload = player_l3_payload(
        team, player, side="home", code="H1", sport_code="WBB", photo_root=tmp_path
    )
    assert payload["player_team_logo"] == str(logo.resolve())


def test_substitution_payload_includes_team_logo(tmp_path: Path):
    logo = tmp_path / "WBB" / "Campbell" / "logo.png"
    _touch(logo)
    team = LiveTeam(competitor_id="1", name="Campbell", venue="home")
    team.players["1"] = LivePlayer(
        player_id="1", competitor_id="1", name="Maddy Weaver", number="1", on_court=True
    )
    team.players["2"] = LivePlayer(
        player_id="2", competitor_id="2", name="Alyssa Schuetze", number="2", on_court=False
    )
    state = BasketballLiveState(game_id="1", sport_code="WBB", home=team)
    payload = substitution_payload(
        state,
        SubBatch(team_abbr="Campbell", outs=["Maddy Weaver"], ins=["Alyssa Schuetze"]),
        photo_root=tmp_path,
    )
    assert payload["sub_team_logo"] == str(logo.resolve())
    assert payload["sub_out"] == "Maddy Weaver"
    assert payload["sub_in"] == "Alyssa Schuetze"


def test_controller_declares_starting_five_image_variables():
    xml = XML.read_text()
    for side in ("away", "home"):
        for slot in range(1, 6):
            assert f'<variable name="{side}_p{slot}_photo" type="image" />' in xml
    assert '<variable name="sub_team_logo" type="image" />' in xml
    assert '<variable name="player_photo" type="image" />' in xml
    assert '<variable name="player_team_logo" type="image" />' in xml
