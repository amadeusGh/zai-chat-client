from __future__ import annotations

from pathlib import Path
from typing import Any

from ..exceptions import CookieFileError, CookieFormatError


def _resolve_path(path_value: str | Path) -> Path:
    """Resolve user-provided path to absolute filesystem path."""
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def load_storage_state_from_netscape(path_value: str | Path) -> dict[str, Any]:
    """Parse Netscape cookie file and return Playwright-compatible storage_state."""
    path = _resolve_path(path_value)
    if not path.exists():
        raise CookieFileError(f"Cookies file does not exist: {path}")

    raw_text = path.read_text(encoding="utf-8")
    cookies = _parse_netscape_cookies(raw_text)
    if not cookies:
        raise CookieFormatError(
            "Unsupported cookies file format. Expected Netscape HTTP Cookie File text format."
        )
    return {"cookies": cookies, "origins": []}


def _parse_netscape_cookies(raw_text: str) -> list[dict[str, Any]]:
    """Parse raw Netscape cookie file content into Playwright cookie dicts."""
    cookies: list[dict[str, Any]] = []
    for line in raw_text.splitlines():
        raw_line = line.rstrip("\n")
        line = raw_line.strip()
        if not line:
            continue

        http_only = False
        # Netscape export may mark HttpOnly cookies using a commented prefix line.
        if raw_line.startswith("#HttpOnly_"):
            line = raw_line[1:].strip()
            http_only = True
        elif line.startswith("#"):
            continue

        parts = line.split("\t")
        if len(parts) != 7:
            # Fallback for files where tabs were replaced by spaces.
            parts = line.split(None, 6)
            if len(parts) != 7:
                continue

        domain, _flag, path, secure, expires, name, value = parts
        domain = domain.strip()
        name = name.strip()
        if not domain or not name:
            continue
        if domain.startswith("#HttpOnly_"):
            domain = domain[len("#HttpOnly_") :]
            http_only = True

        try:
            exp_int = int(expires)
        except ValueError:
            exp_int = 0

        cookie: dict[str, Any] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path or "/",
            "secure": secure.upper() == "TRUE",
            "httpOnly": http_only,
        }
        # In Netscape format, 0 means a session cookie.
        if exp_int > 0:
            cookie["expires"] = exp_int
        cookies.append(cookie)
    return cookies
