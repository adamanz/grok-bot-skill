# Changelog

## Unreleased

- Add native group creation, exact-match reuse, inspection and verified member removal.
- Support sending and reading messages in newer server-backed rooms, preserving native authors and streaming status.
- Read bounded live activity snapshots and reconcile uncertain mutations without blindly resending.
- Support active-account session storage and detect the installed desktop client version.
- Add mocked group, native-message and credential compatibility tests.

## 1.0.0 — 2026-08-17

- First public release of the Grok Bot Agent Skill
- CLI: `status`, `list`, `create`, `update`, `send`, `chat`, `transcript`
- macOS session via Grok Bot desktop app (no third-party Python deps)
