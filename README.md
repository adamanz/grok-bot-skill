# Grok Bot Skill

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-SKILL.md-111)](https://agentskills.io)
[![skills.sh](https://skills.sh/b/adamanz/grok-bot-skill)](https://skills.sh/adamanz/grok-bot-skill)
[![Install](https://img.shields.io/badge/install-npx%20skills%20add-000)](https://github.com/vercel-labs/skills)

**Cursor / Claude Code / Codex skill** to chat with [Grok Bot](https://grok.com) teammates and create new Grok Bots from the terminal — without clicking through the desktop UI.

This is the xAI **Grok Bot** desktop app (cloud computer + named teammates). It is **not** the Cursor Cloud Agents API (`api.cursor.com/v1/agents`).

## Install

```bash
npx skills add adamanz/grok-bot-skill -g -a cursor
```

Works with other agents too:

```bash
npx skills add adamanz/grok-bot-skill -g -a claude-code
npx skills add adamanz/grok-bot-skill -g -a codex
npx skills add adamanz/grok-bot-skill --all
```

Or clone it yourself:

```bash
git clone https://github.com/adamanz/grok-bot-skill.git ~/.cursor/skills/grok-bot
```

Then ask Cursor: **“list my Grok Bots”** or **“message Reed”**.

## Requirements

- macOS with the **Grok Bot** desktop app installed and signed in
- An eligible plan (SuperGrok Heavy, Cursor Ultra, or Cursor Teams Premium)
- Python 3.10 or newer, plus macOS `security` and `openssl` commands. No pip packages are needed.

The CLI uses your local Grok Bot session. It never prints tokens, gateway URLs, or Keychain material.

## What you can do

| Command | Purpose |
|---|---|
| `status` | Sign-in + cloud computer health |
| `list` | List Grok Bot teammates |
| `chat` | Send a prompt and wait for a reply |
| `send` | Fire-and-forget a prompt |
| `create` | Spin up a new named Grok Bot |
| `update` | Edit name, title, or standing rules |
| `transcript` | Read recent conversation |

```bash
SCRIPT="$HOME/.cursor/skills/grok-bot/scripts/grokbot.py"

python3 "$SCRIPT" status
python3 "$SCRIPT" list
python3 "$SCRIPT" chat --name Reed --prompt "What are you waiting on from me?"
python3 "$SCRIPT" create --name Reed --title "Chief of staff" \
  --description "Draft-only daily brief. Never send messages."
```

See [examples.md](examples.md) for more prompts and [SKILL.md](SKILL.md) for agent instructions.

Both `send` and `chat` return a `messageId` and accept `--message-id UUID`.
Reuse the same UUID, bot, and prompt when retrying uncertain delivery to avoid
duplicate messages. Use a new UUID for each new message. To retain the ID even
if the process is interrupted, generate it before sending:

```bash
MESSAGE_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
python3 "$SCRIPT" send --name Reed --prompt "Summarize your progress" --message-id "$MESSAGE_ID"
```

Keep `MESSAGE_ID` for retries. After a `chat` error during polling, check
`transcript` before sending again. Without the original ID, a retry could send
the message twice.

`chat` waits to observe the bot running and then becoming idle. Queued messages
can leave the bot idle before work starts. If that transition is not observed
within `--timeout`, output includes `timedOut: true` and the latest transcript.
A very fast reply can finish between polls, so a timeout means completion is
unconfirmed; inspect `newEntries` or use `transcript` before deciding to retry.

## Session troubleshooting

The CLI reads both legacy `scoped:v1:` tokens and the active account in the
newer `cursor-accounts` store. It does not choose an inactive saved account or
fall back to a stale legacy token when a saved-account store is present.

If no saved session exists, open Grok Bot and sign in with Cursor. If the app
is already signed in, fully quit and reopen it to save the session. For an
unexpected-format error, update this skill and select your signed-in account
in the app. Never paste `sand-secrets.json` or Keychain contents into an issue.

## Development

Run the regression tests from the repository root:

```bash
python3 -m unittest discover -s scripts -p 'test_*.py' -v
```

Tests use synthetic credentials and simulated HTTP responses. They do not
access your Keychain, contact Grok Bot, or send messages. OpenSSL is required
for the encrypted-session fixtures.

## Why this exists

Grok Bot teammates live on a shared cloud computer. The official path is the desktop app. This skill lets your coding agent:

- message an existing bot and bring the reply back into chat
- create a focused teammate (one job, standing rules, draft-only first task)
- stay out of the Cursor Cloud Agents API when you asked for a Grok Bot

## Safety

- Do not paste passwords, API keys, or 2FA into prompts. Take over **Agent Computer** in the Grok Bot app instead.
- Separate bots are not a security boundary — they share one computer.
- The CLI decrypts the local session only to call the API. Treat `sand-secrets.json` and Keychain as secrets.

## Discover

- [skills.sh/adamanz/grok-bot-skill](https://skills.sh/adamanz/grok-bot-skill)
- Search GitHub: `grok-bot skill`, `cursor grok bot`, `xai grok teammates`
- Agent Skills spec: [agentskills.io](https://agentskills.io)

## License

MIT
