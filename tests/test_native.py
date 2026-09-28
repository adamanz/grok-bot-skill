"""Native room regressions: no network, credentials, or live bot actions."""
import base64
import contextlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("native_grok", Path(__file__).resolve().parents[1] / "scripts/grokbot.py")
grok = importlib.util.module_from_spec(spec)
with patch.object(Path, "exists", return_value=False):
    spec.loader.exec_module(grok)

AGENT = {"id": "room-id", "name": "Demo", "harness": "temporal", "isGroup": True, "memberIds": ["a", "b"]}


def row(entry):
    return {"body": base64.b64encode(json.dumps(entry).encode()).decode()}


class NativeConversationTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(grok, "_decrypt_access_token", side_effect=AssertionError("No credentials")))
        self.stack.enter_context(patch.object(grok, "_gateway", side_effect=AssertionError("No gateway")))
        self.connect = self.stack.enter_context(patch.object(grok, "_connect"))
        self.stack.enter_context(patch.object(grok, "_native_running", return_value=False))

    def test_send_uses_native_identity_and_requires_explicit_acceptance(self):
        self.connect.return_value = {"delivery": "GROK_BOT_USER_MESSAGE_DELIVERY_ACCEPTED_TEMPORAL"}
        self.assertTrue(grok._send_message("synthetic", {}, AGENT, "Draft only"))
        call = self.connect.call_args.args
        self.assertEqual(call[:3], ("synthetic", "aiserver.v1.GrokBotService", "SendGrokBotUserMessage"))
        self.assertEqual(call[3]["agentId"], "room-id")
        self.assertEqual(call[3]["text"], "Draft only")
        self.assertTrue(call[3]["messageId"])
        self.assertEqual(call[3]["source"], "GROK_BOT_CLIENT_SURFACE_DESKTOP")
        for response in ({}, {"delivery": 0}, {"delivery": 4}):
            self.connect.return_value = response
            with self.assertRaises(grok.GrokBotError):
                grok._send_message("synthetic", {}, AGENT, "Draft only")

    def test_uncertain_send_checks_same_identity_without_resending(self):
        self.connect.side_effect = [OSError("synthetic failure"), {"status": "GROK_BOT_SEND_STATUS_ACCEPTED"}]
        self.assertTrue(grok._send_message("synthetic", {}, AGENT, "Draft only"))
        sent, status = [call.args for call in self.connect.call_args_list]
        self.assertEqual(status[2], "GetGrokBotSendStatus")
        self.assertEqual(sent[3]["messageId"], status[3]["messageId"])
        self.assertEqual(self.connect.call_count, 2)

    def test_uncertain_send_never_resends_when_status_unknown(self):
        self.connect.side_effect = [OSError("synthetic failure"), {"status": "GROK_BOT_SEND_STATUS_NOT_FOUND"}]
        with self.assertRaisesRegex(grok.GrokBotError, "not resent"):
            grok._send_message("synthetic", {}, AGENT, "Draft only")
        self.assertEqual([call.args[2] for call in self.connect.call_args_list], ["SendGrokBotUserMessage", "GetGrokBotSendStatus"])

    def test_reconciled_refusal_is_not_swallowed_as_unknown_delivery(self):
        for status in (3, "GROK_BOT_SEND_STATUS_REJECTED"):
            with self.subTest(status=status):
                self.connect.reset_mock()
                self.connect.side_effect = [OSError("synthetic failure"), {"status": status}]
                with self.assertRaisesRegex(grok.GrokBotError, "^Grok Bot refused the message\\.$"):
                    grok._send_message("synthetic", {}, AGENT, "Draft only")
                sent, checked = [call.args for call in self.connect.call_args_list]
                self.assertEqual(checked[2], "GetGrokBotSendStatus")
                self.assertEqual(sent[3]["messageId"], checked[3]["messageId"])

    def test_failed_or_malformed_status_lookup_stays_unknown_without_resending(self):
        for response in (OSError("synthetic failure"), grok.GrokBotError("status unavailable"),
                         ValueError("invalid JSON"), None, [], {}):
            with self.subTest(response=response):
                self.connect.reset_mock()
                self.connect.side_effect = [OSError("synthetic failure"), response]
                with self.assertRaisesRegex(grok.GrokBotError, "could not be confirmed.*not resent"):
                    grok._send_message("synthetic", {}, AGENT, "Draft only")
                self.assertEqual([call.args[2] for call in self.connect.call_args_list],
                                 ["SendGrokBotUserMessage", "GetGrokBotSendStatus"])

    def test_native_transcript_is_chronological_and_preserves_authors(self):
        first = {"id": "user-1", "kind": "message", "authorId": "user", "content": "Begin"}
        last = {"id": "bot-1", "kind": "send-message", "author": {"id": "a", "name": "Merchant"}, "message": {"type": "text", "content": "Offer"}}
        self.connect.return_value = {"entries": [row(last), row(first)]}
        self.assertEqual(grok._native_transcript("synthetic", "room-id", 80), [first, last])
        self.assertEqual(self.connect.call_args.args[3], {"agentId": "room-id", "limit": 80, "sessionId": ""})

    def test_transcript_preserves_streaming_to_avoid_final_action_loss(self):
        entries = [
            {"id": "partial", "kind": "send-message", "author": {"id": "a", "name": "Merchant"}, "text": "Thinking", "streaming": True},
            {"id": "complete", "kind": "send-message", "author": {"id": "b", "name": "Buyer"}, "text": "Final offer", "streaming": False},
        ]
        with patch.object(grok, "_session", return_value=("synthetic", {})), \
             patch.object(grok, "_resolve_agent", return_value=AGENT), \
             patch.object(grok, "_read_transcript", return_value=entries):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                grok.cmd_transcript(SimpleNamespace(id="room-id", name=None, limit=80))
        result = json.loads(stream.getvalue())
        self.assertIs(result["entries"][0]["streaming"], True)
        self.assertIs(result["entries"][1]["streaming"], False)

    def test_blob_reads_only_requested_paths_without_exposing_urls(self):
        blob_hash = "a" * 64
        entry = {"id": "bot-1", "kind": "send-message", "message": {"type": "text", "content": "Offer"}}
        signed_url = "https://storage.example.invalid/object?private=signature"
        self.connect.side_effect = [{"entries": [{"blobHash": blob_hash}]}, {"instructions": [
            {"relPath": "other/object", "url": "https://unrelated.invalid/"},
            {"relPath": "blobs/" + blob_hash, "url": signed_url}]}]
        with patch.object(grok, "_read_signed_blob", return_value=json.dumps(entry).encode()) as read:
            result = grok._native_transcript("synthetic", "room-id")
        self.assertEqual(result, [entry])
        read.assert_called_once_with(signed_url)
        self.assertNotIn(signed_url, json.dumps(result))

    def test_bad_blob_paths_and_missing_bodies_are_not_silently_dropped(self):
        for bad in ({"blobHash": "../../secret"}, {"bodyOmitted": True}, {"body": "not-base64"}):
            self.connect.return_value = {"entries": [bad]}
            with self.subTest(bad=bad), self.assertRaises(grok.GrokBotError):
                grok._native_transcript("synthetic", "room-id")

    def test_signed_blob_rejects_plain_http_before_network(self):
        with patch.object(grok.urllib.request, "urlopen", side_effect=AssertionError("No network")) as read:
            with self.assertRaises(grok.GrokBotError):
                grok._read_signed_blob("http://storage.example.invalid/object")
        read.assert_not_called()

    def test_group_info_finds_server_only_room(self):
        def native_record(agent_id, *, group=False):
            return {"agentId": agent_id, "id": "different-server-id", "name": agent_id,
                    "harness": "temporal", "kind": 2 if group else 1,
                    "memberAgentIds": ["a", "b"] if group else []}
        self.connect.return_value = {"agents": [native_record("room-id", group=True), native_record("a"), native_record("b")]}
        with patch.object(grok, "_session", return_value=("synthetic", {})), patch.object(grok, "_list_agents", return_value=[]):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                grok.cmd_group_info(SimpleNamespace(id="room-id", name=None))
        result = json.loads(stream.getvalue())
        self.assertEqual(result["group"]["id"], "room-id")
        self.assertEqual([member["id"] for member in result["members"]], ["a", "b"])

    def test_native_chat_with_unknown_runtime_does_not_claim_completed(self):
        with patch.object(grok, "_session", return_value=("synthetic", {})), \
             patch.object(grok, "_resolve_agent", return_value=AGENT), \
             patch.object(grok, "_read_transcript", return_value=[]), \
             patch.object(grok, "_send_message", return_value=True):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                grok.cmd_chat(SimpleNamespace(id="room-id", name=None, prompt="Draft", timeout=0, poll=1))
        self.assertTrue(json.loads(stream.getvalue())["stillRunning"])


