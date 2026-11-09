"""Call SSC Scoreboard Automation to build home/away team PNGs via Photoshop."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from college_sports_scraper.basketball.photo_fetch import (
    USER_AGENT,
    _ddg_search_session,
    unwrap_search_href,
)
from college_sports_scraper.basketball.photos import (
    DEFAULT_PHOTO_ROOT,
    _match_team_dir,
    name_key,
    team_logo_path,
)
from college_sports_scraper.ncaa.live_state import BasketballLiveState

logger = logging.getLogger(__name__)

DEFAULT_SSC_ROOT = Path.home() / "SSC Scoreboard Automation"
DEFAULT_PYTHON = DEFAULT_SSC_ROOT / ".venv" / "bin" / "python"
DEFAULT_SCRIPT = DEFAULT_SSC_ROOT / "export_scoreboard.py"
DEFAULT_OUTPUT_DIR = (
    Path.home()
    / "Documents"
    / "Title_Stuff"
    / "textures"
    / "2019 Scoreboards"
    / "Basketball"
    / "teams"
)
DEFAULT_COLORS_PATH = Path(__file__).resolve().parents[3] / "config" / "team_colors.yaml"
DEFAULT_COLOR_CACHE = Path.home() / ".college_sports_scraper" / "team_color_cache.json"

HEX_RE = re.compile(r"^#?(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
HEX_FIND_RE = re.compile(r"#([0-9A-Fa-f]{6})\b")
# Prefer structured color listings over random CSS hexes in the page chrome.
HEX_LABELED_RE = re.compile(
    r"(?:Hex(?:\s*color(?:\s*code)?)?|HEX)\s*[:=]\s*#([0-9A-Fa-f]{6})\b",
    re.I,
)
COLORBLOCK_RE = re.compile(
    r'class="[^"]*colorblock[^"]*"[^>]*style="[^"]*background-color:\s*#([0-9A-Fa-f]{6})',
    re.I,
)
FALLBACK_HOME = "#C8102E"
FALLBACK_AWAY = "#1A3A6B"
_SKIP_HEX = frozenset(
    {
        "#FFFFFF",
        "#000000",
        "#FEFEFE",
        "#F5F5F5",
        "#111111",
        "#222222",
    }
)
# Prefer these result hosts when searching for athletics brand colors.
_COLOR_HOST_SCORE = {
    "teamcolorcodes.com": 100,
    "sportcolorcodes.com": 90,
    "usteamcolors.com": 80,
    "brandcolors.net": 50,
}


class ScoreboardExportError(RuntimeError):
    """Photoshop export failed or returned a non-zero exit."""


def normalize_hex(value: str) -> str:
    if not HEX_RE.match(value or ""):
        raise ScoreboardExportError(f"Invalid hex color: {value!r} (expected #RGB or #RRGGBB)")
    cleaned = value[1:] if value.startswith("#") else value
    if len(cleaned) == 3:
        cleaned = "".join(ch * 2 for ch in cleaned)
    return f"#{cleaned.upper()}"


def safe_filename(name: str) -> str:
    cleaned = (name or "").strip()
    cleaned = re.sub(r'[\\/:\0<>|"?*]', "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "team"


def find_existing_scoreboard_png(
    team_name: str,
    output_dir: Path | None = None,
) -> Path | None:
    """Return an existing team PNG under the scoreboard library, if any.

    Prefers an exact ``{safe_filename}.png`` hit, then a ``name_key`` match so
    ``Penn St.`` can reuse ``Penn St.png``.
    """
    out_dir = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    if not team_name or not out_dir.is_dir():
        return None
    exact = out_dir / f"{safe_filename(team_name)}.png"
    if exact.is_file():
        return exact.resolve()
    wanted = name_key(team_name)
    if not wanted:
        return None
    for path in sorted(out_dir.glob("*.png")):
        if name_key(path.stem) == wanted:
            return path.resolve()
    return None


def team_display_name(sport_code: str, ncaa_name: str, *, photo_root: Path | None = None) -> str:
    """Prefer the ~/SSC folder name when it matches; otherwise the NCAA box-score name."""
    if not ncaa_name:
        return ""
    base = Path(photo_root) if photo_root is not None else DEFAULT_PHOTO_ROOT
    folder = _match_team_dir(base / (sport_code or "WBB").upper(), ncaa_name)
    return folder.name if folder is not None else ncaa_name.strip()


def load_team_colors(path: Path | None = None) -> dict[str, str]:
    """Optional config/team_colors.yaml: ``Team Name: "#RRGGBB"``."""
    colors_path = Path(path) if path is not None else DEFAULT_COLORS_PATH
    if not colors_path.is_file():
        return {}
    try:
        import yaml  # type: ignore
    except ImportError:
        logger.warning("PyYAML not installed; ignoring %s", colors_path)
        return {}
    try:
        data = yaml.safe_load(colors_path.read_text()) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read team colors %s: %s", colors_path, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, str):
            try:
                out[key.casefold()] = normalize_hex(value)
            except ScoreboardExportError:
                continue
    return out


def dominant_hex_from_image(path: Path) -> str | None:
    """Pick a saturated non-near-white/black color from a logo file."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
            rgba.thumbnail((64, 64))
            counts: dict[tuple[int, int, int], int] = {}
            for r, g, b, a in rgba.getdata():
                if a < 128:
                    continue
                if max(r, g, b) < 30 or min(r, g, b) > 230:
                    continue
                # Quantize slightly so GIF dithering collapses.
                key = (r // 16 * 16, g // 16 * 16, b // 16 * 16)
                counts[key] = counts.get(key, 0) + 1
    except Exception:  # noqa: BLE001
        return None
    if not counts:
        return None
    r, g, b = max(counts.items(), key=lambda item: item[1])[0]
    return f"#{r:02X}{g:02X}{b:02X}"


def dominant_hex_from_url(url: str, *, cache_dir: Path | None = None) -> str | None:
    if not url:
        return None
    cache = cache_dir or (Path.home() / ".college_sports_scraper" / "logo_cache")
    cache.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^a-zA-Z0-9._-]+", "_", url.rsplit("/", 1)[-1]) or "logo.gif"
    dest = cache / name
    try:
        if not dest.is_file():
            with urlopen(url, timeout=15) as response:  # noqa: S310 - NCAA logo CDN
                dest.write_bytes(response.read())
        return dominant_hex_from_image(dest)
    except Exception as exc:  # noqa: BLE001
        logger.info("Could not sample logo color from %s: %s", url, exc)
        return None


