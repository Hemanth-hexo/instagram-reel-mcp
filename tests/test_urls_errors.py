import pytest

from reel_mcp.errors import ReelError, classify_fetch_failure
from reel_mcp.urls import parse_reel_url


@pytest.mark.parametrize("url", [
    "https://www.instagram.com/reel/CDUMkliABpa/",
    "https://instagram.com/reel/CDUMkliABpa",
    "instagram.com/reels/CDUMkliABpa/?igsh=abc123",
    "https://www.instagram.com/p/CDUMkliABpa/",
    "https://m.instagram.com/tv/CDUMkliABpa/",
    "https://www.instagram.com/some.user/reel/CDUMkliABpa/",
])
def test_accepts_reel_links(url):
    ref = parse_reel_url(url)
    assert ref.shortcode == "CDUMkliABpa"
    assert ref.url == "https://www.instagram.com/reel/CDUMkliABpa/"


@pytest.mark.parametrize("url", [
    "https://example.com/reel/CDUMkliABpa/",
    "https://instagram.com.evil.net/reel/CDUMkliABpa/",
    "https://www.instagram.com/someone/",
    "https://www.instagram.com/stories/someone/123/",
    "ftp://www.instagram.com/reel/CDUMkliABpa/",
    "https://user:pw@www.instagram.com/reel/CDUMkliABpa/",
    "https://www.instagram.com:8443/reel/CDUMkliABpa/",
    "",
])
def test_rejects_everything_else(url):
    with pytest.raises(ReelError) as exc:
        parse_reel_url(url)
    assert exc.value.code == "invalid_url"


@pytest.mark.parametrize("message, code", [
    ("[Instagram] X: Requested content is not available, rate-limit reached or login required", "not_public"),
    ("[Instagram] X: Instagram sent an empty media response", "not_public"),
    ("This account is private", "not_public"),
    ("HTTP Error 404: Not Found", "not_found"),
    ("Connection reset by peer", "network_error"),
    ("Something odd", "fetch_failed"),
])
def test_fetch_failure_classification(message, code):
    err = classify_fetch_failure(message)
    assert err.code == code
    if code in ("not_public", "not_found", "fetch_failed"):
        assert "video_file" in err.hint  # always points at the upload fallback
