"""Fallback: videos the user supplies (via the /upload endpoint or by dropping them in the inbox)."""

from __future__ import annotations

import re
import secrets
from pathlib import Path

from ..config import Settings
from ..errors import ReelError
from .base import ReelMedia, purge_older_than

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


def new_upload_path(filename: str, settings: Settings) -> Path:
    """A fresh, safe path in the inbox for an uploaded file. Raises on unsupported extensions."""
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ReelError("bad_file", f"Unsupported file type '{ext or '?'}'. Allowed: "
                        + ", ".join(sorted(ALLOWED_EXTENSIONS)))
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(filename).stem).strip("-")[:40] or "video"
    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    return settings.inbox_dir / f"{secrets.token_hex(4)}-{stem}{ext}"


def list_inbox(settings: Settings) -> list[str]:
    if not settings.inbox_dir.is_dir():
        return []
    return sorted(p.name for p in settings.inbox_dir.iterdir()
                  if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS)


def resolve_user_file(name: str, settings: Settings) -> ReelMedia:
    """Look up `name` inside the inbox. Bare file names only, so no other path can be read."""
    purge_older_than(settings.inbox_dir, settings.upload_ttl_s)
    name = (name or "").strip()
    inbox = settings.inbox_dir.resolve()
    candidate = (inbox / name).resolve()
    if not name or Path(name).name != name or candidate.parent != inbox:
        raise ReelError("bad_file", "video_file must be a bare file name from the inbox / upload "
                        "response, not a path.")
    if not candidate.is_file():
        available = list_inbox(settings)
        raise ReelError("file_not_found", f"No uploaded video named '{name}'.",
                        "Available: " + (", ".join(available) if available else "none, upload one first"))
    if candidate.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ReelError("bad_file", f"Unsupported file type '{candidate.suffix}'.")
    return ReelMedia(candidate, "user_file", {"file_name": name})
