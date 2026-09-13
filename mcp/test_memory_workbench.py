"""Memory workspace protocol tests. Mock transport: no device reads/writes."""
import json
import unittest
from unittest.mock import patch
from mcp import debug_tools as d
from mcp.mcp_server import BridgeError, ConnectionConfig, ToolDispatcher


def descriptor(args):
    command = d.encode("memory_search_tabs", args)
    assert command.startswith("MEMORY_SEARCH_TABS ")
    return json.loads(bytes.fromhex(command.split()[1]))


class MemoryWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.bridge = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        notify = patch.object(self.bridge, "_notify_mcp_call")
        notify.start()
        self.addCleanup(notify.stop)

    def test_tab_lifecycle_and_version(self):
        for op in ("list", "create", "saved_list"):
            self.assertEqual(descriptor({"op": op}), {"op": op})
        for op in ("focus", "duplicate", "remove", "clear", "results"):
            args = {"op": op, "tab_id": 3, "version": 2}
            self.assertEqual(descriptor(args), args)
            with self.assertRaises(ValueError): descriptor({"op": op})
        args = {"op": "update", "tab_id": 3, "name": "子弹"}
        self.assertEqual(descriptor(args), args)

    def test_group_query_address_normalization(self):
        for value in ("100;200:512", "100D;1.5F::128", "10~20", "1F~2F"):
            args = {"op": "search", "tab_id": 1, "query": {"types": ["i32", "f32"],
                "value": value, "start": "4096", "end": "0x2000", "regions": "heap,anonymous"}}
            data = descriptor(args)
            self.assertEqual(data["query"]["start"], "0x1000")
            self.assertEqual(data["query"]["value"], value)
            self.assertEqual(args["query"]["start"], "4096")  # no caller mutation

    def test_query_rejects_empty_regions_and_invalid_bounds(self):
        for query in ({"regions": ""}, {"regions": "all,heap"}, {"regions": "bogus"},
                      {"start": 0}, {"start": "0x2000", "end": "0x1000"},
                      {"types": ["i32", "i32"]}, {"types": ["bogus"]}, {"scan_mb": 513},
                      {"timeout_ms": 60001}, {"types": ["hex"], "mode": "fuzzy"}):
            with self.subTest(query=query), self.assertRaises(ValueError):
                descriptor({"op": "search", "tab_id": 1, "query": {"types": ["i32"], "value": "10", **query}})

    def test_text_and_hex_types_and_fuzzy_modes(self):
        for kind, value in (("hex", "A? ?? FF"), ("utf8", "玩家"), ("utf16", "子弹")):
            query = {"types": [kind], "value": value}
            self.assertEqual(descriptor({"op": "search", "tab_id": 1, "query": query})["query"], query)
        self.assertEqual(descriptor({"op": "search", "tab_id": 1,
            "query": {"types": ["i32", "f32"], "mode": "fuzzy"}})["query"]["mode"], "fuzzy")

    def test_all_refine_modes(self):
        for mode in ("equals", "not_equals", "greater", "less", "changed", "unchanged", "increased", "decreased", "increased_by", "decreased_by"):
            args = {"op": "refine", "tab_id": 1, "mode": mode}
            if mode in {"equals", "not_equals", "greater", "less", "increased_by", "decreased_by"}: args["value"] = "10"
            self.assertEqual(descriptor(args), args)
        for args in ({"mode": "equals"}, {"mode": "changed", "value": "10"}, {"mode": "all"}):
            with self.assertRaises(ValueError): descriptor({"op": "refine", "tab_id": 1, **args})

    def test_selection_is_explicit_and_scoped(self):
        for mode in ("add", "remove", "all", "none"):
            args = {"op": "select", "tab_id": 1, "mode": mode}
            if mode in {"add", "remove"}: args["ids"] = ["2:0x1000:i32"]
            self.assertEqual(descriptor(args), args)
        for args in ({"mode": "add"}, {"mode": "all", "ids": []}, {"mode": "changed"}):
            with self.assertRaises(ValueError): descriptor({"op": "select", "tab_id": 1, **args})

    def test_batch_requires_confirm_and_valid_source(self):
        invalid = ({"confirm": False, "tab_id": 1}, {"confirm": True},
            {"confirm": True, "tab_id": 1, "ids": ["1"]},
            {"confirm": True, "source": "saved", "ids": []},
            {"confirm": True, "source": "saved", "ids": [str(i) for i in range(257)]},
            {"confirm": True, "source": "saved", "ids": ["1"], "tab_id": 2},
            {"confirm": True, "tab_id": 1, "interval_ms": 1})
        for args in invalid:
            with self.subTest(args=args), patch.object(self.bridge, "_json_call") as native:
                with self.assertRaises(BridgeError): self.bridge.call("memory_batch_edit", {"value": "7878", **args})
                native.assert_not_called()
        args = {"source": "saved", "ids": ["1"], "value": "9223372036854775807", "confirm": True, "freeze": True}
        self.assertEqual(json.loads(bytes.fromhex(d.encode("memory_batch_edit", args).split()[1])), args)

    def test_exports_return_receipt_without_page_fetches(self):
        receipt = {"success": True, "path": "/private/exports/memory-search-1.json", "count": 100000}
        args = {"op": "export", "tab_id": 1, "scope": "all", "destination": "file"}
        with patch.object(self.bridge, "_json_call", return_value=receipt) as native, patch.object(self.bridge, "_notify_mcp_call"):
            self.assertEqual(self.bridge.call("memory_search_tabs", args), receipt)
            native.assert_called_once_with(d.encode("memory_search_tabs", args), timeout=75.0)
        for args in ({"source": "saved", "scope": "selected"}, {"source": "saved"},
                     {"source": "saved", "scope": "all", "ids": ["1"]}, {"tab_id": 1, "ids": ["1"]}):
            with self.assertRaises(ValueError): descriptor({"op": "export", **args})
        self.assertEqual(descriptor({"op": "export", "source": "saved", "scope": "all"})["scope"], "all")

    def test_exact_search_extensions_use_shared_engine(self):
        for value, kinds in (("100;200::512", ["i32"]), ("A? ?? FF", ["hex"]), ("玩家", ["utf16"])):
            with patch.object(self.bridge, "_json_call", return_value={"sessions": [{"session_id": 3}]}) as native:
                result = self.bridge.memory_search_exact({"value_types": kinds, "value": value})
                command = native.call_args.args[0]
                self.assertTrue(command.startswith("MEMORY_SEARCH_TYPED "))
                self.assertEqual(json.loads(bytes.fromhex(command.split()[1]))["value"], value)
                self.assertEqual(result["search_count"], 1)
        for args in ({"value_types": [{}], "value": "1"}, {"value_types": ["i32"], "value": None},
                     {"value_types": ["f32"], "value": float("inf")}):
            with self.assertRaises(BridgeError): self.bridge.memory_search_exact(args)

    def test_original_filter_supports_shared_and_legacy_sessions(self):
        for args, expected in (({"mode": "equals", "value": "100;200::512"}, "MEMORY_FILTER_TYPED 7 equals "),
                               ({"mode": "changed"}, "MEMORY_FILTER_TYPED 7 changed -"),
                               ({"mode": "increased_by", "value": "9223372036854775807"}, "MEMORY_FILTER_TYPED 7 increased_by ")):
            with patch.object(self.bridge, "_json_call", return_value={"result_count": 1}) as native:
                self.bridge.memory_filter_value({"session_id": 7, **args})
                self.assertTrue(native.call_args.args[0].startswith(expected))
        with patch.object(self.bridge, "_json_call", return_value={}) as native:
            self.bridge.memory_filter_value({"session_id": 7, "mode": "equals", "value_type": "i32", "value": 123})
            native.assert_called_once_with("MEMORY_FILTER 7 equals 3762303030303030", timeout=60.0)

    def test_mixed_rows_and_high_offsets_are_preserved(self):
        payload = {"results": [{"type": "i32", "value": "100"}, {"type": "f32", "value": "1.5"}]}
        with patch.object(self.bridge, "_json_call", return_value=payload) as native:
            self.assertEqual(self.bridge.memory_search_results({"session_id": 7, "offset": 50000}), payload)
            native.assert_called_once_with("MEMORY_SEARCH_RESULTS 7 50000 100")

    def test_feature_gates_and_help_share_existing_catalog(self):
        self.assertEqual(d.native_features("MEMORY_SEARCH_TABS"), ("memory_search", "memory_read"))
        self.assertEqual(d.native_features("MEMORY_BATCH_EDIT"), ("memory_read", "memory_write"))
        for name in ("memory_search_tabs", "memory_batch_edit"):
            self.assertEqual(self.bridge.debug_help({"command": name})["inputSchema"], d.BY_NAME[name]["inputSchema"])
        self.assertTrue(d.BY_NAME["memory_batch_edit"]["annotations"]["destructiveHint"])


if __name__ == "__main__":
    unittest.main()
