# Repository Guidelines

## Project Structure & Module Organization
This repository is a Python plugin for bridging MimicWx messages into AstrBot.

- `main.py`: plugin entrypoint and lifecycle wiring.
- `mimicwx_client.py`: MimicWx API/client communication.
- `mimicwx_platform.py`: platform adapter and send/receive integration.
- `mimicwx_message_parser.py`, `mimicwx_message_event.py`: message parsing and event models.
- `tests/`: pytest-based unit tests (adapter, client, and conversion behavior).
- `metadata.yaml`: plugin metadata used by the host platform.

Keep new modules flat at repo root unless a clear package split is needed.

## Build, Test, and Development Commands
Use Python 3.10+ and run commands from repository root.

- `pytest` — run all tests.
- `pytest tests/test_mimicwx_client.py -q` — run a focused test file.
- `pytest -k "conversion"` — run tests by keyword.

There is no dedicated build step; validation is test-driven.

## Coding Style & Naming Conventions
- Follow PEP 8 with 4-space indentation.
- Use `snake_case` for functions/variables/files and `PascalCase` for classes.
- Prefer explicit type hints for public methods and dataclass/model fields.
- Keep async boundaries clear (`async def` for I/O paths).
- Match existing logging style and avoid noisy logs in hot paths.

Example naming: `parse_group_message`, `MimicWxClient`, `test_private_chat_nickname_fallback`.

## Testing Guidelines
- Framework: `pytest`.
- Place tests under `tests/` as `test_*.py`.
- Name tests by behavior, not implementation details.
- Add/adjust tests for every bug fix or parser/platform behavior change.

Recommended flow:
1. Reproduce with a failing test.
2. Implement fix.
3. Run `pytest` before opening PR.

## Commit & Pull Request Guidelines
Git history uses concise, imperative commit messages (e.g., `Fix message sending...`).

- Commit format: `Fix/Add/Refactor <scope>: <summary>`.
- Keep one logical change per commit.
- PRs should include: purpose, key changes, test evidence (`pytest` output), and linked issue (if any).
- For message-format or adapter behavior changes, include before/after examples in PR description.

## Security & Configuration Tips
- Do not commit secrets, tokens, or real user message payloads.
- Use sanitized fixtures in tests.
- Keep configuration in `metadata.yaml` and documented README sections; avoid hardcoded environment-specific values.
