"""Native group regression tests. No Keychain, network, files or bot actions."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "grokbot.py"
spec = importlib.util.spec_from_file_location("grokbot_groups", SCRIPT)
grokbot = importlib.util.module_from_spec(spec)
# Import-time version discovery also stays independent of the installed app.
with patch.object(Path, "exists", return_value=False):
    spec.loader.exec_module(grokbot)


def bot(bot_id, name=None, **extra):
    return {"id": bot_id, "name": name or bot_id, "isGroup": False,
            "viewerIsOwner": True, "harness": "box", **extra}


def room(room_id, name, ids, **extra):
    return {"id": room_id, "name": name, "isGroup": True,
            "memberIds": list(ids), "viewerIsOwner": True, "harness": "box", **extra}


class NativeGroupTests(unittest.TestCase):
    def setUp(self):
        self.agents = [bot("a", "A"), bot("b", "B"), bot("c", "C"), bot("d", "D")]
        self.native_agents = []
        self.gateway = Mock(side_effect=self.fake_gateway)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(grokbot, "_session", return_value=("synthetic-access", {"test": "box"})))
        self.stack.enter_context(patch.object(grokbot, "_setup_lock", side_effect=contextlib.nullcontext))
        self.stack.enter_context(patch.object(grokbot, "_list_agents", side_effect=lambda box: copy.deepcopy(self.agents)))
        self.stack.enter_context(patch.object(grokbot, "_gateway", self.gateway))
        self.connect = self.stack.enter_context(patch.object(grokbot, "_connect", side_effect=self.fake_connect))
        self.stack.enter_context(patch.object(grokbot, "_decrypt_access_token", side_effect=AssertionError("Credential access forbidden in tests")))
        self.stack.enter_context(patch.object(grokbot, "_native_running", return_value=False))

    def fake_connect(self, access, service, method, body):
        if method == "ListGrokBotAgents":
            return {"agents": copy.deepcopy(self.native_agents)}
        if method == "SetGrokBotRoomMembers":
            raw = next(agent for agent in self.native_agents if agent["agentId"] == body["agentId"])
            raw["memberAgentIds"] = list(body["memberAgentIds"])
            return {"agent": copy.deepcopy(raw)}
        if method == "CreateGrokBotRoom":
            raw = {"agentId": body["agentId"], "name": body["name"], "description": body["description"],
                   "title": body.get("title", ""), "harness": "temporal", "viewerIsOwner": True,
                   "kind": "GROK_BOT_AGENT_KIND_ROOM", "memberAgentIds": list(body["memberAgentIds"])}
            self.native_agents.append(raw)
            return {"agent": copy.deepcopy(raw)}
        raise AssertionError(f"Unexpected remote RPC: {method}")

    def rpc_mutations(self):
        return [call for call in self.connect.call_args_list if call.args[2] != "ListGrokBotAgents"]

    def assert_no_rpc_mutations(self):
        self.assertEqual(self.rpc_mutations(), [])

    def fake_gateway(self, box, route, body):
        if route == "/api/setGroupMembers":
            existing = next(agent for agent in self.agents if agent["id"] == body["id"])
            existing["memberIds"] = list(body["memberAgentIds"])
            return copy.deepcopy(existing)
        if route == "/api/createGroup":
            created = room("new-room", body["name"], body["memberAgentIds"])
            self.agents.append(created)
            return {"agent": copy.deepcopy(created)}
        raise AssertionError(f"Unexpected gateway action: {route}")

    def output(self, command, args):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            command(args)
        return json.loads(stream.getvalue())

    def create(self, ids=None, *, name="Demo room", reuse=False, names=None):
        return self.output(grokbot.cmd_group_create, SimpleNamespace(
            name=name, description="Fictional test room", member_id=ids if ids is not None else ["a", "b"],
            member_name=names, reuse=reuse))


    def remove(self, ids, *, group_id="existing"):
        return self.output(grokbot.cmd_group_remove_member, SimpleNamespace(id=group_id, name=None, member_id=ids))

    def native_room(self, ids=None):
        ids = ids or ["a", "b", "c", "d"]
        for agent in self.agents:
            agent["harness"] = "temporal"
            self.native_agents.append({"agentId": agent["id"], "name": agent["name"], "harness": "temporal", "kind": 1, "viewerIsOwner": True})
        self.agents.append(room("existing", "Demo room", ids, harness="temporal", isRunning=False))
        self.native_agents.append({"agentId": "existing", "name": "Demo room", "harness": "temporal", "kind": 2, "memberAgentIds": list(ids), "viewerIsOwner": True})

    def test_native_removal_updates_real_membership_and_keeps_bots(self):
        self.native_room()
        result = self.remove(["c", "d"])
        self.assertEqual(result["group"]["memberIds"], ["a", "b"])
        self.assertEqual(result["removedMemberIds"], ["c", "d"])
        self.assertTrue(result["removed"])
        self.assertEqual(len(self.native_agents), 5)
        mutations = self.rpc_mutations()
        self.assertEqual(len(mutations), 1)
        self.assertEqual(mutations[0].args[2:], ("SetGrokBotRoomMembers", {"agentId": "existing", "memberAgentIds": ["a", "b"]}))
        self.gateway.assert_not_called()

    def test_removal_uses_native_membership_despite_stale_gateway(self):
        self.native_room()
        self.native_agents[-1]["memberAgentIds"] = ["a", "b", "d"]
        result = self.remove(["d"])
        self.assertEqual(result["group"]["memberIds"], ["a", "b"])
        self.assertNotIn("c", self.rpc_mutations()[0].args[3]["memberAgentIds"])
        info = self.output(grokbot.cmd_group_info, SimpleNamespace(id="existing", name=None))
        self.assertEqual(info["group"]["memberIds"], ["a", "b"])
        self.assertIs(info["group"]["isRunning"], False)

    def test_box_removal_is_idempotent_and_can_keep_one_member(self):
        self.agents.append(room("existing", "Demo room", ["a", "b"]))
        first = self.remove(["b"])
        self.assertEqual(first["group"]["memberIds"], ["a"])
        self.assertEqual([member["id"] for member in first["members"]], ["a"])
        again = self.remove(["b"])
        self.assertFalse(again["removed"])
        self.assertEqual(again["alreadyAbsentMemberIds"], ["b"])
        self.gateway.assert_called_once()
        info = self.output(grokbot.cmd_group_info, SimpleNamespace(id="existing", name=None))
        self.assertEqual(info["group"]["memberIds"], ["a"])

    def test_native_already_absent_is_noop_using_fresh_members(self):
        self.native_room(["a", "b"])
        result = self.remove(["c"])
        self.assertFalse(result["removed"])
        self.assertEqual(result["alreadyAbsentMemberIds"], ["c"])
        self.assert_no_rpc_mutations()
        self.gateway.assert_not_called()

    def test_remove_rejects_last_member_unknown_nonowned_and_duplicate_ids(self):
        self.agents.append(room("existing", "Demo room", ["a", "b"]))
        for ids in (["a", "b"], ["missing"], ["b", "b"], [], ["existing"]):
            with self.subTest(ids=ids), self.assertRaises(grokbot.GrokBotError):
                self.remove(ids)
        self.agents[1]["viewerIsOwner"] = False
        with self.assertRaises(grokbot.GrokBotError):
            self.remove(["b"])
        self.agents[1]["viewerIsOwner"] = True
        self.agents[-1]["viewerIsOwner"] = False
        with self.assertRaises(grokbot.GrokBotError):
            self.remove(["b"])
        self.gateway.assert_not_called()
        self.assert_no_rpc_mutations()

    def test_native_mutation_does_not_fall_back_when_roster_cannot_be_read(self):
        self.native_room()
        self.connect.side_effect = OSError("Synthetic unavailable roster")
        with self.assertRaisesRegex(grokbot.GrokBotError, "could not be read"):
            self.remove(["b"])
        self.gateway.assert_not_called()
        self.assert_no_rpc_mutations()

    def test_native_mutation_rejects_group_missing_from_authoritative_roster(self):
        self.native_room()
        self.native_agents.pop()
        with self.assertRaises(grokbot.GrokBotError):
            self.remove(["b"])
        self.gateway.assert_not_called()
        self.assert_no_rpc_mutations()

    def test_uncertain_removal_reconciles_membership_without_resending(self):
        self.native_room()
        def lost_response(access, service, method, body):
            result = self.fake_connect(access, service, method, body)
            if method == "SetGrokBotRoomMembers":
                raise OSError("Synthetic lost removal response")
            return result
        self.connect.side_effect = lost_response
        result = self.remove(["b", "c", "d"])
        self.assertEqual(result["group"]["memberIds"], ["a"])
        self.assertEqual(len(self.rpc_mutations()), 1)
        self.gateway.assert_not_called()

    def test_unconfirmed_removal_is_not_resent_or_reported_as_success(self):
        self.native_room()
        def failed_response(access, service, method, body):
            if method == "SetGrokBotRoomMembers":
                return {"agent": {**self.native_agents[-1], "agentId": "wrong-room"}}
            return self.fake_connect(access, service, method, body)
        self.connect.side_effect = failed_response
        with self.assertRaisesRegex(grokbot.GrokBotError, "was not resent"):
            self.remove(["b"])
        self.assertEqual(len(self.rpc_mutations()), 1)
        self.gateway.assert_not_called()

    def test_removal_accepts_server_reordering_of_exact_remaining_members(self):
        self.native_room()
        def reordered(access, service, method, body):
            response = self.fake_connect(access, service, method, body)
            if method == "SetGrokBotRoomMembers":
                response["agent"]["memberAgentIds"].reverse()
            return response
        self.connect.side_effect = reordered
        result = self.remove(["d"])
        self.assertEqual(set(result["group"]["memberIds"]), {"a", "b", "c"})
        self.assertEqual(len(self.rpc_mutations()), 1)

    def test_creates_native_group_with_exact_existing_members(self):
        result = self.create(["a", "c", "b"])
        self.assertTrue(result["group"]["isGroup"])
        self.assertEqual(result["group"]["memberIds"], ["a", "c", "b"])
        self.assertEqual([member["id"] for member in result["members"]], ["a", "c", "b"])
        self.assertTrue(result["created"])
        self.gateway.assert_called_once()
        self.assertEqual(self.gateway.call_args.args[1], "/api/createGroup")
        self.assertEqual(self.gateway.call_args.args[2]["memberAgentIds"], ["a", "c", "b"])

    def test_reuses_only_exact_name_and_members_independent_of_order(self):
        self.agents.append(room("existing", "Demo room", ["b", "a"]))
        result = self.create(["a", "b"], name="demo ROOM", reuse=True)
        self.assertEqual(result["group"]["id"], "existing")
        self.assertFalse(result["created"])
        self.gateway.assert_not_called()
        with self.assertRaises(grokbot.GrokBotError):
            self.create(["a", "c"], reuse=True)
        self.gateway.assert_not_called()

    def test_reuse_rejects_malformed_duplicate_group_membership(self):
        self.agents.append(room("malformed", "Demo room", ["a", "b", "a"]))
        with self.assertRaises(grokbot.GrokBotError):
            self.create(["a", "b"], reuse=True)
        self.gateway.assert_not_called()

    def test_duplicate_unknown_or_group_members_are_rejected_before_creation(self):
        self.agents.append(room("group-member", "Another room", ["a", "b"]))
        for ids in (["a", "a"], ["a"], ["a", "missing"], ["a", "group-member"], [str(i) for i in range(7)]):
            with self.subTest(ids=ids), self.assertRaises(grokbot.GrokBotError):
                self.create(ids)
        self.gateway.assert_not_called()

    def test_nonowned_members_and_groups_are_not_reused(self):
        self.agents[0]["viewerIsOwner"] = False
        with self.assertRaises(grokbot.GrokBotError):
            self.create()
        self.agents[0]["viewerIsOwner"] = True
        self.agents.append(room("shared", "Demo room", ["a", "b"], viewerIsOwner=False))
        with self.assertRaises(grokbot.GrokBotError):
            self.create(reuse=True)
        self.gateway.assert_not_called()

    def test_unknown_harness_is_rejected_before_group_mutation(self):
        for harness in ("unverified-future-harness", None, ""):
            self.agents[0]["harness"] = harness
            with self.subTest(harness=harness), self.assertRaises(grokbot.GrokBotError):
                self.create()
        self.gateway.assert_not_called()
        self.assert_no_rpc_mutations()

    def test_ambiguous_member_or_room_name_never_chooses_first(self):
        self.agents.append(bot("other-a", "A"))
        with self.assertRaises(grokbot.GrokBotError):
            self.create([], names=["A", "B"])
        self.agents.extend([room("r1", "Demo room", ["a", "b"]), room("r2", "demo ROOM", ["a", "b"])])
        with self.assertRaises(grokbot.GrokBotError):
            self.create(reuse=True)
        self.gateway.assert_not_called()

    def test_uncertain_create_reconciles_roster_without_resending(self):
        def lost_response(box, route, body):
            self.fake_gateway(box, route, body)
            raise OSError("Synthetic connection dropped after creation")
        self.gateway.side_effect = lost_response
        result = self.create()
        self.assertEqual(result["group"]["id"], "new-room")
        self.assertTrue(result["created"])
        self.gateway.assert_called_once()

    def test_uncertain_create_without_roster_confirmation_does_not_retry(self):
        self.gateway.side_effect = OSError("Synthetic connection dropped")
        with self.assertRaisesRegex(grokbot.GrokBotError, "could not be confirmed"):
            self.create()
        self.gateway.assert_called_once()
        self.assertFalse(any(agent["isGroup"] for agent in self.agents))

    def test_success_response_must_match_fresh_roster_identity(self):
        def wrong_id(box, route, body):
            self.fake_gateway(box, route, body)
            return {"agent": room("different", body["name"], body["memberAgentIds"])}
        self.gateway.side_effect = wrong_id
        with self.assertRaisesRegex(grokbot.GrokBotError, "could not be verified"):
            self.create()
        self.gateway.assert_called_once()

    def test_temporal_members_use_native_room_rpc_and_roundtrip_identity(self):
        self.agents[0]["harness"] = self.agents[1]["harness"] = "temporal"
        self.native_agents = [
            {"agentId": agent["id"], "name": agent["name"], "harness": "temporal",
             "kind": 1, "viewerIsOwner": True}
            for agent in self.agents[:2]
        ]
        result = self.create()
        mutations = self.rpc_mutations()
        self.assertEqual(len(mutations), 1)
        self.assertEqual(mutations[0].args[2], "CreateGrokBotRoom")
        payload = mutations[0].args[3]
        self.assertEqual(payload["memberAgentIds"], ["a", "b"])
        self.assertEqual(payload["humanMemberUserIds"], [])
        self.assertEqual(result["group"]["id"], payload["agentId"])
        self.gateway.assert_not_called()

    def test_temporal_lost_response_reconciles_without_resending(self):
        self.agents[0]["harness"] = self.agents[1]["harness"] = "temporal"
        self.native_agents = [
            {"agentId": agent["id"], "name": agent["name"], "harness": "temporal",
             "kind": 1, "viewerIsOwner": True}
            for agent in self.agents[:2]
        ]
        def lost_response(access, service, method, body):
            result = self.fake_connect(access, service, method, body)
            if method == "CreateGrokBotRoom":
                raise OSError("Synthetic lost native room response")
            return result
        self.connect.side_effect = lost_response
        result = self.create()
        mutations = self.rpc_mutations()
        self.assertEqual(len(mutations), 1)
        self.assertEqual(result["group"]["id"], mutations[0].args[3]["agentId"])
        self.gateway.assert_not_called()

    def test_mixed_runtime_group_is_rejected_before_mutation(self):
        self.agents[0]["harness"] = "temporal"
        with self.assertRaises(grokbot.GrokBotError):
            self.create()
        self.gateway.assert_not_called()
        self.assert_no_rpc_mutations()


if __name__ == "__main__":
    unittest.main()
