class ZaiClientError(Exception):
    """Base error for the zai_chat_client package."""


class CookieFileError(ZaiClientError):
    """Raised when cookie file cannot be read."""


class CookieFormatError(ZaiClientError):
    """Raised when cookie file format is unsupported."""


class SessionStateError(ZaiClientError):
    """Raised when session state cannot be loaded or saved."""


class ManualLoginError(ZaiClientError):
    """Raised when manual login flow cannot be used."""


class AuthorizationError(ZaiClientError):
    """Raised when client cannot confirm authorized state."""


class UnsupportedChatModeError(ZaiClientError):
    """Raised when requested chat mode is unsupported by this client."""


class ChatNavigationError(ZaiClientError):
    """Raised when opening or resolving a chat URL fails."""


class MessageSendBlockedError(ZaiClientError):
    """Raised when message cannot be sent because generation is in progress."""
