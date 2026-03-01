from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ZaiClientConfig:
    """Internal immutable-like runtime configuration for ZaiClient."""

    base_url: str
    headless: bool = True
    use_camoufox: bool = True
    window_width: int | None = None
    window_height: int | None = None
    session: str | Path | None = None
    cookies_path: str | Path | None = None
    allow_manual_login: bool = False
    enable_logs: bool = False
    timeout_ms: int = 30_000
    navigation_retries: int = 3
    keep_browser_open_on_start_error: bool = False
    humanize_actions: bool = True
    min_action_delay_s: float = 0.4
    max_action_delay_s: float = 1.2
