"""MCP server: tools, upload endpoint, auth, and the Streamable HTTP app."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
from typing import Annotated

import anyio
import anyio.from_thread
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from . import __version__, analysis
from . import media as mx
from .config import Settings
from .errors import ReelError
from .providers.base import purge_older_than
from .providers.user_file import new_upload_path
from .sources import resolve_media

log = logging.getLogger("reel_mcp")

INSTRUCTIONS = """\
Tools for public Instagram Reels. inspect_reel returns metadata, technical details and a poster frame; \
analyse_reel returns key frames, a transcript and measured edit signals (cuts, transitions, pacing, \
camera motion) for explaining visuals, editing, animation and transitions. Only reels from public \
accounts can be fetched; private or login-walled reels are refused and this server never logs in. \
When a reel can't be fetched, ask the user to upload the video file and call again with video_file."""

UrlArg = Annotated[str | None, Field(description="Instagram reel link, e.g. https://www.instagram.com/reel/ABC123/ "
                                                 "(public accounts only).")]
FileArg = Annotated[str | None, Field(description="Fallback: name of a video the user uploaded at "
                                                  "<server MCP URL>/upload. Use when Instagram blocks retrieval.")]


def _error_result(err: ReelError) -> CallToolResult:
    text = err.to_text()
    if err.details:
        text += "\n\nStill available:\n" + json.dumps(err.details, ensure_ascii=False, indent=2)
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=err.to_dict(),
                          is_error=True)


def _image(jpeg: bytes) -> ImageContent:
    return ImageContent(type="image", data=base64.b64encode(jpeg).decode(), mime_type="image/jpeg")


def upload_path(settings: Settings) -> str:
    return settings.mcp_path.rstrip("/") + "/upload"


