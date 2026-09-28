"""Bound native transcript retention without real credentials or blob requests."""
import base64
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("grok_transcript_limits", Path(__file__).resolve().parents[1] / "scripts/grokbot.py")
grok = importlib.util.module_from_spec(spec)
with patch.object(Path, "exists", return_value=False):
    spec.loader.exec_module(grok)


class TranscriptLimitTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.dumps({"id": "one", "kind": "send-message", "text": "Draft"}).encode()
        self.blob_hashes = [str(index) * 64 for index in range(4)]

    def instructions(self, hashes):
        return {"instructions": [{"relPath": "blobs/" + value,
                                  "url": "https://storage.example.invalid/" + value} for value in hashes]}

    def test_blob_downloads_stop_when_aggregate_budget_is_exceeded(self):
        rows = [{"blobHash": value} for value in self.blob_hashes]
        with patch.object(grok, "_connect", side_effect=[{"entries": rows}, self.instructions(self.blob_hashes)]), \
             patch.object(grok, "_read_signed_blob", return_value=self.raw) as read, \
             patch.object(grok, "MAX_TRANSCRIPT_BYTES", len(self.raw) * 2):
            with self.assertRaisesRegex(grok.GrokBotError, "smaller --limit"):
                grok._native_transcript("synthetic", "room")
        self.assertEqual(read.call_count, 3)

    def test_repeated_blob_reference_counts_each_decoded_entry(self):
        value = self.blob_hashes[0]
        rows = [{"blobHash": value}] * 3
        with patch.object(grok, "_connect", side_effect=[{"entries": rows}, self.instructions([value, value])]), \
             patch.object(grok, "_read_signed_blob", return_value=self.raw) as read, \
             patch.object(grok, "MAX_TRANSCRIPT_BYTES", len(self.raw) * 2):
            with self.assertRaisesRegex(grok.GrokBotError, "smaller --limit"):
                grok._native_transcript("synthetic", "room")
        read.assert_called_once()

    def test_inline_and_blob_bodies_share_decoded_budget(self):
        value = self.blob_hashes[0]
        rows = [{"body": base64.b64encode(self.raw).decode()}, {"blobHash": value}]
        with patch.object(grok, "_connect", side_effect=[{"entries": rows}, self.instructions([value])]), \
             patch.object(grok, "_read_signed_blob", return_value=self.raw), \
             patch.object(grok, "MAX_TRANSCRIPT_BYTES", len(self.raw) * 2 - 1):
            with self.assertRaisesRegex(grok.GrokBotError, "smaller --limit"):
                grok._native_transcript("synthetic", "room")

    def test_exact_budget_preserves_chronology_and_authors(self):
        first = {"id": "first", "kind": "message", "authorId": "user", "text": "Draft only"}
        second = {"id": "second", "kind": "send-message", "author": {"id": "bot"}, "text": "Draft"}
        first_raw, second_raw = json.dumps(first).encode(), json.dumps(second).encode()
        value = self.blob_hashes[0]
        rows = [{"blobHash": value}, {"body": base64.b64encode(first_raw).decode()}]
        with patch.object(grok, "_connect", side_effect=[{"entries": rows}, self.instructions([value])]), \
             patch.object(grok, "_read_signed_blob", return_value=second_raw), \
             patch.object(grok, "MAX_TRANSCRIPT_BYTES", len(first_raw) + len(second_raw)):
            self.assertEqual(grok._native_transcript("synthetic", "room"), [first, second])

    def test_oversized_inline_body_is_rejected_before_base64_decode(self):
        rows = [{"body": base64.b64encode(self.raw).decode()}]
        with patch.object(grok, "_connect", return_value={"entries": rows}), \
             patch.object(grok, "MAX_TRANSCRIPT_BYTES", len(self.raw) // 2), \
             patch.object(grok.base64, "b64decode", side_effect=AssertionError("Oversized allocation")):
            with self.assertRaisesRegex(grok.GrokBotError, "smaller --limit"):
                grok._native_transcript("synthetic", "room")


if __name__ == "__main__":
    unittest.main()
