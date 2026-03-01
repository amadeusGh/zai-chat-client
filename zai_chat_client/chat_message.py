from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .chat_session import ChatSession
    from .client import ZaiClient


@dataclass(slots=True)
class ChatMessage:
    """Result container for a sent prompt and its assistant response."""

    client: "ZaiClient"
    chat: "ChatSession"
    prompt_text: str
    deep_think: bool | None
    web_search: bool | None
    created_at: datetime
    response_text: str = ""
    response_chars: int = 0
    generation_started_at: datetime | None = None
    generation_finished_at: datetime | None = None
    generation_seconds: float | None = None
    assistant_message_dom_id: str | None = None
    refreshed_count: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Whether the message finished without tracking/collection errors."""
        return self.error is None

    async def regenerate(self) -> "ChatMessage":
        """Trigger response regeneration for this message."""
        return await self.client._regenerate_message(self)