class NativeChatTests(unittest.TestCase):
    def run_chat(self, states, snapshots, *, before=(), agent=None):
        clock = [0]
        def sleep(seconds):
            clock[0] += seconds
        reads = [list(before), *snapshots, snapshots[-1] if snapshots else list(before)]
        with patch.object(grok, "_session", return_value=("synthetic", {})), \
             patch.object(grok, "_resolve_agent", return_value=agent or AGENT), \
             patch.object(grok, "_read_transcript", side_effect=reads) as transcript, \
             patch.object(grok, "_send_message", return_value=True) as send, \
             patch.object(grok, "_native_running", side_effect=states) as runtime, \
             patch.object(grok, "_setup_roster", side_effect=AssertionError("Native chat must use live native status")), \
             patch.object(grok, "_decrypt_access_token", side_effect=AssertionError("No credentials")), \
             patch.object(grok, "_connect", side_effect=AssertionError("No network")), \
             patch.object(grok, "_gateway", side_effect=AssertionError("No gateway")), \
             patch.object(grok.time, "time", side_effect=lambda: clock[0]), \
             patch.object(grok.time, "sleep", side_effect=sleep):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                grok.cmd_chat(SimpleNamespace(id="room-id", name=None, prompt="Draft", timeout=len(states), poll=1))
        send.assert_called_once_with("synthetic", {}, agent or AGENT, "Draft")
        return json.loads(stream.getvalue()), runtime, transcript

    def reply(self, *, streaming=False, entry_id="reply"):
        return {"id": entry_id, "kind": "send-message", "text": "Offer",
                "author": {"id": "a", "name": "Merchant"}, "streaming": streaming}

    def test_server_backed_room_completes_on_authoritative_idle_and_final_reply(self):
        stale = {**AGENT, "isRunning": True, "isRunningTurn": True, "isComposingMessage": True}
        result, runtime, transcript = self.run_chat([False], [[self.reply()]], agent=stale)
        self.assertFalse(result["stillRunning"])
        self.assertIs(result["agent"]["isRunning"], False)
        self.assertFalse(result["newEntries"][0]["streaming"])
        runtime.assert_called_once_with("synthetic", "room-id")
        self.assertEqual(transcript.call_count, 2)

    def test_running_or_unknown_native_state_never_finishes_on_reply_alone(self):
        for state in (True, None):
            with self.subTest(state=state):
                result, runtime, _ = self.run_chat([state], [[self.reply()]], agent={**AGENT, "isRunning": False})
                self.assertTrue(result["stillRunning"])
                self.assertIs(result["agent"]["isRunning"], state)
                runtime.assert_called_once_with("synthetic", "room-id")

    def test_idle_before_delivery_or_only_old_messages_does_not_finish(self):
        old = {"entryId": "old", "kind": "send-message", "text": "Previous reply"}
        for snapshot in ([], [old], [{"id": "user", "kind": "message", "authorId": "user", "text": "Draft"}]):
            with self.subTest(snapshot=snapshot):
                result, _, _ = self.run_chat([False], [snapshot], before=[old])
                self.assertTrue(result["stillRunning"])

    def test_streaming_reply_or_another_streaming_group_member_prevents_completion(self):
        partial = self.reply(streaming=True)
        for snapshot in ([partial], [self.reply(entry_id="done"), partial]):
            with self.subTest(snapshot=snapshot):
                result, _, _ = self.run_chat([False], [snapshot])
                self.assertTrue(result["stillRunning"])
                self.assertTrue(result["newEntries"][-1]["streaming"])

    def test_streaming_update_of_same_entry_waits_for_final_reply(self):
        result, runtime, transcript = self.run_chat([False, False], [[self.reply(streaming=True)], [self.reply()]])
        self.assertFalse(result["stillRunning"])
        self.assertEqual(runtime.call_count, 2)
        self.assertEqual(transcript.call_count, 3)
        self.assertFalse(result["newEntries"][0]["streaming"])

    def test_running_group_is_polled_until_authoritative_idle(self):
        result, runtime, _ = self.run_chat([True, False], [[self.reply()], [self.reply()]])
        self.assertFalse(result["stillRunning"])
        self.assertEqual(runtime.call_count, 2)

    def test_zero_timeout_does_not_reuse_cached_idle_flag(self):
        result, runtime, _ = self.run_chat([], [], agent={**AGENT, "isRunning": False})
        self.assertTrue(result["stillRunning"])
        self.assertIsNone(result["agent"]["isRunning"])
        runtime.assert_not_called()