def build_server(settings: Settings) -> MCPServer:
    settings.ensure_dirs()
    mcp = MCPServer("instagram-reels", instructions=INSTRUCTIONS)
    jobs = asyncio.Semaphore(settings.max_concurrent_jobs)

    async def run_job(ctx: Context | None, fn, *args, **kwargs):
        """Run blocking work in a thread, relaying progress notifications back to the client."""

        def progress(p: float, msg: str) -> None:
            if ctx is None:
                return
            try:
                anyio.from_thread.run(ctx.report_progress, p, 1.0, msg)
            except Exception:  # progress is best-effort
                pass

        async with jobs:
            return await anyio.to_thread.run_sync(lambda: fn(*args, progress=progress, **kwargs))

    def _resolve(url, video_file, progress):
        progress(0.02, "Retrieving video")
        return resolve_media(url, video_file, settings)

    @mcp.tool(
        title="Inspect Instagram Reel",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
    )
    async def inspect_reel(url: UrlArg = None, video_file: FileArg = None, ctx: Context = None) -> CallToolResult:
        """Retrieve a public Instagram Reel and return its metadata (creator, caption, posted date, view/like/
        comment counts, audio), technical details (duration, resolution, aspect ratio, fps, codecs, size) and a
        poster frame. Private or login-walled reels are refused. If retrieval fails, ask the user to upload the
        video and pass video_file instead."""
        try:
            def work(progress):
                media = _resolve(url, video_file, progress)
                progress(0.8, "Probing video")
                return analysis.inspect(media)

            report, frames = await run_job(ctx, work)
        except ReelError as err:
            return _error_result(err)
        except Exception:
            log.exception("inspect_reel failed")
            return _error_result(ReelError("internal_error", "Unexpected server error; see server logs."))
        content: list = [TextContent(type="text", text=analysis.render_inspect(report, frames))]
        content += [_image(f.jpeg) for f in frames]
        return CallToolResult(content=content, structured_content=report)

    @mcp.tool(
        title="Analyse Instagram Reel",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
    )
    async def analyse_reel(
        url: UrlArg = None,
        video_file: FileArg = None,
        max_frames: Annotated[int, Field(ge=1, le=16, description="Maximum key frames to return as images.")] = 8,
        frame_width: Annotated[int, Field(ge=256, le=1024, description="Width of returned frames in px.")] = 512,
        transcribe: Annotated[bool, Field(description="Transcribe speech in the audio.")] = True,
        include_frames: Annotated[bool, Field(description="Return frames as images. Set false for clients "
                                                          "that cannot display images.")] = True,
        ctx: Context = None,
    ) -> CallToolResult:
        """Analyse a public Instagram Reel (or an uploaded video): returns key frames as images, a timestamped
        transcript, and measured editing signals (shots, cut timestamps, transitions such as hard cut, dissolve,
        fade, flash and whip/motion cuts, pacing, camera pans/tilts, motion level), plus a guide for explaining
        the visuals, editing, animation and transitions. Private or login-walled reels are refused; use
        video_file with an uploaded copy instead."""
        try:
            def work(progress):
                media = _resolve(url, video_file, progress)
                return analysis.analyse(media, settings, max_frames=max_frames, frame_width=frame_width,
                                        transcribe=transcribe, include_frames=include_frames, progress=progress)

            report, frames = await run_job(ctx, work)
        except ReelError as err:
            return _error_result(err)
        except Exception:
            log.exception("analyse_reel failed")
            return _error_result(ReelError("internal_error", "Unexpected server error; see server logs."))
        content: list = [TextContent(type="text", text=analysis.render_analysis(report))]
        total = len(frames)
        for i, f in enumerate(frames, 1):
            shot = f" (shot {f.shot})" if f.shot else ""
            content.append(TextContent(type="text", text=f"Frame {i}/{total} at {f.time_s:.2f}s{shot}"))
            content.append(_image(f.jpeg))
        return CallToolResult(content=content, structured_content=report)

    # ------------------------------------------------------------------ HTTP routes (fallback upload)

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> Response:
        return JSONResponse({"status": "ok", "version": __version__})

    # Upload lives under the MCP path, so a secret REEL_MCP_PATH protects it too.
    @mcp.custom_route(upload_path(settings), methods=["GET", "POST"])
    async def upload_page(request: Request) -> Response:
        if request.method == "GET":
            return HTMLResponse(_UPLOAD_PAGE.replace("{{TOKEN_FIELD}}", _TOKEN_FIELD if settings.auth_token else ""))
        return await _receive_upload(request, request.headers.get("x-filename") or request.query_params.get("filename"))

    @mcp.custom_route(upload_path(settings) + "/{filename}", methods=["PUT", "POST"])
    async def upload_named(request: Request) -> Response:
        return await _receive_upload(request, request.path_params["filename"])

    async def _receive_upload(request: Request, filename: str | None) -> Response:
        purge_older_than(settings.inbox_dir, settings.upload_ttl_s)
        try:
            dest = new_upload_path(filename or "", settings)
        except ReelError as err:
            return JSONResponse(err.to_dict(), status_code=400)
        limit, size = settings.max_upload_mb * 1024 * 1024, 0
        try:
            with dest.open("wb") as fh:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        raise ReelError("too_large", f"Upload exceeds {settings.max_upload_mb} MB.")
                    fh.write(chunk)
            if size == 0:
                raise ReelError("bad_file", "Empty upload. Send the video as the raw request body.")
            await anyio.to_thread.run_sync(mx.probe, dest)  # rejects non-videos
        except ReelError as err:
            dest.unlink(missing_ok=True)
            return JSONResponse(err.to_dict(), status_code=413 if err.code == "too_large" else 400)
        except BaseException:  # client went away mid-upload, etc.: never leave partial files
            dest.unlink(missing_ok=True)
            raise
        log.info("upload saved: %s (%.1f MB)", dest.name, size / 1_048_576)
        return JSONResponse({
            "video_file": dest.name,
            "size_mb": round(size / 1_048_576, 2),
            "expires_in_hours": round(settings.upload_ttl_s / 3600, 1),
            "next_step": f"Ask your assistant to run analyse_reel with video_file=\"{dest.name}\".",
        })

    return mcp


