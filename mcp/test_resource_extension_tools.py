"""Protocol tests; no injection, target mutation or Android build."""
import json
import unittest
from unittest.mock import patch
from mcp import workspace_tools as w
from mcp.mcp_server import ToolDispatcher, ConnectionConfig, BridgeError

def decode_frame(command):
    prefix, name, payload = command.split(" ", 2)
    assert prefix == "WORKSPACE_QUERY"
    return name, bytes.fromhex(payload).decode()

class ResourceExtensionTools(unittest.TestCase):
    def test_model_reuses_resource_tools(self):
        command, args = decode_frame(w.encode("unity_resource_preview", {"address":"0x1000","kind":"model"}))
        self.assertEqual((command,args), ("UNITY_MODEL_SNAPSHOT","0x1000"))
        command,args=decode_frame(w.encode("unity_resource_export", {"address":"0x1000","kind":"model","name":"npc"}))
        self.assertEqual((command,args),("UNITY_MODEL_EXPORT","0x1000 6e7063"))
        with self.assertRaises(ValueError): w.encode("unity_resource_preview", {"address":"0x1000","kind":"model","max_edge":64})

    def test_texture_default_compatible(self):
        self.assertEqual(decode_frame(w.encode("unity_resource_preview", {"address":"0x1000"})),("UNITY_ASSET_SNAPSHOT","0x1000 512"))

    def test_monitor_cursor_and_limits(self):
        opts={"op":"events","after_sequence":"9007199254740993","limit":256}
        cmd,args=decode_frame(w.encode("unity_resource_monitor",opts))
        self.assertEqual(cmd,"UNITY_ASSET_MONITOR")
        self.assertEqual(json.loads(bytes.fromhex(args)),opts)
        for opts in ({"op":"stop","type":"Mesh"},{"op":"start","interval_ms":0},{"op":"events","limit":257},{"op":"events","after_sequence":1}):
            with self.subTest(opts=opts),self.assertRaises(ValueError):w.encode("unity_resource_monitor",opts)

    def test_frida_validation(self):
        self.assertTrue(w.encode("frida_control",{"op":"status"}).startswith("FRIDA_CONTROL "))
        for opts in ({"op":"attach","address":"0x3"},{"op":"stalk"},{"op":"stop"},{"op":"attach","address":"0x1000","duration_ms":600001},{"op":"status","address":"0x1000"}):
            with self.subTest(opts=opts),self.assertRaises(ValueError):w.encode("frida_control",opts)
        self.assertEqual(w.features("frida_control"),("frida","trace"))
        self.assertEqual(w.native_features("FRIDA_CONTROL"),("frida","trace"))

    def test_struct_encoding_and_exact_generic_call(self):
        value={"fields":{"x":1,"y":{"number":"9223372036854775807"},"nested":{"fields":{"ok":True}}}}
        token=ToolDispatcher._invoke_token(value)
        self.assertEqual(token[0],"j")
        self.assertEqual(json.loads(bytes.fromhex(token[1:])),value)
        args={"image":"A.dll","class":"C","token":100663297,"generic_handle":"9","arguments":[value]}
        cmd,words=decode_frame(w.encode("il2cpp_call_exact",args,ToolDispatcher._invoke_token))
        self.assertEqual(cmd,"IL2CPP_CALL_EXACT")
        self.assertTrue(words.endswith(" g9"))
        self.assertIn(" "+token+" ",words)
        with self.assertRaises(BridgeError):ToolDispatcher._invoke_token({"fields":[]})

    def test_generic_constraints_and_schema_routing(self):
        selection={"image":"mscorlib.dll","namespace":"System","class_name":"Int32"}
        with self.assertRaises(ValueError):w.encode("il2cpp_generic_resolve",{"type":selection,"method_arguments":[selection]})
        cmd,args=decode_frame(w.encode("il2cpp_parameter_schema",{"image":"A.dll","class":"C","token":1,"generic_handle":"9"}))
        self.assertEqual(cmd,"IL2CPP_PARAMETER_SCHEMA");self.assertTrue(args.endswith(" g9"))

    def test_out_argument_placeholder_preserves_exact_call_protocol(self):
        options={"image":"mscorlib.dll","namespace":"System","class":"Int32",
                 "token":100665246,"arguments":["7878",None]}
        command,args=decode_frame(w.encode("il2cpp_call_exact",options,ToolDispatcher._invoke_token))
        self.assertEqual(command,"IL2CPP_CALL_EXACT")
        self.assertEqual(args.split()[-3:],["2","s37383738","z"])

    def test_help_uses_local_schema_not_target(self):
        dispatcher=ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        with patch.object(dispatcher,"_json_call",side_effect=AssertionError("must not query old target")):
            help=dispatcher.debug_help({"command":"unity_resource_monitor"})
        self.assertEqual(help["source"],"local_mcp_catalog")
        self.assertIn("op",help["inputSchema"]["properties"])

if __name__ == "__main__": unittest.main()
