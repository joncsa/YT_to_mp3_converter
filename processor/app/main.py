"""HTTP API used by the n8n workflow.

POST /resolve  one pasted line  -> concrete video entries (search, playlist expansion)
POST /analyze  one entry        -> downloaded + suggested name and keep-range
POST /export   one reviewed item -> 'Artist - Song Name.mp3' (LAME V2 VBR ~190 kbps, ID3v2.3 + cover)
GET  /preview/{id}.mp3          -> short clip around a proposed cut, for auditioning in the review form
GET  /files/{name}              -> download an exported MP3
GET  /batches/{batch}.zip       -> every file exported by one form submission
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import hmac
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from . import media, naming, trim
from .parsing import CONTENT_TYPES, format_time, parse_line, parse_range

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("processor")

CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/data/cache"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/data/output"))
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/")
CACHE_TTL_HOURS = float(os.environ.get("CACHE_TTL_HOURS", "48"))
API_KEY = os.environ.get("API_KEY", "")
META_DIR = OUTPUT_DIR / ".meta"
_index_lock = threading.Lock()

for d in (CACHE_DIR, OUTPUT_DIR, META_DIR / "batches"):
    d.mkdir(parents=True, exist_ok=True)


def check_key(x_api_key: str | None = Header(default=None)) -> None:
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(401, "missing or wrong X-API-Key")


app = FastAPI(title="YT to MP3 processor", version="1.0")
secured = [Depends(check_key)]


# ------------------------------------------------------------------------------------------ models

class ResolveRequest(BaseModel):
    input: str
    type: str = "auto"


class AnalyzeRequest(BaseModel):
    url: str
    type: str = "auto"
    trim: str = "smart"  # smart | silence | off
    input: str | None = None
    name_override: str | None = None
    start_override: float | None = None
    end_override: float | None = None
    options: dict = Field(default_factory=dict)  # any TrimOptions field, e.g. {"loop_min_seconds": 30}


class ExportRequest(BaseModel):
    id: str
    artist: str
    title: str
    start: float = 0
    end: float | None = None
    range: str | None = None  # "0:12.0 - 3:45.5" as typed in the review form; wins over start/end
    fade_in: float | None = None
    fade_out: float | None = None
    batch: str | None = None
    quality: str | None = None


# ----------------------------------------------------------------------------------------- helpers

def _item_dir(item_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", item_id or ""):
        raise HTTPException(400, "bad id")
    path = CACHE_DIR / item_id
    if not path.is_dir():
        raise HTTPException(404, f"{item_id} is not in the cache (analyze it first)")
    return path


def _source(item_dir: Path) -> Path:
    found = [p for p in item_dir.glob("source.*") if not p.name.endswith(".part")]
    if not found:
        raise HTTPException(404, "source audio missing from cache")
    return found[0]


def _load_index() -> dict:
    path = META_DIR / "index.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _claim_filename(filename: str, source_id: str) -> str:
    """Same source -> overwrite the same file; different source with the same name -> 'Name (2).mp3'."""
    with _index_lock:
        index = _load_index()
        stem, candidate, n = filename[:-4], filename, 1
        while True:
            owner = index.get(candidate)
            exists = (OUTPUT_DIR / candidate).exists()
            if owner == source_id or (not exists and owner is None):
                break
            n += 1
            candidate = f"{stem} ({n}).mp3"
        index[candidate] = source_id
        (META_DIR / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))
        return candidate


def _record_batch(batch: str, filename: str) -> None:
    batch = re.sub(r"[^A-Za-z0-9_.-]", "_", batch)[:80]
    path = META_DIR / "batches" / f"{batch}.json"
    with _index_lock:
        files = json.loads(path.read_text()) if path.exists() else []
        if filename not in files:
            files.append(filename)
        path.write_text(json.dumps(files, ensure_ascii=False, indent=1))


def _trim_options(req: AnalyzeRequest) -> trim.TrimOptions:
    opts = trim.TrimOptions(mode=req.trim if req.trim in ("smart", "silence", "off") else "smart")
    for key, value in (req.options or {}).items():
        if hasattr(opts, key) and key != "mode":
            setattr(opts, key, type(getattr(opts, key))(value))
    return opts


# --------------------------------------------------------------------------------------- endpoints

@app.get("/health")
def health() -> dict:
    return {"ok": True, "quality": media.MP3_QUALITY, "output_dir": str(OUTPUT_DIR),
            "cookies": Path(media.COOKIES).is_file()}


@app.post("/resolve", dependencies=secured)
def resolve(req: ResolveRequest) -> dict:
    media.prune_cache(CACHE_DIR, CACHE_TTL_HOURS)
    parsed = parse_line(req.input, req.type if req.type in CONTENT_TYPES else "auto")
    if parsed is None:
        return {"input": req.input, "entries": []}
    try:
        entries = media.resolve(parsed)
    except Exception as exc:  # noqa: BLE001 - report per line, never fail the batch
        log.warning("resolve %r failed: %s", req.input, exc)
        return {"input": req.input, "entries": [], "error": str(exc)}
    return {"input": req.input, "entries": entries}


@app.post("/analyze", dependencies=secured)
def analyze(req: AnalyzeRequest) -> dict:
    try:
        info, source, thumb = media.download(req.url, CACHE_DIR)
    except Exception as exc:  # noqa: BLE001
        log.warning("download %s failed: %s", req.url, exc)
        return {"ok": False, "url": req.url, "input": req.input, "error": f"download failed: {exc}"}

    content_type = req.type
    if content_type not in ("music", "podcast"):
        is_music = "Music" in (info.get("categories") or []) or bool(info.get("track"))
        content_type = "music" if is_music or (info.get("duration") or 0) < media.PODCAST_MIN_SECONDS else "podcast"

    duration = float(info.get("duration") or 0)
    sr = 22050 if duration and duration <= 30 * 60 else 8000
    try:
        samples = media.decode_mono(source, sr)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "url": req.url, "input": req.input, "error": str(exc)}

    opts = _trim_options(req)
    segments = media.sponsorblock_segments(info) if opts.mode == "smart" and content_type == "music" else []
    result = trim.suggest_cuts(samples, sr, opts, segments, content_type)
    result = trim.apply_override(result, req.start_override, req.end_override)

    if req.name_override and " - " in req.name_override:
        artist, title = naming.split_override(req.name_override)
        name_source = "manual override"
    else:
        artist, title, name_source = naming.derive_names(info, content_type)

    item_id = media.safe_id(info)
    return {
        "ok": True,
        "id": item_id,
        "input": req.input,
        "url": info.get("webpage_url") or req.url,
        "type": content_type,
        "video_title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "artist": artist,
        "title": title,
        "name_source": name_source,
        "filename": naming.build_filename(artist, title),
        **result.as_dict(),
        "range": f"{format_time(result.start)} - {format_time(result.end)}",
        "has_cover": thumb is not None,
        "preview_start_url": f"{PUBLIC_URL}/preview/{item_id}.mp3?at={result.start:.2f}&part=head",
        "preview_end_url": f"{PUBLIC_URL}/preview/{item_id}.mp3?at={result.end:.2f}&part=tail",
    }


@app.post("/export", dependencies=secured)
def export(req: ExportRequest) -> dict:
    item_dir = _item_dir(req.id)
    source = _source(item_dir)
    info = json.loads((item_dir / "info.json").read_text())
    duration = float(info.get("duration") or 0) or 10 ** 6

    start, end = req.start, req.end
    if req.range:
        rng = parse_range(req.range)
        if rng is None:
            raise HTTPException(400, f"can't read range {req.range!r} (use e.g. '0:12.5 - 3:41')")
        start, end = rng[0] or 0.0, rng[1]
    end = min(end if end is not None else duration, duration)
    fade_in = req.fade_in if req.fade_in is not None else (0.3 if start > 0.5 else 0.02)
    fade_out = req.fade_out if req.fade_out is not None else (2.0 if end < duration - 0.5 else 0.05)
    if end - start < 1:
        raise HTTPException(400, f"keep range {start:.1f}-{end:.1f}s is empty")

    filename = _claim_filename(naming.build_filename(req.artist, req.title), req.id)
    year = info.get("release_year") or (info.get("upload_date") or "")[:4]
    tags = {
        "artist": req.artist.strip(),
        "title": req.title.strip(),
        "album": info.get("album") or "",
        "album_artist": req.artist.strip() if info.get("album") else "",
        "date": str(year) if year else "",
        "comment": info.get("webpage_url") or "",
    }
    cover = item_dir / "thumb.jpg"
    try:
        media.encode_mp3(source, OUTPUT_DIR / filename, start, end, fade_in, fade_out, tags,
                         cover if cover.exists() else None, req.quality)
    except media.MediaError as exc:
        raise HTTPException(500, str(exc)) from exc

    if req.batch:
        _record_batch(req.batch, filename)
    size = (OUTPUT_DIR / filename).stat().st_size
    return {
        "ok": True,
        "id": req.id,
        "file": filename,
        "path": str(OUTPUT_DIR / filename),
        "url": f"{PUBLIC_URL}/files/{quote(filename)}",
        "size_mb": round(size / 1e6, 2),
        "seconds": round(end - start, 1),
        "avg_kbps": round(size * 8 / 1000 / max(end - start, 1)),
        "range": f"{format_time(start)} - {format_time(end)}",
    }


@app.get("/preview/{item_id}.mp3")
def preview(item_id: str, at: float = Query(0.0), part: str = Query("head"), seconds: float = Query(15.0)):
    item_dir = _item_dir(item_id)
    seconds = max(3.0, min(seconds, 60.0))
    start = at - 3 if part == "head" else at - seconds  # a little context before the cut-in
    fade_in, fade_out = (0.3, 0.05) if part == "head" else (0.05, 2.0)
    out = item_dir / f"preview-{part}-{at:.2f}-{seconds:.0f}.mp3"
    if not out.exists():
        try:
            media.encode_preview(_source(item_dir), out, max(0.0, start), seconds, fade_in, fade_out)
        except media.MediaError as exc:
            raise HTTPException(500, str(exc)) from exc
    return FileResponse(out, media_type="audio/mpeg")


@app.get("/files/{filename}")
def get_file(filename: str):
    path = (OUTPUT_DIR / filename).resolve()
    if path.parent != OUTPUT_DIR.resolve() or not path.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type="audio/mpeg", filename=filename)


@app.get("/batches/{batch}.zip")
def batch_zip(batch: str):
    path = META_DIR / "batches" / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', batch)[:80]}.json"
    if not path.exists():
        raise HTTPException(404, "unknown batch")
    files = [f for f in json.loads(path.read_text()) if (OUTPUT_DIR / f).is_file()]
    if not files:
        raise HTTPException(404, "no files in this batch")
    zip_path = CACHE_DIR / f"batch-{path.stem}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:  # MP3s don't compress further
        for f in files:
            zf.write(OUTPUT_DIR / f, arcname=f)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return FileResponse(zip_path, media_type="application/zip", filename=f"mp3-batch-{stamp}.zip")


# ------------------------------------------------------------------------------ cookies upload page
# YouTube blocks datacenter IPs ("Sign in to confirm you're not a bot") unless yt-dlp sends cookies
# from a signed-in browser. This page lets you paste a Netscape cookies.txt without shell access.

COOKIE_PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>YouTube cookies</title><style>body{{font:15px system-ui;max-width:760px;margin:24px auto;padding:0 16px}}
textarea{{width:100%;height:280px;font:12px monospace}}input{{width:100%;padding:6px}}button{{padding:8px 18px;margin-top:10px}}
.s{{padding:10px;border-radius:6px;background:#eef}}</style></head><body>
<h2>YouTube cookies for yt-dlp</h2><p class="s">{status}</p>
<ol><li>Open a <b>private/incognito</b> window and sign in to YouTube (a spare Google account is safest).</li>
<li>Export cookies for youtube.com with the <i>Get cookies.txt LOCALLY</i> extension (Netscape format), then <b>close</b> the private window
so the cookies aren't rotated.</li><li>Paste the whole file below with your API key and save.</li></ol>
<form method="post"><label>API key<input type="password" name="key" required></label>
<label>cookies.txt<textarea name="cookies" required placeholder="# Netscape HTTP Cookie File"></textarea></label>
<button type="submit">Save cookies</button></form></body></html>"""


