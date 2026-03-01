"""Helpers for chat URL/id normalization."""

from urllib.parse import urlparse

from ..exceptions import ChatNavigationError


def extract_chat_id(url: str) -> str | None:
    """Extract chat id from URL path `/c/<id>` if present."""
    path = urlparse(url).path.strip("/")
    if not path:
        return None
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] == "c":
        return parts[1]
    return None


def normalize_chat_url(base_url: str, chat_ref: str) -> str:
    """Normalize id/relative/absolute references into full chat URL."""
    ref = chat_ref.strip()
    if not ref:
        raise ChatNavigationError("Chat reference is empty.")
    if ref.startswith("http://") or ref.startswith("https://"):
        return ref
    if ref.startswith("/c/"):
        return f"{base_url.rstrip('/')}{ref}"
    if ref.startswith("c/"):
        return f"{base_url.rstrip('/')}/{ref}"
    return f"{base_url.rstrip('/')}/c/{ref}"

