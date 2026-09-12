"""Offline encoders only: no socket, ADB or target is used by these tests."""
import json
import unittest
from mcp import workspace_tools as w


class AssetSessionToolsTests(unittest.TestCase):
    def frame(self, name, arguments):
        prefix, command, encoded = w.encode(name, arguments).split()
        self.assertEqual(prefix, "WORKSPACE_QUERY")
        return command, bytes.fromhex(encoded).decode()

    def payload(self, name, arguments):
        command, encoded = w.encode(name, arguments).split()
        return command, json.loads(bytes.fromhex(encoded))

    def test_assets_use_existing_resource_tool(self):
        command, args = self.frame("unity_object_resources", {
            "address": "0", "mode": "assets", "asset_type": "Texture2D", "query": "角色", "offset": 256, "limit": 64})
        self.assertEqual(command, "UNITY_ASSETS_LIST")
        fields = args.split()
        self.assertEqual(bytes.fromhex(fields[0]).decode(), "Texture2D")
        self.assertEqual(bytes.fromhex(fields[1]).decode(), "角色")
        self.assertEqual(fields[2:], ["256", "64"])

    def test_assets_reject_cross_mode_arguments(self):
        for args in (
            {"address": "0", "mode": "assets"},
            {"address": "0x1000", "mode": "assets", "asset_type": "Mesh"},
            {"address": "0", "mode": "assets", "asset_type": "Texture2D", "include_inactive": True},
            {"address": "0", "mode": "renderers", "asset_type": "Mesh"},
            {"address": "0x1000", "asset_type": "Mesh"},
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                w.encode("unity_object_resources", args)

    def test_preview_and_export_are_frame_queries(self):
        self.assertEqual(self.frame("unity_resource_preview", {"address": "4096"}), ("UNITY_ASSET_SNAPSHOT", "0x1000 512"))
        command, args = self.frame("unity_resource_export", {"address": "0x1000", "name": "my_texture"})
        self.assertEqual(command, "UNITY_ASSET_EXPORT")
        self.assertEqual(args.split()[:2], ["0x1000", "png"])
        self.assertEqual(bytes.fromhex(args.split()[2]).decode(), "my_texture")
        for name, args in (
            ("unity_resource_preview", {"address": "0"}),
            ("unity_resource_preview", {"address": "1", "max_edge": 1025}),
            ("unity_resource_preview", {"address": "1", "max_edge": True}),
            ("unity_resource_export", {"address": "1", "name": "../texture"}),
            ("unity_resource_export", {"address": "1", "name": "texture.png"}),
        ):
            with self.subTest(name=name, args=args), self.assertRaises(ValueError):
                w.encode(name, args)

    def test_assets_and_handle_inspection_keep_feature_gates(self):
        for name in ("unity_resource_preview", "unity_resource_export", "unity_object_resources"):
            self.assertEqual(w.features(name), ("rendering", *w.OBJECT_FEATURES))
        for command in ("UNITY_ASSETS_LIST", "UNITY_ASSET_SNAPSHOT", "UNITY_ASSET_EXPORT"):
            self.assertEqual(w.native_features(command), ("rendering", *w.OBJECT_FEATURES))
        self.assertEqual(w.native_features("IL2CPP_RETURN_MEMBERS"), w.OBJECT_FEATURES)

    def test_return_handle_reuses_object_inspector(self):
        self.assertEqual(self.frame("il2cpp_inspector_members", {"return_handle": "12", "limit": 8}),
                         ("IL2CPP_RETURN_MEMBERS", "12 0 8"))
        for patch in ({"address": "1"}, {"image": "Core"}, {"return_handle": "0"}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                w.encode("il2cpp_inspector_members", {"return_handle": "12", **patch})

    def test_query_tab_operations_are_explicit(self):
        full = {"id": "1", "version": "2", "name": "角色", "image": "Assembly-CSharp.dll", "query": "npc", "class_filter": "", "kind": 0}
        for request in ({"op": "list"}, {"op": "config"}, {"op": "create", "tab": {}},
                        {"op": "update", "tab": full}, {"op": "duplicate", "id": "1"},
                        {"op": "remove", "id": "1", "version": "2"}, {"op": "save", "name": "default"},
                        {"op": "load", "name": "default"}):
            command, payload = self.payload("workspace_browser", request)
            self.assertEqual(command, "WORKSPACE_BROWSER")
            self.assertEqual(payload, request)

    def test_query_tab_rejects_live_state_and_wrong_operations(self):
        for request in ({"op": "list", "id": "1"}, {"op": "remove"}, {"op": "create", "tab": {"id": "1"}},
                        {"op": "update", "tab": {"id": "1", "version": "2"}},
                        {"op": "create", "tab": {"address": "0x1000"}}, {"op": "save", "name": "../bad"},
                        {"op": "create", "tab": {"kind": 3}}, {"op": "duplicate", "id": 1}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                w.encode("workspace_browser", request)

    def test_caller_drafts_cannot_invoke(self):
        method = {"image": "Assembly-CSharp.dll", "class": "Role", "token": 100663297}
        request = {"op": "set_draft", "method": method, "version": "0", "values": ["3", "Grounded"], "kinds": [0, 1]}
        command, payload = self.payload("workspace_caller", request)
        self.assertEqual(command, "WORKSPACE_CALLER")
        self.assertEqual(payload["method"]["namespace"], "")
        self.assertNotIn("IL2CPP_CALL", command)
        for patch in ({"op": "call"}, {"kinds": [1]}, {"version": 1}, {"values": ["\0"], "kinds": [0]}, {"execute": True}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                w.encode("workspace_caller", {**request, **patch})


if __name__ == "__main__":
    unittest.main()
