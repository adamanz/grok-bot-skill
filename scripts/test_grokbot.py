import argparse
import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import uuid

import grokbot


class SessionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "synthetic-keychain-password"
        key = hashlib.pbkdf2_hmac("sha1", cls.password.encode(), b"saltysalt", 1003, 16)
        encrypted = subprocess.run(
            ["openssl", "enc", "-aes-128-cbc", "-K", key.hex(), "-iv", (b" " * 16).hex()],
            input=b"synthetic-access-token", capture_output=True, check=True,
        ).stdout
        cls.blob = base64.b64encode(b"v10" + encrypted).decode()

    def decrypt(self, value):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sand-secrets.json"
            path.write_text(json.dumps(value))
            with patch.object(grokbot, "SECRETS_PATH", path), patch.object(
                grokbot.subprocess, "check_output", return_value=self.password + "\n"
            ):
                return grokbot._decrypt_access_token()

    def test_legacy_scoped_token(self):
        self.assertEqual(self.decrypt({"cursor-access-token": "scoped:v1:" + "a" * 64 + ":" + self.blob}), "synthetic-access-token")

    def test_unscoped_token(self):
        self.assertEqual(self.decrypt({"cursor-access-token": self.blob}), "synthetic-access-token")

    def test_active_account_wins_over_stale_legacy_token(self):
        self.assertEqual(self.decrypt({
            "cursor-access-token": "stale-token",
            "cursor-accounts": json.dumps({"active": "current", "accounts": {
                "other": {"cursor-access-token": "wrong-token"},
                "current": {"cursor-access-token": self.blob},
            }}),
        }), "synthetic-access-token")

    def test_missing_active_account_never_chooses_another_account(self):
        for active in (None, "missing", [], ""):
            with self.subTest(active=active), self.assertRaises(grokbot.GrokBotError):
                self.decrypt({"cursor-accounts": json.dumps({"active": active, "accounts": {
                    "saved": {"cursor-access-token": self.blob},
                }})})

    def test_invalid_accounts_never_fall_back_to_legacy_token(self):
        for accounts in ("invalid-json", "null", "[]", "{}", {}, "", 12):
            with self.subTest(accounts=accounts), self.assertRaises(grokbot.GrokBotError):
                self.decrypt({"cursor-access-token": "scoped:v1:" + "a" * 64 + ":" + self.blob, "cursor-accounts": accounts})

    def test_malformed_store_and_envelopes_raise_controlled_errors(self):
        for value in ([], None, {}, {"cursor-access-token": "scoped:v1:missing-payload"},
                      {"cursor-access-token": self.blob + "!"}, {"cursor-access-token": "djEw"}):
            with self.subTest(value=value), self.assertRaises(grokbot.GrokBotError):
                self.decrypt(value)

    def test_empty_or_invalid_decrypted_text_is_rejected(self):
        for plaintext in (b"", b"\x10" * 16, b"\xff" + b"\x0f" * 15, b"text" + b"\x00" * 12):
            with self.subTest(plaintext=plaintext), patch.object(
                grokbot.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, plaintext)
            ), self.assertRaises(grokbot.GrokBotError):
                self.decrypt({"cursor-access-token": "scoped:v1:" + "a" * 64 + ":" + self.blob})

    def test_missing_store_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            grokbot, "SECRETS_PATH", Path(directory) / "missing.json"
        ), self.assertRaisesRegex(grokbot.GrokBotError, "Open Grok Bot"):
            grokbot._decrypt_access_token()


class MessageTest(unittest.TestCase):
    def send(self, delivery, *, command="send", message_id=None):
        agent = {"id": "agent-1", "name": "Reed", "isRunning": False}
        box = {"gateway_url": "https://example.invalid", "gateway_token": "fake", "network_token": "fake"}
        response = io.BytesIO(json.dumps({"delivery": delivery}).encode())
        args = argparse.Namespace(id=None, name="Reed", prompt="Hello", message_id=message_id, timeout=1, poll=0.01)
        output = io.StringIO()
        with patch.object(grokbot, "_session", return_value=("fake-access", box)), patch.object(
            grokbot, "_list_agents", return_value=[agent]
        ), patch.object(grokbot, "_transcript_entries", return_value=[]), patch.object(
            grokbot, "_gateway", return_value=[]
        ), patch.object(grokbot.urllib.request, "urlopen", return_value=response) as request, contextlib.redirect_stdout(output):
            getattr(grokbot, "cmd_" + command)(args)
        self.assertIsNotNone(request.call_args, "Message must use the backend service")
        return json.loads(output.getvalue()), request.call_args.args[0]

    def test_send_uses_current_endpoint_and_preserves_message_id(self):
        message_id = "a6eb9092-1043-4839-a057-27c59f12872a"
        output, request = self.send(2, message_id=message_id)
        self.assertTrue(output["accepted"])
        self.assertEqual(output["messageId"], message_id)
        self.assertEqual(request.full_url, grokbot.BACKEND + "/aiserver.v1.GrokBotService/SendGrokBotUserMessage")
        body = json.loads(request.data)
        self.assertEqual(body["agentId"], "agent-1")
        self.assertEqual(body["text"], "Hello")
        self.assertEqual(body["messageId"], message_id)
        self.assertGreater(int(body["sentAtMs"]), 0)

    def test_all_accepted_delivery_values(self):
        for delivery in (1, 2, 3, "ACCEPTED_BOX", "ACCEPTED_TEMPORAL", "DUPLICATE",
                         "GROK_BOT_USER_MESSAGE_DELIVERY_ACCEPTED_BOX",
                         "GROK_BOT_USER_MESSAGE_DELIVERY_ACCEPTED_TEMPORAL",
                         "GROK_BOT_USER_MESSAGE_DELIVERY_DUPLICATE"):
            with self.subTest(delivery=delivery):
                output, _ = self.send(delivery)
                self.assertTrue(output["accepted"])
                self.assertEqual(str(uuid.UUID(output["messageId"])), output["messageId"])

    def test_refused_unknown_and_malformed_delivery_fail(self):
        for delivery in (0, 4, "REFUSED", "UNSPECIFIED", None, True, [], {}):
            with self.subTest(delivery=delivery), self.assertRaises(grokbot.GrokBotError):
                self.send(delivery)

    def test_chat_uses_same_transport_and_returns_message_id(self):
        message_id = "a6eb9092-1043-4839-a057-27c59f12872a"
        output, request = self.send("DUPLICATE", command="chat", message_id=message_id)
        self.assertEqual(output["messageId"], message_id)
        self.assertEqual(json.loads(request.data)["messageId"], message_id)


