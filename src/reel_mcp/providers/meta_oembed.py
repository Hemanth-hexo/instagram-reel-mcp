"""Official Meta oEmbed endpoint (metadata only, never the video file).

Needs a Meta app with the "oEmbed Read" feature; set REEL_META_ACCESS_TOKEN to
"<app-id>|<client-token>" (or any token Meta accepts for instagram_oembed).
oEmbed only answers for public, embeddable posts, so a success also confirms the reel is public.
"""

from __future__ import annotations

import httpx

from ..config import Settings
from ..urls import ReelRef

_FIELDS = ("author_name", "author_url", "title", "thumbnail_url", "thumbnail_width",
           "thumbnail_height", "provider_name", "media_id")


def fetch_oembed(ref: ReelRef, settings: Settings, client: httpx.Client | None = None) -> tuple[dict | None, str]:
    """Return (metadata or None, human-readable note). Never raises."""
    if not settings.meta_access_token:
        return None, "Official Meta oEmbed not configured (set REEL_META_ACCESS_TOKEN to enable)."
    endpoint = f"https://graph.facebook.com/{settings.graph_version}/instagram_oembed"
    params = {"url": ref.url, "access_token": settings.meta_access_token, "omitscript": "true"}
    own = client is None
    client = client or httpx.Client(timeout=10)
    try:
        resp = client.get(endpoint, params=params)
    except httpx.HTTPError as exc:
        return None, f"Official Meta oEmbed request failed: {type(exc).__name__}."
    finally:
        if own:
            client.close()
    if resp.status_code != 200:
        try:
            detail = resp.json().get("error", {}).get("message", "")
        except ValueError:
            detail = ""
        return None, (f"Official Meta oEmbed returned HTTP {resp.status_code}"
                      f"{': ' + detail if detail else ''} (private, removed, or not embeddable).")
    data = resp.json()
    return {k: data[k] for k in _FIELDS if data.get(k) not in (None, "")}, "Official Meta oEmbed: OK (post is public)."
