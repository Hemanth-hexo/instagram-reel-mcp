from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ReelMedia:
    """A video file on local disk plus whatever metadata the access method provided."""

    video_path: Path
    source: str                      # "instagram_public" | "user_file"
    metadata: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def purge_older_than(directory: Path, ttl_s: int) -> None:
    """Delete direct children (files or dirs) of `directory` not modified within `ttl_s`."""
    if not directory.is_dir():
        return
    cutoff = time.time() - ttl_s
    for child in directory.iterdir():
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        except OSError:
            pass
