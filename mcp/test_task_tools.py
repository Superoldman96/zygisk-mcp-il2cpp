"""Task encoding / feature-gating tests, not live native scheduler tests."""
import json
from pathlib import Path
import re
import unittest
try:
    from . import task_tools as t
except ImportError:
    import task_tools as t


def decode(args):
    command, data = t.encode("workspace_jobs", args).split(" ", 1)
    assert command == "WORKSPACE_JOBS"
    return json.loads(bytes.fromhex(data))


class TaskTools(unittest.TestCase):
    def test_minimal_list(self): self.assertEqual(decode({"op": "list"}), {"op": "list"})

    def test_submit_requires_actual_arguments(self):
        self.assertEqual(decode({"op": "submit", "command": "MEMORY_READ", "arguments": "0x1000 4"})["command"], "MEMORY_READ")
        with self.assertRaises(ValueError): decode({"op": "submit", "command": "MEMORY_READ"})

    def test_write_and_nested_calls_rejected(self):
        for command in ("MEMORY_WRITE", "ASM_PATCH", "IL2CPP_INVOKE", "DEBUGGER_CONTROL", "WORKSPACE_QUERY", "WORKSPACE_JOBS", "LUA_EXEC"):
            with self.assertRaises(ValueError): decode({"op": "submit", "command": command, "arguments": ""})

    def test_no_command_line_injection(self):
        for argument in ("a\nb", "a\rb", "a\0b"):
            with self.assertRaises(ValueError): decode({"op": "submit", "command": "HELP", "arguments": argument})

    def test_utf8_byte_limit(self):
        with self.assertRaises(ValueError): decode({"op": "submit", "command": "HELP", "arguments": "测" * 20001})

    def test_result_requires_command_identity(self):
        with self.assertRaises(ValueError): decode({"op": "get", "task_id": "1", "include_result": True})
        self.assertEqual(decode({"op": "get", "task_id": "1", "include_result": True, "expected_command": "MEMORY_READ"})["expected_command"], "MEMORY_READ")

    def test_cancellation_accepts_no_target_or_commands(self):
        self.assertEqual(decode({"op": "cancel", "task_id": "1"}), {"op": "cancel", "task_id": "1"})
        with self.assertRaises(ValueError): decode({"op": "cancel", "task_id": "1", "command": "MEMORY_READ"})

    def test_command_features_gate_submit_and_readback(self):
        for op in ("submit", "get"):
            args = {"op": op, "include_result": True, "command": "DECOMP_DECOMPILE", "expected_command": "DECOMP_DECOMPILE"}
            self.assertEqual(t.extra_features("workspace_jobs", args), {"decompiler", "memory_read"})
        self.assertEqual(t.extra_features("workspace_jobs", {"op": "list"}), set())

    def test_status_commands_do_not_require_memory_read(self):
        self.assertEqual(t.command_features("ASM_STATUS"), ("assembly",))
        self.assertEqual(t.command_features("DECOMP_STATUS"), ("decompiler",))
        self.assertEqual(t.command_features("ASM_DISASSEMBLE"), ("assembly", "memory_read"))
        self.assertEqual(t.command_features("DECOMP_DECOMPILE"), ("decompiler", "memory_read"))
        self.assertEqual(
            t.extra_features("workspace_jobs", {"op": "submit", "command": "ASM_STATUS"}),
            {"assembly"},
        )
        self.assertEqual(
            t.extra_features("workspace_jobs", {
                "op": "get", "task_id": "task-1", "include_result": True,
                "expected_command": "DECOMP_STATUS",
            }),
            {"decompiler"},
        )

    def test_raw_generic_call_is_conservative(self):
        self.assertIn("memory_read", t.native_features("WORKSPACE_JOBS"))
        self.assertIn("breakpoint", t.native_features("WORKSPACE_JOBS"))
        self.assertIsNone(t.native_features("MEMORY_READ"))

    def test_allowlist_matches_native(self):
        cpp = (Path(__file__).resolve().parents[1] / "module/src/main/cpp/overlay/jobs.cpp").read_text(encoding="utf-8")
        region = cpp.split("readOnlyCommands={", 1)[1].split("};", 1)[0]
        self.assertEqual(set(t.READ_COMMANDS), set(re.findall(r'"([A-Z0-9_]+)"', region)))

    def test_pagination_and_state(self):
        self.assertEqual(decode({"op": "list", "state": "cancel_requested", "limit": 256})["state"], "cancel_requested")
        for args in ({"op": "list", "limit": 257}, {"op": "list", "state": "done"}, {"op": "get"}):
            with self.assertRaises(ValueError): decode(args)


if __name__ == "__main__": unittest.main()