def _cookie_status() -> str:
    path = Path(media.COOKIES)
    if not path.is_file():
        return "No cookies saved yet."
    stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = sum(1 for ln in path.read_text(errors="ignore").splitlines() if ln and not ln.startswith("#"))
    return f"Cookies saved ({lines} entries, updated {stamp})."


@app.get("/admin/cookies", response_class=HTMLResponse)
def cookies_page() -> str:
    return COOKIE_PAGE.format(status=_cookie_status())


@app.post("/admin/cookies", response_class=HTMLResponse)
async def cookies_save(request: Request) -> str:
    form = parse_qs((await request.body()).decode("utf-8", "ignore"))
    key = (form.get("key") or [""])[0]
    text = (form.get("cookies") or [""])[0].replace("\r\n", "\n").strip() + "\n"
    if not API_KEY:
        raise HTTPException(403, "Set the API_KEY variable on the service first.")
    if not hmac.compare_digest(key, API_KEY):
        raise HTTPException(401, "Wrong API key.")
    rows = [ln.split("\t") for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    if not rows or any(len(r) < 7 for r in rows):
        raise HTTPException(400, "That doesn't look like a Netscape cookies.txt (7 tab-separated columns per line).")
    if not any("youtube.com" in r[0] for r in rows):
        raise HTTPException(400, "No youtube.com cookies found in the file.")
    path = Path(media.COOKIES)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not text.startswith("# Netscape HTTP Cookie File"):
        text = "# Netscape HTTP Cookie File\n" + text
    path.write_text(text)
    path.chmod(0o600)
    return COOKIE_PAGE.format(status="Saved. " + _cookie_status())
