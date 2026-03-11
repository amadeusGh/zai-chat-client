# zai-chat-client

`zai-chat-client` is an unofficial async Python client for working with the web version of [https://chat.z.ai](https://chat.z.ai).

It drives a real browser via Playwright and lets you use chat.z.ai as a programmable tool: you can log in, create/open/delete chats, select a model, toggle options like Deep Think and Web Search, send prompts, wait until the answer is fully generated, and regenerate responses when needed. It’s especially handy when you want a free model for lightweight tasks, small automations, experiments, or scripts.

---

## What this library can do

It starts a browser session, ensures you’re authorized, and then gives you a clean async API to control chat.z.ai like a normal user would. In practice you’ll use it to bootstrap login (session, cookies.txt, or manual login), manage chats (create/open/delete), choose models, flip Deep Think/Web Search on or off, send messages with reliable “wait until done” behavior, and regenerate the last assistant message.

---

## Requirements

You’ll need Python 3.11+ and Playwright browser binaries installed.

---

## Installation

First, install the package and then install Playwright browsers.

Minimal install (pure Playwright):

```bash
python -m pip install -e .
python -m playwright install
```

Recommended install (with Camoufox):

```bash
python -m pip install -e .[camoufox]
python -m playwright install
```

---

## Camoufox (anti-bot friendly mode)

By default, this library uses Camoufox: [https://github.com/daijro/camoufox](https://github.com/daijro/camoufox)

Camoufox tweaks browser fingerprints and reduces common automation signals. In practice, this helps when a website behaves differently under default Playwright automation or applies anti-bot checks. If you prefer pure Playwright behavior, disable it like this:

```python
from zai_chat_client import ZaiClient

client = ZaiClient(
    session="main",
    use_camoufox=False,
)
```

---

## Quick Start

This example creates a new chat, sends a message, waits for the full response, prints it, and closes the browser.

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

        msg = await chat.send_message("Hello! Reply in one short sentence.")
        print(msg.response_text)

    finally:
        await client.close()


asyncio.run(main())
```

If this is your first run and you already have cookies exported, `cookies_path="cookies.txt"` is usually enough. After a successful start, the library will keep using the saved session and you won’t need cookies again.

---

## Authentication (how it works)

The library resolves authorization in this order.

First it tries a saved session (the `session` argument). A session is a stored browser state from a previous successful login. When it’s valid, startup is fast and fully automatic.

If no valid session is available, it tries cookies from `cookies_path`. The cookie file must be in Netscape format (classic `cookies.txt`). If the cookies authorize successfully, the library saves a session internally, so on the next runs cookies are not needed anymore.

If both session and cookies fail, you can enable manual login with `allow_manual_login=True`. This requires `headless=False`. The browser window opens, you log in normally, then confirm in the terminal, and the session is saved for future runs.

Important practical note: cookies are usually only needed once (first successful run). After that, the session file is enough.

---

## Exporting cookies.txt (Netscape format)

If you want to bootstrap login from your own browser cookies, do this:

Open chat.z.ai in your normal browser and log in.

Install a cookies exporter extension that can output Netscape cookies.txt format. Common options are “Get cookies.txt LOCALLY” for Chrome, or a “cookies.txt” exporter extension for Firefox.

Export cookies for the `chat.z.ai` domain and save them as `cookies.txt`.

Then pass that file to the client:

```python
from zai_chat_client import ZaiClient

client = ZaiClient(
    session="main",
    cookies_path="cookies.txt",
    headless=True,
)
```

If the file is not in Netscape format, the library will raise `CookieFormatError`. If the format is fine but cookies are expired/invalid, authorization will fail and you’ll need fresh cookies or manual login.

---

## Common usage examples

### 1) Session-only startup (typical daily use)

After you have a working session saved, you can start without cookies:

```python
client = ZaiClient(
    session="main",
    headless=True,
)
await client.start()
```

---

### 2) Bootstrap from cookies (first run)

```python
client = ZaiClient(
    session="main",
    cookies_path="cookies.txt",
    headless=True,
)
await client.start()
```

---

### 3) Manual login fallback

Use this when cookies aren’t available or don’t work.

```python
client = ZaiClient(
    session="main_manual",
    allow_manual_login=True,
    headless=False,
)
await client.start()
```

---

### 4) Create a chat and pick model/options

```python
chat = await client.create_chat(
    model="GLM-5",
    mode="chat",
    deep_think=False,
    web_search=False,
)
```

---

### 5) Open an existing chat by id or URL

```python
chat = await client.open_chat("c6400a96-8aa9-4e1b-817c-5ba9419cbfbd")
# or:
chat = await client.open_chat("https://chat.z.ai/c/c6400a96-8aa9-4e1b-817c-5ba9419cbfbd")
```

---

### 6) Send a message and override options per call

```python
msg = await chat.send_message(
    "Summarize this in 3 bullet points.",
    web_search=True,
    deep_think=False,
)

print(msg.ok, msg.response_chars)
print(msg.response_text)
```

---

### 7) Regenerate the last assistant response

```python
msg2 = await chat.regenerate_last_response()
print(msg2.response_text)
```

You can also regenerate directly from the message object:

```python
msg2 = await msg.regenerate()
```

---

### 8) Delete a chat

Delete by chat object:

```python
ok = await chat.delete()
print(ok)
```

Or delete by id/URL using the client:

```python
ok = await client.delete_chat("c6400a96-8aa9-4e1b-817c-5ba9419cbfbd")
print(ok)
```

---

## API Overview

### `ZaiClient`

`ZaiClient` is the main entry point of the library.
It manages the browser instance, authentication, navigation, and chat-level operations.

You usually create one client, start it once, perform your automation tasks, and then close it.

---

### Constructor

```python
client = ZaiClient(...)
```

| Argument                           | Type                | Description                                                                                                          |
| ---------------------------------- | ------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `base_url`                         | `str`               | Base URL of the chat service. Default: `https://chat.z.ai`. Normally you don't need to change it.                  |
| `headless`                         | `bool`              | Whether the browser runs without a visible window. Set `False` if you want to see the browser or use manual login. |
| `use_camoufox`                     | `bool`              | Enables Camoufox browser fingerprint modifications to reduce automation detection. Enabled by default.              |
| `window_width`                     | `int \| None`       | Optional browser window width. Useful when running non-headless sessions.                                           |
| `window_height`                    | `int \| None`       | Optional browser window height.                                                                                      |
| `session`                          | `str \| Path \| None` | Name or path for the stored browser session. If valid, the client restores login automatically.                   |
| `cookies_path`                     | `str \| Path \| None` | Path to a Netscape-format `cookies.txt` file used to bootstrap login. Usually needed only for the first run.     |
| `allow_manual_login`               | `bool`              | Enables manual login fallback if session and cookies fail. Requires `headless=False`.                               |
| `enable_logs`                      | `bool`              | Enables colored terminal logs for debugging and visibility of automation steps.                                     |
| `timeout_ms`                       | `int`               | Default timeout for Playwright operations in milliseconds.                                                          |
| `navigation_retries`               | `int`               | Number of retries when page navigation fails.                                                                       |
| `keep_browser_open_on_start_error` | `bool`              | Keeps the browser open if startup fails, useful for debugging login problems.                                       |
| `humanize_actions`                 | `bool`              | Enables small delays between actions to mimic human behavior.                                                       |
| `min_action_delay_s`               | `float`             | Minimum delay between automated actions when humanization is enabled.                                               |
| `max_action_delay_s`               | `float`             | Maximum delay between automated actions.                                                                            |

---

### `ZaiClient` methods

| Method                             | Arguments (types)                                                                 | Returns         | Description                                                                                    |
| ---------------------------------- | --------------------------------------------------------------------------------- | --------------- | ---------------------------------------------------------------------------------------------- |
| `await start()`                    | `—`                                                                               | `ZaiClient`     | Starts the browser, restores authentication state, and verifies that the account is logged in. |
| `await close()`                    | `—`                                                                               | `None`          | Gracefully closes the browser and releases Playwright resources.                               |
| `await open(url)`                  | `url: str`                                                                        | `None`          | Navigates the browser to a specific URL using retry logic.                                     |
| `await save_session()`             | `—`                                                                               | `None`          | Saves the current browser storage state to the configured session file.                        |
| `await create_chat(...)`           | `model: str \| None`, `mode: str`, `deep_think: bool \| None`, `web_search: bool \| None` | `ChatSession` | Creates a new chat and returns a `ChatSession` object (history list is initialized).            |
| `await open_chat(chat_ref)`        | `chat_ref: str`                                                                   | `ChatSession`   | Opens an existing chat using a chat id or full URL and loads message history into `chat.messages`. |
| `await send_message(...)`          | `text: str`, `deep_think: bool \| None`, `web_search: bool \| None`             | `ChatMessage`   | Sends a message in the currently opened chat page and returns a `ChatMessage`.                 |
| `await regenerate_last_response()` | `—`                                                                               | `ChatMessage`   | Regenerates the latest assistant response in the current chat.                                 |
| `await delete_chat(chat_ref)`      | `chat_ref: str \| ChatSession \| None = None`                                    | `bool`          | Deletes a chat by id, URL, `ChatSession`, or current chat context.                             |

---

### `ChatSession`

`ChatSession` represents one specific chat in chat.z.ai.

Most real interactions with the assistant happen through this object.

### `ChatSession` fields

| Field              | Type                     | Description                                                                 |
| ------------------ | ------------------------ | --------------------------------------------------------------------------- |
| `chat_id`          | `str \| None`            | Current chat id extracted from URL.                                         |
| `url`              | `str`                    | Current chat URL.                                                           |
| `messages`         | `list[ChatHistoryEntry]` | In-memory history snapshot of user/assistant messages for this chat.        |
| `last_action_at`   | `datetime \| None`       | Timestamp of the last chat action executed by automation.                   |
| `last_action_name` | `str \| None`            | Name of the last executed chat action method.                               |

---

### `ChatSession` methods

| Method                          | Arguments (types)                                             | Returns       | Description                                                                                                      |
| ------------------------------- | ------------------------------------------------------------- | ------------- | ---------------------------------------------------------------------------------------------------------------- |
| `await ensure_open()`           | `—`                                                           | `None`        | Ensures the chat is currently opened in the browser.                                                             |
| `await set_deep_think(enabled)` | `enabled: bool`                                               | `bool`        | Enables or disables the Deep Think mode.                                                                         |
| `await get_deep_think()`        | `—`                                                           | `bool`        | Returns the current Deep Think state.                                                                            |
| `await set_web_search(enabled)` | `enabled: bool`                                               | `bool`        | Enables or disables Web Search mode.                                                                             |
| `await get_web_search()`        | `—`                                                           | `bool`        | Returns the current Web Search state.                                                                            |
| `await send_message(text, ...)` | `text: str`, `deep_think: bool \| None`, `web_search: bool \| None` | `ChatMessage` | Sends a message in the chat and waits until the assistant finishes generating a response.                       |
| `await refresh_messages()`      | `—`                                                           | `list[ChatHistoryEntry]` | Re-reads the current chat DOM and refreshes `chat.messages`.                                         |
| `await delete()`                | `—`                                                           | `bool`        | Deletes this chat from the chat list.                                                                            |

`ChatSession` also tracks internal action timing fields such as `last_action_at` and `last_action_name`, which are useful for debugging or automation pacing.

---

### `ChatMessage`

`ChatMessage` represents the result of a single assistant generation.

It contains both the generated text and metadata describing how the generation happened.

---

### `ChatMessage` fields

| Field                      | Type                | Description                                                              |
| -------------------------- | ------------------- | ------------------------------------------------------------------------ |
| `prompt_text`              | `str`               | The text prompt that was sent to the assistant.                          |
| `response_text`            | `str`               | The assistant's generated response text.                                 |
| `response_chars`           | `int`               | Length of the generated response in characters.                          |
| `generation_started_at`    | `datetime \| None`  | Timestamp when generation started.                                       |
| `generation_finished_at`   | `datetime \| None`  | Timestamp when generation finished.                                      |
| `generation_seconds`       | `float \| None`     | Total generation duration in seconds.                                    |
| `assistant_message_dom_id` | `str \| None`       | Internal DOM identifier of the assistant message.                        |
| `refreshed_count`          | `int`               | Number of auto-refresh recovery attempts during stalled generation.      |
| `error`                    | `str \| None`       | Error message if generation failed.                                      |
| `ok`                       | `bool`              | Boolean indicating whether the message was generated successfully.       |

---

### `ChatMessage` methods

| Method               | Arguments (types) | Returns       | Description                                                                             |
| -------------------- | ----------------- | ------------- | --------------------------------------------------------------------------------------- |
| `await regenerate()` | `—`               | `ChatMessage` | Regenerates the assistant response for the same prompt and returns a new `ChatMessage`. |

---

### `ChatHistoryEntry`

`ChatHistoryEntry` represents one item in `chat.messages` and can be either a user or assistant message.

### `ChatHistoryEntry` fields

| Field                    | Type               | Description                                                                 |
| ------------------------ | ------------------ | --------------------------------------------------------------------------- |
| `role`                   | `str`              | Message role: `"user"` or `"assistant"`.                                    |
| `text`                   | `str`              | Message text extracted from chat UI.                                        |
| `dom_id`                 | `str \| None`      | DOM id of the message block (`message-...`) when available.                 |
| `response_chars`         | `int`              | Character count of `text`.                                                  |
| `generation_started_at`  | `datetime \| None` | Filled for tracked assistant generations when available.                     |
| `generation_finished_at` | `datetime \| None` | Filled for tracked assistant generations when available.                     |
| `generation_seconds`     | `float \| None`    | Filled for tracked assistant generations when available.                     |
| `error`                  | `str \| None`      | Error text for tracked assistant generations, if any.                        |
| `source_message`         | `ChatMessage \| None` | Linked tracked `ChatMessage` object when this history item is trackable.  |
| `is_user`                | `bool`             | Convenience flag for user entries.                                          |
| `is_assistant`           | `bool`             | Convenience flag for assistant entries.                                     |
| `can_regenerate`         | `bool`             | `True` only if this entry is assistant and linked to a tracked message.     |

### `ChatHistoryEntry` methods

| Method               | Arguments (types) | Returns       | Description                                                                                     |
| -------------------- | ----------------- | ------------- | ----------------------------------------------------------------------------------------------- |
| `await regenerate()` | `—`               | `ChatMessage` | Regenerates this assistant entry when `can_regenerate=True`; otherwise raises `RuntimeError`. |

---

## Known Constraints

This is UI automation, so it depends on website selectors and can require updates when chat.z.ai changes its UI. At the moment, only `mode="chat"` is supported in `create_chat`. File upload flows are not implemented yet.

---

## What’s being improved

Stability against future UI layout changes is always being improved. More production-ready usage recipes and stronger automated tests for session/navigation layers are planned.

Authentication features are also on the roadmap, including login using email and password, and automated account registration.

---

## License

MIT. See `LICENSE`.
