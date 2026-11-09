"""Find and download missing team headshots/logos via Sidearm roster pages.

On connect, when ~/SSC photos are incomplete, web-search for a Sidearm roster URL,
confirm player-name overlap with the NCAA box score, then download via roster_headshots.
"""

from __future__ import annotations

import logging
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from college_sports_scraper.basketball.photos import (
    DEFAULT_PHOTO_ROOT,
    _find_player_photo,
    name_key,
    team_logo_path,
)
from college_sports_scraper.ncaa.live_state import BasketballLiveState, LiveTeam

logger = logging.getLogger(__name__)

PER_TEAM_TIMEOUT_SECONDS = 120.0
MAX_CANDIDATE_URLS = 5
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_SIDEARM_ROSTER_PATH = re.compile(
    r"^/sports/(?P<sport>womens|mens)-basketball/roster(?:/(?P<rest>.*))?$",
    re.IGNORECASE,
)

# Hosts that show up in search results but are never Sidearm athletics sites.
_SKIP_SEARCH_HOSTS = frozenset(
    {
        "duckduckgo.com",
        "google.com",
        "bing.com",
        "yahoo.com",
        "instagram.com",
        "facebook.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "wikipedia.org",
        "eurobasket.com",
        "usbasket.com",
        "maxpreps.com",
        "espn.com",
        "ncaa.com",
        "linkedin.com",
        "on3.com",
        "herhoopstats.com",
        "d3hoops.com",
    }
)


def roster_headshots_available() -> bool:
    try:
        import roster_headshots  # noqa: F401

        return True
    except ImportError:
        return False


def overlap_threshold(ncaa_named_count: int) -> int:
    """Minimum Sidearm↔NCAA name matches required to accept a roster URL."""
    if ncaa_named_count <= 0:
        return 1
    # Cap at roster size so small teams can still confirm (all names must match).
    return min(ncaa_named_count, max(5, math.ceil(0.5 * ncaa_named_count)))


def roster_name_overlap(ncaa_names: list[str], sidearm_names: list[str]) -> int:
    ncaa_keys = {name_key(n) for n in ncaa_names if name_key(n)}
    sidearm_keys = {name_key(n) for n in sidearm_names if name_key(n)}
    return len(ncaa_keys & sidearm_keys)


def overlap_meets_threshold(matched: int, ncaa_named_count: int) -> bool:
    return matched >= overlap_threshold(ncaa_named_count)


def team_needs_photos(
    sport_code: str,
    team: LiveTeam,
    *,
    root: Path | None = None,
) -> bool:
    """True when logo is missing or fewer than half the roster have headshot files."""
    if not team.name:
        return False
    names = [p.name for p in team.roster() if p.name]
    if not names:
        return False
    logo_ok = bool(team_logo_path(sport_code, team.name, root=root))
    with_photo = sum(
        1
        for name in names
        if _find_player_photo(sport_code, team.name, name, root=root)
    )
    if not logo_ok:
        return True
    return with_photo * 2 < len(names)


def team_photo_dir(
    sport_code: str,
    team_name: str,
    *,
    root: Path | None = None,
) -> Path:
    """Directory where downloads are written: ~/SSC/<sport>/<team>/."""
    base = Path(root) if root is not None else DEFAULT_PHOTO_ROOT
    return base / sport_code.upper() / team_name


def sport_roster_segment(sport_code: str) -> str:
    return "mens-basketball" if (sport_code or "").upper() == "MBB" else "womens-basketball"


def search_query_for_team(team_name: str, sport_code: str) -> str:
    """Primary search query (kept for callers/tests)."""
    return search_queries_for_team(team_name, sport_code)[0]


def search_queries_for_team(team_name: str, sport_code: str) -> list[str]:
    """Google-style queries: ``John Carroll womens basketball roster``.

    No quotes; ``womens``/``mens`` without apostrophe. One-word NCAA names like
    ``Calvin`` also try a ``University`` suffix as a fallback.
    """
    gender = "mens" if (sport_code or "").upper() == "MBB" else "womens"
    name = (team_name or "").strip()
    queries = [f"{name} {gender} basketball roster"]
    lower = name.casefold()
    if name and " " not in name and not any(
        token in lower
        for token in ("university", "college", "state", "tech", "institute", "academy")
    ):
        queries.append(f"{name} University {gender} basketball roster")
    return queries


