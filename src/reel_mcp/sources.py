"""Turn tool arguments (url and/or video_file) into a local video, trying access methods in order:

1. user-supplied file (if `video_file` is given)       -> always allowed
2. official Meta oEmbed (if a token is configured)     -> metadata only, confirms the post is public
3. anonymous public fetch (unless REEL_PUBLIC_FETCH=0)  -> the video itself, public reels only
"""

from __future__ import annotations

from .config import Settings
from .errors import UPLOAD_HINT, ReelError
from .providers.base import ReelMedia
from .providers.instagram_public import fetch_public_reel
from .providers.meta_oembed import fetch_oembed
from .providers.user_file import resolve_user_file
from .urls import parse_reel_url


def resolve_media(url: str | None, video_file: str | None, settings: Settings) -> ReelMedia:
    """Blocking. Raises ReelError with a fallback hint when the reel can't be retrieved."""
    if video_file:
        media = resolve_user_file(video_file, settings)
        if url:  # optional: label the upload with the reel it came from
            try:
                ref = parse_reel_url(url)
            except ReelError:
                media.notes.append("`url` ignored: not a valid reel link.")
            else:
                media.metadata.update(shortcode=ref.shortcode, url=ref.url)
                official, note = fetch_oembed(ref, settings)
                if official:
                    media.metadata["official_oembed"] = official
                if settings.meta_access_token:
                    media.notes.append(note)
        return media

    if not url:
        raise ReelError("missing_input", "Pass `url` (a public Instagram reel link) or `video_file` "
                        "(the name of an uploaded video).", UPLOAD_HINT)
    ref = parse_reel_url(url)
    official, note = fetch_oembed(ref, settings)
    notes = [note] if settings.meta_access_token else []

    if not settings.public_fetch:
        err = ReelError("public_fetch_disabled",
                        "Downloading reels from Instagram is disabled on this server (REEL_PUBLIC_FETCH=0). "
                        "Meta's official APIs do not hand out video files for other accounts' reels.",
                        UPLOAD_HINT)
        err.details = {k: v for k, v in (("official_oembed", official), ("notes", notes)) if v}
        raise err
    try:
        media = fetch_public_reel(ref, settings)
    except ReelError as err:
        err.details = {k: v for k, v in (("official_oembed", official), ("notes", notes)) if v}
        raise
    if official:
        media.metadata["official_oembed"] = official
    media.notes.extend(notes)
    return media
