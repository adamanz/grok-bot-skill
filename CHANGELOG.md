# Changelog

## 1.0.1 - 2026-11-07

- Support raw base64 session tokens and the active account in `cursor-accounts`, while retaining legacy scoped-token support.
- Send messages through `SendGrokBotUserMessage` and support reusable message IDs for retries.
- Wait through the initial idle state of queued messages and report unconfirmed chat timeouts explicitly.
- Reject malformed credentials, ambiguous bot selectors, and invalid polling arguments before they cause unintended behavior.
- Return controlled network errors and omit server error text that could contain secrets.
- Add offline regression tests and continuous integration.

## 1.0.0 — 2026-08-17

- First public release of the Grok Bot Agent Skill
- CLI: `status`, `list`, `create`, `update`, `send`, `chat`, `transcript`
- macOS session via Grok Bot desktop app (no third-party Python deps)
