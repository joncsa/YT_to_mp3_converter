# Handoff: Bulk YouTube → MP3 (n8n + audio processor)

_Status as of 2026-09-25: **working end-to-end** on Zeabur._

## What it is
Bulk YouTube → MP3 converter. You paste links, song names or podcast titles into an n8n form. It downloads the audio,
suggests cuts for non-music parts and repetitive loops at the start and end, shows a review page, and exports
`Artist - Song Name.mp3`: LAME VBR -V2 (~190 kbps, the usual "192 VBR"), 44.1 kHz, ID3v2.3 tags, cover art.

- Repo: `joncsa/YT_to_mp3_converter`, branch **`claude/wonderful-carson-nz5cbd`**. Not merged into the default
  branch; no pull request yet.
- Details: `README.md`.

## Live setup
| Piece | Where |
| --- | --- |
| n8n | `https://axsara.zeabur.app`. The workflow is **"Bulk YouTube → MP3 (Artist - Song Name)"** (id `d9NjIxxUqhyTwLAN`). Form path: `/form/yt-to-mp3` |
| Processor | Zeabur project **Axsara_Main** (Tencent Singapore, 2 cores / 4 GB, shared with n8n, Postgres and other services). Service **yt-to-mp3-converter**, root dir `/processor`, port **8080** |
| Public URL | `https://yt-processor.zeabur.app`. Private host: `yt-to-mp3-converter.zeabur.internal:8080` |
| Volume | `/data` (cache, output MP3s, cookies at `/data/config/cookies.txt`) |
| Zeabur variables | `PUBLIC_URL=https://yt-processor.zeabur.app`, `PORT=8080`, `API_KEY=<secret>` |
| n8n workflow | The **Settings** node has `processor_url=https://yt-processor.zeabur.app`. The 3 HTTP Request nodes send an `X-API-Key` header |

Useful URLs: `/health` (shows `ok` and whether cookies are loaded), `/admin/cookies` (paste cookies.txt plus the API key),
`/files/<name>`, `/batches/<batch>.zip`.

## Code map
- `processor/app/main.py`: FastAPI endpoints `/resolve`, `/analyze`, `/export`, `/preview`, `/files`, `/batches`,
  `/admin/cookies`. Heavy work is limited to `MAX_JOBS` (default 1) at a time.
- `processor/app/media.py`: yt-dlp search and download. On HTTP 403 it retries with other YouTube player clients
  (`YT_PLAYER_CLIENTS`). Also: SponsorBlock lookups, ffmpeg decode and encode, and optional PO-token support
  (`POT_PROVIDER_URL`).
- `processor/app/trim.py`: cut detection (silence → SponsorBlock `music_offtopic` → quiet ambience → repetitive
  intro/outro loops → safety revert). Numpy only. Features are computed in chunks so memory stays low.
- `processor/app/naming.py`: builds `Artist - Song Name` and makes file names safe on every OS.
- `processor/app/parsing.py`: input line syntax (`podcast:`, `song:`, `| Artist - Title`, `| 0:12-3:45`).
- `n8n/code/*.js`: source of the Code nodes. `n8n/build_sdk.py` and `n8n/build_json.py` regenerate
  `workflow.sdk.ts` and `yt-to-mp3-workflow.json`.
- Tests: `cd processor && pip install -r requirements-dev.txt && python -m pytest -q` (29 passing; needs ffmpeg).

## Problems solved along the way
1. The server was hard-coded to port 8000 → it now listens on `$PORT`.
2. Stuck on "Starting" because `pip install` ran at boot → yt-dlp is now updated at build time; removed the Dockerfile `VOLUME`.
3. YouTube "Sign in to confirm you're not a bot" → cookies pasted via `/admin/cookies`.
4. 502 crash: ~1.8 GB of memory per 30-minute analysis ran out of memory on the shared 4 GB server → chunked
   features, lower sample rate for long files, one heavy job at a time.
5. HTTP 403 on media → single-session download plus automatic fallback to other player clients.

## Known limits / next ideas
- Cookies expire. If the "not a bot" errors return, re-export them from an InPrivate window (the Edge
  extension "Get cookies.txt LOCALLY", Netscape format) and paste them at `/admin/cookies`.
- If 403s come back: add the Zeabur service `brainicism/bgutil-ytdlp-pot-provider` (port 4416) and set
  `POT_PROVIDER_URL=http://<its private host>:4416` on the processor.
- Private, deleted and region-blocked videos can't be downloaded.
- The live n8n copy has its sticky notes placed on top of nodes; the JSON file in the repo has them placed properly.
- Not yet done: opening a pull request / merging into the default branch; real-world tuning of loop detection
  (defaults: loop ≥20 s, ≥3 repeats, similarity 0.9); a persistent log of form submissions (e.g. n8n Data Table).