class BearerAuth:
    """Pure-ASGI middleware: require `Authorization: Bearer <token>` except on open paths."""

    def __init__(self, app, token: str, open_paths: set[tuple[str, str]]):
        self.app, self.token, self.open = app, token.encode(), open_paths

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or (scope["method"], scope["path"]) in self.open \
                or scope["method"] == "OPTIONS":
            return await self.app(scope, receive, send)
        auth = dict(scope["headers"]).get(b"authorization", b"")
        if auth.startswith(b"Bearer ") and hmac.compare_digest(auth[7:].strip(), self.token):
            return await self.app(scope, receive, send)
        await JSONResponse({"error": "unauthorized", "message": "Missing or wrong bearer token."},
                           status_code=401, headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)


def _transport_security(settings: Settings) -> TransportSecuritySettings | None:
    if not settings.allowed_hosts:
        return None  # SDK default: DNS-rebinding protection for loopback binds
    if "*" in settings.allowed_hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    local = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    return TransportSecuritySettings(
        allowed_hosts=settings.allowed_hosts + local,
        allowed_origins=[f"https://{h}" for h in settings.allowed_hosts]
        + ["http://127.0.0.1:*", "http://localhost:*"],
    )


def create_app(settings: Settings):
    mcp = build_server(settings)
    app = mcp.streamable_http_app(
        streamable_http_path=settings.mcp_path,
        stateless_http=True,  # no sessions to lose on restart; scales behind any load balancer
        transport_security=_transport_security(settings),
        host=settings.host,
    )
    if settings.auth_token:
        app.add_middleware(BearerAuth, token=settings.auth_token,
                           open_paths={("GET", "/healthz"), ("GET", upload_path(settings))})
    return app


_TOKEN_FIELD = '<label>Access token <input id="token" type="password" autocomplete="off"></label>'
_UPLOAD_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Reel upload</title>
<style>
:root{color-scheme:light dark;--bg:#fafafa;--fg:#111;--muted:#666;--card:#fff;--line:#ddd;--accent:#d62976}
@media (prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--muted:#999;--card:#1b1b1b;--line:#333}}
body{font:16px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:16px}
main{max-width:560px;margin:8vh auto;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:24px}
h1{font-size:1.3rem;margin:0 0 4px}p{color:var(--muted);margin:0 0 16px}label{display:block;margin:12px 0}
input{display:block;width:100%;box-sizing:border-box;margin-top:4px;padding:8px;font:inherit}
button{background:var(--accent);color:#fff;border:0;border-radius:8px;padding:10px 16px;font:inherit;cursor:pointer}
pre{white-space:pre-wrap;background:var(--bg);padding:12px;border-radius:8px;border:1px solid var(--line)}
</style></head><body><main>
<h1>Upload a reel video</h1>
<p>Fallback for reels this server can't fetch (for example, Instagram blocked anonymous access).
Upload your own copy, then give the returned name to your assistant.</p>
<label>Video file <input id="file" type="file" accept="video/*"></label>
{{TOKEN_FIELD}}
<button id="go">Upload</button>
<pre id="out" hidden></pre>
</main><script>
document.getElementById('go').onclick = async () => {
  const f = document.getElementById('file').files[0], out = document.getElementById('out');
  if (!f) return;
  out.hidden = false; out.textContent = 'Uploading ' + f.name + '...';
  const headers = {'X-Filename': f.name};
  const t = document.getElementById('token');
  if (t && t.value) headers['Authorization'] = 'Bearer ' + t.value;
  try {
    const r = await fetch('upload', {method: 'POST', headers, body: f});
    const j = await r.json();
    out.textContent = r.ok ? 'Uploaded. ' + j.next_step : 'Failed: ' + (j.message || r.status);
  } catch (e) { out.textContent = 'Failed: ' + e; }
};
</script></body></html>"""
