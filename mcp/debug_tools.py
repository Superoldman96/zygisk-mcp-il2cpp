"""Additional debugger management and type graphs; no target actions during import."""
from __future__ import annotations
import json
from typing import Any
try:
    from . import render_tools as r, workspace_tools as w
except ImportError:
    import render_tools as r
    import workspace_tools as w

TYPE_SELECTION = {"image": {**w.TEXT, "maxLength": 512},
                  "namespace": {**w.TEXT, "maxLength": 512},
                  "class_name": {"type": "string", "minLength": 1, "maxLength": 512}}
VALUE_TYPES = r.enum("bool", "i8", "u8", "i16", "u16", "i32", "u32", "i64", "u64", "f32", "f64", "hex")
OFFSET = {"type": ["string", "integer"], "maxLength": 32}
RECIPE = w.obj({"module": {"type": "string", "maxLength": 512, "minLength": 1},
    "occurrence": w.integer(1, 4096), "base_address": r.ADDRESS, "base_offset": OFFSET,
    "offsets": w.array(OFFSET, 32), "pointer_size": {"type": "integer", "enum": [4, 8]},
    "dereference_final": w.B, "value_type": r.enum("bool", "i8", "u8", "i16", "u16", "i32", "u32", "i64", "u64", "f32", "f64"),
    "label": w.TEXT})
TOOLS = [
    r.tool("memory_chain_export", "Export ALL currently cached results from one pointer scan session to a new target-private JSON file, never just the visible/current page. session_id comes from memory_scan_base.search; scan_kind=chains (default) selects a multi-level chain scan, pointers selects legacy exact-pointer candidates. Keeps original scan state, truncated/stop reasons, budgets and actual cached/exported counts in the file. Full export does not make a truncated scan complete. Chain JSON preserves module+offset recipes and recipe_batches reusable as memory_chain_batch arguments. Latest four successful chain sessions are retained; expired/non-pointer sessions fail without rescanning. Atomic unique publication never overwrites user files. Returns success/path/count only, never the file contents or result list.", {
        "session_id": w.integer(1, 2**53-1), "scan_kind": r.enum("chains", "pointers")}, ("session_id",)),
    r.tool("memory_chain_batch", "Resolve/read 1..128 pointer chains or saved IDs. Returns an object with results (per-item success/error), total, succeeded and failed; an item failure does not discard other results. Recipe semantics match memory_resolve_pointer_chain: base+base_offset, then dereference and add each offset; optional final dereference. Values use exact text. Re-resolves modules, never writes memory. No promise of an atomic snapshot. Use existing single-chain tools for one-off calls.", {
        "chains": w.array({**RECIPE, "type": ["object", "string"], "minLength": 1, "maxLength": 64}, 128, 1)}, ("chains",), readonly=True),
    r.tool("memory_chain_store", "List/save/remove named pointer-chain recipes in target-private settings/chains.json. list returns an object with chains and total (including an empty list); save/remove keep their success/path response. Save accepts MODULE-relative recipes only (no absolute base_address). Does not automatically resolve, freeze or write after restart. Use memory_chain_batch to load saved IDs. Game updates or duplicate module order can invalidate recipes.", {
        "operation": r.enum("list", "save", "remove"), "id": {"type": "string", "minLength": 1, "maxLength": 64},
        "recipe": RECIPE}, ("operation",)),
    r.tool("memory_freeze_set", "Create or update a recurring KittyMemory write at one writable non-executable address. Value is text (i64/u64 stay exact); hex accepts up to 128 raw bytes. Interval 20..60000 ms. Same address updates its task; overlapping tasks are rejected. Mapping changes/read/write errors stop only that task. Not persisted across process restarts; heap allocation reuse cannot always be detected. Inspect memory_freeze_list for actual writes/errors.", {
        "address": w.ADDRESS, "type": VALUE_TYPES, "value": {"type": "string", "maxLength": 512},
        "interval_ms": w.integer(20, 60000), "label": w.TEXT}, ("address", "type", "value")),
    r.tool("memory_freeze_list", "List all session freeze tasks, their enabled state, target value/address, successful write count and last error. This does not create or resume writes.", readonly=True),
    r.tool("memory_freeze_control", "Pause, resume or remove a freeze by id; clear removes ALL freezes. Resume rechecks the original mapping. clear accepts no id. Removing does not restore a previous memory value.", {
        "operation": r.enum("pause", "resume", "remove", "clear"), "id": w.integer(1, 2**53-1)}, ("operation",)),
    r.tool("dobby_trace_list", "List user-created Dobby tracers/counters, target addresses, enabled state, hit counts and dropped snapshots. Internal game-frame probes are not exposed as user tracers.", readonly=True),
    r.tool("dobby_trace_control", "Pause/resume/reset/remove one user tracer or counter. Pause leaves instrumentation installed but stops capture/counting. reset clears captured history/count; remove uninstalls only a managed user tracer.", {
        "address": w.ADDRESS, "operation": r.enum("pause", "resume", "reset", "remove")}, ("address", "operation")),
    r.tool("dobby_trace_hits", "Read newest-first captured hits for a user tracer. Each record includes id, address, count, tid and captured backtrace. The per-tracer history retains 256 snapshots independently of total hit count; capture can drop samples under contention. Counters do not walk the frame chain.", {
        "address": w.ADDRESS, "offset": w.integer(0, 256), "limit": w.integer(1, 256)}, ("address",), readonly=True),
    r.tool("dobby_trace_hit", "Read one retained trace hit and its captured backtrace by hit_id. Expired/reset/removed hits return an error. Does not resume the target or invoke game functions; optimized code without frame pointers may yield incomplete backtraces.", {
        "hit_id": w.integer(1, 2**53-1), "max_frames": w.integer(1, 32)}, ("hit_id",), readonly=True),
    r.tool("breakpoint_control", "Pause/resume/reset a managed hardware breakpoint task by address. Pause closes its perf events, resume opens them for target threads; reset clears that task's sample history/counts. Unsupported perf/ABI affects only breakpoints. Use existing breakpoint_clear to remove.", {
        "address": w.ADDRESS, "operation": r.enum("pause", "resume", "reset")}, ("address", "operation")),
    r.tool("il2cpp_type_graph", "Build a flowchart of outgoing TYPE dependencies for an exact class or namespace selection. image is optional; namespace='' explicitly selects the global namespace. Includes base classes, interfaces, field types and optional method parameter/return types. Returns indexed nodes/edges, Mermaid flowchart text and explicit truncated flag. Not a runtime object graph or incoming-reference scan. No property getters/game methods are invoked. Export Mermaid/JSON through existing workspace_export_text if needed.", {
        **TYPE_SELECTION, "depth": w.integer(0, 4), "max_nodes": w.integer(1, 128), "method_types": w.B}, readonly=True),
]
BY_NAME = {t["name"]: t for t in TOOLS}

