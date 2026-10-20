"""End-to-end over real Streamable HTTP: uvicorn in a thread + the SDK's MCP client."""

import json
import os
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import replace

import httpx
import pytest
import uvicorn
from mcp import Client

from reel_mcp.config import Settings
from reel_mcp.server import create_app

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@contextmanager
def running(settings):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(5)


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path)


def _upload(base, path, headers=None):
    with path.open("rb") as fh:
        return httpx.put(f"{base}/mcp/upload/{path.name}", content=fh.read(), headers=headers or {}, timeout=30)


@pytest.mark.anyio
async def test_tools_end_to_end(settings, clips):
    with running(settings) as base:
        assert httpx.get(f"{base}/healthz").json()["status"] == "ok"
        assert "Upload a reel video" in httpx.get(f"{base}/mcp/upload").text
        assert _upload(base, clips["cuts"]).status_code == 200
        name = next(settings.inbox_dir.iterdir()).name

        async with Client(f"{base}/mcp") as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            assert set(tools) == {"inspect_reel", "analyse_reel"}
            assert tools["analyse_reel"].annotations.read_only_hint is True

            r = await client.call_tool("inspect_reel", {"video_file": name})
            assert not r.is_error
            assert [b.type for b in r.content] == ["text", "image"]
            assert r.structured_content["technical"]["video"]["aspect_ratio"] == "9:16"

            r = await client.call_tool("analyse_reel", {"video_file": name, "max_frames": 4, "transcribe": False})
            assert not r.is_error
            assert sum(b.type == "image" for b in r.content) == 4
            assert "hard cut" in r.content[0].text
            report = r.structured_content
            assert [t["at_s"] for t in report["editing"]["transitions"]] == [1.5, 3.0, 4.5]
            json.dumps(report)

            r = await client.call_tool("analyse_reel", {"video_file": name, "include_frames": False,
                                                        "transcribe": False})
            assert not any(b.type == "image" for b in r.content)

            r = await client.call_tool("analyse_reel", {"url": "https://www.instagram.com/stories/x/1/"})
            assert r.is_error and r.structured_content["error"] == "invalid_url"
            r = await client.call_tool("inspect_reel", {})
            assert r.is_error and r.structured_content["error"] == "missing_input"


def test_upload_rejects_bad_files(settings, tmp_path):
    bogus = tmp_path / "fake.mp4"
    bogus.write_bytes(b"not a video at all")
    exe = tmp_path / "run.sh"
    exe.write_text("echo hi")
    with running(settings) as base:
        assert _upload(base, bogus).status_code == 400
        assert _upload(base, exe).status_code == 400
    assert list(settings.inbox_dir.iterdir()) == []
    with running(replace(settings, max_upload_mb=0)) as base:
        assert _upload(base, bogus).status_code == 413


def test_bearer_token(settings, clips):
    with running(replace(settings, auth_token="s3cret")) as base:
        assert httpx.post(f"{base}/mcp", json=INIT, headers=MCP_HEADERS).status_code == 401
        bad = {**MCP_HEADERS, "Authorization": "Bearer wrong"}
        assert httpx.post(f"{base}/mcp", json=INIT, headers=bad).status_code == 401
        good = {**MCP_HEADERS, "Authorization": "Bearer s3cret"}
        assert httpx.post(f"{base}/mcp", json=INIT, headers=good).status_code == 200
        assert httpx.get(f"{base}/healthz").status_code == 200          # open
        assert httpx.get(f"{base}/mcp/upload").status_code == 200           # page is open,
        assert _upload(base, clips["static"]).status_code == 401        # uploading is not
        assert _upload(base, clips["static"], {"Authorization": "Bearer s3cret"}).status_code == 200


def test_secret_path_covers_mcp_and_upload(settings, clips):
    with running(replace(settings, mcp_path="/mcp/s3cr3t")) as base:
        assert httpx.post(f"{base}/mcp", json=INIT, headers=MCP_HEADERS).status_code == 404
        assert httpx.get(f"{base}/mcp/upload").status_code == 404
        assert httpx.post(f"{base}/mcp/s3cr3t", json=INIT, headers=MCP_HEADERS).status_code == 200
        with clips["static"].open("rb") as fh:
            r = httpx.put(f"{base}/mcp/s3cr3t/upload/static.mp4", content=fh.read(), timeout=30)
        assert r.status_code == 200


def test_allowed_hosts_for_tunnels(settings):
    with running(settings) as base:  # default: loopback only
        r = httpx.post(f"{base}/mcp", json=INIT, headers={**MCP_HEADERS, "Host": "abc.ngrok.app"})
        assert r.status_code == 421
    with running(replace(settings, allowed_hosts=["abc.ngrok.app"])) as base:
        r = httpx.post(f"{base}/mcp", json=INIT, headers={**MCP_HEADERS, "Host": "abc.ngrok.app"})
        assert r.status_code == 200


@pytest.mark.anyio
@pytest.mark.skipif(not os.environ.get("REEL_TEST_TRANSCRIBE"), reason="set REEL_TEST_TRANSCRIBE=1 (downloads a model)")
async def test_transcription(settings, speech_clip):
    with running(settings) as base:
        assert _upload(base, speech_clip).status_code == 200
        name = next(settings.inbox_dir.iterdir()).name
        async with Client(f"{base}/mcp") as client:
            r = await client.call_tool("analyse_reel", {"video_file": name, "include_frames": False})
    transcript = r.structured_content["audio"]["transcript"]
    assert transcript["speech_detected"] and "transcription" in transcript["text"].lower()


@pytest.mark.anyio
@pytest.mark.skipif(not os.environ.get("REEL_LIVE_URL"), reason="set REEL_LIVE_URL to a public reel to test live")
async def test_live_public_reel(settings):
    with running(settings) as base:
        async with Client(f"{base}/mcp") as client:
            r = await client.call_tool("analyse_reel", {"url": os.environ["REEL_LIVE_URL"], "transcribe": False})
    assert not r.is_error, r.content[0].text
    assert r.structured_content["source"] == "instagram_public"


@pytest.fixture
def anyio_backend():
    return "asyncio"
