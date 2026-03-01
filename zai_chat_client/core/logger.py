from __future__ import annotations

from datetime import datetime


class ColorLogger:
    """Minimal terminal logger with optional ANSI colors."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    GRAY = "\033[90m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def _emit(self, color: str, level: str, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        if not self.enabled:
            print(f"[{ts}] [{level}] {message}")
            return
        print(
            f"{self.GRAY}[{ts}]{self.RESET} "
            f"{color}{self.BOLD}[{level}]{self.RESET} {message}"
        )

    def info(self, message: str) -> None:
        self._emit(self.BLUE, "INFO", message)

    def ok(self, message: str) -> None:
        self._emit(self.GREEN, "OK", message)

    def warn(self, message: str) -> None:
        self._emit(self.YELLOW, "WARN", message)

    def error(self, message: str) -> None:
        self._emit(self.RED, "ERROR", message)
