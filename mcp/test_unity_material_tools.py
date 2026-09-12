"""Offline protocol/feature tests; never connects to Unity or modifies a renderer."""
import json
import unittest
from unittest.mock import patch

from mcp import workspace_tools as wt
from mcp.mcp_server import ToolDispatcher, ConnectionConfig, BridgeError


class UnityMaterialToolsTests(unittest.TestCase):
    def decode(self, name, args):
        outer, command, payload = wt.encode(name, args).split()
        self.assertEqual(outer, "WORKSPACE_QUERY")
        return command, bytes.fromhex(payload).decode().split()

    def test_resources_and_material_pages_are_separate_commands(self):
        command, args = self.decode("unity_object_resources", {"address": "4660", "offset": 128, "limit": 128})
        self.assertEqual(command, "UNITY_OBJECT_RESOURCES")
        self.assertEqual(args, ["0x1234", "128", "128"])
        command, args = self.decode("unity_material_properties", {"renderer": "4660", "material_index": 2})
        self.assertEqual(command, "UNITY_MATERIAL_PROPERTIES")
        self.assertEqual(args, ["0x1234", "2", "0", "32"])

    def test_values_remain_typed_and_names_are_hex_encoded(self):
        for value in (0.5, -1, 2147483647, [0, 1, 0, 1], [-1, 2, 3, 4], "0x2000"):
            with self.subTest(value=value):
                command, args = self.decode("unity_material_set_property", {"renderer": "0x1000", "material_index": 1, "property": "_颜色", "value": value})
                self.assertEqual(command, "UNITY_MATERIAL_SET")
                self.assertEqual(args[:2], ["0x1000", "1"])
                self.assertEqual(bytes.fromhex(args[2]).decode(), "_颜色")
                self.assertEqual(json.loads(bytes.fromhex(args[3])), value)

    def test_renderer_discovery_reuses_resource_tool_with_candidate_cursor(self):
        command, args = self.decode("unity_object_resources", {"mode": "renderers", "address": "0", "query": "NPC 材质", "offset": 8192, "limit": 128})
        self.assertEqual(command, "UNITY_RENDERERS")
        self.assertEqual(args[:1], ["0x0"])
        self.assertEqual(bytes.fromhex(args[1]).decode(), "NPC 材质")
        self.assertEqual(args[2:], ["true", "8192", "128"])
        command, args = self.decode("unity_object_resources", {"mode": "renderers", "address": "4660", "include_inactive": False})
        self.assertEqual(args, ["0x1234", "-", "false", "0", "32"])

    def test_discovery_filters_are_not_silently_ignored_by_object_mode(self):
        for changes in ({"query": "NPC"}, {"include_inactive": True}, {"offset": 4097}, {"mode": "unsupported"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                wt.encode("unity_object_resources", {"address": "0x1000", **changes})
        for changes in ({"offset": -1}, {"offset": 10000001}, {"limit": 129}, {"include_inactive": "true"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                wt.encode("unity_object_resources", {"address": "0", "mode": "renderers", **changes})

    def test_discovery_is_readonly_and_obeys_same_feature_gate(self):
        dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        dispatcher.registry.set("rendering", False)
        with patch.object(dispatcher, "_json_call") as native:
            with self.assertRaises(BridgeError):
                dispatcher.call("unity_object_resources", {"address": "0", "mode": "renderers"})
            native.assert_not_called()
        self.assertIn("rendering", wt.native_features("UNITY_RENDERERS"))
        self.assertNotIn("memory_write", wt.native_features("UNITY_RENDERERS"))
        description = wt.BY_NAME["unity_object_resources"]["description"]
        for word in ("mode=renderers", "next_offset", "no matches", "does not add ESP"):
            self.assertIn(word, description)

    def test_restore_has_no_implicit_set_or_replay(self):
        command, args = self.decode("unity_material_restore", {"renderer": "0x1000", "material_index": 3})
        self.assertEqual(command, "UNITY_MATERIAL_RESTORE")
        self.assertEqual(args, ["0x1000", "3"])
        dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        with patch.object(dispatcher, "_json_call", side_effect=BridgeError("target disconnected")) as native:
            with self.assertRaises(BridgeError):
                dispatcher._workspace_call("unity_material_restore", {"renderer": "0x1000"})
            self.assertEqual(native.call_count, 1)

    def test_invalid_values_rejected_before_transport(self):
        dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        base = {"renderer": "0x1000", "property": "_Color", "value": [0, 1, 0, 1]}
        invalid = [{"value": v} for v in (True, None, {}, [], [1, 2, 3], [1, 2, 3, 4, 5],
                   [0, 1, True, 0], float("nan"), float("inf"), 1e21, "0x0", "not-an-address")]
        invalid += [{"renderer": "0"}, {"material_index": -1}, {"material_index": 256},
                    {"property": ""}, {"property": "x\0y"}, {"shader": "replacement"}]
        for change in invalid:
            with self.subTest(change=change), patch.object(dispatcher, "_json_call") as native:
                with self.assertRaises(BridgeError):
                    dispatcher._workspace_call("unity_material_set_property", {**base, **change})
                native.assert_not_called()

    def test_resource_query_limits(self):
        for name, args in (("unity_object_resources", {"address": "0"}),
                           ("unity_object_resources", {"address": "0x1000", "limit": 129}),
                           ("unity_material_properties", {"renderer": "0x1000", "offset": 4097})):
            with self.subTest(name=name, args=args), self.assertRaises(ValueError):
                wt.encode(name, args)

    def test_write_and_restore_obey_memory_write_gate(self):
        dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        dispatcher.registry.set("memory_write", False)
        for name, args in (("unity_material_set_property", {"renderer": "0x1000", "property": "_Float", "value": 1}),
                           ("unity_material_restore", {"renderer": "0x1000"})):
            with self.subTest(name=name), patch.object(dispatcher, "_json_call") as native:
                with self.assertRaises(BridgeError):
                    dispatcher.call(name, args)
                native.assert_not_called()
        self.assertNotIn("memory_write", wt.features("unity_object_resources"))
        self.assertNotIn("memory_write", wt.features("unity_material_properties"))

    def test_native_and_raw_envelopes_cannot_bypass_rendering_gate(self):
        for command in ("UNITY_OBJECT_RESOURCES", "UNITY_MATERIAL_PROPERTIES", "UNITY_MATERIAL_SET", "UNITY_MATERIAL_RESTORE", "WORKSPACE_QUERY"):
            self.assertIn("rendering", wt.native_features(command))
        for command in ("UNITY_MATERIAL_SET", "UNITY_MATERIAL_RESTORE", "WORKSPACE_QUERY"):
            self.assertIn("memory_write", wt.native_features(command))

    def test_help_explains_value_sources_and_restore_scope(self):
        dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        for name in ("unity_object_resources", "unity_material_properties", "unity_material_set_property", "unity_material_restore"):
            result = dispatcher.debug_help({"command": name})
            self.assertEqual(result["inputSchema"], wt.BY_NAME[name]["inputSchema"])
        self.assertIn("effective_known=false", wt.BY_NAME["unity_material_properties"]["description"])
        self.assertIn("entire", wt.BY_NAME["unity_material_restore"]["description"])


if __name__ == "__main__":
    unittest.main()
