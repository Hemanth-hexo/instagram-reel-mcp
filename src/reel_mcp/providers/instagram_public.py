"""Anonymous retrieval of PUBLIC reels.

This fetches exactly what Instagram serves to a logged-out browser. It never sends cookies,
credentials or session tokens, so private reels (and anything behind Instagram's login wall)
fail with `not_public` instead of being accessed. Uses yt-dlp's Instagram extractor; keep
yt-dlp up to date (`pip install -U yt-dlp`) because Instagram changes its pages often.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from ..config import Settings
from ..errors import UPLOAD_HINT, ReelError, classify_fetch_failure
from ..urls import ReelRef
from .base import ReelMedia, purge_older_than

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class _SilentLogger:
    """Swallow yt-dlp output; failures surface as exceptions instead."""

    def debug(self, msg: str) -> None: ...
    def info(self, msg: str) -> None: ...
    def warning(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...


def _clean(msg: str) -> str:
    msg = re.sub(r"\x1b\[[0-9;]*m", "", msg)
    msg = re.sub(r"^ERROR:\s*", "", msg.strip())
    # yt-dlp suggests passing browser cookies; this server deliberately never does that.
    return re.sub(r"\s*Use --cookies.*$", "", msg, flags=re.S).strip()


def _ydl_options(settings: Settings, out_dir: Path) -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _SilentLogger(),
        "noplaylist": True,
        # Prefer a single progressive MP4 (has audio); otherwise merge best video+audio <= 1080p.
        "format": "b[ext=mp4][height<=1080]/bv*[height<=1080]+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / "video.%(ext)s"),
        "max_filesize": settings.max_video_mb * 1024 * 1024,
        "socket_timeout": 20,
        "retries": 2,
        # Explicitly anonymous: no cookies, no netrc, no credentials -> public reels only.
        "cookiefile": None,
        "cookiesfrombrowser": None,
        "usenetrc": False,
        "username": None,
        "password": None,
    }


def _normalise(info: dict, ref: ReelRef) -> dict:
    keep = {
        "shortcode": info.get("display_id") or info.get("id") or ref.shortcode,
        "url": info.get("webpage_url") or ref.url,
        "username": info.get("channel") or info.get("uploader_id") or ref.username,
        "display_name": info.get("uploader"),
        "caption": info.get("description"),
        "title": info.get("title"),
        "duration_s": info.get("duration"),
        "upload_timestamp": info.get("timestamp"),
        "upload_date": info.get("upload_date"),
        "like_count": info.get("like_count"),
        "comment_count": info.get("comment_count"),
        "view_count": info.get("view_count"),
        "thumbnail_url": info.get("thumbnail"),
        "music": info.get("track") or info.get("artist"),
    }
    return {k: v for k, v in keep.items() if v not in (None, "")}


def _lock_for(code: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(code, threading.Lock())


def fetch_public_reel(ref: ReelRef, settings: Settings) -> ReelMedia:
    """Download a public reel into the cache (reused for `cache_ttl_s`). Blocking."""
    import yt_dlp  # imported lazily: heavy module

    purge_older_than(settings.cache_dir, settings.cache_ttl_s)
    out_dir = settings.cache_dir / ref.shortcode
    meta_file = out_dir / "meta.json"

    with _lock_for(ref.shortcode):
        cached = sorted(out_dir.glob("video.*")) if out_dir.is_dir() else []
        if cached and meta_file.is_file():
            out_dir.touch()
            return ReelMedia(cached[0], "instagram_public", json.loads(meta_file.read_text()),
                             ["Served from cache."])

        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            with yt_dlp.YoutubeDL(_ydl_options(settings, out_dir)) as ydl:
                info = ydl.extract_info(ref.url, download=False)
                if info.get("_type") == "playlist":  # carousel post: take the first video
                    info = next((e for e in info.get("entries") or [] if e and e.get("formats")), None)
                    if info is None:
                        raise ReelError("no_video", "This post has no video (photo or carousel of images).")
                if not info.get("formats") and not info.get("url"):
                    raise ReelError("no_video", "This post has no downloadable video.", UPLOAD_HINT)
                duration = info.get("duration") or 0
                if duration > settings.max_duration_s:
                    raise ReelError("too_long", f"Video is {duration:.0f}s; the limit is "
                                    f"{settings.max_duration_s}s (REEL_MAX_DURATION_S).")
                info = ydl.process_ie_result(info, download=True)
        except ReelError:
            _discard(out_dir)
            raise
        except Exception as exc:  # yt-dlp raises DownloadError/ExtractorError with a message
            _discard(out_dir)
            raise classify_fetch_failure(_clean(str(exc))) from None

        files = sorted(p for p in out_dir.glob("video.*") if not p.name.endswith(".part"))
        if not files:
            _discard(out_dir)
            raise ReelError("too_large", f"Video exceeds {settings.max_video_mb} MB (REEL_MAX_VIDEO_MB).")

        metadata = _normalise(info, ref)
        metadata["fetched_at"] = int(time.time())
        meta_file.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
        return ReelMedia(files[0], "instagram_public", metadata)


def _discard(out_dir: Path) -> None:
    for p in out_dir.glob("*"):
        p.unlink(missing_ok=True)
    try:
        out_dir.rmdir()
    except OSError:
        pass