def unwrap_search_href(href: str, *, base_url: str = "https://html.duckduckgo.com/") -> str:
    """Resolve DDG redirect / protocol-relative hrefs to the destination URL."""
    absolute = urljoin(base_url, (href or "").strip())
    if absolute.startswith("//"):
        absolute = "https:" + absolute
    parsed = urlparse(absolute)
    host = (parsed.netloc or "").lower()
    if "duckduckgo.com" in host:
        qs = parse_qs(parsed.query)
        if "uddg" in qs:
            return unquote(qs["uddg"][0])
    return absolute


def normalize_sidearm_roster_url(url: str, *, sport_code: str | None = None) -> str | None:
    """Return a canonical Sidearm basketball roster index URL, or None."""
    raw = unwrap_search_href(url) if url else ""
    raw = (raw or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    match = _SIDEARM_ROSTER_PATH.match(parsed.path or "")
    if not match:
        return None
    sport = match.group("sport").lower()
    if sport_code:
        if sport_code.upper() == "WBB" and sport != "womens":
            return None
        if sport_code.upper() == "MBB" and sport != "mens":
            return None
    # Prefer the roster index (drop /player-slug/id).
    path = f"/sports/{sport}-basketball/roster"
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return urlunparse((parsed.scheme, host, path, "", "", ""))


def extract_sidearm_roster_urls(
    html: str,
    *,
    sport_code: str,
    base_url: str | None = None,
) -> list[str]:
    """Pull candidate Sidearm roster index URLs from search-result HTML."""
    soup = BeautifulSoup(html or "", "lxml")
    seen: set[str] = set()
    out: list[str] = []
    base = base_url or "https://html.duckduckgo.com/"
    for tag in soup.find_all("a", href=True):
        absolute = unwrap_search_href(tag["href"], base_url=base)
        normalized = normalize_sidearm_roster_url(absolute, sport_code=sport_code)
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def extract_candidate_hosts(
    html: str,
    *,
    base_url: str | None = None,
) -> list[str]:
    """Athletics hostnames from search results (for probing /sports/.../roster)."""
    soup = BeautifulSoup(html or "", "lxml")
    base = base_url or "https://html.duckduckgo.com/"
    hosts: list[str] = []
    seen: set[str] = set()
    for tag in soup.find_all("a", href=True):
        absolute = unwrap_search_href(tag["href"], base_url=base)
        parsed = urlparse(absolute)
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if not host or host in seen:
            continue
        if any(host == skip or host.endswith("." + skip) for skip in _SKIP_SEARCH_HOSTS):
            continue
        seen.add(host)
        hosts.append(host)
    return hosts


def probe_sidearm_roster_url(
    host: str,
    sport_code: str,
    *,
    session: requests.Session | None = None,
) -> str | None:
    """GET /sports/<gender>-basketball/roster on a host; return canonical URL if it looks real.

    University .edu hubs (e.g. calvin.edu/athletics) often only link out to the
    real Sidearm site (calvinknights.com) — follow those when the direct path 404s.
    """
    sess = session or requests.Session()
    sess.headers.setdefault("User-Agent", USER_AGENT)
    segment = sport_roster_segment(sport_code)
    host = host.lower().removeprefix("www.")
    direct = _probe_roster_path(host, segment, sport_code, sess)
    if direct:
        return direct
    return _probe_via_athletics_hub(host, segment, sport_code, sess)


def _probe_roster_path(
    host: str,
    segment: str,
    sport_code: str,
    sess: requests.Session,
) -> str | None:
    for scheme in ("https", "http"):
        url = f"{scheme}://{host}/sports/{segment}/roster"
        try:
            response = sess.get(url, timeout=15, allow_redirects=True)
        except requests.RequestException:
            continue
        if response.status_code != 200 or len(response.text) < 2000:
            continue
        normalized = normalize_sidearm_roster_url(str(response.url), sport_code=sport_code)
        if normalized:
            return normalized
        normalized = normalize_sidearm_roster_url(url, sport_code=sport_code)
        if normalized and (
            "sidearm" in response.text.casefold() or "roster" in response.text.casefold()
        ):
            return normalized
    return None


def _probe_via_athletics_hub(
    host: str,
    segment: str,
    sport_code: str,
    sess: requests.Session,
) -> str | None:
    """Follow links from /athletics (or home) to a Sidearm basketball roster."""
    gender = "womens" if segment.startswith("womens") else "mens"
    for path in ("/athletics", "/"):
        hub = f"https://{host}{path}"
        try:
            response = sess.get(hub, timeout=15, allow_redirects=True)
        except requests.RequestException:
            continue
        if response.status_code != 200 or len(response.text) < 500:
            continue
        soup = BeautifulSoup(response.text, "lxml")
        linked_hosts: list[str] = []
        seen_hosts: set[str] = set()
        for tag in soup.find_all("a", href=True):
            absolute = urljoin(str(response.url), tag["href"])
            # Sport page without /roster → try appending it.
            parsed = urlparse(absolute)
            path_l = (parsed.path or "").rstrip("/").lower()
            if path_l.endswith(f"/sports/{segment}") or path_l.endswith(
                f"/sports/{gender}-basketball"
            ):
                candidate = normalize_sidearm_roster_url(
                    absolute.rstrip("/") + "/roster", sport_code=sport_code
                )
                if candidate:
                    hit = _probe_roster_path(
                        urlparse(candidate).netloc, segment, sport_code, sess
                    )
                    if hit:
                        return hit
            linked = (parsed.netloc or "").lower().removeprefix("www.")
            if (
                linked
                and linked != host
                and linked not in seen_hosts
                and not any(
                    linked == skip or linked.endswith("." + skip) for skip in _SKIP_SEARCH_HOSTS
                )
            ):
                seen_hosts.add(linked)
                linked_hosts.append(linked)
        for linked in linked_hosts:
            hit = _probe_roster_path(linked, segment, sport_code, sess)
            if hit:
                return hit
    return None


def _ddg_search_session() -> Any:
    """Chrome-impersonated session — plain requests gets DDG bot challenges (HTTP 202)."""
    try:
        from curl_cffi import requests as creq

        return creq.Session(impersonate="chrome")
    except Exception:  # noqa: BLE001 - fall back to plain requests
        sess = requests.Session()
        sess.headers["User-Agent"] = USER_AGENT
        return sess


def search_roster_candidate_urls(
    team_name: str,
    sport_code: str,
    *,
    session: Any | None = None,
    limit: int = MAX_CANDIDATE_URLS,
) -> list[str]:
    """DuckDuckGo HTML search → Sidearm roster URLs (direct hits + host probes)."""
    sess = session or _ddg_search_session()
    probe_sess = requests.Session()
    probe_sess.headers["User-Agent"] = USER_AGENT
    found: list[str] = []
    seen: set[str] = set()
    hosts_to_probe: list[str] = []
    host_seen: set[str] = set()

    def _add(url: str | None) -> None:
        if url and url not in seen:
            seen.add(url)
            found.append(url)

    for query in search_queries_for_team(team_name, sport_code):
        if len(found) >= limit:
            break
        print(f"  Searching for roster: {query}")
        try:
            response = sess.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                timeout=30,
            )
            status = getattr(response, "status_code", 0)
            if status == 202 or status >= 400:
                print(f"  ! Search blocked or failed (HTTP {status})")
                continue
        except Exception as exc:  # noqa: BLE001 - curl_cffi or requests
            print(f"  ! Search failed: {exc}")
            continue
        for url in extract_sidearm_roster_urls(response.text, sport_code=sport_code):
            _add(url)
        for host in extract_candidate_hosts(response.text):
            if host not in host_seen:
                host_seen.add(host)
                hosts_to_probe.append(host)

    # NCAA short names often surface the athletics homepage, not /roster — probe it.
    for host in hosts_to_probe:
        if len(found) >= limit:
            break
        probed = probe_sidearm_roster_url(host, sport_code, session=probe_sess)
        if probed:
            print(f"  Probed athletics host → {probed}")
            _add(probed)

    return found[:limit]


