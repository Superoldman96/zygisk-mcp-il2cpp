"""Explicit, chunked custom-SO loading in the already connected target only."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import time
import zlib
try:
    from . import render_tools as r
except ImportError:
    import render_tools as r

MAX_BYTES = 64 * 1024 * 1024
TOOLS = [
    r.tool("native_library_inject", "Upload a local SO from the MCP SERVER machine and load it in the already connected target (not an arbitrary PID or an offline app). No ADB file transfer required. Validates ELF ABI and transfer CRC32; optional expected_sha256 checks the local file. Requires allow_execute=true: constructors, and optionally JNI_OnLoad, execute in the target and CAN CRASH/HANG IT; unlike decompilation this cannot be isolated. Default RTLD_LOCAL; preload dependencies separately with global_symbols=true if needed. Returns an operation ID; if pending, poll native_library_status, NEVER reinject just because the wait expired. No automatic dlclose/unload. Loading does not grant access to Android-private dependency libraries.",
        {"path": {"type": "string", "minLength": 1, "maxLength": 4096},
         "allow_execute": {"type": "boolean", "enum": [True]},
         "expected_sha256": {"type": "string", "pattern": "^[a-fA-F0-9]{64}$"},
         "call_jni_onload": {"type": "boolean", "default": False},
         "global_symbols": {"type": "boolean", "default": False},
         "wait_seconds": {"type": "number", "minimum": 0, "maximum": 30, "default": 5}},
        ("path", "allow_execute")),
    r.tool("native_library_status", "List custom SO operations and target ABI, or inspect an operation ID. Reports received bytes, loading/loaded/failed state and full linker/JNI error. Read-only, remains available during a hanging constructor. Loaded code is pinned until process exit. Decompiler SO is managed separately by decompiler_status.",
        {"id": {"type": "string", "minLength": 1, "maxLength": 96}}, (), readonly=True),
]
BY_NAME = {tool["name"]: tool for tool in TOOLS}

def control(call, options):
    raw = json.dumps(options, separators=(",", ":"), ensure_ascii=True).encode().hex()
    return call("NATIVE_LIBRARY_CONTROL " + raw, timeout=15.0)

def execute(name, args, call):
    if name not in BY_NAME:
        raise ValueError("unknown native library tool")
    r.validate(args, BY_NAME[name]["inputSchema"], name)
    if name == "native_library_status":
        return control(call, {"op": "status", "id": args["id"]} if "id" in args else {"op": "list"})
    if args.get("allow_execute") is not True:
        raise ValueError("allow_execute=true is required; custom SO code executes inside target")
    # Open/read once: a file replaced during upload cannot change transferred data.
    path = Path(args["path"]).expanduser()
    if not path.is_file():
        raise ValueError("SO path must identify a regular file on the MCP server machine")
    with path.open("rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 64 <= info.st_size <= MAX_BYTES:
            raise ValueError("SO must be a regular file of 64 bytes to 64 MiB")
        data = stream.read(MAX_BYTES + 1)
    if len(data) != info.st_size or len(data) > MAX_BYTES:
        raise ValueError("SO file changed size while reading")
    if data[:4] != b"\x7fELF" or data[4] not in (1, 2) or data[5:7] != b"\x01\x01" or struct.unpack_from("<H", data, 16)[0] != 3:
        raise ValueError("expected a little-endian ELF shared library")
    digest = hashlib.sha256(data).hexdigest()
    if "expected_sha256" in args and digest != args["expected_sha256"].lower():
        raise ValueError("SO SHA-256 mismatch; nothing uploaded")
    target = control(call, {"op": "list"})
    if target.get("elf_bits") != (64 if data[4] == 2 else 32) or target.get("elf_machine") != struct.unpack_from("<H", data, 18)[0]:
        raise ValueError("SO architecture does not match connected target; nothing uploaded")
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", path.name)[:128] or "plugin.so"
    begin = control(call, {"op": "begin", "name": safe_name, "size": len(data), "crc32": zlib.crc32(data),
        "call_jni_onload": args.get("call_jni_onload", False), "global_symbols": args.get("global_symbols", False)})
    identity = begin.get("id")
    if not isinstance(identity, str) or not identity:
        raise ValueError("target did not return an upload ID")
    try:
        for offset in range(0, len(data), 32768):
            part = data[offset:offset + 32768]
            receipt = control(call, {"op": "chunk", "id": identity, "offset": offset, "hex": part.hex()})
            if receipt.get("id") != identity or receipt.get("received") != offset + len(part):
                raise ValueError("SO upload acknowledgement mismatch")
    except Exception:
        try:
            control(call, {"op": "cancel", "id": identity})
        except Exception:
            pass
        raise
    # Once commit is sent, completion may be unknown. Never cancel/reinject on
    # transport failure: constructors might already be running in the target.
    try:
        result = control(call, {"op": "commit", "id": identity, "allow_execute": True})
        deadline = time.monotonic() + float(args.get("wait_seconds", 5))
        while result.get("pending") and time.monotonic() < deadline:
            time.sleep(min(0.2, max(0, deadline-time.monotonic())))
            result = control(call, {"op": "status", "id": identity})
    except Exception as exc:
        result = {"id": identity, "state": "completion_unknown", "pending": True,
                  "error": str(exc), "next_action": "Poll native_library_status with this id. Do not reinject."}
    result = dict(result)
    result["sha256"] = digest
    result["warning"] = "Custom SO code runs in the target process; it is not crash-isolated or safely unloadable."
    return result
