from __future__ import annotations

import asyncio
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    async_playwright,
)

from .chat_session import ChatSession
from .chat_message import ChatMessage
from .core.chat_urls import extract_chat_id, normalize_chat_url
from .core.cookies import load_storage_state_from_netscape
from .core.logger import ColorLogger
from .core.session_store import SessionStore
from . import selectors as S
from .exceptions import (
    AuthorizationError,
    ChatNavigationError,
    ManualLoginError,
    MessageSendBlockedError,
    UnsupportedChatModeError,
)
from .types import ZaiClientConfig


class ZaiClient:
    """Async browser client for chat.z.ai.

    The client encapsulates authentication, chat lifecycle operations, message
    exchange, and response tracking.
    """
    _DEFAULT_WINDOW_WIDTH = 1920
    _DEFAULT_WINDOW_HEIGHT = 1080
    _GEN_POLL_SECONDS = 0.6
    _GEN_STALL_SECONDS = 22.0
    _GEN_TOTAL_TIMEOUT_SECONDS = 1800.0
    _GEN_MAX_REFRESHES = 2
    _GEN_HEARTBEAT_SECONDS = 10.0
    _GEN_WEB_SEARCH_STALE_SECONDS = 35.0
    _GEN_DONE_STABLE_SECONDS = 3.0
    _GEN_DONE_STABLE_REASONING_SECONDS = 8.0

    def __init__(
        self,
        base_url: str = "https://chat.z.ai",
        headless: bool = True,
        use_camoufox: bool = True,
        window_width: int | None = None,
        window_height: int | None = None,
        session: str | Path | None = None,
        cookies_path: str | Path | None = None,
        allow_manual_login: bool = False,
        enable_logs: bool = False,
        timeout_ms: int = 30_000,
        navigation_retries: int = 3,
        keep_browser_open_on_start_error: bool = False,
        humanize_actions: bool = True,
        min_action_delay_s: float = 0.4,
        max_action_delay_s: float = 1.2,
    ) -> None:
        """Initialize client configuration and runtime placeholders."""
        normalized_base_url = base_url.strip()
        if not normalized_base_url:
            raise ValueError("base_url must not be empty.")
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be greater than 0.")
        if navigation_retries < 1:
            raise ValueError("navigation_retries must be at least 1.")

        self.config = ZaiClientConfig(
            base_url=normalized_base_url,
            headless=headless,
            use_camoufox=use_camoufox,
            window_width=window_width,
            window_height=window_height,
            session=session,
            cookies_path=cookies_path,
            allow_manual_login=allow_manual_login,
            enable_logs=enable_logs,
            timeout_ms=timeout_ms,
            navigation_retries=navigation_retries,
            keep_browser_open_on_start_error=keep_browser_open_on_start_error,
            humanize_actions=humanize_actions,
            min_action_delay_s=min_action_delay_s,
            max_action_delay_s=max_action_delay_s,
        )
        self._session_store = SessionStore()
        self._log = ColorLogger(enabled=enable_logs)
        self._camoufox_cm: Any | None = None
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._authorized = False
        self._manual_cancelled = False
        self._closing = False
        self._window_width = 0
        self._window_height = 0

    @property
    def page(self) -> Page:
        """Return active page instance.

        Raises:
            RuntimeError: If client was not started.
        """
        if self._page is None:
            raise RuntimeError("Client is not started. Call 'await client.start()' first.")
        return self._page

    @property
    def context(self) -> BrowserContext:
        """Return active browser context.

        Raises:
            RuntimeError: If client was not started.
        """
        if self._context is None:
            raise RuntimeError("Client is not started. Call 'await client.start()' first.")
        return self._context

    @property
    def authorized(self) -> bool:
        """Return the last known authorization state."""
        return self._authorized

    async def start(self) -> "ZaiClient":
        """Start browser/context, load auth state and verify authorization."""
        if self._playwright is not None or self._camoufox_cm is not None:
            return self

        self._manual_cancelled = False
        self._log.info("Starting browser client")
        try:
            if self.config.allow_manual_login and self.config.headless:
                raise ManualLoginError(
                    "Manual login requires headed mode. Set headless=False."
                )
            await self._resolve_window_size()
            session_state = self._load_session_state()
            cookies_state = self._load_cookies_state()
            if cookies_state is not None:
                self._log.info(
                    f"Cookies loaded: {len(cookies_state.get('cookies', []))}"
                )
            initial_state = session_state
            initial_state_source = "none"

            if session_state is not None:
                self._log.info("Session found. Loading saved session state")
                initial_state_source = "session"
            elif cookies_state is not None:
                self._log.info("Session not found. Loading cookies state")
                initial_state = cookies_state
                initial_state_source = "cookies"
            await self._launch_browser_engine()
            await self._new_context_and_page(storage_state=initial_state)
            await self._navigate_with_retries(self.config.base_url)
            await self._dismiss_startup_popup()

            if not await self._wait_authorization_resolved():
                if cookies_state is not None and initial_state_source != "cookies":
                    self._log.warn("Saved session is not authorized. Trying cookies")
                    await self._new_context_and_page(storage_state=cookies_state)
                    await self._navigate_with_retries(self.config.base_url)
                    await self._dismiss_startup_popup()
                elif initial_state_source == "cookies":
                    self._log.warn("Cookies applied, but authorization is not confirmed")

            authorized = await self.ensure_authorized()
            if authorized and self._has_session_target:
                self._log.ok("Authorized")
            elif authorized:
                self._log.ok("Authorized")
            else:
                self._log.warn("Authorization canceled by user")
        except Exception:
            if self.config.keep_browser_open_on_start_error:
                self._log.error("Client start failed. Browser is kept open for debugging")
            else:
                self._log.error("Client start failed. Closing resources")
                await self.close()
            raise
        return self

    async def close(self) -> None:
        """Gracefully close browser resources and persist session if needed."""
        if self._closing:
            return
        self._closing = True
        self._log.info("Closing browser client")
        try:
            if self._context is not None and self._has_session_target and self._authorized:
                await self.save_session()

            if self._camoufox_cm is not None:
                try:
                    await self._camoufox_cm.__aexit__(None, None, None)
                except Exception as exc:
                    self._log.warn(f"Camoufox shutdown warning: {exc}")
            else:
                if self._context is not None:
                    try:
                        await self._context.close()
                    except Exception as exc:
                        self._log.warn(f"Context close warning: {exc}")
                if self._browser is not None:
                    try:
                        await self._browser.close()
                    except Exception as exc:
                        self._log.warn(f"Browser close warning: {exc}")
                if self._playwright is not None:
                    try:
                        await self._playwright.stop()
                    except Exception as exc:
                        self._log.warn(f"Playwright stop warning: {exc}")
        finally:
            self._context = None
            self._browser = None
            self._playwright = None
            self._camoufox_cm = None
            self._page = None
            self._authorized = False
            self._manual_cancelled = False
            self._closing = False

    async def __aenter__(self) -> "ZaiClient":
        """Enter async context by starting the browser client."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context by closing browser resources."""
        await self.close()

    async def open(self, url: str) -> None:
        """Navigate current page to a URL with retry logic."""
        await self._navigate_with_retries(url)
        if self._has_session_target and self._authorized:
            await self.save_session()

    async def save_session(self) -> None:
        """Persist current context storage state to configured session target."""
        if not self._has_session_target or self._context is None:
            return
        await self._session_store.save(
            context=self._context,
            session=self.config.session,
        )
        self._log.ok(f"Session state updated: {self.config.session}")

    async def wait_for_manual_login(
        self,
    ) -> bool:
        """Prompt user to complete login in browser and re-check auth state."""
        if self.config.headless:
            raise ManualLoginError("Manual login is available only in headed mode (headless=False).")
        if not self.config.allow_manual_login:
            raise ManualLoginError("Manual login is disabled. Set allow_manual_login=True.")

        self._log.warn("Manual login required")
        answer = (
            await asyncio.to_thread(
                input,
                "[Manual login] Sign in in the browser and press Enter to continue (q to cancel): ",
            )
        ).strip().lower()
        if answer in {"q", "quit", "exit"}:
            self._manual_cancelled = True
            return False

        await self._navigate_with_retries(self.config.base_url)
        await self._dismiss_startup_popup_silent()
        if await self._wait_authorization_resolved():
            self._authorized = True
            if self._has_session_target:
                await self.save_session()
            return True
        return False

    async def ensure_authorized(
        self,
    ) -> bool:
        """Ensure the client is authorized, optionally using manual login flow."""
        if await self._wait_authorization_resolved():
            if self._has_session_target:
                self._authorized = True
                await self.save_session()
            return True

        if self.config.allow_manual_login and not self.config.headless:
            success = await self.wait_for_manual_login()
            if success:
                self._authorized = True
                return True
            if self._manual_cancelled:
                return False
            raise AuthorizationError("Manual login failed or was canceled.")

        raise AuthorizationError("Client is not authorized: no valid session/cookies.")

    async def is_authorized(self) -> bool:
        """Quick authorization probe based on profile/sign-in elements."""
        profile = self.page.locator(S.USER_PROFILE_IMAGE).first
        if await profile.count() > 0 and await profile.is_visible():
            return True

        sign_in_button = self.page.get_by_role("button", name=S.SIGN_IN_BUTTON_NAME)
        if await sign_in_button.count() > 0 and await sign_in_button.first.is_visible():
            return False

        return False

    async def _wait_authorization_resolved(self, timeout_ms: int = 8_000) -> bool:
        """Wait until auth UI resolves to either profile-visible or sign-in-visible."""
        deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
        while True:
            profile = self.page.locator(S.USER_PROFILE_IMAGE).first
            if await profile.count() > 0 and await profile.is_visible():
                return True

            sign_in_button = self.page.get_by_role("button", name=S.SIGN_IN_BUTTON_NAME)
            if await sign_in_button.count() > 0 and await sign_in_button.first.is_visible():
                return False

            if asyncio.get_running_loop().time() >= deadline:
                return await self.is_authorized()
            await asyncio.sleep(0.2)

    async def _dismiss_startup_popup(
        self,
        chat: ChatSession | None = None,
        allow_chat_restore: bool = True,
    ) -> bool:
        """Dismiss known blocking dialogs and return whether a model switch happened."""
        switched_model = False
        for _ in range(8):
            action_taken = False
            popup = self.page.locator("div[role='dialog']").filter(
                has_text=re.compile(r"(GLM-?5\s+Now\s+Available|Now\s+Available)", re.I)
            ).first
            if await popup.count() > 0 and await popup.is_visible():
                popup_later = popup.get_by_role(
                    "button", name=re.compile(r"^(Later|Not now|Maybe later)$", re.I)
                ).first
                if await popup_later.count() > 0 and await popup_later.is_visible():
                    await popup_later.click()
                    self._log.info("Dismissed startup popup via dialog action button")
                    await self._short_pause(220)
                    action_taken = True
                    continue

                popup_close = popup.locator(
                    "button[aria-label='Close'], "
                    "button[aria-label*='close' i], "
                    "button:has(svg path[d='M6 18 18 6M6 6l12 12'])"
                ).first
                if await popup_close.count() > 0 and await popup_close.is_visible():
                    await popup_close.click()
                    self._log.info("Dismissed startup popup via dialog close button")
                    await self._short_pause(220)
                    action_taken = True
                    continue

            later_button = self.page.get_by_role(
                "button", name=re.compile(r"^(Later|Not now|Maybe later)$", re.I)
            ).first
            if await later_button.count() > 0 and await later_button.is_visible():
                await later_button.click()
                self._log.info("Dismissed startup popup via fallback action button")
                await self._short_pause(220)
                action_taken = True
                continue

            # Fallback: close by top-right X button on popup container.
            close_button = self.page.locator(
                "div.shadow-3xl button:has(svg path[d='M6 18 18 6M6 6l12 12'])"
            ).first
            if await close_button.count() > 0 and await close_button.is_visible():
                await close_button.click()
                self._log.info("Dismissed startup popup via fallback close button")
                await self._short_pause(220)
                action_taken = True
                continue
            switched_now = await self._handle_peak_hours_popup(
                chat=chat,
                allow_chat_restore=allow_chat_restore,
            )
            if switched_now:
                switched_model = True
                action_taken = True
            if not action_taken:
                return switched_model
        return switched_model

    async def _handle_peak_hours_popup(
        self,
        chat: ChatSession | None = None,
        allow_chat_restore: bool = True,
    ) -> bool:
        """Handle the peak-hours modal by clicking `Switch to ...` and restoring chat."""
        restore_chat = chat
        if restore_chat is None:
            current_id = extract_chat_id(self.page.url)
            if current_id:
                restore_chat = ChatSession(
                    client=self,
                    url=self.page.url,
                    chat_id=current_id,
                )

        switch_button = await self._find_visible_peak_hours_switch_button()
        if switch_button is None:
            return False

        try:
            label = (await switch_button.inner_text() or "").strip()
        except Exception:
            label = ""
        self._log.warn(
            "Peak-hours popup detected. "
            f"Applying suggested model switch: {label or 'Switch to ...'}"
        )

        try:
            await self._wait_clickable(
                switch_button,
                "Peak-hours switch button",
                timeout_ms=4_000,
            )
            await switch_button.click()
            await self._short_pause(250)
        except Exception as exc:
            self._log.warn(f"Could not click peak-hours switch button: {exc}")
            return False

        # Observe for a short window in case dialog reappears after reload.
        deadline = asyncio.get_running_loop().time() + 8.0
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.5)
            repeated = await self._find_visible_peak_hours_switch_button()
            if repeated is None:
                continue
            self._log.warn("Peak-hours popup appeared again. Retrying switch")
            try:
                await self._wait_clickable(
                    repeated,
                    "Peak-hours switch button",
                    timeout_ms=3_000,
                )
                await repeated.click()
                await self._short_pause(250)
            except Exception as exc:
                self._log.warn(f"Retry click for peak-hours popup failed: {exc}")

        if allow_chat_restore and restore_chat is not None:
            await self._restore_chat_after_peak_hours_switch(restore_chat)

        return True

    async def _find_visible_peak_hours_switch_button(self) -> Locator | None:
        """Return the first visible `Switch to ...` button from the active UI."""
        # Preferred: switch button inside an active dialog.
        dialog_candidates = self.page.locator(
            "div[role='dialog'] button"
        ).filter(has_text=re.compile(r"^\s*Switch\s+to\b", re.I))
        visible_dialog_switch = await self._first_visible(dialog_candidates)
        if visible_dialog_switch is not None:
            return visible_dialog_switch

        # Fallback: role-based query in case dialog markup changes.
        role_candidates = self.page.get_by_role(
            "button", name=re.compile(r"^\s*Switch\s+to\b", re.I)
        )
        return await self._first_visible(role_candidates)

    async def _restore_chat_after_peak_hours_switch(self, chat: ChatSession) -> None:
        """Re-open expected chat if a model switch navigation moved the user away."""
        target_id = chat.chat_id or extract_chat_id(chat.url)
        if not target_id:
            return
        current_id = extract_chat_id(self.page.url)
        if current_id == target_id:
            chat.url = self.page.url
            chat.chat_id = current_id
            return

        target_url = normalize_chat_url(self.config.base_url, target_id)
        self._log.warn(
            "Re-opening target chat after model switch popup: "
            f"{target_url}"
        )
        try:
            await self._navigate_with_retries(target_url)
            await self._short_pause(300)
        except Exception as exc:
            self._log.warn(f"Failed to navigate back to chat after model switch: {exc}")
            return

        deadline = asyncio.get_running_loop().time() + 6.0
        while asyncio.get_running_loop().time() < deadline:
            current_id = extract_chat_id(self.page.url)
            if current_id == target_id:
                chat.url = self.page.url
                chat.chat_id = current_id
                self._log.info(f"Chat restored after model switch: {current_id}")
                return
            await asyncio.sleep(0.2)

        self._log.warn(
            "Could not confirm chat restoration after model switch popup. "
            f"Current URL: {self.page.url}"
        )

    @property
    def _has_session_target(self) -> bool:
        """Whether session persistence is configured."""
        return self.config.session is not None

    def _load_session_state(self) -> dict[str, Any] | None:
        """Load saved session storage state from disk if configured."""
        if not self._has_session_target:
            return None
        return self._session_store.load(session=self.config.session)

    def _load_cookies_state(self) -> dict[str, Any] | None:
        """Load Playwright storage state generated from Netscape cookies file."""
        if not self.config.cookies_path:
            return None
        return load_storage_state_from_netscape(self.config.cookies_path)

    async def _resolve_window_size(self) -> None:
        """Resolve final browser window dimensions with optional host auto-detection."""
        width = self.config.window_width
        height = self.config.window_height

        if (
            not self.config.headless
            and self.config.allow_manual_login
            and (width is None or height is None)
        ):
            detected = await asyncio.to_thread(self._detect_host_screen_size)
            if detected is not None:
                detected_width, detected_height = detected
                width = width or detected_width
                height = height or detected_height
                self._log.info(
                    f"Window size auto-detected from host screen: {width}x{height}"
                )
            else:
                self._log.warn(
                    "Failed to auto-detect host screen size. Using default 1920x1080"
                )

        width = self._normalize_window_value(width, self._DEFAULT_WINDOW_WIDTH)
        height = self._normalize_window_value(height, self._DEFAULT_WINDOW_HEIGHT)
        self._window_width = width
        self._window_height = height

    def _normalize_window_value(self, value: int | None, fallback: int) -> int:
        """Normalize a window dimension into a positive integer."""
        if value is None:
            return fallback
        try:
            parsed = int(value)
        except Exception:
            return fallback
        return parsed if parsed > 0 else fallback

    def _detect_host_screen_size(self) -> tuple[int, int] | None:
        """Detect host screen size using available platform-specific backends."""
        detected = self._detect_screen_size_tkinter()
        if detected is not None:
            return detected

        detected = self._detect_screen_size_windows()
        if detected is not None:
            return detected

        detected = self._detect_screen_size_linux()
        if detected is not None:
            return detected
        return None

    def _detect_screen_size_tkinter(self) -> tuple[int, int] | None:
        """Detect screen size via Tkinter (cross-platform, preferred fallback)."""
        try:
            import tkinter
        except Exception:
            return None
        try:
            root = tkinter.Tk()
            root.withdraw()
            width = int(root.winfo_screenwidth())
            height = int(root.winfo_screenheight())
            root.destroy()
            if self._is_valid_screen_size(width, height):
                return width, height
        except Exception:
            return None
        return None

    def _detect_screen_size_windows(self) -> tuple[int, int] | None:
        """Detect screen size via Win32 API on Windows hosts."""
        try:
            import ctypes
            import platform

            if platform.system().lower() != "windows":
                return None
            width = int(ctypes.windll.user32.GetSystemMetrics(0))
            height = int(ctypes.windll.user32.GetSystemMetrics(1))
            if self._is_valid_screen_size(width, height):
                return width, height
        except Exception:
            return None
        return None

    def _detect_screen_size_linux(self) -> tuple[int, int] | None:
        """Detect screen size on Linux via `xrandr`/`xdpyinfo` commands."""
        try:
            import platform

            if platform.system().lower() != "linux":
                return None
        except Exception:
            return None

        # xrandr: "current 1920 x 1080"
        parsed = self._read_screen_size_from_command(
            ["xrandr", "--current"],
            re.compile(r"current\s+(\d+)\s+x\s+(\d+)", re.I),
        )
        if parsed is not None:
            return parsed

        # xdpyinfo: "dimensions:    1920x1080 pixels"
        parsed = self._read_screen_size_from_command(
            ["xdpyinfo"],
            re.compile(r"dimensions:\s+(\d+)x(\d+)\s+pixels", re.I),
        )
        if parsed is not None:
            return parsed
        return None

    def _read_screen_size_from_command(
        self,
        command: list[str],
        pattern: re.Pattern[str],
    ) -> tuple[int, int] | None:
        """Run a command and parse screen dimensions from its output."""
        try:
            import subprocess

            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2.0,
                check=False,
            )
            output = (result.stdout or "").strip()
            if not output:
                return None
            match = pattern.search(output)
            if not match:
                return None
            width = int(match.group(1))
            height = int(match.group(2))
            if self._is_valid_screen_size(width, height):
                return width, height
        except Exception:
            return None
        return None

    def _is_valid_screen_size(self, width: int, height: int) -> bool:
        """Return `True` when width and height are positive numbers."""
        return width > 0 and height > 0

    async def _launch_browser_engine(self) -> None:
        """Start Camoufox or Playwright browser engine according to config."""
        if self.config.use_camoufox:
            try:
                from camoufox.async_api import AsyncCamoufox
                from browserforge.fingerprints.generator import Screen
            except ImportError as exc:
                raise RuntimeError(
                    "Camoufox is not installed. Install it or set use_camoufox=False."
                ) from exc

            screen = Screen(
                min_width=self._window_width,
                max_width=self._window_width,
                min_height=self._window_height,
                max_height=self._window_height,
            )
            self._camoufox_cm = AsyncCamoufox(
                headless=self.config.headless,
                window=(self._window_width, self._window_height),
                screen=screen,
            )
            launched = await self._camoufox_cm.__aenter__()
            if isinstance(launched, BrowserContext):
                self._context = launched
                self._context.set_default_timeout(self.config.timeout_ms)
                self._browser = None
            else:
                self._browser = launched
            return

        self._playwright = await async_playwright().start()
        launch_args: dict[str, Any] = {"headless": self.config.headless}
        if not self.config.headless:
            launch_args["args"] = [f"--window-size={self._window_width},{self._window_height}"]
        self._browser = await self._playwright.chromium.launch(**launch_args)

    async def _new_context_and_page(self, storage_state: dict[str, Any] | None = None) -> None:
        """Create a fresh context/page and optionally preload storage state."""
        if self._browser is None and self._context is None:
            raise RuntimeError("Browser is not started.")

        if self._browser is not None:
            if self._context is not None:
                await self._context.close()
                self._context = None
            context_args: dict[str, Any] = {}
            if storage_state is not None:
                context_args["storage_state"] = storage_state
            if self.config.headless:
                context_args["viewport"] = {
                    "width": self._window_width,
                    "height": self._window_height,
                }
            else:
                # In headed mode, use native window size to avoid viewport clipping/mismatch.
                context_args["viewport"] = None
            self._context = await self._browser.new_context(**context_args)
            self._context.set_default_timeout(self.config.timeout_ms)
            self._page = await self._context.new_page()
            return

        if storage_state is not None:
            cookies = storage_state.get("cookies")
            if not isinstance(cookies, list):
                raise RuntimeError("storage_state for persistent context must contain 'cookies' list.")
            await self._context.add_cookies(cookies)
        if self._context is None:
            raise RuntimeError("Context is unavailable.")
        if self._page is not None and not self._page.is_closed():
            await self._page.close()
        self._context.set_default_timeout(self.config.timeout_ms)
        self._page = await self._context.new_page()

    async def _navigate_with_retries(self, url: str) -> None:
        """Navigate with retry logic and robust load-state waits."""
        last_error: Exception | None = None
        for attempt in range(1, self.config.navigation_retries + 1):
            try:
                await self.page.goto(url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                await self.page.wait_for_load_state("load", timeout=self.config.timeout_ms)
                # Network idle may never happen on modern apps due to background polling/websockets.
                try:
                    await self.page.wait_for_load_state("networkidle", timeout=min(7_000, self.config.timeout_ms))
                except Exception:
                    pass
                return
            except Exception as exc:
                last_error = exc
                if attempt == self.config.navigation_retries:
                    break
                await asyncio.sleep(1.0)
        if last_error is not None:
            raise last_error

    async def create_chat(
        self,
        model: str | None = None,
        mode: str = "chat",
        deep_think: bool | None = None,
        web_search: bool | None = None,
    ) -> ChatSession:
        """Create a new chat context and optionally configure model/toggles."""
        self._log.info("Creating new chat")
        await self._click_new_chat_button()
        await self._ensure_chat_mode(mode=mode, strict=False)
        if model:
            await self._select_model(model)
        if deep_think is not None:
            await self._set_deep_think(deep_think)
        if web_search is not None:
            await self._set_web_search(web_search)
        chat = ChatSession(
            client=self,
            url=self.page.url,
            chat_id=extract_chat_id(self.page.url),
        )
        self._log.ok(f"New chat ready: {chat.url}")
        return chat

    async def open_chat(self, chat_ref: str) -> ChatSession:
        """Open an existing chat by id or URL and validate final target id."""
        target_url = normalize_chat_url(self.config.base_url, chat_ref)
        target_id = extract_chat_id(target_url)
        if not target_id:
            raise ChatNavigationError(f"Invalid chat reference: {chat_ref}")
        target_chat = ChatSession(client=self, url=target_url, chat_id=target_id)
        self._log.info(f"Opening chat: {target_url}")
        await self._dismiss_startup_popup_silent(chat=target_chat)
        if self.page.url.rstrip("/") != target_url.rstrip("/"):
            await self.open(target_url)
        await self._dismiss_startup_popup_silent(chat=target_chat)
        await self._short_pause(250)

        # Stabilize URL to catch delayed SPA redirects for invalid/deleted chats.
        stable_hits = 0
        for _ in range(20):
            await self._dismiss_startup_popup_silent(chat=target_chat)
            current_url = self.page.url
            current_url_norm = current_url.rstrip("/")
            current_id = extract_chat_id(current_url)

            if current_url_norm == self.config.base_url.rstrip("/"):
                raise ChatNavigationError(
                    f"Chat not found or access denied: {chat_ref}. Redirected to home page."
                )
            if current_id and current_id != target_id:
                raise ChatNavigationError(
                    f"Opened a different chat. Expected '{target_id}', got '{current_id}'."
                )
            if current_id == target_id:
                stable_hits += 1
                if stable_hits >= 3:
                    break
            else:
                stable_hits = 0
            await asyncio.sleep(0.2)

        chat_id = extract_chat_id(self.page.url)
        if chat_id != target_id:
            raise ChatNavigationError(
                f"Failed to open target chat '{target_id}'. Current URL: {self.page.url}"
            )
        chat = ChatSession(client=self, url=self.page.url, chat_id=chat_id)
        self._log.ok(f"Chat opened: {chat.chat_id}")
        return chat

    async def send_message(
        self,
        text: str,
        deep_think: bool | None = None,
        web_search: bool | None = None,
    ) -> ChatMessage:
        """Send a message in the current chat page context."""
        chat = ChatSession(
            client=self,
            url=self.page.url,
            chat_id=extract_chat_id(self.page.url),
        )
        return await self._send_message_impl(
            chat=chat,
            text=text,
            deep_think=deep_think,
            web_search=web_search,
        )

    async def _ensure_chat_open(self, chat: ChatSession) -> None:
        """Ensure that the current page points to the target chat session."""
        target_id = chat.chat_id or extract_chat_id(chat.url)
        if target_id is None:
            return
        current_id = extract_chat_id(self.page.url)
        if current_id == target_id:
            chat.url = self.page.url
            chat.chat_id = current_id
            return
        opened = await self.open_chat(target_id)
        chat.url = opened.url
        chat.chat_id = opened.chat_id

    async def _send_message_impl(
        self,
        chat: ChatSession,
        text: str,
        deep_think: bool | None = None,
        web_search: bool | None = None,
    ) -> ChatMessage:
        """Send one prompt and wait until response tracking is fully completed."""
        await self._dismiss_startup_popup_silent(chat=chat)
        if not text.strip():
            raise ValueError("Message text must not be empty.")

        message = ChatMessage(
            client=self,
            chat=chat,
            prompt_text=text,
            deep_think=deep_think,
            web_search=web_search,
            created_at=datetime.now(),
        )

        if deep_think is not None:
            await self._set_deep_think(deep_think)
        if web_search is not None:
            await self._set_web_search(web_search)

        await self._ensure_chat_mode("chat", strict=False)
        input_box = await self._resolve_input_box()
        if input_box is None:
            raise MessageSendBlockedError(
                "Message input element not found. Selector may need update."
            )
        await self._wait_clickable(input_box, "Chat input")

        response_count_before = await self._response_containers().count()
        send_button = self.page.locator(S.SEND_MESSAGE_BUTTON).first
        if await send_button.count() == 0:
            raise MessageSendBlockedError(
                "Cannot send message now: send button is unavailable."
            )
        await input_box.click()
        tag_name = (await input_box.evaluate("el => el.tagName")).lower()
        if tag_name == "textarea":
            await input_box.fill(text)
        else:
            await input_box.fill("")
            await input_box.type(text)
        await self._short_pause(120)
        await self._wait_send_button_ready(send_button, timeout_ms=8_000)

        previous_url = self.page.url
        started_monotonic = time.monotonic()
        await send_button.click()
        message.generation_started_at = datetime.now()
        self._log.info("Message submitted. Waiting for generation to finish")
        try:
            await self.page.wait_for_url("**/c/*", timeout=8_000)
        except Exception:
            pass
        await self._dismiss_startup_popup_silent(chat=chat)
        chat.url = self.page.url
        chat.chat_id = extract_chat_id(self.page.url)
        if previous_url != self.page.url:
            self._log.ok(f"Chat URL updated: {self.page.url}")
        try:
            container = await self._wait_for_response_container(
                response_count_before, timeout_ms=18_000
            )
            message = await self._collect_response_until_done(
                chat=chat,
                message=message,
                container=container,
            )
        except Exception as exc:
            message.error = str(exc)
            message.generation_finished_at = datetime.now()
            if message.generation_started_at is not None:
                message.generation_seconds = (
                    message.generation_finished_at - message.generation_started_at
                ).total_seconds()
            self._log.error(f"Message tracking failed: {exc}")
        total_elapsed = time.monotonic() - started_monotonic
        if message.generation_seconds is None or total_elapsed > message.generation_seconds:
            message.generation_seconds = total_elapsed
        if message.ok:
            self._log.ok(
                f"Response ready: {message.response_chars} chars in {message.generation_seconds:.1f}s"
            )
        else:
            self._log.warn(
                f"Response completed with warning: {message.error} "
                f"(chars={message.response_chars}, refreshes={message.refreshed_count})"
            )
        return message

    async def _resolve_input_box(self) -> Locator | None:
        """Find the currently visible composer input element."""
        selectors = (
            S.CHAT_INPUT_TEXTAREA,
            "textarea",
            "div[contenteditable='true']",
            "[role='textbox'][contenteditable='true']",
        )
        for selector in selectors:
            box = self.page.locator(selector).first
            if await box.count() > 0 and await box.is_visible():
                return box
        return None

    async def _wait_composer_ready(self, timeout_ms: int) -> None:
        """Wait until chat composer controls are available for interaction."""
        # Core readiness: input + send button zone.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (timeout_ms / 1000)
        next_popup_check = 0.0
        while True:
            now = loop.time()
            if now >= next_popup_check:
                await self._dismiss_startup_popup_silent()
                next_popup_check = now + 0.5
            box = await self._resolve_input_box()
            if box is not None:
                break
            if now >= deadline:
                raise ChatNavigationError("Composer input did not become visible in time.")
            await asyncio.sleep(0.1)

        # Optional UI parts may be absent for some models/layouts.
        model_selector = self.page.locator(S.MODEL_SELECTOR_BUTTON).first
        if await model_selector.count() > 0:
            try:
                await model_selector.wait_for(state="visible", timeout=min(3_000, timeout_ms))
            except Exception:
                self._log.warn("Model selector not visible yet. Continuing")

        chat_tab = self.page.locator(S.CHAT_MODE_TAB).first
        if await chat_tab.count() > 0:
            try:
                await chat_tab.wait_for(state="visible", timeout=min(3_000, timeout_ms))
            except Exception:
                self._log.warn("Chat mode tab not visible yet. Continuing")

    def _response_containers(self):
        """Return locator for all assistant response containers on the page."""
        return self.page.locator(S.RESPONSE_CONTAINER)

    async def _wait_for_response_container(
        self,
        previous_count: int | None,
        timeout_ms: int = 18_000,
    ) -> Locator:
        """Wait for the newly created response container after message submit."""
        deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
        containers = self._response_containers()
        while True:
            count = await containers.count()
            if count > 0:
                if previous_count is None or count > previous_count:
                    return containers.nth(count - 1)
            if asyncio.get_running_loop().time() >= deadline:
                if count > 0:
                    latest = containers.nth(count - 1)
                    if previous_count is not None and count <= previous_count:
                        if await self._is_response_busy(latest):
                            self._log.warn(
                                "No new response container detected, but latest is generating. "
                                "Using latest block."
                            )
                            return latest
                        raise ChatNavigationError(
                            "New response container was not detected after sending message."
                        )
                    self._log.warn(
                        "Timed out waiting for a new response container. "
                        "Using latest visible response block."
                    )
                    return latest
                raise ChatNavigationError("Assistant response container did not appear.")
            await asyncio.sleep(0.12)

    async def _extract_response_text(self, container: Locator) -> str:
        """Extract assistant text while filtering thinking/search UI artifacts."""
        prose = container.locator(".markdown-prose").first
        if await prose.count() > 0:
            try:
                raw = (
                    await prose.evaluate(
                        """(el) => {
                            const root = el.cloneNode(true);
                            root.querySelectorAll(
                              '.thinking-chain-container, .thinking-block, blockquote[slot="content"]'
                            ).forEach((node) => node.remove());
                            const blocks = [];
                            root.querySelectorAll('p, li, blockquote, pre, h1, h2, h3, h4, h5, h6')
                              .forEach((node) => {
                                const text = (node.innerText || '').trim();
                                if (text) blocks.push(text);
                              });
                            if (blocks.length > 0) {
                              return blocks.join('\\n\\n');
                            }
                            return (root.innerText || '').trim();
                        }"""
                    )
                    or ""
                ).strip()
                raw = re.sub(r"[ \t]+\n", "\n", raw)
                raw = re.sub(r"(?im)^\s*Searching the web\s*$", "", raw)
                raw = re.sub(
                    r"(?im)^\s*(Thought Process|Thinking(?:\.\.\.)?|Searching the web)\s*$",
                    "",
                    raw,
                )
                raw = re.sub(r"\n{3,}", "\n\n", raw)
                return raw.strip()
            except Exception:
                # Non-critical while response is still streaming; fallback below.
                pass
        text = await self._safe_locator_inner_text(container, timeout_ms=800)
        text = re.sub(
            r"(?im)^\s*(Thought Process|Thinking(?:\.\.\.)?|Searching the web)\s*$",
            "",
            text,
        )
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    async def _is_response_generating(self, container: Locator) -> bool:
        """Check if dot animation indicates active token generation."""
        dots = container.locator(S.GEN_DOT)
        count = await dots.count()
        if count == 0:
            return False
        for i in range(count):
            if await dots.nth(i).is_visible():
                return True
        return False

    async def _is_response_busy(self, container: Locator) -> bool:
        """Return whether any response phase (generate/think/search) is active."""
        if await self._is_response_generating(container):
            return True
        if await self._is_thinking_active(container):
            return True
        if await self._is_web_search_active(container):
            return True
        return False

    async def _collect_response_until_done(
        self,
        chat: ChatSession,
        message: ChatMessage,
        container: Locator,
    ) -> ChatMessage:
        """Track response lifecycle until stable completion or terminal error."""
        start = time.monotonic()
        last_change = start
        last_text = ""
        log_mark = 0
        last_heartbeat = start
        last_thinking_text = ""
        last_web_search_text = ""
        thinking_was_active = False
        web_search_was_active = False
        seen_busy_signal = False
        last_busy_signal_at = start
        done_candidate_at: float | None = None
        saw_reasoning_signal = False

        while True:
            # Popups can interrupt page context mid-generation; recover container if needed.
            popup_switched_model = await self._dismiss_startup_popup_silent(chat=chat)
            if popup_switched_model:
                prefix = (message.response_text or "")[:80]
                try:
                    container = await self._recover_response_container(prefix=prefix)
                except Exception as exc:
                    self._log.warn(
                        "Could not re-bind response container after model switch popup: "
                        f"{exc}"
                    )
            current_text = await self._extract_response_text(container)
            if current_text != last_text:
                last_text = current_text
                message.response_text = current_text
                message.response_chars = len(current_text)
                last_change = time.monotonic()
                if message.response_chars >= log_mark + 160:
                    log_mark = message.response_chars
                    self._log.info(f"Generation progress: {message.response_chars} chars")

            generating = await self._is_response_generating(container)
            thinking_active = await self._is_thinking_active(container)
            web_search_active = await self._is_web_search_active(container)
            now = time.monotonic()
            if generating or thinking_active or web_search_active:
                seen_busy_signal = True
                last_busy_signal_at = now
                done_candidate_at = None
            if thinking_active or web_search_active:
                saw_reasoning_signal = True
            thinking_text = await self._extract_thinking_text(container)
            web_search_text = await self._extract_web_search_text(container)
            if (
                web_search_active
                and not generating
                and not thinking_active
                and message.response_chars > 0
            ):
                # Some responses keep the "Searching the web" marker visible even after completion.
                web_search_active = False
            if thinking_active and not thinking_was_active:
                self._log.info("Thinking phase started")
            if not thinking_active and thinking_was_active:
                self._log.info("Thinking phase finished")
            thinking_was_active = thinking_active
            if thinking_text and thinking_text != last_thinking_text:
                last_thinking_text = thinking_text
                self._log.info(f"Thinking: {thinking_text}")
            if web_search_active and not web_search_was_active:
                self._log.info("Web search phase started")
            if not web_search_active and web_search_was_active:
                self._log.info("Web search phase finished")
            web_search_was_active = web_search_active
            if web_search_text and web_search_text != last_web_search_text:
                last_web_search_text = web_search_text
                self._log.info(f"Web search: {web_search_text}")

            elapsed = now - start
            stalled_for = now - last_change
            idle_after_busy = now - last_busy_signal_at

            if (
                web_search_active
                and not generating
                and not thinking_active
                and stalled_for > self._GEN_WEB_SEARCH_STALE_SECONDS
            ):
                self._log.warn(
                    "Web search indicator looks stale. Continuing without waiting for it"
                )
                web_search_active = False

            if elapsed - (last_heartbeat - start) >= self._GEN_HEARTBEAT_SECONDS:
                last_heartbeat = time.monotonic()
                self._log.info(
                    "Waiting response... "
                    f"elapsed={int(elapsed)}s, chars={message.response_chars}, "
                    f"generating={generating}, thinking={thinking_active}, "
                    f"web_search={web_search_active}, "
                    f"refreshes={message.refreshed_count}"
                )

            if (
                not generating
                and not thinking_active
                and not web_search_active
                and message.response_chars > 0
            ):
                # Require a stability window to avoid premature completion on delayed updates.
                stable_required = (
                    self._GEN_DONE_STABLE_REASONING_SECONDS
                    if saw_reasoning_signal
                    else self._GEN_DONE_STABLE_SECONDS
                )
                if done_candidate_at is None:
                    done_candidate_at = now
                    await asyncio.sleep(0.2)
                    continue
                if (now - last_change) < stable_required:
                    await asyncio.sleep(0.2)
                    continue
                if (now - done_candidate_at) < stable_required:
                    await asyncio.sleep(0.2)
                    continue
                await asyncio.sleep(0.8)
                verify_text = await self._extract_response_text(container)
                verify_generating = await self._is_response_generating(container)
                verify_thinking = await self._is_thinking_active(container)
                verify_web_search = await self._is_web_search_active(container)
                if verify_text != last_text:
                    last_text = verify_text
                    message.response_text = verify_text
                    message.response_chars = len(verify_text)
                    last_change = time.monotonic()
                    done_candidate_at = None
                    continue
                if verify_generating or verify_thinking or verify_web_search:
                    done_candidate_at = None
                    continue
                break
            if (
                not generating
                and not thinking_active
                and not web_search_active
                and message.response_chars == 0
                and seen_busy_signal
                and elapsed > 20.0
                and stalled_for > 14.0
                and idle_after_busy > 8.0
            ):
                await asyncio.sleep(1.0)
                retry_text = await self._extract_response_text(container)
                if retry_text != last_text:
                    last_text = retry_text
                    message.response_text = retry_text
                    message.response_chars = len(retry_text)
                    last_change = time.monotonic()
                    if message.response_chars > 0:
                        self._log.info(
                            "Empty-response check recovered: text appeared after grace wait."
                        )
                    continue
                retry_generating = await self._is_response_generating(container)
                retry_thinking = await self._is_thinking_active(container)
                retry_web_search = await self._is_web_search_active(container)
                if retry_generating or retry_thinking or retry_web_search:
                    self._log.info(
                        "Empty-response check recovered: response activity resumed."
                    )
                    continue
                message.error = "Assistant returned an empty response."
                break
            else:
                done_candidate_at = None

            if elapsed > self._GEN_TOTAL_TIMEOUT_SECONDS:
                message.error = "Generation timeout exceeded."
                break

            if (
                not thinking_active
                and not web_search_active
                and stalled_for > self._GEN_STALL_SECONDS
                and (generating or message.response_chars == 0)
            ):
                if message.refreshed_count >= self._GEN_MAX_REFRESHES:
                    message.error = (
                        "Generation seems stalled; max recovery attempts exceeded."
                    )
                    break
                message.refreshed_count += 1
                prefix = (message.response_text or "")[:80]
                self._log.warn(
                    "Generation stalled (no new chars). "
                    f"Refreshing page (attempt {message.refreshed_count}/{self._GEN_MAX_REFRESHES})"
                )
                await self._reload_chat_page()
                await self._ensure_chat_open(chat)
                container = await self._recover_response_container(prefix=prefix)
                last_change = time.monotonic()

            await asyncio.sleep(self._GEN_POLL_SECONDS)

        message.generation_finished_at = datetime.now()
        if message.generation_started_at is not None:
            message.generation_seconds = (
                message.generation_finished_at - message.generation_started_at
            ).total_seconds()
        message.assistant_message_dom_id = await self._get_last_assistant_message_dom_id()
        return message

    async def _extract_thinking_text(self, container: Locator) -> str:
        """Extract the latest visible text from the deep-think status area."""
        chain = container.locator(S.THINKING_CONTAINER).first
        if await chain.count() == 0:
            return ""
        try:
            if not await chain.is_visible():
                return ""
        except Exception:
            return ""

        # Active state uses shimmer text (e.g. "Thinking...").
        specific = chain.locator(S.THINKING_SHIMMER).first
        if await specific.count() > 0:
            try:
                if not await specific.is_visible():
                    return ""
            except Exception:
                return ""
            text = await self._safe_locator_inner_text(specific, timeout_ms=450)
            if text:
                return re.sub(r"\s+", " ", text)
        # Fallback: shimmer may be re-rendered; inspect any visible span in thinking container.
        fallback = chain.locator("span").first
        if await fallback.count() > 0:
            try:
                if await fallback.is_visible():
                    text = await self._safe_locator_inner_text(fallback, timeout_ms=350)
                    if text:
                        return re.sub(r"\s+", " ", text)
            except Exception:
                return ""
        return ""

    async def _is_thinking_active(self, container: Locator) -> bool:
        """Detect active deep-think phase from shimmer/skip UI markers."""
        chain = container.locator(S.THINKING_CONTAINER).first
        if await chain.count() == 0 or not await chain.is_visible():
            return False

        # While deep-think is running, UI typically shows shimmer status and Skip button.
        shimmer = chain.locator(S.THINKING_SHIMMER).first
        if await shimmer.count() > 0 and await shimmer.is_visible():
            return True

        skip_button = chain.get_by_role("button", name=re.compile(r"^\s*Skip\s*$", re.I)).first
        if await skip_button.count() > 0 and await skip_button.is_visible():
            return True

        return False

    async def _extract_web_search_text(self, container: Locator) -> str:
        """Extract visible web-search status text from the response block."""
        label = container.locator(S.THINKING_SHIMMER, has_text=re.compile("Searching the web", re.I)).first
        if await label.count() == 0:
            return ""
        try:
            if not await label.is_visible():
                return ""
        except Exception:
            return ""
        text = await self._safe_locator_inner_text(label, timeout_ms=450)
        if not text:
            return ""
        return re.sub(r"\s+", " ", text)

    async def _safe_locator_inner_text(self, locator: Locator, timeout_ms: int = 500) -> str:
        """Read locator inner text safely, returning empty string on transient failures."""
        try:
            return (await locator.inner_text(timeout=timeout_ms) or "").strip()
        except Exception:
            return ""

    async def _is_web_search_active(self, container: Locator) -> bool:
        """Detect active web-search phase from response status label."""
        label = container.locator(S.THINKING_SHIMMER, has_text=re.compile("Searching the web", re.I)).first
        return await label.count() > 0 and await label.is_visible()

    async def _recover_response_container(self, prefix: str) -> Locator:
        """Recover the most likely response container after reload/navigation."""
        containers = self._response_containers()
        await containers.first.wait_for(state="visible", timeout=self.config.timeout_ms)
        count = await containers.count()
        if count == 0:
            raise ChatNavigationError("No assistant response container after page reload.")

        if prefix:
            for i in range(count - 1, -1, -1):
                candidate = containers.nth(i)
                text = await self._extract_response_text(candidate)
                if text.startswith(prefix) or prefix in text[: max(120, len(prefix) + 30)]:
                    return candidate
        return containers.nth(count - 1)

    async def _reload_chat_page(self) -> None:
        """Reload current page with fallback navigation when reload is unstable."""
        current_url = self.page.url
        try:
            await self.page.reload(
                wait_until="domcontentloaded",
                timeout=min(20_000, self.config.timeout_ms),
            )
            try:
                await self.page.wait_for_load_state("load", timeout=min(6_000, self.config.timeout_ms))
            except Exception:
                # Dynamic pages may keep loading forever; domcontentloaded is enough here.
                pass
        except Exception as exc:
            self._log.info(
                "Reload did not complete normally. "
                f"Falling back to direct navigation ({exc.__class__.__name__})"
            )
            await self._navigate_with_retries(current_url)
        self._log.info(f"Page refreshed: {self.page.url}")

    async def _get_last_assistant_message_dom_id(self) -> str | None:
        """Return DOM id of the last assistant message wrapper, if present."""
        wrappers = self.page.locator("div[id^='message-']").filter(
            has=self.page.locator(".chat-assistant")
        )
        count = await wrappers.count()
        if count == 0:
            return None
        return await wrappers.nth(count - 1).get_attribute("id")

    async def _wait_send_button_ready(
        self,
        send_button: Locator,
        timeout_ms: int = 8_000,
    ) -> None:
        """Wait until send button becomes enabled and not marked disabled."""
        await send_button.wait_for(state="visible", timeout=timeout_ms)
        deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
        while True:
            disabled_attr = await send_button.get_attribute("disabled")
            class_attr = (await send_button.get_attribute("class") or "").lower()
            if (
                await send_button.is_enabled()
                and disabled_attr is None
                and "disabled" not in class_attr
            ):
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise MessageSendBlockedError(
                    "Cannot send message: send button did not become ready."
                )
            await asyncio.sleep(0.1)

    async def _regenerate_message(self, message: ChatMessage) -> ChatMessage:
        """Trigger regeneration and collect the refreshed assistant response."""
        started_monotonic = time.monotonic()
        regenerated = ChatMessage(
            client=self,
            chat=message.chat,
            prompt_text=message.prompt_text,
            deep_think=message.deep_think,
            web_search=message.web_search,
            created_at=datetime.now(),
            generation_started_at=datetime.now(),
            assistant_message_dom_id=message.assistant_message_dom_id,
        )
        try:
            await self._ensure_chat_open(message.chat)
            button = await self._resolve_regenerate_button(message)
            await self._wait_clickable(button, "Regenerate button", timeout_ms=5_000)
            await button.scroll_into_view_if_needed()
            await button.click()
            await self._short_pause(150)
            self._log.info("Regeneration triggered. Waiting for updated response")
            target_container = await self._resolve_regeneration_container(message)
            regenerated = await self._collect_response_until_done(
                chat=message.chat,
                message=regenerated,
                container=target_container,
            )
        except Exception as exc:
            regenerated.error = str(exc)
            regenerated.generation_finished_at = datetime.now()
            regenerated.generation_seconds = (
                regenerated.generation_finished_at - regenerated.generation_started_at
            ).total_seconds()
            self._log.error(f"Regeneration tracking failed: {exc}")
        total_elapsed = time.monotonic() - started_monotonic
        if regenerated.generation_seconds is None or total_elapsed > regenerated.generation_seconds:
            regenerated.generation_seconds = total_elapsed
        if regenerated.ok:
            self._log.ok(
                f"Regeneration complete: {regenerated.response_chars} chars "
                f"in {regenerated.generation_seconds:.1f}s"
            )
        else:
            self._log.warn(f"Regeneration finished with warning: {regenerated.error}")
        return regenerated

    async def _resolve_regenerate_button(self, message: ChatMessage) -> Locator:
        """Locate regenerate button for the target message, with safe fallback."""
        if message.assistant_message_dom_id:
            wrapper = self.page.locator(f"#{message.assistant_message_dom_id}").first
            if await wrapper.count() > 0:
                button = wrapper.locator(S.REGENERATE_BUTTON).first
                if await button.count() > 0:
                    return button
        button = self.page.locator(S.REGENERATE_BUTTON).last
        if await button.count() == 0:
            raise ChatNavigationError("Regenerate button not found.")
        return button

    async def _resolve_regeneration_container(self, message: ChatMessage) -> Locator:
        """Resolve response container to monitor after regeneration click."""
        if message.assistant_message_dom_id:
            wrapper = self.page.locator(f"#{message.assistant_message_dom_id}").first
            if await wrapper.count() > 0:
                container = wrapper.locator("#response-content-container").last
                if await container.count() > 0:
                    return container
        containers = self._response_containers()
        count = await containers.count()
        if count == 0:
            raise ChatNavigationError("Response container not found for regeneration.")
        return containers.nth(count - 1)

    async def _click_new_chat_button(self) -> None:
        """Open new-chat composer with retries and popup/sidebar recovery."""
        await self._dismiss_startup_popup_silent()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(8.0, self.config.timeout_ms / 1000)
        next_popup_check = 0.0

        while True:
            now = loop.time()
            if now >= next_popup_check:
                await self._dismiss_startup_popup_silent()
                next_popup_check = now + 0.6

            try:
                await self._ensure_sidebar_open()
            except Exception as exc:
                if now >= deadline:
                    raise ChatNavigationError("Failed to open sidebar while creating a new chat.") from exc
                await asyncio.sleep(0.2)
                continue

            button = self.page.locator(S.NEW_CHAT_BUTTON_ID).first
            if await button.count() == 0:
                button = self.page.get_by_role("button", name=S.NEW_CHAT_BUTTON_NAME).first

            if await button.count() > 0 and await button.is_visible():
                try:
                    await self._wait_clickable(button, "New Chat button")
                    await button.scroll_into_view_if_needed()
                    await button.click()
                    await self._short_pause()
                    await self.page.wait_for_load_state("domcontentloaded")
                    await self._wait_composer_ready(timeout_ms=self.config.timeout_ms)
                    return
                except Exception as exc:
                    await self._dismiss_startup_popup_silent()
                    if now >= deadline:
                        raise ChatNavigationError(
                            "Failed to open new chat composer after retries."
                        ) from exc
                    await asyncio.sleep(0.2)
                    continue

            if now >= deadline:
                raise ChatNavigationError("New Chat button not found.")

            await asyncio.sleep(0.2)

    async def _ensure_chat_mode(self, mode: str = "chat", strict: bool = False) -> None:
        """Ensure chat mode is active; raise on unsupported modes."""
        normalized = mode.strip().lower()
        if normalized != "chat":
            raise UnsupportedChatModeError("Currently only 'chat' mode is supported.")
        chat_tab = self.page.locator(S.CHAT_MODE_TAB).first
        if await chat_tab.count() == 0:
            self._log.warn("Chat mode tab not found. Skipping mode check")
            return
        await self._wait_clickable(chat_tab, "Chat mode tab")
        state = await chat_tab.get_attribute("data-state")
        if state != "active":
            await chat_tab.click()
            await self._short_pause()
            state = await self._wait_for_attribute(
                chat_tab, name="data-state", expected="active", timeout_ms=3_000
            )
        if state != "active":
            if strict:
                raise ChatNavigationError("Failed to switch mode to 'Chat'.")
            self._log.warn("Could not confirm Chat mode state. Continuing")

    async def _select_model(self, model: str) -> None:
        """Select an exact model name from selector, including hidden list."""
        desired = model.strip()
        if not desired:
            raise ValueError("Model value must not be empty.")
        current = await self._get_current_model_label()
        current_name = current.splitlines()[0].strip() if current else ""
        if current_name == desired:
            self._log.info(f"Model already selected: {desired}")
            return

        selector_button = self.page.locator(S.MODEL_SELECTOR_BUTTON_FALLBACK).first
        if await selector_button.count() == 0:
            raise ChatNavigationError("Model selector button not found.")
        await self._wait_clickable(selector_button, "Model selector button")
        await selector_button.scroll_into_view_if_needed()
        await selector_button.click()
        await self._short_pause()

        menu = self.page.locator("div[role='menu'][data-melt-dropdown-menu]").last
        await menu.wait_for(state="visible", timeout=self.config.timeout_ms)

        candidate = await self._find_model_button(menu, desired)
        if candidate is None:
            more_button = menu.get_by_role("button", name=re.compile("More models", re.I)).first
            if await more_button.count() > 0:
                self._log.info("Model not in primary list. Expanding 'More models'")
                await more_button.scroll_into_view_if_needed()
                await self._wait_clickable(more_button, "More models button", timeout_ms=3_000)
                await more_button.click()
                await self._short_pause(220)
            candidate = await self._find_model_button(menu, desired)
        if candidate is None:
            # Fallback: scroll model list and retry once more.
            scroll_box = menu.locator("div.overflow-y-scroll").first
            if await scroll_box.count() > 0:
                await scroll_box.evaluate("el => { el.scrollTop = el.scrollHeight; }")
                await self._short_pause(180)
            candidate = await self._find_model_button(menu, desired)
        if candidate is None:
            raise ChatNavigationError(f"Model '{model}' not found in selector.")

        await candidate.scroll_into_view_if_needed()
        await self._wait_clickable(candidate, f"Model item '{model}'")
        await candidate.click()
        await self._short_pause(220)
        selected = await self._get_current_model_label()
        if selected is None:
            raise ChatNavigationError("Failed to read selected model after change.")

        selected_name = selected.splitlines()[0].strip() if selected else ""
        if selected_name != desired:
            self._log.error(
                f"Model select verification failed. Expected '{model}', got '{selected_name or selected}'"
            )
            raise ChatNavigationError(
                f"Model verification failed. Expected '{model}', got '{selected_name or selected}'."
            )
        self._log.ok(f"Model selected: {selected_name}")

    async def _find_model_button(self, menu: Locator, model: str) -> Locator | None:
        """Find exact model item by `data-value` or first visible text line."""
        target = model.strip()
        items = menu.locator(S.MODEL_ITEM_BUTTON)
        count = await items.count()
        for i in range(count):
            item = items.nth(i)
            if not await item.is_visible():
                continue
            value = (await item.get_attribute("data-value") or "").strip()
            text = (await item.inner_text() or "").strip()
            # First non-empty line is the visible model name (e.g., "GLM-4.6").
            name_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
            if target == value or target == name_line:
                return item
        return None

    async def _get_current_model_label(self) -> str | None:
        """Read currently selected model label from selector button."""
        selector_button = self.page.locator(S.MODEL_SELECTOR_BUTTON).first
        if await selector_button.count() == 0:
            selector_button = self.page.locator(S.MODEL_SELECTOR_BUTTON_FALLBACK).first
        if await selector_button.count() == 0:
            return None
        raw = (await selector_button.inner_text() or "")
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if not lines:
            return None
        name = lines[0]
        name = re.sub(r"[▾▼]\s*$", "", name).strip()
        return name or None

    async def _wait_clickable(
        self,
        locator: Locator,
        element_name: str,
        timeout_ms: int | None = None,
    ) -> None:
        """Wait until an element is visible, enabled, and not disabled by attrs."""
        timeout = timeout_ms or self.config.timeout_ms
        await locator.wait_for(state="visible", timeout=timeout)
        deadline = asyncio.get_running_loop().time() + (timeout / 1000)
        while True:
            if await locator.is_enabled():
                aria_disabled = (await locator.get_attribute("aria-disabled") or "").lower()
                data_disabled = await locator.get_attribute("data-disabled")
                if aria_disabled != "true" and data_disabled is None:
                    return
            if asyncio.get_running_loop().time() >= deadline:
                raise ChatNavigationError(f"{element_name} is visible but not clickable.")
            await asyncio.sleep(0.08)

    async def _wait_for_attribute(
        self,
        locator: Locator,
        name: str,
        expected: str,
        timeout_ms: int = 3_000,
    ) -> str | None:
        """Poll for attribute value until expected state or timeout."""
        deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
        while True:
            value = await locator.get_attribute(name)
            if value == expected:
                return value
            if asyncio.get_running_loop().time() >= deadline:
                return value
            await asyncio.sleep(0.08)

    async def _short_pause(self, ms: int = 140) -> None:
        """Sleep helper for short UI pacing pauses."""
        await asyncio.sleep(ms / 1000)

    async def _before_chat_action(
        self,
        chat: ChatSession,
        action_name: str,
        pace: bool = False,
    ) -> None:
        """Apply optional human-like pacing before chat-scoped action execution."""
        if not pace or not self.config.humanize_actions:
            return

        low = float(self.config.min_action_delay_s)
        high = float(self.config.max_action_delay_s)
        if high < low:
            low, high = high, low
        low = max(0.0, low)
        high = max(0.0, high)
        if high == 0.0:
            return

        target_delay = random.uniform(low, high)
        now = time.monotonic()
        if chat.last_action_monotonic is None:
            sleep_for = random.uniform(min(0.08, high), min(0.22, high)) if high > 0 else 0.0
        else:
            elapsed = now - chat.last_action_monotonic
            sleep_for = max(0.0, target_delay - elapsed)

        if sleep_for > 0:
            self._log.info(
                f"Pacing before '{action_name}': {sleep_for:.2f}s"
            )
            await asyncio.sleep(sleep_for)

    def _after_chat_action(self, chat: ChatSession, action_name: str) -> None:
        """Record timing metadata after a chat-scoped action is executed."""
        chat.last_action_monotonic = time.monotonic()
        chat.last_action_at = datetime.now()
        chat.last_action_name = action_name

    async def _get_deep_think(self) -> bool:
        """Return current deep-think toggle state from composer controls."""
        await self._dismiss_startup_popup_silent()
        button = self.page.locator(S.DEEP_THINK_BUTTON).first
        if await button.count() == 0:
            raise ChatNavigationError("Deep think toggle button not found.")
        state = await button.get_attribute("data-autothink")
        return (state or "").lower() == "true"

    async def _set_deep_think(self, enabled: bool) -> bool:
        """Set deep-think toggle and verify final state."""
        await self._dismiss_startup_popup_silent()
        button = self.page.locator(S.DEEP_THINK_BUTTON).first
        if await button.count() == 0:
            raise ChatNavigationError("Deep think toggle button not found.")
        await self._wait_clickable(button, "Deep think toggle", timeout_ms=4_000)
        current = await self._get_deep_think()
        if current == enabled:
            self._log.ok(f"Deep think set to: {enabled} (already)")
            return current
        await button.click()
        for _ in range(20):
            if await self._get_deep_think() == enabled:
                self._log.ok(f"Deep think set to: {enabled}")
                return enabled
            await asyncio.sleep(0.1)
        raise ChatNavigationError("Failed to switch Deep think state.")

    async def _resolve_web_search_toggle(self) -> Locator:
        """Resolve the effective web-search toggle button in current layout."""
        # Primary strategy: same controls row as Deep think button.
        deep_think_button = self.page.locator(S.DEEP_THINK_BUTTON).first
        if await deep_think_button.count() > 0 and await deep_think_button.is_visible():
            controls_row = deep_think_button.locator("xpath=ancestor::div[contains(@class,'items-center')][1]")
            row_candidates = controls_row.locator("button").filter(
                has=self.page.locator(S.WEB_SEARCH_SVG_PATH_PREFIX)
            )
            row_count = await row_candidates.count()
            for i in range(row_count):
                btn = row_candidates.nth(i)
                if not await btn.is_visible() or not await btn.is_enabled():
                    continue
                if await btn.locator("button").count() > 0:
                    continue
                if await btn.get_attribute("data-autothink") is not None:
                    continue
                if (await btn.get_attribute("type") or "").lower() != "button":
                    continue
                return btn

        form = self.page.locator(f"form:has({S.CHAT_INPUT_TEXTAREA})").first
        if await form.count() == 0:
            form = self.page.locator("form").first
        if await form.count() == 0:
            raise ChatNavigationError("Message form not found for web search toggle.")

        candidates = form.locator("button").filter(
            has=self.page.locator(S.WEB_SEARCH_SVG_PATH_PREFIX)
        )
        count = await candidates.count()
        if count == 0:
            raise ChatNavigationError("Web search toggle button not found.")

        for i in range(count):
            btn = candidates.nth(i)
            if not await btn.is_visible() or not await btn.is_enabled():
                continue
            nested_buttons = btn.locator("button")
            if await nested_buttons.count() > 0:
                continue
            if await btn.get_attribute("data-tooltip-trigger") is not None:
                continue
            if await btn.get_attribute("data-melt-tooltip-trigger") is not None:
                continue
            if await btn.get_attribute("id") is not None:
                continue
            if await btn.get_attribute("aria-describedby") is not None:
                continue
            if (await btn.get_attribute("type") or "").lower() != "button":
                continue
            class_attr = (await btn.get_attribute("class") or "").lower()
            if "transition-colors" not in class_attr and "bg-transparent" not in class_attr:
                continue
            if "sendmessagebutton" in class_attr:
                continue
            if "upload-file-button" in class_attr:
                continue
            if await btn.is_visible() and await btn.is_enabled():
                return btn
        raise ChatNavigationError("Visible web search toggle button not found.")

    async def _get_web_search(self) -> bool:
        """Return current web-search toggle state based on active CSS state."""
        await self._dismiss_startup_popup_silent()
        button = await self._resolve_web_search_toggle()
        class_attr = (await button.get_attribute("class") or "").lower()
        return (
            ("bg-black/6" in class_attr or "dark:bg-white/10" in class_attr)
            and "bg-transparent" not in class_attr
        )

    async def _set_web_search(self, enabled: bool) -> bool:
        """Set web-search toggle state and verify result."""
        await self._dismiss_startup_popup_silent()
        button = await self._resolve_web_search_toggle()
        await self._wait_clickable(button, "Web search toggle", timeout_ms=4_000)
        current = await self._get_web_search()
        if current == enabled:
            self._log.ok(f"Web search set to: {enabled} (already)")
            return current
        await button.click()
        for _ in range(20):
            if await self._get_web_search() == enabled:
                self._log.ok(f"Web search set to: {enabled}")
                return enabled
            await asyncio.sleep(0.1)
        raise ChatNavigationError("Failed to switch Web search state.")

    async def regenerate_last_response(self) -> ChatMessage:
        """Regenerate the latest assistant response in the current chat."""
        chat_id = extract_chat_id(self.page.url)
        if not chat_id:
            raise ChatNavigationError("Current page is not a chat. Cannot regenerate response.")
        chat = ChatSession(client=self, url=self.page.url, chat_id=chat_id)
        message = ChatMessage(
            client=self,
            chat=chat,
            prompt_text="",
            deep_think=None,
            web_search=None,
            created_at=datetime.now(),
            assistant_message_dom_id=await self._get_last_assistant_message_dom_id(),
        )
        return await self._regenerate_message(message)

    async def delete_chat(self, chat_ref: str | ChatSession | None = None) -> bool:
        """Delete chat by session object, id/url, or current page chat."""
        await self._dismiss_startup_popup_silent()
        target_chat: ChatSession | None = None
        if isinstance(chat_ref, ChatSession):
            target_chat = chat_ref
            await self._ensure_chat_open(target_chat)
        elif isinstance(chat_ref, str):
            target_chat = await self.open_chat(chat_ref)
        elif chat_ref is None:
            current_id = extract_chat_id(self.page.url)
            if not current_id:
                raise ChatNavigationError("Current page is not a chat. Provide chat id/url to delete.")
            target_chat = ChatSession(client=self, url=self.page.url, chat_id=current_id)
        else:
            raise TypeError("chat_ref must be ChatSession, str, or None.")

        target_id = target_chat.chat_id or extract_chat_id(target_chat.url)
        if not target_id:
            self._log.warn(
                "Deleting chat without URL id (new chat before first message). "
                "Will confirm deletion without id verification."
            )

        self._log.warn(f"Deleting chat: {target_id or 'no-id'}")
        await self._dismiss_startup_popup_silent(chat=target_chat)

        last_exc: Exception | None = None
        for attempt in range(1, 3):
            try:
                await self._open_current_chat_menu()

                menu = self.page.locator("div[role='menu'][data-melt-dropdown-menu]").last
                await menu.wait_for(state="visible", timeout=self.config.timeout_ms)
                delete_item = menu.locator("div[role='menuitem']").filter(
                    has_text=re.compile(r"^\s*Delete\s*$", re.I)
                ).first
                if await delete_item.count() == 0:
                    # Fallback for non-strict spacing around icon/text.
                    delete_item = menu.locator("div[role='menuitem']").filter(
                        has_text=re.compile("Delete", re.I)
                    ).first
                if await delete_item.count() == 0:
                    raise ChatNavigationError("Delete action not found in chat menu.")
                await self._wait_clickable(delete_item, "Delete chat action", timeout_ms=4_000)
                await delete_item.click()
                await self._short_pause(160)

                confirm_dialog = self.page.locator("div").filter(
                    has_text=re.compile(r"Delete chat\?", re.I)
                ).last
                await confirm_dialog.wait_for(state="visible", timeout=self.config.timeout_ms)
                confirm_button = self.page.get_by_role(
                    "button", name=re.compile(r"^\s*Confirm\s*$", re.I)
                ).last
                await self._wait_clickable(
                    confirm_button, "Delete confirmation button", timeout_ms=4_000
                )
                await confirm_button.click()
                await self._short_pause(260)
                last_exc = None
                break
            except ChatNavigationError as exc:
                last_exc = exc
                if (
                    "Visible chat menu button not found in sidebar." in str(exc)
                    and attempt < 2
                ):
                    self._log.warn(
                        "Delete retry: chat menu not ready yet. "
                        f"Retrying in 2s (attempt {attempt + 1}/2)"
                    )
                    await asyncio.sleep(2.0)
                    continue
                raise

        if last_exc is not None:
            raise last_exc

        if target_id:
            deleted = await self._wait_chat_deleted(target_id=target_id, timeout_ms=12_000)
        else:
            deleted = await self._wait_delete_modal_closed(timeout_ms=6_000)
        if deleted:
            self._log.ok(f"Chat deleted: {target_id or 'no-id'}")
        else:
            self._log.warn(f"Delete flow finished but chat may still exist: {target_id or 'no-id'}")
        return deleted

    async def _open_current_chat_menu(self) -> None:
        """Open contextual menu for the currently selected chat in sidebar."""
        await self._dismiss_startup_popup_silent()
        await self._ensure_sidebar_open()
        menus = self.page.locator(S.CHAT_MENU_BUTTON)
        visible_menu = await self._first_visible(menus)
        if visible_menu is None:
            raise ChatNavigationError("Visible chat menu button not found in sidebar.")
        await self._wait_clickable(visible_menu, "Chat menu button", timeout_ms=4_000)
        await visible_menu.scroll_into_view_if_needed()
        await visible_menu.click()
        await self._short_pause(140)

    async def _wait_chat_deleted(self, target_id: str, timeout_ms: int = 12_000) -> bool:
        """Wait until current URL no longer points to the deleted chat id."""
        deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
        while True:
            current_id = extract_chat_id(self.page.url)
            if current_id != target_id:
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.2)

    async def _wait_delete_modal_closed(self, timeout_ms: int = 6_000) -> bool:
        """Wait until delete confirmation modal disappears."""
        deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
        while True:
            confirm_button = self.page.get_by_role("button", name=re.compile(r"^\s*Confirm\s*$", re.I)).last
            if await confirm_button.count() == 0:
                return True
            if not await confirm_button.is_visible():
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.2)

    async def _first_visible(self, locator: Locator) -> Locator | None:
        """Return first visible element from a locator collection."""
        count = await locator.count()
        for i in range(count):
            item = locator.nth(i)
            if await item.is_visible():
                return item
        return None

    async def _ensure_sidebar_open(self) -> None:
        """Open sidebar if it is currently collapsed."""
        # Closed sidebar has explicit toggle button with this id.
        closed_toggle = self.page.locator(S.SIDEBAR_TOGGLE_BUTTON).first
        if await closed_toggle.count() > 0 and await closed_toggle.is_visible():
            await self._wait_clickable(closed_toggle, "Sidebar toggle button", timeout_ms=4_000)
            await closed_toggle.click()
            await self._short_pause(220)
            # wait until closed toggle disappears
            for _ in range(20):
                if await closed_toggle.count() == 0:
                    return
                if not await closed_toggle.is_visible():
                    return
                await asyncio.sleep(0.1)

    async def _dismiss_startup_popup_silent(
        self,
        chat: ChatSession | None = None,
        allow_chat_restore: bool = True,
    ) -> bool:
        """Best-effort popup dismissal wrapper that never raises."""
        try:
            return await self._dismiss_startup_popup(
                chat=chat,
                allow_chat_restore=allow_chat_restore,
            )
        except Exception:
            # Non-critical helper; ignore transient failures.
            return False
