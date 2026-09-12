"""2026-09-12 report regressions: real local socket/stdio, no device or native build."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import socketserver
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from mcp import debug_tools
from mcp.mcp_server import BridgeError, ConnectionConfig, HookSocketClient, McpServer, ToolDispatcher


RECIPE = {"module": "libfixture.so", "base_offset": "0x120", "offsets": ["0x20"]}


class LocalNative(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, callback):
        self.callback = callback
        self.commands = []
        self.lock = threading.Lock()

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                command = self.rfile.readline().decode("utf-8").rstrip("\r\n")
                with self.server.lock:
                    self.server.commands.append(command)
                try:
                    if command.startswith("UI_TOAST_NOTIFY "):
                        body = "{}"
                    else:
                        body = self.server.callback(command)
                    data = body.encode("utf-8")
                    self.wfile.write(f"OK {len(data)}\n".encode() + data)
                except BridgeError as exc:
                    self.wfile.write(f"ERR {exc}\n".encode())

        super().__init__(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=lambda: self.serve_forever(poll_interval=.01), daemon=True)
        self.thread.start()

    def dispatcher(self):
        return ToolDispatcher(ConnectionConfig(port=self.server_address[1], auto_adb_forward=False, timeout=3))

    def __exit__(self, *args):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)


class InputNative:
    def __init__(self, blocked_pings=0):
        self.waiting = threading.Event()
        self.done = threading.Event()
        self.saturated = threading.Event()
        self.lock = threading.Lock()
        self.text = ""
        self.show_count = 0
        self.blocked_pings = blocked_pings
        self.ping_count = 0

    def __call__(self, command):
        if command.startswith("SHOW_INPUT_BOX "):
            with self.lock:
                self.show_count += 1
                self.text = ""
                self.done.clear()
            return "INPUT_BOX_SHOWN"
        if command.startswith("WAIT_INPUT "):
            self.waiting.set()
            if not self.done.wait(min(float(command.split()[1]) / 1000, 3)):
                raise BridgeError("TIMEOUT")
            return self.text
        if command.startswith("INPUT_RESULT "):
            self.text = command.partition(" ")[2]
            self.done.set()
            return "OK"
        if command == "PING":
            if self.blocked_pings:
                with self.lock:
                    self.ping_count += 1
                    if self.ping_count == self.blocked_pings:
                        self.saturated.set()
                if not self.done.wait(3):
                    raise BridgeError("TEST_PRODUCER_STARVED")
            return "PONG"
        raise BridgeError("TEST_UNEXPECTED_COMMAND")


def request(number, name, arguments):
    return {"jsonrpc": "2.0", "id": number, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}


def stdio_run(dispatcher, messages):
    incoming = (json.dumps(message).encode() + b"\n" for message in messages)
    output = io.BytesIO()
    with patch("mcp.mcp_server.sys.stdin", SimpleNamespace(buffer=incoming)), \
         patch("mcp.mcp_server.sys.stdout", SimpleNamespace(buffer=output)):
        McpServer(dispatcher).run_stdio()
    return {message["id"]: message for message in map(json.loads, output.getvalue().splitlines()) if "id" in message}


class ReportRegressions(unittest.TestCase):
    def native_tool(self, tool, arguments, body):
        with LocalNative(lambda command: json.dumps(body)) as native:
            return McpServer(native.dispatcher()).handle(request(1, tool, arguments))["result"]

    def test_single_chain_array_is_an_mcp_object(self):
        item = {"success": True, "address": "0x1000", "value": "18446744073709551615", "steps": []}
        response = self.native_tool("memory_chain_batch", {"chains": [RECIPE]}, [item])
        self.assertFalse(response["isError"])
        expected = {"results": [item], "total": 1, "succeeded": 1, "failed": 0}
        self.assertEqual(response["structuredContent"], expected)
        self.assertEqual(json.loads(response["content"][0]["text"]), expected)

    def test_batch_keeps_item_failure_and_other_successes(self):
        items = [{"success": False, "id": "missing", "error": "CHAIN_NOT_FOUND"},
                 {"success": True, "address": "0x1000"}]
        response = self.native_tool("memory_chain_batch", {"chains": ["missing", RECIPE]}, items)
        self.assertFalse(response["isError"])
        self.assertEqual(response["structuredContent"]["results"], items)
        self.assertEqual(response["structuredContent"]["succeeded"], 1)
        self.assertEqual(response["structuredContent"]["failed"], 1)

    def test_saved_chain_list_including_empty(self):
        for items in ([], [{"id": "fixture", "recipe": RECIPE}]):
            with self.subTest(items=items):
                response = self.native_tool("memory_chain_store", {"operation": "list"}, items)
                self.assertFalse(response["isError"])
                self.assertEqual(response["structuredContent"], {"chains": items, "total": len(items)})

    def test_store_mutations_and_existing_object_responses_stay_unchanged(self):
        expected = {"success": True, "path": "/private/chains.json"}
        for args in ({"operation": "save", "id": "fixture", "recipe": RECIPE},
                     {"operation": "remove", "id": "fixture"}):
            self.assertEqual(self.native_tool("memory_chain_store", args, expected)["structuredContent"], expected)
        result = {"results": [], "total": 0}
        self.assertEqual(self.native_tool("memory_chain_batch", {"chains": [RECIPE]}, result)["structuredContent"], result)

    def test_array_compatibility_does_not_mask_other_contract_errors(self):
        dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        store_save = debug_tools.encode("memory_chain_store", {"operation": "save", "id": "a", "recipe": RECIPE})
        with patch.object(HookSocketClient, "call", return_value="[]"):
            for command in ("IL2CPP_IMAGES 64", store_save):
                with self.subTest(command=command), self.assertRaisesRegex(BridgeError, "non-object"):
                    dispatcher._json_call(command)

    def test_malformed_chain_items_are_not_reported_as_success(self):
        for tool, args, body in (("memory_chain_batch", {"chains": [RECIPE]}, [42]),
                                 ("memory_chain_store", {"operation": "list"}, [{"id": "x"}])):
            with self.subTest(tool=tool):
                response = self.native_tool(tool, args, body)
                self.assertTrue(response["isError"])

    def test_parallel_dispatch_completes_wait_and_push(self):
        fixture = InputNative()
        with LocalNative(fixture) as native, ThreadPoolExecutor(max_workers=2) as pool:
            dispatcher = native.dispatcher()
            pending = pool.submit(dispatcher.call, "input_and_wait", {"timeout_ms": 2000})
            self.assertTrue(fixture.waiting.wait(1))
            pushed = pool.submit(dispatcher.call, "push_input_result", {"text": "测试 input"})
            self.assertTrue(pushed.result(timeout=1)["accepted"])
            self.assertEqual(pending.result(timeout=1), {"text": "测试 input"})

    def test_stdio_reader_keeps_reading_while_a_tool_waits(self):
        fixture = InputNative()
        def incoming():
            yield request(1, "input_and_wait", {"timeout_ms": 2000})
            self.assertTrue(fixture.waiting.wait(1))
            yield request(2, "ping", {})
            yield request(3, "push_input_result", {"text": "stdio result"})
        with LocalNative(fixture) as native:
            responses = stdio_run(native.dispatcher(), incoming())
        self.assertEqual(set(responses), {1, 2, 3})
        for response in responses.values():
            self.assertFalse(response["result"]["isError"])
        self.assertEqual(responses[1]["result"]["structuredContent"], {"text": "stdio result"})
        self.assertTrue(responses[3]["result"]["structuredContent"]["accepted"])

    def test_push_has_reserved_worker_when_normal_pool_is_full(self):
        fixture = InputNative(blocked_pings=7)
        def incoming():
            yield request(1, "input_and_wait", {"timeout_ms": 3000})
            self.assertTrue(fixture.waiting.wait(1))
            for number in range(2, 9):
                yield request(number, "ping", {})
            self.assertTrue(fixture.saturated.wait(2))
            yield request(9, "push_input_result", {"text": "reserved lane"})
        with LocalNative(fixture) as native:
            responses = stdio_run(native.dispatcher(), incoming())
        self.assertEqual(len(responses), 9)
        for response in responses.values():
            self.assertFalse(response["result"]["isError"])

    def test_second_input_does_not_reset_a_pending_dialog(self):
        fixture = InputNative()
        with LocalNative(fixture) as native, ThreadPoolExecutor(max_workers=1) as pool:
            dispatcher = native.dispatcher()
            pending = pool.submit(dispatcher.call, "input_and_wait", {"timeout_ms": 2000})
            self.assertTrue(fixture.waiting.wait(1))
            with self.assertRaisesRegex(BridgeError, "INPUT_REQUEST_BUSY"):
                dispatcher.call("input_and_wait", {"timeout_ms": 2000})
            self.assertEqual(fixture.show_count, 1)
            dispatcher.call("push_input_result", {"text": "first dialog"})
            self.assertEqual(pending.result(timeout=1)["text"], "first dialog")

    def test_bad_input_timeout_does_not_open_a_dialog(self):
        dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        for timeout in (0, 99, 300001, True, "bad"):
            with self.subTest(timeout=timeout), patch.object(dispatcher, "show_input_box") as show:
                with self.assertRaises(BridgeError):
                    dispatcher.input_and_wait({"timeout_ms": timeout})
                show.assert_not_called()

    def test_input_lock_is_released_after_timeout(self):
        fixture = InputNative()
        with LocalNative(fixture) as native:
            dispatcher = native.dispatcher()
            with self.assertRaisesRegex(BridgeError, "TIMEOUT"):
                dispatcher.call("input_and_wait", {"timeout_ms": 100})
            self.assertTrue(dispatcher.call("show_input_box", {})["shown"])
            dispatcher.call("push_input_result", {"text": "after timeout"})
            self.assertEqual(dispatcher.call("wait_input", {"timeout_ms": 100})["text"], "after timeout")

    def test_connected_read_timeout_never_replays_a_command(self):
        sock = MagicMock()
        sock.__enter__.return_value = sock
        sock.recv.side_effect = TimeoutError("read timed out")
        client = HookSocketClient(ConnectionConfig(auto_adb_forward=True))
        with patch("mcp.mcp_server.socket.create_connection", return_value=sock) as connect, \
             patch.object(client, "_adb_forward") as forward:
            with self.assertRaisesRegex(BridgeError, "command was not replayed"):
                client.call("IL2CPP_INVOKE fixture")
            forward.assert_not_called()
            self.assertEqual(connect.call_count, 1)
            sock.sendall.assert_called_once()

    def test_running_tool_keeps_one_connection_snapshot(self):
        dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        ports = []
        def fake_call(client, command, **kwargs):
            ports.append(client.config.port)
            if command.startswith("UI_TOAST_NOTIFY "):
                self.assertEqual(dispatcher.configure_connection({"port": 29000})["port"], 29000)
                return "{}"
            self.assertEqual(command, "PING")
            return "PONG"
        with patch.object(HookSocketClient, "call", autospec=True, side_effect=fake_call):
            result = dispatcher.call("ping", {})
        self.assertEqual(ports, [27184, 27184])
        self.assertEqual(result["port"], 27184)
        self.assertEqual(dispatcher.call("connection_info", {})["port"], 29000)

    def test_connect_failure_still_allows_one_adb_forward_retry(self):
        sock = MagicMock()
        sock.__enter__.return_value = sock
        sock.recv.side_effect = [b"O", b"K", b" ", b"4", b"\n", b"PONG"]
        client = HookSocketClient(ConnectionConfig(auto_adb_forward=True))
        with patch("mcp.mcp_server.socket.create_connection", side_effect=[ConnectionRefusedError("offline"), sock]), \
             patch.object(client, "_adb_forward") as forward:
            self.assertEqual(client.call("PING"), "PONG")
            forward.assert_called_once()
            sock.sendall.assert_called_once()


if __name__ == "__main__":
    unittest.main()
