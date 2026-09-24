"""yt-dlp + ffmpeg plumbing: search, download, decode, SponsorBlock, MP3 encode."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import yt_dlp

from .parsing import ParsedLine

log = logging.getLogger("processor.media")

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
COOKIES = os.environ.get("YTDLP_COOKIES") or None
PLAYLIST_LIMIT = int(os.environ.get("PLAYLIST_LIMIT", "200"))
PODCAST_MIN_SECONDS = 15 * 60
# LAME presets. "V2" = VBR averaging ~190 kbps (the standard "192 VBR" preset); "ABR192" = average bitrate 192k.
MP3_QUALITY = os.environ.get("MP3_QUALITY", "V2").upper()
MP3_ARGS = {
    "V0": ["-q:a", "0"],
    "V2": ["-q:a", "2"],
    "ABR192": ["-abr", "1", "-b:a", "192k"],
    "CBR192": ["-b:a", "192k"],
}


class MediaError(RuntimeError):
    pass


def _ydl_opts(**extra) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "noplaylist": True,
    }
    if COOKIES and Path(COOKIES).is_file():
        opts["cookiefile"] = COOKIES
    opts.update(extra)
    return opts


def _entry_url(entry: dict) -> str:
    url = entry.get("webpage_url") or entry.get("url") or ""
    if url.startswith("http"):
        return url
    if entry.get("id") and (entry.get("ie_key") or "").lower().startswith("youtube"):
        return f"https://www.youtube.com/watch?v={entry['id']}"
    return url or entry.get("id", "")


def _flat(query: str, limit: int) -> dict:
    with yt_dlp.YoutubeDL(_ydl_opts(extract_flat="in_playlist", playlistend=limit, noplaylist=False)) as ydl:
        return ydl.extract_info(query, download=False) or {}


def resolve(parsed: ParsedLine) -> list[dict]:
    """Turn one input line into one or more concrete video entries."""
    base = {
        "input": parsed.raw,
        "type": parsed.content_type,
        "name_override": parsed.name_override,
        "start_override": parsed.start_override,
        "end_override": parsed.end_override,
        "warnings": list(parsed.warnings),
    }

    if parsed.is_url:
        info = _flat(parsed.query, PLAYLIST_LIMIT)
        if info.get("_type") == "playlist" and info.get("entries"):
            entries = [e for e in info["entries"] if e]
            return [
                {**base, "url": _entry_url(e), "title": e.get("title"), "name_override": None,
                 "start_override": None, "end_override": None, "playlist": info.get("title")}
                for e in entries
            ]
        return [{**base, "url": info.get("webpage_url") or parsed.query, "title": info.get("title")}]

    query = parsed.query
    if parsed.content_type == "podcast":
        info = _flat(f"ytsearch8:{query}", 8)
        entries = [e for e in info.get("entries") or [] if e]
        if not entries:
            raise MediaError(f"no results for {query!r}")
        long_ones = [e for e in entries if (e.get("duration") or 0) >= PODCAST_MIN_SECONDS]
        pick = (long_ones or entries)[0]
        return [{**base, "url": _entry_url(pick), "title": pick.get("title")}]

    # Songs: YouTube Music's "Songs" shelf gives clean studio audio with proper artist/track tags
    # (no video intros), so try it first and fall back to regular YouTube search.
    for search in (
        f"https://music.youtube.com/search?q={urllib.parse.quote_plus(query)}#songs",
        f"ytsearch1:{query}",
    ):
        try:
            info = _flat(search, 1)
        except Exception as exc:  # noqa: BLE001 - try the next search backend
            log.info("search %s failed: %s", search, exc)
            continue
        entries = [e for e in info.get("entries") or [] if e and (e.get("id") or e.get("url"))]
        if entries:
            pick = entries[0]
            if parsed.content_type == "auto":
                base["type"] = "music"
            return [{**base, "url": _entry_url(pick), "title": pick.get("title")}]
    raise MediaError(f"no results for {query!r}")


def safe_id(info: dict) -> str:
    raw = f"{info.get('extractor_key', 'x')}-{info.get('id', 'unknown')}"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", raw)[:120]


def download(url: str, cache_dir: Path) -> tuple[dict, Path, Path | None]:
    """Download best audio + thumbnail. Returns (info, audio_path, thumb_path)."""
    with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") == "playlist":
        raise MediaError("expected a single video, got a playlist")
    if info.get("is_live"):
        raise MediaError("live streams are not supported")
    item_dir = cache_dir / safe_id(info)
    item_dir.mkdir(parents=True, exist_ok=True)

    existing = [p for p in item_dir.glob("source.*") if not p.name.endswith(".part")]
    if not existing:
        opts = _ydl_opts(
            format="bestaudio/best",
            outtmpl={"default": str(item_dir / "source.%(ext)s"), "thumbnail": str(item_dir / "thumb.%(ext)s")},
            writethumbnail=True,
            postprocessors=[{"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"}],
        )
        if FFMPEG != "ffmpeg":
            opts["ffmpeg_location"] = FFMPEG
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.process_ie_result(info, download=True)
        existing = [p for p in item_dir.glob("source.*") if not p.name.endswith(".part")]
    if not existing:
        raise MediaError("download produced no audio file")

    thumb = item_dir / "thumb.jpg"
    slim = {k: info.get(k) for k in (
        "id", "extractor_key", "title", "channel", "uploader", "artist", "artists", "creator", "track",
        "album", "release_year", "upload_date", "duration", "categories", "webpage_url", "series",
    )}
    (item_dir / "info.json").write_text(json.dumps(slim, ensure_ascii=False, indent=1))
    return slim, existing[0], thumb if thumb.exists() else None


def sponsorblock_segments(info: dict, categories=("music_offtopic",)) -> list[dict]:
    if (info.get("extractor_key") or "").lower() != "youtube" or not info.get("id"):
        return []
    qs = urllib.parse.urlencode({"videoID": info["id"], "categories": json.dumps(list(categories))})
    try:
        with urllib.request.urlopen(f"https://sponsor.ajay.app/api/skipSegments?{qs}", timeout=10) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code != 404:  # 404 = no segments submitted
            log.warning("SponsorBlock %s: HTTP %s", info["id"], exc.code)
    except Exception as exc:  # noqa: BLE001 - SponsorBlock is best-effort
        log.warning("SponsorBlock %s: %s", info["id"], exc)
    return []


def decode_mono(path: Path, sr: int) -> np.ndarray:
    cmd = [FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path),
           "-map", "0:a:0", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        raise MediaError(f"ffmpeg decode failed: {proc.stderr.decode(errors='ignore')[-400:]}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def _fade_filter(length: float, fade_in: float, fade_out: float) -> str:
    fade_in = min(fade_in, length / 4)
    fade_out = min(fade_out, length / 4)
    return (f"afade=t=in:st=0:d={fade_in:.3f},"
            f"afade=t=out:st={max(0.0, length - fade_out):.3f}:d={fade_out:.3f}")


def encode_mp3(src: Path, dst: Path, start: float, end: float, fade_in: float, fade_out: float,
               tags: dict, cover: Path | None, quality: str | None = None) -> None:
    length = max(0.5, end - start)
    tmp = dst.with_name(f".{dst.stem}.partial.mp3")
    cmd = [FFMPEG, "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
           "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(src)]
    if cover:
        cmd += ["-i", str(cover)]
    cmd += ["-map", "0:a:0"]
    if cover:
        cmd += ["-map", "1:v:0", "-c:v", "mjpeg", "-q:v", "3",
                "-vf", "crop='min(iw,ih)':'min(iw,ih)',scale=600:600",
                "-disposition:v", "attached_pic",
                "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"]
    cmd += ["-af", _fade_filter(length, fade_in, fade_out), "-ar", "44100", "-ac", "2",
            "-c:a", "libmp3lame", *MP3_ARGS.get((quality or MP3_QUALITY).upper(), MP3_ARGS["V2"]),
            "-id3v2_version", "3", "-write_id3v1", "1"]
    for key, value in tags.items():
        if value:
            cmd += ["-metadata", f"{key}={value}"]
    cmd += ["-f", "mp3", str(tmp)]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise MediaError(f"ffmpeg encode failed: {proc.stderr.decode(errors='ignore')[-400:]}")
    os.replace(tmp, dst)


def encode_preview(src: Path, dst: Path, start: float, length: float, fade_in: float, fade_out: float) -> None:
    cmd = [FFMPEG, "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
           "-ss", f"{max(0.0, start):.3f}", "-t", f"{length:.3f}", "-i", str(src), "-map", "0:a:0",
           "-af", _fade_filter(length, fade_in, fade_out), "-ac", "2", "-c:a", "libmp3lame", "-b:a", "96k",
           "-f", "mp3", str(dst)]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        raise MediaError(f"ffmpeg preview failed: {proc.stderr.decode(errors='ignore')[-400:]}")


def prune_cache(cache_dir: Path, max_age_hours: float) -> None:
    if max_age_hours <= 0 or not cache_dir.exists():
        return
    cutoff = time.time() - max_age_hours * 3600
    for item in cache_dir.iterdir():
        try:
            if item.is_dir() and item.stat().st_mtime < cutoff:
                shutil.rmtree(item, ignore_errors=True)
        except OSError:
            pass
