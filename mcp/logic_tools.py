"""AI-authored native logic programs. Independent of the optional Lua runtime."""
from __future__ import annotations
import json
try:
    from . import render_tools as r, workspace_tools as w
except ImportError:
    import render_tools as r, workspace_tools as w

ID = {"type": "string", "minLength": 1, "maxLength": 24, "pattern": r"[A-Za-z0-9_-]+"}
SOURCE = w.obj({"id": ID, "image": {**r.TEXT, "minLength": 1}, "namespace": r.TEXT, "class": {**r.TEXT, "minLength": 1},
                "limit": w.integer(1, 128), "include_inactive": w.B, "select_new": w.B,
                "refresh_ms": w.integer(20, 60000)}, ("id", "image", "class"))
PROGRAM = w.obj({"id": ID, "title": {"type": "string", "maxLength": 128},
                 "version": w.integer(1, 1), "enabled": w.B, "auto_start": w.B, "allow_calls": w.B,
                 "functions": {"type": "object", "maxProperties": 32, "additionalProperties": w.obj({
                     "params": {"type": "array", "maxItems": 16, "items": {"type": "string", "maxLength": 64}},
                     "steps": {"type": "array", "maxItems": 128, "items": {"type": "object"}}}, ("params", "steps"))},
                 "interval_ms": w.integer(20, 60000), "budget_us": w.integer(500, 12000),
                 "max_steps": w.integer(100, 100000), "default_visible": w.B,
                 "sources": w.array(SOURCE, 4),
                 "steps": {"type": "array", "maxItems": 128, "items": {"type": "object"}}},
                ("id", "steps"))
TOOLS = [
    r.tool("logic_program_schema", "Read the native JSON logic language, operators, limits and an example. No Lua or game calls are executed.", {}),
    r.tool("logic_program_validate", "Validate a complete native logic definition without saving or running it. Expressions use {var:name} or {op:name,args:[...]}; statements: let/store/if/foreach/while/return/assert/break/continue/emit/log. Named functions, bounded loops, math/collections/strings and staged exact managed calls. Read logic_program_schema first.", {"descriptor": PROGRAM}, ("descriptor",)),
    r.tool("logic_program_set", "Create or replace a native game-thread JSON program (64 KiB, up to 8 programs), independent of Lua. New programs default stopped. Own symbolic sources rediscover instances; select_new defaults true and is configurable; field/static_field paths are resolved from current objects each tick. emit render sets visible/label/style; emit variable updates UI bindings. interval_ms defaults 100, budget_us 6000, max_steps 20000. default_visible=false hides unclassified source objects. auto_start is an explicit preset startup choice; save the default workspace preset to persist. Runtime object caches and program state are not restored; use symbolic sources and static fields, never persist literal object addresses.", {"descriptor": PROGRAM}, ("descriptor",)),
    r.tool("logic_program_list", "List native programs, running state, execution time/steps, error path, bounded logs and runtime state.", {}),
    r.tool("logic_program_get", "Read one native logic program including its full definition and diagnostics.", {"id": ID}, ("id",)),
    r.tool("logic_program_control", "Start, stop or restart a native program. Restart clears state. Stop removes only its owned class trackers and display overrides; other programs and manual selections survive.", {"id": ID, "operation": r.enum("start", "stop", "restart")}, ("id", "operation")),
    r.tool("logic_program_remove", "Remove one native program and its owned class trackers. Does not destroy game objects.", {"id": ID}, ("id",)),
]
BY_NAME = {tool["name"]: tool for tool in TOOLS}
BY_NAME["logic_program_set"]["description"] += (
    " Optional functions={name:{params,steps}}; call expression invokes a local function (depth8)."
    " allow_calls=true enables emit kind=call with unique target id and value={image,namespace,class,token,instance,arguments};"
    " auto_start must be false. Calls execute on later game frames, not inline. call_result(id) returns pending/success/result/error."
    " Identical ids are deduplicated for the program lifetime (64 ids,8 new calls/tick); changed arguments with the same id fail."
    " Stop/error/remove cancels queued calls; already executed game changes cannot roll back. Explicit restart may repeat calls."
    " Unsupported runtime APIs fail only that call. get/list expose receipts; omitted large results can be queried by workspace_result while retained.")
for _name in ("logic_program_set", "logic_program_control"):
    BY_NAME[_name]["annotations"]["destructiveHint"] = True
for _name in ("logic_program_schema", "logic_program_validate", "logic_program_list", "logic_program_get"):
    BY_NAME[_name]["annotations"]["readOnlyHint"] = True
FEATURES = ("overlay_ui", "rendering", "il2cpp_metadata", "il2cpp_objects", "memory_read")

def _bounded(value, depth=0, count=None):
    if count is None:
        count = [0]
    count[0] += 1
    if depth > 32 or count[0] > 16384:
        raise ValueError("native logic exceeds depth/node limit")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("logic JSON keys must be strings")
            _bounded(child, depth + 1, count)
    elif isinstance(value, list):
        for child in value:
            _bounded(child, depth + 1, count)
    elif not isinstance(value, (str, int, float, bool, type(None))):
        raise ValueError("logic values must be JSON data")

def encode(name: str, args: dict) -> str:
    if name not in BY_NAME:
        raise ValueError("unknown native logic tool")
    r.validate(args, BY_NAME[name]["inputSchema"], name)
    command = name.upper()
    if name in {"logic_program_set", "logic_program_validate"}:
        value = args["descriptor"]
        _bounded(value)
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(data) > 65536:
            raise ValueError("native logic exceeds 64 KiB")
        return command + " " + data.hex()
    if name in {"logic_program_list", "logic_program_schema"}:
        return command
    suffix = " " + args["operation"] if name == "logic_program_control" else ""
    return command + " " + r.text(args["id"]) + suffix

def features(name: str) -> tuple[str, ...]:
    if name in {"logic_program_schema", "logic_program_validate"}:
        return ("overlay_ui",)
    return (*FEATURES, "il2cpp_invoke") if name in {"logic_program_set", "logic_program_control"} else FEATURES

def native_features(command: str) -> tuple[str, ...] | None:
    if command.startswith("LOGIC_PROGRAM_"):
        return features(command.lower())
    return None