class ErrorTest(unittest.TestCase):
    def test_send_timeout_includes_reusable_message_id(self):
        message_id = "a6eb9092-1043-4839-a057-27c59f12872a"
        with patch.object(grokbot.urllib.request, "urlopen", side_effect=TimeoutError("private-url")), self.assertRaises(grokbot.GrokBotError) as caught:
            grokbot._send_prompt("fake", "agent-1", "Hello", message_id)
        self.assertIn("--message-id " + message_id, str(caught.exception))
        self.assertNotIn("private-url", str(caught.exception))

    def test_http_error_does_not_echo_credentials(self):
        error = urllib.error.HTTPError("https://private.invalid", 403, "secret", {}, io.BytesIO(b'{"code":"permission_denied","message":"secret"}'))
        with patch.object(grokbot.urllib.request, "urlopen", side_effect=error), self.assertRaises(grokbot.GrokBotError) as caught:
            grokbot._connect("fake", "Service", "Method")
        self.assertIn("HTTP 403: permission_denied", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))
        self.assertNotIn("private.invalid", str(caught.exception))

    def test_untrusted_errors_do_not_echo_response_content(self):
        for raw in ("private-token", '"private-token"', '["private-token"]',
                    '{"message":"private-token","details":[{"debug":{"error":"private-token"}}]}',
                    '{"error":"private-token"}', '{"code":"private-token"}'):
            with self.subTest(raw=raw):
                self.assertNotIn("private-token", grokbot._safe_error(raw))

    def test_known_error_codes_remain_actionable(self):
        self.assertIn("permission_denied", grokbot._safe_error('{"code":"permission_denied"}'))

    def test_network_and_invalid_json_errors_are_controlled(self):
        box = {"gateway_url": "https://example.invalid", "gateway_token": "fake", "network_token": "fake"}
        calls = (lambda: grokbot._connect("fake", "Service", "Method"),
                 lambda: grokbot._gateway(box, "/api/listAgents"),
                 lambda: grokbot._gateway_get(box, "/health"))
        for call in calls:
            for error in (urllib.error.URLError("private-url"), TimeoutError("private-url")):
                with self.subTest(call=call, error=error):
                    with patch.object(grokbot.urllib.request, "urlopen", side_effect=error), self.assertRaises(grokbot.GrokBotError) as caught:
                        call()
                    self.assertNotIn("private-url", str(caught.exception))
            with patch.object(grokbot.urllib.request, "urlopen", return_value=io.BytesIO(b"not-json")), self.assertRaises(grokbot.GrokBotError):
                call()


class ArgumentTest(unittest.TestCase):
    def test_invalid_arguments_fail_before_opening_a_session(self):
        for args in (
            ["send", "--prompt", "Hello"],
            ["send", "--name", "Reed", "--id", "agent-1", "--prompt", "Hello"],
            ["send", "--name", "Reed", "--prompt", "Hello", "--message-id", "bad-id"],
            ["chat", "--name", "Reed", "--prompt", "Hello", "--poll", "-1"],
            ["chat", "--name", "Reed", "--prompt", "Hello", "--poll", "nan"],
            ["chat", "--name", "Reed", "--prompt", "Hello", "--poll", "inf"],
            ["chat", "--name", "Reed", "--prompt", "Hello", "--timeout", "0"],
            ["transcript", "--name", "Reed", "--limit", "-1"],
        ):
            with self.subTest(args=args), patch.object(grokbot.sys, "argv", ["grokbot.py", *args]), patch.object(grokbot, "_session") as session, contextlib.redirect_stderr(io.StringIO()):
                try:
                    with self.assertRaises(SystemExit) as caught:
                        grokbot.main()
                    self.assertEqual(caught.exception.code, 2)
                finally:
                    session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
