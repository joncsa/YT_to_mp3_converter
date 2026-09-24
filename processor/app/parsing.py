"""Parsing of the free-form lines users paste into the bulk form.

One line = one request. Supported shapes::

    https://www.youtube.com/watch?v=xxxx
    https://www.youtube.com/playlist?list=xxxx          (expands to every video)
    Daft Punk Around the World                          (song search)
    podcast: Lex Fridman #400                           (podcast/episode search)
    song: Nirvana Lithium                               (force music search)

Optional overrides after a pipe, in any order::

    <anything> | Artist - Song Name                     (force the file name)
    <anything> | 0:12-3:45                              (force the keep range)
    <anything> | 0:12-                                  (only force the start)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

URL_RE = re.compile(r"^(https?://|www\.)\S+$", re.I)
TYPE_PREFIX_RE = re.compile(r"^(podcast|episode|song|music|track)\s*:\s*", re.I)
TIME_RE = r"(?:\d+:)?\d{1,2}(?::\d{1,2})?(?:\.\d+)?|\d+(?:\.\d+)?"
RANGE_RE = re.compile(rf"^\s*(?P<start>{TIME_RE})?\s*[-–—]\s*(?P<end>{TIME_RE}|end)?\s*$", re.I)

CONTENT_TYPES = ("auto", "music", "podcast")


@dataclass
class ParsedLine:
    raw: str
    query: str
    is_url: bool
    content_type: str = "auto"
    name_override: str | None = None
    start_override: float | None = None
    end_override: float | None = None
    warnings: list[str] = field(default_factory=list)


def parse_time(value: str | float | int | None) -> float | None:
    """'1:02:03.5' -> 3723.5, '3:45' -> 225, '12.5' -> 12.5, '' / None -> None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    value = value.strip()
    if not value or value.lower() == "end":
        return None
    parts = value.split(":")
    if len(parts) > 3:
        raise ValueError(f"bad time: {value!r}")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def format_time(seconds: float | None) -> str:
    """3723.5 -> '1:02:03.5', 225 -> '3:45.0'."""
    if seconds is None:
        return ""
    seconds = max(0.0, float(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours >= 1:
        return f"{int(hours)}:{int(minutes):02d}:{secs:04.1f}"
    return f"{int(minutes)}:{secs:04.1f}"


def parse_range(text: str) -> tuple[float | None, float | None] | None:
    """'0:12 - 3:45' -> (12, 225). Returns None if the text is not a range."""
    m = RANGE_RE.match(text or "")
    if not m or (m.group("start") is None and m.group("end") is None):
        return None
    return parse_time(m.group("start")), parse_time(m.group("end"))


def parse_line(line: str, default_type: str = "auto") -> ParsedLine | None:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None

    segments = [s.strip() for s in raw.split(" | ")]
    head, overrides = segments[0], segments[1:]
    # Also accept "x|y" without spaces when the head is a URL.
    if not overrides and "|" in raw and URL_RE.match(raw.split("|", 1)[0].strip()):
        head, *overrides = [s.strip() for s in raw.split("|")]

    content_type = default_type if default_type in CONTENT_TYPES else "auto"
    m = TYPE_PREFIX_RE.match(head)
    if m:
        content_type = "podcast" if m.group(1).lower() in ("podcast", "episode") else "music"
        head = head[m.end():].strip()

    parsed = ParsedLine(raw=raw, query=head, is_url=bool(URL_RE.match(head)), content_type=content_type)
    if parsed.is_url and head.lower().startswith("www."):
        parsed.query = "https://" + head

    for seg in overrides:
        if not seg:
            continue
        rng = parse_range(seg)
        if rng is not None:
            parsed.start_override, parsed.end_override = rng
        elif " - " in seg:
            parsed.name_override = seg
        else:
            parsed.warnings.append(f"ignored override {seg!r} (expected 'Artist - Title' or 'start-end')")
    return parsed


def split_input(text: str, default_type: str = "auto") -> list[ParsedLine]:
    """Split a textarea blob into parsed lines, dropping blanks, comments and duplicates."""
    seen: set[str] = set()
    out: list[ParsedLine] = []
    for line in (text or "").splitlines():
        parsed = parse_line(line, default_type)
        if parsed is None:
            continue
        key = parsed.raw.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(parsed)
    return out
