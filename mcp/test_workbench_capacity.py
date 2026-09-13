"""Capacity and wire regressions. Mock transport only; no phone access."""
import json
import unittest
from unittest.mock import patch
from mcp import render_tools as rt, workspace_tools as wt
from mcp.mcp_server import BridgeError, ConnectionConfig, ToolDispatcher, TOOLS


class WorkbenchCapacityTests(unittest.TestCase):
    def test_page_rows_upper_bound(self):
        rt.encode("overlay_set", {"key": "page_rows", "value": 100000})
        for value in (0, 100001, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rt.encode("overlay_set", {"key": "page_rows", "value": value})

    def test_restore_512_independent_queries(self):
        tab = {"name": "N"*96, "image": "I"*256, "query": "Q"*256,
               "class_filter": "C"*256, "kind": 0}
        config = {"format": "il2cpp-workbench-queries", "schema": 1,
                  "tabs": [dict(tab, name=str(i)+tab["name"][:90]) for i in range(512)]}
        wire = wt.encode("workspace_browser", {"op": "restore", "config": config})
        self.assertGreater(len(wire), 256*1024)
        self.assertLess(len(wire), 8*1024*1024+64)
        self.assertEqual(json.loads(bytes.fromhex(wire.split()[1]))["config"], config)
        config["tabs"].append(dict(tab))
        with self.assertRaises(ValueError):
            wt.encode("workspace_browser", {"op": "restore", "config": config})

    def test_scan_15_levels_uses_existing_multithread_tool(self):
        dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        with patch.object(dispatcher, "_json_call", return_value={"chains": []}) as call:
            dispatcher.memory_scan_base({"target_address": "0x1234", "max_depth": 15,
                                         "workers": 8, "max_results": 100})
        wire = call.call_args.args[0]
        self.assertTrue(wire.startswith("MEMORY_CHAIN_SCAN "))
        data = json.loads(bytes.fromhex(wire.split()[1]))
        self.assertEqual(data["max_depth"], 15)
        self.assertEqual(data["workers"], 8)
        self.assertEqual(data["max_results"], 100)

    def test_scan_rejects_outside_15_layers(self):
        dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        for depth in (0, 16, True):
            with self.subTest(depth=depth), self.assertRaises(BridgeError):
                dispatcher.memory_scan_base({"target_address": "0x1234", "max_depth": depth})

    def test_published_schema_matches_validation(self):
        tool = next(t for t in TOOLS if t["name"] == "memory_scan_base")
        self.assertEqual(tool["inputSchema"]["properties"]["max_depth"]["maximum"], 15)
        self.assertEqual(wt.QUERY_CONFIG["properties"]["tabs"]["maxItems"], 512)

if __name__ == "__main__":
    unittest.main()
