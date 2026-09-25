"""Native logic MCP protocol tests; no Android build or target connection."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from mcp import logic_tools as lt
from mcp.mcp_server import BridgeError, ConnectionConfig, ToolDispatcher, TOOLS


class NativeLogicToolsTests(unittest.TestCase):
    def setUp(self):
        self.dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))

    def test_all_tools_dispatch_and_explain_themselves(self):
        examples = {
            "logic_program_schema": {},
            "logic_program_validate": {"descriptor": {"id": "counter", "steps": []}},
            "logic_program_set": {"descriptor": {"id": "counter", "steps": [], "enabled": False}},
            "logic_program_list": {},
            "logic_program_get": {"id": "counter"},
            "logic_program_control": {"id": "counter", "operation": "restart"},
            "logic_program_remove": {"id": "counter"},
        }
        self.assertEqual(set(examples), set(lt.BY_NAME))
        names = [tool["name"] for tool in TOOLS]
        self.assertEqual(len(names), len(set(names)))
        for name, arguments in examples.items():
            with self.subTest(name=name), patch.object(self.dispatcher, "_json_call", return_value={"success": True}) as native, patch.object(self.dispatcher, "_notify_mcp_call"):
                self.assertEqual(self.dispatcher.call(name, arguments), {"success": True})
                native.assert_called_once_with(lt.encode(name, arguments))
                self.assertNotIn("\n", native.call_args.args[0])
                help_result = self.dispatcher.debug_help({"command": name})
                self.assertEqual(help_result["inputSchema"], lt.BY_NAME[name]["inputSchema"])

    def test_definition_roundtrip_preserves_unicode_and_symbolic_sources(self):
        definition = {
            "id": "generic_reader", "title": "自定义逻辑\n编辑器", "auto_start": True,
            "sources": [{"id": "items", "image": "Example.dll", "namespace": "Demo", "class": "Item",
                         "select_new": False, "refresh_ms": 250}],
            "steps": [{"op": "log", "value": {"literal": {"text": "中文😀", "address": "0x123456789abcdef0"}}}],
        }
        command, data = lt.encode("logic_program_set", {"descriptor": definition}).split()
        self.assertEqual(command, "LOGIC_PROGRAM_SET")
        self.assertEqual(json.loads(bytes.fromhex(data)), definition)

    def test_invalid_descriptors_never_reach_transport(self):
        invalid = [
            {"id": "a:b", "steps": []},  # Source group separators cannot collide.
            {"id": "x\nLOGIC_PROGRAM_LIST", "steps": []},
            {"id": "a" * 25, "steps": []},
            {"id": "x", "steps": [], "enabled": "true"},
            {"id": "x", "steps": [], "interval_ms": 0},
            {"id": "x", "steps": [], "max_steps": True},
            {"id": "x", "steps": [], "budget_us": 12001},
            {"id": "x", "steps": [], "allow_game_actions": True},
            {"id": "x", "steps": [], "sources": [{"id": "s", "image": "", "class": "Item"}]},
            {"id": "x", "steps": [], "sources": [{"id": "s:t", "image": "x.dll", "class": "Item"}]},
            {"id": "x", "steps": [{"op": "log", "value": float("nan")}]},
            {"id": "x", "steps": [{"op": "log", "value": {"not_json"}}]},
            {"id": "x", "steps": [{"op": "log", "value": "中" * 23000}]},
        ]
        for descriptor in invalid:
            with self.subTest(descriptor=str(descriptor)[:120]), patch.object(self.dispatcher, "_json_call") as native:
                with self.assertRaises(BridgeError):
                    self.dispatcher._logic_call("logic_program_set", {"descriptor": descriptor})
                native.assert_not_called()

    def test_nested_and_wide_definitions_are_bounded(self):
        nested = None
        for _ in range(35):
            nested = {"nested": nested}
        for value in (nested, [None] * 16385):
            with self.subTest(shape=type(value).__name__), self.assertRaises(ValueError):
                lt.encode("logic_program_set", {"descriptor": {"id": "x", "steps": [{"op": "log", "value": value}]}})

    def test_tool_and_raw_transport_obey_feature_switches(self):
        self.dispatcher.registry.set("memory_read", False)
        with patch.object(self.dispatcher, "_json_call") as native, patch.object(self.dispatcher, "_client") as client:
            with self.assertRaises(BridgeError):
                self.dispatcher.call("logic_program_set", {"descriptor": {"id": "x", "steps": []}})
            with self.assertRaises(BridgeError):
                self.dispatcher.raw_hook_call({"command": "LOGIC_PROGRAM_SET 7b7d"})
            native.assert_not_called()
            client.assert_not_called()
        self.assertEqual(lt.features("logic_program_validate"), ("overlay_ui",))
        self.assertIsNone(lt.native_features("PING"))
        for name in lt.BY_NAME:
            self.assertNotIn("lua", lt.features(name))
            self.assertEqual(lt.native_features(name.upper()), lt.features(name))

    def test_readonly_annotations_match_operations(self):
        readonly = {"logic_program_schema", "logic_program_validate", "logic_program_list", "logic_program_get"}
        for name, tool in lt.BY_NAME.items():
            self.assertEqual(tool["annotations"]["readOnlyHint"], name in readonly)

    def test_functions_and_explicit_calls_roundtrip(self):
        descriptor = {"id": "flow", "allow_calls": True, "functions": {
            "twice": {"params": ["n"], "steps": [{"op": "return", "value": {"op": "mul", "args": [{"var": "n"}, 2]}}]}},
            "steps": [{"op": "emit", "kind": "call", "target": "once", "value": {
                "image": "Example.dll", "class": "Counter", "token": 100663297, "arguments": [3]}}]}
        wire = lt.encode("logic_program_set", {"descriptor": descriptor})
        self.assertEqual(json.loads(bytes.fromhex(wire.split()[1])), descriptor)
        self.assertIn("il2cpp_invoke", lt.features("logic_program_set"))
        self.dispatcher.registry.set("il2cpp_invoke", False)
        with patch.object(self.dispatcher, "_json_call") as native:
            with self.assertRaises(BridgeError):
                self.dispatcher.call("logic_program_set", {"descriptor": descriptor})
            native.assert_not_called()


if __name__ == "__main__":
    unittest.main()
