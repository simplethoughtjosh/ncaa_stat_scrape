"""Render 2160p made-shot chart PNGs from NCAA shot_chart coordinates."""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

# NCAA SVG court is 940 x 500; map percent coords the same way the site does.
COURT_W = 940
COURT_H = 500
OUT_W = 3840
OUT_H = 2160
CANVAS = (12, 14, 18)  # dark letterbox
COURT_FILL = (22, 28, 36)  # dark floor (not the light wheat court)
LINE = (170, 180, 190)  # light lines on the dark floor
AWAY_DOT = (70, 130, 180)  # steel blue
HOME_DOT = (70, 130, 180)  # same blue — each PNG is one team
# Center circle is r=60 court units; logo sits inside that area.
CENTER_X = 469.35
CENTER_Y = 250.19
LOGO_COURT_SIZE = 100.0


@dataclass
class ShotPoint:
    x: float  # 0–100 percent of court width
    y: float  # 0–100 percent of court height
    org_id: str
    made: bool
    play_id: str = ""


def _draw_court(draw: ImageDraw.ImageDraw, ox: int, oy: int, scale: float) -> None:
    def sx(x: float) -> float:
        return ox + x * scale

    def sy(y: float) -> float:
        return oy + y * scale

    def line(points: list[tuple[float, float]], width: int = 3) -> None:
        if len(points) < 2:
            return
        coords = [(sx(x), sy(y)) for x, y in points]
        draw.line(coords, fill=LINE, width=max(1, int(width * scale / 2)))

    # Floor
    draw.rectangle([sx(0), sy(0), sx(COURT_W), sy(COURT_H)], fill=COURT_FILL, outline=LINE, width=max(2, int(3 * scale)))
    # Half court
    line([(470, 0), (470, 500)], width=2)
    # Center circle
    r = 60 * scale
    cx, cy = sx(CENTER_X), sy(CENTER_Y)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=LINE, width=max(2, int(2 * scale)))
    # Free throw lanes
    draw.rectangle([sx(0), sy(190.15), sx(190), sy(310.15)], outline=LINE, width=max(2, int(2 * scale)))
    draw.rectangle([sx(750), sy(190.15), sx(940), sy(310.15)], outline=LINE, width=max(2, int(2 * scale)))
    # FT arcs
    for cx0, open_right in ((190, True), (750, False)):
        box = [sx(cx0 - 60), sy(190.15), sx(cx0 + 60), sy(310.15)]
        if open_right:
            draw.arc(box, start=270, end=90, fill=LINE, width=max(2, int(2 * scale)))
        else:
            draw.arc(box, start=90, end=270, fill=LINE, width=max(2, int(2 * scale)))
    # Baskets
    for bx in (50, 890):
        br = 9 * scale
        draw.ellipse([sx(bx) - br, sy(250) - br, sx(bx) + br, sy(250) + br], outline=LINE, width=max(2, int(2 * scale)))
        line([(bx - 10 if bx < 500 else bx + 10, 220), (bx - 10 if bx < 500 else bx + 10, 280)], width=2)
    # 3pt straight segments + arcs (simplified)
    line([(0, 41.03), (52, 41.03)], width=2)
    line([(0, 456.34), (51, 456.34)], width=2)
    line([(940, 41.03), (889, 41.03)], width=2)
    line([(940, 456.34), (889, 456.34)], width=2)
    # Left 3pt arc approx
    left_box = [sx(49 - 208), sy(250 - 208), sx(49 + 208), sy(250 + 208)]
    draw.arc(left_box, start=270, end=90, fill=LINE, width=max(2, int(2 * scale)))
    right_box = [sx(891 - 208), sy(250 - 208), sx(891 + 208), sy(250 + 208)]
    draw.arc(right_box, start=90, end=270, fill=LINE, width=max(2, int(2 * scale)))


