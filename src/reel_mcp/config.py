"""Settings, read from REEL_* environment variables (a local .env file is honoured too)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Tiny .env reader: KEY=VALUE lines, existing environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in ("1", "true", "yes", "on")


def _split(name: str) -> list[str]:
    return [p.strip() for p in os.environ.get(name, "").split(",") if p.strip()]


@dataclass(frozen=True)
class Settings:
    # --- server
    host: str = "127.0.0.1"
    port: int = 8000
    mcp_path: str = "/mcp"
    auth_token: str | None = None            # Bearer token required on every request when set
    allowed_hosts: list[str] = field(default_factory=list)  # extra Host headers (tunnel/domain)
    # --- storage
    data_dir: Path = Path("data")
    cache_ttl_s: int = 3600
    upload_ttl_s: int = 24 * 3600
    # --- limits
    max_video_mb: int = 150
    max_duration_s: int = 300
    max_upload_mb: int = 200
    max_concurrent_jobs: int = 2
    # --- access methods
    public_fetch: bool = True                # anonymous download of PUBLIC reels (yt-dlp, no login)
    meta_access_token: str | None = None     # Meta Graph token enabling official oEmbed metadata
    graph_version: str = "v23.0"
    # --- transcription
    whisper_model: str = "base"
    whisper_device: str = "auto"
    whisper_compute_type: str = "int8"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    def ensure_dirs(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    _load_dotenv(Path(".env"))
    e = os.environ.get
    path = e("REEL_MCP_PATH", "/mcp")
    return Settings(
        host=e("REEL_HOST", "127.0.0.1"),
        port=int(e("REEL_PORT") or e("PORT") or "8000"),  # PORT: set by Vercel, Render, Cloud Run, ...
        mcp_path="/" + path.strip("/"),
        auth_token=e("REEL_AUTH_TOKEN") or None,
        allowed_hosts=_split("REEL_ALLOWED_HOSTS"),
        data_dir=Path(e("REEL_DATA_DIR", "data")).expanduser().resolve(),
        cache_ttl_s=int(e("REEL_CACHE_TTL_S", "3600")),
        upload_ttl_s=int(e("REEL_UPLOAD_TTL_S", str(24 * 3600))),
        max_video_mb=int(e("REEL_MAX_VIDEO_MB", "150")),
        max_duration_s=int(e("REEL_MAX_DURATION_S", "300")),
        max_upload_mb=int(e("REEL_MAX_UPLOAD_MB", "200")),
        max_concurrent_jobs=int(e("REEL_MAX_CONCURRENT_JOBS", "2")),
        public_fetch=_bool("REEL_PUBLIC_FETCH", True),
        meta_access_token=e("REEL_META_ACCESS_TOKEN") or None,
        graph_version=e("REEL_GRAPH_VERSION", "v23.0"),
        whisper_model=e("REEL_WHISPER_MODEL", "base"),
        whisper_device=e("REEL_WHISPER_DEVICE", "auto"),
        whisper_compute_type=e("REEL_WHISPER_COMPUTE_TYPE", "int8"),
    )