class NativeRuntimeTests(unittest.TestCase):
    def response(self, frames):
        payload = b''
        for frame in frames:
            body = json.dumps(frame).encode()
            payload += b'\x00' + len(body).to_bytes(4, 'big') + body
        return io.BytesIO(payload)

    def test_complete_snapshot_distinguishes_running_idle_and_other_agents(self):
        for live, expected in [([], False), ([{'agentId': 'room', 'isRunning': True}], True),
                               ([{'agentId': 'room', 'hasRunningSubagents': True}], True),
                               ([{'agentId': 'other', 'isRunning': True}], False)]:
            with self.subTest(live=live), patch.object(grok.urllib.request, 'urlopen', return_value=self.response([
                {'connected': {'streamId': 'synthetic'}}, {'agentState': {'snapshot': True, 'live': live}}
            ])) as request:
                self.assertIs(grok._native_running('synthetic', 'room'), expected)
                body = json.loads(request.call_args.args[0].data[5:])
                self.assertEqual(body['cursors'][0]['agentId'], 'room')

    def test_failure_or_incomplete_snapshot_is_unknown_not_idle(self):
        with patch.object(grok.urllib.request, 'urlopen', side_effect=TimeoutError()):
            self.assertIsNone(grok._native_running('synthetic', 'room'))
        with patch.object(grok.urllib.request, 'urlopen', return_value=self.response([
            {'agentState': {'snapshot': False, 'live': []}}
        ])):
            self.assertIsNone(grok._native_running('synthetic', 'room'))


if __name__ == "__main__":
    unittest.main()
