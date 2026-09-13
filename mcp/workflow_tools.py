"""Persistent debug journals and workflow documents; encoding never contacts a target."""
from __future__ import annotations

import json
from typing import Any

try:
    from . import render_tools as r
except ImportError:
    import render_tools as r


def integer(low: int, high: int) -> dict[str, Any]:
    return {"type": "integer", "minimum": low, "maximum": high}


B = {"type": "boolean"}
ADDRESS = r.ADDRESS
DOCUMENT_ID = {
    "type": "string", "minLength": 1, "maxLength": 64,
    "pattern": r"[A-Za-z0-9_-]{1,64}",
}
REVISION = {
    "type": "string", "minLength": 1, "maxLength": 16,
    "pattern": r"[1-9][0-9]{0,15}",
    "description": "Exact decimal revision returned by debug_project; never a JSON number.",
}
PAGE_AFTER = {**DOCUMENT_ID, "description": "Exact next_after value from the preceding page."}
SNAPSHOT_LIMIT = {
    **integer(1, 256),
    "description": (
        "Operation-specific item limit: IL2CPP capture and diff accept 1..256; "
        "list accepts 1..100. It is not accepted by get/export or memory capture."
    ),
}
SNAPSHOT_OFFSET = {
    **integer(0, 65536),
    "description": (
        "Operation-specific start offset: IL2CPP capture accepts 0..10000 (global breadth-first field ordinal for object, item index for list/dictionary); "
        "diff accepts 0..65536 changed entries. It is not accepted by list/get/export or memory capture."
    ),
}
DECIMAL_CURSOR = {
    "type": "string", "minLength": 1, "maxLength": 20,
    "pattern": r"(?:0|[1-9][0-9]{0,19})",
    "description": "Exact decimal next_cursor returned by journal_query; never a JSON number.",
}
JOURNAL_SESSION = {
    "type": "string", "minLength": 1, "maxLength": 160,
    "pattern": r"session-[0-9]+-[0-9]+-[0-9]+(?:-part-[1-9][0-9]*)?\.jsonl",
    "description": "Exact journal session filename returned by journal_status or journal_query list.",
}
DEBUG_SESSION = {
    "type": "string", "minLength": 3, "maxLength": 41,
    "pattern": r"[1-9][0-9]{0,19}-[1-9][0-9]{0,19}",
    "description": "Exact opaque session string returned by change_history; never normalize or reuse it.",
}
LABEL = {"type": "string", "maxLength": 256}
JSON_ITEM = {
    "type": ["object", "array", "string", "number", "boolean", "null"],
    "maxLength": 60000,
}
PROJECT_DATA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "maxLength": 256},
        "goal": {"type": "string", "maxLength": 8192},
        "notes": {"type": "string", "maxLength": 16384},
        **{
            key: {"type": "array", "items": JSON_ITEM, "maxItems": 256}
            for key in ("tasks", "findings", "bookmarks", "artifacts", "next_actions", "observations")
        },
    },
    "additionalProperties": False,
}


