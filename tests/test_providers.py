from dataclasses import replace

import httpx
import pytest

from reel_mcp.config import Settings
from reel_mcp.errors import ReelError
from reel_mcp.providers import instagram_public
from reel_mcp.providers.meta_oembed import fetch_oembed
from reel_mcp.providers.user_file import new_upload_path, resolve_user_file
from reel_mcp.sources import resolve_media
from reel_mcp.urls import parse_reel_url

REF = parse_reel_url("https://www.instagram.com/reel/CDUMkliABpa/")


@pytest.fixture
def settings(tmp_path):
    s = Settings(data_dir=tmp_path)
    s.ensure_dirs()
    return s


def test_public_fetch_is_anonymous(settings, tmp_path):
    opts = instagram_public._ydl_options(settings, tmp_path)
    assert opts["cookiefile"] is None and opts["cookiesfrombrowser"] is None
    assert opts["usenetrc"] is False and opts["username"] is None and opts["password"] is None


def test_login_wall_maps_to_not_public(settings, monkeypatch):
    class FakeYDL:
        def __init__(self, opts): ...
        def __enter__(self): return self
        def __exit__(self, *a): ...
        def extract_info(self, url, download):
            raise RuntimeError("ERROR: [Instagram] X: Requested content is not available, rate-limit reached or "
                               "login required. Use --cookies-from-browser or --cookies for the authentication.")

    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    with pytest.raises(ReelError) as exc:
        instagram_public.fetch_public_reel(REF, settings)
    assert exc.value.code == "not_public"
    assert "cookies" not in exc.value.message.lower()
    assert not (settings.cache_dir / REF.shortcode).exists()  # nothing left behind


def test_too_long_is_refused_before_download(settings, monkeypatch):
    downloaded = []

    class FakeYDL:
        def __init__(self, opts): ...
        def __enter__(self): return self
        def __exit__(self, *a): ...
        def extract_info(self, url, download): return {"formats": [{}], "duration": 9999}
        def process_ie_result(self, info, download): downloaded.append(1)

    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    with pytest.raises(ReelError) as exc:
        instagram_public.fetch_public_reel(REF, settings)
    assert exc.value.code == "too_long" and not downloaded


def test_public_fetch_can_be_disabled(settings):
    with pytest.raises(ReelError) as exc:
        resolve_media(REF.url, None, replace(settings, public_fetch=False))
    assert exc.value.code == "public_fetch_disabled" and "video_file" in exc.value.hint


def _oembed_client(status, body):
    def handler(request):
        assert request.url.path.endswith("/instagram_oembed")
        assert request.url.params["url"] == REF.url
        return httpx.Response(status, json=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_oembed_success(settings):
    s = replace(settings, meta_access_token="app|token")
    data, note = fetch_oembed(REF, s, _oembed_client(200, {"author_name": "clippedinandfree", "html": "<x>"}))
    assert data == {"author_name": "clippedinandfree"} and "public" in note


def test_oembed_private(settings):
    s = replace(settings, meta_access_token="app|token")
    data, note = fetch_oembed(REF, s, _oembed_client(400, {"error": {"message": "Invalid URL"}}))
    assert data is None and "400" in note


def test_oembed_not_configured(settings):
    data, note = fetch_oembed(REF, settings)
    assert data is None and "not configured" in note


def test_user_file_lookup_is_confined_to_inbox(settings, tmp_path):
    (tmp_path / "secret.mp4").write_bytes(b"x")
    for name in ("../secret.mp4", "/etc/passwd", "sub/clip.mp4", ""):
        with pytest.raises(ReelError) as exc:
            resolve_user_file(name, settings)
        assert exc.value.code == "bad_file"
    dest = new_upload_path("My Clip!.MOV", settings)
    dest.write_bytes(b"x")
    assert dest.parent == settings.inbox_dir and dest.suffix == ".mov"
    assert resolve_user_file(dest.name, settings).video_path == dest.resolve()
    with pytest.raises(ReelError):
        new_upload_path("notes.txt", settings)
