"""Edit/motion signals computed from low-resolution grayscale frames (numpy only).

Everything here is a heuristic meant to give the client model facts it cannot see in a handful of
still frames: where the cuts are, how transitions were made, how fast it is cut, and how the
camera moves. Thresholds are tuned for 96x54 frames in the 0..1 range.
"""

from __future__ import annotations

import numpy as np

CUT_MIN = 0.07        # mean abs pixel change that can be a cut
CUT_RATIO = 3.0       # ... and must exceed the local baseline by this factor
CUT_STRONG = 0.14     # change this large is a cut even if the histogram barely moves
HIST_MIN = 0.10       # histogram distance that confirms a weaker cut
GRADUAL_MIN = 0.08    # total change across a dissolve/fade window
BLACK, WHITE = 0.07, 0.90


def _correlation(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a - a.mean(), b - b.mean()
    return float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-9))


def _rolling_median(x: np.ndarray, k: int) -> np.ndarray:
    padded = np.pad(x, k, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(padded, 2 * k + 1), axis=1)


def _hist_dist(frames_u8: np.ndarray) -> np.ndarray:
    q = frames_u8.reshape(len(frames_u8), -1) >> 3  # 32 bins
    hist = np.stack([np.bincount(row, minlength=32) for row in q]).astype(np.float32)
    hist /= q.shape[1]
    out = np.zeros(len(frames_u8), dtype=np.float32)
    out[1:] = 0.5 * np.abs(hist[1:] - hist[:-1]).sum(axis=1)
    return out


def _detect_cuts(d: np.ndarray, hd: np.ndarray, fps: float) -> list[int]:
    base = _rolling_median(d, max(2, round(fps * 0.5)))
    strong = d >= CUT_STRONG
    candidate = (d >= CUT_MIN) & (d >= CUT_RATIO * base + 0.01) & (strong | (hd >= HIST_MIN))
    cuts: list[int] = []
    for i in np.flatnonzero(candidate):
        lo, hi = max(1, i - 2), min(len(d), i + 3)
        if d[i] < d[lo:hi].max():
            continue  # not the local peak
        if cuts and i - cuts[-1] < 2:
            continue
        cuts.append(int(i))
    return cuts