TOOLS = [
    r.tool(
        "journal_status",
        "Read persistent root-journal health, current exact session filename, client session, async queue counters and write failures. Does not create, clear or delete logs.",
        readonly=True,
    ),
    r.tool(
        "journal_query",
        "List persistent journal session files or read one bounded page. op=list uses cursor/limit; op=read uses the exact session filename plus byte cursor/limit and optional kind/command/failures_only filters. Preserve next_cursor and snapshot_bytes as decimal strings. A read page scans at most 256 KiB and returns at most 100 records/48 KiB; has_more requires another explicit call. Filesystem list ordering can change after refresh. This tool never clears logs.",
        {
            "op": r.enum("list", "read"), "session": JOURNAL_SESSION,
            "cursor": DECIMAL_CURSOR, "limit": integer(1, 100),
            "kind": {"type": "string", "maxLength": 64},
            "command": {"type": "string", "maxLength": 128},
            "failures_only": B,
        },
        ("op",), readonly=True,
    ),
    r.tool(
        "journal_export",
        "Copy one exact persistent journal session (current session when omitted) to a new root-owned, target-scoped JSONL file under /data/adb/zygisk_il2cpp_mcp/journal/<target>/exports/. Returns a receipt/path/byte count, never file contents, never overwrites or deletes an existing log.",
        {"session": JOURNAL_SESSION},
    ),
    r.tool(
        "diagnostic_export",
        "Create a new root-owned, target-scoped bounded diagnostic JSON bundle under /data/adb/zygisk_il2cpp_mcp/journal/<target>/exports/ for one exact journal session (current session when omitted). Includes journal/backend/module/OS diagnostics and a bounded recent event tail; excludes raw memory and system-wide crash logs. Returns only a receipt/path.",
        {"session": JOURNAL_SESSION},
    ),
    r.tool(
        "debug_project",
        "Persistent target-version-scoped debug projects. op=list pages IDs with after/limit; create accepts optional safe id plus data; get reads a full project; update merges supplied data and requires the exact revision; archive irreversibly marks the project archived and requires its exact revision; summary returns bounded resume fields without replay; export creates a new target-private JSON file. Projects persist symbolic notes/configuration only and never replay execution or trust saved live addresses after restart.",
        {
            "op": r.enum("list", "create", "get", "update", "archive", "summary", "export"),
            "id": DOCUMENT_ID, "after": PAGE_AFTER, "limit": integer(1, 100),
            "revision": REVISION, "data": PROJECT_DATA,
        },
        ("op",),
    ),
    r.tool(
        "debug_snapshot",
        "Persistent bounded debug snapshots. op=capture kind=object/list/dict/dictionary queues an IL2CPP game-frame capture (limit 1..256, offset 0..10000); object pagination uses a global breadth-first field ordinal, and each page is a fresh capture. kind=memory performs a checked memory read and accepts size instead of limit/offset. Historical addresses are records, not reusable identities. list pages IDs with after/limit (limit 1..100); get reads one; diff compares before/after_id with offset 0..65536 and limit 1..256 and requires allow_cross_session=true for differing sessions/targets. Memory diff covers all saved bytes in address order. export creates a target-private JSON file. Inspect next_offset, has_more, partial and stop_reasons; snapshots are not coherent whole-process snapshots.",
        {
            "op": r.enum("capture", "list", "get", "diff", "export"),
            "kind": r.enum("object", "list", "dict", "dictionary", "memory"),
            "address": ADDRESS, "label": LABEL, "project": DOCUMENT_ID,
            "size": integer(1, 65536), "depth": integer(0, 4),
            "limit": SNAPSHOT_LIMIT, "offset": SNAPSHOT_OFFSET,
            "budget_ms": integer(1, 100), "after": PAGE_AFTER, "id": DOCUMENT_ID,
            "before": DOCUMENT_ID, "after_id": DOCUMENT_ID, "allow_cross_session": B,
        },
        ("op",),
    ),
    r.tool(
        "change_history",
        "Persistent bridge memory-write/code-patch history. op=list pages redacted summaries with after/limit; get reads one full record; export creates a target-private JSON file; undo requires id, the exact current session returned by list/get, and confirm=true. Undo revalidates session, backend, mapping and current bytes, refuses frozen/conflicting addresses, and is not a general transaction rollback.",
        {
            "op": r.enum("list", "get", "export", "undo"), "id": DOCUMENT_ID,
            "after": PAGE_AFTER, "limit": integer(1, 100),
            "session": DEBUG_SESSION, "confirm": B,
        },
        ("op",),
    ),
    r.tool(
        "debug_stop_all",
        "Stop or pause all managed repeating debug work: debugger-owned paused threads, freezes, traces, sampling breakpoints, Frida tasks, native logic and UI programs. Requires confirm=true. This is best-effort per component and does NOT restore memory changes, remove retained instrumentation, undo accepted game calls or revert arbitrary hook side effects.",
        {"confirm": B}, ("confirm",),
    ),
]

BY_NAME = {tool["name"]: tool for tool in TOOLS}
for _name in ("debug_project", "change_history", "debug_stop_all"):
    BY_NAME[_name]["annotations"]["destructiveHint"] = True


def _json(data: dict[str, Any], limit: int) -> str:
    try:
        raw = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("workflow request is not bounded JSON") from exc
    if len(raw.encode("utf-8")) > limit:
        raise ValueError(f"workflow request exceeds {limit} UTF-8 bytes")
    return raw


def _cursor(value: str) -> None:
    if int(value, 10) > 2**64 - 1:
        raise ValueError("journal cursor exceeds unsigned 64-bit range")


def _fields(args: dict[str, Any], required: set[str], optional: set[str], op: str) -> None:
    if required - args.keys() or args.keys() - required - optional:
        raise ValueError(f"fields do not match {op} operation")


def _document(command: str, args: dict[str, Any], *, limit: int = 65536) -> str:
    return f"{command} {r.text(_json(args, limit))}"


