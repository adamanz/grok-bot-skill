---
name: grok-bot
description: >-
  Chat with Grok Bot teammates, message a named Grok Bot, create a new Grok Bot,
  list Grok Bots, create and inspect native multi-bot groups, remove group members,
  read a Grok Bot transcript, or operate the xAI Grok Bot
  desktop app from Cursor, Claude Code, or Codex instead of the UI. Use when
  the user mentions Grok Bot, Grok teammates, Reed, Agent Computer, spinning
  up a new bot, or talking to Grok from the terminal.
license: MIT
metadata:
  author: adamanz
  version: "1.0.0"
  homepage: https://github.com/adamanz/grok-bot-skill
---

# Grok Bot

Talk to existing Grok Bot teammates and create new ones through the local CLI.
Do not drive the Grok Bot desktop UI and do not call the gateway by hand.

## Workflow

1. Run `status`, then `list`.
2. Message an existing bot with `chat` (waits for a reply) or `send` (fire-and-forget).
3. Create a new bot with `create` only when the user asked for a new teammate.
4. Prefer one focused job per bot. Put standing rules in `--description`. Keep first tasks draft-only.

The CLI is `scripts/grokbot.py` next to this file. Resolve that path from the
installed skill directory (often `~/.cursor/skills/grok-bot/scripts/grokbot.py`).

```bash
SCRIPT="$(dirname "$0")/scripts/grokbot.py"
# or, after a global Cursor install:
SCRIPT="$HOME/.cursor/skills/grok-bot/scripts/grokbot.py"

python3 "$SCRIPT" status
python3 "$SCRIPT" list
python3 "$SCRIPT" chat --name Reed --prompt "What are you waiting on from me?"
python3 "$SCRIPT" create --name Reed --title "Chief of staff" --description "Draft-only daily brief. Never send messages."
python3 "$SCRIPT" transcript --name Reed --limit 20
```

Output is JSON. Summarize it for the user. Never paste tokens, gateway URLs, or raw keychain material into chat.

## Create rules

A new bot needs:

- `--name` — short, unique
- `--title` — one job, not "general helper"
- `--description` — outcome, sources, and what it must not do without approval

Do not create a second bot with the same name unless the user asks. The CLI refuses duplicates unless `--force` is passed.

After create, send one concrete first task with `chat` unless the user only wanted the empty teammate.

## Native groups

Use `group-create --name NAME --member-name BOT ... --reuse` to create a native
shared conversation with 2–6 existing, distinct bots. Repeat `--member-id` when
names are ambiguous. Reuse requires the exact name and member set; it never
replaces members or starts a conversation. Use the returned group ID with
`send`, `chat`, or `transcript` when messaging is authorized.

Use `group-info --id ID` to inspect verified members. Use
`group-remove-member --id GROUP_ID --member-id BOT_ID` (repeat `--member-id`
as needed) to remove members without deleting their bots or message history.
Keep at least one bot in the group. Only remove members within the user's
requested workflow; a controller acting on a bot's withdrawal must verify
that bot's native author identity rather than trusting another bot's text.

Groups require owned bots on the same runtime. Unknown activity is reported as
`null`, not idle. Inspect uncertain operations before retrying; mutations are
not blindly resent. Read [native group details](references/native-groups.md)
for runtime compatibility, result shapes, and concurrent membership edits.

## Chat rules

- Resolve bots by `--name` when unique; use `--id` if names collide.
- Use `chat` when the user expects an answer in this conversation.
- Use `send` for long-running work the user will check in the Grok Bot app.
- If `stillRunning` is true, say so and offer to poll again. Do not invent a reply.
- Do not send passwords, API keys, or 2FA codes. Tell the user to take over **Agent Computer** in the Grok Bot app.
- Do not approve local-computer access unless the user explicitly asked for it.

## Safety

- The CLI decrypts the Grok Bot session from Keychain and `sand-secrets.json`. Treat that as a secret broker. Never print or log tokens.
- All of a user's bots share one cloud computer. Do not treat separate bots as a security boundary.
- This is not the Cursor Cloud Agents API (`api.cursor.com/v1/agents`). Do not create a coding cloud agent when the user asked for a Grok Bot.

## Troubleshooting

| Symptom | What to do |
|---|---|
| Not signed in / unexpected token format | Ask the user to open Grok Bot and sign in with Cursor |
| Keychain read failed | Ask them to unlock the login keychain and retry |
| Access not granted | Eligible plans: SuperGrok Heavy, Cursor Ultra, Cursor Teams Premium |
| Computer still starting | Wait and rerun `status` until box is running |
| No bot with that name | `list` and confirm the name |

If the CLI is missing a command the user needs, read `scripts/grokbot.py --help`. Do not invent new gateway routes in chat.
