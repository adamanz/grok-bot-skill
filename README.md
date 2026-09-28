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
- Python 3 (stdlib only — no pip packages)

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
| `group-create` | Create or reuse a native group of 2–6 existing bots |
| `group-info` | Inspect a native group and its members |
| `group-remove-member` | Remove members while preserving bots and history |

```bash
SCRIPT="$HOME/.cursor/skills/grok-bot/scripts/grokbot.py"

python3 "$SCRIPT" status
python3 "$SCRIPT" list
python3 "$SCRIPT" chat --name Reed --prompt "What are you waiting on from me?"
python3 "$SCRIPT" create --name Reed --title "Chief of staff" \
  --description "Draft-only daily brief. Never send messages."
```

See [examples.md](examples.md) for more prompts and [SKILL.md](SKILL.md) for agent instructions.

## Native shared conversations

```bash
python3 "$SCRIPT" group-create --name "Planning room" \
  --member-name "Reed" --member-name "Researcher" --reuse
python3 "$SCRIPT" group-info --name "Planning room"
python3 "$SCRIPT" send --name "Planning room" --prompt "Compare your draft plans."
python3 "$SCRIPT" transcript --name "Planning room" --limit 40
python3 "$SCRIPT" group-remove-member --name "Planning room" --member-id BOT_ID
```

The members must already exist. Group creation does not send a prompt or change
existing bot profiles. `--reuse` accepts only the same name and member set;
ambiguous names and mismatched membership fail without replacing a room.
Removal preserves the bots and history and must leave at least one member.

Both older computer-backed and newer server-backed rooms are supported. The
CLI reads each message's actual author and current running state, including
rooms not yet opened in the desktop app. This uses the app's private protocol,
verified against Grok Bot 0.59.1, and is not an official SDK. See
[native group details](references/native-groups.md) for limitations and results.

Run the mocked regression suite without credentials or live bot actions:

```bash
python3 -m unittest discover -s tests -v
```

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
