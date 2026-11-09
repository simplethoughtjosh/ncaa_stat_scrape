"""Parse NCAA play-by-play lines for FT, substitutions, and timeouts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PLAYER_CODE_RE = re.compile(r"^([AHah])(\d{1,2})$")
FT_RE = re.compile(
    r"^Free throw\s+(\d+)\s+of\s+(\d+)\b.*?\b(made|missed)\b.*?\bby\s+(.+?)(?:\(|$)",
    re.I,
)
# Fallback when the line lacks a clear made/missed token.
FT_RE_LOOSE = re.compile(
    r"^Free throw\s+(\d+)\s+of\s+(\d+)\b.*?\bby\s+(.+?)(?:\(|$)",
    re.I,
)
# Team can be an abbr (CUH) or a full name with spaces (Westminster (UT), Idaho St.).
SUB_RE = re.compile(r"^Substitution\s+(.+?):\s+(in|out)\s+(.+)$", re.I)
TIMEOUT_RE = re.compile(
    r"^(Media\s+Timeout|Timeout(?:\s+short)?)\s*(.*)$",
    re.I,
)


@dataclass
class FreeThrowEvent:
    attempt: int
    of_total: int
    player_name: str
    raw: str
    made: bool | None = None

    @property
    def attempt_label(self) -> str:
        return f"{self.attempt} of {self.of_total}"


@dataclass
class SubLine:
    team_abbr: str
    direction: str  # in | out
    player_name: str
    raw: str


@dataclass
class SubBatch:
    team_abbr: str
    outs: list[str] = field(default_factory=list)
    ins: list[str] = field(default_factory=list)
    period: str | None = None
    clock: str | None = None

    def pairs(self) -> list[tuple[str, str]]:
        """Out/in pairs in arrival order. NCAA does not say who replaced whom.

        A longer column is paired with a blank on the other side.
        """
        count = max(len(self.outs), len(self.ins))
        return [
            (
                self.outs[i] if i < len(self.outs) else "",
                self.ins[i] if i < len(self.ins) else "",
            )
            for i in range(count)
        ]


@dataclass
class TimeoutEvent:
    label: str
    team_abbr: str
    raw: str
    is_media: bool = False


def parse_player_code(code: str) -> tuple[str, str] | None:
    """Return (side, jersey) where side is 'home' or 'away'."""
    match = PLAYER_CODE_RE.match((code or "").strip())
    if not match:
        return None
    side = "home" if match.group(1).upper() == "H" else "away"
    return side, match.group(2)


def parse_free_throw(text: str) -> FreeThrowEvent | None:
    raw = (text or "").strip()
    match = FT_RE.search(raw)
    if match:
        result = match.group(3).lower()
        name = match.group(4).strip()
        made: bool | None = True if result == "made" else False if result == "missed" else None
    else:
        match = FT_RE_LOOSE.search(raw)
        if not match:
            return None
        name = match.group(3).strip()
        made = None
        lower = raw.lower()
        if re.search(r"\bmissed\b", lower):
            made = False
        elif re.search(r"\bmade\b", lower):
            made = True
    # Strip trailing team abbr in "Name(ABC)" if capture included it oddly
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    return FreeThrowEvent(
        attempt=int(match.group(1)),
        of_total=int(match.group(2)),
        player_name=name,
        raw=text,
        made=made,
    )


def parse_substitution(text: str) -> SubLine | None:
    match = SUB_RE.match((text or "").strip())
    if not match:
        return None
    return SubLine(
        team_abbr=match.group(1),
        direction=match.group(2).lower(),
        player_name=match.group(3).strip(),
        raw=text,
    )


def clock_seconds(clock: str | None) -> int | None:
    """Whole seconds remaining. ``10:00`` and ``10:00:00`` are both 600."""
    text = (clock or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) < 2 or len(parts) > 3:
        return None
    try:
        minutes = int(parts[0].split(".", 1)[0])
        seconds = int(parts[1].split(".", 1)[0])
    except ValueError:
        return None
    if minutes < 0 or seconds < 0 or seconds >= 60:
        return None
    return minutes * 60 + seconds


def period_opening_seconds(sport_code: str, period: str | None) -> int:
    """Clock at the start of the period. Overtime is 5:00 for both sports."""
    label = (period or "").upper()
    if "OT" in label or "OVERTIME" in label:
        return 5 * 60
    if (sport_code or "").upper() == "MBB":
        return 20 * 60
    return 10 * 60


def clock_has_counted_down(clock: str | None, *, sport_code: str, period: str | None) -> bool:
    """True once the clock is below the period's opening time.

    A missing clock, or a clock still sitting on 10:00 / 20:00 / 5:00, has not
    counted down, so a starting lineup is not treated as a substitution.
    """
    remaining = clock_seconds(clock)
    if remaining is None:
        return False
    return remaining < period_opening_seconds(sport_code, period)


def parse_timeout(text: str) -> TimeoutEvent | None:
    match = TIMEOUT_RE.match((text or "").strip())
    if not match:
        return None
    kind = match.group(1).strip()
    team = (match.group(2) or "").strip()
    is_media = "media" in kind.lower()
    label = "Media Timeout" if is_media else ("Timeout Short" if "short" in kind.lower() else "Timeout")
    return TimeoutEvent(label=label, team_abbr=team, raw=text, is_media=is_media)


class SubBatcher:
    """Accumulate same-team in/out lines into boards.

    Balanced batches (equal outs and ins) publish after ``quiet_seconds`` of
    silence. Incomplete boards (lonely in or out) keep waiting for a same-team
    match; only after ``unmatched_hold_seconds`` of silence do we publish the
    short side so a never-arriving match cannot hang forever.
    """

    def __init__(
        self,
        quiet_seconds: float = 1.5,
        unmatched_hold_seconds: float = 8.0,
        max_slots: int = 5,
    ) -> None:
        self.quiet_seconds = quiet_seconds
        self.unmatched_hold_seconds = unmatched_hold_seconds
        self.max_slots = max_slots
        self._buffers: dict[str, SubBatch] = {}
        self._last_ts: dict[str, float] = {}

    @staticmethod
    def _is_balanced(batch: SubBatch) -> bool:
        return bool(batch.outs) and len(batch.outs) == len(batch.ins)

    def _ready_to_publish(self, key: str, now: float) -> bool:
        batch = self._buffers.get(key)
        ts = self._last_ts.get(key)
        if batch is None or ts is None or not (batch.outs or batch.ins):
            return False
        silent = now - ts
        if self._is_balanced(batch):
            return silent >= self.quiet_seconds
        return silent >= self.unmatched_hold_seconds

    def _take_batch(self, key: str) -> SubBatch | None:
        batch = self._buffers.pop(key, None)
        self._last_ts.pop(key, None)
        if batch and (batch.outs or batch.ins):
            return batch
        return None

    def add(
        self,
        line: SubLine,
        *,
        period: str | None,
        clock: str | None,
        now: float,
    ) -> list[SubBatch]:
        """Add a line. Returns other teams' batches that are ready to publish."""
        key = line.team_abbr
        flushed: list[SubBatch] = []
        # Flush other teams only when ready (balanced+quiet, or unmatched hold).
        for other in list(self._last_ts):
            if other != key and self._ready_to_publish(other, now):
                taken = self._take_batch(other)
                if taken:
                    flushed.append(taken)

        batch = self._buffers.get(key)
        if batch is None:
            batch = SubBatch(team_abbr=key, period=period, clock=clock)
            self._buffers[key] = batch
        else:
            self.refresh_meta(key, period=period, clock=clock)
        if line.direction == "out":
            if line.player_name not in batch.outs and len(batch.outs) < self.max_slots:
                batch.outs.append(line.player_name)
        else:
            if line.player_name not in batch.ins and len(batch.ins) < self.max_slots:
                batch.ins.append(line.player_name)
        self._last_ts[key] = now
        return flushed

    def refresh_meta(
        self,
        team_abbr: str,
        *,
        period: str | None,
        clock: str | None,
    ) -> bool:
        """Update period/clock on an open batch (late Faye fragments). Returns True if found."""
        batch = self._buffers.get(team_abbr)
        if batch is None:
            return False
        if period:
            batch.period = period
        if clock:
            batch.clock = clock
        return True

    def has_open_batch(self, team_abbr: str) -> bool:
        return team_abbr in self._buffers

    def flush_ready(self, now: float) -> list[SubBatch]:
        ready: list[SubBatch] = []
        for key in list(self._last_ts):
            if self._ready_to_publish(key, now):
                taken = self._take_batch(key)
                if taken:
                    ready.append(taken)
        return ready

    def flush_all(self) -> list[SubBatch]:
        out = [b for b in self._buffers.values() if b.outs or b.ins]
        self._buffers.clear()
        self._last_ts.clear()
        return out
