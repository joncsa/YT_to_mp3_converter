# YT_to_mp3_converter

Bulk **YouTube → MP3** with an n8n form front-end. Paste links, song names or podcast titles (one per line). It
finds each one, downloads the audio, suggests where the actual music starts and ends, lets you check
the names and cut points, and exports

```
Artist - Song Name.mp3      LAME VBR -V2 (~190 kbps, the usual "192 VBR"), 44.1 kHz stereo, ID3v2.3 tags + cover art
```

```
n8n form ──► n8n workflow ──HTTP──► audio-processor (FastAPI + yt-dlp + ffmpeg + trim detector) ──► ./output/*.mp3
```

## What's in the repo

| Path | What it is |
| --- | --- |
| `n8n/yt-to-mp3-workflow.json` | The workflow. Import it into n8n (**Workflows → Import from file**). |
| `n8n/code/*.js` | Source of the workflow's Code nodes. `build_sdk.py` / `build_json.py` regenerate the workflow from them. |
| `processor/` | The audio service n8n calls: search, download, trim detection, MP3 encoding. |
| `docker-compose.yml` | Runs n8n and the processor together. |

## Quick start (local, Docker)

```bash
docker compose up -d --build
```

1. Open n8n at http://localhost:5678, import `n8n/yt-to-mp3-workflow.json`, then **Publish/Activate** it.
2. Open the **Bulk MP3 Request** node and copy the *Production URL*. That link is your form (it looks like
   `http://localhost:5678/form/yt-to-mp3`).
3. Paste your list and submit. You'll get a review page, then a results page with download links and a
   **Download all as .zip** link. The files are also saved in `./output`.

### If n8n already runs somewhere else (e.g. Zeabur, n8n Cloud, a VPS)

The processor has to run somewhere with ffmpeg, and your n8n instance has to be able to reach it:

* Deploy `processor/` (it has its own Dockerfile) next to n8n. On Zeabur, add a service from this repo with
  root directory `processor`, give it a volume at `/data`, and generate a domain on the
  port Zeabur assigns (the service listens on `$PORT`, e.g. 8080; set a `PORT` variable if Zeabur doesn't).
* In the workflow's **Settings** node, set `processor_url` to the address n8n can use (for example the internal
  service hostname, or `https://your-processor.example.com`).
* Set the processor's `PUBLIC_URL` env var to the address **your browser** can use. Preview and download links
  are built from it.
* YouTube often blocks datacenter IPs ("Sign in to confirm you're not a bot"). If that happens, export a
  `cookies.txt` from a logged-in browser and mount it at `/data/config/cookies.txt`.

## Using the form

One line per item:

```
https://www.youtube.com/watch?v=xxxxxxxxxxx            a video
https://www.youtube.com/playlist?list=xxxx             every video in the playlist (up to 200)
Daft Punk Around the World                             a song search (YouTube Music "Songs" first)
podcast: Huberman Lab sleep toolkit                    a podcast search (prefers full-length episodes)
song: Nirvana Lithium                                  force a music search
https://youtu.be/xxxx | Daft Punk - Around the World   force the file name
https://youtu.be/xxxx | 0:08-3:52                      force the cut (start-end; either side may be blank)
# lines starting with # are ignored; duplicate lines are removed
```

Form options:

* **Content type**: *Auto-detect* (music unless it's a long non-music video), *Music* or *Podcast*. For podcasts the
  file is named `Show - Episode title.mp3` and only silence is trimmed.
* **Trim**: *Smart*, *Silence only* or *Off*.
* **Review before export**: on the review page every track shows the reasons for its cut, with **▶ new start** and
  **▶ new ending** preview links. The file name, keep range and export/skip choice are all editable.

## How "Smart" trim works

The processor analyses each track at 0.1 s resolution (`processor/app/trim.py`, numpy only). Each pass can only
move the start later or the end earlier:

1. **Silence** at either edge (below −50 dBFS).
2. **SponsorBlock `music_offtopic`** segments that touch the start or end. These are crowd-sourced markers for the
   talking, skits and ads that music videos put around the song.
3. **Quiet ambience**: a sustained edge region (at least 2 s) more than 24 dB below the track's median loudness,
   such as room tone, whispering or noise.
4. **Repetitive loops**: an edge region that repeats one short pattern (1–16 s) at least 3 times and lasts at least
   20 s, like a beat looping under a video's intro or end credits. Detection uses self-similarity of log-band
   spectra. If the "loop" would cover most of the file, it's treated as the song itself (loop-based beats) and
   left alone.
5. A safety check: if the content cuts would leave less than 35% of the audio (or under 30 s), they are undone and
   the review page tells you.

Content cuts get a short fade-in (0.3 s) and a 2 s fade-out so they don't sound abrupt. Short intro riffs (such as a
3-bar intro) are deliberately kept. When a guess is wrong, fix it on the review page or with `| start-end` on the
input line.

**Songs searched by name come from YouTube Music's "Songs" shelf** where possible. That is the clean studio audio
with proper artist and track tags, so there's usually nothing to trim.

## Naming rules

`processor/app/naming.py`, in this order:

1. YouTube Music / "Artist - Topic" uploads: use the platform's `artist` and `track` tags.
2. A video title like `Artist - Song (Official Video) [4K]` becomes `Artist - Song`. Noise such as *Official
   Video/Audio, Lyrics, Visualizer, M/V, HD/4K, #hashtags* is removed; *Remix, Live, Acoustic…* are kept;
   `ft.`/`featuring` becomes `(feat. X)` and is moved from the artist into the title.
3. Otherwise: channel name (without *VEVO / - Topic / Official*) plus the cleaned title.

File names work on every system: no `<>:"/\|?*`, no Windows reserved names, NFC unicode, length-capped. Re-exporting
the same video overwrites its file; a *different* video with the same name gets `(2)` added.

## Processor API

| Method & path | Purpose |
| --- | --- |
| `POST /resolve` `{input, type}` | One pasted line → concrete video entries (search / playlist expansion) |
| `POST /analyze` `{url, type, trim, …}` | Download plus suggested name and keep range |
| `POST /export` `{id, artist, title, range \| start,end, batch}` | Encode the MP3 into `OUTPUT_DIR` |
| `GET /preview/{id}.mp3?at=…&part=head\|tail` | 15 s clip around a proposed cut |
| `GET /files/{name}` · `GET /batches/{batch}.zip` | Downloads |

Environment variables: `PUBLIC_URL`, `MP3_QUALITY` (`V2` default, or `ABR192`, `CBR192`, `V0`), `CACHE_TTL_HOURS`,
`API_KEY` (if set, add an `X-API-Key` header to the three HTTP nodes), `YTDLP_COOKIES`, `PLAYLIST_LIMIT`.

> **About "192 kbps VBR"**: LAME `-V2` is the standard preset for that. It averages about 190 kbps on real music,
> goes lower on quiet or simple passages, and goes higher on dense ones. If every file must average close to
> 192 kbps, set `MP3_QUALITY=ABR192`.

## Development

```bash
cd processor
pip install -r requirements-dev.txt
python -m pytest -q          # trim detector, naming, parsing, and an end-to-end API test (needs ffmpeg)
```

Only download content you have the rights to. Respect YouTube's Terms of Service and copyright law where you live.