def selection(args: dict[str, Any]) -> dict[str, Any]:
    r.validate(args, w.obj(TYPE_SELECTION), "selection")
    if args.get("class_name") == "":
        raise ValueError("class_name cannot be empty")
    return dict(args)

def normalize_recipe(recipe: dict[str, Any], persistent: bool = False) -> dict[str, Any]:
    r.validate(recipe, RECIPE, "recipe")
    has_module = bool(recipe.get("module"))
    has_address = "base_address" in recipe
    if has_module == has_address or (persistent and not has_module):
        raise ValueError("provide exactly one base; saved chains require module")
    value = dict(recipe)
    if has_address:
        value["base_address"] = r.address(value["base_address"])
        if value["base_address"] == "0x0": raise ValueError("base_address cannot be zero")
    def offset(raw: Any) -> str:
        if isinstance(raw, bool): raise ValueError("boolean is not an offset")
        try:
            n = int(raw, 0) if isinstance(raw, str) else int(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid pointer-chain offset") from exc
        if not -(2**63) <= n < 2**63: raise ValueError("offset outside signed 64-bit range")
        return str(n)
    value["base_offset"] = offset(value.get("base_offset", 0))
    value["offsets"] = [offset(x) for x in value.get("offsets", [])]
    value.setdefault("pointer_size", 8)
    return value

def features(name: str) -> tuple[str, ...]:
    if name.startswith("memory_chain_"):
        return ("memory_maps", "memory_read", "pointer_chain")
    if name == "il2cpp_type_graph":
        return ("il2cpp_metadata",)
    if name == "memory_freeze_list":
        return ("memory_read",)
    if name.startswith("memory_freeze_"):
        return ("memory_read", "memory_write")
    if name.startswith("dobby_trace_"):
        return ("dobby", "trace")
    return ("breakpoint",)

def native_features(command: str) -> tuple[str, ...] | None:
    if command == "MEMORY_CHAIN_SCAN":
        return ("memory_maps", "memory_search", "pointer_chain")
    if command.startswith("MEMORY_CHAIN_"):
        return features("memory_chain_batch")
    if command == "IL2CPP_TYPE_GRAPH":
        return ("il2cpp_metadata",)
    if command == "MEMORY_FREEZE_LIST":
        return features("memory_freeze_list")
    if command.startswith("MEMORY_FREEZE_") or command == "MEMORY_VALUE_WRITE":
        return features("memory_freeze_set")
    if command in {"MEMORY_SEARCH_TYPED", "MEMORY_FILTER_TYPED"}:
        return ("memory_search",)
    if command.startswith("DOBBY_TRACE_"):
        return ("dobby", "trace")
    return None

def encode(name: str, args: dict[str, Any]) -> str:
    r.validate(args, BY_NAME[name]["inputSchema"], "arguments")
    if name == "memory_chain_export":
        return "MEMORY_CHAIN_EXPORT " + w._json({"scan_kind": "chains", **args}, 8192)
    if name == "memory_chain_batch":
        batch = [normalize_recipe(x) if isinstance(x, dict) else x for x in args["chains"]]
        return "MEMORY_CHAIN_BATCH " + w._json(batch, 112*1024)
    if name == "memory_chain_store":
        op = args["operation"]
        expected = {"operation"} | ({"id", "recipe"} if op == "save" else {"id"} if op == "remove" else set())
        if set(args) != expected: raise ValueError("list: operation only; remove: id; save: id and recipe")
        data = dict(args)
        if op == "save": data["recipe"] = normalize_recipe(data["recipe"], persistent=True)
        return "MEMORY_CHAIN_STORE " + w._json(data, 112*1024)
    if name == "il2cpp_type_graph":
        selection({k: v for k, v in args.items() if k in TYPE_SELECTION})
        if not args.get("class_name") and "namespace" not in args:
            raise ValueError("select a class_name or namespace explicitly")
        return "IL2CPP_TYPE_GRAPH " + w._json(args, 8192)
    if name == "memory_freeze_set":
        data = dict(args); data["address"] = r.address(args["address"])
        if data["address"] == "0x0":
            raise ValueError("freeze needs a nonzero address")
        return "MEMORY_FREEZE_SET " + w._json(data, 8192)
    if name in {"memory_freeze_list", "dobby_trace_list"}:
        return "MEMORY_FREEZE_LIST" if name == "memory_freeze_list" else "DOBBY_TRACE_LIST"
    if name == "memory_freeze_control":
        op = args["operation"]
        if op == "clear":
            if "id" in args: raise ValueError("clear accepts no id")
            return "MEMORY_FREEZE_CLEAR"
        if "id" not in args: raise ValueError("id is required")
        if op == "remove": return f"MEMORY_FREEZE_REMOVE {args['id']}"
        return f"MEMORY_FREEZE_CONTROL {args['id']} {'true' if op == 'resume' else 'false'}"
    if name == "dobby_trace_hit":
        return f"DOBBY_TRACE_HIT {args['hit_id']} {args.get('max_frames', 32)}"
    address = r.address(args["address"])
    if address == "0x0": raise ValueError("nonzero address is required")
    if name == "dobby_trace_hits":
        return f"DOBBY_TRACE_HITS {address} {args.get('offset', 0)} {args.get('limit', 32)}"
    op = args["operation"]
    if name == "dobby_trace_control" and op == "remove":
        return f"DOBBY_TRACE_REMOVE {address}"
    return f"{'DOBBY_TRACE_CONTROL' if name == 'dobby_trace_control' else 'BREAKPOINT_CONTROL'} {address} {op}"
