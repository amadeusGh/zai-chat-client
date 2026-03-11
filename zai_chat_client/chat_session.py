from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from functools import wraps
from typing import TYPE_CHECKING, Any, Awaitable, Callable, TypeVar

if TYPE_CHECKING:
    from .client import ZaiClient
    from .chat_message import ChatHistoryEntry, ChatMessage

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def _chat_action(*, pace: bool = False) -> Callable[[F], F]:
    """Wrap a chat action to apply pacing and action timestamp updates."""

    def decorator(func: F) -> F:
        """Decorate an async ChatSession method with pre/post action hooks."""
        @wraps(func)
        async def wrapper(self: "ChatSession", *args, **kwargs):
            """Execute the action while recording pacing/last-action metadata."""
            await self.client._before_chat_action(self, action_name=func.__name__, pace=pace)
            try:
                return await func(self, *args, **kwargs)
            finally:
                self.client._after_chat_action(self, action_name=func.__name__)

        return wrapper  # type: ignore[return-value]

    return decorator


@dataclass(slots=True)
class ChatSession:
    """Represents a concrete chat context and exposes chat-scoped operations."""

    client: "ZaiClient"
    url: str
    chat_id: str | None = None
    messages: list["ChatHistoryEntry"] = field(default_factory=list)
    last_action_at: datetime | None = None
    last_action_name: str | None = None
    last_action_monotonic: float | None = None

    @_chat_action(pace=False)
    async def ensure_open(self) -> None:
        """Ensure browser page is currently opened on this chat."""
        await self.client._ensure_chat_open(self)

    @_chat_action(pace=True)
    async def set_deep_think(self, enabled: bool) -> bool:
        """Enable or disable Deep Think mode in current chat."""
        await self.client._ensure_chat_open(self)
        return await self.client._set_deep_think(enabled)

    @_chat_action(pace=False)
    async def get_deep_think(self) -> bool:
        """Return current Deep Think toggle state."""
        await self.client._ensure_chat_open(self)
        return await self.client._get_deep_think()

    @_chat_action(pace=True)
    async def set_web_search(self, enabled: bool) -> bool:
        """Enable or disable Web Search mode in current chat."""
        await self.client._ensure_chat_open(self)
        return await self.client._set_web_search(enabled)

    @_chat_action(pace=False)
    async def get_web_search(self) -> bool:
        """Return current Web Search toggle state."""
        await self.client._ensure_chat_open(self)
        return await self.client._get_web_search()

    @_chat_action(pace=True)
    async def send_message(
        self,
        text: str,
        deep_think: bool | None = None,
        web_search: bool | None = None,
    ) -> "ChatMessage":
        """Send a message in this chat and wait for assistant response."""
        await self.client._ensure_chat_open(self)
        return await self.client._send_message_impl(
            self,
            text=text,
            deep_think=deep_think,
            web_search=web_search,
        )

    @_chat_action(pace=False)
    async def refresh_messages(self) -> list["ChatHistoryEntry"]:
        """Refresh chat history entries from current DOM state and return them."""
        await self.client._ensure_chat_open(self)
        return await self.client._refresh_chat_history(self)

    @_chat_action(pace=True)
    async def delete(self) -> bool:
        """Delete this chat from history."""
        return await self.client.delete_chat(self)
