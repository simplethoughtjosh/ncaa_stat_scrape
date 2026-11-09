"""Resolve local player headshots from ~/SSC/<sport>/<team>/<player>.jpg."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

DEFAULT_PHOTO_ROOT = Path.home() / "SSC"

# Titler's bundled player silhouette. Used when a roster jpg is missing.
NEWBLUE_SILHOUETTE = Path(
    "/Library/Application Support/NewBlue/Titler Content/Resources/"
    "Library/Project Templates/Sports/Carbon/Textures/Headshot.png"
)
_SILHOUETTE_NAMES = ("Headshot.png", "headshot_silhouette.png", "silhouette.png", "silhouette.jpg")

# NCAA box scores shorten a few school names. Folder names use the longer form.
# "St." is Saint at the start of a name (St. Leo) and State at the end (New Mexico St.).
_TOKEN_EXPAND = {
    "atl": "atlantic",
    "fla": "florida",
}


def name_key(value: str) -> str:
    """Fold a team or player name into comparable words.

    Accents, apostrophes, and punctuation drop out, so ``Trey O'Neil`` and
    ``trey_oneil.jpg`` land on the same key, and ``Tin Tikveša`` matches
    ``tin_tikveša.jpg``.
    """
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = text.replace("'", "").replace("’", "").replace("`", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    parts = text.split()
    tokens = []
    for index, token in enumerate(parts):
        if token == "st":
            token = "state" if index == len(parts) - 1 else "saint"
        else:
            token = _TOKEN_EXPAND.get(token, token)
        tokens.append(token)
    return " ".join(tokens)


def silhouette_path(root: Path | None = None, sport_code: str = "WBB") -> str:
    """Placeholder headshot. Women's games use a female silhouette; men's use the male one."""
    code = (sport_code or "WBB").upper()
    base = Path(root) if root is not None else DEFAULT_PHOTO_ROOT
    gendered = (
        ("Headshot-WBB.png", "headshot_female.png")
        if code == "WBB"
        else ("Headshot-MBB.png", "headshot_male.png")
    )
    for name in gendered + _SILHOUETTE_NAMES:
        candidate = base / name
        if candidate.is_file():
            return str(candidate.resolve())
    bundled = _bundled_silhouette(code)
    if bundled is not None:
        return str(bundled)
    return ""


def _bundled_silhouette(sport_code: str) -> Path | None:
    assets = Path(__file__).resolve().parent / "assets"
    if sport_code == "WBB":
        female = assets / "headshot_female.png"
        if female.is_file():
            return female.resolve()
    if NEWBLUE_SILHOUETTE.is_file():
        return NEWBLUE_SILHOUETTE
    return None


def player_photo_path(
    sport_code: str,
    team_name: str,
    player_name: str,
    *,
    root: Path | None = None,
) -> str:
    """Absolute path to the player's jpg, or the silhouette when no file matches."""
    found = _find_player_photo(sport_code, team_name, player_name, root=root)
    if found:
        return found
    return silhouette_path(root, sport_code)


def team_logo_path(
    sport_code: str,
    team_name: str,
    *,
    root: Path | None = None,
) -> str:
    """Absolute path to ``logo.png`` (or ``logo.jpg``) in the team's photo folder."""
    if not sport_code or not team_name:
        return ""
    base = Path(root) if root is not None else DEFAULT_PHOTO_ROOT
    team_dir = _match_team_dir(base / sport_code.upper(), team_name)
    if team_dir is None:
        return ""
    for name in ("logo.png", "logo.jpg", "logo.jpeg", "Logo.png", "Logo.jpg"):
        candidate = team_dir / name
        if candidate.is_file():
            return str(candidate.resolve())
    return ""


def _find_player_photo(
    sport_code: str,
    team_name: str,
    player_name: str,
    *,
    root: Path | None,
) -> str:
    if not sport_code or not team_name or not player_name:
        return ""
    base = Path(root) if root is not None else DEFAULT_PHOTO_ROOT
    team_dir = _match_team_dir(base / sport_code.upper(), team_name)
    if team_dir is None:
        return ""
    wanted = name_key(player_name)
    if not wanted:
        return ""
    for path in sorted(team_dir.iterdir()):
        if not path.is_file() or path.suffix.casefold() not in {".jpg", ".jpeg", ".png"}:
            continue
        if name_key(path.stem) == wanted:
            return str(path.resolve())
    return ""


def _match_team_dir(sport_dir: Path, team_name: str) -> Path | None:
    if not sport_dir.is_dir():
        return None
    wanted = name_key(team_name)
    if not wanted:
        return None
    best: Path | None = None
    best_score = 0
    for folder in sport_dir.iterdir():
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        got = name_key(folder.name)
        if not got:
            continue
        if got == wanted:
            score = 1000 + len(got)
        elif _same_words_abbreviated(got, wanted):
            # "Palm Beach Atl." vs folder "Palm Beach Atlantic"
            score = 700 + min(len(got), len(wanted))
        else:
            continue
        if score > best_score:
            best = folder
            best_score = score
    return best


def _same_words_abbreviated(left: str, right: str) -> bool:
    """True when one name only shortens the last word, rather than naming another school."""
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if not longer.startswith(shorter):
        return False
    rest = longer[len(shorter) :]
    return bool(rest) and not rest.startswith(" ")
