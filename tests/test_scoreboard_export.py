"""Unit tests for SSC Scoreboard Automation wiring (no Photoshop)."""

from pathlib import Path

import pytest

from college_sports_scraper.basketball import scoreboard_export as mod
from college_sports_scraper.basketball.scoreboard_export import (
    ScoreboardExportError,
    export_scoreboard_pngs,
    find_existing_scoreboard_png,
    normalize_hex,
    resolve_team_color,
    safe_filename,
)


def test_normalize_hex_and_safe_filename():
    assert normalize_hex("#c81") == "#CC8811"
    assert normalize_hex("1a3a6b") == "#1A3A6B"
    assert safe_filename("Florida Southern") == "Florida Southern"
    assert safe_filename("A/B:C?.png") == "ABC.png"
    with pytest.raises(ScoreboardExportError):
        normalize_hex("red")


def test_resolve_team_color_uses_override_and_table(tmp_path):
    colors = {"florida southern": "#C8102E"}
    assert (
        resolve_team_color("Florida Southern", override="#abc", colors=colors, search=False)
        == "#AABBCC"
    )
    assert (
        resolve_team_color(
            "Florida Southern", colors=colors, fallback="#111111", search=False
        )
        == "#C8102E"
    )
    assert (
        resolve_team_color("Unknown U", colors={}, fallback="#1A3A6B", search=False)
        == "#1A3A6B"
    )


def test_color_search_queries_and_hex_pick():
    assert "Rollins Athletics Hex colors" in mod.color_search_queries("Rollins")
    html = """
    <a href="https://teamcolorcodes.com/rollins-tars-color-codes/">Rollins hex</a>
    <a href="https://example.com/unrelated">Other</a>
    """
    urls = mod.extract_color_page_urls(html)
    assert urls[0].startswith("https://teamcolorcodes.com/")
    page = (
        '<div class="colorblock" style="background-color: #0072BC; color: #FFFFFF;">'
        "Rollins Blue</div>"
        '<div class="colorblock" style="background-color: #FED304;">Rollins Gold</div>'
        "Hex: #0072BC Hex: #FED304 Hex: #FFFFFF"
        "body{background-color:#00E5FF}"
    )
    assert mod.extract_hex_colors(page) == ["#0072BC", "#FED304"]
    assert mod.pick_primary_hex(["#FED304", "#0072BC"]) == "#0072BC"


def test_resolve_team_color_uses_web_search_before_fallback(monkeypatch):
    monkeypatch.setattr(
        mod,
        "search_athletics_hex_color",
        lambda name, **kwargs: "#0072BC" if "Rollins" in name else None,
    )
    assert (
        resolve_team_color("Rollins", colors={}, fallback="#111111", search=True)
        == "#0072BC"
    )


def test_export_scoreboard_pngs_invokes_absolute_venv_python(tmp_path, monkeypatch):
    py = tmp_path / "python"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    script = tmp_path / "export_scoreboard.py"
    script.write_text("# stub\n")
    out = tmp_path / "teams"
    out.mkdir()

    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = list(cmd)
        seen["kwargs"] = kwargs
        (out / "Florida Southern.png").write_bytes(b"home")
        (out / "Riverside.png").write_bytes(b"away")

        class Result:
            returncode = 0
            stdout = (
                f"Saved PNG: {out / 'Florida Southern.png'}\n"
                f"Saved PNG: {out / 'Riverside.png'}\n"
            )
            stderr = ""

        return Result()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    home_png, away_png = export_scoreboard_pngs(
        home="Florida Southern",
        home_color="#C8102E",
        away="Riverside",
        away_color="#1A3A6B",
        python_bin=py,
        script=script,
        output_dir=out,
    )

    assert seen["cmd"][0] == str(py)
    assert seen["cmd"][1] == str(script)
    assert "--home" in seen["cmd"] and "Florida Southern" in seen["cmd"]
    assert "--home-color" in seen["cmd"] and "#C8102E" in seen["cmd"]
    assert "--away" in seen["cmd"] and "Riverside" in seen["cmd"]
    assert "--away-color" in seen["cmd"] and "#1A3A6B" in seen["cmd"]
    assert "--skip-existing" in seen["cmd"]
    assert home_png == out / "Florida Southern.png"
    assert away_png == out / "Riverside.png"


