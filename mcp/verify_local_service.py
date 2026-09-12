"""Check the registered source and a fresh stdio server; never kills a client.

python -m mcp.verify_local_service --config C:/Users/DELL/.codex/config.toml
No ADB calls, game requests, config writes, hooks or Android builds.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib

EXPECTED={"unity_resource_monitor","unity_resource_preview","unity_resource_export",
          "il2cpp_generic_resolve","il2cpp_parameter_schema","frida_control",
          "memory_chain_export","workspace_browser","workspace_caller"}

def verify(config_path: Path):
    server=Path(__file__).with_name("mcp_server.py").resolve()
    config=tomllib.loads(config_path.read_text(encoding="utf-8"))
    installed=config.get("mcp_servers",{}).get("zygisk-il2cpp",{})
    args=installed.get("args",[])
    if not args or Path(args[0]).resolve()!=server:
        raise RuntimeError("zygisk-il2cpp is not registered to this checkout")
    interpreter=Path(installed.get("command",""))
    if not interpreter.is_file():raise RuntimeError("registered Python executable is missing")
    if installed.get("enabled") is False:raise RuntimeError("registered MCP server is disabled")
    requests=[{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"local-source-verifier","version":"1"}}},
              {"jsonrpc":"2.0","id":2,"method":"tools/list"},
              {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"debug_help","arguments":{"command":"frida_control"}}}]
    # Isolate feature configuration; do not enable/disable the user's switches.
    with tempfile.TemporaryDirectory(prefix="zygisk-mcp-verify-") as folder:
        command=[str(interpreter),str(server),"--no-admin","--no-adb-forward","--feature-config",str(Path(folder)/"features.json")]
        result=subprocess.run(command,input="".join(json.dumps(r)+"\n" for r in requests),
            text=True,encoding="utf-8",capture_output=True,timeout=30,
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    if result.returncode:raise RuntimeError("fresh MCP process failed: "+result.stderr[:2000])
    responses={r["id"]:r for line in result.stdout.splitlines() if (r:=json.loads(line)).get("id") is not None}
    catalog=responses[2]["result"]["tools"];names={t["name"] for t in catalog}
    if missing:=EXPECTED-names:raise RuntimeError("missing tools: "+", ".join(sorted(missing)))
    help_result=responses[3]["result"]
    if help_result.get("isError") or help_result["structuredContent"]["source"]!="local_mcp_catalog":
        raise RuntimeError("new tools do not have working local help")
    return {"registered_source":str(server),"version":responses[1]["result"]["serverInfo"]["version"],
        "tool_count":len(catalog),"new_tools_verified":sorted(EXPECTED),
        "fresh_stdio_handshake":"passed","existing_client_restarted":False,
        "next_step":"Restart zygisk-il2cpp in the MCP client to refresh its current tool catalog."}

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,required=True)
    print(json.dumps(verify(parser.parse_args().config),ensure_ascii=False,indent=2))
