"""Host-side ARM64 ELF analysis helpers.

The module deliberately uses only the Python standard library.  Capstone-backed
display text is requested from the injected runtime through callbacks, while
whole-module indexing is performed locally from the ELF image so repeated MCP
queries do not rescan target memory.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import shlex
import struct
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable


class StaticAnalysisError(RuntimeError):
    pass


def _parse_int(value: Any, name: str = "value") -> int:
    if isinstance(value, bool):
        raise StaticAnalysisError(f"{name} must be an integer or hexadecimal string")
    try:
        result = value if isinstance(value, int) else int(str(value), 0)
    except (TypeError, ValueError):
        raise StaticAnalysisError(f"{name} must be an integer or hexadecimal string") from None
    if not 0 <= result < (1 << 64):
        raise StaticAnalysisError(f"{name} is outside the 64-bit address range")
    return result


def _bounded_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise StaticAnalysisError(f"{name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise StaticAnalysisError(f"{name} must be an integer") from None
    if not minimum <= parsed <= maximum:
        raise StaticAnalysisError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _signed(value: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    return (value & (sign - 1)) - (value & sign)


def _hex(value: int) -> str:
    return f"0x{value:x}"


def _page(items: list[dict[str, Any]], offset: int, limit: int) -> dict[str, Any]:
    total = len(items)
    page = items[offset:offset + limit]
    return {
        "total": total,
        "offset": offset,
        "returned": len(page),
        "has_more": offset + len(page) < total,
        "results": page,
    }


@dataclass(frozen=True)
class ElfSection:
    index: int
    name: str
    type: int
    flags: int
    address: int
    offset: int
    size: int
    link: int
    info: int
    alignment: int
    entry_size: int

    @property
    def allocated(self) -> bool:
        return bool(self.flags & 0x2)

    @property
    def executable(self) -> bool:
        return bool(self.flags & 0x4)

    @property
    def writable(self) -> bool:
        return bool(self.flags & 0x1)


@dataclass(frozen=True)
class ElfSegment:
    index: int
    type: int
    flags: int
    offset: int
    virtual_address: int
    file_size: int
    memory_size: int
    alignment: int


@dataclass(frozen=True)
class ElfSymbol:
    name: str
    value: int
    size: int
    binding: int
    type: int
    visibility: int
    section_index: int
    table: str


class ElfImage:
    """Small, bounds-checked ELF64 little-endian parser for Android ARM64 SOs."""

    PT_LOAD = 1
    SHT_SYMTAB = 2
    SHT_DYNSYM = 11
    SHN_UNDEF = 0
    STT_FUNC = 2

    def __init__(self, data: bytes):
        self.data = data
        if len(data) < 64 or data[:4] != b"\x7fELF":
            raise StaticAnalysisError("module file is not an ELF image")
        if data[4] != 2:
            raise StaticAnalysisError("static analysis currently requires ELF64")
        if data[5] != 1:
            raise StaticAnalysisError("static analysis currently requires little-endian ELF")
        header = self._unpack("<16sHHIQQQIHHHHHH", 0)
        (
            _, self.file_type, self.machine, _, self.entry, self.program_offset,
            self.section_offset, _, self.header_size, self.program_entry_size,
            self.program_count, self.section_entry_size, self.section_count,
            self.section_name_index,
        ) = header
        if self.machine != 183:
            raise StaticAnalysisError(f"analysis requires AArch64 ELF (machine={self.machine})")
        self.segments = self._parse_segments()
        self.sections = self._parse_sections()
        self.symbols = self._parse_symbols()

    def _unpack(self, fmt: str, offset: int) -> tuple[Any, ...]:
        size = struct.calcsize(fmt)
        if offset < 0 or offset + size > len(self.data):
            raise StaticAnalysisError("ELF table points outside the module file")
        return struct.unpack_from(fmt, self.data, offset)

    def _slice(self, offset: int, size: int) -> bytes:
        if offset < 0 or size < 0 or offset + size > len(self.data):
            raise StaticAnalysisError("ELF range points outside the module file")
        return self.data[offset:offset + size]

    def _parse_segments(self) -> list[ElfSegment]:
        if self.program_count > 8192 or self.program_entry_size < 56:
            raise StaticAnalysisError("invalid ELF program-header table")
        result: list[ElfSegment] = []
        for index in range(self.program_count):
            offset = self.program_offset + index * self.program_entry_size
            p_type, flags, file_offset, vaddr, _, file_size, memory_size, alignment = self._unpack(
                "<IIQQQQQQ", offset
            )
            if file_size and file_offset + file_size > len(self.data):
                raise StaticAnalysisError("ELF segment exceeds the module file")
            result.append(ElfSegment(
                index, p_type, flags, file_offset, vaddr, file_size, memory_size, alignment
            ))
        return result

    def _parse_sections(self) -> list[ElfSection]:
        if not self.section_count:
            return []
        if self.section_count > 65535 or self.section_entry_size < 64:
            raise StaticAnalysisError("invalid ELF section-header table")
        raw: list[tuple[Any, ...]] = []
        for index in range(self.section_count):
            raw.append(self._unpack(
                "<IIQQQQIIQQ", self.section_offset + index * self.section_entry_size
            ))
        if self.section_name_index >= len(raw):
            raise StaticAnalysisError("invalid ELF section-name table")
        name_header = raw[self.section_name_index]
        names = self._slice(name_header[4], name_header[5])

        def section_name(name_offset: int) -> str:
            if name_offset >= len(names):
                return ""
            end = names.find(b"\0", name_offset)
            if end < 0:
                end = len(names)
            return names[name_offset:end].decode("utf-8", errors="replace")

        result: list[ElfSection] = []
        for index, item in enumerate(raw):
            name, section_type, flags, address, offset, size, link, info, alignment, entry_size = item
            if section_type != 8 and size and offset + size > len(self.data):
                raise StaticAnalysisError(f"ELF section {index} exceeds the module file")
            result.append(ElfSection(
                index, section_name(name), section_type, flags, address, offset, size,
                link, info, alignment, entry_size,
            ))
        return result

    def _parse_symbols(self) -> list[ElfSymbol]:
        result: list[ElfSymbol] = []
        for section in self.sections:
            if section.type not in {self.SHT_SYMTAB, self.SHT_DYNSYM}:
                continue
            if section.link >= len(self.sections):
                continue
            strings_section = self.sections[section.link]
            strings = self._slice(strings_section.offset, strings_section.size)
            entry_size = section.entry_size or 24
            if entry_size < 24:
                continue
            count = min(section.size // entry_size, 2_000_000)
            table = "dynsym" if section.type == self.SHT_DYNSYM else "symtab"
            for index in range(count):
                offset = section.offset + index * entry_size
                name_offset, info, other, shndx, value, size = self._unpack("<IBBHQQ", offset)
                if name_offset >= len(strings):
                    name = ""
                else:
                    end = strings.find(b"\0", name_offset)
                    if end < 0:
                        end = len(strings)
                    name = strings[name_offset:end].decode("utf-8", errors="replace")
                if name or value or shndx:
                    result.append(ElfSymbol(
                        name, value, size, info >> 4, info & 0xF, other & 0x3, shndx, table
                    ))
        return result

    def executable_sections(self) -> list[ElfSection]:
        sections = [s for s in self.sections if s.allocated and s.executable and s.size]
        if sections:
            return sections
        # A sectionless ELF can still be analyzed from executable PT_LOAD ranges.
        return [
            ElfSection(-(p.index + 1), f"PT_LOAD[{p.index}]", 1, 0x6,
                       p.virtual_address, p.offset, p.file_size, 0, 0, p.alignment, 0)
            for p in self.segments if p.type == self.PT_LOAD and (p.flags & 1) and p.file_size
        ]

    def section_bytes(self, section: ElfSection) -> bytes:
        return self._slice(section.offset, section.size)

    def vaddr_to_file(self, vaddr: int) -> int | None:
        for segment in self.segments:
            if segment.type == self.PT_LOAD and segment.virtual_address <= vaddr < segment.virtual_address + segment.file_size:
                return segment.offset + vaddr - segment.virtual_address
        return None

    def file_to_vaddr(self, file_offset: int) -> int | None:
        for segment in self.segments:
            if segment.type == self.PT_LOAD and segment.offset <= file_offset < segment.offset + segment.file_size:
                return segment.virtual_address + file_offset - segment.offset
        return None

    def section_for_vaddr(self, vaddr: int) -> ElfSection | None:
        for section in self.sections:
            if section.address <= vaddr < section.address + section.size:
                return section
        return None


def decode_arm64(word: int, address: int) -> dict[str, Any]:
    """Decode the ARM64 control/data forms required by indexing and CFG analysis."""
    item: dict[str, Any] = {
        "address": _hex(address),
        "size": 4,
        "bytes_hex": struct.pack("<I", word).hex(),
        "word": _hex(word),
        "mnemonic": ".word",
        "operands": _hex(word),
        "reads": [],
        "writes": [],
        "immediates": [],
        "groups": [],
    }

    def reg(index: int, width64: bool = True, sp: bool = False) -> str:
        if index == 31:
            return "sp" if sp else ("xzr" if width64 else "wzr")
        return ("x" if width64 else "w") + str(index)

    top = word & 0xFC000000
    if top in {0x14000000, 0x94000000}:
        target = address + _signed((word & 0x03FFFFFF) << 2, 28)
        call = top == 0x94000000
        item.update(mnemonic="bl" if call else "b", operands=_hex(target), target=_hex(target))
        item["immediates"] = [_hex(target)]
        item["groups"] = ["call" if call else "jump", "branch"]
        if call:
            item["writes"] = ["x30"]
        return item

    if (word & 0xFF000010) == 0x54000000:
        conditions = ["eq", "ne", "cs", "cc", "mi", "pl", "vs", "vc",
                      "hi", "ls", "ge", "lt", "gt", "le", "al", "nv"]
        target = address + _signed(((word >> 5) & 0x7FFFF) << 2, 21)
        condition = conditions[word & 0xF]
        item.update(mnemonic=f"b.{condition}", operands=_hex(target), target=_hex(target), conditional=True)
        item["immediates"] = [_hex(target)]
        item["groups"] = ["jump", "branch", "conditional"]
        item["reads"] = ["nzcv"]
        return item

    if (word & 0x7E000000) == 0x34000000:
        width64 = bool(word & (1 << 31))
        nonzero = bool(word & (1 << 24))
        rt = reg(word & 0x1F, width64)
        target = address + _signed(((word >> 5) & 0x7FFFF) << 2, 21)
        item.update(mnemonic="cbnz" if nonzero else "cbz", operands=f"{rt}, {_hex(target)}",
                    target=_hex(target), conditional=True)
        item["reads"] = [rt]
        item["immediates"] = [_hex(target)]
        item["groups"] = ["jump", "branch", "conditional"]
        return item

    if (word & 0x7E000000) == 0x36000000:
        nonzero = bool(word & (1 << 24))
        bit = ((word >> 19) & 0x1F) | ((word >> 26) & 0x20)
        rt = reg(word & 0x1F, bool(word & (1 << 31)))
        target = address + _signed(((word >> 5) & 0x3FFF) << 2, 16)
        item.update(mnemonic="tbnz" if nonzero else "tbz", operands=f"{rt}, #{bit}, {_hex(target)}",
                    target=_hex(target), conditional=True)
        item["reads"] = [rt]
        item["immediates"] = [bit, _hex(target)]
        item["groups"] = ["jump", "branch", "conditional"]
        return item

    branch_register = word & 0xFFFFFC1F
    if branch_register in {0xD61F0000, 0xD63F0000, 0xD65F0000}:
        rn = reg((word >> 5) & 0x1F)
        mnemonic = {0xD61F0000: "br", 0xD63F0000: "blr", 0xD65F0000: "ret"}[branch_register]
        operands = "" if mnemonic == "ret" and rn == "x30" else rn
        item.update(mnemonic=mnemonic, operands=operands)
        item["reads"] = [rn]
        item["groups"] = ["return" if mnemonic == "ret" else "branch",
                          "call" if mnemonic == "blr" else "indirect"]
        if mnemonic == "blr":
            item["writes"] = ["x30"]
        return item

    if (word & 0x9F000000) in {0x10000000, 0x90000000}:
        page = (word & 0x9F000000) == 0x90000000
        immediate = _signed((((word >> 5) & 0x7FFFF) << 2) | ((word >> 29) & 0x3), 21)
        target = ((address & ~0xFFF) + (immediate << 12)) if page else address + immediate
        rd = reg(word & 0x1F)
        item.update(mnemonic="adrp" if page else "adr", operands=f"{rd}, {_hex(target)}", target=_hex(target))
        item["writes"] = [rd]
        item["immediates"] = [_hex(target)]
        item["groups"] = ["address"]
        return item

    if (word & 0x3B000000) == 0x18000000:
        rt = reg(word & 0x1F, bool(word & (1 << 30)))
        target = address + _signed(((word >> 5) & 0x7FFFF) << 2, 21)
        item.update(mnemonic="ldr", operands=f"{rt}, {_hex(target)}", target=_hex(target))
        item["writes"] = [rt]
        item["immediates"] = [_hex(target)]
        item["groups"] = ["load", "literal"]
        item["memory"] = {"access": "read", "address": _hex(target)}
        return item

    if (word & 0x1F000000) == 0x11000000:
        width64 = bool(word & (1 << 31))
        subtract = bool(word & (1 << 30))
        set_flags = bool(word & (1 << 29))
        shift = 12 if word & (1 << 22) else 0
        immediate = ((word >> 10) & 0xFFF) << shift
        rn = reg((word >> 5) & 0x1F, width64, True)
        rd = reg(word & 0x1F, width64, True)
        mnemonic = ("sub" if subtract else "add") + ("s" if set_flags else "")
        item.update(mnemonic=mnemonic, operands=f"{rd}, {rn}, #{_hex(immediate)}")
        item["reads"] = [rn]
        item["writes"] = [rd] + (["nzcv"] if set_flags else [])
        item["immediates"] = [immediate]
        item["groups"] = ["arithmetic"]
        return item

    if (word & 0x3B000000) == 0x39000000 and not (word & (1 << 26)):
        size_bits = (word >> 30) & 0x3
        scale = 1 << size_bits
        load = bool(word & (1 << 22))
        immediate = ((word >> 10) & 0xFFF) * scale
        rn = reg((word >> 5) & 0x1F, True, True)
        rt = reg(word & 0x1F, size_bits == 3)
        mnemonic = "ldr" if load else "str"
        item.update(mnemonic=mnemonic, operands=f"{rt}, [{rn}, #{_hex(immediate)}]")
        item["reads"] = [rn] + ([] if load else [rt])
        item["writes"] = [rt] if load else []
        item["immediates"] = [immediate]
        item["groups"] = ["load" if load else "store"]
        item["memory"] = {"access": "read" if load else "write", "base": rn, "offset": immediate}
        return item

    if (word & 0x3A000000) == 0x28000000:
        width64 = bool(word & (1 << 31))
        load = bool(word & (1 << 22))
        scale = 8 if width64 else 4
        immediate = _signed((word >> 15) & 0x7F, 7) * scale
        rt = reg(word & 0x1F, width64)
        rt2 = reg((word >> 10) & 0x1F, width64)
        rn = reg((word >> 5) & 0x1F, True, True)
        item.update(mnemonic="ldp" if load else "stp", operands=f"{rt}, {rt2}, [{rn}, #{immediate}]")
        item["reads"] = [rn] + ([] if load else [rt, rt2])
        item["writes"] = [rt, rt2] if load else []
        item["immediates"] = [immediate]
        item["groups"] = ["load" if load else "store"]
        return item

    wide = word & 0x7F800000
    if wide in {0x12800000, 0x52800000, 0x72800000}:
        width64 = bool(word & (1 << 31))
        mnemonic = {0x12800000: "movn", 0x52800000: "movz", 0x72800000: "movk"}[wide]
        immediate = (word >> 5) & 0xFFFF
        shift = ((word >> 21) & 0x3) * 16
        rd = reg(word & 0x1F, width64)
        item.update(mnemonic=mnemonic, operands=f"{rd}, #{_hex(immediate)}, lsl #{shift}")
        item["writes"] = [rd]
        item["immediates"] = [immediate, shift]
        item["groups"] = ["move"]
        return item

    if word == 0xD503201F:
        item.update(mnemonic="nop", operands="")
        return item
    if word in {0xD503233F, 0xD50323BF}:
        item.update(mnemonic="paciasp" if word == 0xD503233F else "autiasp", operands="")
        item["reads"] = ["sp", "x30"]
        item["writes"] = ["x30"]
        return item
    return item


@dataclass
class AnalysisContext:
    key: str
    module: dict[str, Any]
    data: bytes
    elf: ElfImage
    load_bias: int
    local_path: Path
    strings: list[dict[str, Any]] | None = None
    functions: list[dict[str, Any]] | None = None
    xrefs: list[dict[str, Any]] | None = None
    decompiled: dict[tuple[int, int], str] = field(default_factory=dict)

    def runtime(self, vaddr: int) -> int:
        return self.load_bias + vaddr

    def vaddr(self, runtime: int) -> int:
        return runtime - self.load_bias


class StaticAnalysisManager:
    def __init__(
        self,
        config: Any,
        module_lookup: Callable[[dict[str, Any]], dict[str, Any]],
        disassemble: Callable[[dict[str, Any]], dict[str, Any]],
    ):
        self.config = config
        self._module_lookup = module_lookup
        self._disassemble = disassemble
        self._lock = threading.RLock()
        self._contexts: dict[str, AnalysisContext] = {}
        cache_root = getattr(config, "analysis_cache_dir", None)
        self.cache_root = Path(cache_root) if cache_root else Path(tempfile.gettempdir()) / "zygisk_il2cpp_mcp_analysis"

    @staticmethod
    def _module_args(args: dict[str, Any]) -> tuple[str, int]:
        module = args.get("module") or args.get("module_name")
        if not isinstance(module, str) or not module.strip():
            raise StaticAnalysisError("module is required")
        occurrence = _bounded_int(args.get("occurrence", 1), "occurrence", 1, 4096)
        return module.strip(), occurrence

    def _read_module_file(self, remote_path: str) -> bytes:
        local = Path(remote_path)
        if local.is_file():
            data = local.read_bytes()
        else:
            adb = str(getattr(self.config, "adb_path", "adb"))
            command = [adb]
            serial = getattr(self.config, "adb_serial", None)
            if serial:
                command.extend(["-s", serial])
            command.extend(["exec-out", "su", "-c", f"cat {shlex.quote(remote_path)}"])
            try:
                completed = subprocess.run(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=max(float(getattr(self.config, "timeout", 5.0)), 60.0), check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise StaticAnalysisError(f"cannot read module file through ADB: {exc}") from exc
            if completed.returncode != 0:
                detail = completed.stderr.decode("utf-8", errors="replace").strip()
                raise StaticAnalysisError(f"ADB module read failed: {detail or completed.returncode}")
            data = completed.stdout
        maximum = int(getattr(self.config, "analysis_max_module_bytes", 512 * 1024 * 1024))
        if not data or len(data) > maximum:
            raise StaticAnalysisError(f"module file size must be between 1 and {maximum} bytes")
        return data

    def context(self, args: dict[str, Any], refresh: bool = False) -> AnalysisContext:
        module_name, occurrence = self._module_args(args)
        module = self._module_lookup({"module_name": module_name, "occurrence": occurrence})
        path = module.get("path")
        load_bias_text = module.get("load_bias")
        if not isinstance(path, str) or not path or not isinstance(load_bias_text, str):
            raise StaticAnalysisError("module lookup did not return path and load_bias")
        load_bias = _parse_int(load_bias_text, "load_bias")
        key = f"{path}|{load_bias:x}"
        with self._lock:
            if not refresh and key in self._contexts:
                return self._contexts[key]
        data = self._read_module_file(path)
        digest = hashlib.sha256(data).hexdigest()[:20]
        self.cache_root.mkdir(parents=True, exist_ok=True)
        safe_suffix = Path(path).suffix if Path(path).suffix else ".elf"
        local_path = self.cache_root / f"{digest}{safe_suffix}"
        if not local_path.is_file() or local_path.stat().st_size != len(data):
            temporary = local_path.with_suffix(local_path.suffix + ".tmp")
            temporary.write_bytes(data)
            os.replace(temporary, local_path)
        context = AnalysisContext(key, module, data, ElfImage(data), load_bias, local_path)
        with self._lock:
            self._contexts[key] = context
        return context

    @staticmethod
    def _iter_words(context: AnalysisContext) -> Iterable[tuple[ElfSection, int, int]]:
        for section in context.elf.executable_sections():
            data = context.elf.section_bytes(section)
            for offset in range(0, len(data) - 3, 4):
                word = struct.unpack_from("<I", data, offset)[0]
                yield section, context.runtime(section.address + offset), word

    def list_sections(self, args: dict[str, Any]) -> dict[str, Any]:
        context = self.context(args)
        offset = _bounded_int(args.get("offset", 0), "offset", 0, 1_000_000)
        limit = _bounded_int(args.get("limit", 200), "limit", 1, 4096)
        results = []
        for section in context.elf.sections:
            results.append({
                "index": section.index,
                "name": section.name,
                "type": section.type,
                "flags": _hex(section.flags),
                "runtime_start": _hex(context.runtime(section.address)) if section.address else None,
                "runtime_end": _hex(context.runtime(section.address + section.size)) if section.address else None,
                "virtual_address": _hex(section.address),
                "file_offset": _hex(section.offset),
                "size": section.size,
                "allocated": section.allocated,
                "executable": section.executable,
                "writable": section.writable,
            })
        return {"module": context.module, **_page(results, offset, limit)}

    def list_symbols(self, args: dict[str, Any]) -> dict[str, Any]:
        context = self.context(args)
        query = str(args.get("query", ""))
        offset = _bounded_int(args.get("offset", 0), "offset", 0, 10_000_000)
        limit = _bounded_int(args.get("limit", 200), "limit", 1, 5000)
        results = []
        seen: set[tuple[str, int, int]] = set()
        for symbol in context.elf.symbols:
            if query and query.casefold() not in symbol.name.casefold():
                continue
            identity = (symbol.name, symbol.value, symbol.section_index)
            if identity in seen:
                continue
            seen.add(identity)
            results.append(self._symbol_json(context, symbol))
        results.sort(key=lambda item: (int(item["value"], 16), item["name"]))
        return {"module": context.module, **_page(results, offset, limit)}

    @staticmethod
    def _symbol_json(context: AnalysisContext, symbol: ElfSymbol) -> dict[str, Any]:
        defined = symbol.section_index != ElfImage.SHN_UNDEF
        return {
            "name": symbol.name,
            "table": symbol.table,
            "binding": {0: "local", 1: "global", 2: "weak"}.get(symbol.binding, str(symbol.binding)),
            "type": {0: "notype", 1: "object", 2: "function", 3: "section", 4: "file", 6: "tls"}.get(symbol.type, str(symbol.type)),
            "visibility": symbol.visibility,
            "defined": defined,
            "section_index": symbol.section_index,
            "value": _hex(symbol.value),
            "runtime_address": _hex(context.runtime(symbol.value)) if defined and symbol.value else None,
            "size": symbol.size,
        }

    def list_imports(self, args: dict[str, Any]) -> dict[str, Any]:
        context = self.context(args)
        query = str(args.get("query", ""))
        offset = _bounded_int(args.get("offset", 0), "offset", 0, 10_000_000)
        limit = _bounded_int(args.get("limit", 200), "limit", 1, 5000)
        results = []
        seen: set[str] = set()
        for symbol in context.elf.symbols:
            if symbol.table != "dynsym" or symbol.section_index != 0 or not symbol.name:
                continue
            if query and query.casefold() not in symbol.name.casefold() or symbol.name in seen:
                continue
            seen.add(symbol.name)
            results.append(self._symbol_json(context, symbol))
        results.sort(key=lambda item: item["name"])
        return {"module": context.module, **_page(results, offset, limit)}

    def list_exports(self, args: dict[str, Any]) -> dict[str, Any]:
        context = self.context(args)
        query = str(args.get("query", ""))
        offset = _bounded_int(args.get("offset", 0), "offset", 0, 10_000_000)
        limit = _bounded_int(args.get("limit", 200), "limit", 1, 5000)
        results = []
        seen: set[tuple[str, int]] = set()
        for symbol in context.elf.symbols:
            if symbol.table != "dynsym" or symbol.section_index == 0 or symbol.binding not in {1, 2} or not symbol.name:
                continue
            if query and query.casefold() not in symbol.name.casefold():
                continue
            identity = (symbol.name, symbol.value)
            if identity in seen:
                continue
            seen.add(identity)
            results.append(self._symbol_json(context, symbol))
        results.sort(key=lambda item: (int(item["value"], 16), item["name"]))
        return {"module": context.module, **_page(results, offset, limit)}

    def _build_strings(self, context: AnalysisContext) -> list[dict[str, Any]]:
        with self._lock:
            if context.strings is not None:
                return context.strings
        results: list[dict[str, Any]] = []
        candidates = [s for s in context.elf.sections if s.allocated and not s.executable and s.type != 8 and s.size]
        for section in candidates:
            data = context.elf.section_bytes(section)
            start = 0
            while start < len(data):
                while start < len(data) and (data[start] == 0 or data[start] < 0x20 or data[start] == 0x7F):
                    start += 1
                end = start
                while end < len(data) and data[end] != 0 and (data[end] >= 0x20 and data[end] != 0x7F):
                    end += 1
                if end - start >= 4:
                    raw = data[start:end]
                    try:
                        value = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        value = ""
                    if value and all(character.isprintable() or character in "\t\r\n" for character in value):
                        vaddr = section.address + start
                        results.append({
                            "address": _hex(context.runtime(vaddr)),
                            "virtual_address": _hex(vaddr),
                            "file_offset": _hex(section.offset + start),
                            "section": section.name,
                            "byte_length": len(raw),
                            "text": value,
                        })
                start = end + 1
        results.sort(key=lambda item: int(item["address"], 16))
        with self._lock:
            context.strings = results
        return results

    def list_strings(self, args: dict[str, Any]) -> dict[str, Any]:
        context = self.context(args)
        minimum = _bounded_int(args.get("min_length", 4), "min_length", 1, 4096)
        offset = _bounded_int(args.get("offset", 0), "offset", 0, 10_000_000)
        limit = _bounded_int(args.get("limit", 200), "limit", 1, 5000)
        values = [item for item in self._build_strings(context) if item["byte_length"] >= minimum]
        return {"module": context.module, "cached": True, **_page(values, offset, limit)}

    def search_strings(self, args: dict[str, Any]) -> dict[str, Any]:
        context = self.context(args)
        query = str(args.get("query", ""))
        if not query:
            raise StaticAnalysisError("query is required")
        case_sensitive = bool(args.get("case_sensitive", False))
        offset = _bounded_int(args.get("offset", 0), "offset", 0, 10_000_000)
        limit = _bounded_int(args.get("limit", 200), "limit", 1, 5000)
        needle = query if case_sensitive else query.casefold()
        results = []
        for item in self._build_strings(context):
            text = item["text"] if case_sensitive else item["text"].casefold()
            if needle in text:
                results.append(item)
        return {"module": context.module, "query": query, "cached": True, **_page(results, offset, limit)}
