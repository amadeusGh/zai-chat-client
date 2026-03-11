from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass(slots=True)
class ChatHistoryEntry:
    """Single chat history entry for either user or assistant role."""

    client: "ZaiClient"
    chat: "ChatSession"
    role: str
    text: str
    dom_id: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    response_chars: int = 0
    generation_started_at: datetime | None = None
    generation_finished_at: datetime | None = None
    generation_seconds: float | None = None
    error: str | None = None
    source_message: ChatMessage | None = None

    @property
    def is_user(self) -> bool:
        """Return True if entry role is `user`."""
        return self.role == "user"

    @property
    def is_assistant(self) -> bool:
        """Return True if entry role is `assistant`."""
        return self.role == "assistant"

    @property
    def can_regenerate(self) -> bool:
        """Return True when regeneration is available for this entry."""
        return self.is_assistant and self.source_message is not None

    async def regenerate(self) -> ChatMessage:
        """Regenerate this assistant entry when linked to tracked message object."""
        if not self.can_regenerate or self.source_message is None:
            raise RuntimeError(
                "Regenerate is available only for assistant entries linked to a tracked message."
            )
        return await self.source_message.regenerate()