def confirm_roster_url(
    roster_url: str,
    ncaa_names: list[str],
    *,
    discover_players_fn: Any | None = None,
) -> tuple[bool, int]:
    """Fetch Sidearm player list and compare to NCAA names. Returns (ok, matched)."""
    discover = discover_players_fn
    if discover is None:
        from roster_headshots.scrape import discover_players as discover

    players = discover(roster_url)
    sidearm_names = [p.name for p in players if getattr(p, "name", None)]
    matched = roster_name_overlap(ncaa_names, sidearm_names)
    ok = overlap_meets_threshold(matched, len([n for n in ncaa_names if name_key(n)]))
    return ok, matched


def download_roster_photos(roster_url: str, output_dir: Path) -> int:
    """Run roster_headshots into output_dir (players + coach + logo). Returns exit code."""
    from roster_headshots import main as roster_main

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Downloading photos → {output_dir}")
    return int(
        roster_main(
            [
                roster_url,
                "-o",
                str(output_dir),
            ]
        )
    )


def ensure_team_photos(
    state: BasketballLiveState,
    *,
    root: Path | None = None,
    per_team_timeout: float = PER_TEAM_TIMEOUT_SECONDS,
    search_fn: Any | None = None,
    confirm_fn: Any | None = None,
    download_fn: Any | None = None,
) -> dict[str, Any]:
    """Blocking: for each side missing photos, search → confirm → download.

    Failures are logged and skipped so live control can continue with silhouettes.
    """
    summary: dict[str, Any] = {"ok": True, "teams": [], "skipped": False}
    if not roster_headshots_available() and download_fn is None and confirm_fn is None:
        print(
            "Photos incomplete but roster-headshots is not installed; "
            'run: pip install -e "../player-photos"'
        )
        summary["ok"] = False
        summary["skipped"] = True
        summary["error"] = "roster_headshots_missing"
        return summary

    search = search_fn or search_roster_candidate_urls
    confirm = confirm_fn or confirm_roster_url
    download = download_fn or download_roster_photos

    for team in (state.away, state.home):
        if team is None or not team.name:
            continue
        entry: dict[str, Any] = {"team": team.name, "fetched": False}
        if not team_needs_photos(state.sport_code, team, root=root):
            entry["status"] = "present"
            summary["teams"].append(entry)
            continue

        print(f"\nMissing photos/logo for {team.name} — looking up athletics roster…")
        ncaa_names = [p.name for p in team.roster() if p.name]
        out_dir = team_photo_dir(state.sport_code, team.name, root=root)
        deadline = time.monotonic() + per_team_timeout

        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("per-team photo budget exhausted")

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    _fetch_one_team,
                    team_name=team.name,
                    sport_code=state.sport_code,
                    ncaa_names=ncaa_names,
                    out_dir=out_dir,
                    search=search,
                    confirm=confirm,
                    download=download,
                    deadline=deadline,
                )
                try:
                    result = future.result(timeout=max(1.0, remaining))
                except FuturesTimeout as exc:
                    future.cancel()
                    raise TimeoutError(
                        f"photo fetch timed out after {per_team_timeout:.0f}s"
                    ) from exc

            entry.update(result)
            entry["fetched"] = bool(result.get("ok"))
            if not result.get("ok"):
                summary["ok"] = False
        except Exception as exc:  # noqa: BLE001 - continue to graphics
            logger.exception("Photo fetch failed for %s", team.name)
            print(f"  ! Photo fetch failed for {team.name}: {exc}")
            entry["status"] = "error"
            entry["error"] = str(exc)
            summary["ok"] = False

        summary["teams"].append(entry)

    return summary


