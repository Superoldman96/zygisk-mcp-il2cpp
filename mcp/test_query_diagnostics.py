"""Real loopback protocol tests; never call a target game or build native code."""
import json
import socketserver
import threading
import unittest
from unittest.mock import patch

from mcp.mcp_server import BridgeError, ConnectionConfig, HookSocketClient, ToolDispatcher


class Peer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, respond):
        self.commands = []
        self.respond = respond

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                command = self.rfile.readline().decode().strip()
                self.server.commands.append(command)
                try:
                    for packet in self.server.respond(command):
                        self.wfile.write(packet)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        super().__init__(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=lambda: self.serve_forever(poll_interval=.01), daemon=True)
        self.thread.start()

    def client(self):
        return HookSocketClient(ConnectionConfig(port=self.server_address[1], auto_adb_forward=False, timeout=2))

    def __exit__(self, *args):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)


def stage(name):
    return ("MCP_STAGE " + json.dumps({"stage": name, "address": "0x1000", "pid": 42, "tid": 43, "index": 0}) + "\n").encode()

def ok(body):
    raw = body.encode()
    return f"OK {len(raw)}\n".encode() + raw


class QueryDiagnosticsTests(unittest.TestCase):
    query = "IL2CPP_FIND_METHOD 41 - 42 43 0"

    def test_success_preserves_original_json(self):
        expected = '{"name":"get_gravity","address":"0x1000"}'
        with Peer(lambda _: [stage("command.begin"), stage("thread.detached"), ok(expected)]) as peer:
            self.assertEqual(peer.client().call(self.query), expected)
            self.assertEqual(peer.commands, ["MCP_QUERY_V1 " + self.query])

    def test_abrupt_close_reports_last_stage_and_never_replays(self):
        with Peer(lambda _: [stage("command.begin"), stage("method.return_type")]) as peer:
            with patch.object(HookSocketClient, "_adb_forward") as adb:
                with self.assertRaises(BridgeError) as error:
                    peer.client().call(self.query)
                self.assertIn("method.return_type", str(error.exception))
                self.assertIn('"pid": 42', str(error.exception))
                self.assertIn("not a confirmed crash backtrace", str(error.exception))
                self.assertEqual(len(peer.commands), 1)
                adb.assert_not_called()

    def test_old_module_only_retries_rejected_read_only_envelope(self):
        def respond(command):
            return [b"ERR UNKNOWN_COMMAND\n"] if command.startswith("MCP_QUERY_V1 ") else [ok("{}")]
        with Peer(respond) as peer:
            self.assertEqual(peer.client().call(self.query), "{}")
            self.assertEqual(peer.commands, ["MCP_QUERY_V1 " + self.query, self.query])

    def test_unknown_error_after_entering_native_query_is_not_replayed(self):
        with Peer(lambda _: [stage("command.begin"), b"ERR UNKNOWN_COMMAND\n"]) as peer:
            with self.assertRaisesRegex(BridgeError, "UNKNOWN_COMMAND"):
                peer.client().call(self.query)
            self.assertEqual(len(peer.commands), 1)

    def test_native_validation_error_is_preserved(self):
        with Peer(lambda _: [stage("method.parameter_count"), b"ERR METHOD_PARAMETER_COUNT_INVALID\n"]) as peer:
            with self.assertRaisesRegex(BridgeError, "METHOD_PARAMETER_COUNT_INVALID"):
                peer.client().call(self.query)

    def test_mutations_do_not_use_diagnostic_envelope_or_unknown_fallback(self):
        for command in ("DOBBY_HOOK_RETURN 0x1000 bool 0", "IL2CPP_INVOKE 41 - 42 43 0 0 0"):
            with self.subTest(command=command), Peer(lambda _: [b"ERR UNKNOWN_COMMAND\n"]) as peer:
                with self.assertRaisesRegex(BridgeError, "UNKNOWN_COMMAND"):
                    peer.client().call(command)
                self.assertEqual(peer.commands, [command])

    def test_malformed_stage_is_rejected(self):
        for packet in (b"MCP_STAGE null\n", b"MCP_STAGE []\n", b"MCP_STAGE {bad}\n", b"MCP_STAGE {}\n"):
            with self.subTest(packet=packet), Peer(lambda _: [packet]) as peer:
                with self.assertRaisesRegex(BridgeError, "invalid native diagnostic stage"):
                    peer.client().call(self.query)
                self.assertEqual(len(peer.commands), 1)

    def test_stage_budget_is_bounded(self):
        with Peer(lambda _: [stage("method.read")] * 4098) as peer:
            with self.assertRaisesRegex(BridgeError, "too many native diagnostic stages"):
                peer.client().call(self.query)

    def test_plain_nonquery_protocol_is_unchanged(self):
        with Peer(lambda _: [ok("PONG")]) as peer:
            self.assertEqual(peer.client().call("PING"), "PONG")
            self.assertEqual(peer.commands, ["PING"])

    def test_internal_envelope_cannot_be_sent_through_raw_tool(self):
        dispatcher=ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        with patch.object(HookSocketClient, "call") as call:
            with self.assertRaisesRegex(BridgeError, "internal envelope"):
                dispatcher.raw_hook_call({"command": "MCP_QUERY_V1 " + self.query})
            call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