def color_search_queries(team_name: str) -> list[str]:
    """Web queries used to find athletics brand hex colors."""
    name = (team_name or "").strip()
    if not name:
        return []
    return [
        f"{name} Athletics Hex colors",
        f"{name} athletics color codes hex",
        f"{name} team color codes hex",
    ]


def _load_color_cache(path: Path | None = None) -> dict[str, Any]:
    cache_path = Path(path) if path is not None else DEFAULT_COLOR_CACHE
    if not cache_path.is_file():
        return {}
    try:
        data = json.loads(cache_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_color_cache(entry_key: str, hex_color: str, source: str, *, path: Path | None = None) -> None:
    cache_path = Path(path) if path is not None else DEFAULT_COLOR_CACHE
    data = _load_color_cache(cache_path)
    data[entry_key] = {"hex": hex_color, "source": source}
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data, indent=2, sort_keys=True))
    except OSError as exc:
        logger.info("Could not write team color cache: %s", exc)


def _is_usable_brand_hex(value: str) -> bool:
    try:
        hex_color = normalize_hex(value)
    except ScoreboardExportError:
        return False
    if hex_color in _SKIP_HEX:
        return False
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    # Skip near-white / near-black / very pale golds that read as background.
    if max(r, g, b) < 40 or min(r, g, b) > 235:
        return False
    return True


def extract_hex_colors(text: str) -> list[str]:
    """Ordered unique usable hex colors from a page or search snippet.

    Prefers labeled ``Hex: #…`` / ``colorblock`` values so site chrome CSS is ignored.
    """
    found: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        hex_color = f"#{raw.upper()}"
        if hex_color in seen or not _is_usable_brand_hex(hex_color):
            return
        seen.add(hex_color)
        found.append(hex_color)

    for match in COLORBLOCK_RE.finditer(text or ""):
        _add(match.group(1))
    for match in HEX_LABELED_RE.finditer(text or ""):
        _add(match.group(1))
    # Snippets / plain pages: only fall back to bare #RRGGBB when nothing labeled.
    if not found:
        for match in HEX_FIND_RE.finditer(text or ""):
            _add(match.group(1))
    return found


def pick_primary_hex(hexes: list[str]) -> str | None:
    """Prefer a strong primary (often the first non-gold color on color-code sites)."""
    if not hexes:
        return None

    def _score(item: tuple[int, str]) -> tuple[int, int, int]:
        index, hex_color = item
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        # Golds/yellows are common secondary athletics colors — demote them.
        is_gold = r > 180 and g > 140 and b < 80
        saturation = max(r, g, b) - min(r, g, b)
        # Keep early listed colors ahead of later secondaries when scores tie.
        return (0 if is_gold else 1, saturation, -index)

    ranked = list(enumerate(hexes[:8]))
    return max(ranked, key=_score)[1]


