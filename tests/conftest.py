"""Synthetic test clips with known edits, generated with ffmpeg's built-in sources."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

S = "s=540x960:r=30"


def _src(name: str) -> str:
    return f"{name}{':' if '=' in name else '='}{S}"


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def _concat(out: Path, sources: list[str], seconds: list[float]) -> None:
    inputs, chains = [], []
    for i, (src, sec) in enumerate(zip(sources, seconds)):
        inputs += ["-f", "lavfi", "-t", str(sec), "-i", _src(src)]
        chains.append(f"[{i}:v]format=yuv420p,setsar=1[v{i}]")
    graph = ";".join(chains) + ";" + "".join(f"[v{i}]" for i in range(len(sources))) \
        + f"concat=n={len(sources)}:v=1:a=0[out]"
    _ffmpeg(*inputs, "-filter_complex", graph, "-map", "[out]", "-c:v", "libx264", "-preset", "veryfast", str(out))


@pytest.fixture(scope="session")
def clips(tmp_path_factory) -> dict[str, Path]:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    d = tmp_path_factory.mktemp("clips")
    out = {k: d / f"{k}.mp4" for k in ("cuts", "dissolve", "fades", "pan", "exposure", "flash", "static")}

    # Cuts at 1.5s, 3.0s and 4.5s.
    _concat(out["cuts"], ["testsrc2", "smptehdbars", "mandelbrot", "rgbtestsrc"], [1.5, 1.5, 1.5, 1.5])
    # 0.6s crossfade starting at 1.7s.
    _ffmpeg("-f", "lavfi", "-t", "2.5", "-i", _src("testsrc2"), "-f", "lavfi", "-t", "2.5", "-i", _src("smptehdbars"),
            "-filter_complex", "[0:v]format=yuv420p,setsar=1[a];[1:v]format=yuv420p,setsar=1[b];"
            "[a][b]xfade=transition=fade:duration=0.6:offset=1.7[out]",
            "-map", "[out]", "-c:v", "libx264", "-preset", "veryfast", str(out["dissolve"]))
    # Fade in from black over 0.5s, fade out to black from 2.4s.
    _ffmpeg("-f", "lavfi", "-t", "3", "-i", _src("smptehdbars"), "-vf",
            "fade=t=in:st=0:d=0.5,fade=t=out:st=2.4:d=0.6,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", str(out["fades"]))
    # Camera pans right across a wide frame (content moves left).
    _ffmpeg("-f", "lavfi", "-t", "4", "-i", "testsrc2=s=1800x960:r=30", "-vf",
            "crop=540:960:x='min(t*300\\,1200)':y=0,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", str(out["pan"]))
    # Same shot, brightness ramp (must NOT be called a dissolve).
    _ffmpeg("-f", "lavfi", "-t", "3", "-i", _src("smptehdbars"), "-vf",
            "eq=brightness='0.25*t-0.35':eval=frame,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", str(out["exposure"]))
    # Two white frames between two shots at 1.5s.
    _concat(out["flash"], ["testsrc2", "color=c=white", "smptehdbars"], [1.5, 2 / 30, 1.5])
    _ffmpeg("-f", "lavfi", "-t", "3", "-i", _src("smptehdbars"), "-vf", "format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", str(out["static"]))
    return out


@pytest.fixture(scope="session")
def speech_clip(tmp_path_factory, clips) -> Path:
    """A 2-shot clip with spoken English (macOS `say`); skipped where `say` is missing."""
    if not shutil.which("say"):
        pytest.skip("macOS `say` not available for generating speech")
    d = tmp_path_factory.mktemp("speech")
    aiff = d / "voice.aiff"
    subprocess.run(["say", "-o", str(aiff), "Welcome to the reel. Today we test the transcription pipeline."],
                   check=True)
    out = d / "speech.mp4"
    _ffmpeg("-i", str(clips["cuts"]), "-i", str(aiff), "-map", "0:v", "-map", "1:a", "-c:v", "copy",
            "-c:a", "aac", "-shortest", str(out))
    return out
