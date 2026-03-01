# Contributing

Thanks for your interest in contributing.

## Development Setup

1. Create and activate a virtual environment.
2. Install in editable mode:
   ```bash
   pip install -e .
   ```
3. Install browser binaries:
   ```bash
   playwright install
   ```

## Guidelines

- Keep public API backward-compatible when possible.
- Prefer explicit waits over blind sleeps for UI synchronization.
- Keep selectors centralized in `zai_chat_client/selectors.py`.
- Use package exceptions from `zai_chat_client/exceptions.py` for predictable error handling.
- Keep logs concise and actionable.

## Pull Request Checklist

- Code compiles and runs for the intended flow.
- Public behavior changes are reflected in `README.md`.
- Changelog entry is updated when needed.