def _paste_center_logo(
    img: Image.Image,
    logo_path: str | Path | None,
    *,
    ox: int,
    oy: int,
    scale: float,
) -> None:
    """Composite team logo over midcourt, under shot dots."""
    if not logo_path:
        return
    path = Path(logo_path)
    if not path.is_file():
        return
    try:
        logo = Image.open(path).convert("RGBA")
    except OSError:
        return
    max_px = max(32, int(LOGO_COURT_SIZE * scale))
    logo.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    cx = int(ox + CENTER_X * scale - logo.width / 2)
    cy = int(oy + CENTER_Y * scale - logo.height / 2)
    img.paste(logo, (cx, cy), logo)


def render_made_shot_png(
    shots: list[ShotPoint],
    *,
    org_id: str,
    out_path: Path,
    dot_color: tuple[int, int, int] = HOME_DOT,
    width: int = OUT_W,
    height: int = OUT_H,
    logo_path: str | Path | None = None,
) -> Path:
    """Render made shots for one team onto a 16:9 canvas with letterboxed court."""
    img = Image.new("RGBA", (width, height), (*CANVAS, 255))
    draw = ImageDraw.Draw(img)

    scale = min(width / COURT_W, height / COURT_H) * 0.92
    court_pw = COURT_W * scale
    court_ph = COURT_H * scale
    ox = int((width - court_pw) / 2)
    oy = int((height - court_ph) / 2)
    _draw_court(draw, ox, oy, scale)
    # Logo under makes so dots stay readable over the mark.
    _paste_center_logo(img, logo_path, ox=ox, oy=oy, scale=scale)

    radius = max(8, int(5 * (court_pw / COURT_W) * 1.4))
    for shot in shots:
        if not shot.made or str(shot.org_id) != str(org_id):
            continue
        cx = ox + (shot.x / 100.0) * court_pw
        cy = oy + (shot.y / 100.0) * court_ph
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=dot_color,
            outline=dot_color,
        )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, format="PNG", optimize=True)
    return out_path


def prune_old_shot_maps(output_dir: Path, keep: set[Path]) -> None:
    """Delete prior versioned shot maps; keep stables + the current pair."""
    keep_resolved = {p.resolve() for p in keep}
    for pattern in ("away_shot_map_*.png", "home_shot_map_*.png"):
        for path in Path(output_dir).glob(pattern):
            if path.resolve() in keep_resolved:
                continue
            try:
                path.unlink()
            except OSError:
                pass


def render_home_away_maps(
    shots: list[ShotPoint],
    *,
    away_org_id: str,
    home_org_id: str,
    output_dir: Path,
    stamp: str | None = None,
    away_logo_path: str | Path | None = None,
    home_logo_path: str | Path | None = None,
) -> tuple[Path, Path]:
    """Write versioned PNGs for Captivate plus stable copies for operators.

    Captivate caches image textures by path string, so each rebuild must push a
    new absolute path. Stable ``away_shot_map.png`` / ``home_shot_map.png`` are
    overwritten for browsing; older ``*_shot_map_*.png`` files are pruned.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    token = stamp if stamp is not None else str(time.time_ns() // 1_000_000)
    away_versioned = output_dir / f"away_shot_map_{token}.png"
    home_versioned = output_dir / f"home_shot_map_{token}.png"
    away_stable = output_dir / "away_shot_map.png"
    home_stable = output_dir / "home_shot_map.png"

    render_made_shot_png(
        shots,
        org_id=away_org_id,
        out_path=away_versioned,
        dot_color=AWAY_DOT,
        logo_path=away_logo_path,
    )
    render_made_shot_png(
        shots,
        org_id=home_org_id,
        out_path=home_versioned,
        dot_color=HOME_DOT,
        logo_path=home_logo_path,
    )
    shutil.copy2(away_versioned, away_stable)
    shutil.copy2(home_versioned, home_stable)
    prune_old_shot_maps(
        output_dir, {away_versioned, home_versioned, away_stable, home_stable}
    )
    return away_versioned, home_versioned
