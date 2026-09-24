"""Turn YouTube metadata into a clean ``Artist - Song Name`` pair and a safe file name."""

from __future__ import annotations

import re
import unicodedata

# Bracketed chunks that are video/upload noise rather than part of the song name.
NOISE_WORDS = (
    r"official|video|audio|lyrics?|lyric video|visuali[sz]er|music video|m/?v|hd|hq|4k|8k|1080p|720p|"
    r"clip officiel|videoclip|video oficial|full song|full audio|color coded|explicit|clean|"
    r"high quality|with lyrics|letra|sub(?:titulado|s)?|español|eng|official performance video"
)
BRACKET_RE = re.compile(r"\s*[\(\[\{【]([^\)\]\}】]*)[\)\]\}】]")
NOISE_INNER_RE = re.compile(rf"^\s*(?:(?:{NOISE_WORDS})[\s/&+,.-]*)+$", re.I)
TRAILING_NOISE_RE = re.compile(
    rf"\s*(?:[|/•]\s*)?(?:official\s+(?:music\s+)?(?:video|audio|lyric video|visuali[sz]er)|"
    rf"lyric video|lyrics|audio only|music video|\bm/?v\b|\b(?:hd|hq|4k)\b)\s*$",
    re.I,
)
HASHTAG_RE = re.compile(r"\s#\w+")
FEAT_RE = re.compile(r"\s*[\(\[]?\b(?:feat\.?|ft\.?|featuring)\s+([^\)\]]+?)[\)\]]?\s*$", re.I)
SEPARATORS = (" - ", " – ", " — ", " -- ", " ~ ", " | ")
QUOTED_TITLE_RE = re.compile(r"^(?P<artist>[^\"“”'‘’]+?)\s*[\"“'‘](?P<title>[^\"”'’]+)[\"”'’](?P<rest>.*)$")
CHANNEL_NOISE_RE = re.compile(r"\s*(?:-\s*topic|vevo|official(?:\s+(?:channel|artist channel))?|music|tv|records?)\s*$", re.I)
ILLEGAL_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def _collapse(s: str) -> str:
    return re.sub(r"\s{2,}", " ", s).strip(" -–—_|~·•.,").strip()


def clean_title(title: str, feat: bool = True) -> str:
    """Strip '(Official Video)', '[HD]', '#tags' etc. but keep '(feat. X)', '(Remix)', '(Live)'."""
    title = HASHTAG_RE.sub("", title or "")

    def _drop_noise(m: re.Match) -> str:
        return "" if NOISE_INNER_RE.match(m.group(1)) else m.group(0)

    prev = None
    while prev != title:  # repeat: "(Official Video) [4K]"
        prev = title
        title = BRACKET_RE.sub(_drop_noise, title)
        title = TRAILING_NOISE_RE.sub("", title)
    title = _collapse(title)
    return normalize_feat(title) if feat else title


def normalize_feat(text: str) -> str:
    """'Song ft. X' / 'Song (featuring X)' -> 'Song (feat. X)'."""
    m = FEAT_RE.search(text)
    if not m:
        return text
    return f"{text[:m.start()].rstrip()} (feat. {m.group(1).strip()})"


def clean_channel(name: str) -> str:
    name = (name or "").strip()
    prev = None
    while prev != name:
        prev = name
        name = CHANNEL_NOISE_RE.sub("", name).strip()
    return name or "Unknown Artist"


def split_artist_title(title: str) -> tuple[str, str] | None:
    """Split a video title of the form 'Artist - Song (...)'."""
    for sep in SEPARATORS:
        if sep in title:
            artist, song = title.split(sep, 1)
            if artist.strip() and song.strip():
                return artist.strip(), song.strip()
    m = QUOTED_TITLE_RE.match(title)
    if m and m.group("artist").strip():
        return m.group("artist").strip(), (m.group("title") + m.group("rest")).strip()
    return None


def move_feat_from_artist(artist: str, title: str) -> tuple[str, str]:
    m = FEAT_RE.search(artist)
    if not m:
        return artist, title
    feat = m.group(1).strip()
    artist = artist[:m.start()].strip()
    if "feat." not in title.lower():
        title = f"{title} (feat. {feat})"
    return artist, title


def derive_names(info: dict, content_type: str) -> tuple[str, str, str]:
    """Return (artist, title, source) where source explains which rule was used."""
    video_title = info.get("title") or info.get("id") or "Untitled"
    channel = info.get("channel") or info.get("uploader") or ""

    if content_type == "podcast":
        show = info.get("series") or clean_channel(channel)
        title = clean_title(video_title)
        # "Show Name - Episode title" / "Show Name | Episode" -> keep just the episode part
        for sep in SEPARATORS:
            if title.lower().startswith(show.lower() + sep.rstrip()):
                title = title[len(show) + len(sep.rstrip()):].strip()
                break
        return show, title or video_title, "podcast (channel - episode)"

    # 1) YouTube Music / auto-generated "Topic" uploads carry proper tags.
    artist = info.get("artist") or ", ".join(info.get("artists") or []) or info.get("creator")
    track = info.get("track")
    if artist and track:
        artist, track = move_feat_from_artist(artist, track)
        return artist, normalize_feat(_collapse(track)), "youtube music tags"

    # 2) "Artist - Song (Official Video)"
    cleaned = clean_title(video_title, feat=False)
    split = split_artist_title(cleaned)
    if split:
        a, t = move_feat_from_artist(*split)
        return _collapse(a), clean_title(t), "parsed from video title"

    # 3) Fallback: channel name + cleaned title.
    return clean_channel(channel), normalize_feat(cleaned) or video_title, "channel + title"


def split_override(name: str) -> tuple[str, str]:
    artist, title = name.split(" - ", 1)
    return artist.strip(), title.strip()


def safe_component(text: str) -> str:
    """Make a string safe on Windows, macOS, Linux, Android, FAT32/exFAT USB sticks and car stereos."""
    text = unicodedata.normalize("NFC", text or "")
    text = text.replace('"', "'").replace("“", "'").replace("”", "'")
    text = text.replace("/", "-").replace("\\", "-").replace(":", " -").replace("|", "-")
    text = ILLEGAL_FS_CHARS.sub("", text)
    text = _collapse(text).rstrip(". ")
    if text.lower() in WINDOWS_RESERVED:
        text = f"_{text}"
    return text


def build_filename(artist: str, title: str, max_len: int = 180) -> str:
    """'Artist - Song Name.mp3', trimmed to a length every filesystem accepts."""
    artist_s, title_s = safe_component(artist) or "Unknown Artist", safe_component(title) or "Untitled"
    stem = f"{artist_s} - {title_s}"
    if len(stem.encode("utf-8")) > max_len:
        stem = stem.encode("utf-8")[:max_len].decode("utf-8", "ignore").rstrip(" .-")
    return f"{stem}.mp3"
