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
SEARCH_TYPES = r.enum("bool", "i8", "u8", "i16", "u16", "i32", "u32", "i64", "u64", "f32", "f64", "hex", "utf8", "utf16")
SEARCH_QUERY = w.obj({"mode": r.enum("exact", "fuzzy"), "types": w.array(SEARCH_TYPES, 8, 1),
    "value": {"type": "string", "maxLength": 2048}, "regions": {"type": "string", "minLength": 1, "maxLength": 256},
    "start": w.ADDRESS, "end": w.ADDRESS, "max_results": w.integer(1, 100000),
    "scan_mb": w.integer(1, 512), "timeout_ms": w.integer(100, 60000), "alignment": w.integer(1, 4096)})
ROW_IDS = w.array({"type": "string", "minLength": 1, "maxLength": 128}, 1000)
OFFSET = {"type": ["string", "integer"], "maxLength": 32}
RECIPE = w.obj({"module": {"type": "string", "maxLength": 512, "minLength": 1},
    "occurrence": w.integer(1, 4096), "base_address": r.ADDRESS, "base_offset": OFFSET,
    "offsets": w.array(OFFSET, 32), "pointer_size": {"type": "integer", "enum": [4, 8]},
    "dereference_final": w.B, "value_type": r.enum("bool", "i8", "u8", "i16", "u16", "i32", "u32", "i64", "u64", "f32", "f64"),
    "label": w.TEXT})
