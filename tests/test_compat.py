"""Regression checks for the patched skill; all credential operations are mocked.

Run: python3 -m unittest discover -s tests -v
Set GROK_BOT_SCRIPT only to test a different checkout explicitly.
"""
import base64
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SCRIPT = Path(os.environ.get("GROK_BOT_SCRIPT", str(Path(__file__).resolve().parents[1] / "scripts" / "grokbot.py"))).expanduser()
spec = importlib.util.spec_from_file_location("grokbot", SCRIPT)
grokbot = importlib.util.module_from_spec(spec)
# Import-time version discovery must not inspect the locally installed app.
with patch.object(Path, "exists", return_value=False):
    spec.loader.exec_module(grokbot)

# Synthetic ciphertext and plaintext only. No real secret store is read.
ENCRYPTED = base64.b64encode(b"v10" + b"0" * 16).decode()
FAKE_TOKEN = b"fake-test-token"
PAD = 16 - len(FAKE_TOKEN) % 16


class AuthCompatibilityTests(unittest.TestCase):
    def decrypt(self, contents):
        store = Mock()
        store.exists.return_value = True
        store.read_text.return_value = json.dumps(contents)
        with patch.object(grokbot, "SECRETS_PATH", store), patch.object(
            grokbot.subprocess, "check_output", return_value="fake-keychain-password"
        ), patch.object(
            grokbot.subprocess, "run",
            return_value=SimpleNamespace(returncode=0, stdout=FAKE_TOKEN + bytes([PAD]) * PAD),
        ):
            return grokbot._decrypt_access_token()

    def test_legacy_scoped_token(self):
        self.assertEqual(self.decrypt({"cursor-access-token": "scoped:v1:test:" + ENCRYPTED}), FAKE_TOKEN.decode())

    def test_current_active_account(self):
        store = {"active": "selected", "accounts": {
            "other": {"cursor-access-token": "invalid"},
            "selected": {"cursor-access-token": ENCRYPTED},
        }}
        self.assertEqual(self.decrypt({"cursor-accounts": json.dumps(store)}), FAKE_TOKEN.decode())

    def test_signed_out_does_not_use_another_account_or_legacy_token(self):
        store = {"active": None, "accounts": {"other": {"cursor-access-token": ENCRYPTED}}}
        with self.assertRaises(grokbot.GrokBotError):
            self.decrypt({"cursor-accounts": json.dumps(store), "cursor-access-token": "scoped:v1:test:" + ENCRYPTED})

    def test_corrupt_accounts_are_reported_without_contents(self):
        with self.assertRaisesRegex(grokbot.GrokBotError, "account storage is invalid") as raised:
            self.decrypt({"cursor-accounts": "private-invalid-contents"})
        self.assertNotIn("private-invalid-contents", str(raised.exception))

    def test_invalid_envelopes_are_rejected(self):
        for value in ("scoped:v1:missing-delimiter", "not-base64", base64.b64encode(b"plaintext").decode()):
            with self.subTest(value=value), self.assertRaises(grokbot.GrokBotError):
                self.decrypt({"cursor-access-token": value})

    def test_version_comes_from_installed_app(self):
        with patch.object(Path, "exists", return_value=True), patch.object(Path, "open", unittest.mock.mock_open()), patch.object(
            grokbot.plistlib, "load", return_value={"CFBundleShortVersionString": "0.59.1"}
        ):
            self.assertEqual(grokbot._client_version(), "0.59.1")


if __name__ == "__main__":
    unittest.main()
