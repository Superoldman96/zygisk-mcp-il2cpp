"""Process routing wire/dispatcher tests over real loopback sockets (no game)."""
import json
import unittest
from unittest.mock import patch

from mcp.mcp_server import BridgeError, ConnectionConfig, FeatureRegistry, HookSocketClient, ToolDispatcher, TOOLS
from mcp.test_query_diagnostics import Peer, ok, stage

MAIN = {"pid": 1201, "uid": 10123, "process_name": "com.example.game", "session": "abcd-1201-100"}
CHILD = {"pid": 1202, "uid": 10123, "process_name": "com.example.game:minigame0", "session": "abcd-1202-101"}


def identity(target):
    return ("MCP_TARGET_INFO " + json.dumps(target) + "\n").encode()


class Registry:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []
        self.drop_after_write = False

    def __call__(self, wire):
        if wire == "MCP_TARGETS":
            return [ok(json.dumps({"protocol": 1, "processes": self.rows}))]
        if not wire.startswith("MCP_ROUTE "):
            return [b"ERR TEST_EXPECTED_ROUTE\n"]
        _, selected, command = wire.split(" ", 2)
        matches = self.rows if selected == "auto" else [t for t in self.rows if t["session"] == selected]
        if len(matches) != 1:
            return [b"ERR MULTIPLE_TARGET_PROCESSES\n" if len(matches) > 1 else b"ERR TARGET_PROCESS_EXITED\n"]
        target = matches[0]
        if command == "PING":
            return [identity(target), ok("PONG")]
        if command == "MCP_PROCESS_INFO":
            return [identity(target), ok(json.dumps(target))]
        self.executed.append((target["pid"], command))
        if self.drop_after_write:
            return [identity(target)]
        if command.startswith("MCP_QUERY_V1 "):
            return [identity(target), stage("method.return_type"), ok("{}")]
        return [identity(target), ok("{}")]


def dispatcher(peer):
    return ToolDispatcher(ConnectionConfig(port=peer.server_address[1], auto_adb_forward=False, timeout=2))


