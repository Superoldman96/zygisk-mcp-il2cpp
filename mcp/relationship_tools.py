"""Multi-selector, actionable type relationships; no duplicate memory APIs.

Named selections grow through append batches. Capacity is a byte budget, not a
two/three-type signature. Live address resolution always uses the game queue.
"""
from __future__ import annotations

import json
try:
    from . import render_tools as r
    from . import workspace_tools as w
except ImportError:
    import render_tools as r
    import workspace_tools as w

TYPE = w.obj({"image": {"type": "string", "minLength": 1, "maxLength": 512},
              "namespace": {"type": "string", "maxLength": 512},
              "class_name": {"type": "string", "minLength": 1, "maxLength": 512},
              "field": {"type": "string", "minLength": 1, "maxLength": 512}}, ("image", "class_name"))
NAME = {"type": "string", "minLength": 1, "maxLength": 96}
REVISION = {"type": "string", "pattern": r"[1-9][0-9]{0,19}", "maxLength": 20}
SESSION = {"type": "string", "pattern": r"[0-9]+-[0-9]+-[0-9]+", "maxLength": 64,
           "description": "Opaque process-epoch search ID returned by relation_find; never reuse after target restart."}
OFFSET = {"type": ["string", "integer"], "maxLength": 32,
          "minimum": -(2**53 - 1), "maximum": 2**53 - 1}
RECIPE = w.obj({"module": {"type": "string", "minLength": 1, "maxLength": 512},
    "occurrence": w.integer(1, 4096), "base_address": w.ADDRESS, "base_offset": OFFSET,
    "offsets": w.array(OFFSET, 32), "pointer_size": {"type": "integer", "enum": [4, 8]},
    "dereference_final": w.B, "label": {"type": "string", "maxLength": 256}}, ("offsets",))
PAGE = {"offset": w.integer(0, 2**53 - 1), "limit": w.integer(1, 256)}
TOOLS = [
    r.tool("il2cpp_relation_selection", "Manage named multi-class relationship selections: create/list/get/append/remove/clear/delete. Append arbitrary numbers via batches <=128 selectors, not fixed two/three arguments; aggregate process-local selection budget 4 MiB and 64 named sets. Get returns revision; mutations require that decimal-string revision, preventing lost updates. Select exact image/namespace/class_name. A field on the first ordered selector constrains the first edge (World.player); a field on a later selector is an exact terminal field (Player.health), including scalar endpoints. Selections do not persist raw runtime addresses. Use debug projects to retain symbolic configuration.",
        {"op": r.enum("create", "list", "get", "append", "remove", "clear", "delete"), "name": NAME,
         "revision": REVISION, "types": w.array(TYPE, 128, 1), **PAGE}, ("op",)),
    r.tool("il2cpp_relation_find", "Find directed field paths across all selectors in a named set; intermediate unselected classes allowed. any returns links between selected types (one selector explores outgoing paths). all returns a connecting NETWORK and all_connected/components, not necessarily one chain through all types. ordered returns acyclic paths visiting selectors in supplied order. max_depth<=15; all budgets return explicit partial stop_reasons. Metadata references (optional inheritance/interfaces), static fields, references, inline value types and scalar endpoints are distinguished; only valid instance-field paths are loadable. Returns summary/session_id; page paths/nodes/edges using relation_results. Latest four successful searches retained per process. No methods invoked.",
        {"set": NAME, "revision": REVISION, "mode": r.enum("any", "all", "ordered"),
         "max_depth": w.integer(1, 15), "max_results": w.integer(1, 512), "max_nodes": w.integer(16, 4096),
         "work_limit": w.integer(100, 100000), "time_ms": w.integer(50, 10000), "include_metadata": w.B}, ("set", "revision"), readonly=True),
    r.tool("il2cpp_relation_results", "Page a saved relationship search's paths/nodes/edges. Paths display DeclaringType.field(0xOFFSET) -> ... and include symbolic field/type descriptors, kind and loadable. Offset is NOT an absolute address. After resolving a path, navigate address via workspace_navigate kind=object/memory; field/type descriptors use existing type tools. Response next_offset accounts for the 192-KiB page byte budget. Expired sessions fail explicitly.",
        {"session_id": SESSION, "kind": r.enum("paths", "nodes", "edges", "selection"), **PAGE}, ("session_id",), readonly=True),
    r.tool("il2cpp_relation_resolve", "Queue checked live traversal of a saved LOADABLE relationship path. Supply exactly one root_address (actual managed object) or root_recipe (existing module/pointer-chain recipe whose final address is the root object). Use dereference_final=true if recipe resolves an object pointer slot. Verifies exact class identity, field layout, object bounds and every copied read; expired/wrong/derived-only headers fail instead of invoking unknown APIs. Returns pending/request_id; poll workspace_result. Result nodes contain type, storage_address, object address, scalar/string text and render eligibility for clickable inspection, memory edit/freeze and existing render tools. Static/inheritance/interface paths cannot be dereferenced. Strings have bounded UTF-16 decoding and truncation status. No writes/getters/constructors are executed.",
        {"session_id": SESSION, "path_id": w.integer(0, 511), "root_address": w.ADDRESS,
         "root_recipe": RECIPE, "max_string_chars": w.integer(1, 8192)}, ("session_id", "path_id"), readonly=True),
]
BY_NAME = {tool["name"]: tool for tool in TOOLS}
COMMANDS = {"il2cpp_relation_selection": "IL2CPP_RELATION_SELECTION", "il2cpp_relation_find": "IL2CPP_RELATION_FIND",
            "il2cpp_relation_results": "IL2CPP_RELATION_RESULTS", "il2cpp_relation_resolve": "IL2CPP_RELATION_RESOLVE"}

