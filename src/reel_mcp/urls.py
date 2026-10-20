"""Validate and normalise Instagram reel URLs.

Only instagram.com reel/post/tv links are accepted. Anything else is rejected before a network
request is made, which also means the server cannot be pointed at arbitrary hosts (SSRF).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import ReelError

_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}
_PATH = re.compile(
    r"^/(?:(?P<user>[A-Za-z0-9._]{1,30})/)?(?P<kind>reels?|p|tv)/(?P<code>[A-Za-z0-9_-]{5,32})/?$"
)


@dataclass(frozen=True)
class ReelRef:
    shortcode: str
    url: str  # canonical https://www.instagram.com/reel/<code>/
    username: str | None = None


def parse_reel_url(raw: str) -> ReelRef:
    raw = (raw or "").strip()
    if "://" not in raw:
        raw = "https://" + raw  # tolerate "instagram.com/reel/..." pasted without a scheme
    parts = urlsplit(raw)
    bad = ReelError(
        "invalid_url",
        "Expected a link like https://www.instagram.com/reel/<shortcode>/ "
        "(profile, story and highlight links are not supported).",
    )
    if parts.scheme not in ("http", "https") or (parts.hostname or "").lower() not in _HOSTS:
        raise bad
    if parts.username or parts.password or parts.port:
        raise bad
    m = _PATH.match(parts.path)
    if not m:
        raise bad
    code = m.group("code")
    return ReelRef(
        shortcode=code,
        url=f"https://www.instagram.com/reel/{code}/",
        username=m.group("user"),
    )
