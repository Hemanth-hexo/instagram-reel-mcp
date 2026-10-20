"""Errors that carry a machine-readable code and advice an LLM client can relay to the user."""

from __future__ import annotations

UPLOAD_HINT = (
    "Fallback: download the video yourself (e.g. with Instagram's own 'Download' option on your own "
    "reels, or a screen recording), upload it on this server's upload page (the MCP URL + /upload, e.g. "
    "http://localhost:8000/mcp/upload) or drop it into the inbox folder, then call this tool again with "
    "video_file='<name returned by the upload>'."
)


class ReelError(Exception):
    """A failure the caller can act on. `code` is stable; `hint` says what to do next."""

    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details: dict = {}  # e.g. official metadata that was still retrievable

    def to_dict(self) -> dict:
        out = {"error": self.code, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        if self.details:
            out["details"] = self.details
        return out

    def to_text(self) -> str:
        text = f"[{self.code}] {self.message}"
        if self.hint:
            text += f"\n{self.hint}"
        return text


def classify_fetch_failure(raw: str) -> ReelError:
    """Map a yt-dlp / HTTP failure message to a ReelError.

    Instagram does not tell anonymous visitors *why* a reel is unavailable, so "private",
    "login wall" and "blocked IP" are deliberately reported together instead of guessing.
    """
    low = raw.lower()
    if any(s in low for s in ("login", "log in", "private", "restricted", "not granting access",
                              "rate-limit", "rate limit", "empty media response", "checkpoint",
                              "age-restricted", "age restricted")):
        return ReelError(
            "not_public",
            "Instagram did not serve this reel to an anonymous visitor. It is most likely from a private "
            "account, deleted, age/region-restricted, or Instagram is rate-limiting this server's IP. "
            "This server only reads public reels and never logs in, so it will not go further.",
            UPLOAD_HINT,
        )
    if any(s in low for s in ("404", "not found", "does not exist", "unavailable", "removed", "deleted")):
        return ReelError(
            "not_found",
            "This reel was not found. It may have been deleted, or the link is wrong.",
            UPLOAD_HINT,
        )
    if any(s in low for s in ("timed out", "timeout", "connection", "name resolution", "network", "ssl")):
        return ReelError("network_error", f"Could not reach Instagram: {raw[:300]}", "Retry in a moment.")
    return ReelError("fetch_failed", f"Could not retrieve the reel: {raw[:300]}", UPLOAD_HINT)