def test_find_existing_scoreboard_png_exact_and_name_key(tmp_path):
    (tmp_path / "Penn St.png").write_bytes(b"x")
    (tmp_path / "Florida Southern.png").write_bytes(b"y")
    assert find_existing_scoreboard_png("Florida Southern", tmp_path) == (
        tmp_path / "Florida Southern.png"
    ).resolve()
    assert find_existing_scoreboard_png("Penn St.", tmp_path) == (
        tmp_path / "Penn St.png"
    ).resolve()
    assert find_existing_scoreboard_png("Missing U", tmp_path) is None


def test_export_scoreboard_pngs_skips_existing_without_photoshop(tmp_path, monkeypatch):
    py = tmp_path / "python"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    script = tmp_path / "export_scoreboard.py"
    script.write_text("# stub\n")
    out = tmp_path / "teams"
    out.mkdir()
    (out / "Florida Southern.png").write_bytes(b"home")
    (out / "Riverside.png").write_bytes(b"away")

    def boom(*args, **kwargs):
        raise AssertionError("Photoshop should not run when both PNGs exist")

    monkeypatch.setattr(mod.subprocess, "run", boom)

    home_png, away_png = export_scoreboard_pngs(
        home="Florida Southern",
        home_color="#C8102E",
        away="Riverside",
        away_color="#1A3A6B",
        python_bin=py,
        script=script,
        output_dir=out,
    )
    assert home_png == (out / "Florida Southern.png").resolve()
    assert away_png == (out / "Riverside.png").resolve()


def test_export_scoreboard_pngs_generates_only_missing_side(tmp_path, monkeypatch):
    py = tmp_path / "python"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    script = tmp_path / "export_scoreboard.py"
    script.write_text("# stub\n")
    out = tmp_path / "teams"
    out.mkdir()
    (out / "Florida Southern.png").write_bytes(b"home")

    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = list(cmd)

        class Result:
            returncode = 0
            stdout = f"Saved PNG: {out / 'Riverside.png'}\n"
            stderr = ""

        (out / "Riverside.png").write_bytes(b"away")
        return Result()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    home_png, away_png = export_scoreboard_pngs(
        home="Florida Southern",
        home_color="#C8102E",
        away="Riverside",
        away_color="#1A3A6B",
        python_bin=py,
        script=script,
        output_dir=out,
    )
    assert "--skip-existing" in seen["cmd"]
    assert home_png == (out / "Florida Southern.png").resolve()
    assert away_png == out / "Riverside.png"


def test_export_scoreboard_for_state_skips_existing_without_color_or_photoshop(
    tmp_path, monkeypatch
):
    """Existing Title_Stuff PNGs must short-circuit color search and Photoshop."""
    from college_sports_scraper.ncaa.live_state import bootstrap_live_state

    fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "wbb_live_box_score_6656891.html"
    )
    state = bootstrap_live_state(fixture.read_text(), "6656891")
    assert state.home and state.away
    out = tmp_path / "teams"
    out.mkdir()
    home_name = mod.team_display_name(
        state.sport_code, state.home.name or "", photo_root=tmp_path
    )
    away_name = mod.team_display_name(
        state.sport_code, state.away.name or "", photo_root=tmp_path
    )
    (out / f"{mod.safe_filename(home_name)}.png").write_bytes(b"home")
    (out / f"{mod.safe_filename(away_name)}.png").write_bytes(b"away")

    def boom_color(*args, **kwargs):
        raise AssertionError("color resolve should not run when PNGs exist")

    monkeypatch.setattr(mod, "resolve_team_color", boom_color)
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("subprocess should not run")
        ),
    )

    result = mod.export_scoreboard_for_state(state, output_dir=out, photo_root=tmp_path)
    assert result["home_skipped"] == "1"
    assert result["away_skipped"] == "1"
    assert result["home_color"] == "(existing)"
    assert result["away_color"] == "(existing)"
    assert Path(result["home_png"]).is_file()
    assert Path(result["away_png"]).is_file()


def test_export_scoreboard_pngs_surfaces_stderr_on_failure(tmp_path, monkeypatch):
    py = tmp_path / "python"
    py.write_text("x")
    script = tmp_path / "export_scoreboard.py"
    script.write_text("x")

    def fake_run(cmd, **kwargs):
        class Result:
            returncode = 2
            stdout = ""
            stderr = "Template PSD not found: templates/home.psd"

        return Result()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    with pytest.raises(ScoreboardExportError, match="Template PSD not found"):
        export_scoreboard_pngs(
            home="A",
            home_color="#111111",
            away="B",
            away_color="#222222",
            python_bin=py,
            script=script,
            output_dir=tmp_path,
            skip_existing=False,
        )
