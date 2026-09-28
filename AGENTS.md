# Grok Bot Skill

This repository is an [Agent Skill](https://agentskills.io) for the xAI Grok Bot desktop app.

Install:

```bash
npx skills add adamanz/grok-bot-skill -g
```

When the user wants to chat with a Grok Bot, list Grok Bots, create a new Grok Bot teammate, create or inspect native groups, remove group members, or operate Grok Bot from the terminal, follow [SKILL.md](SKILL.md) and run `scripts/grokbot.py`. Do not use the Cursor Cloud Agents API.

Native group behavior and private-protocol limitations are documented in
[references/native-groups.md](references/native-groups.md). Run
`python3 -m unittest discover -s tests -v` for mocked regression coverage; tests
must not read credentials, create bots, or send live messages.
