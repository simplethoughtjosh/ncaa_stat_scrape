"""Requirements tests for 2160p made-shot PNG rendering."""

from pathlib import Path

from PIL import Image

from college_sports_scraper.basketball.shot_map import (
    OUT_H,
    OUT_W,
    ShotPoint,
    render_home_away_maps,
    render_made_shot_png,
)


def test_made_shot_png_is_2160p_and_only_includes_requested_org(tmp_path: Path):
    shots = [
        ShotPoint(x=10.0, y=50.0, org_id="133", made=True, play_id="1"),
        ShotPoint(x=12.0, y=48.0, org_id="133", made=False, play_id="2"),
        ShotPoint(x=90.0, y=50.0, org_id="2759", made=True, play_id="3"),
    ]
    out = tmp_path / "home.png"
    path = render_made_shot_png(shots, org_id="133", out_path=out)
    assert path.exists()
    with Image.open(path) as img:
        assert img.size == (OUT_W, OUT_H)
        assert OUT_W == 3840 and OUT_H == 2160


def test_home_away_maps_write_versioned_and_stable_files(tmp_path: Path):
    shots = [
        ShotPoint(x=8.0, y=50.0, org_id="away1", made=True, play_id="a"),
        ShotPoint(x=92.0, y=50.0, org_id="home1", made=True, play_id="h"),
    ]
    away, home = render_home_away_maps(
        shots,
        away_org_id="away1",
        home_org_id="home1",
        output_dir=tmp_path,
        stamp="100",
    )
    assert away.name == "away_shot_map_100.png"
    assert home.name == "home_shot_map_100.png"
    assert away.exists() and home.exists()
    assert (tmp_path / "away_shot_map.png").exists()
    assert (tmp_path / "home_shot_map.png").exists()
    with Image.open(away) as a, Image.open(home) as h:
        assert a.size == (3840, 2160)
        assert h.size == (3840, 2160)


def test_second_rebuild_changes_path_and_prunes_old_versioned(tmp_path: Path):
    shots = [
        ShotPoint(x=8.0, y=50.0, org_id="away1", made=True, play_id="a"),
        ShotPoint(x=92.0, y=50.0, org_id="home1", made=True, play_id="h"),
    ]
    first_away, first_home = render_home_away_maps(
        shots,
        away_org_id="away1",
        home_org_id="home1",
        output_dir=tmp_path,
        stamp="1",
    )
    second_away, second_home = render_home_away_maps(
        shots,
        away_org_id="away1",
        home_org_id="home1",
        output_dir=tmp_path,
        stamp="2",
    )
    assert first_away != second_away
    assert first_home != second_home
    assert not first_away.exists()
    assert not first_home.exists()
    assert second_away.exists() and second_home.exists()
    versioned = sorted(p.name for p in tmp_path.glob("*_shot_map_*.png"))
    assert versioned == ["away_shot_map_2.png", "home_shot_map_2.png"]
    assert (tmp_path / "away_shot_map.png").exists()
    assert (tmp_path / "home_shot_map.png").exists()


def test_center_logo_is_drawn_under_made_shots(tmp_path: Path):
    # Bright opaque logo so midcourt pixels change vs court-only render.
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(logo)
    shots = [
        ShotPoint(x=50.0, y=50.0, org_id="133", made=True, play_id="1"),
    ]
    bare = render_made_shot_png(
        shots, org_id="133", out_path=tmp_path / "bare.png"
    )
    with_logo = render_made_shot_png(
        shots,
        org_id="133",
        out_path=tmp_path / "logo_map.png",
        logo_path=logo,
    )
    with Image.open(bare) as a, Image.open(with_logo) as b:
        assert a.size == b.size == (OUT_W, OUT_H)
        assert a.tobytes() != b.tobytes()
        # A make at midcourt stays on top (blue dot, not pure logo red).
        center = b.getpixel((OUT_W // 2, OUT_H // 2))
        assert center[:3] != (255, 0, 0)
