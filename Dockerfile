FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home app \
    && mkdir /data && chown app /data

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[transcribe]"

# Everything mutable (download cache, uploads, speech model) lives on the /data volume.
ENV REEL_HOST=0.0.0.0 \
    REEL_PORT=8000 \
    REEL_DATA_DIR=/data \
    HF_HOME=/data/models
VOLUME /data
EXPOSE 8000
USER app

HEALTHCHECK --interval=30s --timeout=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1

CMD ["reel-mcp", "serve"]
