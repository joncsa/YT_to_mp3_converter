"""End-to-end API test with real ffmpeg but a fake download (no network)."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("FFMPEG_BIN", FFMPEG)
    import importlib

    from app import main, media

    importlib.reload(media)
    importlib.reload(main)
    from fastapi.testclient import TestClient

    from tests.test_trim import SR, _song

    audio = _song(np.random.default_rng(11), intro_loop=32, outro_loop=30)

    def fake_download(url, cache_dir):
        info = {"id": "vid123", "extractor_key": "Youtube", "title": "Some Artist - Great Song (Official Video)",
                "channel": "SomeArtistVEVO", "duration": len(audio) / SR, "categories": ["Music"],
                "webpage_url": url, "upload_date": "20240101"}
        item = cache_dir / media.safe_id(info)
        item.mkdir(parents=True, exist_ok=True)
        src = item / "source.wav"
        if not src.exists():
            subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1",
                            "-i", "-", str(src)], input=audio.tobytes(), check=True)
            subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=red:s=640x360",
                            "-frames:v", "1", str(item / "thumb.jpg")], check=True)
        (item / "info.json").write_text(json.dumps(info))
        return info, src, item / "thumb.jpg"

    monkeypatch.setattr(media, "download", fake_download)
    monkeypatch.setattr(media, "sponsorblock_segments", lambda info: [])
    return TestClient(main.app), tmp_path


def test_analyze_export_zip(client):
    c, tmp = client
    a = c.post("/analyze", json={"url": "https://youtu.be/vid123", "type": "auto", "trim": "smart"}).json()
    assert a["ok"], a
    assert (a["artist"], a["title"]) == ("Some Artist", "Great Song")
    assert a["filename"] == "Some Artist - Great Song.mp3"
    assert abs(a["start"] - 34) < 1.5 and abs(a["end"] - 184) < 1.5, a["reasons"]

    p = c.get(a["preview_start_url"].split("8000", 1)[1])
    assert p.status_code == 200 and p.content[:3] in (b"ID3", b"\xff\xfb", b"\xff\xf3")

    e = c.post("/export", json={"id": a["id"], "artist": a["artist"], "title": a["title"],
                                "range": a["range"], "batch": "b1"}).json()
    assert e["ok"], e
    out = tmp / "out" / "Some Artist - Great Song.mp3"
    assert out.exists()
    data = out.read_bytes()
    assert data[:3] == b"ID3" and data[3] == 3  # ID3v2.3
    assert b"APIC" in data[:200_000]  # cover art embedded
    assert b"Xing" in data[:300_000] and b"Info" not in data[:300_000]  # VBR header, not CBR

    # re-export of the same source overwrites instead of creating "(2)"
    e2 = c.post("/export", json={"id": a["id"], "artist": a["artist"], "title": a["title"],
                                 "start": 40, "end": 100, "batch": "b1"}).json()
    assert e2["file"] == e["file"] and abs(e2["seconds"] - 60) < 0.2

    z = c.get("/batches/b1.zip")
    assert z.status_code == 200 and z.content[:2] == b"PK"
    assert c.get(f"/files/{e['file']}").status_code == 200
    assert c.get("/files/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_export_rejects_bad_range(client):
    c, _ = client
    a = c.post("/analyze", json={"url": "https://youtu.be/vid123"}).json()
    r = c.post("/export", json={"id": a["id"], "artist": "A", "title": "B", "range": "later"})
    assert r.status_code == 400


def test_cookie_upload(client, monkeypatch, tmp_path):
    c, _ = client
    from app import main, media

    monkeypatch.setattr(main, "API_KEY", "k1")
    monkeypatch.setattr(media, "COOKIES", str(tmp_path / "cfg" / "cookies.txt"))
    assert "No cookies" in c.get("/admin/cookies").text
    good = ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tSID\tabc\n"
    assert c.post("/admin/cookies", data={"key": "bad", "cookies": good}).status_code == 401
    assert c.post("/admin/cookies", data={"key": "k1", "cookies": "hello"}).status_code == 400
    r = c.post("/admin/cookies", data={"key": "k1", "cookies": good})
    assert r.status_code == 200 and "1 entries" in r.text
    assert (tmp_path / "cfg" / "cookies.txt").read_text().startswith("# Netscape")
    assert c.get("/health").json()["cookies"] is True