TOOLS = [
    r.tool("memory_search_tabs", "Shared GG-style memory workspace used by the manual UI and MCP. op=list/create/update/focus/duplicate/remove/clear/search/refine/results/select/save_selection/saved_list/saved_remove/export. Up to 512 independent tabs. create accepts name; duplicate copies query only. All tab ops require tab_id; optional version provides compare-and-swap. search accepts query {types:['i32'],value:'100;200::512',mode:'exact',regions:'anonymous,heap,app_data'}; semicolon=joint, :N unordered span, ::N ordered span, default 512; span counts distance between first bytes + 1. B/W/D/Q/F/E suffixes select signed byte/word/dword/qword/float/double. a~b is inclusive numeric range. hex accepts ?? and nibble wildcards; utf8/utf16 literal text. Search returns ALL witnessed group members, not A OR B. Fuzzy creates a bounded unknown-value snapshot. Refine mode=equals/not_equals/greater/less/changed/unchanged/increased/decreased/increased_by/decreased_by; value required for numeric comparisons. results merges tab sessions with per-row id/type, supports offset/limit/live (live does not change baseline). select: mode=add/remove with ids, or all/none. save_selection copies selected rows to a PROCESS-LOCAL saved list; no automatic replay after restart. export scope=selected/all, source=results/saved, destination=file/clipboard; files return status/path/count only. Reads use the WebUI-selected external root driver or local KittyMemory. Check truncated/stop_reason/read_error; not every GG grammar is supported. No memory writes: use memory_batch_edit or existing freeze tools.", {
        "op": r.enum("list", "create", "update", "focus", "duplicate", "remove", "clear", "search", "refine", "results", "select", "save_selection", "saved_list", "saved_remove", "export"),
        "tab_id": w.integer(1, 2**53-1), "version": w.integer(1, 2**53-1), "name": {"type": "string", "minLength": 1, "maxLength": 96},
        "query": SEARCH_QUERY, "mode": r.enum("equals", "not_equals", "greater", "less", "changed", "unchanged", "increased", "decreased", "increased_by", "decreased_by", "add", "remove", "all", "none"),
        "value": {"type": "string", "maxLength": 2048}, "offset": w.integer(0, 800000), "limit": w.integer(1, 1000), "live": w.B,
        "ids": ROW_IDS, "scope": r.enum("selected", "all"), "source": r.enum("results", "saved"), "destination": r.enum("file", "clipboard")}, ("op",)),
    r.tool("memory_batch_edit", "Write or freeze 1..256 selected memory-workspace rows using the selected data backend. source=results (default) requires tab_id and uses its current selection; source=saved requires explicit saved ids. value is exact text encoded separately for each row type. confirm=true is REQUIRED. Duplicate/overlapping addresses and non-writable/executable mappings are rejected. freeze=true creates/updates existing freeze tasks, interval_ms=20..60000; manage through existing memory_freeze_* tools. Batch is NOT atomic; reports requested/processed/succeeded and per-row errors, stops at first failure without falling back to another backend. Larger selections must be divided explicitly. No arbitrary PID or device-node arguments.", {
        "tab_id": w.integer(1, 2**53-1), "version": w.integer(1, 2**53-1), "source": r.enum("results", "saved"),
        "ids": ROW_IDS, "value": {"type": "string", "minLength": 1, "maxLength": 2048},
        "confirm": w.B, "freeze": w.B, "interval_ms": w.integer(20, 60000)}, ("value", "confirm")),
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
BY_NAME["memory_batch_edit"]["annotations"]["destructiveHint"] = True

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
    if name == "memory_search_tabs":
        return ("memory_search", "memory_read")
    if name == "memory_batch_edit":
        return ("memory_read", "memory_write")
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
    if command == "MEMORY_SEARCH_TABS": return features("memory_search_tabs")
    if command == "MEMORY_BATCH_EDIT": return features("memory_batch_edit")
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
    if name == "memory_batch_edit":
        if args["confirm"] is not True: raise ValueError("confirm=true is required")
        if args.get("source", "results") == "saved":
            if not args.get("ids") or "tab_id" in args or "version" in args: raise ValueError("saved edits require ids, not tab_id/version")
            if len(args["ids"]) > 256: raise ValueError("at most 256 saved ids per batch")
        elif "tab_id" not in args or "ids" in args: raise ValueError("result edits require tab_id and use its current selection")
        return "MEMORY_BATCH_EDIT " + w._json(args, 16384)
    if name == "memory_search_tabs":
        op = args["op"]
        optional = {"list": set(), "create": {"name"}, "update": {"name", "query"}, "focus": set(),
            "duplicate": set(), "remove": set(), "clear": set(), "search": {"query"}, "refine": {"mode", "value"},
            "results": {"offset", "limit", "live"}, "select": {"mode", "ids"}, "save_selection": {"name", "scope"},
            "saved_list": {"offset", "limit", "live"}, "saved_remove": {"ids"}, "export": {"scope", "source", "destination", "ids"}}[op]
        saved_export = op == "export" and args.get("source") == "saved"
        needs_tab = op not in {"list", "create", "saved_list", "saved_remove"} and not saved_export
        permitted = {"op"} | optional | ({"tab_id", "version"} if needs_tab else set())
        if set(args) - permitted: raise ValueError("arguments not used by " + op)
        if needs_tab and "tab_id" not in args: raise ValueError("tab_id is required")
        if op == "refine":
            modes = {"equals", "not_equals", "greater", "less", "changed", "unchanged", "increased", "decreased", "increased_by", "decreased_by"}
            if args.get("mode") not in modes: raise ValueError("invalid refine mode")
            needs_value = args["mode"] in {"equals", "not_equals", "greater", "less", "increased_by", "decreased_by"}
            if needs_value != ("value" in args): raise ValueError("value is required only for comparison/delta filtering")
        if op == "select":
            if args.get("mode") not in {"add", "remove", "all", "none"}: raise ValueError("invalid selection mode")
            if (args["mode"] in {"add", "remove"}) != ("ids" in args): raise ValueError("add/remove require ids; all/none do not")
        if op == "saved_remove" and not args.get("ids"): raise ValueError("saved_remove requires ids")
        if "query" in args:
            args = dict(args)
            query = dict(args["query"])
            args["query"] = query
            for key in ("start", "end"):
                if key in query: query[key] = r.address(query[key])
            if not query.get("types"): raise ValueError("query.types is required")
            if len(set(query["types"])) != len(query["types"]): raise ValueError("duplicate value types")
            if "regions" in query and any(x not in {"all", "anonymous", "heap", "stack", "app_code", "system_code", "app_data", "ashmem", "java", "other"} for x in query["regions"].split(",")):
                raise ValueError("unknown memory region")
            if "all" in query.get("regions", "").split(",") and query["regions"] != "all": raise ValueError("all must be used alone")
            if "start" in query and int(query["start"], 16) == 0: raise ValueError("start must be nonzero")
            if "start" in query and "end" in query and int(query["start"], 16) >= int(query["end"], 16): raise ValueError("end must be greater than start")
            if query.get("mode", "exact") == "exact" and not query.get("value") and op == "search": raise ValueError("query.value is required for exact search")
            if query.get("mode") == "fuzzy" and any(t in {"hex", "utf8", "utf16"} for t in query["types"]): raise ValueError("fuzzy requires scalar types")
        if op == "export":
            if args.get("source") == "saved":
                if args.get("scope", "selected") == "selected" and not args.get("ids"):
                    raise ValueError("selected saved export requires nonempty ids")
                if args.get("scope") == "all" and "ids" in args: raise ValueError("all saved export does not accept ids")
            elif "ids" in args: raise ValueError("result export uses tab selection, not ids")
        return "MEMORY_SEARCH_TABS " + w._json(args, 16384)
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
