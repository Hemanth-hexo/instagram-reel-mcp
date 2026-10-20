"""Build the inspect/analyse reports: a dict (structured content) plus a markdown rendering and frames."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import media as mx
from . import transcribe as stt
from .config import Settings
from .editing import analyse_editing
from .providers.base import ReelMedia

Progress = Callable[[float, str], None]

EXPLAIN_GUIDE = """\
Explain the reel from three kinds of evidence: the frames (what is on screen), the edit timeline \
(cuts, transitions and camera motion, which still frames cannot show) and the transcript. Cover:
1. Hook and content: what happens in the first 1-3 seconds, the subject, setting and story beats.
2. Visuals: framing (close-up, medium, wide), composition, colour grade, lighting, on-screen text, \
captions, stickers or logos visible in the frames.
3. Editing and pacing: shot count, shot lengths, rhythm, whether cuts land on speech or beats, \
jump cuts, and whether the pace speeds up or slows down.
4. Transitions: each one with its timestamp and type (hard cut, dissolve, fade, flash, or \
motion_cut = whip/swipe/zoom-blur). Types are heuristics, so check them against the frames.
5. Animation and motion: camera moves from the shot table, motion graphics, kinetic text, zooms or \
effects (compare frames from the same shot).
6. Audio: voiceover vs music, tone, and how the script structures the reel.
Say when something is inferred or the evidence is thin, for example one frame for a long shot."""


@dataclass
class Frame:
    time_s: float
    shot: int | None
    jpeg: bytes


def pick_frame_times(shots: list[dict], duration: float, budget: int) -> list[tuple[float, int | None]]:
    """One frame per shot (evenly subsampled if there are more shots than `budget`); spare budget goes to
    the longest shots so motion inside a shot shows up as several frames."""
    if budget <= 0 or duration <= 0:
        return []
    if not shots:
        return [(duration * (i + 0.5) / budget, None) for i in range(budget)]
    if len(shots) >= budget:
        step = (len(shots) - 1) / max(budget - 1, 1)
        chosen = sorted({round(i * step) for i in range(budget)})
        return [((shots[i]["start_s"] + shots[i]["end_s"]) / 2, shots[i]["index"]) for i in chosen]
    alloc = [1] * len(shots)
    for _ in range(budget - len(shots)):
        k = max(range(len(shots)), key=lambda j: shots[j]["duration_s"] / alloc[j])
        if shots[k]["duration_s"] / (alloc[k] + 1) < 0.75:  # don't oversample short shots
            break
        alloc[k] += 1
    times = []
    for s, a in zip(shots, alloc):
        times += [(s["start_s"] + s["duration_s"] * (j + 0.5) / a, s["index"]) for j in range(a)]
    return sorted(times)


def _extract_frames(path: Path, picks: list[tuple[float, int | None]], width: int, workdir: Path) -> list[Frame]:
    frames = []
    for i, (t, shot) in enumerate(picks):
        out = mx.extract_frame(path, t, workdir / f"f{i:02d}.jpg", width)
        if out.is_file():
            frames.append(Frame(round(t, 2), shot, out.read_bytes()))
    return frames


def _access(media: ReelMedia) -> str:
    if media.source == "instagram_public":
        return "public Instagram reel (anonymous, no login)"
    return "user-supplied video file"


def inspect(media: ReelMedia, include_poster: bool = True, frame_width: int = 512) -> tuple[dict, list[Frame]]:
    mx.require_ffmpeg()
    tech = mx.probe(media.video_path)
    report = {
        "source": media.source,
        "access": _access(media),
        "metadata": media.metadata,
        "technical": tech,
        "notes": media.notes,
        "next_step": "Call analyse_reel with the same arguments for frames, transcript and edit analysis.",
    }
    frames: list[Frame] = []
    if include_poster:
        with tempfile.TemporaryDirectory() as tmp:
            t = min(1.0, tech["duration_s"] * 0.1)
            frames = _extract_frames(media.video_path, [(t, None)], frame_width, Path(tmp))
    return report, frames


def analyse(media: ReelMedia, settings: Settings, *, max_frames: int = 8, frame_width: int = 512,
            transcribe: bool = True, include_frames: bool = True,
            progress: Progress = lambda p, m: None) -> tuple[dict, list[Frame]]:
    mx.require_ffmpeg()
    progress(0.1, "Probing video")
    tech = mx.probe(media.video_path)
    duration = min(tech["duration_s"], settings.max_duration_s)

    progress(0.2, "Detecting cuts, transitions and motion")
    fps = min(30.0, tech["video"]["fps"] or 30.0)
    if duration > 120:
        fps = min(fps, 15.0)  # keep long videos fast
    editing = analyse_editing(mx.decode_gray(media.video_path, fps, max_seconds=duration), fps)

    frames: list[Frame] = []
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        if include_frames:
            progress(0.45, "Extracting key frames")
            picks = pick_frame_times(editing["shots"], duration, max_frames)
            frames = _extract_frames(media.video_path, picks, frame_width, workdir)

        audio: dict = {"present": tech["audio"] is not None}
        if tech["audio"]:
            progress(0.6, "Measuring audio")
            audio.update(mx.audio_levels(media.video_path))
            if transcribe:
                progress(0.7, "Transcribing speech (first run downloads the model)")
                wav = mx.extract_audio_wav(media.video_path, workdir / "audio.wav", duration)
                audio["transcript"] = stt.transcribe(wav, settings)
            else:
                audio["transcript"] = {"available": False, "note": "Skipped (transcribe=false)."}

    progress(0.95, "Writing report")
    report = {
        "source": media.source,
        "access": _access(media),
        "metadata": media.metadata,
        "technical": tech,
        "editing": editing,
        "audio": audio,
        "frames": [{"index": i + 1, "time_s": f.time_s, "shot": f.shot} for i, f in enumerate(frames)],
        "notes": media.notes,
        "how_to_explain": EXPLAIN_GUIDE,
    }
    return report, frames


# ----------------------------------------------------------------------------- markdown rendering

def _num(n) -> str:
    if n is None:
        return "?"
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.1f}K" if n >= 1e4 else str(n)


def _header(report: dict) -> list[str]:
    meta = report.get("metadata", {})
    who = f"@{meta['username']}" if meta.get("username") else meta.get("file_name", "uploaded video")
    lines = [f"# Reel: {who}" + (f" ({meta['shortcode']})" if meta.get("shortcode") else "")]
    lines.append(f"Source: {report.get('access') or report['source']}")
    if meta.get("url"):
        lines.append(f"URL: {meta['url']}")
    stats = []
    if meta.get("upload_timestamp"):
        stats.append("posted " + datetime.fromtimestamp(meta["upload_timestamp"], timezone.utc).strftime("%Y-%m-%d"))
    for key, label in (("view_count", "views"), ("like_count", "likes"), ("comment_count", "comments")):
        if meta.get(key) is not None:
            stats.append(f"{_num(meta[key])} {label}")
    if meta.get("music"):
        stats.append(f"audio: {meta['music']}")
    if stats:
        lines.append(" · ".join(stats))
    if meta.get("caption"):
        cap = meta["caption"].strip().replace("\n", " ")
        lines.append(f"Caption: \"{cap[:600]}{'…' if len(cap) > 600 else ''}\"")
    official = meta.get("official_oembed")
    if official:
        lines.append("Official Meta oEmbed: " + ", ".join(f"{k}={v}" for k, v in official.items()
                                                          if k in ("author_name", "title", "media_id")))
    return lines


def _technical(t: dict) -> str:
    v, a = t["video"], t["audio"]
    parts = [f"{t['duration_s']:.1f}s", f"{v['width']}x{v['height']} ({v['aspect_ratio']})",
             f"{v['fps']} fps", v["codec"] or "?"]
    parts.append(f"{a['codec']} audio" if a else "no audio track")
    parts.append(f"{t['size_mb']} MB")
    return " · ".join(str(p) for p in parts)


def render_inspect(report: dict, frames: list[Frame]) -> str:
    lines = _header(report) + ["", "## Technical", _technical(report["technical"])]
    if report.get("notes"):
        lines += ["", "Notes: " + " ".join(report["notes"])]
    if frames:
        lines += ["", f"Poster frame at {frames[0].time_s}s follows."]
    lines += ["", report["next_step"]]
    return "\n".join(lines)


def render_analysis(report: dict) -> str:
    ed, au = report["editing"], report["audio"]
    pace = ed.get("pace", {})
    lines = _header(report) + ["", "## Technical", _technical(report["technical"]), "", "## Editing & pacing"]
    if pace:
        lines.append(f"{pace['shot_count']} shots, {pace['transition_count']} transitions · avg shot "
                     f"{pace['avg_shot_s']}s (median {pace['median_shot_s']}s) · {pace['cuts_per_10s']} "
                     f"transitions/10s · pace: {pace['pace']} · overall motion: {pace['overall_motion']}")
        a, b = pace["transitions_first_half"], pace["transitions_second_half"]
        if a + b >= 4 and (b >= 1.5 * max(a, 1) or a >= 1.5 * max(b, 1)):
            lines.append(f"Pacing {'accelerates' if b > a else 'slows down'}: {a} transitions in the first half "
                         f"vs {b} in the second.")
    if ed["shots"]:
        lines += ["", "| Shot | Time | Length | Motion | Camera move |", "|---|---|---|---|---|"]
        for s in ed["shots"]:
            lines.append(f"| {s['index']} | {s['start_s']:.2f}–{s['end_s']:.2f}s | {s['duration_s']}s | "
                         f"{s['motion']} | {s['camera_move'] or '—'} |")
    if ed["transitions"]:
        lines += ["", "Transitions: " + " · ".join(
            f"{t['at_s']:.2f}s {t['type'].replace('_', ' ')}" + (f" ({t['duration_s']}s)" if t["duration_s"] else "")
            for t in ed["transitions"])]
    lines.append(f"(Signals measured at {ed['analysed_fps']} fps; transition types and camera moves are heuristics.)")

    lines += ["", "## Audio"]
    if not au["present"]:
        lines.append("No audio track.")
    else:
        lines.append(f"Mean {au.get('mean_volume_db')} dBFS, peak {au.get('peak_volume_db')} dBFS, "
                     f"{au.get('silent_s')}s near-silent.")
        tr = au.get("transcript", {})
        if not tr.get("available"):
            lines.append(tr.get("note", "No transcript."))
        elif not tr["speech_detected"]:
            lines.append(f"No clear speech detected (likely music or ambient sound only). Language guess: {tr['language']}.")
        else:
            lines.append(f"Speech detected ({tr['language']}, p={tr['language_probability']}). Transcript:")
            total = 0
            for s in tr["segments"]:
                line = f"[{s['start_s']:.1f}–{s['end_s']:.1f}s] {s['text']}"
                total += len(line)
                if total > 5000:
                    lines.append("… (truncated; full text in structured content)")
                    break
                lines.append(line)

    if report["frames"]:
        lines += ["", "## Frames", f"{len(report['frames'])} frames follow, each labelled with its timestamp and shot."]
    if report.get("notes"):
        lines += ["", "Notes: " + " ".join(report["notes"])]
    lines += ["", "## How to explain this reel", report["how_to_explain"]]
    return "\n".join(lines)