class ProcessToolsTests(unittest.TestCase):
    def test_tools_are_unique_and_use_connection_feature(self):
        names = [tool["name"] for tool in TOOLS]
        for name in ("process_list", "process_select", "process_current"):
            self.assertEqual(names.count(name), 1)
        registry = FeatureRegistry()
        registry.set("connection", False)
        service = ToolDispatcher(ConnectionConfig(auto_adb_forward=False), registry)
        with patch.object(HookSocketClient, "call") as client:
            for name in ("process_list", "process_select", "process_current"):
                with self.assertRaises(BridgeError):
                    service.call(name, {})
            client.assert_not_called()

    def test_list_does_not_select_or_notify(self):
        registry = Registry([MAIN, CHILD])
        with Peer(registry) as peer:
            service = dispatcher(peer)
            self.assertEqual(service.call("process_list", {})["processes"], [MAIN, CHILD])
            self.assertIsNone(service.config.target_session)
            self.assertEqual(peer.commands, ["MCP_TARGETS"])

    def test_explicit_subprocess_selection_routes_writes(self):
        registry = Registry([MAIN, CHILD])
        with Peer(registry) as peer:
            service = dispatcher(peer)
            result = service.call("process_select", {"process_name": CHILD["process_name"]})
            self.assertEqual(result["target_process"], CHILD)
            service.call("memory_write", {"address": "0x1000", "hex_bytes": "0100"})
            writes = [(pid, cmd) for pid, cmd in registry.executed if cmd.startswith("MEMORY_WRITE ")]
            self.assertEqual(writes, [(CHILD["pid"], "MEMORY_WRITE 0x1000 0100")])
            self.assertEqual(service.call("connection_info", {})["target_process"], CHILD)

    def test_sole_process_autopins_before_second_command(self):
        registry = Registry([CHILD])
        with Peer(registry) as peer:
            service = dispatcher(peer)
            result = service.call("ping", {})
            self.assertTrue(result["connected"])
            self.assertEqual(result["target_process"], CHILD)
            self.assertTrue(peer.commands[0].startswith("MCP_ROUTE auto UI_TOAST_NOTIFY "))
            self.assertEqual(peer.commands[-1], "MCP_ROUTE " + CHILD["session"] + " PING")

    def test_no_silent_retarget_after_exit_or_restart(self):
        registry = Registry([CHILD])
        with Peer(registry) as peer:
            service = dispatcher(peer)
            service.call("process_current", {})
            for replacement in (MAIN, dict(CHILD, session="abcd-1202-900")):
                registry.rows = [replacement]
                with self.assertRaisesRegex(BridgeError, "TARGET_PROCESS_EXITED"):
                    service.memory_write({"address": "0x1000", "hex_bytes": "01"})
            self.assertEqual(registry.executed, [])
            self.assertEqual(service.call("process_list", {})["processes"], registry.rows)
            service.call("process_select", {"pid": CHILD["pid"]})
            self.assertEqual(service.config.target_session, "abcd-1202-900")

    def test_ambiguous_selection_cannot_mutate(self):
        registry = Registry([MAIN, CHILD])
        with Peer(registry) as peer:
            service = dispatcher(peer)
            with self.assertRaisesRegex(BridgeError, "MULTIPLE_TARGET_PROCESSES"):
                service.memory_write({"address": "0x1000", "hex_bytes": "01"})
            for args in ({}, {"pid": True}, {"process_name": ""}, {"process_name": "com.example"}, {"pid": MAIN["pid"], "process_name": CHILD["process_name"]}):
                with self.assertRaises(BridgeError):
                    service.call("process_select", args)
            self.assertEqual(registry.executed, [])

    def test_failed_selection_preserves_previous(self):
        registry = Registry([MAIN, CHILD])
        with Peer(registry) as peer:
            service = dispatcher(peer)
            service.call("process_select", {"pid": MAIN["pid"]})
            with self.assertRaises(BridgeError):
                service.call("process_select", {"pid": 9999})
            self.assertEqual(service.config.target_process, MAIN)

    def test_settings_keep_target_unless_endpoint_changed(self):
        service = ToolDispatcher(ConnectionConfig(target_session=CHILD["session"], target_process=CHILD))
        service.configure_connection({"timeout": 10})
        self.assertEqual(service.config.target_process, CHILD)
        service.configure_connection({"port": 30000})
        self.assertIsNone(service.config.target_session)

    def test_disconnect_does_not_replay_mutation(self):
        registry = Registry([CHILD]); registry.drop_after_write = True
        with Peer(registry) as peer:
            service = dispatcher(peer)
            with self.assertRaises(BridgeError):
                service.memory_write({"address": "0x1000", "hex_bytes": "01"})
            self.assertEqual(len(registry.executed), 1)
            self.assertEqual(len(peer.commands), 1)

    def test_selected_identity_mismatch_and_missing_identity_fail_closed(self):
        for packet in ([identity(MAIN), ok("{}")], [ok("{}")], [b"ERR UNKNOWN_COMMAND\n"]):
            with self.subTest(packet=packet), Peer(lambda _: packet) as peer:
                client = HookSocketClient(ConnectionConfig(port=peer.server_address[1], auto_adb_forward=False,
                                                          target_session=CHILD["session"]))
                with self.assertRaises(BridgeError):
                    client.call("MEMORY_WRITE 0x1000 01")
                self.assertEqual(len(peer.commands), 1)

    def test_query_diagnostics_survive_route(self):
        registry = Registry([CHILD])
        with Peer(registry) as peer:
            client = HookSocketClient(ConnectionConfig(port=peer.server_address[1], auto_adb_forward=False), route_process=True)
            self.assertEqual(client.call("IL2CPP_METHODS 41 - 42 0 20"), "{}")
            self.assertIn("MCP_QUERY_V1 IL2CPP_METHODS", peer.commands[0])

    def test_raw_tool_cannot_override_selected_route(self):
        service = ToolDispatcher(ConnectionConfig())
        with patch.object(HookSocketClient, "call") as client:
            for command in ("MCP_ROUTE auto PING", "MCP_TARGETS", "MCP_TARGET_INFO {}"):
                with self.assertRaisesRegex(BridgeError, "internal envelope"):
                    service.raw_hook_call({"command": command})
            client.assert_not_called()

    def test_legacy_rejection_fallback_executes_inner_command_once(self):
        def respond(command):
            return [b"ERR UNKNOWN_COMMAND\n"] if command.startswith("MCP_ROUTE ") else [ok("{}")]
        with Peer(respond) as peer:
            service = dispatcher(peer)
            service.memory_write({"address": "0x1000", "hex_bytes": "01"})
            self.assertEqual(peer.commands, ["MCP_ROUTE auto MEMORY_WRITE 0x1000 01", "MEMORY_WRITE 0x1000 01"])


if __name__ == "__main__":
    unittest.main()
