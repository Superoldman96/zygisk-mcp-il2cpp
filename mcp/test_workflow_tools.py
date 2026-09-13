"""Offline workflow-tool schema, encoding and feature-gate tests."""
import json
import unittest

try:
    from . import workflow_tools as w
except ImportError:
    import workflow_tools as w


def decode_document(name, args):
    command, payload = w.encode(name, args).split(" ", 1)
    return command, json.loads(bytes.fromhex(payload))


def decode_frame_capture(args):
    command, nested, outer = w.encode("debug_snapshot", args).split(" ", 2)
    assert command == "WORKSPACE_QUERY"
    assert nested == "IL2CPP_SNAPSHOT_CAPTURE"
    inner_hex = bytes.fromhex(outer).decode("ascii")
    return json.loads(bytes.fromhex(inner_hex))


class WorkflowTools(unittest.TestCase):
    def test_catalog_is_unique_and_has_no_clear(self):
        expected = {"journal_status", "journal_query", "journal_export", "diagnostic_export",
                    "debug_project", "debug_snapshot", "change_history", "debug_stop_all"}
        self.assertEqual(set(w.BY_NAME), expected)
        self.assertEqual(len(w.TOOLS), len(w.BY_NAME))
        self.assertFalse(any("clear" in item["name"] for item in w.TOOLS))
        self.assertEqual(w.encode("journal_status", {}), "JOURNAL_STATUS")

    def test_journal_queries_are_exact_and_paginated(self):
        command, value = decode_document("journal_query", {"op": "read",
            "session": "session-100-20-30-part-2.jsonl", "cursor": "18446744073709551615",
            "limit": 100, "kind": "command_outcome", "failures_only": True})
        self.assertEqual(command, "JOURNAL_QUERY")
        self.assertEqual(value["cursor"], "18446744073709551615")
        with self.assertRaises(ValueError):
            w.encode("journal_query", {"op": "read", "session": "100-20", "cursor": "0"})
        with self.assertRaises(ValueError):
            w.encode("journal_query", {"op": "list", "session": "session-1-2-3.jsonl"})
        with self.assertRaises(ValueError):
            w.encode("journal_query", {"op": "list", "cursor": "18446744073709551616"})
        with self.assertRaises(ValueError):
            w.encode("journal_query", {"op": "read", "limit": 101})

    def test_exports_use_receipt_commands_without_clear(self):
        session = "session-100-20-30.jsonl"
        for name, expected in (("journal_export", "JOURNAL_EXPORT"),
                               ("diagnostic_export", "DIAGNOSTIC_EXPORT")):
            command, value = decode_document(name, {"session": session})
            self.assertEqual((command, value), (expected, {"session": session}))
            description = w.BY_NAME[name]["description"]
            self.assertIn("root-owned", description)
            self.assertIn("/data/adb/zygisk_il2cpp_mcp/journal/<target>/exports/", description)

    def test_project_operation_envelopes_and_revision(self):
        command, value = decode_document("debug_project", {"op": "create", "id": "crash_1",
            "data": {"title": "Crash", "tasks": [{"state": "open"}]}})
        self.assertEqual(command, "WORKSPACE_PROJECT")
        self.assertEqual(value["data"]["title"], "Crash")
        _, value = decode_document("debug_project", {"op": "update", "id": "crash_1",
            "revision": "12", "data": {"notes": "reproduced"}})
        self.assertEqual(value["revision"], "12")
        for bad in ({"op": "create", "id": "x"},
                    {"op": "update", "id": "x", "revision": "1"},
                    {"op": "archive", "id": "x", "revision": "1", "data": {}},
                    {"op": "get", "id": "x", "limit": 1}):
            with self.assertRaises(ValueError):
                w.encode("debug_project", bad)

    def test_il2cpp_capture_is_double_hex_and_normalizes_address(self):
        value = decode_frame_capture({"op": "capture", "kind": "object", "address": "4096",
                                      "depth": 2, "limit": 64, "budget_ms": 25})
        self.assertEqual(value["address"], "0x1000")
        self.assertEqual(value["kind"], "object")
        value = decode_frame_capture({"op": "capture", "kind": "dict", "address": 8192,
                                      "offset": 4, "limit": 20})
        self.assertEqual(value["kind"], "dictionary")
        self.assertEqual(value["address"], "0x2000")
        with self.assertRaises(ValueError):
            w.encode("debug_snapshot", {"op": "capture", "kind": "list", "address": "0"})
        with self.assertRaises(ValueError):
            w.encode("debug_snapshot", {"op": "capture", "kind": "object", "address": "1", "offset": 10001})

    def test_memory_capture_uses_direct_command(self):
        command, value = decode_document("debug_snapshot", {"op": "capture", "kind": "memory",
                                                              "address": "0X2000", "size": 512})
        self.assertEqual(command, "WORKSPACE_MEMORY_CAPTURE")
        self.assertEqual(value, {"address": "0x2000", "size": 512})
        with self.assertRaises(ValueError):
            w.encode("debug_snapshot", {"op": "capture", "kind": "memory", "address": "1", "depth": 1})

    def test_snapshot_pages_and_diff_have_distinct_fields(self):
        _, page = decode_document("debug_snapshot", {"op": "list", "after": "snapshot-1", "limit": 100})
        self.assertEqual(page["after"], "snapshot-1")
        _, diff = decode_document("debug_snapshot", {"op": "diff", "before": "snapshot-1",
            "after_id": "snapshot-2", "offset": 65535, "limit": 256, "allow_cross_session": True})
        self.assertTrue(diff["allow_cross_session"])
        self.assertEqual(diff["offset"], 65535)
        with self.assertRaises(ValueError):
            w.encode("debug_snapshot", {"op": "diff", "before": "a", "after_id": "b", "offset": 65537})
        with self.assertRaises(ValueError):
            w.encode("debug_snapshot", {"op": "list", "limit": 101})
        with self.assertRaises(ValueError):
            w.encode("debug_snapshot", {"op": "diff", "before": "snapshot-1"})

    def test_snapshot_schema_documents_operation_specific_bounds(self):
        properties = w.BY_NAME["debug_snapshot"]["inputSchema"]["properties"]
        limit_description = properties["limit"]["description"]
        offset_description = properties["offset"]["description"]
        self.assertIn("capture and diff accept 1..256", limit_description)
        self.assertIn("list accepts 1..100", limit_description)
        self.assertIn("capture accepts 0..10000", offset_description)
        self.assertIn("diff accepts 0..65536", offset_description)

    def test_change_undo_keeps_exact_session_and_requires_confirmation(self):
        args = {"op": "undo", "id": "change-1", "session": "1234-1700000000000", "confirm": True}
        command, value = decode_document("change_history", args)
        self.assertEqual(command, "WORKSPACE_CHANGES")
        self.assertEqual(value["session"], args["session"])
        for bad in ({**args, "confirm": False}, {**args, "session": "0x123-4"},
                    {"op": "get", "id": "change-1", "session": args["session"]}):
            with self.assertRaises(ValueError):
                w.encode("change_history", bad)

    def test_stop_requires_true_and_destructive_annotations(self):
        command, value = decode_document("debug_stop_all", {"confirm": True})
        self.assertEqual((command, value), ("WORKSPACE_STOP", {"confirm": True}))
        with self.assertRaises(ValueError):
            w.encode("debug_stop_all", {"confirm": False})
        for name in ("debug_project", "change_history", "debug_stop_all"):
            self.assertTrue(w.BY_NAME[name]["annotations"]["destructiveHint"])

    def test_dynamic_and_raw_feature_gates(self):
        self.assertEqual(w.features("debug_snapshot"), ("diagnostics",))
        self.assertEqual(w.extra_features("debug_snapshot", {"op": "capture", "kind": "memory"}), {"memory_read"})
        self.assertEqual(w.extra_features("debug_snapshot", {"op": "capture", "kind": "list"}),
                         {"il2cpp_metadata", "il2cpp_objects", "il2cpp_invoke"})
        self.assertEqual(w.extra_features("change_history", {"op": "undo"}), {"memory_read", "memory_write"})
        self.assertEqual(w.extra_features("change_history", {"op": "list"}), set())
        self.assertEqual(w.native_features("WORKSPACE_CHANGES"), ("diagnostics", "memory_read", "memory_write"))
        self.assertEqual(w.native_features("WORKSPACE_MEMORY_CAPTURE"), ("diagnostics", "memory_read"))
        self.assertIsNone(w.native_features("WORKSPACE_QUERY"))


if __name__ == "__main__":
    unittest.main()
