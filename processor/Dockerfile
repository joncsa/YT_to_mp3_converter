FROM python:3.12-slim

# ffmpeg: decode/trim/encode (libmp3lame). deno: JavaScript runtime yt-dlp needs for YouTube.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

ENV CACHE_DIR=/data/cache \
    OUTPUT_DIR=/data/output \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
# Listens on $PORT (Zeabur/Railway/Render set it, e.g. 8080); 8000 when unset (docker compose).
EXPOSE 8080 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8000'))"
# Update yt-dlp on every start: YouTube changes often and stale versions break downloads.
CMD pip install --no-cache-dir -q -U "yt-dlp[default]" || true; \
    exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1 --timeout-keep-alive 1900
