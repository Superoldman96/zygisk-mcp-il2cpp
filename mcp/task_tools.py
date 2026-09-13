"""Unified read-only background jobs; exact command-level feature requirements."""
from __future__ import annotations
import json
try:
    from . import render_tools as r
except ImportError:
    import render_tools as r

READ_COMMANDS = (
    "CAPABILITIES", "HELP", "IL2CPP_STATUS", "IL2CPP_IMAGES", "IL2CPP_CLASSES",
    "IL2CPP_METHODS", "IL2CPP_FIELDS", "IL2CPP_FIND_METHOD", "IL2CPP_SEARCH", "IL2CPP_TYPE_GRAPH",
    "IL2CPP_RELATION_FIND", "IL2CPP_RELATION_RESULTS",
    "MEMORY_MODULES", "MEMORY_MODULE_FIND", "MEMORY_ADDRESS_INFO", "MEMORY_READ",
    "MEMORY_SEARCH", "MEMORY_SEARCH_FUZZY", "MEMORY_SEARCH_TYPED", "MEMORY_SEARCH_RESULTS",
    "MEMORY_POINTER_SCAN_MT", "MEMORY_CHAIN_SCAN", "MEMORY_CHAIN_BATCH",
    "ASM_STATUS", "ASM_DISASSEMBLE", "DECOMP_STATUS", "DECOMP_DECOMPILE",
    "DOBBY_LIST_HOOKS", "DOBBY_TRACE_LIST", "DOBBY_TRACE_HITS", "DOBBY_TRACE_HIT",
    "BREAKPOINT_STATUS", "BREAKPOINT_LIST", "BREAKPOINT_HITS", "BREAKPOINT_BACKTRACE",
)


def integer(low, high):
    return {"type": "integer", "minimum": low, "maximum": high}


TOOLS = [r.tool("workspace_jobs", "Unified session-local background jobs, including existing manual UI jobs. op=list/get/cancel/submit. list accepts state,offset,limit; states queued/running/succeeded/failed/cancel_requested/cancelled. submit accepts only command from exact read-only allowlist plus its SINGLE-LINE native arguments (text params still hex-encoded per debug_help); accepted=true is NOT completion. get requires task_id; include_result=true additionally requires expected_command matching the task and its enabled feature. Inline result <=256KiB; larger cached results export via existing workspace_export_job using owner. cancel removes queued work; running allowlisted read-only work receives cooperative cancellation, and cancel_requested is NOT cancelled until acknowledged. Running mutations cannot be cancelled; no thread termination. Some operations have no checkpoint and can finish before cancellation. Inspect cancel_outcome. Result caches/history are bounded and session-local, separate from persistent command logs; restarting never replays jobs. Never submits Lua, invocations, writes, nested workspace commands or arbitrary PIDs. No fabricated progress percentage; includes real elapsed time/cache bytes.",
    {"op": r.enum("list", "get", "cancel", "submit"),
     "offset": integer(0, 1000000), "limit": integer(1, 256),
     "state": r.enum("queued", "running", "succeeded", "failed", "cancel_requested", "cancelled"),
     "task_id": {"type": "string", "minLength": 1, "maxLength": 128},
     "command": r.enum(*READ_COMMANDS), "expected_command": r.enum(*READ_COMMANDS),
     "arguments": {"type": "string", "maxLength": 60000},
     "include_result": {"type": "boolean"}}, ("op",))]
BY_NAME = {item["name"]: item for item in TOOLS}


def encode(name: str, args: dict) -> str:
    if name not in BY_NAME:
        raise ValueError("unknown task tool")
    r.validate(args, BY_NAME[name]["inputSchema"], "arguments")
    op = args["op"]
    required = {"list": {"op"}, "get": {"op", "task_id"}, "cancel": {"op", "task_id"},
                "submit": {"op", "command", "arguments"}}[op]
    optional = {"list": {"offset", "limit", "state"}, "get": {"include_result", "expected_command"},
                "cancel": set(), "submit": set()}[op]
    if not required <= args.keys() or not args.keys() <= required | optional:
        raise ValueError(f"invalid {op} fields")
    if args.get("include_result") and "expected_command" not in args:
        raise ValueError("include_result requires expected_command from task metadata")
    if "expected_command" in args and not args.get("include_result"):
        raise ValueError("expected_command only used with include_result=true")
    if op == "submit" and (len(args["arguments"].encode("utf-8")) > 60000 or any(c in args["arguments"] for c in "\0\r\n")):
        raise ValueError("native arguments must be a single NUL-free line <=60000 UTF-8 bytes")
    data = json.dumps(args, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data) > 65536:
        raise ValueError("task request exceeds 65536 bytes")
    return "WORKSPACE_JOBS " + data.hex()


def command_features(command: str) -> tuple[str, ...]:
    if command not in READ_COMMANDS:
        raise ValueError("task command not allowlisted")
    if command.startswith("IL2CPP_"): return ("il2cpp_metadata",)
    if command == "MEMORY_CHAIN_BATCH": return ("memory_maps", "memory_read", "pointer_chain")
    if command in {"MEMORY_CHAIN_SCAN", "MEMORY_POINTER_SCAN_MT"}: return ("memory_maps", "memory_read", "memory_search", "pointer_chain")
    if command.startswith("MEMORY_SEARCH"): return ("memory_read", "memory_search")
    if command == "MEMORY_READ": return ("memory_read",)
    if command.startswith("MEMORY_"): return ("memory_maps",)
    if command == "ASM_STATUS": return ("assembly",)
    if command == "ASM_DISASSEMBLE": return ("assembly", "memory_read")
    if command == "DECOMP_STATUS": return ("decompiler",)
    if command == "DECOMP_DECOMPILE": return ("decompiler", "memory_read")
    if command.startswith("DOBBY_TRACE"): return ("dobby", "trace")
    if command.startswith("DOBBY_"): return ("dobby",)
    if command.startswith("BREAKPOINT_"): return ("breakpoint",)
    return ("diagnostics",)


def features(name: str) -> tuple[str, ...]:
    return ("diagnostics",)


def extra_features(name: str, args: dict) -> set[str]:
    if name != "workspace_jobs": return set()
    if args.get("op") == "submit": return set(command_features(args["command"]))
    if args.get("op") == "get" and args.get("include_result"):
        return set(command_features(args["expected_command"]))
    return set()


def native_features(command: str) -> tuple[str, ...] | None:
    # Raw generic calls do not receive typed operation validation: conservatively
    # require every covered feature so a raw nested submit cannot bypass a gate.
    if command != "WORKSPACE_JOBS": return None
    return tuple(sorted({feature for cmd in READ_COMMANDS for feature in command_features(cmd)}))