def encode(name: str, args: dict) -> str:
    if name not in BY_NAME or not isinstance(args, dict):
        raise ValueError("unknown relationship tool or invalid arguments")
    r.validate(args, BY_NAME[name]["inputSchema"], name)
    w._bounded_json(args)
    if name == "il2cpp_relation_selection":
        op = args["op"]
        required = {"list": set(), "create": {"name"}, "get": {"name"},
                    "append": {"name", "revision", "types"}, "remove": {"name", "revision", "types"},
                    "clear": {"name", "revision"}, "delete": {"name", "revision"}}[op]
        allowed = required | {"op", "offset", "limit"}
        if required - args.keys() or args.keys() - allowed:
            raise ValueError("fields do not match relationship selection operation")
    if name == "il2cpp_relation_resolve":
        if ("root_address" in args) == ("root_recipe" in args):
            raise ValueError("exactly one root_address or root_recipe is required")
        recipe = args.get("root_recipe")
        if recipe is not None and (("module" in recipe) == ("base_address" in recipe)):
            raise ValueError("root_recipe requires exactly one module or base_address")
    raw = json.dumps(args, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > 24 * 1024:
        raise ValueError("relationship JSON exceeds 24 KiB; split selection append batches")
    command, payload = COMMANDS[name], r.text(raw)
    if name == "il2cpp_relation_resolve":
        return f"WORKSPACE_QUERY {command} {r.text(payload)}"
    return f"{command} {payload}"

build_command = encode

def features(name: str) -> tuple[str, ...]:
    return ("il2cpp_metadata", "il2cpp_objects", "memory_read") if name == "il2cpp_relation_resolve" else ("il2cpp_metadata",)

def extra_features(name: str, args: dict) -> set[str]:
    if name == "il2cpp_relation_resolve" and "root_recipe" in args:
        return {"memory_maps", "pointer_chain"}
    return set()

def native_features(command: str) -> tuple[str, ...] | None:
    for name, native in COMMANDS.items():
        if command == native:
            return features(name)
    return None