def _detect_gradual(f: np.ndarray, d: np.ndarray, fps: float, cuts: list[int]) -> list[dict]:
    """Dissolves and fades: big change between window ends, spread evenly, no spike,
    and the middle frame looks like a blend of the two ends."""
    h, w = f.shape[1] // 2 * 2, f.shape[2] // 2 * 2
    small = f[:, :h, :w].reshape(len(f), h // 2, 2, w // 2, 2).mean(axis=(2, 4))
    n = len(small)
    cum = np.concatenate([[0.0], np.cumsum(d[1:])])  # cum[j] = sum d[1..j]
    lengths = sorted({max(3, round(fps * s)) for s in (0.2, 0.33, 0.5, 0.75, 1.0, 1.5)})
    cut_mask = np.zeros(n, dtype=bool)
    for c in cuts:
        cut_mask[max(0, c - 1): c + 2] = True
    cands = []
    for L in lengths:
        if n <= L:
            continue
        a, b = small[:-L], small[L:]
        mid = small[L // 2: n - L + L // 2]
        end_diff = np.abs(b - a).mean(axis=(1, 2))
        path = cum[L:n] - cum[0:n - L]
        peak = np.lib.stride_tricks.sliding_window_view(d[1:], L).max(axis=1)[: n - L]
        resid = np.abs(mid - (a + b) / 2).mean(axis=(1, 2))
        ok = (end_diff >= GRADUAL_MIN) & (end_diff >= 0.75 * path) & (peak <= 0.5 * end_diff) \
            & (resid <= 0.35 * end_diff)
        for i in np.flatnonzero(ok):
            if cut_mask[i: i + L + 1].any():
                continue
            cands.append((float(end_diff[i]), int(i), int(L)))
    events: list[dict] = []
    for score, i, L in sorted(cands, reverse=True):
        seg = d[i + 1: i + L + 1]
        active = np.flatnonzero(seg >= 0.35 * seg.max())
        start, end = i + int(active[0]), i + int(active[-1]) + 1
        if any(not (end < e["_start"] or start > e["_end"]) for e in events):
            continue
        lum_a, lum_b = float(f[start].mean()), float(f[end].mean())
        if lum_b <= BLACK:
            kind = "fade_to_black"
        elif lum_a <= BLACK:
            kind = "fade_from_black"
        elif lum_b >= WHITE or lum_a >= WHITE:
            kind = "fade_white"
        else:
            # Same picture, different brightness is an exposure/grade change, not a dissolve.
            if _correlation(f[start], f[end]) >= 0.7:
                continue
            kind = "dissolve"
        events.append({"_start": start, "_end": end, "type": kind, "score": score})
    return events


def _detect_flashes(luma: np.ndarray, fps: float) -> list[dict]:
    """Runs of at most ~0.12s that are near-white (flash) or near-black (dip) between normal frames."""
    events = []
    max_len = max(1, round(fps * 0.12))
    for kind, mask in (("flash_white", luma >= WHITE), ("dip_to_black", luma <= BLACK)):
        i, n = 1, len(luma)
        while i < n - 1:
            if not mask[i]:
                i += 1
                continue
            j = i
            while j < n and mask[j]:
                j += 1
            if j < n and j - i <= max_len:
                level = luma[i:j].mean()
                if abs(luma[i - 1] - level) >= 0.25 and abs(luma[j] - level) >= 0.25:
                    events.append({"_start": i, "_end": j, "type": kind, "score": float(abs(luma[i - 1] - level))})
            i = j
    return events


def _global_shifts(f: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Frame-to-frame content translation (dx, dy in px) and confidence, via phase correlation."""
    n, h, w = f.shape
    win = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)
    spec = np.fft.fft2((f - f.mean(axis=(1, 2), keepdims=True)) * win)
    cross = spec[1:] * np.conj(spec[:-1])
    cross /= np.abs(cross) + 1e-9
    corr = np.fft.ifft2(cross).real
    flat = corr.reshape(n - 1, -1)
    idx = flat.argmax(axis=1)
    dy, dx = np.unravel_index(idx, (h, w))
    dy = np.where(dy > h // 2, dy - h, dy).astype(np.float32)
    dx = np.where(dx > w // 2, dx - w, dx).astype(np.float32)
    conf = flat.max(axis=1)
    # Keep a shift only if undoing it explains the change; otherwise it is local (subject) motion.
    for k in np.flatnonzero((dx != 0) | (dy != 0)):
        sy, sx, m = int(dy[k]), int(dx[k]), int(max(abs(dy[k]), abs(dx[k])))
        if m * 4 >= min(h, w):
            conf[k] = 0
            continue
        inner = (slice(m, h - m), slice(m, w - m))
        before = np.abs(f[k + 1] - f[k])[inner].mean()
        after = np.abs(f[k + 1] - np.roll(f[k], (sy, sx), axis=(0, 1)))[inner].mean()
        if not after < 0.7 * before:
            conf[k] = 0
    pad = np.zeros(1, dtype=np.float32)
    return np.concatenate([pad, dx]), np.concatenate([pad, dy]), np.concatenate([pad, conf])


BLOCKS_Y, BLOCKS_X = 6, 8


def _blocks(f: np.ndarray) -> np.ndarray:
    """(n, h, w) -> (n, BLOCKS_Y * BLOCKS_X, bh, bw) tiles."""
    n, h, w = f.shape
    bh, bw = h // BLOCKS_Y, w // BLOCKS_X
    t = f[:, :bh * BLOCKS_Y, :bw * BLOCKS_X].reshape(n, BLOCKS_Y, bh, BLOCKS_X, bw)
    return t.transpose(0, 1, 3, 2, 4).reshape(n, BLOCKS_Y * BLOCKS_X, bh, bw)


def _block_signals(f: np.ndarray, fps: float) -> dict:
    """Per-frame share of textured blocks that changed abruptly / stayed put, plus the block diffs.

    A real cut changes every block. A title pop-in, effect flash, split-screen panel switch or jump cut
    changes some blocks while the rest of the framing stays put."""
    tiles = _blocks(f)
    n, k = tiles.shape[:2]
    std = tiles.std(axis=(2, 3))
    diff = np.zeros((n, k), dtype=np.float32)
    diff[1:] = np.abs(tiles[1:] - tiles[:-1]).mean(axis=(2, 3))
    textured = np.zeros((n, k), dtype=bool)
    textured[1:] = (std[1:] > 0.02) | (std[:-1] > 0.02)  # flat areas (black bars, sky) say nothing
    base = np.stack([_rolling_median(diff[:, j], max(2, round(fps * 0.5))) for j in range(k)], axis=1)
    abrupt = (diff >= 0.12) & (diff >= 4 * base + 0.01) & textured
    count = np.maximum(textured.sum(axis=1), 1)
    return {"diff": diff, "abrupt": abrupt,
            "changed": abrupt.sum(axis=1) / count,
            "unchanged": ((diff < 0.03) & textured).sum(axis=1) / count}


def _detect_partial_changes(sig: dict, fps: float, busy: np.ndarray) -> list[int]:
    """Frames where part of the picture switches in one step while the rest holds still.

    Requiring a step (the changed blocks are calm just before or just after) separates graphics and
    panel switches from camera or subject motion, which keeps changing frame after frame."""
    diff, abrupt, changed, unchanged = sig["diff"], sig["abrupt"], sig["changed"], sig["unchanged"]
    n = len(diff)
    hits = []
    for i in np.flatnonzero((changed >= 0.15) & (unchanged >= 0.2) & (changed * unchanged >= 0.1)):
        if i < 3 or i > n - 4 or busy[i]:
            continue
        cols = np.flatnonzero(abrupt[i])
        before = float((diff[i - 2:i][:, cols].mean(axis=0) / diff[i, cols]).mean())
        after = float((diff[i + 1:i + 3][:, cols].mean(axis=0) / diff[i, cols]).mean())
        if min(before, after) <= 0.25:
            hits.append(int(i))
    return hits


def _detect_motion_transitions(f: np.ndarray, d: np.ndarray, fps: float, busy: np.ndarray) -> list[dict]:
    """Whip pans and object wipes: a short burst of fast motion with no cut in it, where the picture
    after the burst is unrelated to the picture before it (the cut is hidden in the blur)."""
    base = _rolling_median(d, max(2, round(fps)))
    hot = d >= np.maximum(0.05, 3 * base)
    events, i, n = [], 1, len(d)
    while i < n:
        if not hot[i]:
            i += 1
            continue
        j = i
        while j < n and hot[j]:
            j += 1
        if 2 <= j - i <= round(0.6 * fps) and i >= 2 and j + 1 < n and not busy[i - 1: j + 1].any():
            a, b = f[i - 2], f[j + 1]
            kept = float((np.abs(_blocks(a[None])[0] - _blocks(b[None])[0]).mean(axis=(1, 2)) < 0.04).mean())
            if kept < 0.05 and _correlation(a, b) < 0.3:
                events.append({"_start": i, "_end": j, "type": "whip_or_wipe", "score": float(d[i:j].max())})
        i = j
    return events


def _group(frames: list[int], gap: int) -> list[list[int]]:
    """Split sorted frame indices into runs whose neighbours are at most `gap` frames apart."""
    groups: list[list[int]] = []
    for i in frames:
        if groups and i - groups[-1][-1] <= gap:
            groups[-1].append(i)
        else:
            groups.append([i])
    return groups


def _motion_label(score: float) -> str:
    if score < 0.004:
        return "static"
    if score < 0.015:
        return "low"
    if score < 0.04:
        return "medium"
    return "high"


def _camera_move(dx: np.ndarray, dy: np.ndarray, conf: np.ndarray, w: int, h: int) -> str | None:
    good = conf >= 0.15
    if good.sum() < 3:
        return None
    moves = []
    for vals, size, neg, pos in ((dx[good], w, "pan right", "pan left"), (dy[good], h, "tilt down", "tilt up")):
        total = float(vals.sum())
        moving = vals[vals != 0]
        consistent = len(moving) and (np.sign(moving) == np.sign(total)).mean() >= 0.7
        if abs(total) >= 0.15 * size and consistent:
            # content moving left (dx<0) means the camera is panning right, and so on
            moves.append(neg if total < 0 else pos)
    return " + ".join(moves) or None


def _pace_label(avg_shot: float) -> str:
    if avg_shot >= 6:
        return "slow"
    if avg_shot >= 3:
        return "moderate"
    if avg_shot >= 1.5:
        return "fast"
    return "very fast (rapid cutting)"


def analyse_editing(frames_u8: np.ndarray, fps: float) -> dict:
    n = len(frames_u8)
    duration = n / fps
    if n < 3:
        return {"analysed_fps": fps, "shots": [], "transitions": [], "in_shot_changes": [], "pace": {}}
    f = frames_u8.astype(np.float32) / 255.0
    d = np.zeros(n, dtype=np.float32)
    d[1:] = np.abs(f[1:] - f[:-1]).mean(axis=(1, 2))
    hd = _hist_dist(frames_u8)
    luma = f.mean(axis=(1, 2))

    flashes = _detect_flashes(luma, fps)
    candidates = [c for c in _detect_cuts(d, hd, fps)
                  if not any(e["_start"] - 1 <= c <= e["_end"] + 1 for e in flashes)]
    sig = _block_signals(f, fps)
    # A "cut" that keeps part of the framing is a change inside the shot (title pop-in, effect,
    # split-screen panel, jump cut), not a new shot.
    cuts = [c for c in candidates if sig["unchanged"][c] < 0.15]
    same_framing = [c for c in candidates if sig["unchanged"][c] >= 0.15]
    gradual = [g for g in _detect_gradual(f, d, fps, cuts)
               if not any(g["_start"] <= e["_end"] and e["_start"] <= g["_end"] for e in flashes)]
    dx, dy, conf = _global_shifts(f)

    busy = np.zeros(n, dtype=bool)  # frames already explained by a transition
    for c in cuts:
        busy[max(0, c - 1): c + 2] = True
    for e in gradual + flashes:
        busy[max(0, e["_start"] - 1): e["_end"] + 2] = True
    hidden = _detect_motion_transitions(f, d, fps, busy)
    for e in hidden:
        busy[max(0, e["_start"] - 1): e["_end"] + 2] = True
    in_shot = _group(sorted(set(same_framing) | set(_detect_partial_changes(sig, fps, busy))), round(fps * 0.25))

    # Transition events in frame units. A hard cut at frame c means frame c starts the new shot.
    events = [{"_start": c, "_end": c, "type": "hard_cut", "score": float(d[c])} for c in cuts]
    events = sorted(events + gradual + flashes + hidden, key=lambda e: e["_start"])

    # Shots are the spans between transitions.
    spans, prev = [], 0
    for e in events:
        if e["_start"] > prev:
            spans.append((prev, e["_start"]))
        prev = max(prev, e["_end"])
    if n > prev:
        spans.append((prev, n))
    spans = [(a, b) for a, b in spans if b - a >= 2]  # drop 1-frame remnants (e.g. black tail)

    shots = []
    for idx, (a, b) in enumerate(spans, 1):
        inner = slice(a + 1, b) if b - a > 2 else slice(a, b)
        motion = float(d[inner].mean()) if b - a > 1 else 0.0
        shots.append({
            "index": idx,
            "start_s": round(a / fps, 2),
            "end_s": round(b / fps, 2),
            "duration_s": round((b - a) / fps, 2),
            "motion": _motion_label(motion),
            "motion_score": round(motion, 4),
            "camera_move": _camera_move(dx[inner], dy[inner], conf[inner], f.shape[2], f.shape[1]),
            "brightness": round(float(luma[a:b].mean()), 2),
            "in_shot_changes": sum(a <= g[0] < b for g in in_shot),
        })

    transitions = []
    for e in events:
        transitions.append({
            "at_s": round(e["_start"] / fps, 2),
            "type": e["type"],
            "duration_s": round((e["_end"] - e["_start"]) / fps, 2),
        })
    # A cut surrounded by a burst of motion is usually a whip/swipe/zoom transition.
    for t, e in zip(transitions, events):
        if e["type"] == "hard_cut":
            c = e["_start"]
            nb = np.concatenate([d[max(1, c - 3): c], d[c + 1: c + 4]])
            base = float(np.median(d[max(1, c - round(fps)): c + round(fps)]))
            if (nb >= max(0.03, 3 * base)).sum() >= 3:
                t["type"] = "motion_cut"
                t["note"] = "cut hidden inside fast motion (whip pan / swipe / zoom-blur style)"

    in_shot_changes = [{"at_s": round(g[0] / fps, 2),
                        "share_of_frame_changed": round(float(max(sig["changed"][i] for i in g)), 2)}
                       for g in in_shot]

    durations = [s["duration_s"] for s in shots] or [round(duration, 2)]
    half = duration / 2
    cut_times = [t["at_s"] for t in transitions]
    pace = {
        "shot_count": len(shots),
        "transition_count": len(transitions),
        "avg_shot_s": round(float(np.mean(durations)), 2),
        "median_shot_s": round(float(np.median(durations)), 2),
        "shortest_shot_s": min(durations),
        "longest_shot_s": max(durations),
        "cuts_per_10s": round(len(transitions) / duration * 10, 2) if duration else 0,
        "transitions_first_half": sum(t < half for t in cut_times),
        "transitions_second_half": sum(t >= half for t in cut_times),
        "in_shot_change_count": len(in_shot_changes),
        "pace": _pace_label(float(np.mean(durations))) if len(shots) > 1 else "single continuous shot",
        # Duration-weighted shot motion, so transition spikes don't count as movement.
        "overall_motion": _motion_label(
            sum(s["motion_score"] * s["duration_s"] for s in shots) / max(sum(durations), 1e-6) if shots else 0.0),
    }
    return {"analysed_fps": fps, "shots": shots, "transitions": transitions,
            "in_shot_changes": in_shot_changes, "pace": pace}