def _fetch_one_team(
    *,
    team_name: str,
    sport_code: str,
    ncaa_names: list[str],
    out_dir: Path,
    search: Any,
    confirm: Any,
    download: Any,
    deadline: float,
) -> dict[str, Any]:
    if time.monotonic() >= deadline:
        return {"status": "timeout", "ok": False}

    urls = search(team_name, sport_code)
    if not urls:
        print(f"  No Sidearm roster URLs found for {team_name}")
        return {"status": "no_candidates", "ok": False}

    needed = overlap_threshold(len([n for n in ncaa_names if name_key(n)]))
    for url in urls:
        if time.monotonic() >= deadline:
            return {"status": "timeout", "ok": False, "roster_url": url}
        print(f"  Candidate: {url}")
        try:
            ok, matched = confirm(url, ncaa_names)
        except Exception as exc:  # noqa: BLE001 - try next candidate
            print(f"  ! Could not read roster: {exc}")
            continue
        print(f"  Name match: {matched} (need ≥ {needed})")
        if not ok:
            continue
        if time.monotonic() >= deadline:
            return {"status": "timeout", "ok": False, "roster_url": url, "matched": matched}
        print(f"  Confirmed roster for {team_name}")
        rc = download(url, out_dir)
        return {
            "status": "downloaded" if rc == 0 else "download_partial",
            "ok": rc == 0,
            "roster_url": url,
            "matched": matched,
            "exit_code": rc,
            "output_dir": str(out_dir),
        }

    print(f"  No confirmed roster URL for {team_name}")
    return {"status": "unconfirmed", "ok": False}
