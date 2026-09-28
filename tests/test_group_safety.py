"""Roster and creation safety regressions; all remote/credential access is mocked."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "grokbot.py"
spec = importlib.util.spec_from_file_location("grokbot_group_safety", SCRIPT)
grokbot = importlib.util.module_from_spec(spec)
with patch.object(Path, "exists", return_value=False):
    spec.loader.exec_module(grokbot)


def bot(agent_id, *, harness="temporal"):
    return {"id": agent_id, "name": agent_id.upper(), "isGroup": False,
            "harness": harness, "viewerIsOwner": True}


def room(member_ids=("a", "b"), *, harness="temporal"):
    return {"id": "room-id", "name": "Demo room", "isGroup": True,
            "harness": harness, "viewerIsOwner": True,
            "memberIds": list(member_ids), "isRunning": False}


def native(agent_id, member_ids=None):
    return {"agentId": agent_id, "name": "Demo room" if member_ids is not None else agent_id.upper(),
            "harness": "temporal", "viewerIsOwner": True,
            "kind": 2 if member_ids is not None else 1,
            "memberAgentIds": list(member_ids or [])}


class NativeGroupSafetyTests(unittest.TestCase):
    def setUp(self):
        self.rows = [bot("a"), bot("b")]
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(grokbot, "_list_agents", side_effect=lambda _: copy.deepcopy(self.rows)))
        self.stack.enter_context(patch.object(grokbot, "_session", return_value=("synthetic", {})))
        self.stack.enter_context(patch.object(grokbot, "_decrypt_access_token", side_effect=AssertionError("No credentials in tests")))
        self.stack.enter_context(patch.object(grokbot.urllib.request, "urlopen", side_effect=AssertionError("No live network in tests")))
        self.gateway = self.stack.enter_context(patch.object(grokbot, "_gateway", side_effect=AssertionError("Unexpected gateway mutation")))
        self.running = self.stack.enter_context(patch.object(grokbot, "_native_running", side_effect=AssertionError("Unexpected runtime read")))
        self.connect = self.stack.enter_context(patch.object(grokbot, "_connect"))

    def create(self, *, reuse=False, access="synthetic"):
        return grokbot._create_group({}, name="Demo room", description="Mocked test group",
                                     ids=["a", "b"], reuse=reuse, access=access)

    def mutations(self):
        return [call for call in self.connect.call_args_list if call.args[2] != "ListGrokBotAgents"]

    def test_failed_native_roster_never_returns_cached_temporal_membership(self):
        self.rows.append(room())
        for error in (OSError("Unavailable"), grokbot.GrokBotError("Unavailable")):
            for strict in (False, True):
                with self.subTest(error=type(error).__name__, strict=strict):
                    self.connect.side_effect = error
                    with self.assertRaisesRegex(grokbot.GrokBotError, "could not be read"):
                        grokbot._setup_roster({}, "synthetic", strict_native=strict)
        self.gateway.assert_not_called()

    def test_malformed_native_roster_fails_closed_without_partial_merge(self):
        self.rows.append(room())
        for response in (None, [], {"agents": None}, {"agents": [None]},
                         {"agents": [native("a"), None]},
                         {"agents": [native("a"), native("a")]}):
            with self.subTest(response=response):
                self.connect.return_value = response
                with self.assertRaisesRegex(grokbot.GrokBotError, "could not be read"):
                    grokbot._setup_roster({}, "synthetic")
        self.assertEqual(self.rows[-1]["memberIds"], ["a", "b"])

    def test_no_access_cannot_use_cached_native_roster(self):
        with self.assertRaisesRegex(grokbot.GrokBotError, "signed-in"):
            grokbot._setup_roster({}, None)
        self.connect.assert_not_called()

    def test_box_only_roster_retains_legacy_fallback_without_partial_native_rows(self):
        self.rows = [bot("a", harness="box"), bot("b", harness="box"), room(harness="box")]
        for response in (None, {"agents": [native("new-native-bot"), None]}):
            self.connect.return_value = response
            self.assertEqual(grokbot._setup_roster({}, "synthetic"), self.rows)
        self.connect.side_effect = OSError("Native endpoint unsupported")
        self.assertEqual(grokbot._setup_roster({}, "synthetic"), self.rows)
        self.assertEqual(grokbot._setup_roster({}, None), self.rows)
        with self.assertRaisesRegex(grokbot.GrokBotError, "could not be read"):
            grokbot._setup_roster({}, "synthetic", strict_native=True)

    def test_authoritative_native_roster_replaces_membership_and_drops_missing_rooms(self):
        self.rows.append(room())
        self.connect.return_value = {"agents": [native("a"), native("b"), native("room-id", ["a"])]}
        result = grokbot._setup_roster({}, "synthetic")
        found = next(row for row in result if row["id"] == "room-id")
        self.assertEqual(found["memberIds"], ["a"])
        self.assertIs(found["isRunning"], False)
        self.connect.return_value = {"agents": [native("a"), native("b")]}
        self.assertNotIn("room-id", [row["id"] for row in grokbot._setup_roster({}, "synthetic")])

    def test_stale_group_reuse_fails_before_creation_if_native_read_fails(self):
        self.rows.append(room())
        self.connect.side_effect = OSError("No authoritative membership")
        with self.assertRaisesRegex(grokbot.GrokBotError, "could not be read"):
            self.create(reuse=True)
        self.assertEqual(self.mutations(), [])
        self.gateway.assert_not_called()

    def test_stale_group_reuse_cannot_override_changed_authoritative_membership(self):
        self.rows.append(room())
        self.connect.return_value = {"agents": [native("a"), native("b"), native("room-id", ["a"])]}
        with self.assertRaisesRegex(grokbot.GrokBotError, "different conversation or member set"):
            self.create(reuse=True)
        self.assertEqual(self.mutations(), [])

    def test_group_info_does_not_report_stale_membership_when_native_read_fails(self):
        self.rows.append(room())
        self.connect.side_effect = OSError("No authoritative membership")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaisesRegex(grokbot.GrokBotError, "could not be read"):
            grokbot.cmd_group_info(SimpleNamespace(id="room-id", name=None))
        self.assertEqual(output.getvalue(), "")
        self.running.assert_not_called()

    def test_box_only_group_reuse_and_info_work_without_native_endpoint(self):
        self.rows = [bot("a", harness="box"), bot("b", harness="box"), room(harness="box")]
        self.connect.side_effect = OSError("Native endpoint unsupported")
        reused = self.create(reuse=True)
        self.assertFalse(reused["created"])
        self.assertEqual(reused["group"]["memberIds"], ["a", "b"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            grokbot.cmd_group_info(SimpleNamespace(id="room-id", name=None))
        self.assertEqual(json.loads(output.getvalue())["group"]["memberIds"], ["a", "b"])
        self.assertEqual(self.mutations(), [])
        self.gateway.assert_not_called()
        self.running.assert_not_called()

    def creation_rpc(self, malformed, *, commit):
        authoritative = [native("a"), native("b")]

        def rpc(access, service, method, payload):
            if method == "ListGrokBotAgents":
                return {"agents": copy.deepcopy(authoritative)}
            if method == "CreateGrokBotRoom":
                if commit:
                    authoritative.append(native(payload["agentId"], payload["memberAgentIds"]))
                return malformed
            raise AssertionError(f"Unexpected RPC: {method}")
        self.connect.side_effect = rpc

    def test_malformed_creation_response_reconciles_success_without_resending(self):
        for response in (None, [], "invalid", {}, {"agent": []}):
            with self.subTest(response=response):
                self.connect.reset_mock()
                self.creation_rpc(response, commit=True)
                result = self.create()
                self.assertTrue(result["created"])
                mutations = self.mutations()
                self.assertEqual(len(mutations), 1)
                self.assertEqual(mutations[0].args[2], "CreateGrokBotRoom")
                self.assertEqual(result["group"]["id"], mutations[0].args[3]["agentId"])
                self.assertEqual(result["group"]["memberIds"], ["a", "b"])
                self.assertEqual([call.args[2] for call in self.connect.call_args_list],
                                 ["ListGrokBotAgents", "CreateGrokBotRoom", "ListGrokBotAgents"])
        self.gateway.assert_not_called()

    def test_unconfirmed_malformed_creation_is_never_resent(self):
        for response in (None, [], "invalid", {}, {"agent": []}):
            with self.subTest(response=response):
                self.connect.reset_mock()
                self.creation_rpc(response, commit=False)
                with self.assertRaisesRegex(grokbot.GrokBotError, "did not resend"):
                    self.create()
                self.assertEqual(len(self.mutations()), 1)
                self.assertEqual([call.args[2] for call in self.connect.call_args_list],
                                 ["ListGrokBotAgents", "CreateGrokBotRoom", "ListGrokBotAgents"])
        self.gateway.assert_not_called()


if __name__ == "__main__":
    unittest.main()
