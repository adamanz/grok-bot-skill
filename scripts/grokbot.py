#!/usr/bin/env python3
"""Talk to Grok Bot teammates and create new ones. Never prints secrets."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import math
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

SECRETS_PATH = Path.home() / "Library/Application Support/Grok Bot/sand-secrets.json"
BACKEND = "https://api2.cursor.sh"
CLIENT_VERSION = "0.30.0"


class GrokBotError(RuntimeError):
    pass


def _die(message: str, code: int = 1) -> None:
    print(json.dumps({"error": message}), file=sys.stderr)
    raise SystemExit(code)


def _safe_storage_key() -> bytes:
    try:
        password = subprocess.check_output(
            [
                "security",
                "find-generic-password",
                "-s",
                "Grok Bot Safe Storage",
                "-a",
                "Grok Bot Key",
                "-w",
            ],
            text=True,
            stderr=subprocess.PIPE,
        ).rstrip("\n")
    except (subprocess.CalledProcessError, OSError) as exc:
        raise GrokBotError(
            "Could not read Grok Bot Safe Storage from Keychain. Unlock the login keychain and retry."
        ) from exc
    return hashlib.pbkdf2_hmac("sha1", password.encode(), b"saltysalt", 1003, dklen=16)


def _decrypt_v10(raw: bytes) -> str:
    if not raw.startswith(b"v10") or len(raw) <= 3 or (len(raw) - 3) % 16:
        raise GrokBotError("Grok Bot access token is not in the expected v10 envelope.")
    proc = subprocess.run(
        [
            "openssl",
            "enc",
            "-aes-128-cbc",
            "-d",
            "-K",
            _safe_storage_key().hex(),
            "-iv",
            (b" " * 16).hex(),
            "-nopad",
        ],
        input=raw[3:],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise GrokBotError("Failed to decrypt the Grok Bot access token.")
    plaintext = proc.stdout
    if not plaintext:
        raise GrokBotError("Failed to decrypt the Grok Bot access token.")
    pad = plaintext[-1]
    if pad < 1 or pad > 16 or plaintext[-pad:] != bytes([pad]) * pad:
        raise GrokBotError("Failed to unpad the Grok Bot access token.")
    try:
        token = plaintext[:-pad].decode()
    except UnicodeDecodeError as exc:
        raise GrokBotError("Failed to decode the Grok Bot access token.") from exc
    if not token:
        raise _unexpected_token()
    return token


def _unexpected_token() -> GrokBotError:
    return GrokBotError(
        "Grok Bot access token is missing or in an unexpected format. "
        "Update this skill and select the signed-in account in Grok Bot. "
        "Do not share sand-secrets.json or Keychain contents."
    )


def _ciphertext_from_stored(stored: str) -> bytes:
    # App 0.18: scoped:v1:<label>:<base64-v10>
    if stored.startswith("scoped:v1:"):
        rest = stored[len("scoped:v1:") :]
        try:
            raw = base64.b64decode(rest[rest.index(":") + 1 :], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise _unexpected_token() from exc
        return raw
    # App 0.23+: Chromium v10 blob as raw base64 (djEw…)
    try:
        raw = base64.b64decode(stored, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise _unexpected_token() from exc
    if raw.startswith(b"v10"):
        return raw
    raise _unexpected_token()


def _token_from_accounts(accounts: dict[str, Any]) -> str | None:
    by_id = accounts.get("accounts")
    if not isinstance(by_id, dict) or not by_id:
        return None
    active = accounts.get("active")
    row = by_id.get(active) if isinstance(active, str) else None
    if not isinstance(row, dict):
        return None
    token = row.get("cursor-access-token")
    return token if isinstance(token, str) and token else None


def _stored_access_token(secrets: dict[str, Any]) -> str:
    # Prefer 0.23+ cursor-accounts so a leftover top-level 0.18 token cannot win.
    if "cursor-accounts" in secrets:
        accounts_raw = secrets["cursor-accounts"]
        if not isinstance(accounts_raw, str):
            raise _unexpected_token()
        try:
            accounts = json.loads(accounts_raw)
        except json.JSONDecodeError as exc:
            raise _unexpected_token() from exc
        if isinstance(accounts, dict):
            token = _token_from_accounts(accounts)
            if token:
                return token
        raise _unexpected_token()
    stored = secrets.get("cursor-access-token")
    if isinstance(stored, str) and stored:
        return stored
    raise _unexpected_token()


def _decrypt_access_token() -> str:
    try:
        secrets = json.loads(SECRETS_PATH.read_text())
    except FileNotFoundError as exc:
        raise GrokBotError(
            "No saved Grok Bot session found. Open Grok Bot and sign in with Cursor. "
            "If already signed in, fully quit and reopen the app to save the session."
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _unexpected_token() from exc
    except OSError as exc:
        raise GrokBotError("Could not read the saved Grok Bot session. Check file permissions.") from exc
    if not isinstance(secrets, dict):
        raise _unexpected_token()
    try:
        return _decrypt_v10(_ciphertext_from_stored(_stored_access_token(secrets)))
    except OSError as exc:
        raise GrokBotError("Could not run OpenSSL to decrypt the saved Grok Bot session.") from exc


def _connect(access_token: str, service: str, method: str, body: dict[str, Any] | None = None) -> Any:
    url = f"{BACKEND}/{service}/{method}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Connect-Protocol-Version": "1",
        "x-cursor-client-type": "sand",
        "x-cursor-client-version": CLIENT_VERSION,
        "x-sand-box-namespace": "prod",
        "x-ghost-mode": "false",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body or {}).encode(),
        headers=headers,
        method="POST",
    )
    return _request_json(req, f"{service}/{method}", timeout=90)


def _safe_error(raw: str) -> str:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return "request failed"
    # Server messages and debug fields may contain credentials or private URLs.
    code = parsed.get("code") if isinstance(parsed, dict) else None
    known_codes = {
        "canceled", "unknown", "invalid_argument", "deadline_exceeded", "not_found",
        "already_exists", "permission_denied", "resource_exhausted", "failed_precondition",
        "aborted", "out_of_range", "unimplemented", "internal", "unavailable",
        "data_loss", "unauthenticated",
    }
    return code if isinstance(code, str) and code in known_codes else "request failed"


def _request_json(req: urllib.request.Request, operation: str, *, timeout: int) -> Any:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        try:
            detail = _safe_error(exc.read().decode(errors="replace"))
        except OSError:
            detail = "request failed"
        finally:
            exc.close()
        raise GrokBotError(f"{operation} failed HTTP {exc.code}: {detail}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise GrokBotError(f"{operation} failed: network connection or timeout error.") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GrokBotError(f"{operation} returned an invalid JSON response.") from exc


def _ensure_box(access_token: str) -> dict[str, str]:
    box = _connect(access_token, "aiserver.v1.GrokBotService", "EnsureSandBox", {})
    gateway_url = box.get("gatewayUrl") or box.get("gateway_url")
    gateway_token = box.get("gatewayToken") or box.get("gateway_token")
    network_token = box.get("networkToken") or box.get("network_token")
    if not gateway_url or not gateway_token or not network_token:
        raise GrokBotError("EnsureSandBox did not return a gateway URL and tokens. Is the computer still starting?")
    return {
        "gateway_url": str(gateway_url).rstrip("/"),
        "gateway_token": str(gateway_token),
        "network_token": str(network_token),
        "cluster": str(box.get("cluster") or ""),
        "pod_id": str(box.get("podId") or box.get("pod_id") or ""),
    }


def _gateway(box: dict[str, str], path: str, body: dict[str, Any] | None = None) -> Any:
    url = box["gateway_url"] + path
    headers = {
        "Authorization": f"Bearer {box['gateway_token']}",
        "x-anyrun-network-token": box["network_token"],
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps({} if body is None else body).encode(),
        headers=headers,
        method="POST",
    )
    return _request_json(req, path, timeout=90)


def _session() -> tuple[str, dict[str, str]]:
    access = _decrypt_access_token()
    return access, _ensure_box(access)


def _summarize_agent(agent: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    description = agent.get("description") or ""
    preview = agent.get("lastMessagePreview") or ""
    if not full and len(description) > 180:
        description = description[:177] + "..."
    if not full and len(preview) > 180:
        preview = preview[:177] + "..."
    return {
        "id": agent.get("id"),
        "name": agent.get("name"),
        "title": agent.get("title") or "",
        "description": description,
        "isActive": agent.get("isActive"),
        "isRunning": agent["isRunning"]
        if "isRunning" in agent
        else agent.get("isRunningTurn"),
        "hasUnread": agent.get("hasUnread"),
        "lastMessagePreview": preview,
        "origin": agent.get("origin"),
    }


def _list_agents(box: dict[str, str]) -> list[dict[str, Any]]:
    rows = _gateway(box, "/api/listAgents", {})
    if isinstance(rows, dict):
        rows = rows.get("agents") or rows.get("rows") or []
    if not isinstance(rows, list):
        raise GrokBotError("listAgents returned an unexpected payload.")
    return [row for row in rows if isinstance(row, dict)]


def _resolve_agent(box: dict[str, str], *, agent_id: str | None, name: str | None) -> dict[str, Any]:
    agents = _list_agents(box)
    if agent_id:
        for agent in agents:
            if agent.get("id") == agent_id:
                return agent
        raise GrokBotError(f"No Grok Bot with id {agent_id}.")
    if name:
        matches = [agent for agent in agents if str(agent.get("name") or "").lower() == name.lower()]
        if not matches:
            available = ", ".join(str(agent.get("name") or "?") for agent in agents) or "(none)"
            raise GrokBotError(f"No Grok Bot named {name!r}. Available: {available}")
        if len(matches) > 1:
            raise GrokBotError(f"Multiple Grok Bots named {name!r}; pass --id.")
        return matches[0]
    raise GrokBotError("Pass --id or --name.")


def _extract_agent(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("agent"), dict):
        return payload["agent"]
    if isinstance(payload, dict) and payload.get("id"):
        return payload
    raise GrokBotError("The gateway did not return an agent object.")


def _transcript_entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("entries", "items", "messages", "transcript"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _entry_text(entry: dict[str, Any]) -> str:
    for key in ("text", "content", "preview", "message"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict):
            nested = value.get("text") or value.get("content")
            if isinstance(nested, str) and nested.strip():
                return nested
    return ""


def cmd_status(_: argparse.Namespace) -> None:
    access = _decrypt_access_token()
    access_status = _connect(access, "aiserver.v1.DashboardService", "GetSandAccessStatus", {})
    run_state = _connect(access, "aiserver.v1.GrokBotService", "GetSandBoxRunState", {})
    box = _ensure_box(access)
    try:
        health = _gateway_get(box, "/health")
    except GrokBotError as exc:
        health = {"error": str(exc)}
    print(
        json.dumps(
            {
                "access": access_status.get("state") or access_status,
                "box": run_state.get("state") or run_state,
                "cluster": box["cluster"],
                "podIdPresent": bool(box["pod_id"]),
                "health": {
                    "ok": bool(isinstance(health, dict) and health.get("ok")),
                    "isBusy": health.get("isBusy") if isinstance(health, dict) else None,
                    "activeAgentId": health.get("activeAgentId") if isinstance(health, dict) else None,
                },
            },
            indent=2,
        )
    )


def _gateway_get(box: dict[str, str], path: str) -> Any:
    url = box["gateway_url"] + path
    headers = {
        "Authorization": f"Bearer {box['gateway_token']}",
        "x-anyrun-network-token": box["network_token"],
    }
    req = urllib.request.Request(url, method="GET", headers=headers)
    return _request_json(req, path, timeout=30)


def cmd_list(args: argparse.Namespace) -> None:
    _, box = _session()
    agents = _list_agents(box)
    print(json.dumps([_summarize_agent(agent, full=args.full) for agent in agents], indent=2))


def cmd_create(args: argparse.Namespace) -> None:
    _, box = _session()
    existing = [
        agent
        for agent in _list_agents(box)
        if str(agent.get("name") or "").lower() == args.name.lower()
    ]
    if existing and not args.force:
        raise GrokBotError(
            f"A Grok Bot named {args.name!r} already exists ({existing[0].get('id')}). "
            "Use a different name, or pass --force to create another."
        )
    created = _gateway(
        box,
        "/api/createAgent",
        {
            "name": args.name,
            "description": args.description or "",
            "origin": "user",
            "isKickstartRequested": not args.no_kickstart,
        },
    )
    agent = _extract_agent(created)
    agent_id = agent.get("id")
    if agent_id and (args.title or args.description):
        updated = _gateway(
            box,
            "/api/updateAgent",
            {
                "id": agent_id,
                "profile": {
                    "name": args.name,
                    "title": args.title or "",
                    "description": args.description or "",
                },
            },
        )
        agent = _extract_agent(updated) if isinstance(updated, dict) else agent
    print(json.dumps(_summarize_agent(agent, full=True), indent=2))


def cmd_update(args: argparse.Namespace) -> None:
    _, box = _session()
    agent = _resolve_agent(box, agent_id=args.id, name=args.name)
    profile = {
        "name": args.rename or agent.get("name") or "",
        "title": args.title if args.title is not None else agent.get("title") or "",
        "description": args.description
        if args.description is not None
        else agent.get("description") or "",
    }
    updated = _gateway(box, "/api/updateAgent", {"id": agent["id"], "profile": profile})
    print(json.dumps(_summarize_agent(_extract_agent(updated), full=True), indent=2))


def _send_prompt(access: str, agent_id: str, prompt: str, message_id: str) -> dict[str, Any]:
    try:
        result = _connect(access, "aiserver.v1.GrokBotService", "SendGrokBotUserMessage", {
            "agentId": agent_id,
            "messageId": message_id,
            "text": prompt,
            "sentAtMs": str(int(time.time() * 1000)),
        })
    except GrokBotError as exc:
        raise GrokBotError(
            f"{exc} Delivery is unconfirmed. Retry the same message to the same bot "
            f"with --message-id {message_id}."
        ) from exc
    accepted = {
        1, 2, 3, "ACCEPTED_BOX", "ACCEPTED_TEMPORAL", "DUPLICATE",
        "GROK_BOT_USER_MESSAGE_DELIVERY_ACCEPTED_BOX",
        "GROK_BOT_USER_MESSAGE_DELIVERY_ACCEPTED_TEMPORAL",
        "GROK_BOT_USER_MESSAGE_DELIVERY_DUPLICATE",
    }
    delivery = result.get("delivery") if isinstance(result, dict) else None
    if type(delivery) not in (int, str) or delivery not in accepted:
        raise GrokBotError(f"Message {message_id} was not confirmed accepted.")
    return {"accepted": True, "messageId": message_id}


def cmd_send(args: argparse.Namespace) -> None:
    access, box = _session()
    agent = _resolve_agent(box, agent_id=args.id, name=args.name)
    result = _send_prompt(access, agent["id"], args.prompt, args.message_id or str(uuid.uuid4()))
    print(
        json.dumps(
            {
                **result,
                "agent": _summarize_agent(agent),
            },
            indent=2,
        )
    )


def cmd_transcript(args: argparse.Namespace) -> None:
    _, box = _session()
    agent = _resolve_agent(box, agent_id=args.id, name=args.name)
    payload = _gateway(box, "/api/getAgentTranscript", {"id": agent["id"]})
    entries = _transcript_entries(payload)
    if args.limit:
        entries = entries[-args.limit :]
    print(
        json.dumps(
            {
                "agent": _summarize_agent(agent),
                "entries": [
                    {
                        "id": entry.get("id") or entry.get("entryId"),
                        "kind": entry.get("kind") or entry.get("type"),
                        "author": entry.get("authorId") or entry.get("role") or entry.get("author"),
                        "text": _entry_text(entry),
                    }
                    for entry in entries
                ],
            },
            indent=2,
        )
    )


def cmd_chat(args: argparse.Namespace) -> None:
    access, box = _session()
    agent = _resolve_agent(box, agent_id=args.id, name=args.name)
    before = _transcript_entries(_gateway(box, "/api/getAgentTranscript", {"id": agent["id"]}))
    before_ids = {entry.get("id") or entry.get("entryId") for entry in before}
    sent = _send_prompt(access, agent["id"], args.prompt, args.message_id or str(uuid.uuid4()))
    deadline = time.time() + args.timeout
    latest = agent
    while time.time() < deadline:
        time.sleep(args.poll)
        agents = _list_agents(box)
        latest = next((row for row in agents if row.get("id") == agent["id"]), latest)
        running = bool(latest.get("isRunning") or latest.get("isRunningTurn") or latest.get("isComposingMessage"))
        if not running:
            break
    after = _transcript_entries(_gateway(box, "/api/getAgentTranscript", {"id": agent["id"]}))
    new_entries = [
        entry
        for entry in after
        if (entry.get("id") or entry.get("entryId")) not in before_ids
    ]
    print(
        json.dumps(
            {
                "messageId": sent["messageId"],
                "agent": _summarize_agent(latest, full=True),
                "stillRunning": bool(
                    latest.get("isRunning") or latest.get("isRunningTurn") or latest.get("isComposingMessage")
                ),
                "newEntries": [
                    {
                        "id": entry.get("id") or entry.get("entryId"),
                        "kind": entry.get("kind") or entry.get("type"),
                        "author": entry.get("authorId") or entry.get("role") or entry.get("author"),
                        "text": _entry_text(entry),
                    }
                    for entry in new_entries
                ],
            },
            indent=2,
        )
    )


def _add_agent_selector(parser: argparse.ArgumentParser) -> None:
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--id", help="Grok Bot UUID")
    selector.add_argument("--name", help="Grok Bot display name")


def _positive_seconds(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return number


def _nonnegative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def _message_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a UUID") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with Grok Bot and create new teammates.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Check sign-in and cloud computer health")

    list_p = sub.add_parser("list", help="List Grok Bots")
    list_p.add_argument("--full", action="store_true", help="Do not truncate descriptions")

    create_p = sub.add_parser("create", help="Spin up a new Grok Bot")
    create_p.add_argument("--name", required=True)
    create_p.add_argument("--title", default="")
    create_p.add_argument("--description", default="")
    create_p.add_argument("--no-kickstart", action="store_true")
    create_p.add_argument("--force", action="store_true", help="Allow a duplicate name")

    update_p = sub.add_parser("update", help="Edit a Grok Bot profile")
    _add_agent_selector(update_p)
    update_p.add_argument("--rename")
    update_p.add_argument("--title")
    update_p.add_argument("--description")

    send_p = sub.add_parser("send", help="Send a message without waiting")
    _add_agent_selector(send_p)
    send_p.add_argument("--prompt", required=True)
    send_p.add_argument("--message-id", type=_message_id, help="Reuse the same UUID when retrying a message")

    chat_p = sub.add_parser("chat", help="Send a message and wait for new replies")
    _add_agent_selector(chat_p)
    chat_p.add_argument("--prompt", required=True)
    chat_p.add_argument("--message-id", type=_message_id, help="Reuse the same UUID when retrying a message")
    chat_p.add_argument("--timeout", type=_positive_seconds, default=90)
    chat_p.add_argument("--poll", type=_positive_seconds, default=2.0)

    transcript_p = sub.add_parser("transcript", help="Read a Grok Bot conversation")
    _add_agent_selector(transcript_p)
    transcript_p.add_argument("--limit", type=_nonnegative_int, default=20, help="Recent entries to show; 0 shows all")

    args = parser.parse_args()
    commands = {
        "status": cmd_status,
        "list": cmd_list,
        "create": cmd_create,
        "update": cmd_update,
        "send": cmd_send,
        "chat": cmd_chat,
        "transcript": cmd_transcript,
    }
    try:
        commands[args.command](args)
    except GrokBotError as exc:
        _die(str(exc))


if __name__ == "__main__":
    main()
