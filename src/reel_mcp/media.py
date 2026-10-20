"""Thin ffmpeg / ffprobe wrappers. Every function is blocking; call them from a worker thread."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from fractions import Fraction
from math import gcd
from pathlib import Path

import numpy as np

from .errors import ReelError


def require_ffmpeg() -> None:
    missing = [b for b in ("ffmpeg", "ffprobe") if shutil.which(b) is None]
    if missing:
        raise ReelError("ffmpeg_missing", f"{' and '.join(missing)} not found on PATH.",
                        "Install ffmpeg (macOS: brew install ffmpeg, Debian/Ubuntu: apt install ffmpeg).")


def _run(cmd: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)
    except subprocess.TimeoutExpired:
        raise ReelError("processing_timeout", f"{cmd[0]} took longer than {timeout}s.") from None
    except subprocess.CalledProcessError as exc:
        tail = exc.stderr.decode(errors="replace").strip().splitlines()[-3:]
        raise ReelError("bad_file", f"{cmd[0]} could not process the video: {' '.join(tail)[:300]}") from None


def _fps(rate: str | None) -> float | None:
    try:
        value = float(Fraction(rate)) if rate and rate != "0/0" else None
    except (ValueError, ZeroDivisionError):
        return None
    return round(value, 3) if value else None


def probe(path: Path) -> dict:
    """Technical description of the file. Raises `bad_file` if there is no video stream."""
    out = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    data = json.loads(out.stdout or b"{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise ReelError("bad_file", "The file has no video stream.")
    fmt = data.get("format", {})
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    rotation = 0
    for side in video.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = int(side["rotation"])
    if abs(rotation) in (90, 270):
        width, height = height, width  # ffmpeg auto-rotates on decode; report what viewers see
    duration = float(fmt.get("duration") or video.get("duration") or 0)
    info = {
        "duration_s": round(duration, 3),
        "size_mb": round(int(fmt.get("size") or path.stat().st_size) / 1_048_576, 2),
        "container": fmt.get("format_name"),
        "bitrate_kbps": round(int(fmt["bit_rate"]) / 1000) if fmt.get("bit_rate") else None,
        "video": {
            "codec": video.get("codec_name"),
            "width": width,
            "height": height,
            "aspect_ratio": _aspect(width, height),
            "fps": _fps(video.get("avg_frame_rate")) or _fps(video.get("r_frame_rate")),
        },
        "audio": None,
    }
    if audio:
        info["audio"] = {
            "codec": audio.get("codec_name"),
            "sample_rate": int(audio.get("sample_rate") or 0) or None,
            "channels": audio.get("channels"),
        }
    return info


def _aspect(w: int, h: int) -> str | None:
    if not w or not h:
        return None
    for name, ratio in (("9:16", 9 / 16), ("4:5", 4 / 5), ("1:1", 1.0), ("16:9", 16 / 9), ("3:4", 3 / 4),
                        ("4:3", 4 / 3), ("2:3", 2 / 3), ("3:2", 3 / 2)):
        if abs(w / h - ratio) < 0.03:
            return name
    g = gcd(w, h)
    return f"{w // g}:{h // g}"


def extract_frame(path: Path, t: float, out: Path, width: int = 512) -> Path:
    """Write one JPEG at time `t` (seconds), scaled to `width` px wide."""
    _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(t, 0):.3f}", "-i", str(path), "-frames:v", "1",
          "-vf", f"scale={width}:-2", "-q:v", "5", str(out)], timeout=60)
    if not out.is_file():  # seeking past the last frame produces nothing
        _run(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.2", "-i", str(path), "-frames:v", "1",
              "-vf", f"scale={width}:-2", "-q:v", "5", str(out)], timeout=60)
    return out


def decode_gray(path: Path, fps: float, width: int = 96, height: int = 54, max_seconds: float = 300) -> np.ndarray:
    """Decode to an (N, height, width) uint8 array of grayscale frames at a constant `fps`."""
    out = _run(["ffmpeg", "-v", "error", "-t", str(max_seconds), "-i", str(path), "-an",
                "-vf", f"fps={fps},scale={width}:{height},format=gray",
                "-f", "rawvideo", "-pix_fmt", "gray", "-"], timeout=300)
    frame_bytes = width * height
    n = len(out.stdout) // frame_bytes
    return np.frombuffer(out.stdout[: n * frame_bytes], dtype=np.uint8).reshape(n, height, width)


def extract_audio_wav(path: Path, out: Path, max_seconds: float = 300) -> Path:
    """16 kHz mono WAV, the format speech models expect."""
    _run(["ffmpeg", "-v", "error", "-y", "-t", str(max_seconds), "-i", str(path), "-vn",
          "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)])
    return out


def audio_levels(path: Path) -> dict:
    """Mean/peak loudness in dBFS and how much of the track is near-silent."""
    out = _run(["ffmpeg", "-v", "info", "-nostats", "-i", str(path), "-vn",
                "-af", "silencedetect=noise=-45dB:d=0.4,volumedetect", "-f", "null", "-"])
    err = out.stderr.decode(errors="replace")
    mean = re.search(r"mean_volume:\s*(-?[\d.]+) dB", err)
    peak = re.search(r"max_volume:\s*(-?[\d.]+) dB", err)
    silence = sum(float(x) for x in re.findall(r"silence_duration:\s*([\d.]+)", err))
    return {
        "mean_volume_db": float(mean.group(1)) if mean else None,
        "peak_volume_db": float(peak.group(1)) if peak else None,
        "silent_s": round(silence, 2),
    }
