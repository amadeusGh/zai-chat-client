# zai-chat-client

`zai-chat-client` is a Python async library for working with the web version of [https://chat.z.ai](https://chat.z.ai) through a real browser session.

It allows you to log in using a saved session, a Netscape-format cookie file, or manual login, then create and open chats, choose a model, toggle Deep Think and Web Search, send prompts, wait until the response is fully generated, and regenerate answers when needed.

If you want to use chat.z.ai as a programmable tool — for scripts, lightweight automation, content generation, experiments, or simply to access its free models for small tasks — this library gives you a clean and repeatable way to do it.

---

## What It Does

The client starts a Playwright browser session and verifies authorization state. It can restore a previously saved session file or bootstrap login from cookies. Manual login is supported when automatic methods are not sufficient.

Once authorized, you can create new chats or open existing ones by id or URL. The client supports selecting a model during chat creation and toggling Deep Think or Web Search either globally or per message.

Message sending is wrapped in a controlled flow that waits until generation is complete before returning the result. Each assistant response is represented as a structured object that includes the prompt text, response text, character count, generation timing, DOM identifier, regeneration count, and error state.

You can also regenerate the last assistant response and delete chats programmatically. Optional logs and configurable action delays are available for better visibility and pacing control.

---

## Installation

Requirements:

Python 3.11 or newer
Playwright browser binaries installed

Recommended installation with Camoufox:

```bash
python -m pip install -e .[camoufox]
python -m playwright install
```

Minimal installation without Camoufox:

```bash
python -m pip install -e .
python -m playwright install
```

---

## Camoufox

This library uses Camoufox by default:
[https://github.com/daijro/camoufox](https://github.com/daijro/camoufox)

Camoufox modifies browser fingerprints and reduces common automation signals. In practice, this helps automated sessions behave closer to normal user activity and can improve stability on sites with stricter bot-detection heuristics.

If you prefer pure Playwright behavior, you can disable it:

```python
client = ZaiClient(
    session="main",
    use_camoufox=False,
)
```

---

## Quick Start

```python
import asyncio
from zai_chat_client import ZaiClient


async def main() -> None:
    client = ZaiClient(
        session="main",
        cookies_path="cookies.txt",
        headless=False,
        enable_logs=True,
    )

    await client.start()

    try:
        chat = await client.create_chat(
            model="GLM-5",
            mode="chat",
            deep_think=False,
            web_search=False,
        )

        message = await chat.send_message(
            "Hello! Reply in one short sentence."
        )

        print(message.response_text)

    finally:
        await client.close()


asyncio.run(main())
```

---

## Authentication

Authentication is resolved in priority order.

If a session file is configured and valid, it is used immediately. A session file stores the browser storage state from a previous successful login and is the recommended production setup.

If no valid session is available, the client can load cookies from a Netscape HTTP Cookie File provided through `cookies_path`. If authorization succeeds, the session state is saved internally. After that, cookies are no longer required for subsequent runs.

If both methods fail and `allow_manual_login=True` is enabled, the browser opens in non-headless mode and you can log in manually. After successful login confirmation, the session is saved and reused in future runs. Manual login requires `headless=False`; otherwise a `ManualLoginError` is raised.

---

## Exporting Cookies (Netscape Format)

The client expects cookies in the classic Netscape HTTP Cookie File format.

To export cookies from your browser:

1. Install a browser extension such as:

   * “Get cookies.txt LOCALLY” for Chrome
   * A “cookies.txt” export extension for Firefox

2. Log in to [https://chat.z.ai](https://chat.z.ai) in your regular browser.

3. Use the extension to export cookies for the `chat.z.ai` domain.

4. Save the file as `cookies.txt` and pass its path to the client:

```python
client = ZaiClient(
    session="main",
    cookies_path="cookies.txt",
)
```

The file must contain cookies for the correct domain and follow the Netscape format. If the format is invalid, `CookieFormatError` will be raised.

After successful startup, the client saves the session internally, so cookies do not need to be reused again.

---

## API Overview

The `ZaiClient` constructor allows configuration of browser behavior, window size, session storage target, cookie path, login fallback, logging, timeout, navigation retry count, and optional humanized action delays.

The `start()` method initializes the browser and verifies authorization. The `close()` method gracefully shuts down browser resources. Additional methods allow navigation, session saving, chat creation, chat opening, message sending, regeneration of the last response, and chat deletion.

`ChatSession` represents an active chat context. It exposes methods to ensure the chat is open, toggle Deep Think and Web Search, send messages, and delete the chat. It also tracks internal timing information for action sequencing.

`ChatMessage` represents a single assistant generation result. It contains the original prompt text, assistant response text, response character count, timestamps for generation start and finish, total generation duration in seconds, DOM identifiers, regeneration count, and error state. The `ok` property indicates whether the generation completed successfully. The `regenerate()` method triggers response regeneration.

---

## Known Constraints

The library depends on website selectors and may require updates if the chat.z.ai UI changes. Currently only `mode="chat"` is supported in chat creation. File upload flows are not implemented.

---

## Roadmap

Planned improvements include stronger resilience against UI changes, expanded usage examples for production workflows, broader automated testing for session and navigation layers, and additional authentication features such as login using email and password, as well as automated account registration.

---

## License

MIT. See `LICENSE`.