def extract_color_page_urls(html: str, *, base_url: str = "https://html.duckduckgo.com/") -> list[str]:
    """Rank search-result links for athletics color pages."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "lxml")
    ranked: list[tuple[int, str]] = []
    seen: set[str] = set()
    for tag in soup.find_all("a", href=True):
        absolute = unwrap_search_href(tag["href"], base_url=base_url)
        if not absolute or absolute in seen:
            continue
        host = urlparse(absolute).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        score = _COLOR_HOST_SCORE.get(host, 0)
        text = f"{tag.get_text(' ', strip=True)} {absolute}".casefold()
        if "hex" in text or "color code" in text or "brand" in text:
            score += 10
        if "athletics" in text or "team color" in text:
            score += 5
        if score <= 0:
            continue
        seen.add(absolute)
        ranked.append((score, absolute))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [url for _score, url in ranked]


def _fetch_text(url: str, *, session: Any | None = None, timeout: float = 20.0) -> str:
    if session is not None:
        response = session.get(url, timeout=timeout)
        return getattr(response, "text", "") or ""
    request = Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    with urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read().decode("utf-8", errors="ignore")


def search_athletics_hex_color(
    team_name: str,
    *,
    session: Any | None = None,
    cache_path: Path | None = None,
    use_cache: bool = True,
) -> str | None:
    """DuckDuckGo ``{team} Athletics Hex colors`` → primary brand hex."""
    name = (team_name or "").strip()
    if not name:
        return None
    key = name.casefold()
    if use_cache:
        cached = _load_color_cache(cache_path).get(key)
        if isinstance(cached, dict) and cached.get("hex"):
            try:
                return normalize_hex(str(cached["hex"]))
            except ScoreboardExportError:
                pass

    sess = session or _ddg_search_session()
    page_urls: list[str] = []
    snippet_hexes: list[str] = []
    for query in color_search_queries(name):
        print(f"  Searching team colors: {query}")
        try:
            response = sess.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                timeout=30,
            )
            status = int(getattr(response, "status_code", 0) or 0)
            if status == 202 or status >= 400:
                print(f"  ! Color search blocked or failed (HTTP {status})")
                continue
            html = getattr(response, "text", "") or ""
        except Exception as exc:  # noqa: BLE001
            print(f"  ! Color search failed: {exc}")
            continue
        snippet_hexes.extend(extract_hex_colors(html))
        for url in extract_color_page_urls(html):
            if url not in page_urls:
                page_urls.append(url)

    for url in page_urls[:5]:
        try:
            page_text = _fetch_text(url, session=sess)
        except Exception as exc:  # noqa: BLE001
            logger.info("Color page fetch failed for %s: %s", url, exc)
            continue
        picked = pick_primary_hex(extract_hex_colors(page_text))
        if picked:
            print(f"  Team color for {name}: {picked} (from {url})")
            _save_color_cache(key, picked, url, path=cache_path)
            return picked

    picked = pick_primary_hex(snippet_hexes)
    if picked:
        print(f"  Team color for {name}: {picked} (from search snippets)")
        _save_color_cache(key, picked, "search-snippets", path=cache_path)
        return picked
    return None


def resolve_team_color(
    team_name: str,
    *,
    sport_code: str = "WBB",
    logo_url: str | None = None,
    override: str | None = None,
    colors: dict[str, str] | None = None,
    fallback: str = FALLBACK_HOME,
    photo_root: Path | None = None,
    search: bool = True,
    search_fn: Any | None = None,
) -> str:
    if override:
        return normalize_hex(override)
    table = colors if colors is not None else load_team_colors()
    if team_name and team_name.casefold() in table:
        return table[team_name.casefold()]
    display = team_display_name(sport_code, team_name, photo_root=photo_root)
    if display and display.casefold() in table:
        return table[display.casefold()]
    if search:
        finder = search_fn or search_athletics_hex_color
        for candidate in (display, team_name):
            if not candidate:
                continue
            found = finder(candidate)
            if found:
                return normalize_hex(found)
    local_logo = team_logo_path(sport_code, team_name, root=photo_root)
    if local_logo:
        sampled = dominant_hex_from_image(Path(local_logo))
        if sampled:
            return sampled
    if logo_url:
        sampled = dominant_hex_from_url(logo_url)
        if sampled:
            return sampled
    return normalize_hex(fallback)


def export_scoreboard_pngs(
    *,
    home: str,
    home_color: str,
    away: str,
    away_color: str,
    python_bin: Path | None = None,
    script: Path | None = None,
    output_dir: Path | None = None,
    trim: bool = True,
    timeout: float = 300.0,
    skip_existing: bool = True,
) -> tuple[Path, Path]:
    """Run SSC Scoreboard Automation. Raises ScoreboardExportError on failure.

    When ``skip_existing`` is true (default), teams that already have a PNG in
    the Title_Stuff scoreboard library are not regenerated.
    """
    py = Path(python_bin) if python_bin else DEFAULT_PYTHON
    entry = Path(script) if script else DEFAULT_SCRIPT
    out_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR

    home_existing = find_existing_scoreboard_png(home, out_dir) if skip_existing else None
    away_existing = find_existing_scoreboard_png(away, out_dir) if skip_existing else None
    if home_existing:
        print(f"Skipping home scoreboard (already exists): {home_existing}")
    if away_existing:
        print(f"Skipping away scoreboard (already exists): {away_existing}")
    if home_existing and away_existing:
        return home_existing, away_existing

    if not py.is_file():
        raise ScoreboardExportError(f"SSC Scoreboard Automation Python not found: {py}")
    if not entry.is_file():
        raise ScoreboardExportError(f"export_scoreboard.py not found: {entry}")

    # SSC still expects both sides; it skips whichever PNG already exists when
    # --skip-existing is passed. Placeholder colors for a skipped side are unused.
    home_hex = normalize_hex(home_color) if home_existing is None else FALLBACK_HOME
    away_hex = normalize_hex(away_color) if away_existing is None else FALLBACK_AWAY
    cmd = [
        str(py),
        str(entry),
        "--home",
        home,
        "--home-color",
        home_hex,
        "--away",
        away,
        "--away-color",
        away_hex,
    ]
    if not trim:
        cmd.append("--no-trim")
    if skip_existing:
        cmd.append("--skip-existing")

    logger.info("Running scoreboard export: %s", " ".join(cmd))
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ScoreboardExportError(f"Photoshop automation timed out after {timeout:.0f}s") from exc
    except OSError as exc:
        raise ScoreboardExportError(f"Could not start scoreboard export: {exc}") from exc

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if stdout:
        print(stdout)
    if completed.returncode != 0:
        detail = stderr or stdout or f"exit {completed.returncode}"
        raise ScoreboardExportError(
            f"Scoreboard export failed (exit {completed.returncode}): {detail}"
        )

    home_path = home_existing or (out_dir / f"{safe_filename(home)}.png")
    away_path = away_existing or (out_dir / f"{safe_filename(away)}.png")
    missing = [str(p) for p in (home_path, away_path) if not Path(p).is_file()]
    if missing:
        raise ScoreboardExportError(
            "Scoreboard export reported success but PNG(s) missing: " + ", ".join(missing)
        )
    return Path(home_path), Path(away_path)


def export_scoreboard_for_state(
    state: BasketballLiveState,
    *,
    home_color: str | None = None,
    away_color: str | None = None,
    colors_path: Path | None = None,
    photo_root: Path | None = None,
    output_dir: Path | None = None,
    skip_existing: bool = True,
    **kwargs: Any,
) -> dict[str, str]:
    """Resolve names/colors from live state and export both team PNGs."""
    if not state.away or not state.home:
        raise ScoreboardExportError("Live state has no away/home teams yet")
    sport = state.sport_code or "WBB"
    home_name = team_display_name(sport, state.home.name or "", photo_root=photo_root)
    away_name = team_display_name(sport, state.away.name or "", photo_root=photo_root)
    if not home_name or not away_name:
        raise ScoreboardExportError("Missing home or away team name")

    out_dir = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    home_existing = find_existing_scoreboard_png(home_name, out_dir) if skip_existing else None
    away_existing = find_existing_scoreboard_png(away_name, out_dir) if skip_existing else None

    colors = load_team_colors(colors_path) if (home_existing is None or away_existing is None) else {}
    home_hex = ""
    away_hex = ""
    if home_existing is None:
        home_hex = resolve_team_color(
            home_name,
            sport_code=sport,
            logo_url=state.home.logo_url,
            override=home_color,
            colors=colors,
            fallback=FALLBACK_HOME,
            photo_root=photo_root,
        )
    if away_existing is None:
        away_hex = resolve_team_color(
            away_name,
            sport_code=sport,
            logo_url=state.away.logo_url,
            override=away_color,
            colors=colors,
            fallback=FALLBACK_AWAY,
            photo_root=photo_root,
        )

    home_path, away_path = export_scoreboard_pngs(
        home=home_name,
        home_color=home_hex or FALLBACK_HOME,
        away=away_name,
        away_color=away_hex or FALLBACK_AWAY,
        output_dir=out_dir,
        skip_existing=skip_existing,
        **kwargs,
    )
    return {
        "home": home_name,
        "home_color": home_hex or "(existing)",
        "home_png": str(home_path),
        "home_skipped": "1" if home_existing else "0",
        "away": away_name,
        "away_color": away_hex or "(existing)",
        "away_png": str(away_path),
        "away_skipped": "1" if away_existing else "0",
    }
