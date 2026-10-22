"""Local speech-to-text with faster-whisper (optional extra: pip install 'reel-mcp[transcribe]').

The model is downloaded from Hugging Face on first use and cached (see `reel-mcp warmup`).
"""

from __future__ import annotations

import importlib.util
import threading
import wave
from pathlib import Path

import numpy as np

from .config import Settings

_model = None
_model_lock = threading.Lock()


def available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def _get_model(settings: Settings):
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel

            _model = WhisperModel(settings.whisper_model, device=settings.whisper_device,
                                  compute_type=settings.whisper_compute_type)
        return _model


def transcribe(wav: Path, settings: Settings) -> dict:
    """Transcript with timestamps. Never raises: failures come back as `available: False`."""
    if not available():
        return {"available": False,
                "note": "Transcription disabled: install with pip install 'reel-mcp[transcribe]'."}
    try:
        model = _get_model(settings)
        # Hand over samples rather than a path: skips faster-whisper's PyAV decoding, which breaks
        # with some PyAV releases. ffmpeg already produced 16 kHz mono PCM.
        with wave.open(str(wav)) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        segments, info = model.transcribe(pcm.astype(np.float32) / 32768.0, vad_filter=True, beam_size=1,
                                          condition_on_previous_text=False)
        segs = []
        for s in segments:
            if s.no_speech_prob > 0.6 and s.avg_logprob < -1.0:
                continue  # likely music/noise hallucination
            text = s.text.strip()
            if text:
                segs.append({"start_s": round(s.start, 2), "end_s": round(s.end, 2), "text": text})
    except Exception as exc:  # model download/runtime problems should not kill the analysis
        return {"available": False, "note": f"Transcription failed: {type(exc).__name__}: {exc}"[:300]}
    return {
        "available": True,
        "model": settings.whisper_model,
        "language": info.language,
        "language_probability": round(info.language_probability, 2),
        "speech_detected": bool(segs),
        "text": " ".join(s["text"] for s in segs),
        "segments": segs,
    }
