from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext

from ..exceptions import SessionStateError

_SESSION_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_session_name(session_name: str) -> str:
    """Normalize a user session alias so it can be safely used as a filename."""
    cleaned = _SESSION_NAME_RE.sub("_", session_name).strip("._")
    if not cleaned:
        raise SessionStateError("Session name is empty after sanitization.")
    return cleaned


class SessionStore:
    """Load/save Playwright storage state by session name or explicit path."""

    def __init__(self, base_dir: Path | None = None) -> None:
        """Create a session store rooted at `.sessions` unless overridden."""
        default_dir = Path.cwd() / ".sessions"
        self.base_dir = base_dir or default_dir

    def _resolve_path(self, session: str | Path | None) -> Path:
        """Resolve session identifier to concrete JSON file path."""
        if session is None:
            raise SessionStateError("Session value must be provided.")

        if isinstance(session, Path):
            raw = session.expanduser()
            if raw.is_absolute() or raw.parent != Path(".") or raw.suffix == ".json":
                return (raw if raw.is_absolute() else (Path.cwd() / raw)).resolve()
            safe_name = _safe_session_name(raw.name)
            return (self.base_dir / f"{safe_name}.json").resolve()

        raw_str = str(session).strip()
        if not raw_str:
            raise SessionStateError("Session value must not be empty.")
        path_candidate = Path(raw_str).expanduser()
        looks_like_path = (
            path_candidate.is_absolute()
            or path_candidate.parent != Path(".")
            or path_candidate.suffix == ".json"
            or "/" in raw_str
            or "\\" in raw_str
        )
        if looks_like_path:
            return (
                path_candidate
                if path_candidate.is_absolute()
                else (Path.cwd() / path_candidate)
            ).resolve()

        safe_name = _safe_session_name(raw_str)
        return (self.base_dir / f"{safe_name}.json").resolve()

    def load(
        self,
        session: str | Path | None = None,
    ) -> dict[str, Any] | None:
        """Load session storage state from disk."""
        path = self._resolve_path(session)
        if not path.exists():
            return None

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SessionStateError(f"Failed to read session file: {path}") from exc

        if not isinstance(raw, dict):
            raise SessionStateError("Session state must be a JSON object.")
        if not isinstance(raw.get("cookies"), list):
            raise SessionStateError("Session state must contain 'cookies' list.")
        if "origins" in raw and not isinstance(raw["origins"], list):
            raise SessionStateError("Session state 'origins' must be a list.")
        raw.setdefault("origins", [])
        return raw

    async def save(
        self,
        context: BrowserContext,
        session: str | Path | None = None,
    ) -> Path:
        """Save current browser context storage state to disk."""
        path = self._resolve_path(session)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=str(path))
        except Exception as exc:
            raise SessionStateError(f"Failed to save session state: {path}") from exc
        return path