def encode(name: str, args: dict[str, Any]) -> str:
    if name not in BY_NAME or not isinstance(args, dict):
        raise ValueError("unknown workflow tool or invalid arguments")
    r.validate(args, BY_NAME[name]["inputSchema"], "arguments")

    if name == "journal_status":
        return "JOURNAL_STATUS"
    if name == "journal_query":
        op = args["op"]
        optional = {"cursor", "limit"} if op == "list" else {
            "session", "cursor", "limit", "kind", "command", "failures_only",
        }
        _fields(args, {"op"}, optional, f"journal {op}")
        if "cursor" in args:
            _cursor(args["cursor"])
        return _document("JOURNAL_QUERY", args, limit=32768)
    if name in {"journal_export", "diagnostic_export"}:
        return _document("JOURNAL_EXPORT" if name == "journal_export" else "DIAGNOSTIC_EXPORT", args, limit=32768)

    if name == "debug_project":
        op = args["op"]
        required = {
            "list": {"op"}, "create": {"op", "data"}, "get": {"op", "id"},
            "update": {"op", "id", "revision", "data"},
            "archive": {"op", "id", "revision"}, "summary": {"op", "id"},
            "export": {"op", "id"},
        }[op]
        optional = {"after", "limit"} if op == "list" else {"id"} if op == "create" else set()
        _fields(args, required, optional, f"project {op}")
        if "data" in args:
            _json(args["data"], 60000)
        return _document("WORKSPACE_PROJECT", args)

    if name == "debug_snapshot":
        op = args["op"]
        if op == "capture":
            kind = args.get("kind", "object")
            if kind == "memory":
                _fields(args, {"op", "kind", "address"}, {"size", "label", "project"}, "memory snapshot capture")
            else:
                _fields(args, {"op", "address"}, {"kind", "label", "project", "depth", "limit", "offset", "budget_ms"}, "IL2CPP snapshot capture")
                if args.get("offset", 0) > 10000:
                    raise ValueError("capture offset exceeds 10000")
            data = {key: value for key, value in args.items() if key != "op"}
            data["address"] = r.address(data["address"])
            if data["address"] == "0x0":
                raise ValueError("snapshot address must be nonzero")
            if kind == "memory":
                data.pop("kind", None)
                return _document("WORKSPACE_MEMORY_CAPTURE", data, limit=8192)
            data["kind"] = "dictionary" if kind == "dict" else kind
            # IL2CPP_SNAPSHOT_CAPTURE itself consumes one hex JSON token. WORKSPACE_QUERY
            # consumes the hex encoding of that entire token, hence the deliberate double hex.
            inner_arguments = r.text(_json(data, 8192))
            return f"WORKSPACE_QUERY IL2CPP_SNAPSHOT_CAPTURE {r.text(inner_arguments)}"
        required = {
            "list": {"op"}, "get": {"op", "id"}, "export": {"op", "id"},
            "diff": {"op", "before", "after_id"},
        }[op]
        optional = {"after", "limit"} if op == "list" else (
            {"offset", "limit", "allow_cross_session"} if op == "diff" else set()
        )
        _fields(args, required, optional, f"snapshot {op}")
        if op == "list" and args.get("limit", 50) > 100:
            raise ValueError("snapshot list limit exceeds 100")
        return _document("WORKSPACE_SNAPSHOT", args)

    if name == "change_history":
        op = args["op"]
        required = {"list": {"op"}, "get": {"op", "id"}, "export": {"op", "id"},
                    "undo": {"op", "id", "session", "confirm"}}[op]
        optional = {"after", "limit"} if op == "list" else set()
        _fields(args, required, optional, f"change {op}")
        if op == "undo" and args["confirm"] is not True:
            raise ValueError("undo requires confirm=true")
        return _document("WORKSPACE_CHANGES", args, limit=8192)

    if name == "debug_stop_all":
        if args["confirm"] is not True:
            raise ValueError("stop all requires confirm=true")
        return _document("WORKSPACE_STOP", args, limit=8192)
    raise ValueError("unimplemented workflow tool")


def features(name: str) -> tuple[str, ...]:
    if name not in BY_NAME:
        raise ValueError("unknown workflow tool")
    return ("diagnostics",)


def extra_features(name: str, args: dict[str, Any]) -> set[str]:
    if name == "debug_snapshot" and args.get("op") == "capture":
        if args.get("kind", "object") == "memory":
            return {"memory_read"}
        return {"il2cpp_metadata", "il2cpp_objects", "il2cpp_invoke"}
    if name == "change_history" and args.get("op") == "undo":
        return {"memory_read", "memory_write"}
    return set()


def native_features(command: str) -> tuple[str, ...] | None:
    if command in {"JOURNAL_STATUS", "JOURNAL_QUERY", "JOURNAL_EXPORT", "DIAGNOSTIC_EXPORT",
                   "WORKSPACE_PROJECT", "WORKSPACE_SNAPSHOT", "WORKSPACE_STOP"}:
        return ("diagnostics",)
    if command == "WORKSPACE_MEMORY_CAPTURE":
        return ("diagnostics", "memory_read")
    if command == "IL2CPP_SNAPSHOT_CAPTURE":
        return ("diagnostics", "il2cpp_metadata", "il2cpp_objects", "il2cpp_invoke")
    if command == "WORKSPACE_CHANGES":
        # Raw calls do not receive typed op validation and may contain undo.
        return ("diagnostics", "memory_read", "memory_write")
    return None
