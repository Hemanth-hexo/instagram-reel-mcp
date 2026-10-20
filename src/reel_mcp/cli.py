"""Command line: `reel-mcp [serve|doctor|warmup]` (also `python -m reel_mcp ...`)."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from dataclasses import replace

from . import __version__
from .config import load_settings


def _serve(args) -> None:
    import uvicorn

    from .server import create_app

    settings = load_settings()
    if args.host:
        settings = replace(settings, host=args.host)
    if args.port:
        settings = replace(settings, port=args.port)
    if settings.host not in ("127.0.0.1", "localhost", "::1") and not settings.auth_token:
        logging.warning("Listening on %s without REEL_AUTH_TOKEN: anyone who can reach this port can use it.",
                        settings.host)
    logging.info("MCP endpoint: http://%s:%s%s", settings.host, settings.port, settings.mcp_path)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


def _doctor(args) -> int:
    from . import transcribe

    settings = load_settings()
    ok = True

    def line(good: bool, label: str, detail: str = "") -> None:
        nonlocal ok
        ok &= good
        print(f"[{'ok' if good else '!!'}] {label}" + (f": {detail}" if detail else ""))

    print(f"reel-mcp {__version__} (Python {sys.version.split()[0]})")
    for binary in ("ffmpeg", "ffprobe"):
        line(bool(shutil.which(binary)), binary, shutil.which(binary) or "not found (install ffmpeg)")
    try:
        import yt_dlp.version

        line(True, "yt-dlp", yt_dlp.version.__version__ + " (keep updated: pip install -U yt-dlp)")
    except ImportError:
        line(False, "yt-dlp", "not installed")
    print(f"[--] public fetch: {'enabled' if settings.public_fetch else 'disabled'} (REEL_PUBLIC_FETCH)")
    print(f"[--] official Meta oEmbed: {'configured' if settings.meta_access_token else 'not configured'}")
    print(f"[--] transcription: {'faster-whisper ' + settings.whisper_model if transcribe.available() else 'not installed'}")
    print(f"[--] auth token: {'set' if settings.auth_token else 'not set'}")
    print(f"[--] data dir: {settings.data_dir}")
    return 0 if ok else 1


def _warmup(args) -> int:
    from . import transcribe

    settings = load_settings()
    if not transcribe.available():
        print("faster-whisper is not installed: pip install 'reel-mcp[transcribe]'")
        return 1
    print(f"Downloading/loading whisper model '{settings.whisper_model}'...")
    transcribe._get_model(settings)
    print("Ready.")
    return 0


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="reel-mcp", description="Instagram Reel MCP server")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="run the Streamable HTTP server (default)")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    sub.add_parser("doctor", help="check dependencies and configuration")
    sub.add_parser("warmup", help="download the speech-to-text model ahead of time")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        sys.exit(_doctor(args))
    if args.command == "warmup":
        sys.exit(_warmup(args))
    if args.command is None:
        args.host = args.port = None
    _serve(args)
