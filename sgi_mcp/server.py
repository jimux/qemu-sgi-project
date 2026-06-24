#!/usr/bin/env python3
# SGI PROM Comparative Analysis MCP Server
"""
MCP server providing tools for SGI PROM binary analysis.
"""

import sys
import asyncio
import platform
from typing import Any, Optional
import json

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Local imports
from .config import PROM_BASE, PLATFORMS, detect_platform, prom_offset_to_addr
from .prom_loader import (
    list_prom_files,
    load_prom,
    get_prom_metadata,
    get_prom_summary,
    extract_strings,
    normalize_data,
)
from .hardware_defs import (
    annotate_address,
    format_annotation,
    list_devices,
    get_device_info,
    DEVICES,
)
from .disassembler import (
    disassemble_prom,
    format_disassembly,
    find_function_prologues,
    CAPSTONE_AVAILABLE,
)
from .xxd import xxd_prom, xxd, reverse_xxd
from .pattern_detector import (
    find_hardware_probes,
    find_exception_vectors,
    find_graphics_init,
    find_memory_detection,
    find_device_detection,
    find_jump_tables,
    find_all_patterns,
    format_pattern_matches,
    find_instructions,
    # New analysis functions
    build_call_graph,
    find_function_boundaries,
    track_hardware_accesses,
    trace_boot_sequence,
    find_string_references,
    identify_arcs_callbacks,
    analyze_function,
    build_function_database,
    # Enhanced function detection (supports MIPS64)
    find_function_prologues_enhanced,
    # QEMU debugging tools
    parse_qemu_log,
    generate_expected_sequence,
    analyze_register_values,
    compare_execution,
)
from .comparator import (
    diff_binary,
    format_diff,
    find_common_code,
    signature_search,
    version_compare,
    find_unique_code,
)
from .formatters import (
    format_json,
    format_markdown_table,
    format_prom_info,
    format_disassembly_markdown,
    format_diff_markdown,
    format_pattern_matches_markdown,
    format_string_list,
    format_common_code_summary,
    to_dict,
    # QEMU debugging formatters
    format_qemu_log_summary,
    format_expected_sequence,
    format_register_value_analysis,
    format_execution_comparison,
)
from .export import (
    export_ghidra_symbols,
    export_ida_idc,
    export_function_json,
    export_hardware_sequence_json,
    export_hardware_sequence_markdown,
    export_arcs_callbacks_json,
    export_call_graph_dot,
)
from . import ghidra_bridge
from . import vm_instances
from . import sgi_fs
from . import disk_safety
from . import golden_catalog

# --- Persistent QEMU session support ---
import bisect
import os
import re
import glob as glob_module
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path


class QemuSession:
    """A persistent QEMU instance with serial console access."""

    def __init__(
        self,
        session_id: str,
        proc: subprocess.Popen,
        serial_sock: socket.socket,
        monitor_sock_path: str,
        tmpdir: str,
        machine: str,
        prom_name: str,
        qemu_binary: str = "",
        extra_args: str = "",
        disks: list = None,
    ):
        self.session_id = session_id
        self.proc = proc
        self.disks = disks or []  # writable disk images (for dirty-marking on kill)
        self.serial_sock = serial_sock
        self.monitor_sock_path = monitor_sock_path
        self.tmpdir = tmpdir
        self.machine = machine
        self.prom_name = prom_name
        self.qemu_binary = qemu_binary
        self.extra_args = extra_args
        self.output_buffer = bytearray()
        self.buffer_lock = threading.Lock()
        self.alive = True
        self.created_at = time.time()
        self.reader_thread = threading.Thread(
            target=_session_reader, args=(self,), daemon=True
        )
        self.reader_thread.start()

    def is_running(self) -> bool:
        """Check if QEMU process is still running."""
        poll_result = self.proc.poll()
        running = self.alive and poll_result is None
        if not running:
            with open("/tmp/mcp_debug.log", "a") as _dbg:
                _dbg.write(
                    f"[is_running] alive={self.alive} poll={poll_result} pid={self.proc.pid}\n"
                )
        return running

    def drain_buffer(self) -> str:
        """Return all accumulated output and clear the buffer."""
        with self.buffer_lock:
            data = bytes(self.output_buffer)
            self.output_buffer.clear()
        return data.decode("latin-1", errors="replace")

    def send(self, text: str, chunk_size: int = 256, chunk_delay: float = 0.04):
        """Send text to the serial console.

        Sends in small chunks with a brief inter-chunk delay to avoid
        overflowing the IRIX TTY LDTERM ring buffer (~4KB).  At the default
        256-byte / 40ms rate, throughput is ~6 KB/s — fast enough for
        interactive use and slow enough for the guest to drain the buffer
        before the next chunk arrives.
        """
        send_bytes = text.encode("latin-1").decode("unicode_escape").encode("latin-1")
        if len(send_bytes) <= chunk_size:
            # Short strings — no chunking needed
            self.serial_sock.sendall(send_bytes)
            return
        for i in range(0, len(send_bytes), chunk_size):
            chunk = send_bytes[i : i + chunk_size]
            self.serial_sock.sendall(chunk)
            if i + chunk_size < len(send_bytes):
                time.sleep(chunk_delay)

    def graceful_shutdown(self, timeout: int = 90) -> bool:
        """Best-effort clean in-guest shutdown (`sync; sync; init 0`).

        Only attempts it when the serial console looks like a logged-in shell;
        otherwise returns None and the caller falls back to monitor `quit`.
        Returns True if a halt marker was seen, False on timeout, None if no
        shell was detected. A clean halt means no dirty-marking and no journal
        replay on next boot.
        """
        HALT = ("going down", "halted", "okay to power off", "PROM Monitor",
                "System Maintenance", "ok to power", "Press <ENTER>")
        try:
            self.send("\n")
            time.sleep(1.0)
            probe = self.drain_buffer()
            if not re.search(r"[#$]\s*$", probe) and "# " not in probe:
                return None  # not at a shell — let caller use monitor quit
            self.send("sync; sync; init 0\n")
            buf = ""
            deadline = time.time() + timeout
            while time.time() < deadline:
                time.sleep(2.0)
                buf += self.drain_buffer()
                if any(m in buf for m in HALT):
                    return True
            return False
        except Exception:
            return None

    def stop(self, graceful: bool = False):
        """Stop the session and clean up all resources.

        With graceful=True, first attempt an in-guest `init 0` (when a shell is
        reachable) so the filesystem is cleanly unmounted. Always tries monitor
        `quit` (flushes qcow2) before any SIGKILL; a SIGKILL marks disks dirty.
        """
        if graceful:
            try:
                self.graceful_shutdown()
            except Exception:
                pass
        self.alive = False

        # Send quit to monitor
        try:
            mon_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            mon_sock.settimeout(2)
            mon_sock.connect(self.monitor_sock_path)
            mon_sock.sendall(b"quit\n")
            mon_sock.close()
        except Exception:
            pass

        # Wait for QEMU to exit gracefully (flush qcow2 metadata)
        if self.proc:
            try:
                self.proc.wait(timeout=10)
                self.proc = None  # Exited cleanly, skip kill
            except subprocess.TimeoutExpired:
                pass  # Fall through to kill

        # Close serial socket
        try:
            self.serial_sock.close()
        except Exception:
            pass

        # Kill process (if quit didn't work). SIGKILL can corrupt in-flight
        # writes, so mark every writable disk dirty — it must be scanned
        # (xfs_scan) or rolled back to a golden before reuse.
        if self.proc:
            for _d in self.disks:
                disk_safety.mark_dirty(_d, f"SIGKILL of session {self.session_id} "
                                           f"(monitor quit timed out)")
            try:
                self.proc.kill()
                self.proc.wait(timeout=5)
            except Exception:
                pass

        # Wait for reader thread
        if self.reader_thread.is_alive():
            self.reader_thread.join(timeout=2)

        # Clean up temp files
        serial_sock_path = os.path.join(self.tmpdir, "serial.sock")
        for p in [serial_sock_path, self.monitor_sock_path]:
            try:
                os.unlink(p)
            except Exception:
                pass
        try:
            shutil.rmtree(self.tmpdir, ignore_errors=True)
        except Exception:
            pass


def _session_reader(session: QemuSession):
    """Background thread: continuously read serial output into buffer."""
    while session.alive:
        try:
            session.serial_sock.settimeout(0.5)
            data = session.serial_sock.recv(4096)
            if data:
                with session.buffer_lock:
                    session.output_buffer.extend(data)
            else:
                # EOF - QEMU closed the connection
                with open("/tmp/mcp_debug.log", "a") as _dbg:
                    _dbg.write(
                        f"[reader] EOF on serial socket, pid={session.proc.pid}\n"
                    )
                break
        except socket.timeout:
            continue
        except OSError as e:
            with open("/tmp/mcp_debug.log", "a") as _dbg:
                _dbg.write(
                    f"[reader] OSError on serial socket: {e}, pid={session.proc.pid}\n"
                )
            break
    session.alive = False


def _extract_writable_disks(cmd):
    """Return the writable disk image paths from a QEMU launch command line.

    Parses `-drive` specs, skipping CD-ROMs and read-only disks. These are the
    images a force-kill could corrupt, so they're what we mark dirty on SIGKILL.
    """
    disks = []
    for i, a in enumerate(cmd):
        if a == "-drive" and i + 1 < len(cmd):
            spec = cmd[i + 1]
            if "media=cdrom" in spec or "readonly=on" in spec:
                continue
            if spec.startswith("if=scsi") or spec.startswith("if=mtd"):
                m = re.search(r"file=([^,]+)", spec)
                if m:
                    disks.append(m.group(1))
    return disks


def _disks_from_pid(pid):
    """Best-effort: extract writable disk paths from a running QEMU's cmdline."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = f.read().split(b"\x00")
        cmd = [a.decode("utf-8", "replace") for a in argv if a]
        return _extract_writable_disks(cmd)
    except OSError:
        return []


def _instance_disk_in_use(inst_name):
    """Return a running session_id whose writable disks include this instance's
    disk image, else None. Used to refuse delete/reset of a disk under a live VM
    (which would corrupt the qcow2)."""
    try:
        disk = str(vm_instances.get_disk_path(inst_name).resolve())
    except Exception:
        return None
    for sid, s in _qemu_sessions.items():
        try:
            if not s.is_running():
                continue
            for d in getattr(s, "disks", []):
                if str(Path(d).resolve()) == disk:
                    return sid
        except Exception:
            continue
    return None


def _qemu_ppid(pid):
    """Return the parent PID of a process, or None."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("PPid:"):
                    return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


def _is_zombie(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("State:"):
                    return "Z" in line
    except OSError:
        pass
    return False


def _tmpdirs_from_pid(pid):
    """Extract this QEMU's own temp dir(s) from its unix-socket cmdline args."""
    dirs = set()
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = [a.decode("utf-8", "replace") for a in f.read().split(b"\x00") if a]
        for a in argv:
            m = re.search(r"unix:([^,]+\.sock)", a)
            if m:
                dirs.add(os.path.dirname(m.group(1)))
    except OSError:
        pass
    return dirs


def _kill_orphaned_qemu(scope="own"):
    """Kill orphaned qemu-system-mips64 processes and clean up temp dirs.

    Ownership is determined by the OS process tree: every QEMU this MCP server
    launched is our direct child (PPid == our pid). Another Claude session's VMs
    are children of a *different* MCP process, so:

    - scope="own" (default): only kill QEMUs that are our own children (+ already
      tracked sessions). Foreign QEMUs (other sessions) are left untouched, and
      only the temp dirs of QEMUs we actually killed are removed.
    - scope="all": nuclear — kill every qemu-system-mips64 and sweep all qemu_*
      temp dirs. Use ONLY when you know no other session is active.

    Returns (killed, cleaned, skipped_foreign).
    """
    killed = 0
    cleaned = 0
    skipped_foreign = 0
    my_pid = os.getpid()
    killed_tmpdirs = set()
    tracked_pids = {s.proc.pid for s in _qemu_sessions.values() if s.is_running()}
    try:
        result = subprocess.run(
            ["pgrep", "-x", "qemu-system-mip"],  # pgrep -x matches 15-char comm
            capture_output=True, timeout=5, text=True,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                try:
                    pid = int(line.strip())
                    if pid in tracked_pids:
                        continue
                    # Ownership gate: never touch another session's VMs.
                    if scope != "all" and _qemu_ppid(pid) != my_pid:
                        skipped_foreign += 1
                        continue
                    if _is_zombie(pid):
                        continue
                    # SIGKILL corrupts in-flight writes → mark disks dirty first.
                    for _d in _disks_from_pid(pid):
                        disk_safety.mark_dirty(_d, f"SIGKILL of orphan QEMU pid {pid}")
                    killed_tmpdirs |= _tmpdirs_from_pid(pid)
                    os.kill(pid, 9)
                    try:
                        os.waitpid(pid, os.WNOHANG)
                    except ChildProcessError:
                        pass
                    killed += 1
                except (ValueError, ProcessLookupError, FileNotFoundError):
                    pass
    except Exception:
        pass

    # Temp-dir cleanup. In "own" scope only remove the dirs of QEMUs we killed
    # (broad-globbing all /tmp/qemu_* would delete another session's live sockets).
    if scope == "all":
        for pattern in [
            "qemu_serial_*", "qemu_session_*", "qemu_mon_*", "qemu_snap_*",
            "qemu_restore_*", "qemu_symbols_*", "qemu_pcsample_*",
            "qemu_inspect_*", "qemu_qinsp_*", "qemu_harness_*",
        ]:
            for d in glob_module.glob(os.path.join(tempfile.gettempdir(), pattern)):
                try:
                    shutil.rmtree(d, ignore_errors=True)
                    cleaned += 1
                except Exception:
                    pass
    else:
        for d in killed_tmpdirs:
            try:
                if os.path.isdir(d) and "qemu" in os.path.basename(d):
                    shutil.rmtree(d, ignore_errors=True)
                    cleaned += 1
            except Exception:
                pass
    return killed, cleaned, skipped_foreign


# Module-level session registry
_qemu_sessions: dict[str, QemuSession] = {}

# ── VMI (Virtual Machine Introspection) shared helpers ──────────────────


def _vmi_virt_to_phys(vaddr: int) -> int:
    """Convert MIPS kseg0/kseg1 virtual address to physical."""
    if 0x80000000 <= vaddr < 0xA0000000:
        return vaddr - 0x80000000
    elif 0xA0000000 <= vaddr < 0xC0000000:
        return vaddr - 0xA0000000
    return vaddr  # Already physical or unmappable


def _vmi_read_phys(mon_sock, phys_addr: int, num_words: int) -> bytes:
    """Read physical memory via QEMU monitor xp command. Returns big-endian bytes."""
    import struct as _struct

    result = b""
    chunk_size = 128  # words per request
    for offset in range(0, num_words, chunk_size):
        n = min(chunk_size, num_words - offset)
        addr = phys_addr + offset * 4
        mon_sock.sendall(f"xp/{n}wx 0x{addr:x}\n".encode())
        time.sleep(0.15)
        resp = b""
        try:
            while True:
                d = mon_sock.recv(65536)
                if not d:
                    break
                resp += d
        except socket.timeout:
            pass

        for line in resp.decode("utf-8", errors="replace").split("\n"):
            line = line.strip()
            if not line or line.startswith("QEMU") or "(qemu)" in line:
                continue
            parts = line.split(":")
            if len(parts) >= 2:
                for w in parts[1].strip().split():
                    w = w.strip()
                    if w.startswith("0x"):
                        try:
                            val = int(w, 16)
                            result += _struct.pack(">I", val)
                        except ValueError:
                            pass
    return result


def _vmi_read_virt(mon_sock, vaddr: int, num_words: int) -> bytes:
    """Read virtual memory (kseg0/kseg1) via physical address translation."""
    return _vmi_read_phys(mon_sock, _vmi_virt_to_phys(vaddr), num_words)


def _vmi_read_u32(mon_sock, vaddr: int) -> int:
    """Read a single 32-bit big-endian word from virtual address."""
    import struct as _struct

    data = _vmi_read_virt(mon_sock, vaddr, 1)
    if len(data) < 4:
        return 0
    return _struct.unpack(">I", data[:4])[0]


def _vmi_read_string(mon_sock, vaddr: int, max_len: int = 256) -> str:
    """Read a null-terminated C string from virtual address."""
    num_words = (max_len + 3) // 4
    data = _vmi_read_virt(mon_sock, vaddr, num_words)
    # Find null terminator
    null_pos = data.find(b"\x00")
    if null_pos >= 0:
        data = data[:null_pos]
    return data.decode("ascii", errors="replace")


def _vmi_session_monitor(session_id: str) -> socket.socket:
    """Open a monitor socket to a running QEMU session. Caller must close."""
    if session_id not in _qemu_sessions:
        available = ", ".join(_qemu_sessions.keys()) if _qemu_sessions else "none"
        raise ValueError(f"Session '{session_id}' not found. Active: {available}")
    session = _qemu_sessions[session_id]
    if not session.is_running():
        del _qemu_sessions[session_id]
        raise ValueError(f"Session '{session_id}' is no longer running")
    mon_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    mon_sock.settimeout(5)
    mon_sock.connect(session.monitor_sock_path)
    # Drain banner
    try:
        mon_sock.recv(4096)
    except socket.timeout:
        pass
    return mon_sock


def _vmi_load_symbols(symbols_file: str) -> dict:
    """Load symbol table from JSON file. Returns {name: address} dict."""
    from pathlib import Path

    symbols = {}
    if not symbols_file:
        return symbols
    sym_path = Path(symbols_file)
    if not sym_path.is_absolute():
        sym_path = Path(__file__).parent.parent / symbols_file
    if sym_path.exists():
        sym_list = json.loads(sym_path.read_text())
        for s in sym_list:
            symbols[s["name"]] = s["address"]
    return symbols


def _vmi_lookup_func(symbols: dict, addr: int) -> str:
    """Look up function name for an address using bisect on symbol table."""
    import bisect

    if not symbols:
        return ""
    sorted_syms = sorted([(a, n) for n, a in symbols.items()], key=lambda x: x[0])
    addrs = [s[0] for s in sorted_syms]
    idx = bisect.bisect_right(addrs, addr) - 1
    if idx < 0:
        return ""
    base, name = sorted_syms[idx]
    offset = addr - base
    if offset > 0x10000:
        return ""
    return f"{name}+0x{offset:x}" if offset else name


# MIPS Cause.ExcCode -> human name (bits 6:2 of CP0 Cause)
_MIPS_EXCCODE = {
    0: "Int (interrupt)",
    1: "Mod (TLB modification)",
    2: "TLBL (TLB miss on load/ifetch)",
    3: "TLBS (TLB miss on store)",
    4: "AdEL (address error on load/ifetch)",
    5: "AdES (address error on store)",
    6: "IBE (bus error, ifetch)",
    7: "DBE (bus error, data)",
    8: "Sys (syscall)",
    9: "Bp (breakpoint)",
    10: "RI (reserved instruction)",
    11: "CpU (coprocessor unusable)",
    12: "Ov (arithmetic overflow)",
    13: "Tr (trap)",
    14: "VCEI (virtual coherency, ifetch)",
    15: "FPE (floating point)",
    23: "WATCH (watchpoint)",
    31: "VCED (virtual coherency, data)",
}


def _crash_sym(symbols: dict, addr: int) -> str:
    """Symbolize, masking 64-bit sign-extended KSEG0 addrs (0xffffffff88..) to low 32."""
    if addr is None:
        return ""
    return _vmi_lookup_func(symbols, addr & 0xFFFFFFFF)


def _crash_parse_regs(dump: str) -> dict:
    """Pull register values out of a panic / qemu-registers / FWCB dump.

    Handles `name=0x..`, `name: 0x..`, `name 0x..` and qemu's GPR blocks. Keys are
    lower-cased; common aliases are normalized (pc->epc, badva->badvaddr, sr->status).
    """
    regs: dict[str, int] = {}
    alias = {
        "pc": "epc", "badva": "badvaddr", "badvaddr": "badvaddr", "sr": "status",
        "epc": "epc", "errorepc": "errorepc", "ra": "ra", "sp": "sp", "gp": "gp",
        "cause": "cause", "status": "status", "hi": "hi", "lo": "lo",
    }
    pat = re.compile(r"\b([A-Za-z_$][A-Za-z0-9_]{0,9})\s*[=:\s]\s*(0x[0-9a-fA-F]+)")
    for m in pat.finditer(dump):
        key = m.group(1).lstrip("$").lower()
        val = int(m.group(2), 16)
        key = alias.get(key, key)
        # First occurrence wins for named regs; GPRs (r0..r31, a0.. etc) kept as-is.
        if key not in regs:
            regs[key] = val
    return regs


def _crash_disasm_elf(kernel_elf: str, vaddr: int, n: int = 12):
    """Disassemble n instructions around a KSEG0 vaddr using the kernel ELF + capstone.

    Maps vaddr->file offset via program headers (p_vaddr/p_offset). Returns a list of
    (addr, mnemonic, opstr) or None if it can't map / capstone is missing.
    """
    if not CAPSTONE_AVAILABLE:
        return None
    from pathlib import Path
    p = Path(kernel_elf)
    if not p.is_absolute():
        p = Path(__file__).parent.parent / kernel_elf
    if not p.exists():
        return None
    data = p.read_bytes()
    if data[:4] != b"\x7fELF":
        return None
    is64 = data[4] == 2
    be = data[5] == 2
    endc = ">" if be else "<"
    import struct as _st
    if is64:
        e_phoff = _st.unpack_from(endc + "Q", data, 0x20)[0]
        e_phentsize = _st.unpack_from(endc + "H", data, 0x36)[0]
        e_phnum = _st.unpack_from(endc + "H", data, 0x38)[0]
    else:
        e_phoff = _st.unpack_from(endc + "I", data, 0x1C)[0]
        e_phentsize = _st.unpack_from(endc + "H", data, 0x2A)[0]
        e_phnum = _st.unpack_from(endc + "H", data, 0x2C)[0]
    v = vaddr & 0xFFFFFFFF
    foff = None
    seg_vaddr = None
    for i in range(e_phnum):
        ph = e_phoff + i * e_phentsize
        if is64:
            p_offset = _st.unpack_from(endc + "Q", data, ph + 8)[0]
            p_vaddr = _st.unpack_from(endc + "Q", data, ph + 16)[0] & 0xFFFFFFFF
            p_filesz = _st.unpack_from(endc + "Q", data, ph + 32)[0]
        else:
            p_offset = _st.unpack_from(endc + "I", data, ph + 4)[0]
            p_vaddr = _st.unpack_from(endc + "I", data, ph + 8)[0] & 0xFFFFFFFF
            p_filesz = _st.unpack_from(endc + "I", data, ph + 16)[0]
        if p_vaddr <= v < p_vaddr + p_filesz:
            foff = p_offset + (v - p_vaddr)
            seg_vaddr = v
            break
    if foff is None:
        return None
    start = max(0, foff - (n // 2) * 4)
    blob = data[start:start + n * 4]
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_MIPS,
                     capstone.CS_MODE_MIPS64 if is64 else capstone.CS_MODE_MIPS32)
    md.mode |= capstone.CS_MODE_BIG_ENDIAN if be else capstone.CS_MODE_LITTLE_ENDIAN
    base = seg_vaddr - (foff - start)
    out = []
    for ins in md.disasm(blob, base):
        out.append((ins.address, ins.mnemonic, ins.op_str))
    return out


def _irix_crash_analyze(dump: str, symbols: dict, kernel_elf: str = "") -> str:
    """Symbolize a panic/FWCB/register dump: fault regs, Cause decode, eframe backtrace."""
    regs = _crash_parse_regs(dump)
    L = ["# IRIX crash analysis", ""]

    epc = regs.get("epc")
    ra = regs.get("ra")
    badv = regs.get("badvaddr")
    cause = regs.get("cause")
    sp = regs.get("sp")
    status = regs.get("status")

    # --- Fault summary --------------------------------------------------------
    L.append("## Fault")
    if cause is not None:
        exc = (cause >> 2) & 0x1F
        name = _MIPS_EXCCODE.get(exc, f"unknown({exc})")
        bd = " [in branch delay slot]" if (cause & 0x80000000) else ""
        ip = (cause >> 8) & 0xFF
        L.append(f"- Cause   = 0x{cause:08x}  ExcCode={exc} {name}{bd}  IP=0x{ip:02x}")
    if status is not None:
        L.append(f"- Status  = 0x{status:08x}  "
                 f"(KSU={(status >> 3) & 3} EXL={(status >> 1) & 1} ERL={(status >> 2) & 1} IE={status & 1})")
    if epc is not None:
        L.append(f"- EPC     = 0x{epc & 0xFFFFFFFF:08x}  {_crash_sym(symbols, epc) or '<no symbol>'}")
    if ra is not None:
        L.append(f"- RA      = 0x{ra & 0xFFFFFFFF:08x}  {_crash_sym(symbols, ra) or '<no symbol>'}")
    if badv is not None:
        b = badv & 0xFFFFFFFF
        region = ("user/KUSEG" if b < 0x80000000 else
                  "KSEG0 (cached)" if b < 0xA0000000 else
                  "KSEG1 (uncached)" if b < 0xC0000000 else "KSEG2/mapped")
        bsym = _crash_sym(symbols, badv)
        L.append(f"- BadVAddr= 0x{b:08x}  [{region}]" + (f"  {bsym}" if bsym else ""))
        if b == 0 or b < 0x1000:
            L.append("  ^ NULL-ish address — likely a NULL pointer deref")
    if sp is not None:
        L.append(f"- SP      = 0x{sp & 0xFFFFFFFF:08x}")
    L.append("")

    # --- Backtrace (eframe level) --------------------------------------------
    L.append("## Backtrace (eframe)")
    frame = 0
    for label, val in (("EPC", epc), ("RA", ra)):
        if val is not None:
            s = _crash_sym(symbols, val) or "<no symbol>"
            L.append(f"  #{frame}  0x{val & 0xFFFFFFFF:08x}  {s}   ({label})")
            frame += 1
    L.append("  (deeper frames need stack memory — pass a live session to walk SP)")
    L.append("")

    # --- Other GPRs that point into kernel text ------------------------------
    code_ptrs = []
    for k, v in regs.items():
        if k in ("epc", "ra", "badvaddr", "cause", "status", "sp", "errorepc", "hi", "lo"):
            continue
        s = _crash_sym(symbols, v)
        if s:
            code_ptrs.append((k, v & 0xFFFFFFFF, s))
    if code_ptrs:
        L.append("## GPRs pointing into kernel text")
        for k, v, s in code_ptrs:
            L.append(f"- {k:5s} = 0x{v:08x}  {s}")
        L.append("")

    # --- Disasm around EPC ----------------------------------------------------
    if kernel_elf and epc is not None:
        dis = _crash_disasm_elf(kernel_elf, epc, 12)
        if dis:
            L.append(f"## Disassembly around EPC (from {kernel_elf})")
            for addr, mn, op in dis:
                marker = " <== EPC" if (addr & 0xFFFFFFFF) == (epc & 0xFFFFFFFF) else ""
                L.append(f"  0x{addr & 0xFFFFFFFF:08x}:  {mn:8s} {op}{marker}")
            L.append("")
        else:
            L.append(f"(could not disassemble EPC from {kernel_elf} — not mapped / no capstone)")
            L.append("")

    if not regs:
        L.append("_No registers parsed. Expected hex tokens like `epc=0x..`, `ra 0x..`, `BadVAddr: 0x..`._")
    return "\n".join(L)


# Cached calibration data keyed by kernel version string
_vmi_calibration_cache: dict[str, dict] = {}


# Machine type -> PROM subdirectory and default PROM mapping
_MACHINE_PROM_MAP = {
    "indy": ("ip24", "Indy_ip24prom.070-9101-011.bin"),
    # Virtuix (IP55) reuses the Indy IP24 PROM binary for now (it normally boots
    # via -kernel; -bios supplies the PROM for SCSI boot). A dedicated ip55 PROM
    # is future work. Distinct entry so the two never collide in tooling.
    "virtuix": ("ip24", "Indy_ip24prom.070-9101-011.bin"),
    "indigo2": ("ip22", None),
    "indigo2-r10k": ("ip28", None),
    "indigo2-r8k": ("ip26", None),
    "indigo": ("ip20", None),
    "octane": ("ip30", None),
    "sgi-ip54": ("ip54", "ip54.bin"),
}


def _platform_build_subdir() -> str:
    """Return the build subdirectory name for the current platform."""
    return "build-mac" if platform.system() == "Darwin" else "build-linux"


def _is_native_binary(path: Path) -> bool:
    """Return True if the binary is executable on the current OS (ELF on Linux, Mach-O on macOS)."""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
        if platform.system() == "Darwin":
            return magic in (
                b"\xce\xfa\xed\xfe",
                b"\xcf\xfa\xed\xfe",
                b"\xfe\xed\xfa\xce",
                b"\xfe\xed\xfa\xcf",
                b"\xca\xfe\xba\xbe",
            )
        return magic == b"\x7fELF"
    except OSError:
        return False


def _has_qemu_binary(d: Path) -> bool:
    """Check if a directory contains a native QEMU system binary."""
    for name in ("qemu-system-mips64", "qemu-system-mips64-unsigned"):
        p = d / name
        if p.exists() and _is_native_binary(p):
            return True
    return False


def _find_build_dir() -> Path:
    """Return the QEMU build directory for the current platform.

    Always uses the platform-specific directory (build-mac/ on macOS,
    build-linux/ on Linux) to match qemu_configure behavior. No fallback
    to other directories — a missing binary produces a clear error rather
    than silently running from the wrong build tree.
    """
    project_root = Path(__file__).parent.parent
    return project_root / "qemu" / _platform_build_subdir()


def _find_qemu_binary(build_dir: Path) -> Path:
    """Find the QEMU system binary in a build directory.

    On macOS, ninja produces qemu-system-mips64-unsigned (unsigned codesign
    variant for softmmu — no Hypervisor entitlement needed).  Check that name
    first so we always prefer the most-recently-built binary.  On Linux the
    output is simply qemu-system-mips64.
    """
    if platform.system() == "Darwin":
        # macOS: prefer the unsigned build artifact; fall back to signed name
        preference = ("qemu-system-mips64-unsigned", "qemu-system-mips64")
    else:
        preference = ("qemu-system-mips64", "qemu-system-mips64-unsigned")
    for name in preference:
        p = build_dir / name
        if p.exists():
            return p
    return build_dir / "qemu-system-mips64"


def _build_qemu_launch(args: dict) -> tuple[list[str], str, str, str, str, str]:
    """Build QEMU command line from args dict.

    Returns (cmd, serial_sock_path, monitor_sock_path, tmpdir, prom_name, error).
    If error is non-empty, the other values are invalid.
    """
    project_root = Path(__file__).parent.parent
    build_dir = _find_build_dir()
    machine = args.get("machine", "indy")
    prom_subdir, default_prom_name = _MACHINE_PROM_MAP.get(machine, ("ip24", None))
    prom_library = project_root / "PROM_library" / "bins" / "cpu" / prom_subdir

    qemu_bin = _find_qemu_binary(build_dir)
    if not qemu_bin.exists():
        return (
            [],
            "",
            "",
            "",
            "",
            f"Error: QEMU binary not found in {build_dir}. Run qemu_build first.",
        )

    # Find PROM file
    prom_arg = args.get("prom", "")
    prom_path = None
    if prom_arg:
        prom_path = Path(prom_arg)
        if not prom_path.exists():
            prom_path = prom_library / prom_arg
        if not prom_path.exists():
            for f in prom_library.glob(f"*{prom_arg}*"):
                prom_path = f
                break
    else:
        if default_prom_name:
            default_prom = prom_library / default_prom_name
        else:
            default_prom = None
        if default_prom and default_prom.exists():
            prom_path = default_prom
        else:
            proms = list(prom_library.glob("*.bin"))
            if proms:
                prom_path = proms[0]
            else:
                return (
                    [],
                    "",
                    "",
                    "",
                    "",
                    f"Error: No PROM files found in {prom_library}",
                )

    if prom_path is None or not prom_path.exists():
        return [], "", "", "", "", f"Error: PROM file not found: {prom_arg}"

    ram_mb = args.get("ram_mb", 64)
    autoload = args.get("autoload", False)
    debug_flags = args.get("debug_flags", "")

    # Create temp directory for Unix sockets
    tmpdir = tempfile.mkdtemp(prefix="qemu_session_")
    serial_sock_path = os.path.join(tmpdir, "serial.sock")
    monitor_sock_path = os.path.join(tmpdir, "monitor.sock")

    vnc_enabled = args.get("vnc", False)
    vnc_port = args.get("vnc_port", 0)

    cmd = [
        str(qemu_bin),
        "-M",
        machine,
        "-bios",
        str(prom_path),
        "-m",
        f"{ram_mb}M",
        "-L",
        str(build_dir / "pc-bios"),
        "-display",
        f"vnc=0.0.0.0:{vnc_port},to=99,password-secret=vnc-pw"
        if vnc_enabled
        else "none",
        "-serial",
        "none",
        "-monitor",
        f"unix:{monitor_sock_path},server,nowait",
    ]
    # Always create the ser0 socket chardev. For indy/indigo2, -serial chardev:ser0
    # connects it to the Z85C30. For octane, BRIDGE/IOC3 connects to it directly
    # via qemu_chr_find("ser0") in sgi_bridge_realize. For sgi-ip54, serial_hd(0)
    # is connected to sgi-pvuart which reads chardev:ser0 via the -serial flag.
    if machine in ("indy", "indigo2", "indigo2-r10k", "indigo2-r8k", "indigo",
                   "virtuix", "sgi-ip54"):
        cmd[cmd.index("-serial") + 1] = "chardev:ser0"
    # sgi-ip54 PROM runs so fast it completes startup before the socket client
    # connects. Use wait=on so QEMU holds serial output until connected.
    serial_wait = "on" if machine == "sgi-ip54" else "off"
    cmd.extend(
        ["-chardev", f"socket,id=ser0,path={serial_sock_path},server=on,wait={serial_wait}"]
    )
    if vnc_enabled:
        cmd.extend(["-object", "secret,id=vnc-pw,data=sgi"])

    if not autoload:
        if machine not in ("sgi-o2", "sgi-ip54"):
            cmd.extend(["-global", "sgi-hpc3.autoload=false"])

    if debug_flags:
        cmd.extend(["-d", debug_flags])

    # Add SCSI drives
    scsi_drives = args.get("scsi_drives", [])
    next_disk_id = 1
    next_cdrom_id = 4
    for drive_spec in scsi_drives:
        # Parse suffixes: ":cdrom" for CD-ROM media, ":ro" for read-only disk
        is_cdrom = False
        is_readonly = False
        drive_path = drive_spec
        for suffix in (":cdrom", ":ro"):
            if drive_path.endswith(suffix):
                drive_path = drive_path[: -len(suffix)]
                if suffix == ":cdrom":
                    is_cdrom = True
                    is_readonly = True
                elif suffix == ":ro":
                    is_readonly = True

        if is_cdrom:
            scsi_id = next_cdrom_id
            next_cdrom_id += 1
        else:
            scsi_id = next_disk_id
            next_disk_id += 1
        if scsi_id > 7:
            shutil.rmtree(tmpdir, ignore_errors=True)
            return [], "", "", "", "", "Error: too many SCSI drives (max 7 targets)"
        drive_file = Path(drive_path)
        if not drive_file.is_absolute():
            if (build_dir / drive_path).exists():
                drive_file = build_dir / drive_path
            elif (project_root / drive_path).exists():
                drive_file = project_root / drive_path
        if not drive_file.exists():
            shutil.rmtree(tmpdir, ignore_errors=True)
            return (
                [],
                "",
                "",
                "",
                "",
                f"Error: SCSI drive image not found: {drive_path}",
            )
        if machine == "sgi-ip54":
            # IP54 has no SCSI bus; bootdisk uses sgi-bootdisk via IF_MTD
            fmt = "qcow2" if drive_file.suffix == ".qcow2" else "raw"
            ro_opt = ",readonly=on" if is_readonly else ""
            cmd.extend(
                [
                    "-drive",
                    # writeback: pvdisk does 512-byte PIO writes; writethrough
                    # would fsync each one. Crash consistency is covered by the
                    # golden-backup workflow.
                    f"if=mtd,file={drive_file},format={fmt},cache=writeback,file.locking=off{ro_opt}",
                ]
            )
        elif is_cdrom:
            cmd.extend(
                [
                    "-drive",
                    f"if=scsi,bus=0,unit={scsi_id},file={drive_file},media=cdrom,format=raw,cache=writethrough",
                ]
            )
        else:
            fmt = "qcow2" if drive_file.suffix == ".qcow2" else "raw"
            ro_opt = ",readonly=on" if is_readonly else ""
            cmd.extend(
                [
                    "-drive",
                    f"if=scsi,bus=0,unit={scsi_id},file={drive_file},format={fmt},cache=writethrough,file.locking=off{ro_opt}",
                ]
            )

    # Snapshot restore
    snapshot = args.get("snapshot", "")
    if snapshot:
        cmd.extend(["-loadvm", snapshot])

    # Deterministic record/replay + gdb (validated on sgi-ip54; see
    # progress_notes/ip54/replay_debugging.md). Emitted before extra_args so a
    # caller can still override via extra_args if needed.
    icount_shift = str(args.get("icount_shift", "") or "")
    rr_mode = args.get("rr_mode", "off") or "off"
    rrfile = args.get("rrfile", "") or ""
    rrsnapshot = args.get("rrsnapshot", "") or ""
    if rr_mode != "off" and not icount_shift:
        # record/replay is meaningless without icount; default to the IP54 value
        icount_shift = "7"
    if icount_shift:
        icount = f"shift={icount_shift},sleep=off"
        if rr_mode in ("record", "replay"):
            icount += f",rr={rr_mode}"
            if rrfile:
                icount += f",rrfile={rrfile}"
            if rrsnapshot:
                icount += f",rrsnapshot={rrsnapshot}"
        cmd.extend(["-icount", icount])

    gdb_port = args.get("gdb_port")
    if gdb_port:
        cmd.extend(["-gdb", f"tcp::{int(gdb_port)}"])
    if args.get("start_stopped"):
        cmd.append("-S")

    extra_args = args.get("extra_args", "")
    if extra_args and rr_mode in ("record", "replay") and "-nic user" in extra_args \
            and "filter-replay" not in extra_args:
        # For deterministic networking the NIC's netdev must be named and have a
        # filter-replay attached. `-nic user,...` (NOT `-netdev`) is what binds
        # to the sysbus pvnet NIC; id=n0 makes it filter-able. (validated: a bare
        # `-netdev` leaves pvnet with "no peer".) See replay_debugging_ip54.
        import re as _re
        extra_args = _re.sub(r"-nic user,", "-nic user,id=n0,", extra_args, count=1)
        extra_args += " -object filter-replay,id=replay,netdev=n0"

    if extra_args:
        cmd.extend(extra_args.split())

    # Redirect QEMU debug/trace output to a file (-D flag)
    save_log = args.get("save_log", "")
    if save_log:
        cmd.extend(["-D", save_log])

    return cmd, serial_sock_path, monitor_sock_path, tmpdir, prom_path.name, ""


def _resolve_prom(args: dict) -> tuple:
    """Resolve PROM file path from args dict.

    Returns (prom_path, build_dir, project_root, error).
    If error is non-empty, prom_path is None.
    """
    project_root = Path(__file__).parent.parent
    build_dir = _find_build_dir()
    machine = args.get("machine", "indy")
    prom_subdir, default_prom_name = _MACHINE_PROM_MAP.get(machine, ("ip24", None))
    prom_library = project_root / "PROM_library" / "bins" / "cpu" / prom_subdir

    qemu_bin = _find_qemu_binary(build_dir)
    if not qemu_bin.exists():
        return (
            None,
            build_dir,
            project_root,
            f"Error: QEMU binary not found in {build_dir}. Run qemu_build first.",
        )

    prom_arg = args.get("prom", "")
    prom_path = None
    if prom_arg:
        prom_path = Path(prom_arg)
        if not prom_path.exists():
            prom_path = prom_library / prom_arg
        if not prom_path.exists():
            for f in prom_library.glob(f"*{prom_arg}*"):
                prom_path = f
                break
    else:
        if default_prom_name:
            default_prom = prom_library / default_prom_name
        else:
            default_prom = None
        if default_prom and default_prom.exists():
            prom_path = default_prom
        else:
            proms = list(prom_library.glob("*.bin"))
            if proms:
                prom_path = proms[0]
            else:
                return (
                    None,
                    build_dir,
                    project_root,
                    f"Error: No PROM files found in {prom_library}",
                )

    if prom_path is None or not prom_path.exists():
        return None, build_dir, project_root, f"Error: PROM file not found: {prom_arg}"

    return prom_path, build_dir, project_root, ""


def _resolve_scsi_drives(args: dict, build_dir: Path, project_root: Path) -> tuple:
    """Resolve SCSI drive paths and build command-line arguments.

    Returns (drive_cmd_args, has_qcow2, error).
    drive_cmd_args is a list of command-line arguments to append.
    """
    scsi_drives = args.get("scsi_drives", [])
    machine = args.get("machine", "indy")
    drive_cmd_args = []
    has_qcow2 = False
    next_disk_id = 1
    next_cdrom_id = 4
    for drive_spec in scsi_drives:
        # Parse suffixes: ":cdrom" for CD-ROM media, ":ro" for read-only disk
        is_cdrom = False
        is_readonly = False
        drive_path = drive_spec
        for suffix in (":cdrom", ":ro"):
            if drive_path.endswith(suffix):
                drive_path = drive_path[: -len(suffix)]
                if suffix == ":cdrom":
                    is_cdrom = True
                    is_readonly = True
                elif suffix == ":ro":
                    is_readonly = True

        if is_cdrom:
            scsi_id = next_cdrom_id
            next_cdrom_id += 1
        else:
            scsi_id = next_disk_id
            next_disk_id += 1
        if scsi_id > 7:
            return [], False, "Error: too many SCSI drives (max 7 targets)"
        drive_file = Path(drive_path)
        if not drive_file.is_absolute():
            if (build_dir / drive_path).exists():
                drive_file = build_dir / drive_path
            elif (project_root / drive_path).exists():
                drive_file = project_root / drive_path
        if not drive_file.exists():
            return [], False, f"Error: SCSI drive image not found: {drive_path}"
        if machine == "sgi-ip54":
            # IP54 has no SCSI bus; bootdisk uses sgi-bootdisk via IF_MTD
            fmt = "qcow2" if drive_file.suffix == ".qcow2" else "raw"
            if fmt == "qcow2":
                has_qcow2 = True
            ro_opt = ",readonly=on" if is_readonly else ""
            drive_cmd_args.extend(
                [
                    "-drive",
                    # writeback: pvdisk does 512-byte PIO writes; writethrough
                    # would fsync each one. Crash consistency is covered by the
                    # golden-backup workflow.
                    f"if=mtd,file={drive_file},format={fmt},cache=writeback,file.locking=off{ro_opt}",
                ]
            )
        elif is_cdrom:
            drive_cmd_args.extend(
                [
                    "-drive",
                    f"if=scsi,bus=0,unit={scsi_id},file={drive_file},media=cdrom,format=raw,cache=writethrough",
                ]
            )
        else:
            fmt = "qcow2" if drive_file.suffix == ".qcow2" else "raw"
            if fmt == "qcow2":
                has_qcow2 = True
            ro_opt = ",readonly=on" if is_readonly else ""
            drive_cmd_args.extend(
                [
                    "-drive",
                    f"if=scsi,bus=0,unit={scsi_id},file={drive_file},format={fmt},cache=writethrough,file.locking=off{ro_opt}",
                ]
            )
    return drive_cmd_args, has_qcow2, ""


def _apply_instance_defaults(args: dict, manifest: dict) -> dict:
    """Apply default_extra_args, default_snapshot, and hostfwd_port from manifest to args.

    Prepends default_extra_args before any caller-provided extra_args.
    Sets default snapshot only if caller did not specify one.
    Injects hostfwd into the -nic user,... arg in default_extra_args if absent.
    """
    import re

    default_extra = manifest.get("default_extra_args", "")
    default_snap = manifest.get("default_snapshot", "")
    hostfwd_port = manifest.get("hostfwd_port")

    # Inject hostfwd into the -nic user,... token if not already present
    if hostfwd_port and default_extra and "-nic user" in default_extra:
        if "hostfwd" not in default_extra:
            default_extra = re.sub(
                r"(-nic\s+user[^\s]*)",
                lambda m: m.group(1) + f",hostfwd=tcp::{hostfwd_port}-10.0.2.15:23",
                default_extra,
            )

    # Prepend manifest defaults; caller extra_args appended after
    # (NVRAM -global is added by _resolve_instance after this, so it comes last)
    caller_extra = args.get("extra_args", "")
    if default_extra:
        args["extra_args"] = (
            (default_extra + " " + caller_extra).strip()
            if caller_extra
            else default_extra
        )

    # Apply default snapshot only when caller has not specified one
    if default_snap and not args.get("snapshot"):
        args["snapshot"] = default_snap

    return args


def _resolve_instance(args: dict) -> dict:
    """Resolve instance parameter into concrete paths and defaults.

    If args contains 'instance', populates scsi_drives, machine, ram_mb,
    default_extra_args, default_snapshot, and injects NVRAM path into
    extra_args. Returns modified args copy.
    """
    instance_name = args.get("instance")
    if not instance_name:
        return args

    args = dict(args)  # don't mutate original

    manifest = vm_instances.load_manifest(instance_name)
    disk_path = vm_instances.get_disk_path(instance_name)
    nvram_path = vm_instances.get_nvram_path(instance_name)

    # Use instance disk if no explicit scsi_drives
    if not args.get("scsi_drives"):
        if disk_path.exists():
            args["scsi_drives"] = [str(disk_path)]

    # Use instance disk if no explicit disk path (for harness tools)
    default_disk = str(Path(__file__).parent.parent / "irix_disk.qcow2")
    if not args.get("disk") or args.get("disk") == default_disk:
        if disk_path.exists():
            args["disk"] = str(disk_path)

    # Apply manifest defaults (only if not explicitly provided)
    if manifest:
        if "machine" not in args or args.get("machine") == "indy":
            args.setdefault("machine", manifest.get("machine", "indy"))
        if "ram_mb" not in args or args.get("ram_mb") == 64:
            args.setdefault("ram_mb", manifest.get("ram_mb", 64))

    # Apply launch defaults from manifest (default_extra_args, default_snapshot)
    if manifest:
        args = _apply_instance_defaults(args, manifest)

    # Inject NVRAM path via extra_args — always last so it cannot be shadowed
    # IP54 has no HPC3, so skip NVRAM injection for that machine
    machine_for_nvram = args.get("machine", manifest.get("machine", "indy") if manifest else "indy")
    if machine_for_nvram != "sgi-ip54":
        nvram_global = f"-global sgi-hpc3.nvram-file={nvram_path}"
        extra = args.get("extra_args", "")
        if "nvram-file" not in extra:
            args["extra_args"] = f"{extra} {nvram_global}".strip()

    # When using an instance, the NVRAM file is the authority for autoload.
    # The MCP schema defaults autoload=False which would cause _build_qemu_cmd
    # to inject -global sgi-hpc3.autoload=false, overriding the NVRAM file.
    # Read the NVRAM and propagate its autoload setting into args.
    if nvram_path.exists():
        from sgi_mcp.nvram_utils import nvram_read

        nvars = nvram_read(nvram_path)
        if nvars.get("autoload") == "Y":
            args["autoload"] = True

    return args


def _collect_serial_output(sock: socket.socket, duration: float) -> bytes:
    """Non-blocking recv loop collecting output for `duration` seconds."""
    collected = b""
    end_time = time.time() + duration
    while time.time() < end_time:
        remaining = end_time - time.time()
        if remaining <= 0:
            break
        sock.settimeout(min(remaining, 0.5))
        try:
            data = sock.recv(4096)
            if data:
                collected += data
        except socket.timeout:
            continue
        except OSError:
            break
    return collected


def _expect_serial(
    sock: socket.socket, pattern: str, expect_timeout: float, initial_data: bytes = b""
) -> tuple:
    """Accumulate output until pattern matches. Returns (output_bytes, matched).
    initial_data: data already received to check first."""
    accumulated = initial_data
    end_time = time.time() + expect_timeout
    try:
        compiled = re.compile(
            pattern if isinstance(pattern, str) else pattern.decode("latin-1")
        )
    except re.error:
        compiled = None

    # Check initial_data for match
    if accumulated:
        text = accumulated.decode("latin-1")
        if compiled:
            if compiled.search(text):
                return accumulated, True
        else:
            if pattern in text:
                return accumulated, True

    while time.time() < end_time:
        remaining = end_time - time.time()
        if remaining <= 0:
            break
        sock.settimeout(min(remaining, 0.5))
        try:
            data = sock.recv(4096)
            if data:
                accumulated += data
                text = accumulated.decode("latin-1")
                if compiled:
                    if compiled.search(text):
                        return accumulated, True
                else:
                    if pattern in text:
                        return accumulated, True
        except socket.timeout:
            continue
        except OSError:
            break
    return accumulated, False


def _connect_serial_retry(
    serial_sock_path: str,
    proc: subprocess.Popen = None,
    max_retries: int = 20,
    stderr_log_path: str = None,
    cmd: list = None,
) -> tuple:
    """Connect to serial socket with retry.
    Returns (socket, error_string). If error_string is non-empty, socket is None.
    If proc is provided, checks for early QEMU exit.
    stderr_log_path: path to file receiving QEMU stderr (for error diagnostics).
    cmd: the command list to include in error messages."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    for attempt in range(max_retries):
        if proc and proc.poll() is not None:
            stderr_out = ""
            # Prefer reading from log file (works when stderr=file, avoids blocking)
            if stderr_log_path:
                try:
                    with open(stderr_log_path, "r", errors="replace") as _f:
                        stderr_out = _f.read().strip()
                except Exception:
                    pass
            # Fall back to proc.stderr pipe (when stderr=PIPE)
            if not stderr_out:
                try:
                    stderr_out = (
                        proc.stderr.read().decode("utf-8", errors="replace").strip()
                    )
                except Exception:
                    pass
            sock.close()
            err = f"Error: QEMU exited with code {proc.returncode} before serial connected"
            if cmd:
                err += f"\n\n**Command:** `{' '.join(str(x) for x in cmd)}`"
            if stderr_out:
                err += f"\n\n**QEMU stderr:**\n```\n{stderr_out}\n```"
            return None, err
        try:
            sock.connect(serial_sock_path)
            return sock, ""
        except (FileNotFoundError, ConnectionRefusedError):
            time.sleep(0.5)
    sock.close()
    return None, "Error: could not connect to QEMU serial socket"


def _popen_qemu(cmd: list, tmpdir: str) -> tuple:
    """Launch a QEMU process with stderr captured to a temp file.

    Using a file instead of subprocess.PIPE avoids OS pipe-buffer overflow
    for long-running sessions with debug output enabled.

    Returns (proc, stderr_log_path).
    """
    stderr_log = os.path.join(tmpdir, "qemu_stderr.txt")
    stderr_f = open(stderr_log, "w")
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr_f)
    finally:
        stderr_f.close()  # Parent closes its handle; child retains its inherited fd
    return proc, stderr_log


def _cleanup_qemu(proc, serial_sock, monitor_sock_path, tmpdir):
    """Clean up a QEMU session: quit via monitor, close sockets, kill process, remove tmpdir."""
    # Quit via monitor
    try:
        mon_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        mon_sock.settimeout(3)
        mon_sock.connect(monitor_sock_path)
        mon_sock.sendall(b"quit\n")
        mon_sock.close()
    except Exception:
        pass
    # Wait for QEMU to exit gracefully (flush qcow2 metadata)
    if proc:
        try:
            proc.wait(timeout=10)
            proc = None  # Exited cleanly, skip kill
        except subprocess.TimeoutExpired:
            pass  # Fall through to kill
    # Close serial
    if serial_sock:
        try:
            serial_sock.close()
        except Exception:
            pass
    # Kill process (if quit didn't work)
    if proc:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass
    # Clean up tmpdir
    if tmpdir:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _run_serial_interactions(serial_sock, interactions, timeout, boot_output=b""):
    """Execute expect/send interaction loop on serial socket.
    Returns (transcript_parts, pending_data, all_ok)."""
    transcript = []
    pending_data = boot_output or b""
    session_start = time.time()
    all_ok = True

    for interaction in interactions:
        if time.time() - session_start > timeout:
            transcript.append("\n[TIMEOUT: session timeout reached]\n")
            all_ok = False
            break

        expect_pattern = interaction.get("expect", "")
        send_text = interaction.get("send", "")
        expect_timeout = interaction.get("timeout", 10)

        if expect_pattern:
            skip_len = len(pending_data)
            output, matched = _expect_serial(
                serial_sock, expect_pattern, expect_timeout, initial_data=pending_data
            )
            pending_data = b""
            if output and len(output) > skip_len:
                transcript.append(output[skip_len:].decode("latin-1", errors="replace"))
            if not matched:
                transcript.append(
                    f"\n[EXPECT TIMEOUT: pattern '{expect_pattern}' not found after {expect_timeout}s]\n"
                )
                all_ok = False
                break
        else:
            pending_data = b""

        if send_text:
            send_bytes = (
                send_text.encode("latin-1").decode("unicode_escape").encode("latin-1")
            )
            serial_sock.sendall(send_bytes)
            transcript.append(f"\n[SENT: {repr(send_text)}]\n")

    return transcript, pending_data, all_ok


def _format_transcript(full_transcript: str, max_lines: int = 200) -> list:
    """Format transcript with truncation for output."""
    output = ["```"]
    lines = full_transcript.split("\n")
    if len(lines) > max_lines:
        half = max_lines // 4
        output.extend(lines[:half])
        output.append(f"... ({len(lines) - half * 2} lines omitted)")
        output.extend(lines[-half:])
    else:
        output.extend(lines)
    output.append("```")
    return output


# Create server instance
server = Server("sgi-prom-analyzer")


# Tool definitions


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools."""
    tools = [
        # Basic Analysis
        Tool(
            name="list_proms",
            description="List all available PROM files in the samples directory",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="info",
            description="Get metadata for a PROM file (size, platform, entry point, SHA256, vectors)",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"}
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="hexdump",
            description="Simple hex dump of PROM data",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"},
                    "offset": {
                        "type": "integer",
                        "description": "Start offset",
                        "default": 0,
                    },
                    "length": {
                        "type": "integer",
                        "description": "Bytes to dump",
                        "default": 256,
                    },
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="xxd",
            description="Full xxd-compatible hex dump with options: -c cols, -g groupsize, -s seek, -l length, -e little-endian, -b binary, -i C-include, -p plain, -u uppercase",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"},
                    "seek": {
                        "type": "integer",
                        "description": "Start offset (-s)",
                        "default": 0,
                    },
                    "length": {
                        "type": "integer",
                        "description": "Bytes to dump (-l)",
                        "default": 256,
                    },
                    "cols": {
                        "type": "integer",
                        "description": "Bytes per line (-c)",
                        "default": 16,
                    },
                    "groupsize": {
                        "type": "integer",
                        "description": "Byte grouping (-g)",
                        "default": 4,
                    },
                    "little_endian": {
                        "type": "boolean",
                        "description": "Little-endian mode (-e)",
                        "default": False,
                    },
                    "binary": {
                        "type": "boolean",
                        "description": "Binary dump (-b)",
                        "default": False,
                    },
                    "c_include": {
                        "type": "boolean",
                        "description": "C include format (-i)",
                        "default": False,
                    },
                    "plain": {
                        "type": "boolean",
                        "description": "Plain hex (-p)",
                        "default": False,
                    },
                    "uppercase": {
                        "type": "boolean",
                        "description": "Uppercase hex (-u)",
                        "default": False,
                    },
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="disassemble",
            description="MIPS disassembly with hardware annotations",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"},
                    "offset": {
                        "type": "integer",
                        "description": "Start offset",
                        "default": 0,
                    },
                    "length": {
                        "type": "integer",
                        "description": "Bytes to disassemble (0=auto)",
                        "default": 0,
                    },
                    "max_instructions": {
                        "type": "integer",
                        "description": "Max instructions",
                        "default": 100,
                    },
                    "annotate": {
                        "type": "boolean",
                        "description": "Add hardware annotations",
                        "default": True,
                    },
                    "format": {
                        "type": "string",
                        "description": "Output format: text or markdown",
                        "default": "text",
                    },
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="strings",
            description="Extract ASCII strings from PROM",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"},
                    "min_length": {
                        "type": "integer",
                        "description": "Minimum string length",
                        "default": 4,
                    },
                    "max_strings": {
                        "type": "integer",
                        "description": "Maximum strings to return",
                        "default": 100,
                    },
                },
                "required": ["filename"],
            },
        ),
        # Structure Detection
        Tool(
            name="find_entry_points",
            description="Find reset vector and entry point from PROM header",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"}
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="find_vector_table",
            description="Find exception vectors (BEV mode) in PROM",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"}
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="find_function_prologues",
            description="Find function start patterns (addiu $sp, $sp, -N for MIPS32, daddiu $sp, $sp, -N for MIPS64)",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"},
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum results",
                        "default": 100,
                    },
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="find_jump_tables",
            description="Find jump tables (sequences of PROM addresses)",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"}
                },
                "required": ["filename"],
            },
        ),
        # Hardware Patterns
        Tool(
            name="find_hardware_probes",
            description="Find hardware probe patterns (MMIO access via LUI)",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"}
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="find_graphics_init",
            description="Find Newport/REX3 graphics initialization sequences",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"}
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="find_memory_detection",
            description="Find memory sizing code (MEMCFG register access)",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"}
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="find_device_detection",
            description="Find GIO slot device probing patterns",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"}
                },
                "required": ["filename"],
            },
        ),
        # Comparative Analysis
        Tool(
            name="diff_proms",
            description="Binary diff between two PROMs with context",
            inputSchema={
                "type": "object",
                "properties": {
                    "prom1": {"type": "string", "description": "First PROM filename"},
                    "prom2": {"type": "string", "description": "Second PROM filename"},
                    "max_diffs": {
                        "type": "integer",
                        "description": "Maximum diffs to show",
                        "default": 50,
                    },
                },
                "required": ["prom1", "prom2"],
            },
        ),
        Tool(
            name="find_common_code",
            description="Find shared code sequences across multiple PROMs",
            inputSchema={
                "type": "object",
                "properties": {
                    "prom_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of PROM filenames (empty = all)",
                    },
                    "block_size": {
                        "type": "integer",
                        "description": "Block size for comparison",
                        "default": 64,
                    },
                    "min_occurrences": {
                        "type": "integer",
                        "description": "Minimum occurrences",
                        "default": 2,
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="signature_search",
            description="Search for byte pattern across all PROMs",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Hex pattern to search (e.g., '3c1fbfa0')",
                    },
                    "prom_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "PROMs to search (empty = all)",
                    },
                },
                "required": ["pattern"],
            },
        ),
        Tool(
            name="version_compare",
            description="Compare two versions of the same platform PROM",
            inputSchema={
                "type": "object",
                "properties": {
                    "prom1": {"type": "string", "description": "First PROM filename"},
                    "prom2": {"type": "string", "description": "Second PROM filename"},
                },
                "required": ["prom1", "prom2"],
            },
        ),
        # Cross-Reference
        Tool(
            name="xref_address",
            description="Find references to a specific address in PROM",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"},
                    "address": {
                        "type": "string",
                        "description": "Address to find references to (hex)",
                    },
                },
                "required": ["filename", "address"],
            },
        ),
        Tool(
            name="annotate_address",
            description="Get hardware annotation for an address",
            inputSchema={
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "Address to annotate (hex)",
                    }
                },
                "required": ["address"],
            },
        ),
        Tool(
            name="list_devices",
            description="List all known hardware devices and their base addresses",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="device_registers",
            description="List registers for a specific device",
            inputSchema={
                "type": "object",
                "properties": {
                    "device": {
                        "type": "string",
                        "description": "Device ID (MC, HPC3, IOC2_IP24, REX3, etc.)",
                    }
                },
                "required": ["device"],
            },
        ),
        # Advanced Analysis Tools
        Tool(
            name="build_call_graph",
            description="Build function call graph from PROM. Returns caller/callee relationships, entry points, and orphan functions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"}
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="trace_boot_sequence",
            description="Trace boot sequence from reset vector, recording hardware accesses chronologically",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"},
                    "start_address": {
                        "type": "string",
                        "description": "Start address (hex), default 0xbfc003c0",
                        "default": "0xbfc003c0",
                    },
                    "max_steps": {
                        "type": "integer",
                        "description": "Maximum trace steps",
                        "default": 500,
                    },
                    "max_call_depth": {
                        "type": "integer",
                        "description": "Maximum call depth to follow",
                        "default": 3,
                    },
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="find_string_refs",
            description="Find code that references ASCII strings in the PROM",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"},
                    "min_length": {
                        "type": "integer",
                        "description": "Minimum string length",
                        "default": 4,
                    },
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="identify_arcs_callbacks",
            description="Identify ARCS (ARC firmware) callback functions from jump table",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"},
                    "table_address": {
                        "type": "string",
                        "description": "Known callback table address (hex), 0 for auto-detect",
                        "default": "0",
                    },
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="analyze_function",
            description="Detailed analysis of a single function: hardware accesses, calls, string refs, suggested name",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"},
                    "address": {
                        "type": "string",
                        "description": "Function address (hex)",
                    },
                },
                "required": ["filename", "address"],
            },
        ),
        Tool(
            name="build_function_database",
            description="Build complete function database with call graph, hardware accesses, and naming",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"}
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="export_symbols",
            description="Export function symbols in various formats (ghidra, ida, json)",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"},
                    "format": {
                        "type": "string",
                        "description": "Export format: ghidra, ida, json, dot",
                        "default": "json",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Output file path (optional, returns content if not specified)",
                    },
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="track_hardware_accesses",
            description="Track all hardware register accesses with full address reconstruction",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"},
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum results to return",
                        "default": 100,
                    },
                },
                "required": ["filename"],
            },
        ),
        # QEMU Debugging Tools
        Tool(
            name="parse_qemu_log",
            description="Parse QEMU -d unimp output, extract device accesses, map to SGI hardware",
            inputSchema={
                "type": "object",
                "properties": {
                    "log_file": {
                        "type": "string",
                        "description": "Path to QEMU log file",
                    },
                    "log_content": {
                        "type": "string",
                        "description": "Raw log content (alternative to log_file)",
                    },
                    "max_entries": {
                        "type": "integer",
                        "description": "Maximum entries to return",
                        "default": 200,
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="generate_expected_sequence",
            description="Generate expected hardware access sequence with register values from PROM analysis",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"},
                    "start_address": {
                        "type": "string",
                        "description": "Start address (hex)",
                        "default": "0xbfc003c0",
                    },
                    "max_steps": {
                        "type": "integer",
                        "description": "Maximum trace steps",
                        "default": 500,
                    },
                    "include_values": {
                        "type": "boolean",
                        "description": "Include expected register values",
                        "default": True,
                    },
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="analyze_register_values",
            description="Analyze register values from PROM code, track LUI+ORI sequences, detect polling loops",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"},
                    "device_filter": {
                        "type": "string",
                        "description": "Filter to specific device (e.g., 'MC', 'HPC3')",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum results to return",
                        "default": 100,
                    },
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="compare_execution",
            description="Compare QEMU trace vs expected PROM sequence, highlight divergences",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "PROM filename for expected sequence",
                    },
                    "log_file": {
                        "type": "string",
                        "description": "Path to QEMU log file",
                    },
                    "log_content": {
                        "type": "string",
                        "description": "Raw log content (alternative to log_file)",
                    },
                    "strict_order": {
                        "type": "boolean",
                        "description": "Require strict order matching",
                        "default": False,
                    },
                    "max_divergences": {
                        "type": "integer",
                        "description": "Maximum divergences to report",
                        "default": 50,
                    },
                },
                "required": ["filename"],
            },
        ),
        # QEMU Build Tools
        Tool(
            name="qemu_configure",
            description="Configure QEMU build (runs ../configure in qemu/build/)",
            inputSchema={
                "type": "object",
                "properties": {
                    "target_list": {
                        "type": "string",
                        "description": "Comma-separated target list",
                        "default": "mips64-softmmu",
                    },
                    "extra_args": {
                        "type": "string",
                        "description": "Additional configure arguments",
                    },
                    "clean": {
                        "type": "boolean",
                        "description": "Remove build directory first",
                        "default": False,
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="qemu_build",
            description="Build QEMU using ninja",
            inputSchema={
                "type": "object",
                "properties": {
                    "jobs": {
                        "type": "integer",
                        "description": "Number of parallel jobs",
                        "default": 4,
                    },
                    "target": {
                        "type": "string",
                        "description": "Specific ninja target (optional)",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="qemu_create_disk",
            description="Create a disk image for QEMU SCSI drive",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to disk image file",
                    },
                    "size": {
                        "type": "string",
                        "description": "Disk size (e.g., '100M', '1G', '4G')",
                        "default": "100M",
                    },
                    "format": {
                        "type": "string",
                        "description": "Image format (raw, qcow2)",
                        "default": "raw",
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="qemu_run_sgi",
            description="Run QEMU SGI machine with PROM. When `instance` is provided, `default_extra_args` and `default_snapshot` from the manifest are applied automatically; caller-provided `extra_args` is appended after the defaults.",
            inputSchema={
                "type": "object",
                "properties": {
                    "prom": {
                        "type": "string",
                        "description": "PROM file path or name from PROM_library",
                    },
                    "machine": {
                        "type": "string",
                        "description": "QEMU machine type: indy (IP24), indigo2 (IP22), indigo2-r10k (IP28), indigo2-r8k (IP26), indigo (IP20)",
                        "default": "indy",
                    },
                    "ram_mb": {
                        "type": "integer",
                        "description": "RAM size in MB",
                        "default": 64,
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Timeout in seconds",
                        "default": 5,
                    },
                    "debug_flags": {
                        "type": "string",
                        "description": "QEMU -d flags. Standard: unimp, int, guest_errors. SGI trace events: trace:sgi_mc_*, trace:sgi_hpc3_*, trace:sgi_newport_*. Combine: 'unimp,trace:sgi_hpc3_scsi_*'",
                        "default": "unimp",
                    },
                    "scsi_drives": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of SCSI drive paths. Append ':cdrom' for CD-ROM media (e.g., ['disk0.img', 'install.img:cdrom', 'apps.img:cdrom'])",
                    },
                    "extra_args": {
                        "type": "string",
                        "description": "Additional QEMU arguments",
                    },
                    "grep_filter": {
                        "type": "string",
                        "description": "Only show lines matching this pattern (regex)",
                    },
                    "save_log": {
                        "type": "string",
                        "description": "Save full log to this file path",
                    },
                    "autoload": {
                        "type": "boolean",
                        "description": "NVRAM AutoLoad setting (false = stop at System Maintenance Menu)",
                        "default": True,
                    },
                    "vnc": {
                        "type": "boolean",
                        "description": "Enable VNC display (listen on port 5900+). Shows Newport framebuffer with keyboard/mouse input. Connect with any VNC client. Password: sgi",
                        "default": False,
                    },
                    "vnc_port": {
                        "type": "integer",
                        "description": "VNC display number (port = 5900 + vnc_port). Default 0 = port 5900. Uses ,to=99 to auto-find free port.",
                        "default": 0,
                    },
                    "instance": {
                        "type": "string",
                        "description": "VM instance name — uses instance disk/NVRAM, overrides scsi_drives and machine/ram_mb from manifest",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="qemu_monitor",
            description="Run QEMU monitor command and return output",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Monitor command (e.g., 'info mtree -f', 'info registers')",
                    },
                    "prom": {"type": "string", "description": "PROM file to use"},
                    "machine": {
                        "type": "string",
                        "description": "QEMU machine type (indy, indigo2, indigo2-r10k, etc.)",
                        "default": "indy",
                    },
                    "boot_wait": {
                        "type": "number",
                        "description": "Seconds to wait before sending command (lets PROM execute first)",
                        "default": 2,
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Total timeout in seconds",
                        "default": 5,
                    },
                },
                "required": ["command"],
            },
        ),
        Tool(
            name="qemu_registers",
            description="Dump CPU registers while PROM is running (boots, waits, then captures register state)",
            inputSchema={
                "type": "object",
                "properties": {
                    "prom": {"type": "string", "description": "PROM file to use"},
                    "machine": {
                        "type": "string",
                        "description": "QEMU machine type (indy, indigo2, indigo2-r10k, etc.)",
                        "default": "indy",
                    },
                    "boot_wait": {
                        "type": "number",
                        "description": "Seconds to let PROM execute before dumping",
                        "default": 3,
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="qemu_guest_disasm",
            description="Disassemble guest code at a virtual address using QEMU's built-in disassembler. Supports SCSI drives and serial interactions for disassembling kernel code after boot.",
            inputSchema={
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "Guest virtual address (hex, e.g., '0xbfc00000')",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of instructions",
                        "default": 20,
                    },
                    "prom": {"type": "string", "description": "PROM file to use"},
                    "machine": {
                        "type": "string",
                        "description": "QEMU machine type (indy, indigo2, indigo2-r10k, etc.)",
                        "default": "indy",
                    },
                    "ram_mb": {
                        "type": "integer",
                        "description": "RAM size in MB",
                        "default": 64,
                    },
                    "boot_wait": {
                        "type": "number",
                        "description": "Seconds to wait before disassembling",
                        "default": 2,
                    },
                    "scsi_drives": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "SCSI drive paths (append :cdrom for CD-ROM). Required for kernel code.",
                    },
                    "interactions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "expect": {
                                    "type": "string",
                                    "description": "Pattern to wait for",
                                },
                                "send": {
                                    "type": "string",
                                    "description": "Text to send",
                                },
                                "timeout": {
                                    "type": "number",
                                    "description": "Timeout",
                                    "default": 30,
                                },
                            },
                            "required": ["expect", "send"],
                        },
                        "description": "Serial interactions to reach desired state before disassembly",
                    },
                    "extra_args": {
                        "type": "string",
                        "description": "Additional QEMU arguments (e.g., '-icount shift=0,sleep=off')",
                    },
                    "autoload": {
                        "type": "boolean",
                        "description": "NVRAM AutoLoad",
                        "default": True,
                    },
                },
                "required": ["address"],
            },
        ),
        Tool(
            name="qemu_guest_memory",
            description="Dump guest physical memory at an address. Supports SCSI drives and serial interactions for reading kernel memory after boot.",
            inputSchema={
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "Physical address (hex, e.g., '0x1fb80000')",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of 32-bit words to dump",
                        "default": 16,
                    },
                    "prom": {"type": "string", "description": "PROM file to use"},
                    "machine": {
                        "type": "string",
                        "description": "QEMU machine type (indy, indigo2, indigo2-r10k, etc.)",
                        "default": "indy",
                    },
                    "ram_mb": {
                        "type": "integer",
                        "description": "RAM size in MB",
                        "default": 64,
                    },
                    "boot_wait": {
                        "type": "number",
                        "description": "Seconds to wait before dumping",
                        "default": 2,
                    },
                    "scsi_drives": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "SCSI drive paths (append :cdrom for CD-ROM). Required for kernel memory.",
                    },
                    "interactions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "expect": {
                                    "type": "string",
                                    "description": "Pattern to wait for",
                                },
                                "send": {
                                    "type": "string",
                                    "description": "Text to send",
                                },
                                "timeout": {
                                    "type": "number",
                                    "description": "Timeout",
                                    "default": 30,
                                },
                            },
                            "required": ["expect", "send"],
                        },
                        "description": "Serial interactions to reach desired state before dumping",
                    },
                    "extra_args": {
                        "type": "string",
                        "description": "Additional QEMU arguments (e.g., '-icount shift=0,sleep=off')",
                    },
                    "autoload": {
                        "type": "boolean",
                        "description": "NVRAM AutoLoad",
                        "default": True,
                    },
                },
                "required": ["address"],
            },
        ),
        # Log analysis tools
        Tool(
            name="log_grep",
            description="Search a log file for lines matching a pattern (regex supported)",
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Path to log file"},
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for",
                    },
                    "invert": {
                        "type": "boolean",
                        "description": "Invert match (show non-matching lines)",
                        "default": False,
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": "Maximum lines to return",
                        "default": 200,
                    },
                },
                "required": ["file", "pattern"],
            },
        ),
        Tool(
            name="log_context",
            description="Show lines around a pattern match in a log file",
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Path to log file"},
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to find",
                    },
                    "before": {
                        "type": "integer",
                        "description": "Lines before match",
                        "default": 5,
                    },
                    "after": {
                        "type": "integer",
                        "description": "Lines after match",
                        "default": 5,
                    },
                    "occurrence": {
                        "type": "integer",
                        "description": "Which occurrence (1=first, -1=last)",
                        "default": 1,
                    },
                },
                "required": ["file", "pattern"],
            },
        ),
        Tool(
            name="log_uniq",
            description="Show unique lines with counts (like uniq -c), useful for finding patterns",
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Path to log file"},
                    "pattern": {
                        "type": "string",
                        "description": "Optional: filter to lines matching this pattern first",
                    },
                    "max_groups": {
                        "type": "integer",
                        "description": "Maximum unique groups to show",
                        "default": 100,
                    },
                },
                "required": ["file"],
            },
        ),
        Tool(
            name="log_range",
            description="Show a range of lines from a log file",
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Path to log file"},
                    "start": {
                        "type": "integer",
                        "description": "Start line (1-indexed, negative counts from end)",
                        "default": 1,
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of lines to show",
                        "default": 100,
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Optional: filter to lines matching this pattern",
                    },
                },
                "required": ["file"],
            },
        ),
        Tool(
            name="nvram_dump",
            description="Inspect SGI NVRAM variables from a persistent NVRAM file. Shows all environment variables (console, autoload, syspart, osloader, etc.) and their current values.",
            inputSchema={
                "type": "object",
                "properties": {
                    "instance": {
                        "type": "string",
                        "description": "VM instance name — uses instance NVRAM file (highest priority)",
                    },
                    "file": {
                        "type": "string",
                        "description": "Path to NVRAM file (e.g., 'sgi_indy_nvram.bin'). If relative, searched in project root.",
                    },
                    "machine": {
                        "type": "string",
                        "description": "Machine type for default NVRAM file: indy, indigo2, indigo2-r10k",
                        "default": "indy",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="nvram_set",
            description="Patch an SGI NVRAM variable in the persistent NVRAM file. Updates the value and recomputes the checksum. Common variables: console (d=serial, g=graphics, g1/g2=specific graphics, d1/d2=specific serial), autoload (Y/N), dbaud (9600/19200/38400), netaddr (IP), volume (0-255), scsihostid (0-7).",
            inputSchema={
                "type": "object",
                "properties": {
                    "variable": {
                        "type": "string",
                        "description": "Variable name: console, autoload, syspart, osloader, osfile, osopts, dbaud, diskless, timezone, ospart, netaddr, nokbd, volume, scsihostid, sgilogo, nogui, autopower, monitor",
                    },
                    "value": {
                        "type": "string",
                        "description": "New value for the variable",
                    },
                    "instance": {
                        "type": "string",
                        "description": "VM instance name — uses instance NVRAM file (highest priority)",
                    },
                    "file": {
                        "type": "string",
                        "description": "Path to NVRAM file. If relative, searched in project root.",
                    },
                    "machine": {
                        "type": "string",
                        "description": "Machine type for default NVRAM file: indy, indigo2, indigo2-r10k",
                        "default": "indy",
                    },
                },
                "required": ["variable", "value"],
            },
        ),
        Tool(
            name="find_instructions",
            description=(
                "Search for MIPS instructions by mnemonic and optional field filters. "
                "Supported mnemonics: cache, mfc0, mtc0, dmfc0, dmtc0, lui, ori, "
                "lw, sw, ld, sd, jal, jr, addiu, daddiu, beq, bne, and more. "
                "For CACHE: filter by cache_op (5-bit), cache_type (0=PI,1=PD,2=T,3=SD), "
                "or cache_operation (1=LoadTag,2=StoreTag,6=LoadData,7=StoreData). "
                "For COP0: filter by cp0_reg (e.g., 28=TagLo, 26=ECC). "
                "Use context to show surrounding instructions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"},
                    "mnemonic": {
                        "type": "string",
                        "description": "Instruction mnemonic (e.g., 'cache', 'dmtc0')",
                    },
                    "rs": {"type": "integer", "description": "Filter by rs field"},
                    "rt": {
                        "type": "integer",
                        "description": "Filter by rt field (for CACHE: full 5-bit op)",
                    },
                    "rd": {"type": "integer", "description": "Filter by rd field"},
                    "imm": {
                        "type": "integer",
                        "description": "Filter by immediate value (unsigned 16-bit)",
                    },
                    "cp0_reg": {
                        "type": "integer",
                        "description": "Filter by CP0 register number (mtc0/mfc0/dmtc0/dmfc0)",
                    },
                    "cache_op": {
                        "type": "integer",
                        "description": "Filter by 5-bit CACHE op code",
                    },
                    "cache_type": {
                        "type": "integer",
                        "description": "Filter by 2-bit cache type (0=PI,1=PD,2=T,3=SD)",
                    },
                    "cache_operation": {
                        "type": "integer",
                        "description": "Filter by 3-bit cache operation (1=LoadTag,2=StoreTag,6=LoadData,7=StoreData)",
                    },
                    "context": {
                        "type": "integer",
                        "description": "Show N surrounding instructions for context",
                        "default": 0,
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum results",
                        "default": 200,
                    },
                },
                "required": ["filename", "mnemonic"],
            },
        ),
        Tool(
            name="qemu_serial_interact",
            description="Run QEMU with interactive serial console: launches QEMU with serial on a Unix socket, executes expect/send pairs, returns full transcript. When `instance` is provided, `default_extra_args` and `default_snapshot` from the manifest are applied automatically; caller-provided `extra_args` is appended after the defaults.",
            inputSchema={
                "type": "object",
                "properties": {
                    "prom": {
                        "type": "string",
                        "description": "PROM file path or name from PROM_library",
                    },
                    "machine": {
                        "type": "string",
                        "description": "QEMU machine type",
                        "default": "indy",
                    },
                    "ram_mb": {
                        "type": "integer",
                        "description": "RAM size in MB",
                        "default": 64,
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Total session timeout in seconds",
                        "default": 30,
                    },
                    "boot_wait": {
                        "type": "number",
                        "description": "Seconds to collect boot output before first interaction",
                        "default": 10,
                    },
                    "autoload": {
                        "type": "boolean",
                        "description": "NVRAM AutoLoad (false = stop at System Maintenance Menu)",
                        "default": False,
                    },
                    "interactions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "expect": {
                                    "type": "string",
                                    "description": "String or regex pattern to wait for",
                                },
                                "send": {
                                    "type": "string",
                                    "description": "Text to send (use \\r for Enter)",
                                },
                                "timeout": {
                                    "type": "number",
                                    "description": "Timeout for this expect in seconds",
                                    "default": 10,
                                },
                            },
                            "required": ["expect", "send"],
                        },
                        "description": "List of expect/send interaction pairs",
                    },
                    "scsi_drives": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "SCSI drive paths (append :cdrom for CD-ROM)",
                    },
                    "debug_flags": {
                        "type": "string",
                        "description": "QEMU -d flags. Standard: unimp, int, guest_errors. SGI trace events: trace:sgi_mc_*, trace:sgi_hpc3_*, trace:sgi_newport_*. Combine: 'unimp,trace:sgi_hpc3_scsi_*'",
                        "default": "",
                    },
                    "collect_after": {
                        "type": "number",
                        "description": "Seconds to collect output after last interaction",
                        "default": 3,
                    },
                    "save_log": {
                        "type": "string",
                        "description": "Save full transcript to this file path",
                    },
                    "extra_args": {
                        "type": "string",
                        "description": "Additional QEMU arguments (e.g., '-icount shift=0,sleep=off')",
                    },
                    "instance": {
                        "type": "string",
                        "description": "VM instance name — uses instance disk/NVRAM, overrides scsi_drives and machine/ram_mb from manifest",
                    },
                },
                "required": [],
            },
        ),
        # --- Persistent QEMU session tools ---
        Tool(
            name="qemu_session_start",
            description="Start a persistent QEMU session. Keeps QEMU running between calls for interactive serial exploration. Returns session ID + initial output. Kills any orphaned QEMU processes first. When `instance` is provided, `default_extra_args` and `default_snapshot` from the manifest are applied automatically; caller-provided `extra_args` is appended after the defaults. Refuses to boot a disk marked dirty by a force-kill (see force_dirty) or to write-open an immutable golden (boot a fresh overlay instead).",
            inputSchema={
                "type": "object",
                "properties": {
                    "prom": {
                        "type": "string",
                        "description": "PROM file path or name from PROM_library",
                    },
                    "force_dirty": {
                        "type": "boolean",
                        "description": "Boot even if a disk is marked dirty (force-killed, not yet scanned). NOT recommended — risks an EFSCORRUPTED replay. Prefer xfs_scan or rolling back to a golden.",
                        "default": False,
                    },
                    "machine": {
                        "type": "string",
                        "description": "QEMU machine type",
                        "default": "indy",
                    },
                    "ram_mb": {
                        "type": "integer",
                        "description": "RAM size in MB",
                        "default": 64,
                    },
                    "boot_wait": {
                        "type": "number",
                        "description": "Seconds to collect initial boot output",
                        "default": 15,
                    },
                    "autoload": {
                        "type": "boolean",
                        "description": "NVRAM AutoLoad (false = stop at System Maintenance Menu)",
                        "default": False,
                    },
                    "scsi_drives": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "SCSI drive paths (append :cdrom for CD-ROM)",
                    },
                    "debug_flags": {
                        "type": "string",
                        "description": "QEMU -d flags. Standard: unimp, int, guest_errors. SGI trace events: trace:sgi_mc_*, trace:sgi_hpc3_*, trace:sgi_newport_*. Combine: 'unimp,trace:sgi_hpc3_scsi_*'",
                        "default": "",
                    },
                    "extra_args": {
                        "type": "string",
                        "description": "Additional QEMU arguments (e.g., '-icount shift=0,sleep=off')",
                    },
                    "snapshot": {
                        "type": "string",
                        "description": "Restore from this saved snapshot (-loadvm) instead of fresh boot",
                    },
                    "vnc": {
                        "type": "boolean",
                        "description": "Enable VNC display (listen on port 5900+). Shows Newport framebuffer with keyboard/mouse input. Connect with any VNC client. Password: sgi",
                        "default": False,
                    },
                    "vnc_port": {
                        "type": "integer",
                        "description": "VNC display number (port = 5900 + vnc_port). Default 0 = port 5900.",
                        "default": 0,
                    },
                    "save_log": {
                        "type": "string",
                        "description": "Save QEMU debug/trace output (-d flags) to this file path. Persists after session stops. Use with debug_flags to capture trace events.",
                    },
                    "instance": {
                        "type": "string",
                        "description": "VM instance name — uses instance disk/NVRAM, overrides scsi_drives and machine/ram_mb from manifest",
                    },
                    "icount_shift": {
                        "type": "string",
                        "description": "Enable deterministic icount with this shift (e.g. '7' or 'auto'). Use '7' for sgi-ip54; 'auto' THROTTLES to realtime — pair with sleep via rr_mode. Required for record/replay.",
                    },
                    "rr_mode": {
                        "type": "string",
                        "enum": ["off", "record", "replay"],
                        "description": "Deterministic record/replay mode (needs icount_shift + rrfile). 'record' logs nondeterministic inputs; 'replay' re-executes them. For sgi-ip54 the pvclock path is already replay-safe.",
                        "default": "off",
                    },
                    "rrfile": {
                        "type": "string",
                        "description": "Path to the record/replay event log (used with rr_mode record/replay).",
                    },
                    "rrsnapshot": {
                        "type": "string",
                        "description": "Named VM snapshot taken at record start / restored at replay start. REQUIRED for gdb reverse-execution (the auto start_debugging path asserts on sgi-ip54). Saved into the disk overlay — use a disposable fork.",
                    },
                    "gdb_port": {
                        "type": "integer",
                        "description": "Start the gdbstub on tcp::<port> (connect with gdb-multiarch; see pyirix_qemu/guest_gdb.py).",
                    },
                    "start_stopped": {
                        "type": "boolean",
                        "description": "Start QEMU halted (-S) so gdb can attach before the first instruction. Pair with gdb_port + rr_mode=replay for reverse-debugging.",
                        "default": False,
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="qemu_session_send",
            description="Send text to a persistent QEMU session's serial console and return output. With empty text, acts as a pure read. With expect pattern, waits for match.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID from qemu_session_start",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to send (use \\r for Enter, empty string for read-only)",
                        "default": "",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Seconds to wait for output after sending",
                        "default": 5,
                    },
                    "expect": {
                        "type": "string",
                        "description": "Wait until this regex pattern appears in output (or timeout)",
                    },
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="qemu_serial_write_file",
            description=(
                "Write a text file to the IRIX guest VM over the serial console using printf commands. "
                "Content is split into lines and sent in batches; a sentinel echo confirms each batch. "
                "Handles single quotes and backslashes in content. The session should be at a POSIX sh "
                "prompt (exec sh) to avoid csh history-expansion issues — use use_sh=true (default) to "
                "ensure this automatically. After writing, verifies the line count matches."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID from qemu_session_start",
                    },
                    "guest_path": {
                        "type": "string",
                        "description": "Destination file path on the guest (e.g. /var/tmp/timer.c)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full text content to write",
                    },
                    "batch_size": {
                        "type": "integer",
                        "default": 25,
                        "description": "Number of lines per batch (default 25)",
                    },
                    "timeout_per_batch": {
                        "type": "integer",
                        "default": 30,
                        "description": "Seconds to wait for each batch sentinel (default 30)",
                    },
                    "use_sh": {
                        "type": "boolean",
                        "default": True,
                        "description": "Send 'exec sh' first to ensure POSIX sh context (default true)",
                    },
                },
                "required": ["session_id", "guest_path", "content"],
            },
        ),
        Tool(
            name="qemu_serial_upload_binary",
            description=(
                "Upload a binary file from the host to the IRIX guest over the serial console. "
                "Uuencodes the file on the host, transfers the ASCII text via printf batches "
                "(same mechanism as qemu_serial_write_file), then runs uudecode on the guest "
                "to recover the binary. Works for .o object files, executables, etc. "
                "The session should be at a shell prompt."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID from qemu_session_start",
                    },
                    "host_path": {
                        "type": "string",
                        "description": "Path to binary file on the host",
                    },
                    "guest_path": {
                        "type": "string",
                        "description": "Destination path on the IRIX guest (e.g. /var/sysgen/boot/if_pvnet.o)",
                    },
                    "batch_size": {
                        "type": "integer",
                        "default": 20,
                        "description": "Uuencode lines per batch (default 20)",
                    },
                    "timeout_per_batch": {
                        "type": "integer",
                        "default": 30,
                        "description": "Seconds to wait per batch sentinel (default 30)",
                    },
                },
                "required": ["session_id", "host_path", "guest_path"],
            },
        ),
        Tool(
            name="qemu_session_snapshot",
            description="[DEPRECATED] Save a VM snapshot on a running persistent QEMU session. WARNING: snapshots are incompatible across QEMU builds and can corrupt the disk when loaded. Prefer vm_instance_fork for disposable test copies.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Session ID"},
                    "snapshot_name": {
                        "type": "string",
                        "description": "Name for the snapshot",
                    },
                    "instance": {
                        "type": "string",
                        "description": "VM instance name — records snapshot in manifest",
                    },
                    "description": {
                        "type": "string",
                        "description": "Human-readable snapshot description",
                    },
                },
                "required": ["session_id", "snapshot_name"],
            },
        ),
        Tool(
            name="qemu_session_monitor",
            description="Send an HMP monitor command to a running persistent QEMU session. Returns the monitor response text. Useful for 'info block', 'change', 'info registers', etc.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID from qemu_session_start",
                    },
                    "command": {
                        "type": "string",
                        "description": "Monitor command (e.g., 'info block', 'info registers')",
                    },
                },
                "required": ["session_id", "command"],
            },
        ),
        Tool(
            name="qemu_session_stop",
            description="Stop a persistent QEMU session and clean up all resources. By default attempts a clean in-guest shutdown (sync; init 0) when a shell is reachable, then monitor quit; SIGKILL is a last resort and marks the disk dirty.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID to stop",
                    },
                    "graceful": {
                        "type": "boolean",
                        "description": "Attempt an in-guest 'sync; init 0' clean shutdown first (default true). Set false to skip straight to monitor quit (e.g. guest is wedged).",
                        "default": True,
                    },
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="qemu_session_cleanup",
            description="Force-kill QEMU sessions and clean up. SIGKILLs, so affected disks are marked DIRTY (xfs_scan or roll back to a golden before reuse); prefer qemu_session_stop (graceful) when possible. scope='own' (DEFAULT) kills only this session's tracked sessions + our own child QEMUs — it will NOT touch another Claude session's VMs (multi-session safe). scope='all' is the nuclear option that kills every qemu-system-mips64 on the machine — use ONLY when you know no other session is active.",
            inputSchema={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["own", "all"],
                        "description": "'own' (default): only our own VMs. 'all': every QEMU on the box (nuclear).",
                        "default": "own",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="newport_screendump",
            description="Capture the Newport framebuffer from a running QEMU session. Dumps raw VRAM through the full compositing pipeline (DID/XMAP/CMAP/RAMDAC) to a PPM file, then converts to PNG. Returns the PNG file path for visual inspection. Works independently of the display surface — shows what's actually in VRAM even if the display window appears black.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID from qemu_session_start",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Output PNG path (default: /tmp/newport_fb.png)",
                    },
                    "method": {
                        "type": "string",
                        "description": "Dump method: 'vram' (raw VRAM via qom-set fb-dump, default) or 'screendump' (QEMU display surface via monitor screendump command)",
                        "default": "vram",
                    },
                    "label": {
                        "type": "string",
                        "description": "Short label for archival (e.g., 'vc2_fix_test'). When provided, saves a copy to framebuffers/{timestamp}_{label}.png in the project root with a companion .txt description file.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Description of what this capture is testing or showing. Saved alongside the archived PNG.",
                    },
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="newport_inspect",
            description="Query Newport diagnostic properties from a running QEMU session. Returns structured text about CMAP palette, XMAP modes, VC2 timing, REX3 drawing state, or DCB bus state. Zero-rebuild alternative to adding debug prints.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID from qemu_session_start",
                    },
                    "subsystem": {
                        "type": "string",
                        "description": "Subsystem to inspect: 'all' (default), 'cmap', 'xmap', 'vc2', 'rex3', 'dcb'",
                        "default": "all",
                    },
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="newport_sendkey",
            description="Inject keyboard input into a running QEMU session via the monitor sendkey command. Keys flow through the PS/2 keyboard controller to the guest OS. Use 'keys' for raw key specs or 'text' to type a string (characters are converted to sendkey sequences automatically).",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID from qemu_session_start",
                    },
                    "keys": {
                        "type": "string",
                        "description": "Key specification (e.g., 'a', 'ret', 'ctrl-alt-delete', 'shift-a')",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text string to type. Characters are converted to sendkey sequences (e.g., 'root\\n' types r,o,o,t,Enter). Supports a-z, A-Z, 0-9, common punctuation, space, tab, newline.",
                    },
                    "delay_ms": {
                        "type": "integer",
                        "description": "Delay between keystrokes in milliseconds (default: 100)",
                        "default": 100,
                    },
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="newport_mouse",
            description="Inject mouse input into a running QEMU session via the monitor. Sends relative mouse movement and/or button state.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID from qemu_session_start",
                    },
                    "dx": {
                        "type": "integer",
                        "description": "Relative X movement",
                        "default": 0,
                    },
                    "dy": {
                        "type": "integer",
                        "description": "Relative Y movement",
                        "default": 0,
                    },
                    "dz": {
                        "type": "integer",
                        "description": "Scroll wheel movement",
                        "default": 0,
                    },
                    "buttons": {
                        "type": "integer",
                        "description": "Button bitmask (1=left, 2=middle, 4=right)",
                    },
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="qemu_scsi_trace",
            description="Boot QEMU and return a structured SCSI command trace. Automatically enables -d unimp and captures debug log via -D.",
            inputSchema={
                "type": "object",
                "properties": {
                    "disk": {
                        "type": "string",
                        "description": "Path to SCSI disk image",
                    },
                    "cdrom": {
                        "type": "string",
                        "description": "Path to SCSI CD-ROM image",
                    },
                    "machine": {
                        "type": "string",
                        "description": "QEMU machine type",
                        "default": "indy",
                    },
                    "ram_mb": {
                        "type": "integer",
                        "description": "RAM size in MB",
                        "default": 64,
                    },
                    "max_wait": {
                        "type": "integer",
                        "description": "Maximum boot wait time in seconds",
                        "default": 120,
                    },
                    "errors_only": {
                        "type": "boolean",
                        "description": "Only show failed commands",
                        "default": False,
                    },
                    "max_commands": {
                        "type": "integer",
                        "description": "Maximum commands to return (0=unlimited)",
                        "default": 0,
                    },
                    "save_log": {
                        "type": "string",
                        "description": "Save raw debug log to this file path",
                    },
                    "extra_args": {
                        "type": "string",
                        "description": "Additional QEMU arguments",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="scsi_log_parse",
            description="Parse an existing log file for SCSI events. Same analysis as qemu_scsi_trace but doesn't boot QEMU.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Path to log file containing QEMU -d unimp output",
                    },
                    "errors_only": {
                        "type": "boolean",
                        "description": "Only show failed commands",
                        "default": False,
                    },
                    "max_commands": {
                        "type": "integer",
                        "description": "Maximum commands to return (0=unlimited)",
                        "default": 0,
                    },
                    "target_filter": {
                        "type": "integer",
                        "description": "Only show commands to this SCSI target ID",
                    },
                    "opcode_filter": {
                        "type": "string",
                        "description": "Only show this opcode (name like MODE_SENSE or hex like 0x1a)",
                    },
                },
                "required": ["file"],
            },
        ),
        Tool(
            name="qemu_boot_milestones",
            description="Boot QEMU and report structured progress milestones. Shows how far the boot got with a concise timeline.",
            inputSchema={
                "type": "object",
                "properties": {
                    "disk": {
                        "type": "string",
                        "description": "Path to SCSI disk image",
                    },
                    "cdrom": {
                        "type": "string",
                        "description": "Path to SCSI CD-ROM image",
                    },
                    "machine": {
                        "type": "string",
                        "description": "QEMU machine type",
                        "default": "indy",
                    },
                    "ram_mb": {
                        "type": "integer",
                        "description": "RAM size in MB",
                        "default": 64,
                    },
                    "max_wait": {
                        "type": "integer",
                        "description": "Maximum boot wait time in seconds",
                        "default": 120,
                    },
                    "reload": {
                        "type": "boolean",
                        "description": "Send 'r' to reload miniroot (for corrupted disks)",
                        "default": False,
                    },
                    "extra_args": {
                        "type": "string",
                        "description": "Additional QEMU arguments",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="qemu_disk_convert",
            description="Convert a disk image between formats (e.g., raw to qcow2 for snapshot support)",
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Source disk image path",
                    },
                    "dest": {
                        "type": "string",
                        "description": "Destination disk image path (optional, auto-generated if omitted)",
                    },
                    "output_format": {
                        "type": "string",
                        "description": "Output format: qcow2, raw",
                        "default": "qcow2",
                    },
                },
                "required": ["source"],
            },
        ),
        Tool(
            name="qemu_snapshot_save",
            description="[DEPRECATED] Boot QEMU with serial interaction, then save a VM snapshot. WARNING: qcow2 snapshots are incompatible across QEMU builds — loading a stale snapshot corrupts the disk. Prefer vm_instance_fork + vm_instance_reset instead.",
            inputSchema={
                "type": "object",
                "properties": {
                    "snapshot_name": {
                        "type": "string",
                        "description": "Name for the snapshot (e.g., 'miniroot_booted')",
                    },
                    "prom": {"type": "string", "description": "PROM file path or name"},
                    "machine": {
                        "type": "string",
                        "description": "QEMU machine type",
                        "default": "indy",
                    },
                    "ram_mb": {
                        "type": "integer",
                        "description": "RAM size in MB",
                        "default": 64,
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Total session timeout in seconds",
                        "default": 300,
                    },
                    "boot_wait": {
                        "type": "number",
                        "description": "Seconds to collect boot output before first interaction",
                        "default": 10,
                    },
                    "autoload": {
                        "type": "boolean",
                        "description": "NVRAM AutoLoad",
                        "default": False,
                    },
                    "interactions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "expect": {
                                    "type": "string",
                                    "description": "String or regex pattern to wait for",
                                },
                                "send": {
                                    "type": "string",
                                    "description": "Text to send (use \\r for Enter)",
                                },
                                "timeout": {
                                    "type": "number",
                                    "description": "Timeout for this expect in seconds",
                                    "default": 30,
                                },
                            },
                            "required": ["expect", "send"],
                        },
                        "description": "List of expect/send pairs to reach desired state before snapshot",
                    },
                    "wait_after_interactions": {
                        "type": "number",
                        "description": "Extra seconds to wait after last interaction before saving snapshot",
                        "default": 5,
                    },
                    "scsi_drives": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "SCSI drive paths (append :cdrom for CD-ROM). At least one must be .qcow2",
                    },
                    "debug_flags": {
                        "type": "string",
                        "description": "QEMU -d flags. Standard: unimp, int, guest_errors. SGI trace events: trace:sgi_mc_*, trace:sgi_hpc3_*, trace:sgi_newport_*. Combine: 'unimp,trace:sgi_hpc3_scsi_*'",
                        "default": "",
                    },
                    "save_log": {
                        "type": "string",
                        "description": "Save full transcript to this file path",
                    },
                    "extra_args": {
                        "type": "string",
                        "description": "Additional QEMU arguments (e.g., '-icount shift=0,sleep=off')",
                    },
                    "instance": {
                        "type": "string",
                        "description": "VM instance name — uses instance disk/NVRAM and records snapshot in manifest",
                    },
                    "description": {
                        "type": "string",
                        "description": "Human-readable snapshot description",
                    },
                },
                "required": ["snapshot_name"],
            },
        ),
        Tool(
            name="qemu_snapshot_restore",
            description="[DEPRECATED] Restore a VM snapshot and collect serial output. STRONG WARNING: loading snapshots across QEMU builds will corrupt the qcow2 disk. Prefer vm_instance_fork + vm_instance_reset for disposable test instances.",
            inputSchema={
                "type": "object",
                "properties": {
                    "snapshot_name": {
                        "type": "string",
                        "description": "Name of the snapshot to restore",
                    },
                    "prom": {"type": "string", "description": "PROM file path or name"},
                    "machine": {
                        "type": "string",
                        "description": "QEMU machine type",
                        "default": "indy",
                    },
                    "ram_mb": {
                        "type": "integer",
                        "description": "RAM size in MB",
                        "default": 64,
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Seconds to collect serial output after restore",
                        "default": 30,
                    },
                    "autoload": {
                        "type": "boolean",
                        "description": "NVRAM AutoLoad",
                        "default": False,
                    },
                    "interactions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "expect": {
                                    "type": "string",
                                    "description": "String or regex pattern to wait for",
                                },
                                "send": {
                                    "type": "string",
                                    "description": "Text to send (use \\r for Enter)",
                                },
                                "timeout": {
                                    "type": "number",
                                    "description": "Timeout for this expect in seconds",
                                    "default": 10,
                                },
                            },
                            "required": ["expect", "send"],
                        },
                        "description": "Optional expect/send pairs after restore",
                    },
                    "scsi_drives": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "SCSI drive paths (must match the original snapshot session)",
                    },
                    "debug_flags": {
                        "type": "string",
                        "description": "QEMU -d flags. Standard: unimp, int, guest_errors. SGI trace events: trace:sgi_mc_*, trace:sgi_hpc3_*, trace:sgi_newport_*. Combine: 'unimp,trace:sgi_hpc3_scsi_*'",
                        "default": "",
                    },
                    "collect_after": {
                        "type": "number",
                        "description": "Seconds to collect output after last interaction",
                        "default": 5,
                    },
                    "save_log": {
                        "type": "string",
                        "description": "Save full transcript to this file path",
                    },
                    "grep_filter": {
                        "type": "string",
                        "description": "Only show debug log lines matching this pattern",
                    },
                    "instance": {
                        "type": "string",
                        "description": "VM instance name — uses instance disk/NVRAM, overrides scsi_drives and machine/ram_mb from manifest",
                    },
                },
                "required": ["snapshot_name"],
            },
        ),
        # ── IP54 PROM Build Tools ──────────────────────────────────
        Tool(
            name="prom_build",
            description="Build the IP54 PROM (make all). Compiles assembly, C, links, and produces ip54.bin",
            inputSchema={
                "type": "object",
                "properties": {
                    "clean": {
                        "type": "boolean",
                        "description": "Run 'make clean' first",
                        "default": False,
                    },
                    "target": {
                        "type": "string",
                        "description": "Specific make target (e.g., 'asm', 'compile', 'link', 'all')",
                        "default": "all",
                    },
                    "jobs": {
                        "type": "integer",
                        "description": "Parallel jobs (-j flag)",
                        "default": 4,
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="prom_try_compile",
            description="Try compiling a single source file to check for errors",
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Source file path relative to prom-building/ (e.g., 'src/fw/finit.c')",
                    }
                },
                "required": ["file"],
            },
        ),
        Tool(
            name="prom_symbols",
            description="List symbols in the PROM ELF (nm). Useful for checking what's defined/undefined",
            inputSchema={
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "description": "Grep filter for symbol names",
                    },
                    "undefined_only": {
                        "type": "boolean",
                        "description": "Show only undefined symbols",
                        "default": False,
                    },
                    "sort": {
                        "type": "string",
                        "description": "Sort order: 'name' or 'address'",
                        "default": "address",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="prom_disasm",
            description="Disassemble a function or address range in the IP54 PROM ELF",
            inputSchema={
                "type": "object",
                "properties": {
                    "function": {
                        "type": "string",
                        "description": "Function name to disassemble (e.g., 'firmware', 'fwEntry')",
                    },
                    "address": {
                        "type": "string",
                        "description": "Address to disassemble (hex, e.g., '0xbfc00048')",
                    },
                    "context": {
                        "type": "integer",
                        "description": "Lines of context around address",
                        "default": 20,
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="prom_sections",
            description="Show ELF section headers for the IP54 PROM (addresses, sizes, layout)",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="prom_preprocess",
            description="Run the C preprocessor on a source file or expression to see macro expansion",
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Source file to preprocess",
                    },
                    "expression": {
                        "type": "string",
                        "description": "C expression to evaluate (e.g., 'PROM_STACK' or 'PHYS_RAMBASE')",
                    },
                    "header": {
                        "type": "string",
                        "description": "Header to include before expression (e.g., 'sys/IP32.h')",
                    },
                },
                "required": [],
            },
        ),
        # === Boot Harness Tools ===
        Tool(
            name="harness_boot",
            description="Smart IRIX boot attempt with idle timeouts, repeat detection, and auto-bail on fatal errors. Uses Unix socket serial I/O with intelligent waiting — no more fixed 5-minute timeouts. Returns structured result with success/failure, bail reason, and transcript.",
            inputSchema={
                "type": "object",
                "properties": {
                    "disk": {
                        "type": "string",
                        "description": "Disk image path (qcow2 recommended for snapshots)",
                        "default": str(
                            Path(__file__).parent.parent / "irix_disk.qcow2"
                        ),
                    },
                    "cdrom": {
                        "type": "string",
                        "description": "CD-ROM image path (overrides version)",
                    },
                    "version": {
                        "type": "string",
                        "description": "IRIX version: '6.5', '6.2', or '5.3'",
                        "default": "6.5",
                    },
                    "machine": {
                        "type": "string",
                        "description": "QEMU machine type",
                        "default": "indy",
                    },
                    "ram_mb": {
                        "type": "integer",
                        "description": "RAM in MB",
                        "default": 64,
                    },
                    "reload": {
                        "type": "boolean",
                        "description": "Send 'r' to reload miniroot instead of 'c'",
                        "default": False,
                    },
                    "max_wait": {
                        "type": "integer",
                        "description": "Max seconds to wait for installer prompt",
                        "default": 600,
                    },
                    "debug_flags": {
                        "type": "string",
                        "description": "QEMU -d flags. Standard: unimp, int, guest_errors. SGI trace events: trace:sgi_mc_*, trace:sgi_hpc3_*, trace:sgi_newport_*. Combine: 'unimp,trace:sgi_hpc3_scsi_*'",
                    },
                    "repeat_threshold": {
                        "type": "integer",
                        "description": "Bail after N identical lines (0=disable)",
                        "default": 3,
                    },
                    "transcript_tail": {
                        "type": "integer",
                        "description": "Number of transcript lines to show",
                        "default": 60,
                    },
                    "instance": {
                        "type": "string",
                        "description": "VM instance name — uses instance disk/NVRAM and machine/ram_mb from manifest",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="harness_resume",
            description="Resume QEMU from a saved snapshot and wait for installer prompt. Much faster than re-booting from PROM — skips POST and miniroot load entirely.",
            inputSchema={
                "type": "object",
                "properties": {
                    "snapshot": {
                        "type": "string",
                        "description": "Snapshot name to restore",
                    },
                    "disk": {
                        "type": "string",
                        "description": "Disk image path (must match snapshot)",
                        "default": str(
                            Path(__file__).parent.parent / "irix_disk.qcow2"
                        ),
                    },
                    "cdrom": {
                        "type": "string",
                        "description": "CD-ROM image path (overrides version)",
                    },
                    "version": {
                        "type": "string",
                        "description": "IRIX version: '6.5', '6.2', or '5.3'",
                        "default": "6.5",
                    },
                    "machine": {
                        "type": "string",
                        "description": "QEMU machine type",
                        "default": "indy",
                    },
                    "ram_mb": {
                        "type": "integer",
                        "description": "RAM in MB",
                        "default": 64,
                    },
                    "max_wait": {
                        "type": "integer",
                        "description": "Max seconds to wait for prompt",
                        "default": 600,
                    },
                    "transcript_tail": {
                        "type": "integer",
                        "description": "Number of transcript lines to show",
                        "default": 60,
                    },
                    "instance": {
                        "type": "string",
                        "description": "VM instance name — uses instance disk/NVRAM and machine/ram_mb from manifest",
                    },
                },
                "required": ["snapshot"],
            },
        ),
        Tool(
            name="harness_disk",
            description="Disk image management: create, convert, list snapshots, create overlay. Wraps qemu-img with SGI/IRIX defaults.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action: 'create', 'convert', 'info', 'snapshots', 'delete_snapshot', 'overlay'",
                    },
                    "path": {"type": "string", "description": "Disk image path"},
                    "size_mb": {
                        "type": "integer",
                        "description": "Size for 'create' action in MB",
                        "default": 2048,
                    },
                    "format": {
                        "type": "string",
                        "description": "Image format: 'qcow2' or 'raw'",
                        "default": "qcow2",
                    },
                    "dst": {
                        "type": "string",
                        "description": "Destination path for 'convert' action",
                    },
                    "backing": {
                        "type": "string",
                        "description": "Backing file for 'overlay' action",
                    },
                    "snapshot_name": {
                        "type": "string",
                        "description": "Snapshot name for 'delete_snapshot'",
                    },
                },
                "required": ["action"],
            },
        ),
        Tool(
            name="harness_install",
            description="Fully automated IRIX installation: partitions disk, creates filesystems, installs packages from all CDs, builds kernel, verifies boot. Supports IRIX 5.3, 6.2, 6.5, 6.5.5. Disc images are auto-discovered from software_library/. Returns structured progress report.",
            inputSchema={
                "type": "object",
                "properties": {
                    "version": {
                        "type": "string",
                        "description": "IRIX version: '5.3', '6.2', '6.5', or '6.5.5'",
                        "default": "6.5",
                    },
                    "disk": {
                        "type": "string",
                        "description": "Path to disk image (created fresh, old one deleted). Defaults to irix_disk.qcow2 in the project root.",
                    },
                    "verify_only": {
                        "type": "boolean",
                        "description": "Skip install, just verify boot from existing disk",
                        "default": False,
                    },
                    "instance": {
                        "type": "string",
                        "description": "VM instance name — creates instance dir, uses its disk/NVRAM paths, records snapshots in manifest",
                    },
                    "ram_mb": {
                        "type": "integer",
                        "description": "RAM size in MB (64 minimum, 256 recommended for large installs)",
                        "default": 64,
                    },
                    "disk_size_mb": {
                        "type": "integer",
                        "description": "Disk image size in MB",
                        "default": 2048,
                    },
                    "conflict_mode": {
                        "type": "string",
                        "enum": ["auto", "collect", "apply"],
                        "description": "Conflict handling: 'auto' (legacy blind resolve), 'collect' (stop at conflicts, save to JSON, save snapshot), 'apply' (use provided resolutions)",
                        "default": "auto",
                    },
                    "conflict_resolutions": {
                        "type": "object",
                        "description": 'Resolution decisions for \'apply\' mode. Format: {"commands": ["conflicts 1a 2a 3a"]} or {"resolutions": [{"package": "dps_eoe.sw.dpsfonts", "action": "do_not_install"}]}',
                    },
                    "install_level": {
                        "type": "string",
                        "enum": ["standard", "default", "all"],
                        "description": "Package selection level: 'standard' (recommended subset), 'default' (everything available), or 'all' (every subsystem via install *)",
                        "default": "standard",
                    },
                    "inst_debug": {
                        "type": "boolean",
                        "description": "Enable inst internal debug logging via Admin menu (set debug true, rules_debug true, etc.). Produces very verbose output — useful for diagnosing conflict/package resolution failures.",
                        "default": False,
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional image categories to discover: os_base, os_overlay, dev_compiler, dev_tools, applications, demos, networking. Default: [os_base, os_overlay]",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="harness_addon",
            description="Install additional packages onto an existing IRIX instance. Boots the installed system, mounts the addon dist image, and uses inst to install all available packages. Supports both single-dist and per-CD layouts. Addon image can be specified explicitly, discovered by category, or found by package name (e.g. package_name='netscape').",
            inputSchema={
                "type": "object",
                "properties": {
                    "instance": {
                        "type": "string",
                        "description": "VM instance name — uses instance disk/NVRAM and machine/ram_mb from manifest",
                    },
                    "base_disk": {
                        "type": "string",
                        "description": "Path to existing IRIX qcow2 disk (alternative to instance)",
                    },
                    "addon_image": {
                        "type": "string",
                        "description": "Path to combined dist image. Optional — can auto-discover via categories or package_name",
                    },
                    "addon_name": {
                        "type": "string",
                        "description": "Human-readable name for the addon (for logging)",
                        "default": "addon",
                    },
                    "snapshot_name": {
                        "type": "string",
                        "description": "Snapshot name to save after install",
                    },
                    "machine": {
                        "type": "string",
                        "description": "QEMU machine type",
                        "default": "indy",
                    },
                    "ram_mb": {
                        "type": "integer",
                        "description": "RAM size in MB for booting",
                        "default": 256,
                    },
                    "addon_dirs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Specific subdirectories to open from per-CD layout (default: all)",
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Image categories to discover: dev_compiler, dev_tools, applications, demos, networking, third_party",
                    },
                    "package_name": {
                        "type": "string",
                        "description": "Package name to search for (e.g. 'netscape', 'MIPSpro'). Searches irix_packages.db to find the disc image containing this package",
                    },
                    "version": {
                        "type": "string",
                        "description": "IRIX version for image discovery filtering",
                        "default": "6.5",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="harness_addon_live",
            description="Install packages on a RUNNING IRIX instance (via serial session or telnet). Does not boot a new QEMU — connects to an existing session. Can discover disc images by package name (e.g. 'netscape') or category.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Running QEMU session ID (for serial method)",
                    },
                    "addon_image": {
                        "type": "string",
                        "description": "Path to disc image. Optional — auto-discovers via categories or package_name",
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Image categories: dev_compiler, dev_tools, applications, demos, networking, third_party",
                    },
                    "package_name": {
                        "type": "string",
                        "description": "Package name to search for (e.g. 'netscape'). Searches irix_packages.db",
                    },
                    "method": {
                        "type": "string",
                        "enum": ["serial", "telnet"],
                        "description": "Connection method",
                        "default": "serial",
                    },
                    "host": {
                        "type": "string",
                        "description": "Telnet hostname",
                        "default": "localhost",
                    },
                    "port": {
                        "type": "integer",
                        "description": "Telnet port",
                        "default": 2323,
                    },
                    "version": {
                        "type": "string",
                        "description": "IRIX version for image discovery",
                        "default": "6.5",
                    },
                },
                "required": [],
            },
        ),
        # ── IRIX Kernel Introspection Tools ──────────────────────────
        Tool(
            name="irix_kernel_symbols",
            description="Extract ELF symbols from an IRIX kernel binary. Reads from guest RAM (after kernel is loaded) or from a raw ELF file on disk. Returns function/variable names with virtual addresses for use by other introspection tools.",
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Symbol source: 'ram' to read from guest RAM at load address, or a file path to an ELF binary",
                        "default": "ram",
                    },
                    "ram_address": {
                        "type": "string",
                        "description": "Physical address to scan for ELF header (hex). Default 0x08000000 (kernel load address)",
                        "default": "0x08000000",
                    },
                    "prom": {
                        "type": "string",
                        "description": "PROM file (needed for ram source)",
                    },
                    "machine": {
                        "type": "string",
                        "description": "QEMU machine type",
                        "default": "indy",
                    },
                    "ram_mb": {
                        "type": "integer",
                        "description": "RAM size in MB",
                        "default": 64,
                    },
                    "boot_wait": {
                        "type": "number",
                        "description": "Seconds to wait for kernel to load before reading RAM",
                        "default": 120,
                    },
                    "scsi_drives": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "SCSI drive paths (needed for ram source to boot kernel)",
                    },
                    "interactions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "expect": {
                                    "type": "string",
                                    "description": "Pattern to wait for",
                                },
                                "send": {
                                    "type": "string",
                                    "description": "Text to send",
                                },
                                "timeout": {
                                    "type": "number",
                                    "description": "Timeout",
                                    "default": 30,
                                },
                            },
                            "required": ["expect", "send"],
                        },
                        "description": "Interactions to get past PROM menu before kernel loads",
                    },
                    "extra_args": {
                        "type": "string",
                        "description": "Additional QEMU arguments",
                    },
                    "filter": {
                        "type": "string",
                        "description": "Regex filter for symbol names",
                    },
                    "save_to": {
                        "type": "string",
                        "description": "Save symbol table to JSON file for reuse",
                    },
                    "max_symbols": {
                        "type": "integer",
                        "description": "Maximum symbols to return (0=all)",
                        "default": 200,
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="irix_pc_sample",
            description="Sample the program counter periodically during IRIX boot to build a histogram of where the CPU spends time. Immediately reveals if the kernel is stuck in a loop, idle, or doing useful work. Maps PCs to kernel function names using symbol table.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sample_interval_ms": {
                        "type": "integer",
                        "description": "Milliseconds between PC samples",
                        "default": 500,
                    },
                    "duration_s": {
                        "type": "integer",
                        "description": "Total sampling duration in seconds",
                        "default": 60,
                    },
                    "symbols_file": {
                        "type": "string",
                        "description": "Path to saved symbol table JSON (from irix_kernel_symbols save_to)",
                    },
                    "prom": {"type": "string", "description": "PROM file path"},
                    "machine": {
                        "type": "string",
                        "description": "QEMU machine type",
                        "default": "indy",
                    },
                    "ram_mb": {
                        "type": "integer",
                        "description": "RAM size in MB",
                        "default": 64,
                    },
                    "boot_wait": {
                        "type": "number",
                        "description": "Seconds to wait before starting sampling (let PROM/kernel boot)",
                        "default": 10,
                    },
                    "scsi_drives": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "SCSI drive paths",
                    },
                    "interactions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "expect": {
                                    "type": "string",
                                    "description": "Pattern to wait for",
                                },
                                "send": {
                                    "type": "string",
                                    "description": "Text to send",
                                },
                                "timeout": {
                                    "type": "number",
                                    "description": "Timeout",
                                    "default": 30,
                                },
                            },
                            "required": ["expect", "send"],
                        },
                        "description": "Interactions to navigate PROM menu before sampling",
                    },
                    "extra_args": {
                        "type": "string",
                        "description": "Additional QEMU arguments (e.g., '-icount shift=0,sleep=off')",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "Show top N functions in histogram",
                        "default": 30,
                    },
                    "autoload": {
                        "type": "boolean",
                        "description": "NVRAM AutoLoad",
                        "default": False,
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="irix_kernel_inspect",
            description="Read IRIX kernel data structures from a running or snapshotted QEMU instance. Inspects klogmsgs (kernel message ring buffer), putbuf (printf buffer), SPB (System Parameter Block), and CPU registers to reveal kernel state invisible on serial output.",
            inputSchema={
                "type": "object",
                "properties": {
                    "inspect": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "What to inspect: 'klogmsgs', 'putbuf', 'spb', 'registers', 'pc'. Default: all",
                    },
                    "symbols_file": {
                        "type": "string",
                        "description": "Path to saved symbol table JSON (from irix_kernel_symbols)",
                    },
                    "klogmsgs_addr": {
                        "type": "string",
                        "description": "Override klogmsgs virtual address (hex). Auto-detected from symbols if available.",
                    },
                    "klog_buf_addr": {
                        "type": "string",
                        "description": "Direct klogmsgs buffer address (hex). Use when fields are separate globals.",
                    },
                    "klog_writeloc_addr": {
                        "type": "string",
                        "description": "klogmsgs writeloc variable address (hex). Use when fields are separate globals.",
                    },
                    "klog_size": {
                        "type": "integer",
                        "description": "klogmsgs buffer size (default 2048 for IRIX 6.5)",
                        "default": 2048,
                    },
                    "putbuf_addr": {
                        "type": "string",
                        "description": "Override putbuf virtual address (hex)",
                    },
                    "putbufndx_addr": {
                        "type": "string",
                        "description": "Override putbufndx virtual address (hex)",
                    },
                    "prom": {"type": "string", "description": "PROM file path"},
                    "machine": {
                        "type": "string",
                        "description": "QEMU machine type",
                        "default": "indy",
                    },
                    "ram_mb": {
                        "type": "integer",
                        "description": "RAM size in MB",
                        "default": 64,
                    },
                    "boot_wait": {
                        "type": "number",
                        "description": "Seconds to wait before inspecting",
                        "default": 120,
                    },
                    "scsi_drives": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "SCSI drive paths",
                    },
                    "interactions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "expect": {
                                    "type": "string",
                                    "description": "Pattern to wait for",
                                },
                                "send": {
                                    "type": "string",
                                    "description": "Text to send",
                                },
                                "timeout": {
                                    "type": "number",
                                    "description": "Timeout",
                                    "default": 30,
                                },
                            },
                            "required": ["expect", "send"],
                        },
                        "description": "Interactions to navigate before inspecting",
                    },
                    "extra_args": {
                        "type": "string",
                        "description": "Additional QEMU arguments",
                    },
                    "autoload": {
                        "type": "boolean",
                        "description": "NVRAM AutoLoad",
                        "default": False,
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="irix_quick_inspect",
            description="Quick all-in-one kernel state dump: boots QEMU, navigates to kernel, then dumps PC (with function name if symbols available), klogmsgs, putbuf, SPB, and key CP0 registers in a single call. Designed as a fast 'what is the kernel doing right now' tool.",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbols_file": {
                        "type": "string",
                        "description": "Path to saved symbol table JSON (from irix_kernel_symbols save_to)",
                    },
                    "wait_for": {
                        "type": "string",
                        "description": "Serial pattern to wait for before inspecting (regex). Default: 'audio:.*responding' (last known kernel message)",
                        "default": "audio:.*responding",
                    },
                    "wait_timeout": {
                        "type": "integer",
                        "description": "Max seconds to wait for wait_for pattern",
                        "default": 300,
                    },
                    "post_wait": {
                        "type": "integer",
                        "description": "Extra seconds to wait after pattern match before inspecting",
                        "default": 5,
                    },
                    "prom": {"type": "string", "description": "PROM file path"},
                    "machine": {
                        "type": "string",
                        "description": "QEMU machine type",
                        "default": "indy",
                    },
                    "ram_mb": {
                        "type": "integer",
                        "description": "RAM size in MB",
                        "default": 64,
                    },
                    "scsi_drives": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "SCSI drive paths (append :cdrom for CD-ROM)",
                    },
                    "extra_args": {
                        "type": "string",
                        "description": "Additional QEMU arguments",
                        "default": "-icount shift=0,sleep=off",
                    },
                    "klogmsgs_addr": {
                        "type": "string",
                        "description": "Override klogmsgs virtual address (hex)",
                    },
                    "klog_buf_addr": {
                        "type": "string",
                        "description": "Direct klogmsgs buffer address (hex). Default: auto-detect via pointer at 0x882DA228",
                    },
                    "klog_writeloc_addr": {
                        "type": "string",
                        "description": "klogmsgs writeloc variable address (hex). Default: 0x882D66C0",
                    },
                    "klog_size": {
                        "type": "integer",
                        "description": "klogmsgs buffer size. Default: 2048",
                    },
                    "putbuf_addr": {
                        "type": "string",
                        "description": "Override putbuf buffer address (hex). Default: 0x882FA438",
                    },
                    "putbufndx_addr": {
                        "type": "string",
                        "description": "Override putbufndx address (hex). Default: 0x882FA434",
                    },
                    "snapshot_name": {
                        "type": "string",
                        "description": "Restore from this snapshot instead of booting fresh (much faster)",
                    },
                },
                "required": [],
            },
        ),
        # Ghidra Integration
        Tool(
            name="ghidra_analyze",
            description="Import PROM into Ghidra, run auto-analysis, import our function names. One-time setup per PROM.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"},
                    "force": {
                        "type": "boolean",
                        "description": "Delete and recreate project",
                        "default": False,
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Timeout in seconds",
                        "default": 300,
                    },
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="ghidra_decompile",
            description="Get C pseudocode for a function. The primary value-add of Ghidra integration.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"},
                    "address": {
                        "type": "string",
                        "description": "Function address (hex) or 'all'",
                    },
                    "max_functions": {
                        "type": "integer",
                        "description": "Max functions when address='all'",
                        "default": 10,
                    },
                },
                "required": ["filename", "address"],
            },
        ),
        Tool(
            name="ghidra_functions",
            description="List all Ghidra-detected functions with sizes, call counts, stack frame info.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"},
                    "filter": {
                        "type": "string",
                        "description": "Optional substring match for function names",
                    },
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="ghidra_xrefs",
            description="Cross-references to/from an address (calls, data refs, reads, writes).",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"},
                    "address": {
                        "type": "string",
                        "description": "Address to find references for (hex)",
                    },
                    "direction": {
                        "type": "string",
                        "description": "Direction: 'to', 'from', or 'both'",
                        "default": "both",
                    },
                },
                "required": ["filename", "address"],
            },
        ),
        Tool(
            name="ghidra_import_symbols",
            description="Re-import our MCP function names and hardware annotations into an existing Ghidra project.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"}
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="ghidra_disassemble",
            description="Ghidra's disassembly at address with its own labels, comments, and function boundaries.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PROM filename"},
                    "address": {
                        "type": "string",
                        "description": "Address to disassemble (hex)",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of instructions",
                        "default": 50,
                    },
                },
                "required": ["filename", "address"],
            },
        ),
        # ── VM Instance Management ──────────────────────────────────
        Tool(
            name="vm_instance_create",
            description="Create a new VM instance directory with disk image and manifest. Organizes disk, NVRAM, and metadata in vm_instances/{name}/.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Instance name (used as directory name)",
                    },
                    "machine": {
                        "type": "string",
                        "description": "QEMU machine type",
                        "default": "indy",
                    },
                    "ram_mb": {
                        "type": "integer",
                        "description": "RAM size in MB",
                        "default": 64,
                    },
                    "irix_version": {
                        "type": "string",
                        "description": "IRIX version installed",
                    },
                    "description": {
                        "type": "string",
                        "description": "Human-readable description",
                    },
                    "disk_size_mb": {
                        "type": "integer",
                        "description": "Disk image size in MB",
                        "default": 2048,
                    },
                    "default_extra_args": {
                        "type": "string",
                        "description": "Default QEMU args injected before caller extra_args on every session (e.g. '-icount shift=0,sleep=off -nic user,model=sgi-hpc3')",
                        "default": "",
                    },
                    "default_snapshot": {
                        "type": "string",
                        "description": "Snapshot restored by default when no snapshot is specified",
                        "default": "",
                    },
                    "hostfwd_port": {
                        "type": "integer",
                        "description": "Host TCP port forwarded to guest port 23 (telnet). Appended to -nic user arg in default_extra_args automatically.",
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="vm_instance_list",
            description="List all VM instances with summary (name, machine, version, snapshots count, disk size).",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="vm_instance_info",
            description="Show full manifest for a VM instance including all snapshots with descriptions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Instance name"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="vm_instance_update",
            description="Update launch defaults and description on an existing VM instance manifest. Use this to set default_extra_args, default_snapshot, and hostfwd_port so sessions launch correctly without explicit args every time.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Instance name to update",
                    },
                    "default_extra_args": {
                        "type": "string",
                        "description": "Default QEMU args injected before caller extra_args on every session (e.g. '-icount shift=0,sleep=off -nic user,model=sgi-hpc3')",
                    },
                    "default_snapshot": {
                        "type": "string",
                        "description": "Snapshot restored by default when no snapshot is specified (empty string clears it)",
                    },
                    "hostfwd_port": {
                        "type": "integer",
                        "description": "Host TCP port forwarded to guest port 23 (telnet). Appended to -nic user arg in default_extra_args automatically.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Human-readable description to set on the instance",
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="vm_instance_delete",
            description="Delete a VM instance directory and all its contents (disk, NVRAM, manifest).",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Instance name to delete",
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="vm_instance_migrate",
            description="Move an existing disk image and optional NVRAM into a new VM instance. Files are moved (not copied).",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Instance name to create",
                    },
                    "disk_path": {
                        "type": "string",
                        "description": "Path to existing disk image",
                    },
                    "nvram_path": {
                        "type": "string",
                        "description": "Path to existing NVRAM file (optional)",
                    },
                    "machine": {
                        "type": "string",
                        "description": "QEMU machine type",
                        "default": "indy",
                    },
                    "ram_mb": {
                        "type": "integer",
                        "description": "RAM size in MB",
                        "default": 64,
                    },
                    "irix_version": {"type": "string", "description": "IRIX version"},
                    "description": {
                        "type": "string",
                        "description": "Human-readable description",
                    },
                    "default_extra_args": {
                        "type": "string",
                        "description": "Default QEMU args injected before caller extra_args on every session (e.g. '-icount shift=0,sleep=off -nic user,model=sgi-hpc3')",
                        "default": "",
                    },
                    "default_snapshot": {
                        "type": "string",
                        "description": "Snapshot restored by default when no snapshot is specified",
                        "default": "",
                    },
                    "hostfwd_port": {
                        "type": "integer",
                        "description": "Host TCP port forwarded to guest port 23 (telnet). Appended to -nic user arg in default_extra_args automatically.",
                    },
                },
                "required": ["name", "disk_path"],
            },
        ),
        Tool(
            name="vm_instance_fork",
            description=(
                "Create a thin-provisioned test copy of an instance. The new instance "
                "shares the source disk as a read-only backing file — changes made in the "
                "forked instance never touch the source. Reset with vm_instance_reset when broken."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Source instance name",
                    },
                    "name": {
                        "type": "string",
                        "description": "New instance name",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional description for the new instance",
                    },
                },
                "required": ["source", "name"],
            },
        ),
        Tool(
            name="vm_instance_reset",
            description=(
                "Reset a forked instance to its original state by discarding its disk and "
                "creating a fresh thin copy from the backing source. All previous state is "
                "discarded. NVRAM is re-copied from the source. The instance must not be running."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Instance name to reset",
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="qemu_copy_file",
            description=(
                "Copy a file between two running QEMU sessions via serial. "
                "Uses uuencode on source and uudecode on dest. Suitable for small-to-medium "
                "files (compiled .o files, config files, etc.)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "src_session": {
                        "type": "string",
                        "description": "Source session ID",
                    },
                    "src_path": {
                        "type": "string",
                        "description": "Path on source guest to read",
                    },
                    "dst_session": {
                        "type": "string",
                        "description": "Destination session ID",
                    },
                    "dst_path": {
                        "type": "string",
                        "description": "Path on destination guest to write",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Timeout per step in seconds",
                        "default": 60,
                    },
                },
                "required": ["src_session", "src_path", "dst_session", "dst_path"],
            },
        ),
        # --- External Library Tools ---
        Tool(
            name="library_scan",
            description="Scan an external software library directory (e.g. NAS/SMB mount) and build a searchable SQLite index. Fast filename-based scan by default; use deep=true to read magic bytes for ambiguous formats.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to external library root (e.g. /Volumes/Library/software/IRIX)",
                    },
                    "deep": {
                        "type": "boolean",
                        "description": "Read magic bytes for format detection (slower over network)",
                        "default": False,
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="library_search",
            description="Search the external software library index for disc images, tardists, or packages. Searches filenames, display names, categories, and notes. Optionally filter by category (os, dev, freeware, nekoware, tgcware, application, graphics, etc.) or format (efs_image, iso9660, tardist, tarball).",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (e.g. 'MIPSpro', 'Cosmo', 'neko_gcc')",
                    },
                    "category": {
                        "type": "string",
                        "description": "Filter by category: os, dev, freeware, nekoware, tgcware, application, networking, graphics, multimedia, demo, patches, misc",
                    },
                    "format": {
                        "type": "string",
                        "description": "Filter by format: efs_image, iso9660, tardist, tarball, raw_image, nekoware_iso",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 30)",
                        "default": 30,
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="library_stage",
            description="Copy a file from the external library to local staging for use with QEMU. Skips copy if already staged with same size.",
            inputSchema={
                "type": "object",
                "properties": {
                    "source_path": {
                        "type": "string",
                        "description": "Absolute path to source file in external library",
                    },
                    "dest": {
                        "type": "string",
                        "description": "Explicit destination path (optional — defaults to staging/<filename>)",
                    },
                },
                "required": ["source_path"],
            },
        ),
        Tool(
            name="library_info",
            description="Show statistics about the external library index: total entries, counts by category and format.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        # --- Filesystem Tools ---
        Tool(
            name="fs_info",
            description="Show volume header, partition table, and filesystem details for an SGI disk image. Supports raw .img and QEMU .qcow2 formats.",
            inputSchema={
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "Path to disk image file",
                    },
                },
                "required": ["image"],
            },
        ),
        Tool(
            name="fs_ls",
            description="List files in an SGI disk image (EFS or XFS). Shows permissions, uid, gid, size, and path. Supports raw .img and QEMU .qcow2 formats.",
            inputSchema={
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "Path to disk image file",
                    },
                    "path": {
                        "type": "string",
                        "description": "Path within filesystem to list (default: /)",
                        "default": "/",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "List recursively (default: true)",
                        "default": True,
                    },
                    "max_entries": {
                        "type": "integer",
                        "description": "Maximum entries to return (default: 500)",
                        "default": 500,
                    },
                    "partition": {
                        "type": "string",
                        "description": "Force partition type: 'efs' or 'xfs' (default: auto-detect)",
                    },
                },
                "required": ["image"],
            },
        ),
        Tool(
            name="fs_cat",
            description="Read a file's contents from an SGI disk image (EFS or XFS). Text files shown directly, binary as hex dump. Supports raw .img and QEMU .qcow2 formats.",
            inputSchema={
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "Path to disk image file",
                    },
                    "path": {
                        "type": "string",
                        "description": "Path to file within filesystem",
                    },
                    "binary": {
                        "type": "boolean",
                        "description": "Force hex dump output (default: false)",
                        "default": False,
                    },
                    "max_size": {
                        "type": "integer",
                        "description": "Maximum file size in bytes (default: 65536)",
                        "default": 65536,
                    },
                    "partition": {
                        "type": "string",
                        "description": "Force partition type: 'efs' or 'xfs' (default: auto-detect)",
                    },
                },
                "required": ["image", "path"],
            },
        ),
        Tool(
            name="fs_extract",
            description="Extract files/directories from an SGI disk image (EFS or XFS) to host filesystem. Supports raw .img and QEMU .qcow2 formats.",
            inputSchema={
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "Path to disk image file",
                    },
                    "dest": {
                        "type": "string",
                        "description": "Destination directory on host",
                    },
                    "path": {
                        "type": "string",
                        "description": "Path within filesystem to extract (default: everything)",
                    },
                    "partition": {
                        "type": "string",
                        "description": "Force partition type: 'efs' or 'xfs' (default: auto-detect)",
                    },
                },
                "required": ["image", "dest"],
            },
        ),
        Tool(
            name="fs_inject",
            description="Add a file from host into an EFS or XFS partition on an SGI disk image. EFS rebuilds the partition; XFS (V1) writes in place via pyirix.xfs — creates or overwrites the guest path. Supports raw .img and QEMU .qcow2 formats (NOTE: writing a qcow2 flattens its backing chain). VM must be shut down first.",
            inputSchema={
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "Path to disk image file",
                    },
                    "host_path": {
                        "type": "string",
                        "description": "Path to file on host to inject",
                    },
                    "guest_path": {
                        "type": "string",
                        "description": "Destination path within filesystem",
                    },
                    "uid": {
                        "type": "integer",
                        "description": "Owner UID (default: 0)",
                        "default": 0,
                    },
                    "gid": {
                        "type": "integer",
                        "description": "Owner GID (default: 0)",
                        "default": 0,
                    },
                    "mode": {
                        "type": "integer",
                        "description": "File permissions in octal (e.g., 0o755)",
                    },
                },
                "required": ["image", "host_path", "guest_path"],
            },
        ),
        # ── XFS Analysis Tools ────────────────────────────────────────
        Tool(
            name="xfs_superblock",
            description="Dump the XFS superblock with full field-by-field annotation and PROM/SASH compatibility analysis. Shows every field at its exact byte offset with raw hex, interpreted value, and validity notes. Essential for diagnosing version mismatches that prevent the IP54 PROM from booting an XFS volume.",
            inputSchema={
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "Path to disk image (.img or .qcow2)",
                    },
                },
                "required": ["image"],
            },
        ),
        Tool(
            name="xfs_inode",
            description="Dump and annotate a single XFS inode: core fields (mode/uid/gid/size), data fork format and content (inline data, extent list, or B+tree root), and directory entries if it is a directory.",
            inputSchema={
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "Path to disk image (.img or .qcow2)",
                    },
                    "inode": {
                        "type": "integer",
                        "description": "XFS inode number to dump",
                    },
                },
                "required": ["image", "inode"],
            },
        ),
        Tool(
            name="xfs_path",
            description="Walk an XFS path component by component with verbose output. Shows each directory lookup: which inode is searched, its format, all entries, and whether the component was found. Pinpoints exactly where path resolution fails.",
            inputSchema={
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "Path to disk image (.img or .qcow2)",
                    },
                    "path": {
                        "type": "string",
                        "description": "Absolute path to walk (e.g. /unix.new, /stand/sash)",
                    },
                },
                "required": ["image", "path"],
            },
        ),
        Tool(
            name="xfs_block",
            description="Dump a raw XFS filesystem block by fsblock address (agno|agbno encoding). Auto-detects block type (AGF/AGI, dir2 data/block, V1 leaf 0xfeeb, BMap B+tree, superblock) and annotates the hex dump.",
            inputSchema={
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "Path to disk image (.img or .qcow2)",
                    },
                    "fsblock": {
                        "type": "integer",
                        "description": "XFS filesystem block address (agno << agblklog | agbno)",
                    },
                },
                "required": ["image", "fsblock"],
            },
        ),
        Tool(
            name="xfs_check",
            description="Run a comprehensive XFS consistency check: volume header, partition table, superblock magic and version, PROM/SASH compatibility, root inode accessibility, root directory, and path probes for /unix, /unix.new, /stand, /sash. Reports PASS/FAIL for each check.",
            inputSchema={
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "Path to disk image (.img or .qcow2)",
                    },
                },
                "required": ["image"],
            },
        ),
        Tool(
            name="xfs_scan",
            description="DEEP, READ-ONLY XFS corruption scan — the gate before reusing a force-killed disk. Unlike xfs_check (which PASSes on poisoned disks), this validates per-AG AGF/AGI headers, geometry, root inode, mkfs-in-progress flag, and internal-log state. It does NOT repair: a force-killed disk's poisoned journal can't be certified by inspection, so the trustworthy fix is to ROLL BACK to a golden (vm_instance_reset / fresh overlay), not repair. On a genuinely clean scan the <disk>.dirty marker is cleared.",
            inputSchema={
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "Path to disk image (.img or .qcow2)",
                    },
                },
                "required": ["image"],
            },
        ),
        Tool(
            name="disk_verify",
            description="Full pre-reuse corruption gate: qcow2 container integrity (qemu-img check) THEN the deep guest XFS scan (xfs_scan: per-AG AGF/AGI, geometry, root, internal-log). Run before reusing any disk that was force-killed or whose provenance you don't trust. A clean result means 'safe to mount', not 'no data lost' — when in doubt, roll back to a golden rather than trust a repair.",
            inputSchema={
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "Path to disk image (.img or .qcow2)",
                    },
                },
                "required": ["image"],
            },
        ),
        Tool(
            name="golden_list",
            description="List the golden image catalog — immutable, checksummed milestone disk snapshots with provenance. Always do work on a fresh overlay of a golden (golden_fork); roll back to a golden instead of repairing a corrupted disk.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="golden_snapshot",
            description="Promote a CLEAN, verified disk into an immutable golden (the milestone-snapshot ritual). Gates: source must not be dirty-marked and must pass qemu-img check; it should already be cleanly shut down (init 0) and verified to boot. Flattens to golden_catalog/<name>.qcow2, sha256s, chmod 444, records provenance. Record how you verified it in `verified`.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "New golden name (must be unique; goldens are immutable)"},
                    "source": {"type": "string", "description": "Path to the clean source disk to promote"},
                    "parent": {"type": "string", "description": "Name of the golden this was derived from (provenance)"},
                    "machine": {"type": "string", "description": "Machine type it was verified on (e.g. virtuix, indy)"},
                    "kernel_md5": {"type": "string", "description": "md5 of the /unix kernel, if relevant"},
                    "verified": {"type": "string", "description": "How this state was verified (e.g. 'boots -smp 4 to 4Dwm desktop')"},
                    "notes": {"type": "string", "description": "Free-form notes"},
                },
                "required": ["name", "source"],
            },
        ),
        Tool(
            name="golden_register",
            description="Register an EXISTING qcow2 (e.g. a prebuilt_disks golden like irix-6.5.5-complete-fixed.qcow2) into the catalog without copying — computes sha256 and locks it read-only (0444).",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Catalog name for this golden"},
                    "file": {"type": "string", "description": "Path to the existing qcow2 (absolute or repo-relative)"},
                    "machine": {"type": "string", "description": "Machine type"},
                    "verified": {"type": "string", "description": "How it was verified"},
                    "parent": {"type": "string", "description": "Parent golden, if any"},
                    "lock": {"type": "boolean", "description": "chmod 0444 the file (default true)", "default": True},
                },
                "required": ["name", "file"],
            },
        ),
        Tool(
            name="golden_fork",
            description="Create a fresh writable overlay backed by a golden — the ONLY safe way to use one. Boot the overlay, never the golden; a crash poisons only the throwaway, and rollback = discard + re-fork.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Golden name (see golden_list)"},
                    "dest": {"type": "string", "description": "Path for the new overlay qcow2 (must not exist)"},
                },
                "required": ["name", "dest"],
            },
        ),
        Tool(
            name="xfs_repair_superblock",
            description="Patch a single XFS superblock field. Supported fields: versionnum (offset 100, uint16), blocksize (offset 4, uint32), agcount (offset 88, uint32). With dry_run=True (default) shows what would change. With dry_run=False converts qcow2 to raw, patches it, and leaves a .patched.raw file alongside — the original qcow2 is never modified.",
            inputSchema={
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "Path to disk image (.img or .qcow2)",
                    },
                    "field": {
                        "type": "string",
                        "description": "Field to patch: 'versionnum', 'blocksize', or 'agcount'",
                    },
                    "value": {
                        "type": "integer",
                        "description": "New value to write (e.g. 0x1094 for versionnum)",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true (default), show what would change without writing",
                        "default": True,
                    },
                },
                "required": ["image", "field", "value"],
            },
        ),
        # ── Live IRIX Kernel Introspection (VMI) ──────────────────────
        Tool(
            name="irix_sysinfo",
            description="Read IRIX system info from a running QEMU session via VMI (no guest tools needed). Shows uname, uptime, current process, logged-in users, and kernel log.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Running QEMU session ID",
                    },
                    "symbols_file": {
                        "type": "string",
                        "description": "Path to saved symbol table JSON (from irix_kernel_symbols)",
                    },
                    "include": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "What to include: 'uname', 'uptime', 'current', 'who', 'klog'. Default: all",
                    },
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="irix_ps",
            description="List IRIX processes from a running QEMU session via VMI. Walks the kernel pidtab array to enumerate all active processes with PID, PPID, state, and command.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Running QEMU session ID",
                    },
                    "symbols_file": {
                        "type": "string",
                        "description": "Path to saved symbol table JSON (from irix_kernel_symbols)",
                    },
                    "max_procs": {
                        "type": "integer",
                        "description": "Maximum processes to list",
                        "default": 100,
                    },
                    "verbose": {
                        "type": "boolean",
                        "description": "Show extra fields (ppid, pgid, state details)",
                        "default": False,
                    },
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="irix_netstat",
            description="Show IRIX network connections from a running QEMU session via VMI. Walks kernel inpcb linked lists for TCP and UDP connections.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Running QEMU session ID",
                    },
                    "symbols_file": {
                        "type": "string",
                        "description": "Path to saved symbol table JSON (from irix_kernel_symbols)",
                    },
                    "proto": {
                        "type": "string",
                        "description": "Protocol filter: 'tcp', 'udp', or 'all'",
                        "default": "all",
                    },
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="irix_crash_analyze",
            description="Symbolize an IRIX panic / FWCB-reboot / qemu-registers dump. Parses EPC/RA/BadVAddr/Cause/Status, decodes the MIPS exception code, walks the eframe-level backtrace (EPC->RA), flags NULL-ish faults and GPRs that point into kernel text, and optionally disassembles around the EPC from a kernel ELF. Supersedes the ad-hoc PROM panic probes (old Layer 2).",
            inputSchema={
                "type": "object",
                "properties": {
                    "dump": {
                        "type": "string",
                        "description": "The panic/register dump text (paste directly).",
                    },
                    "dump_file": {
                        "type": "string",
                        "description": "Path to a file containing the dump (alternative to 'dump').",
                    },
                    "symbols_file": {
                        "type": "string",
                        "description": "Symbol table JSON (list of {name,address}). Default: ip54_kernel_symbols_golden.json",
                        "default": "ip54_kernel_symbols_golden.json",
                    },
                    "kernel_elf": {
                        "type": "string",
                        "description": "Optional kernel ELF for disassembly around the EPC (e.g. a golden /unix copy).",
                    },
                },
            },
        ),
    ]

    return tools


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls.

    Most tool handlers use blocking I/O (subprocess, socket, time.sleep).
    Run them in a thread pool so they don't block the asyncio event loop,
    which would prevent the MCP stdio transport from reading/writing and
    cause "Connection closed" errors on long-running tools.

    Tools that use genuine async (Ghidra bridge) are listed in _ASYNC_TOOLS
    and dispatched directly on the event loop.
    """
    try:
        if name in _ASYNC_TOOLS:
            result = await _handle_tool_async(name, arguments)
        else:
            try:
                import anyio
                result = await anyio.to_thread.run_sync(
                    lambda: _handle_tool(name, arguments),
                    abandon_on_cancel=True,
                )
            except RuntimeError:
                # Fallback if anyio task group isn't active
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, _handle_tool, name, arguments
                )
        return [TextContent(type="text", text=result)]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


# Tools that use genuine async I/O (Ghidra bridge) and must run on the event loop
_ASYNC_TOOLS = frozenset({
    "ghidra_analyze", "ghidra_decompile", "ghidra_functions",
    "ghidra_xrefs", "ghidra_import_symbols", "ghidra_disassemble",
})


async def _handle_tool_async(name: str, args: dict) -> str:
    """Handle tools that require genuine async I/O (Ghidra bridge)."""
    if not ghidra_bridge.GHIDRA_AVAILABLE:
        return "Error: Ghidra not available. Expected analyzeHeadless at: " + str(
            ghidra_bridge.GHIDRA_ANALYZE_HEADLESS
        )
    if name == "ghidra_analyze":
        return await ghidra_bridge.ghidra_analyze(
            args["filename"],
            force=args.get("force", False),
            timeout=args.get("timeout", 300),
        )
    elif name == "ghidra_decompile":
        return await ghidra_bridge.ghidra_decompile(
            args["filename"],
            args["address"],
            max_functions=args.get("max_functions", 10),
        )
    elif name == "ghidra_functions":
        return await ghidra_bridge.ghidra_functions(
            args["filename"],
            filter_str=args.get("filter", ""),
        )
    elif name == "ghidra_xrefs":
        return await ghidra_bridge.ghidra_xrefs(
            args["filename"],
            args["address"],
            direction=args.get("direction", "both"),
        )
    elif name == "ghidra_import_symbols":
        return await ghidra_bridge.ghidra_import_symbols(args["filename"])
    elif name == "ghidra_disassemble":
        return await ghidra_bridge.ghidra_disassemble(
            args["filename"],
            args["address"],
            count=args.get("count", 50),
        )
    return f"Unknown async tool: {name}"


def _handle_tool(name: str, args: dict) -> str:
    """Route tool calls to appropriate handlers."""

    # Basic Analysis
    if name == "list_proms":
        proms = get_prom_summary()
        return format_markdown_table(
            proms,
            columns=["filename", "size", "platform", "part_number", "entry_point"],
            headers={
                "filename": "Filename",
                "size": "Size",
                "platform": "Platform",
                "part_number": "Part Number",
                "entry_point": "Entry Point",
            },
        )

    elif name == "info":
        meta = get_prom_metadata(args["filename"])
        if not meta:
            return f"Error: Could not load {args['filename']}"
        return format_prom_info(to_dict(meta))

    elif name == "hexdump":
        return xxd_prom(
            args["filename"],
            seek=args.get("offset", 0),
            length=args.get("length", 256),
            cols=16,
            groupsize=4,
        )

    elif name == "xxd":
        return xxd_prom(
            args["filename"],
            seek=args.get("seek", 0),
            length=args.get("length", 256),
            cols=args.get("cols", 16),
            groupsize=args.get("groupsize", 4),
            little_endian=args.get("little_endian", False),
            binary=args.get("binary", False),
            c_include=args.get("c_include", False),
            plain=args.get("plain", False),
            uppercase=args.get("uppercase", False),
        )

    elif name == "disassemble":
        if not CAPSTONE_AVAILABLE:
            return "Error: Capstone library not installed. Run: pip install capstone"

        lines = disassemble_prom(
            args["filename"],
            offset=args.get("offset", 0),
            length=args.get("length", 0),
            max_instructions=args.get("max_instructions", 100),
            annotate=args.get("annotate", True),
        )

        if not lines:
            return f"Error: Could not disassemble {args['filename']}"

        if args.get("format") == "markdown":
            return format_disassembly_markdown([to_dict(l) for l in lines])
        else:
            return format_disassembly(lines)

    elif name == "strings":
        data = load_prom(args["filename"])
        if not data:
            return f"Error: Could not load {args['filename']}"

        strings = extract_strings(data, args.get("min_length", 4))
        strings = strings[: args.get("max_strings", 100)]
        return format_string_list(strings)

    # Structure Detection
    elif name == "find_entry_points":
        meta = get_prom_metadata(args["filename"])
        if not meta:
            return f"Error: Could not load {args['filename']}"

        lines = [
            f"**Entry Points for {args['filename']}**",
            "",
            f"- Entry Point: `0x{meta.entry_point:08x}`",
        ]

        if meta.vectors:
            lines.append("")
            lines.append("**Vectors from header:**")
            for name, addr in meta.vectors.items():
                lines.append(f"  - {name}: `0x{addr:08x}`")

        return "\n".join(lines)

    elif name == "find_vector_table":
        data = load_prom(args["filename"])
        if not data:
            return f"Error: Could not load {args['filename']}"

        matches = find_exception_vectors(data)
        return format_pattern_matches(matches)

    elif name == "find_function_prologues":
        data = load_prom(args["filename"])
        if not data:
            return f"Error: Could not load {args['filename']}"

        meta = get_prom_metadata(args["filename"])
        if meta and meta.endian != "big":
            data = normalize_data(data, meta.endian)

        # Use enhanced function prologue detection (supports MIPS32 and MIPS64)
        prologues = find_function_prologues_enhanced(data, PROM_BASE)
        total_count = len(prologues)
        prologues = prologues[: args.get("max_results", 100)]

        if not prologues:
            return "No function prologues found."

        lines = [f"Found {total_count} function prologues:", ""]
        for addr, stack_size in prologues:
            lines.append(f"  0x{addr:08x}: stack frame = {stack_size} bytes")

        if total_count > len(prologues):
            lines.append(f"  ... and {total_count - len(prologues)} more")

        return "\n".join(lines)

    elif name == "find_jump_tables":
        data = load_prom(args["filename"])
        if not data:
            return f"Error: Could not load {args['filename']}"

        matches = find_jump_tables(data)
        return format_pattern_matches(matches)

    # Hardware Patterns
    elif name == "find_hardware_probes":
        data = load_prom(args["filename"])
        if not data:
            return f"Error: Could not load {args['filename']}"

        matches = find_hardware_probes(data)
        return format_pattern_matches(matches)

    elif name == "find_graphics_init":
        data = load_prom(args["filename"])
        if not data:
            return f"Error: Could not load {args['filename']}"

        matches = find_graphics_init(data)
        return format_pattern_matches(matches)

    elif name == "find_memory_detection":
        data = load_prom(args["filename"])
        if not data:
            return f"Error: Could not load {args['filename']}"

        matches = find_memory_detection(data)
        return format_pattern_matches(matches)

    elif name == "find_device_detection":
        data = load_prom(args["filename"])
        if not data:
            return f"Error: Could not load {args['filename']}"

        matches = find_device_detection(data)
        return format_pattern_matches(matches)

    # Comparative Analysis
    elif name == "diff_proms":
        try:
            diffs = diff_binary(
                args["prom1"], args["prom2"], max_diffs=args.get("max_diffs", 50)
            )
            return format_diff(diffs, args["prom1"], args["prom2"])
        except ValueError as e:
            return str(e)

    elif name == "find_common_code":
        prom_files = args.get("prom_files") or None
        common = find_common_code(
            prom_files=prom_files,
            block_size=args.get("block_size", 64),
            min_occurrences=args.get("min_occurrences", 2),
        )
        return format_common_code_summary([to_dict(c) for c in common])

    elif name == "signature_search":
        pattern_hex = args["pattern"].replace(" ", "").replace("0x", "")
        try:
            pattern = bytes.fromhex(pattern_hex)
        except ValueError:
            return f"Error: Invalid hex pattern: {args['pattern']}"

        prom_files = args.get("prom_files") or None
        results = signature_search(pattern, prom_files)

        if not results:
            return f"Pattern '{pattern_hex}' not found in any PROM."

        lines = [f"Pattern '{pattern_hex}' found in {len(results)} file(s):", ""]
        for filename, offsets in results.items():
            lines.append(f"**{filename}:** {len(offsets)} match(es)")
            for off in offsets[:10]:
                addr = PROM_BASE + off
                lines.append(f"  - 0x{addr:08x} (+0x{off:05x})")
            if len(offsets) > 10:
                lines.append(f"  - ... and {len(offsets) - 10} more")
            lines.append("")

        return "\n".join(lines)

    elif name == "version_compare":
        try:
            result = version_compare(args["prom1"], args["prom2"])
            return format_diff_markdown(result)
        except ValueError as e:
            return str(e)

    # Cross-Reference
    elif name == "xref_address":
        data = load_prom(args["filename"])
        if not data:
            return f"Error: Could not load {args['filename']}"

        try:
            addr = int(args["address"], 16)
        except ValueError:
            return f"Error: Invalid address: {args['address']}"

        # Search for the address as a 32-bit value (big-endian)


        pattern = struct.pack(">I", addr)
        results = []

        for i in range(0, len(data) - 3, 4):
            if data[i : i + 4] == pattern:
                results.append(PROM_BASE + i)

        if not results:
            return f"No references to 0x{addr:08x} found."

        lines = [f"References to 0x{addr:08x}:", ""]
        for ref in results[:50]:
            lines.append(f"  - 0x{ref:08x}")

        if len(results) > 50:
            lines.append(f"  - ... and {len(results) - 50} more")

        return "\n".join(lines)

    elif name == "annotate_address":
        try:
            addr = int(args["address"], 16)
        except ValueError:
            return f"Error: Invalid address: {args['address']}"

        annotation = annotate_address(addr)
        if annotation:
            device, reg, desc = annotation
            return f"0x{addr:08x}: {device}.{reg}\n  {desc}"
        else:
            return f"0x{addr:08x}: Unknown address"

    elif name == "list_devices":
        lines = ["# Known Hardware Devices", ""]
        lines.append("| Device | Base Address | Size | Description |")
        lines.append("|--------|--------------|------|-------------|")

        for device_id, device in DEVICES.items():
            lines.append(
                f"| {device_id} | 0x{device.base_address:08x} | "
                f"0x{device.size:x} | {device.description} |"
            )

        return "\n".join(lines)

    elif name == "device_registers":
        device_id = args["device"].upper()
        device = get_device_info(device_id)

        if not device:
            return f"Unknown device: {device_id}. Use list_devices to see available devices."

        lines = [
            f"# {device.name} Registers",
            f"Base: 0x{device.base_address:08x}",
            "",
            "| Offset | Name | Size | Access | Description |",
            "|--------|------|------|--------|-------------|",
        ]

        for reg in device.registers:
            lines.append(
                f"| 0x{reg.offset:04x} | {reg.name} | {reg.size} | "
                f"{reg.access} | {reg.description} |"
            )

        return "\n".join(lines)

    # Advanced Analysis Tools
    elif name == "build_call_graph":
        data = load_prom(args["filename"])
        if not data:
            return f"Error: Could not load {args['filename']}"

        meta = get_prom_metadata(args["filename"])
        if meta and meta.endian != "big":
            data = normalize_data(data, meta.endian)

        call_graph = build_call_graph(data)

        lines = [
            f"# Call Graph for {args['filename']}",
            "",
            f"**Total functions:** {len(call_graph.functions)}",
            f"**Entry points:** {len(call_graph.entry_points)}",
            f"**Orphan functions:** {len(call_graph.orphans)}",
            "",
            "## Entry Points",
            "",
        ]

        for addr in sorted(call_graph.entry_points):
            lines.append(f"- `0x{addr:08x}`")

        if call_graph.orphans:
            lines.append("")
            lines.append("## Orphan Functions (never called)")
            lines.append("")
            for addr in sorted(list(call_graph.orphans)[:20]):
                lines.append(f"- `0x{addr:08x}`")
            if len(call_graph.orphans) > 20:
                lines.append(f"- ... and {len(call_graph.orphans) - 20} more")

        lines.append("")
        lines.append("## Call Relationships (sample)")
        lines.append("")
        lines.append("| Caller | Callees |")
        lines.append("|--------|---------|")
        for caller, callees in list(call_graph.callees.items())[:30]:
            callee_str = ", ".join(f"`0x{c:08x}`" for c in callees[:3])
            if len(callees) > 3:
                callee_str += f" +{len(callees) - 3}"
            lines.append(f"| `0x{caller:08x}` | {callee_str} |")

        return "\n".join(lines)

    elif name == "trace_boot_sequence":
        data = load_prom(args["filename"])
        if not data:
            return f"Error: Could not load {args['filename']}"

        meta = get_prom_metadata(args["filename"])
        if meta and meta.endian != "big":
            data = normalize_data(data, meta.endian)

        start_addr_str = args.get("start_address", "0xbfc003c0")
        try:
            start_addr = int(start_addr_str, 16)
        except ValueError:
            return f"Error: Invalid start address: {start_addr_str}"

        max_steps = args.get("max_steps", 500)
        max_depth = args.get("max_call_depth", 3)

        steps = trace_boot_sequence(data, PROM_BASE, start_addr, max_steps, max_depth)

        # Format output
        hw_steps = [s for s in steps if s.hardware_access is not None]
        call_steps = [s for s in steps if s.is_call]

        lines = [
            f"# Boot Sequence Trace: {args['filename']}",
            "",
            f"**Start address:** `0x{start_addr:08x}`",
            f"**Steps traced:** {len(steps)}",
            f"**Hardware accesses:** {len(hw_steps)}",
            f"**Function calls:** {len(call_steps)}",
            "",
            "## Hardware Access Timeline",
            "",
            "| # | Address | Device | Register | Op | Description |",
            "|---|---------|--------|----------|----|-------------|",
        ]

        for step in hw_steps[:50]:
            ha = step.hardware_access
            op = "R" if ha.operation.value == "read" else "W"
            desc = ha.description[:35] if ha.description else ""
            lines.append(
                f"| {step.order} | `0x{step.code_address:08x}` | "
                f"{ha.device} | {ha.register} | {op} | {desc} |"
            )

        if len(hw_steps) > 50:
            lines.append(f"| ... | | | | | {len(hw_steps) - 50} more |")

        return "\n".join(lines)

    elif name == "find_string_refs":
        data = load_prom(args["filename"])
        if not data:
            return f"Error: Could not load {args['filename']}"

        meta = get_prom_metadata(args["filename"])
        if meta and meta.endian != "big":
            data = normalize_data(data, meta.endian)

        min_len = args.get("min_length", 4)
        strings = extract_strings(data, min_len)
        refs = find_string_references(data, strings)

        if not refs:
            return "No string references found."

        lines = [
            f"# String References in {args['filename']}",
            "",
            f"Found {len(refs)} string references:",
            "",
            "| Code Address | String Address | String (truncated) |",
            "|--------------|----------------|--------------------|",
        ]

        for ref in refs[:50]:
            string_preview = ref.string_value[:40].replace("|", "\\|")
            if len(ref.string_value) > 40:
                string_preview += "..."
            lines.append(
                f"| `0x{ref.code_address:08x}` | `0x{ref.string_address:08x}` | {string_preview} |"
            )

        if len(refs) > 50:
            lines.append(f"| ... | ... | {len(refs) - 50} more |")

        return "\n".join(lines)

    elif name == "identify_arcs_callbacks":
        data = load_prom(args["filename"])
        if not data:
            return f"Error: Could not load {args['filename']}"

        meta = get_prom_metadata(args["filename"])
        if meta and meta.endian != "big":
            data = normalize_data(data, meta.endian)

        table_addr_str = args.get("table_address", "0")
        try:
            table_addr = int(table_addr_str, 16)
        except ValueError:
            table_addr = 0

        callbacks = identify_arcs_callbacks(data, PROM_BASE, table_addr)

        if not callbacks:
            return "No ARCS callback table found."

        lines = [
            f"# ARCS Callbacks in {args['filename']}",
            "",
            f"Found {len(callbacks)} callbacks:",
            "",
            "| Index | Name | Address |",
            "|-------|------|---------|",
        ]

        for idx, name_cb, addr in callbacks:
            lines.append(f"| {idx} | {name_cb} | `0x{addr:08x}` |")

        return "\n".join(lines)

    elif name == "analyze_function":
        data = load_prom(args["filename"])
        if not data:
            return f"Error: Could not load {args['filename']}"

        meta = get_prom_metadata(args["filename"])
        if meta and meta.endian != "big":
            data = normalize_data(data, meta.endian)

        try:
            func_addr = int(args["address"], 16)
        except ValueError:
            return f"Error: Invalid address: {args['address']}"

        strings = extract_strings(data, 4)
        func = analyze_function(data, func_addr, PROM_BASE, strings)

        lines = [
            f"# Function Analysis: 0x{func_addr:08x}",
            "",
            f"**Suggested name:** `{func.suggested_name()}`",
            f"**Start address:** `0x{func.address:08x}`",
            f"**End address:** `0x{func.end_address:08x}`"
            if func.end_address
            else "**End address:** Unknown",
            f"**Stack frame:** {func.stack_size} bytes",
            f"**Is leaf:** {func.is_leaf}",
            f"**Returns:** {func.returns}",
            "",
        ]

        if func.callees:
            lines.append("## Function Calls")
            lines.append("")
            for callee in func.callees[:20]:
                lines.append(f"- `0x{callee:08x}`")
            if len(func.callees) > 20:
                lines.append(f"- ... and {len(func.callees) - 20} more")
            lines.append("")

        if func.hardware_accesses:
            lines.append("## Hardware Accesses")
            lines.append("")
            lines.append("| Address | Device | Register | Op |")
            lines.append("|---------|--------|----------|----|")
            for ha in func.hardware_accesses[:20]:
                op = "R" if ha.operation.value == "read" else "W"
                lines.append(
                    f"| `0x{ha.code_address:08x}` | {ha.device} | {ha.register} | {op} |"
                )
            if len(func.hardware_accesses) > 20:
                lines.append(f"| ... | | | {len(func.hardware_accesses) - 20} more |")
            lines.append("")

        if func.string_refs:
            lines.append("## String References")
            lines.append("")
            for sr in func.string_refs[:10]:
                string_preview = sr.string_value[:50].replace("\n", "\\n")
                lines.append(f'- `0x{sr.string_address:08x}`: "{string_preview}"')
            if len(func.string_refs) > 10:
                lines.append(f"- ... and {len(func.string_refs) - 10} more")

        return "\n".join(lines)

    elif name == "build_function_database":
        data = load_prom(args["filename"])
        if not data:
            return f"Error: Could not load {args['filename']}"

        meta = get_prom_metadata(args["filename"])
        if meta and meta.endian != "big":
            data = normalize_data(data, meta.endian)

        db = build_function_database(data, PROM_BASE, args["filename"])

        lines = [
            f"# Function Database: {args['filename']}",
            "",
        ]

        # Show classification if available
        if db.classification:
            lines.append(f"**PROM Type:** {db.classification.prom_type}")
            lines.append(f"**Architecture:** {db.classification.arch or 'N/A'}")
            lines.append(f"**Description:** {db.classification.description}")
            if not db.classification.executable:
                lines.append("")
                lines.append(
                    "⚠️ **Non-executable PROM** - No function analysis available."
                )
                lines.append(
                    f"**Suggested tools:** {', '.join(db.classification.suggested_tools)}"
                )
                lines.append(f"**Strings found:** {len(db.strings)}")
                return "\n".join(lines)
            lines.append("")

        lines.extend(
            [
                f"**Total functions:** {len(db.functions)}",
                f"**Entry points:** {len(db.call_graph.entry_points)}",
                f"**Orphan functions:** {len(db.call_graph.orphans)}",
                f"**Strings:** {len(db.strings)}",
                "",
                "## Functions (sample)",
                "",
                "| Address | Name | Stack | Callees | HW Accesses |",
                "|---------|------|-------|---------|-------------|",
            ]
        )

        for addr, func in list(sorted(db.functions.items()))[:30]:
            name_fn = func.name if func.name else func.suggested_name()
            lines.append(
                f"| `0x{addr:08x}` | {name_fn} | {func.stack_size} | "
                f"{len(func.callees)} | {len(func.hardware_accesses)} |"
            )

        if len(db.functions) > 30:
            lines.append(f"| ... | | | | {len(db.functions) - 30} more |")

        return "\n".join(lines)

    elif name == "export_symbols":
        data = load_prom(args["filename"])
        if not data:
            return f"Error: Could not load {args['filename']}"

        meta = get_prom_metadata(args["filename"])
        if meta and meta.endian != "big":
            data = normalize_data(data, meta.endian)

        db = build_function_database(data, PROM_BASE, args["filename"])

        export_format = args.get("format", "json").lower()
        output_path = args.get("output_path")

        if export_format == "ghidra":
            if output_path:
                export_ghidra_symbols(db, output_path)
                return f"Exported Ghidra symbols to {output_path}"
            else:
                # Return content directly
                lines = []
                for addr, func in sorted(db.functions.items()):
                    name_fn = func.name if func.name else func.suggested_name()
                    lines.append(f"{addr:08x} {name_fn}")
                return "\n".join(lines)

        elif export_format == "ida":
            if output_path:
                export_ida_idc(db, output_path)
                return f"Exported IDA IDC script to {output_path}"
            else:
                # Return sample content
                lines = [
                    "// IDA IDC script",
                    "#include <idc.idc>",
                    "static main() {",
                ]
                for addr, func in list(sorted(db.functions.items()))[:20]:
                    name_fn = func.name if func.name else func.suggested_name()
                    lines.append(f'    MakeName(0x{addr:08x}, "{name_fn}");')
                lines.append("    // ... more functions")
                lines.append("}")
                return "\n".join(lines)

        elif export_format == "dot":
            from .export import format_call_graph_dot

            content = format_call_graph_dot(db)
            if output_path:
                from pathlib import Path

                Path(output_path).write_text(content)
                return f"Exported DOT call graph to {output_path}"
            return content

        else:  # json
            if output_path:
                export_function_json(db, output_path)
                return f"Exported JSON database to {output_path}"
            else:
                return format_json(db.to_dict())

    elif name == "track_hardware_accesses":
        data = load_prom(args["filename"])
        if not data:
            return f"Error: Could not load {args['filename']}"

        meta = get_prom_metadata(args["filename"])
        if meta and meta.endian != "big":
            data = normalize_data(data, meta.endian)

        accesses = track_hardware_accesses(data)
        max_results = args.get("max_results", 100)

        lines = [
            f"# Hardware Accesses in {args['filename']}",
            "",
            f"Found {len(accesses)} hardware register accesses:",
            "",
            "| Code Address | Device | Register | Full Address | Op | Description |",
            "|--------------|--------|----------|--------------|-------|-------------|",
        ]

        for ha in accesses[:max_results]:
            op = "R" if ha.operation.value == "read" else "W"
            desc = ha.description[:30] if ha.description else ""
            lines.append(
                f"| `0x{ha.code_address:08x}` | {ha.device} | {ha.register} | "
                f"`0x{ha.full_address:08x}` | {op} | {desc} |"
            )

        if len(accesses) > max_results:
            lines.append(f"| ... | | | | | {len(accesses) - max_results} more |")

        return "\n".join(lines)

    # QEMU Debugging Tools
    elif name == "parse_qemu_log":
        log_content = args.get("log_content", "")

        if not log_content and args.get("log_file"):
            # Read from file
            try:
                from pathlib import Path

                log_path = Path(args["log_file"])
                if not log_path.exists():
                    return f"Error: Log file not found: {args['log_file']}"
                log_content = log_path.read_text()
            except Exception as e:
                return f"Error reading log file: {e}"

        if not log_content:
            return "Error: Either log_file or log_content is required"

        max_entries = args.get("max_entries", 200)
        summary = parse_qemu_log(log_content, max_entries)
        return format_qemu_log_summary(summary, max_entries)

    elif name == "generate_expected_sequence":
        data = load_prom(args["filename"])
        if not data:
            return f"Error: Could not load {args['filename']}"

        meta = get_prom_metadata(args["filename"])
        if meta and meta.endian != "big":
            data = normalize_data(data, meta.endian)

        start_addr_str = args.get("start_address", "0xbfc003c0")
        try:
            start_addr = int(start_addr_str, 16)
        except ValueError:
            return f"Error: Invalid start address: {start_addr_str}"

        max_steps = args.get("max_steps", 500)
        include_values = args.get("include_values", True)

        sequence = generate_expected_sequence(
            data, PROM_BASE, start_addr, max_steps, 5, include_values
        )

        return format_expected_sequence(sequence)

    elif name == "analyze_register_values":
        data = load_prom(args["filename"])
        if not data:
            return f"Error: Could not load {args['filename']}"

        meta = get_prom_metadata(args["filename"])
        if meta and meta.endian != "big":
            data = normalize_data(data, meta.endian)

        max_results = args.get("max_results", 100)
        device_filter = args.get("device_filter")

        analysis = analyze_register_values(data, PROM_BASE, max_results, device_filter)
        return format_register_value_analysis(analysis)

    elif name == "compare_execution":
        # Load PROM data
        data = load_prom(args["filename"])
        if not data:
            return f"Error: Could not load {args['filename']}"

        meta = get_prom_metadata(args["filename"])
        if meta and meta.endian != "big":
            data = normalize_data(data, meta.endian)

        # Get QEMU log
        log_content = args.get("log_content", "")
        log_file = args.get("log_file", "")

        if not log_content and log_file:
            try:
                from pathlib import Path

                log_path = Path(log_file)
                if not log_path.exists():
                    return f"Error: Log file not found: {log_file}"
                log_content = log_path.read_text()
            except Exception as e:
                return f"Error reading log file: {e}"

        if not log_content:
            return "Error: Either log_file or log_content is required"

        # Parse QEMU log
        qemu_summary = parse_qemu_log(log_content, 1000)

        # Generate expected sequence
        expected = generate_expected_sequence(data, PROM_BASE, 0xBFC003C0, 500, 5, True)

        # Compare
        strict_order = args.get("strict_order", False)
        max_divergences = args.get("max_divergences", 50)

        comparison = compare_execution(
            expected, qemu_summary, strict_order, max_divergences
        )
        comparison.prom_name = args["filename"]
        comparison.log_file = log_file or "(inline content)"

        return format_execution_comparison(comparison)

    # QEMU Build Tools
    elif name == "qemu_configure":

        from pathlib import Path

        # Find project root (where .mcp.json is)
        project_root = Path(__file__).parent.parent
        qemu_dir = project_root / "qemu"
        # Always create the platform-specific build dir for configure
        build_dir = qemu_dir / _platform_build_subdir()

        if not qemu_dir.exists():
            return f"Error: QEMU directory not found at {qemu_dir}"

        # Clean build directory if requested
        if args.get("clean", False):


            if build_dir.exists():
                shutil.rmtree(build_dir)

        # Create build directory
        build_dir.mkdir(exist_ok=True)

        # Build configure command
        target_list = args.get("target_list", "mips64-softmmu")
        cmd = [
            "../configure",
            f"--target-list={target_list}",
            "--disable-fuse",
            "--disable-fuse-lseek",
        ]

        extra_args = args.get("extra_args", "")
        if extra_args:
            cmd.extend(extra_args.split())

        try:
            result = subprocess.run(
                cmd, cwd=build_dir, capture_output=True, text=True, timeout=120
            )

            output_lines = []
            output_lines.append(f"**Command:** `{' '.join(cmd)}`")
            output_lines.append(f"**Working directory:** `{build_dir}`")
            output_lines.append(f"**Exit code:** {result.returncode}")
            output_lines.append("")

            if result.stdout:
                # Show last 30 lines of stdout
                stdout_lines = result.stdout.strip().split("\n")
                if len(stdout_lines) > 30:
                    output_lines.append(f"... ({len(stdout_lines) - 30} lines omitted)")
                output_lines.extend(stdout_lines[-30:])

            if result.stderr:
                output_lines.append("")
                output_lines.append("**Stderr:**")
                output_lines.extend(result.stderr.strip().split("\n")[-20:])

            return "\n".join(output_lines)

        except subprocess.TimeoutExpired:
            return "Error: Configure timed out after 120 seconds"
        except Exception as e:
            return f"Error running configure: {e}"

    elif name == "qemu_build":

        from pathlib import Path

        build_dir = _find_build_dir()

        if not build_dir.exists():
            return f"Error: Build directory not found at {build_dir}. Run qemu_configure first."

        jobs = args.get("jobs", 4)
        cmd = ["ninja", f"-j{jobs}"]

        target = args.get("target")
        if target:
            cmd.append(target)

        try:
            result = subprocess.run(
                cmd,
                cwd=build_dir,
                capture_output=True,
                text=True,
                timeout=600,  # 10 minute timeout for builds
            )

            output_lines = []
            output_lines.append(f"**Command:** `{' '.join(cmd)}`")
            output_lines.append(f"**Exit code:** {result.returncode}")
            output_lines.append("")

            if result.stdout:
                stdout_lines = result.stdout.strip().split("\n")
                # For builds, show last 50 lines
                if len(stdout_lines) > 50:
                    output_lines.append(f"... ({len(stdout_lines) - 50} lines omitted)")
                output_lines.extend(stdout_lines[-50:])

            if result.stderr:
                output_lines.append("")
                output_lines.append("**Stderr:**")
                stderr_lines = result.stderr.strip().split("\n")
                output_lines.extend(stderr_lines[-30:])

            if result.returncode == 0:
                output_lines.append("")
                output_lines.append("**Build successful!**")

            return "\n".join(output_lines)

        except subprocess.TimeoutExpired:
            return "Error: Build timed out after 600 seconds"
        except Exception as e:
            return f"Error running ninja: {e}"

    elif name == "qemu_create_disk":

        from pathlib import Path

        project_root = Path(__file__).parent.parent
        build_dir = _find_build_dir()

        qemu_img = build_dir / "qemu-img"
        if not qemu_img.exists():
            return f"Error: qemu-img not found in {build_dir}. Run qemu_build first."

        disk_path = Path(args["path"])
        size = args.get("size", "100M")
        fmt = args.get("format", "raw")

        # Create parent directory if needed
        disk_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [str(qemu_img), "create", "-f", fmt, str(disk_path), size]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            output_lines = [f"**Command:** `{' '.join(cmd)}`"]
            output_lines.append(f"**Exit code:** {result.returncode}")

            if result.stdout:
                output_lines.append("")
                output_lines.append(result.stdout.strip())

            if result.stderr:
                output_lines.append("")
                output_lines.append(f"**Stderr:** {result.stderr.strip()}")

            if result.returncode == 0:
                output_lines.append("")
                output_lines.append(f"**Disk created:** `{disk_path}`")

            return "\n".join(output_lines)

        except subprocess.TimeoutExpired:
            return "Error: qemu-img timed out"
        except Exception as e:
            return f"Error: {e}"

    elif name == "qemu_run_sgi":


        args = _resolve_instance(args)
        prom_path, build_dir, project_root, err = _resolve_prom(args)
        if err:
            return err

        machine = args.get("machine", "indy")
        ram_mb = args.get("ram_mb", 64)
        timeout = args.get("timeout", 5)
        debug_flags = args.get("debug_flags", "unimp")
        vnc_enabled = args.get("vnc", False)
        vnc_port = args.get("vnc_port", 0)
        scsi_drives = args.get("scsi_drives", [])

        qemu_bin = _find_qemu_binary(build_dir)
        cmd = [
            str(qemu_bin),
            "-M",
            machine,
            "-bios",
            str(prom_path),
            "-m",
            f"{ram_mb}M",
            "-L",
            str(build_dir / "pc-bios"),
        ]

        # For IP30 (octane), use socket chardev for serial output
        # For other machines, use stdio with VNC or nographic
        if machine == "octane":
            # For IP30, the serial MMIO device is created by sgi_bridge.c
            # Use socket chardev for serial I/O to enable PROM serial access
            tmpdir = tempfile.mkdtemp(prefix="qemu_run_sgi_")
            serial_sock_path = os.path.join(tmpdir, "serial.sock")

            cmd.extend(
                [
                    "-serial",
                    "chardev:ser0",
                    "-chardev",
                    f"socket,id=ser0,path={serial_sock_path},server=on,wait=off",
                ]
            )
        elif vnc_enabled:
            cmd.extend(
                [
                    "-object",
                    "secret,id=vnc-pw,data=sgi",
                    "-display",
                    f"vnc=0.0.0.0:{vnc_port},to=99,password-secret=vnc-pw",
                    "-serial",
                    "stdio",
                ]
            )
        else:
            cmd.append("-nographic")

        if debug_flags:
            cmd.extend(["-d", debug_flags])

        drive_cmd_args, _, drive_err = _resolve_scsi_drives(
            args, build_dir, project_root
        )
        if drive_err:
            return drive_err
        cmd.extend(drive_cmd_args)

        if not args.get("autoload", True):
            if machine not in ("sgi-o2",):
                cmd.extend(["-global", "sgi-hpc3.autoload=false"])

        extra_args = args.get("extra_args", "")
        if extra_args:
            cmd.extend(extra_args.split())

        try:
            # Use Popen to capture output even on timeout
            proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )

            try:
                stdout_b, stderr_b = proc.communicate(timeout=timeout)
                timed_out = False
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout_b, stderr_b = proc.communicate()
                timed_out = True
            stdout = stdout_b.decode("latin-1", errors="replace")
            stderr = stderr_b.decode("latin-1", errors="replace")

            output_lines = []
            output_lines.append(f"**PROM:** `{prom_path.name}`")
            output_lines.append(f"**RAM:** {ram_mb}MB")
            if vnc_enabled:
                # Parse actual VNC port from stderr (QEMU prints "VNC server running on ...")
                vnc_actual_port = 5900 + vnc_port
                if stderr:
                    import re as _re

                    vnc_match = _re.search(r"VNC server running on [^:]+:(\d+)", stderr)
                    if vnc_match:
                        vnc_actual_port = int(vnc_match.group(1))
                output_lines.append(
                    f"**VNC:** Connect to port **{vnc_actual_port}** with any VNC client (password: sgi)"
                )
            if scsi_drives:
                output_lines.append(f"**SCSI Drives:** {len(scsi_drives)}")
                disk_id = 1
                cdrom_id = 4
                for d in scsi_drives:
                    if d.endswith(":cdrom"):
                        output_lines.append(
                            f"  - Target {cdrom_id}: `{d[:-6]}` (CD-ROM)"
                        )
                        cdrom_id += 1
                    else:
                        output_lines.append(f"  - Target {disk_id}: `{d}`")
                        disk_id += 1
            if timed_out:
                output_lines.append(
                    f"**Status:** Timed out after {timeout}s (expected)"
                )
            else:
                output_lines.append(f"**Exit code:** {proc.returncode}")
            output_lines.append("")

            # Optional: save full log to file
            save_log = args.get("save_log")
            if save_log and stderr:
                from pathlib import Path

                Path(save_log).write_text(stderr)
                output_lines.append(f"**Full log saved to:** `{save_log}`")
                output_lines.append("")

            # Optional: filter by pattern
            grep_filter = args.get("grep_filter")

            if stdout:
                output_lines.append("**Serial output:**")
                output_lines.append("```")
                stdout_lines = stdout.strip().split("\n")
                output_lines.extend(stdout_lines[-100:])
                output_lines.append("```")

            if stderr:
                stderr_lines = stderr.strip().split("\n")

                if grep_filter:


                    pattern = re.compile(grep_filter, re.IGNORECASE)
                    filtered = [l for l in stderr_lines if pattern.search(l)]
                    output_lines.append("")
                    output_lines.append(f"**Debug log (filtered by `{grep_filter}`):**")
                    output_lines.append("```")
                    if len(filtered) > 300:
                        output_lines.extend(filtered[:150])
                        output_lines.append(
                            f"... ({len(filtered) - 300} lines omitted)"
                        )
                        output_lines.extend(filtered[-150:])
                    else:
                        output_lines.extend(filtered)
                    output_lines.append("```")
                else:
                    output_lines.append("")
                    output_lines.append("**Debug log (stderr):**")
                    output_lines.append("```")
                    # Show first 100 lines and last 100 lines
                    if len(stderr_lines) > 200:
                        output_lines.extend(stderr_lines[:100])
                        output_lines.append(
                            f"... ({len(stderr_lines) - 200} lines omitted)"
                        )
                        output_lines.extend(stderr_lines[-100:])
                    else:
                        output_lines.extend(stderr_lines)
                    output_lines.append("```")

            return "\n".join(output_lines)

        except Exception as e:
            return f"Error running QEMU: {e}"
        finally:
            # Clean up tmpdir for IP30 (if created)
            if machine == "octane" and "tmpdir" in locals():
                shutil.rmtree(tmpdir, ignore_errors=True)

    elif name in (
        "qemu_monitor",
        "qemu_registers",
        "qemu_guest_disasm",
        "qemu_guest_memory",
    ):


        prom_path, build_dir, project_root, err = _resolve_prom(args)
        if err:
            return err

        machine = args.get("machine", "indy")
        ram_mb = args.get("ram_mb", 64)

        # Build monitor command based on tool name
        if name == "qemu_registers":
            monitor_cmd = "info registers"
            boot_wait = args.get("boot_wait", 3)
            timeout = boot_wait + 3
        elif name == "qemu_guest_disasm":
            addr = args.get("address", "0xbfc00000")
            count = args.get("count", 20)
            monitor_cmd = f"x/{count}i {addr}"
            boot_wait = args.get("boot_wait", 2)
            timeout = boot_wait + 3
        elif name == "qemu_guest_memory":
            addr = args.get("address", "0x1fb80000")
            count = args.get("count", 16)
            monitor_cmd = f"xp/{count}wx {addr}"
            boot_wait = args.get("boot_wait", 2)
            timeout = boot_wait + 3
        else:  # qemu_monitor
            monitor_cmd = args["command"]
            boot_wait = args.get("boot_wait", 2)
            timeout = args.get("timeout", 5)

        scsi_drives = args.get("scsi_drives", [])
        interactions = args.get("interactions", [])
        # For octane (IP30), the serial is created internally by sgi_bridge
        # For other machines, serial is only needed if scsi_drives or interactions
        has_serial = machine == "octane" or bool(scsi_drives) or bool(interactions)

        qemu_bin = _find_qemu_binary(build_dir)
        tmpdir = tempfile.mkdtemp(prefix="qemu_mon_")
        monitor_sock_path = os.path.join(tmpdir, "monitor.sock")
        serial_sock_path = os.path.join(tmpdir, "serial.sock") if has_serial else None

        cmd = [
            str(qemu_bin),
            "-M",
            machine,
            "-bios",
            str(prom_path),
            "-m",
            f"{ram_mb}M",
            "-L",
            str(build_dir / "pc-bios"),
            "-display",
            f"vnc=0.0.0.0:{vnc_port},to=99,password-secret=vnc-pw"
            if vnc_enabled
            else "none",
            "-serial",
            "none" if machine == "octane" else "chardev:ser0",
            "-monitor",
            f"unix:{monitor_sock_path},server,nowait",
        ]
        # For machines without internal serial (non-octane), add chardev
        if machine != "octane":
            cmd.extend(
                [
                    "-chardev",
                    f"socket,id=ser0,path={serial_sock_path},server=on,wait=off",
                ]
            )
        else:
            # For octane, the serial is created internally by sgi_bridge
            # The serial_sock_path is still needed for MCP session connection

            if has_serial:
                cmd.extend(
                    [
                        "-display",
                        "none",
                        "-chardev",
                        f"socket,id=ser0,path={serial_sock_path},server=on,wait=off",
                        "-serial",
                        "chardev:ser0",
                    ]
                )
                if not args.get("autoload", True):
                    if machine not in ("sgi-o2",):
                        cmd.extend(["-global", "sgi-hpc3.autoload=false"])
            else:
                cmd.extend(["-nographic", "-serial", "none"])

            drive_cmd_args, _, drive_err = _resolve_scsi_drives(
                args, build_dir, project_root
            )
            if drive_err:
                shutil.rmtree(tmpdir, ignore_errors=True)
                return drive_err
            cmd.extend(drive_cmd_args)

            extra_args = args.get("extra_args", "")
            if extra_args:
                cmd.extend(extra_args.split())

            proc = None
            serial_sock = None
            try:
                proc, _stderr_log = _popen_qemu(cmd, tmpdir)

                if has_serial:
                    serial_sock, connect_err = _connect_serial_retry(
                        serial_sock_path, proc, stderr_log_path=_stderr_log, cmd=cmd
                    )
                    if connect_err:
                        return connect_err

                    boot_data = _collect_serial_output(serial_sock, min(boot_wait, 45))

                    # Execute interactions
                    pending_data = boot_data
                    for interaction in interactions:
                        expect_pattern = interaction.get("expect", "")
                        send_text = interaction.get("send", "")
                        expect_timeout = interaction.get("timeout", 30)

                        if expect_pattern:
                            output, matched = _expect_serial(
                                serial_sock,
                                expect_pattern,
                                expect_timeout,
                                initial_data=pending_data,
                            )
                            pending_data = b""
                            if not matched:
                                return f"Error: expect pattern '{expect_pattern}' not matched"
                        else:
                            pending_data = b""

                        if send_text:
                            send_bytes = (
                                send_text.encode("latin-1")
                                .decode("unicode_escape")
                                .encode("latin-1")
                            )
                            serial_sock.sendall(send_bytes)

                    time.sleep(boot_wait)
                else:
                    time.sleep(boot_wait)

                # Connect to monitor socket and send command
                monitor_output = ""
                try:
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    sock.settimeout(3)
                    sock.connect(monitor_sock_path)

                    try:
                        sock.recv(4096)  # banner
                    except socket.timeout:
                        pass

                    sock.sendall(f"{monitor_cmd}\n".encode())
                    time.sleep(0.5)

                    chunks = []
                    try:
                        while True:
                            data = sock.recv(65536)
                            if not data:
                                break
                            chunks.append(data.decode("utf-8", errors="replace"))
                    except socket.timeout:
                        pass
                    monitor_output = "".join(chunks)

                    try:
                        sock.sendall(b"quit\n")
                    except Exception:
                        pass
                    sock.close()
                except Exception as e:
                    monitor_output = f"(monitor connection error: {e})"

                # Wait for QEMU to exit gracefully (flush qcow2 metadata)
                try:
                    proc.wait(timeout=10)
                    proc = None  # Exited cleanly, skip kill
                except subprocess.TimeoutExpired:
                    pass
                if proc:
                    proc.kill()
                    proc.wait(timeout=3)

                output_lines = []
                output_lines.append(f"**Monitor command:** `{monitor_cmd}`")
                output_lines.append(f"**Boot wait:** {boot_wait}s")
                if scsi_drives:
                    output_lines.append(f"**SCSI Drives:** {len(scsi_drives)}")
                if interactions:
                    output_lines.append(
                        f"**Interactions:** {len(interactions)} completed"
                    )
                output_lines.append("")

                if monitor_output:
                    raw_lines = monitor_output.strip().split("\n")
                    filtered = [
                        l
                        for l in raw_lines
                        if not l.startswith("QEMU") and "(qemu)" not in l and l.strip()
                    ]
                    output_lines.append("```")
                    output_lines.extend(filtered)
                    output_lines.append("```")
                else:
                    output_lines.append("(no output)")

                return "\n".join(output_lines)

            except Exception as e:
                return f"Error: {e}"
            finally:
                _cleanup_qemu(proc, serial_sock, monitor_sock_path, tmpdir)

        # Log analysis tools
    elif name == "log_grep":

        from pathlib import Path

        file_path = Path(args["file"])
        if not file_path.exists():
            return f"Error: File not found: {file_path}"

        pattern = re.compile(args["pattern"], re.IGNORECASE)
        invert = args.get("invert", False)
        max_lines = args.get("max_lines", 200)

        matches = []
        try:
            with open(file_path, "r", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    line = line.rstrip("\n")
                    match = pattern.search(line)
                    if (match and not invert) or (not match and invert):
                        matches.append(f"{i}: {line}")
                        if len(matches) >= max_lines:
                            break
        except Exception as e:
            return f"Error reading file: {e}"

        output = [f"**File:** `{file_path}`"]
        output.append(
            f"**Pattern:** `{args['pattern']}`" + (" (inverted)" if invert else "")
        )
        output.append(
            f"**Matches:** {len(matches)}"
            + (f" (limited to {max_lines})" if len(matches) >= max_lines else "")
        )
        output.append("")
        output.append("```")
        output.extend(matches)
        output.append("```")
        return "\n".join(output)

    elif name == "log_context":

        from pathlib import Path

        file_path = Path(args["file"])
        if not file_path.exists():
            return f"Error: File not found: {file_path}"

        pattern = re.compile(args["pattern"], re.IGNORECASE)
        before = args.get("before", 5)
        after = args.get("after", 5)
        occurrence = args.get("occurrence", 1)

        try:
            with open(file_path, "r", errors="replace") as f:
                lines = f.readlines()
        except Exception as e:
            return f"Error reading file: {e}"

        # Find all matches
        match_indices = []
        for i, line in enumerate(lines):
            if pattern.search(line):
                match_indices.append(i)

        if not match_indices:
            return f"No matches found for pattern: {args['pattern']}"

        # Select the right occurrence
        if occurrence > 0:
            idx = occurrence - 1
        else:
            idx = occurrence  # -1 = last, -2 = second to last, etc.

        if abs(idx) >= len(match_indices) or idx >= len(match_indices):
            return (
                f"Occurrence {occurrence} not found (only {len(match_indices)} matches)"
            )

        match_line = match_indices[idx]
        start = max(0, match_line - before)
        end = min(len(lines), match_line + after + 1)

        output = [f"**File:** `{file_path}`"]
        output.append(f"**Pattern:** `{args['pattern']}`")
        output.append(
            f"**Match:** line {match_line + 1} (occurrence {occurrence} of {len(match_indices)})"
        )
        output.append("")
        output.append("```")
        for i in range(start, end):
            prefix = ">>>" if i == match_line else "   "
            output.append(f"{prefix} {i + 1}: {lines[i].rstrip()}")
        output.append("```")
        return "\n".join(output)

    elif name == "log_uniq":

        from pathlib import Path
        from collections import OrderedDict

        file_path = Path(args["file"])
        if not file_path.exists():
            return f"Error: File not found: {file_path}"

        pattern = None
        if args.get("pattern"):
            pattern = re.compile(args["pattern"], re.IGNORECASE)
        max_groups = args.get("max_groups", 100)

        # Use OrderedDict to maintain order of first occurrence
        groups = OrderedDict()
        total_lines = 0
        filtered_lines = 0

        try:
            with open(file_path, "r", errors="replace") as f:
                for line in f:
                    total_lines += 1
                    line = line.rstrip("\n")
                    if pattern and not pattern.search(line):
                        continue
                    filtered_lines += 1
                    if line in groups:
                        groups[line] += 1
                    else:
                        groups[line] = 1
        except Exception as e:
            return f"Error reading file: {e}"

        output = [f"**File:** `{file_path}`"]
        if pattern:
            output.append(f"**Filter:** `{args['pattern']}`")
        output.append(f"**Lines:** {filtered_lines} filtered / {total_lines} total")
        output.append(f"**Unique patterns:** {len(groups)}")
        output.append("")
        output.append("```")
        # Show groups in order of first occurrence
        count = 0
        for line, num in groups.items():
            output.append(f"{num:8d}  {line[:200]}")
            count += 1
            if count >= max_groups:
                output.append(f"... ({len(groups) - max_groups} more groups)")
                break
        output.append("```")
        return "\n".join(output)

    elif name == "find_instructions":
        data = load_prom(args["filename"])
        if not data:
            return f"Error: Could not load {args['filename']}"

        return find_instructions(
            data,
            mnemonic=args["mnemonic"],
            rs=args.get("rs"),
            rt=args.get("rt"),
            rd=args.get("rd"),
            imm=args.get("imm"),
            cp0_reg=args.get("cp0_reg"),
            cache_op=args.get("cache_op"),
            cache_type=args.get("cache_type"),
            cache_operation=args.get("cache_operation"),
            context=args.get("context", 0),
            max_results=args.get("max_results", 200),
        )

    elif name == "log_range":

        from pathlib import Path

        file_path = Path(args["file"])
        if not file_path.exists():
            return f"Error: File not found: {file_path}"

        start = args.get("start", 1)
        count = args.get("count", 100)
        pattern = None
        if args.get("pattern"):
            pattern = re.compile(args["pattern"], re.IGNORECASE)

        try:
            with open(file_path, "r", errors="replace") as f:
                if pattern:
                    # Filter lines first
                    lines = [
                        (i, l.rstrip("\n"))
                        for i, l in enumerate(f, 1)
                        if pattern.search(l)
                    ]
                else:
                    lines = [(i, l.rstrip("\n")) for i, l in enumerate(f, 1)]
        except Exception as e:
            return f"Error reading file: {e}"

        total = len(lines)
        if start < 0:
            start = max(1, total + start + 1)
        # Convert to 0-indexed
        start_idx = start - 1
        end_idx = min(start_idx + count, total)

        if start_idx >= total:
            return f"Start line {start} is beyond file length ({total} lines)"

        output = [f"**File:** `{file_path}`"]
        if pattern:
            output.append(f"**Filter:** `{args['pattern']}`")
        output.append(f"**Showing:** lines {start_idx + 1}-{end_idx} of {total}")
        output.append("")
        output.append("```")
        for line_num, line in lines[start_idx:end_idx]:
            output.append(f"{line_num}: {line}")
        output.append("```")
        return "\n".join(output)

    elif name == "qemu_serial_interact":


        args = _resolve_instance(args)
        cmd, serial_sock_path, monitor_sock_path, tmpdir, prom_name, err = (
            _build_qemu_launch(args)
        )
        if err:
            return err

        # Add -D for debug log redirect if save_log specified with debug flags
        debug_flags = args.get("debug_flags", "")
        save_log_path = args.get("save_log")
        if debug_flags and save_log_path:
            debug_log_path = save_log_path.replace(".log", "_debug.log")
            cmd.extend(["-D", debug_log_path])

        timeout = args.get("timeout", 30)
        boot_wait = args.get("boot_wait", 10)
        autoload = args.get("autoload", False)
        interactions = args.get("interactions", [])
        collect_after = args.get("collect_after", 3)
        ram_mb = args.get("ram_mb", 64)
        scsi_drives = args.get("scsi_drives", [])

        proc = None
        serial_sock = None
        transcript = []

        try:
            proc, _stderr_log = _popen_qemu(cmd, tmpdir)

            serial_sock, connect_err = _connect_serial_retry(
                serial_sock_path, proc, stderr_log_path=_stderr_log, cmd=cmd
            )
            if connect_err:
                return connect_err

            boot_output = _collect_serial_output(serial_sock, boot_wait)
            if boot_output:
                transcript.append(boot_output.decode("latin-1", errors="replace"))

            interaction_parts, _, _ = _run_serial_interactions(
                serial_sock, interactions, timeout, boot_output
            )
            transcript.extend(interaction_parts)

            if collect_after > 0:
                remaining_output = _collect_serial_output(serial_sock, collect_after)
                if remaining_output:
                    transcript.append(
                        remaining_output.decode("latin-1", errors="replace")
                    )

        except Exception as e:
            transcript.append(f"\n[ERROR: {e}]\n")

        finally:
            _cleanup_qemu(proc, serial_sock, monitor_sock_path, tmpdir)

        full_transcript = "".join(transcript)

        save_log = args.get("save_log")
        if save_log:
            from pathlib import Path as _Path
            _Path(save_log).write_text(full_transcript)

        output_lines = []
        output_lines.append(f"**PROM:** `{prom_name}`")
        output_lines.append(f"**RAM:** {ram_mb}MB")
        output_lines.append(f"**AutoLoad:** {'Y' if autoload else 'N'}")
        if interactions:
            output_lines.append(f"**Interactions:** {len(interactions)}")
        if scsi_drives:
            output_lines.append(f"**SCSI Drives:** {len(scsi_drives)}")
        if save_log:
            output_lines.append(f"**Log saved to:** `{save_log}`")
        output_lines.append("")
        output_lines.append("**Serial transcript:**")
        output_lines.extend(_format_transcript(full_transcript))

        return "\n".join(output_lines)

    # --- Persistent QEMU session handlers ---

    elif name == "qemu_session_start":






        # Resolve instance parameter into concrete paths
        args = _resolve_instance(args)

        # Validate snapshot hardware metadata before attempting -loadvm.
        # A snapshot without recorded hardware metadata was created before this
        # tracking was added, or on a different platform — loading it is likely
        # to produce a kernel panic or vmstate mismatch.
        snapshot_name = args.get("snapshot")
        instance_name = args.get("instance")
        if snapshot_name and instance_name:
            manifest = vm_instances.load_manifest(instance_name)
            if manifest:
                snap_map = {s["name"]: s for s in manifest.get("snapshots", [])}
                if snapshot_name not in snap_map:
                    known = list(snap_map.keys())
                    return (
                        f"Error: snapshot '{snapshot_name}' is not recorded in the "
                        f"'{instance_name}' manifest (known: {known}). "
                        f"It may exist in the qcow2 but has no hardware metadata — "
                        f"boot fresh (without snapshot=) or recreate the snapshot."
                    )
                snap_entry = snap_map[snapshot_name]
                if "hardware" not in snap_entry:
                    return (
                        f"Error: snapshot '{snapshot_name}' has no saved hardware "
                        f"metadata. It was created before hardware tracking was added. "
                        f"Boot fresh (without snapshot=) or delete and recreate it."
                    )
                hw = snap_entry["hardware"]
                snap_platform = hw.get("platform", "")
                current_platform = platform.system().lower()
                if snap_platform and snap_platform != current_platform:
                    return (
                        f"Error: snapshot '{snapshot_name}' was saved on "
                        f"'{snap_platform}' but current platform is '{current_platform}'. "
                        f"QEMU snapshots are not cross-platform compatible."
                    )

        # Stop any dead tracked sessions (but don't kill orphans — use qemu_session_cleanup)
        dead_ids = [sid for sid, s in _qemu_sessions.items() if not s.is_running()]
        for sid in dead_ids:
            try:
                _qemu_sessions[sid].stop()
            except Exception:
                pass
            del _qemu_sessions[sid]

        # Enforce max 2 concurrent sessions
        if len(_qemu_sessions) >= 2:
            return (
                "Error: maximum 2 concurrent sessions. Stop an existing session first.\nActive sessions: "
                + ", ".join(f"{sid} ({s.machine})" for sid, s in _qemu_sessions.items())
            )

        cmd, serial_sock_path, monitor_sock_path, tmpdir, prom_name, error = (
            _build_qemu_launch(args)
        )
        if error:
            return error

        # ── Disk-safety gates (corruption is the #1 time-sink) ──────────────
        _writable_disks = _extract_writable_disks(cmd)
        force_dirty = bool(args.get("force_dirty", False))
        for _d in _writable_disks:
            # 1. Refuse to boot a disk left dirty by a force-kill until it's
            #    scanned (xfs_scan) or rolled back to a golden.
            _info = disk_safety.is_dirty(_d)
            if _info and not force_dirty:
                shutil.rmtree(tmpdir, ignore_errors=True)
                return "Error: " + disk_safety.dirty_error_message(_d, _info)
            # 2. Refuse to write-open an immutable golden (chmod 444) — boot a
            #    fresh overlay instead so a crash can't poison the golden.
            if os.path.exists(_d) and not os.access(_d, os.W_OK):
                shutil.rmtree(tmpdir, ignore_errors=True)
                return (
                    f"Error: disk '{_d}' is read-only (an immutable golden) but is being "
                    f"opened writable. Boot a FRESH OVERLAY instead so a crash only poisons "
                    f"a throwaway:\n  qemu-img create -f qcow2 -b '{_d}' -F qcow2 "
                    f"<work>.qcow2\n  (or use vm_instance_fork / golden_fork). "
                    f"Add ':ro' to the drive spec only if you truly want read-only."
                )

        boot_wait = args.get("boot_wait", 15)
        machine = args.get("machine", "indy")

        # Store the QEMU binary path so qemu_session_snapshot can record
        # hardware metadata when the user saves a snapshot from this session.
        _qemu_bin = str(cmd[0])

        try:
            # Launch QEMU and connect serial socket.
            # _handle_tool already runs in a thread pool, so blocking is fine.
            proc, _stderr_log = _popen_qemu(cmd, tmpdir)
            serial_sock, connect_err = _connect_serial_retry(
                serial_sock_path, proc, stderr_log_path=_stderr_log, cmd=cmd
            )
            if connect_err:
                proc.kill()
                proc.wait(timeout=5)
                return connect_err

            # Create session
            session_id = uuid.uuid4().hex[:8]
            session = QemuSession(
                session_id=session_id,
                proc=proc,
                serial_sock=serial_sock,
                monitor_sock_path=monitor_sock_path,
                tmpdir=tmpdir,
                machine=machine,
                prom_name=prom_name,
                qemu_binary=_qemu_bin,
                extra_args=args.get("extra_args", ""),
                disks=_writable_disks,
            )
            _qemu_sessions[session_id] = session

            # Wait for initial boot output, checking periodically if QEMU is still alive
            elapsed = 0.0
            poll_interval = 1.0
            while elapsed < boot_wait:
                chunk = min(poll_interval, boot_wait - elapsed)
                time.sleep(chunk)
                elapsed += chunk
                if not session.is_running():
                    # QEMU died during boot_wait — collect what we got and bail
                    break
            initial_output = session.drain_buffer()
            if not session.is_running():
                # Clean up the dead session
                try:
                    session.stop()
                except Exception:
                    pass
                del _qemu_sessions[session_id]
                stderr_output = ""
                try:
                    with open(
                        os.path.join(tmpdir, "qemu_stderr.txt"), "r", errors="replace"
                    ) as _f:
                        stderr_output = _f.read()
                except Exception:
                    pass
                error_msg = f"Error: QEMU exited during boot (after {elapsed:.0f}s of {boot_wait}s boot_wait)"
                if cmd:
                    error_msg += f"\n\n**Command:** `{' '.join(str(x) for x in cmd)}`"
                if initial_output.strip():
                    error_msg += f"\n\n**Last serial output:**\n```\n{initial_output.strip()}\n```"
                if stderr_output.strip():
                    error_msg += (
                        f"\n\n**QEMU stderr:**\n```\n{stderr_output.strip()}\n```"
                    )
                return error_msg

            output_lines = []
            output_lines.append(f"**Session started:** `{session_id}`")
            output_lines.append(f"**Machine:** {machine}")
            output_lines.append(f"**PROM:** `{prom_name}`")
            output_lines.append(f"**RAM:** {args.get('ram_mb', 64)}MB")
            if args.get("vnc", False):
                # Detect actual VNC port from QEMU stderr log file
                vnc_actual_port = 5900 + args.get("vnc_port", 0)
                try:
                    import re as _re

                    with open(
                        os.path.join(tmpdir, "qemu_stderr.txt"), "r", errors="replace"
                    ) as _f:
                        err_chunk = _f.read()
                    vnc_match = _re.search(
                        r"VNC server running on [^:]+:(\d+)", err_chunk
                    )
                    if vnc_match:
                        vnc_actual_port = int(vnc_match.group(1))
                except Exception:
                    pass
                output_lines.append(
                    f"**VNC:** Connect to port **{vnc_actual_port}** with any VNC client (password: sgi)"
                )
            if args.get("snapshot"):
                output_lines.append(f"**Restored snapshot:** `{args['snapshot']}`")
                output_lines.append(
                    "**WARNING:** Loading snapshots across QEMU builds can corrupt the qcow2 disk. "
                    "Prefer `vm_instance_fork` + `vm_instance_reset` for disposable test instances."
                )
            if args.get("save_log"):
                output_lines.append(
                    f"**Debug log:** `{args['save_log']}` (QEMU trace/debug output)"
                )
            if dead_ids:
                output_lines.append(
                    f"**Cleanup:** removed {len(dead_ids)} dead session(s)"
                )
            output_lines.append("")
            output_lines.append("**Initial output:**")
            output_lines.append("```")
            # Limit to last 100 lines
            init_lines = initial_output.split("\n")
            if len(init_lines) > 100:
                output_lines.extend(init_lines[-100:])
            else:
                output_lines.extend(init_lines)
            output_lines.append("```")

            return "\n".join(output_lines)

        except Exception as e:
            # Clean up on failure
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass
            return f"Error starting session: {e}"

    elif name == "qemu_session_send":



        session_id = args.get("session_id", "")
        if session_id not in _qemu_sessions:
            available = ", ".join(_qemu_sessions.keys()) if _qemu_sessions else "none"
            return (
                f"Error: session '{session_id}' not found. Active sessions: {available}"
            )

        session = _qemu_sessions[session_id]
        if not session.is_running():
            try:
                session.stop()
            except Exception:
                pass
            del _qemu_sessions[session_id]
            return f"Error: session '{session_id}' is no longer running (QEMU exited)"

        text = args.get("text", "")
        timeout_val = args.get("timeout", 5)
        expect_pattern = args.get("expect", "")

        # Send text if provided
        if text:
            try:
                session.send(text)
            except Exception as e:
                return f"Error sending to session: {e}"

        # Wait for output
        if expect_pattern:
            # Wait until pattern matches or timeout
            try:
                compiled = re.compile(expect_pattern)
            except re.error:
                compiled = None

            end_time = time.time() + timeout_val
            qemu_died = False
            while time.time() < end_time:
                time.sleep(0.3)
                if not session.is_running():
                    qemu_died = True
                    break
                with session.buffer_lock:
                    current = session.output_buffer.decode("latin-1", errors="replace")
                if compiled:
                    if compiled.search(current):
                        break
                else:
                    if expect_pattern in current:
                        break
            # Drain everything accumulated
            output = session.drain_buffer()
            if qemu_died:
                try:
                    session.stop()
                except Exception:
                    pass
                del _qemu_sessions[session_id]
            matched = False
            if compiled:
                matched = bool(compiled.search(output))
            else:
                matched = expect_pattern in output

            result_lines = []
            if text:
                result_lines.append(f"**Sent:** `{repr(text)}`")
            if qemu_died:
                status_str = "QEMU EXITED"
            elif matched:
                status_str = "matched"
            else:
                status_str = "TIMEOUT"
            result_lines.append(f"**Expected:** `{expect_pattern}` — {status_str}")
            result_lines.append("```")
            out_lines = output.split("\n")
            if len(out_lines) > 200:
                result_lines.extend(out_lines[:50])
                result_lines.append(f"... ({len(out_lines) - 100} lines omitted)")
                result_lines.extend(out_lines[-50:])
            else:
                result_lines.extend(out_lines)
            result_lines.append("```")
            return "\n".join(result_lines)
        else:
            # Wait timeout seconds, checking if QEMU is still alive
            elapsed = 0.0
            while elapsed < timeout_val:
                chunk = min(0.5, timeout_val - elapsed)
                time.sleep(chunk)
                elapsed += chunk
                if not session.is_running():
                    break
            output = session.drain_buffer()
            qemu_died = not session.is_running()
            if qemu_died:
                try:
                    session.stop()
                except Exception:
                    pass
                del _qemu_sessions[session_id]

            result_lines = []
            if text:
                result_lines.append(f"**Sent:** `{repr(text)}`")
            if qemu_died:
                result_lines.append(
                    f"**QEMU exited** after {elapsed:.1f}s of {timeout_val}s wait"
                )
            else:
                result_lines.append(f"**Waited:** {timeout_val}s")
            result_lines.append("```")
            out_lines = output.split("\n")
            if len(out_lines) > 200:
                result_lines.extend(out_lines[:50])
                result_lines.append(f"... ({len(out_lines) - 100} lines omitted)")
                result_lines.extend(out_lines[-50:])
            else:
                result_lines.extend(out_lines)
            result_lines.append("```")
            return "\n".join(result_lines)

    elif name == "qemu_serial_write_file":



        session_id = args.get("session_id", "")
        if session_id not in _qemu_sessions:
            available = ", ".join(_qemu_sessions.keys()) if _qemu_sessions else "none"
            return f"Error: session '{session_id}' not found. Active sessions: {available}"

        session = _qemu_sessions[session_id]
        if not session.is_running():
            try:
                session.stop()
            except Exception:
                pass
            del _qemu_sessions[session_id]
            return f"Error: session '{session_id}' is no longer running"

        guest_path = args.get("guest_path", "")
        content = args.get("content", "")
        batch_size = int(args.get("batch_size", 25))
        timeout_per_batch = int(args.get("timeout_per_batch", 30))
        use_sh = args.get("use_sh", True)

        if not guest_path:
            return "Error: guest_path is required"

        # Split into lines; drop trailing empty line if content ends with \n
        lines = content.split("\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]
        total_lines = len(lines)
        if total_lines == 0:
            return "Error: content is empty"

        def _escape_for_send(s: str) -> str:
            """Escape a string so it survives session.send()'s unicode_escape pipeline
            and arrives at the shell correctly inside single quotes."""
            # Double every backslash: after unicode_escape, \\ → \
            s = s.replace("\\", "\\\\")
            # Escape single quotes for sh: ' → '"'"'
            s = s.replace("'", "'\"'\"'")
            return s

        def _build_printf(line: str, path: str, append: bool) -> str:
            """Return a printf command string ready for session.send() (without \\r)."""
            escaped = _escape_for_send(line)
            redirect = ">>" if append else ">"
            # r"printf '%s\\n'" uses raw string so \\n is two chars (backslash+n);
            # after unicode_escape those two bytes → backslash+n, which printf
            # uses as its newline format sequence.
            return r"printf '%s\\n' '" + escaped + r"' " + redirect + r" " + path

        def _wait_for(pattern: str, timeout: float) -> tuple[bool, str]:
            """Poll output buffer until pattern appears or timeout. Returns (found, output)."""
            end_time = time.time() + timeout
            compiled = re.compile(pattern)
            while time.time() < end_time:
                time.sleep(0.3)
                if not session.is_running():
                    break
                with session.buffer_lock:
                    cur = session.output_buffer.decode("latin-1", errors="replace")
                if compiled.search(cur):
                    return True, session.drain_buffer()
            return False, session.drain_buffer()

        # Optionally switch to POSIX sh to avoid csh history-expansion
        if use_sh:
            session.send("exec sh\r")
            _wait_for(r"[#$]\s*$", 5)

        errors = []
        batches_sent = 0

        for batch_start in range(0, total_lines, batch_size):
            batch_end = min(batch_start + batch_size, total_lines)
            batch_num = batch_start // batch_size
            sentinel = f"__WF_{batch_num}__"

            # Build all printf commands for this batch plus the sentinel echo
            cmds = []
            for idx in range(batch_start, batch_end):
                cmd = _build_printf(lines[idx], guest_path, append=(idx > 0))
                cmds.append(cmd)
            cmds.append(f"echo {sentinel}")

            # Send entire batch as one write (each command separated by \r)
            # r"\r" → backslash+r → unicode_escape → CR byte
            full = r"\r".join(cmds) + r"\r"
            session.send(full)

            found, _ = _wait_for(re.escape(sentinel), timeout_per_batch)
            batches_sent += 1

            if not found:
                errors.append(
                    f"Timeout on batch {batch_num} "
                    f"(lines {batch_start + 1}–{batch_end})"
                )
                if not session.is_running():
                    errors.append("QEMU session died")
                    break

        if errors:
            return (
                f"Write incomplete after {batches_sent} batches "
                f"({batch_end} of {total_lines} lines):\n"
                + "\n".join(errors)
            )

        # Verify line count
        session.send(
            r"echo __WF_LINES__$(wc -l < " + guest_path + r")\r"
        )
        found, verify_out = _wait_for("__WF_LINES__", 10)
        actual_lines = None
        if found:
            m = re.search(r"__WF_LINES__\s*(\d+)", verify_out)
            if m:
                actual_lines = int(m.group(1))

        if actual_lines is not None and actual_lines != total_lines:
            return (
                f"Warning: wrote {total_lines} lines but guest reports "
                f"{actual_lines} lines in {guest_path}"
            )

        lines_info = (
            f"{actual_lines} lines confirmed" if actual_lines is not None
            else f"{total_lines} lines sent (verification failed)"
        )
        return (
            f"✓ Wrote {guest_path}: {lines_info} "
            f"in {batches_sent} batches of up to {batch_size}"
        )

    elif name == "qemu_serial_upload_binary":




        session_id = args.get("session_id", "")
        host_path = args.get("host_path", "")
        guest_path = args.get("guest_path", "")
        batch_size = int(args.get("batch_size", 20))
        timeout_per_batch = int(args.get("timeout_per_batch", 30))

        if session_id not in _qemu_sessions:
            available = ", ".join(_qemu_sessions.keys()) if _qemu_sessions else "none"
            return f"Error: session '{session_id}' not found. Active sessions: {available}"

        session = _qemu_sessions[session_id]
        if not session.is_running():
            return f"Error: session '{session_id}' is no longer running"

        if not host_path or not guest_path:
            return "Error: host_path and guest_path are required"

        # Read the binary file
        try:
            with open(host_path, "rb") as f:
                data = f.read()
        except Exception as e:
            return f"Error reading {host_path}: {e}"

        file_size = len(data)
        guest_basename = os.path.basename(guest_path)
        guest_dir = os.path.dirname(guest_path) or "/tmp"

        # Pure-Python uuencode (traditional format; IRIX uudecode understands it)
        def _uuencode(data: bytes, name: str, mode: int = 0o644) -> str:
            lines = [f"begin {mode:o} {name}"]
            for i in range(0, len(data), 45):
                chunk = data[i:i + 45]
                n = len(chunk)
                # Pad to multiple of 3 for encoding
                padded = chunk + b"\x00" * ((3 - n % 3) % 3)
                encoded = chr(n + 32)
                for j in range(0, len(padded), 3):
                    a, b, c = padded[j], padded[j + 1], padded[j + 2]
                    encoded += chr(((a >> 2) & 0x3F) + 32)
                    encoded += chr((((a & 0x03) << 4) | ((b >> 4) & 0x0F)) + 32)
                    encoded += chr((((b & 0x0F) << 2) | ((c >> 6) & 0x03)) + 32)
                    encoded += chr((c & 0x3F) + 32)
                lines.append(encoded)
            lines.append("`")
            lines.append("end")
            return "\n".join(lines) + "\n"

        uu_text = _uuencode(data, guest_basename, mode=0o644)
        uu_lines = uu_text.split("\n")
        if uu_lines and uu_lines[-1] == "":
            uu_lines = uu_lines[:-1]
        total_lines = len(uu_lines)

        tmp_uu = "/tmp/_mcp_upload.uu"

        # Helpers reused from qemu_serial_write_file pattern
        def _escape_for_send(s: str) -> str:
            s = s.replace("\\", "\\\\")
            s = s.replace("'", "'\"'\"'")
            return s

        def _build_printf(line: str, path: str, append: bool) -> str:
            escaped = _escape_for_send(line)
            redirect = ">>" if append else ">"
            return r"printf '%s\\n' '" + escaped + r"' " + redirect + r" " + path

        def _wait_for(pattern: str, timeout: float) -> tuple:
            end_time = time.time() + timeout
            compiled = re.compile(pattern)
            while time.time() < end_time:
                time.sleep(0.3)
                if not session.is_running():
                    break
                with session.buffer_lock:
                    cur = session.output_buffer.decode("latin-1", errors="replace")
                if compiled.search(cur):
                    return True, session.drain_buffer()
            return False, session.drain_buffer()

        # Ensure POSIX sh (avoid csh history expansion on ! chars in uuencode)
        session.send("exec sh\r")
        _wait_for(r"[#$]\s*$", 5)

        # Send uuencoded text in batches
        errors = []
        batches_sent = 0
        for batch_start in range(0, total_lines, batch_size):
            batch_end = min(batch_start + batch_size, total_lines)
            batch_num = batch_start // batch_size
            sentinel = f"__UU_{batch_num}__"

            cmds = []
            for idx in range(batch_start, batch_end):
                cmds.append(_build_printf(uu_lines[idx], tmp_uu, append=(idx > 0 or batch_start > 0)))
            cmds.append(f"echo {sentinel}")

            full = r"\r".join(cmds) + r"\r"
            session.send(full)

            found, _ = _wait_for(re.escape(sentinel), timeout_per_batch)
            batches_sent += 1
            if not found:
                errors.append(f"Timeout on batch {batch_num} (lines {batch_start + 1}–{batch_end})")
                if not session.is_running():
                    errors.append("QEMU session died")
                    break

        if errors:
            return (
                f"Upload failed after {batches_sent} batches:\n" + "\n".join(errors)
            )

        # Run uudecode to recover binary at guest_path
        guest_dir_esc = guest_dir.replace("'", "'\"'\"'")
        session.send(f"cd '{guest_dir_esc}' && uudecode {tmp_uu} && echo __UU_DONE__\r")
        found, out = _wait_for("__UU_DONE__|uudecode:", 30)
        if not found or "uudecode:" in out:
            return f"uudecode failed on guest:\n{out}"

        # Verify the result exists and has the right size
        session.send(f"wc -c < '{guest_path}' && echo __UU_SZ__\r")
        found, sz_out = _wait_for("__UU_SZ__", 10)
        guest_size = None
        if found:
            m = re.search(r"(\d+)\s*\n.*__UU_SZ__", sz_out, re.DOTALL)
            if m:
                guest_size = int(m.group(1))

        if guest_size is not None and guest_size != file_size:
            return (
                f"Size mismatch: host {file_size} bytes, guest reports {guest_size} bytes for {guest_path}"
            )

        size_info = f"{guest_size} bytes confirmed" if guest_size is not None else f"{file_size} bytes (unverified)"
        return f"✓ Uploaded {host_path} → {guest_path}: {size_info} in {batches_sent} batches"

    elif name == "qemu_session_snapshot":



        session_id = args.get("session_id", "")
        snapshot_name = args.get("snapshot_name", "")
        if session_id not in _qemu_sessions:
            available = ", ".join(_qemu_sessions.keys()) if _qemu_sessions else "none"
            return (
                f"Error: session '{session_id}' not found. Active sessions: {available}"
            )
        if not snapshot_name:
            return "Error: snapshot_name is required"

        session = _qemu_sessions[session_id]
        if not session.is_running():
            del _qemu_sessions[session_id]
            return f"Error: session '{session_id}' is no longer running"

        # Connect to monitor and send savevm
        try:
            mon_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            mon_sock.settimeout(10)
            mon_sock.connect(session.monitor_sock_path)
            # Read any pending monitor prompt
            try:
                mon_sock.recv(4096)
            except socket.timeout:
                pass
            mon_sock.sendall(f"savevm {snapshot_name}\n".encode())
            # Wait for completion - savevm can take a few seconds
            response = b""
            end_time = time.time() + 30
            while time.time() < end_time:
                try:
                    mon_sock.settimeout(1)
                    data = mon_sock.recv(4096)
                    if data:
                        response += data
                        # Monitor shows (qemu) prompt when done
                        if b"(qemu)" in response:
                            break
                except socket.timeout:
                    # Check if we've been waiting long enough
                    if time.time() - (end_time - 30) > 5:
                        break
                    continue
            mon_sock.close()

            resp_text = response.decode("latin-1", errors="replace")
            if "Error" in resp_text or "error" in resp_text:
                return f"**Snapshot failed:**\n```\n{resp_text}\n```"

            # Record snapshot in instance manifest if specified
            instance_name = args.get("instance")
            snap_desc = args.get("description", "")
            if instance_name:
                # Capture hardware metadata from the running session so that
                # qemu_session_start can validate compatibility on reload.
                _bin = session.qemu_binary
                _mtime = 0
                try:
                    _mtime = int(Path(_bin).stat().st_mtime)
                except OSError:
                    pass
                hw_meta = {
                    "platform": platform.system().lower(),
                    "qemu_binary": _bin,
                    "qemu_mtime": _mtime,
                    "machine": session.machine,
                    "extra_args": session.extra_args,
                }
                vm_instances.add_snapshot(instance_name, snapshot_name, snap_desc, hardware=hw_meta)

            return f"**Snapshot saved:** `{snapshot_name}` on session `{session_id}`\n```\n{resp_text}\n```"

        except Exception as e:
            return f"Error saving snapshot: {e}"

    elif name == "qemu_session_monitor":



        session_id = args.get("session_id", "")
        command = args.get("command", "")
        if session_id not in _qemu_sessions:
            available = ", ".join(_qemu_sessions.keys()) if _qemu_sessions else "none"
            return (
                f"Error: session '{session_id}' not found. Active sessions: {available}"
            )
        if not command:
            return "Error: command is required"

        session = _qemu_sessions[session_id]
        if not session.is_running():
            del _qemu_sessions[session_id]
            return f"Error: session '{session_id}' is no longer running"

        try:
            mon_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            mon_sock.settimeout(10)
            mon_sock.connect(session.monitor_sock_path)
            # Read any pending monitor prompt
            try:
                mon_sock.recv(4096)
            except socket.timeout:
                pass
            mon_sock.sendall(f"{command}\n".encode())
            # Read response until we see the (qemu) prompt
            response = b""
            end_time = time.time() + 15
            while time.time() < end_time:
                try:
                    mon_sock.settimeout(2)
                    data = mon_sock.recv(4096)
                    if data:
                        response += data
                        if b"(qemu)" in response:
                            break
                except socket.timeout:
                    if time.time() - (end_time - 15) > 3:
                        break
                    continue
            mon_sock.close()

            resp_text = response.decode("latin-1", errors="replace")
            # Strip the (qemu) prompt from output
            resp_text = resp_text.replace("(qemu)", "").strip()
            return f"```\n{resp_text}\n```"

        except Exception as e:
            return f"Error sending monitor command: {e}"

    elif name == "qemu_session_stop":
        session_id = args.get("session_id", "")
        if session_id not in _qemu_sessions:
            available = ", ".join(_qemu_sessions.keys()) if _qemu_sessions else "none"
            return (
                f"Error: session '{session_id}' not found. Active sessions: {available}"
            )

        session = _qemu_sessions[session_id]
        # Drain any remaining output before stopping
        final_output = session.drain_buffer()
        # Graceful by default: clean in-guest `init 0` when a shell is reachable,
        # else monitor `quit`. Set graceful=false to skip the init 0 attempt.
        graceful = bool(args.get("graceful", True))
        session.stop(graceful=graceful)
        del _qemu_sessions[session_id]

        result_lines = [f"**Session `{session_id}` stopped.**"]
        if final_output.strip():
            result_lines.append("")
            result_lines.append("**Final buffered output:**")
            result_lines.append("```")
            final_lines = final_output.split("\n")
            if len(final_lines) > 50:
                result_lines.extend(final_lines[-50:])
            else:
                result_lines.extend(final_lines)
            result_lines.append("```")
        return "\n".join(result_lines)

    elif name == "qemu_session_cleanup":
        # scope="own" (default): only THIS session's tracked sessions + our own
        # child QEMUs — never another Claude session's VMs (multi-session safe).
        # scope="all": nuclear — kill every qemu-system-mips64 on the box.
        scope = args.get("scope", "own")
        if scope not in ("own", "all"):
            return "Error: scope must be 'own' (default) or 'all'."

        session_count = len(_qemu_sessions)
        for sid in list(_qemu_sessions.keys()):
            s = _qemu_sessions[sid]
            s.alive = False
            try:
                s.serial_sock.close()
            except Exception:
                pass
            # Pure SIGKILL — mark this session's disks dirty before killing.
            for _d in getattr(s, "disks", []):
                disk_safety.mark_dirty(_d, f"qemu_session_cleanup SIGKILL of session {sid}")
            try:
                s.proc.kill()
                s.proc.wait(timeout=3)
            except Exception:
                pass
        _qemu_sessions.clear()

        # Kill orphaned processes (our own harness-started QEMUs, etc.).
        killed, cleaned, skipped_foreign = _kill_orphaned_qemu(scope=scope)

        lines = [
            f"**Cleanup complete (scope={scope}).**",
            f"- Tracked sessions stopped: {session_count}",
            f"- Orphaned QEMU processes killed: {killed}",
            f"- Temp directories cleaned: {cleaned}",
        ]
        if scope == "own" and skipped_foreign:
            lines.append(
                f"- Left untouched (other sessions' VMs): {skipped_foreign}  "
                f"— use scope='all' only if you KNOW no other session is active."
            )
        if killed:
            lines.append(
                "- Note: SIGKILLed disks are now marked dirty; xfs_scan or roll "
                "back to a golden before reuse."
            )
        return "\n".join(lines)

    elif name == "newport_screendump":



        from pathlib import Path

        session_id = args.get("session_id", "")
        output_path = args.get("output_path", "/tmp/newport_fb.png")
        method = args.get("method", "vram")

        if session_id not in _qemu_sessions:
            available = ", ".join(_qemu_sessions.keys()) if _qemu_sessions else "none"
            return (
                f"Error: session '{session_id}' not found. Active sessions: {available}"
            )

        session = _qemu_sessions[session_id]
        if not session.is_running():
            del _qemu_sessions[session_id]
            return f"Error: session '{session_id}' is no longer running"

        ppm_path = (
            output_path.replace(".png", ".ppm")
            if output_path.endswith(".png")
            else output_path + ".ppm"
        )

        try:
            mon_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            mon_sock.settimeout(5)
            mon_sock.connect(session.monitor_sock_path)
            # Read banner/prompt
            try:
                mon_sock.recv(4096)
            except socket.timeout:
                pass

            if method == "screendump":
                # Use QEMU's built-in screendump (dumps display surface)
                mon_sock.sendall(f"screendump {ppm_path}\n".encode())
            else:
                # Use our fb-dump property (dumps raw VRAM through compositing pipeline)
                # Indy: /machine/newport; IP54: scan /machine/unattached for sgi-pvrex3
                newport_path = "/machine/newport"

                if session.machine in ("sgi-ip54", "sgi-ip55"):
                    # IP54 has pvrex3 under /machine/unattached — scan for it
                    mon_sock.sendall(b"qom-list /machine/unattached\n")
                    time.sleep(0.5)
                    scan_resp = b""
                    try:
                        while True:
                            chunk = mon_sock.recv(4096)
                            if not chunk:
                                break
                            scan_resp += chunk
                    except socket.timeout:
                        pass
                    scan_text = scan_resp.decode("latin-1", errors="replace")
                    import re as _re
                    device_matches = _re.findall(r'(device\[\d+\])', scan_text)
                    for dev_name in device_matches:
                        dev_path = f"/machine/unattached/{dev_name}"
                        mon_sock.sendall(f"qom-get {dev_path} type\n".encode())
                        time.sleep(0.3)
                        type_resp = b""
                        try:
                            type_resp = mon_sock.recv(4096)
                        except socket.timeout:
                            pass
                        if "sgi-pvrex3" in type_resp.decode("latin-1", errors="replace"):
                            newport_path = dev_path
                            break

                # Trigger the fb-dump via qom-set
                cmd = f"qom-set {newport_path} fb-dump {ppm_path}\n"
                mon_sock.sendall(cmd.encode())

            # Wait for completion
            time.sleep(1)
            response = b""
            try:
                while True:
                    chunk = mon_sock.recv(4096)
                    if not chunk:
                        break
                    response += chunk
            except socket.timeout:
                pass
            mon_sock.close()

            resp_text = response.decode("latin-1", errors="replace")

            # Check if PPM was created
            ppm_file = Path(ppm_path)
            if not ppm_file.exists():
                return f"Error: PPM file not created at {ppm_path}.\nMonitor response:\n```\n{resp_text}\n```"

            ppm_size = ppm_file.stat().st_size
            if ppm_size < 100:
                return f"Error: PPM file too small ({ppm_size} bytes), dump may have failed.\nMonitor response:\n```\n{resp_text}\n```"

            # Convert PPM to PNG
            png_path = (
                output_path if output_path.endswith(".png") else output_path + ".png"
            )
            converted = False
            # Try PIL/Pillow first (available on most systems)
            try:
                from PIL import Image as _PILImage

                _PILImage.open(ppm_path).save(png_path, "PNG")
                converted = True
            except ImportError:
                pass
            if not converted:
                # Try sips (macOS) or pnmtopng (Linux)
                for converter_cmd in [
                    ["sips", "-s", "format", "png", ppm_path, "--out", png_path],
                    ["pnmtopng", ppm_path],
                ]:
                    try:
                        if "pnmtopng" in converter_cmd[0]:
                            subprocess.run(
                                converter_cmd,
                                stdout=open(png_path, "wb"),
                                stderr=subprocess.DEVNULL,
                                timeout=10,
                            )
                        else:
                            subprocess.run(
                                converter_cmd, capture_output=True, timeout=10
                            )
                        converted = True
                        break
                    except (FileNotFoundError, subprocess.TimeoutExpired):
                        continue
            if not converted:
                png_path = ppm_path

            final_path = png_path if Path(png_path).exists() else ppm_path
            final_size = Path(final_path).stat().st_size

            result_lines = [
                f"**Framebuffer captured** ({method} method)",
                f"- **File:** `{final_path}`",
                f"- **Size:** {final_size:,} bytes",
                f"- **Resolution:** 1280x1024",
            ]

            # Fetch Newport hardware state summary for diagnostics
            diag_text = ""
            try:
                diag_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                diag_sock.settimeout(3)
                diag_sock.connect(session.monitor_sock_path)
                try:
                    diag_sock.recv(4096)  # banner
                except socket.timeout:
                    pass
                # Get XMAP state (mode table, cursor/popup cmap)
                diag_sock.sendall(f"qom-get {newport_path} diag-xmap\n".encode())
                time.sleep(0.3)
                xmap_resp = b""
                try:
                    while True:
                        chunk = diag_sock.recv(65536)
                        if not chunk:
                            break
                        xmap_resp += chunk
                except socket.timeout:
                    pass
                # Get CMAP summary
                diag_sock.sendall(f"qom-get {newport_path} diag-cmap\n".encode())
                time.sleep(0.3)
                cmap_resp = b""
                try:
                    while True:
                        chunk = diag_sock.recv(65536)
                        if not chunk:
                            break
                        cmap_resp += chunk
                except socket.timeout:
                    pass
                diag_sock.close()

                # Parse XMAP — extract mode table entries and key config
                xmap_text = xmap_resp.decode("latin-1", errors="replace")
                # Extract the quoted string content from qom-get response
                xmap_start = xmap_text.find('"')
                xmap_end = xmap_text.rfind('"')
                if xmap_start >= 0 and xmap_end > xmap_start:
                    xmap_inner = xmap_text[xmap_start + 1 : xmap_end]
                    xmap_inner = xmap_inner.replace("\\n", "\n")
                    # Get first line (config/revision) and mode entries
                    xmap_lines = xmap_inner.strip().split("\n")
                    xmap_summary = []
                    for xl in xmap_lines:
                        xl_s = xl.strip()
                        if (
                            xl_s.startswith("XMAP")
                            or xl_s.startswith("cursor_cmap")
                            or xl_s.startswith("[")
                        ):
                            xmap_summary.append(xl_s)
                    if xmap_summary:
                        diag_text += "XMAP: " + xmap_summary[0] + "\n"
                        if len(xmap_summary) > 1:
                            diag_text += "  " + xmap_summary[1] + "\n"
                        for xl in xmap_summary[2:]:
                            diag_text += "  " + xl + "\n"

                # Parse CMAP — just extract the header line
                cmap_text = cmap_resp.decode("latin-1", errors="replace")
                cmap_start = cmap_text.find('"')
                cmap_end = cmap_text.rfind('"')
                if cmap_start >= 0 and cmap_end > cmap_start:
                    cmap_inner = cmap_text[cmap_start + 1 : cmap_end]
                    cmap_inner = cmap_inner.replace("\\n", "\n")
                    cmap_lines = cmap_inner.strip().split("\n")
                    if cmap_lines:
                        diag_text += "CMAP: " + cmap_lines[0].strip() + "\n"
                        # Show which pages have data
                        page_names = [
                            cl.strip()
                            for cl in cmap_lines
                            if cl.strip().startswith("Page ")
                        ]
                        if page_names:
                            diag_text += (
                                "  Pages with data: "
                                + ", ".join(p.split("(")[0].strip() for p in page_names)
                                + "\n"
                            )

            except Exception:
                pass  # Diagnostics are best-effort

            if diag_text:
                result_lines.append("")
                result_lines.append("**Newport state:**")
                result_lines.append("```")
                result_lines.append(diag_text.rstrip())
                result_lines.append("```")

            # Archive to framebuffers/ in project root if label is provided
            label = args.get("label", "")
            description = args.get("description", "")
            if label:
                import datetime


                fb_dir = Path(__file__).parent.parent / "framebuffers"
                fb_dir.mkdir(exist_ok=True)
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_label = "".join(
                    c if c.isalnum() or c in "-_" else "_" for c in label
                )
                archive_name = f"{ts}_{safe_label}"
                ext = ".png" if final_path.endswith(".png") else ".ppm"
                archive_png = fb_dir / f"{archive_name}{ext}"
                shutil.copy2(final_path, str(archive_png))
                # Write description file
                desc_file = fb_dir / f"{archive_name}.txt"
                desc_lines = [
                    f"Label: {label}",
                    f"Timestamp: {ts}",
                    f"Method: {method}",
                    f"Source: {final_path}",
                    f"Size: {final_size:,} bytes",
                    "",
                    description if description else "(no description provided)",
                ]
                if diag_text:
                    desc_lines.append("")
                    desc_lines.append("--- Newport Hardware State ---")
                    desc_lines.append(diag_text.rstrip())
                desc_file.write_text("\n".join(desc_lines) + "\n")
                result_lines.append(f"- **Archived:** `{archive_png}`")
                result_lines.append(f"- **Description:** `{desc_file}`")

            result_lines.append("")
            result_lines.append("Use the `Read` tool to view this image file.")
            return "\n".join(result_lines)

        except Exception as e:
            return f"Error capturing framebuffer: {e}"

    elif name == "newport_inspect":



        session_id = args.get("session_id", "")
        subsystem = args.get("subsystem", "all")

        if session_id not in _qemu_sessions:
            available = ", ".join(_qemu_sessions.keys()) if _qemu_sessions else "none"
            return (
                f"Error: session '{session_id}' not found. Active sessions: {available}"
            )

        session = _qemu_sessions[session_id]
        if not session.is_running():
            del _qemu_sessions[session_id]
            return f"Error: session '{session_id}' is no longer running"

        valid_subsystems = ("all", "cmap", "xmap", "vc2", "rex3", "dcb")
        if subsystem not in valid_subsystems:
            return f"Error: subsystem must be one of {valid_subsystems}"

        prop_name = f"diag-{subsystem}"

        try:
            mon_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            mon_sock.settimeout(5)
            mon_sock.connect(session.monitor_sock_path)
            # Read banner
            try:
                mon_sock.recv(4096)
            except socket.timeout:
                pass

            # Indy: /machine/newport; IP54: scan /machine/unattached for sgi-pvrex3
            newport_path = "/machine/newport"

            if session.machine in ("sgi-ip54", "sgi-ip55"):
                mon_sock.sendall(b"qom-list /machine/unattached\n")
                time.sleep(0.5)
                scan_resp = b""
                try:
                    while True:
                        chunk = mon_sock.recv(4096)
                        if not chunk:
                            break
                        scan_resp += chunk
                except socket.timeout:
                    pass
                scan_text = scan_resp.decode("latin-1", errors="replace")
                import re as _re
                device_matches = _re.findall(r'(device\[\d+\])', scan_text)
                for dev_name in device_matches:
                    dev_path = f"/machine/unattached/{dev_name}"
                    mon_sock.sendall(f"qom-get {dev_path} type\n".encode())
                    time.sleep(0.3)
                    type_resp = b""
                    try:
                        type_resp = mon_sock.recv(4096)
                    except socket.timeout:
                        pass
                    if "sgi-pvrex3" in type_resp.decode("latin-1", errors="replace"):
                        newport_path = dev_path
                        break

            cmd = f"qom-get {newport_path} {prop_name}\n"
            mon_sock.sendall(cmd.encode())
            time.sleep(0.5)

            response = b""
            try:
                while True:
                    chunk = mon_sock.recv(65536)
                    if not chunk:
                        break
                    response += chunk
            except socket.timeout:
                pass
            mon_sock.close()

            resp_text = response.decode("latin-1", errors="replace")
            # Strip monitor prompt lines
            lines = resp_text.split("\n")
            result_lines = [l for l in lines if not l.strip().startswith("(qemu)")]
            return f"**Newport {subsystem} diagnostics:**\n```\n{''.join(l + chr(10) for l in result_lines).strip()}\n```"

        except Exception as e:
            return f"Error inspecting Newport: {e}"

    elif name == "newport_sendkey":



        # Character-to-QEMU key name mapping for text mode
        _CHAR_TO_KEY = {
            " ": "spc",
            "\n": "ret",
            "\r": "ret",
            "\t": "tab",
            ".": "dot",
            ",": "comma",
            "/": "slash",
            "\\": "backslash",
            "-": "minus",
            "=": "equal",
            ";": "semicolon",
            "'": "apostrophe",
            "`": "grave_accent",
            "[": "bracket_left",
            "]": "bracket_right",
        }
        # Shifted punctuation mapping
        _SHIFT_CHAR_TO_KEY = {
            "!": "shift-1",
            "@": "shift-2",
            "#": "shift-3",
            "$": "shift-4",
            "%": "shift-5",
            "^": "shift-6",
            "&": "shift-7",
            "*": "shift-8",
            "(": "shift-9",
            ")": "shift-0",
            "_": "shift-minus",
            "+": "shift-equal",
            "{": "shift-bracket_left",
            "}": "shift-bracket_right",
            "|": "shift-backslash",
            ":": "shift-semicolon",
            '"': "shift-apostrophe",
            "<": "shift-comma",
            ">": "shift-dot",
            "?": "shift-slash",
            "~": "shift-grave_accent",
        }

        session_id = args.get("session_id", "")
        keys = args.get("keys", "")
        text = args.get("text", "")
        delay_ms = args.get("delay_ms", 100)

        if session_id not in _qemu_sessions:
            available = ", ".join(_qemu_sessions.keys()) if _qemu_sessions else "none"
            return (
                f"Error: session '{session_id}' not found. Active sessions: {available}"
            )

        session = _qemu_sessions[session_id]
        if not session.is_running():
            del _qemu_sessions[session_id]
            return f"Error: session '{session_id}' is no longer running"

        if not keys and not text:
            return "Error: either 'keys' or 'text' parameter is required"

        # Build list of key specs to send
        key_specs = []
        if text:
            for ch in text:
                if ch.isalpha():
                    if ch.isupper():
                        key_specs.append(f"shift-{ch.lower()}")
                    else:
                        key_specs.append(ch)
                elif ch.isdigit():
                    key_specs.append(ch)
                elif ch in _CHAR_TO_KEY:
                    key_specs.append(_CHAR_TO_KEY[ch])
                elif ch in _SHIFT_CHAR_TO_KEY:
                    key_specs.append(_SHIFT_CHAR_TO_KEY[ch])
                else:
                    return (
                        f"Error: unsupported character in text: {ch!r} (ord={ord(ch)})"
                    )
        elif keys:
            key_specs.append(keys)

        try:
            mon_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            mon_sock.settimeout(5)
            mon_sock.connect(session.monitor_sock_path)
            try:
                mon_sock.recv(4096)
            except socket.timeout:
                pass

            errors = []
            delay_s = delay_ms / 1000.0

            for spec in key_specs:
                mon_sock.sendall(f"sendkey {spec}\n".encode())
                time.sleep(delay_s)
                try:
                    resp = mon_sock.recv(4096).decode("latin-1", errors="replace")
                    if "unknown key" in resp.lower() or "error" in resp.lower():
                        # Strip monitor prompt noise
                        clean = resp.replace("(qemu) ", "").strip()
                        if clean:
                            errors.append(f"{spec}: {clean}")
                except socket.timeout:
                    pass

            mon_sock.close()

            if errors:
                return f"**sendkey errors:**\n" + "\n".join(errors)

            # Collect any serial output change
            time.sleep(0.5)
            serial_out = session.drain_buffer()

            if text:
                # Show abbreviated summary for text mode
                display = text.replace("\n", "\\n").replace("\t", "\\t")
                if len(display) > 60:
                    display = display[:57] + "..."
                result = f"**Typed text:** `{display}` ({len(key_specs)} keys)"
            else:
                result = f"**Sent key:** `{keys}`"

            if serial_out.strip():
                result += f"\n**Serial output:**\n```\n{serial_out}\n```"
            return result

        except Exception as e:
            return f"Error sending key: {e}"

    elif name == "newport_mouse":



        session_id = args.get("session_id", "")
        dx = args.get("dx", 0)
        dy = args.get("dy", 0)
        dz = args.get("dz", 0)
        buttons = args.get("buttons", None)

        if session_id not in _qemu_sessions:
            available = ", ".join(_qemu_sessions.keys()) if _qemu_sessions else "none"
            return (
                f"Error: session '{session_id}' not found. Active sessions: {available}"
            )

        session = _qemu_sessions[session_id]
        if not session.is_running():
            del _qemu_sessions[session_id]
            return f"Error: session '{session_id}' is no longer running"

        try:
            mon_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            mon_sock.settimeout(5)
            mon_sock.connect(session.monitor_sock_path)
            try:
                mon_sock.recv(4096)
            except socket.timeout:
                pass

            commands_sent = []

            if dx != 0 or dy != 0 or dz != 0:
                cmd = f"mouse_move {dx} {dy}"
                if dz != 0:
                    cmd += f" {dz}"
                mon_sock.sendall(f"{cmd}\n".encode())
                commands_sent.append(cmd)
                time.sleep(0.1)
                try:
                    mon_sock.recv(4096)
                except socket.timeout:
                    pass

            if buttons is not None:
                cmd = f"mouse_button {buttons}"
                mon_sock.sendall(f"{cmd}\n".encode())
                commands_sent.append(cmd)
                time.sleep(0.1)
                try:
                    mon_sock.recv(4096)
                except socket.timeout:
                    pass

            mon_sock.close()

            if not commands_sent:
                return "No mouse action specified (set dx/dy/dz or buttons)"

            return (
                f"**Mouse commands sent:** {', '.join(f'`{c}`' for c in commands_sent)}"
            )

        except Exception as e:
            return f"Error sending mouse input: {e}"

    elif name in ("nvram_dump", "nvram_set"):
        from pathlib import Path
        from sgi_mcp import nvram_utils
        from sgi_mcp.nvram_utils import (
            NVRAM_TABLE_BASE,
            NVRAM_TABLE_SIZE,
            NVRAM_VARS,
            nvram_checksum,
        )

        project_root = Path(__file__).parent.parent

        # Resolve NVRAM file path — instance > file > machine default
        machine_nvram_names = {
            "indy": "sgi_indy_nvram.bin",
            "indigo2": "sgi_indigo2_nvram.bin",
            "indigo2-r10k": "sgi_indigo2_r10k_nvram.bin",
            "indigo2-r8k": "sgi_indigo2_r8k_nvram.bin",
        }

        inst_name = args.get("instance")
        if inst_name:
            nvram_path = vm_instances.get_nvram_path(inst_name)
        elif args.get("file"):
            nvram_path = Path(args["file"])
            if not nvram_path.is_absolute():
                nvram_path = Path(project_root) / args["file"]
        else:
            machine = args.get("machine", "indy")
            fname = machine_nvram_names.get(machine, f"sgi_{machine}_nvram.bin")
            nvram_path = Path(project_root) / fname

        if name == "nvram_dump":
            if not nvram_path.exists():
                return f"NVRAM file not found: {nvram_path}\n(Run QEMU once to create it, or specify a path)"

            data = nvram_path.read_bytes()
            if len(data) < NVRAM_TABLE_BASE + NVRAM_TABLE_SIZE:
                return f"NVRAM file too small: {len(data)} bytes (need {NVRAM_TABLE_BASE + NVRAM_TABLE_SIZE})"

            table = data[NVRAM_TABLE_BASE : NVRAM_TABLE_BASE + NVRAM_TABLE_SIZE]

            # Verify checksum
            expected_cksum = nvram_checksum(table)
            stored_cksum = table[0]
            cksum_ok = (
                "OK"
                if stored_cksum == expected_cksum
                else f"MISMATCH (stored=0x{stored_cksum:02x}, computed=0x{expected_cksum:02x})"
            )

            output = [f"**NVRAM File:** `{nvram_path}`"]
            if inst_name:
                output.append(f"**Instance:** `{inst_name}`")
            output.append(f"**File size:** {len(data)} bytes")
            output.append(f"**Checksum:** {cksum_ok}")
            output.append("")
            output.append("| Variable | Offset | Value | Description |")
            output.append("|----------|--------|-------|-------------|")

            for var_name, (offset, length, desc) in sorted(
                NVRAM_VARS.items(), key=lambda x: x[1][0]
            ):
                raw = table[offset : offset + length]
                if var_name == "checksum":
                    val_str = f"0x{raw[0]:02x}"
                elif var_name == "revision":
                    val_str = str(raw[0])
                elif var_name == "enet":
                    val_str = ":".join(f"{b:02x}" for b in raw)
                elif var_name == "netaddr":
                    val_str = ".".join(str(b) for b in raw)
                elif var_name in (
                    "scsihostid",
                    "diskless",
                    "nokbd",
                    "nogui",
                    "autopower",
                ):
                    val_str = str(raw[0])
                elif var_name == "volume":
                    val_str = str(int.from_bytes(raw[:3], "big") if any(raw[:3]) else 0)
                else:
                    # String variable - read until null
                    try:
                        null_idx = raw.index(0)
                        val_str = raw[:null_idx].decode("ascii", errors="replace")
                    except ValueError:
                        val_str = raw.decode("ascii", errors="replace")
                    if not val_str:
                        val_str = "(empty)"

                output.append(f"| {var_name} | {offset} | `{val_str}` | {desc} |")

            return "\n".join(output)

        else:  # nvram_set
            variable = args["variable"]
            value = args["value"]

            if not nvram_path.exists():
                return (
                    f"NVRAM file not found: {nvram_path}\nRun QEMU once to create it."
                )

            try:
                result = nvram_utils.nvram_write_var(nvram_path, variable, value)
            except (ValueError, FileNotFoundError) as e:
                return str(e)

            # Read back to verify and format output
            offset, _max_length, desc = NVRAM_VARS[variable]
            data = nvram_path.read_bytes()
            table = data[NVRAM_TABLE_BASE : NVRAM_TABLE_BASE + NVRAM_TABLE_SIZE]
            verify_cksum = nvram_checksum(table)
            abs_offset = NVRAM_TABLE_BASE + offset

            output = [f"**NVRAM File:** `{nvram_path}`"]
            if inst_name:
                output.append(f"**Instance:** `{inst_name}`")
            output.append(f"**Variable:** `{variable}` = `{value}`")
            output.append(f"**Offset:** {offset} (abs: 0x{abs_offset:x})")
            output.append(
                f"**Checksum:** 0x{table[0]:02x} (verified: {'OK' if table[0] == verify_cksum else 'MISMATCH'})"
            )
            output.append(f"**Description:** {desc}")
            return "\n".join(output)

    elif name == "qemu_disk_convert":

        from pathlib import Path

        project_root = Path(__file__).parent.parent
        build_dir = _find_build_dir()
        qemu_img = build_dir / "qemu-img"
        if not qemu_img.exists():
            return f"Error: qemu-img not found in {build_dir}. Run qemu_build first."

        source = Path(args["source"])
        if not source.is_absolute():
            if (project_root / args["source"]).exists():
                source = project_root / args["source"]
            elif (build_dir / args["source"]).exists():
                source = build_dir / args["source"]
        if not source.exists():
            return f"Error: Source file not found: {args['source']}"

        out_fmt = args.get("output_format", "qcow2")
        dest_arg = args.get("dest")
        if dest_arg:
            dest = Path(dest_arg)
            if not dest.is_absolute():
                dest = project_root / dest_arg
        else:
            ext = ".qcow2" if out_fmt == "qcow2" else ".raw"
            dest = source.with_suffix(ext)

        if dest.exists():
            return f"Error: Destination already exists: {dest}\nDelete it first or specify a different dest."

        # Detect source format
        info_result = subprocess.run(
            [str(qemu_img), "info", "--output=json", str(source)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        src_fmt = "raw"
        if info_result.returncode == 0:
            import json as _json

            try:
                info = _json.loads(info_result.stdout)
                src_fmt = info.get("format", "raw")
            except Exception:
                pass

        cmd = [
            str(qemu_img),
            "convert",
            "-f",
            src_fmt,
            "-O",
            out_fmt,
            str(source),
            str(dest),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                return f"Error: conversion failed\n{result.stderr}"

            # Get info on new file
            info_result = subprocess.run(
                [str(qemu_img), "info", str(dest)],
                capture_output=True,
                text=True,
                timeout=10,
            )

            output = [
                f"**Converted:** `{source.name}` ({src_fmt}) → `{dest.name}` ({out_fmt})"
            ]
            if info_result.returncode == 0:
                output.append("")
                output.append(info_result.stdout.strip())
            return "\n".join(output)

        except subprocess.TimeoutExpired:
            return "Error: conversion timed out"
        except Exception as e:
            return f"Error: {e}"

    elif name == "qemu_snapshot_save":


        args = _resolve_instance(args)
        if not args.get("scsi_drives"):
            return "Error: either 'instance' or 'scsi_drives' must be provided"
        # Check for qcow2 before building command
        prom_path, build_dir, project_root, err = _resolve_prom(args)
        if err:
            return err
        _, has_qcow2, drive_err = _resolve_scsi_drives(args, build_dir, project_root)
        if drive_err:
            return drive_err
        if not has_qcow2:
            return "Error: At least one SCSI drive must be .qcow2 format for snapshots. Use qemu_disk_convert first."

        cmd, serial_sock_path, monitor_sock_path, tmpdir, prom_name, err = (
            _build_qemu_launch(args)
        )
        if err:
            return err

        # Add -D for debug log redirect
        debug_flags = args.get("debug_flags", "")
        save_log_path = args.get("save_log")
        if debug_flags and save_log_path:
            debug_log_path = save_log_path.replace(".log", "_debug.log")
            cmd.extend(["-D", debug_log_path])

        snapshot_name = args["snapshot_name"]
        ram_mb = args.get("ram_mb", 64)
        timeout = args.get("timeout", 300)
        boot_wait = args.get("boot_wait", 10)
        interactions = args.get("interactions", [])
        wait_after = args.get("wait_after_interactions", 5)
        scsi_drives = args.get("scsi_drives", [])

        proc = None
        serial_sock = None
        transcript = []
        snap_info = ""
        snapshot_found = False

        try:
            proc, _stderr_log = _popen_qemu(cmd, tmpdir)

            serial_sock, connect_err = _connect_serial_retry(
                serial_sock_path, proc, stderr_log_path=_stderr_log, cmd=cmd
            )
            if connect_err:
                return connect_err

            boot_output = _collect_serial_output(serial_sock, boot_wait)
            if boot_output:
                transcript.append(boot_output.decode("latin-1", errors="replace"))

            interaction_parts, _, all_ok = _run_serial_interactions(
                serial_sock, interactions, timeout, boot_output
            )
            transcript.extend(interaction_parts)

            if not all_ok:
                return (
                    "Error: interactions failed before snapshot could be saved.\n\n**Transcript:**\n```\n"
                    + "".join(transcript)
                    + "\n```"
                )

            if wait_after > 0:
                extra = _collect_serial_output(serial_sock, wait_after)
                if extra:
                    transcript.append(extra.decode("latin-1", errors="replace"))

            # Save snapshot via monitor
            transcript.append(f"\n[SAVING SNAPSHOT: '{snapshot_name}']\n")
            mon_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            mon_sock.settimeout(10)
            mon_sock.connect(monitor_sock_path)

            try:
                mon_sock.recv(4096)  # banner
            except socket.timeout:
                pass

            mon_sock.sendall(f"savevm {snapshot_name}\n".encode())
            time.sleep(5)

            chunks = []
            try:
                while True:
                    data = mon_sock.recv(65536)
                    if not data:
                        break
                    chunks.append(data.decode("utf-8", errors="replace"))
            except socket.timeout:
                pass
            monitor_response = "".join(chunks)

            mon_sock.sendall(b"info snapshots\n")
            time.sleep(1)
            snap_chunks = []
            try:
                while True:
                    data = mon_sock.recv(65536)
                    if not data:
                        break
                    snap_chunks.append(data.decode("utf-8", errors="replace"))
            except socket.timeout:
                pass
            snap_info = "".join(snap_chunks)

            mon_sock.sendall(b"quit\n")
            mon_sock.close()

            snapshot_found = snapshot_name in snap_info
            transcript.append(
                f"\n[SNAPSHOT {'SAVED' if snapshot_found else 'FAILED'}]\n"
            )
            if monitor_response.strip():
                transcript.append(f"[MONITOR: {monitor_response.strip()}]\n")

            # Record snapshot in instance manifest if specified
            if snapshot_found:
                instance_name = args.get("instance")
                snap_desc = args.get("description", "")
                if instance_name:
                    _bin = str(cmd[0])
                    _mtime = 0
                    try:
                        _mtime = int(Path(_bin).stat().st_mtime)
                    except OSError:
                        pass
                    hw_meta = {
                        "platform": platform.system().lower(),
                        "qemu_binary": _bin,
                        "qemu_mtime": _mtime,
                        "machine": args.get("machine", "indy"),
                        "extra_args": args.get("extra_args", ""),
                    }
                    vm_instances.add_snapshot(instance_name, snapshot_name, snap_desc, hardware=hw_meta)

        except Exception as e:
            transcript.append(f"\n[ERROR: {e}]\n")

        finally:
            _cleanup_qemu(proc, serial_sock, monitor_sock_path, tmpdir)

        full_transcript = "".join(transcript)
        save_log = args.get("save_log")
        if save_log:
            Path(save_log).write_text(full_transcript)

        output_lines = []
        output_lines.append(f"**Snapshot:** `{snapshot_name}`")
        output_lines.append(f"**PROM:** `{prom_name}`")
        output_lines.append(f"**RAM:** {ram_mb}MB")
        if scsi_drives:
            output_lines.append(f"**SCSI Drives:** {', '.join(scsi_drives)}")
        output_lines.append(
            f"**Result:** {'Snapshot saved successfully' if snapshot_found else 'SNAPSHOT SAVE FAILED'}"
        )
        if save_log:
            output_lines.append(f"**Log:** `{save_log}`")
        output_lines.append("")
        output_lines.append("**Serial transcript:**")
        output_lines.extend(_format_transcript(full_transcript))

        if snap_info.strip():
            output_lines.append("")
            output_lines.append("**Snapshots on disk:**")
            output_lines.append("```")
            for line in snap_info.strip().split("\n"):
                if "(qemu)" not in line and "QEMU" not in line:
                    output_lines.append(line)
            output_lines.append("```")

        return "\n".join(output_lines)

    elif name == "qemu_snapshot_restore":


        args = _resolve_instance(args)
        if not args.get("scsi_drives") and not args.get("instance"):
            return "Error: either 'instance' or 'scsi_drives' must be provided"
        # Pass snapshot_name through args so _build_qemu_launch adds -loadvm
        args.setdefault("snapshot", args["snapshot_name"])
        cmd, serial_sock_path, monitor_sock_path, tmpdir, prom_name, err = (
            _build_qemu_launch(args)
        )
        if err:
            return err

        # Add -D for debug log redirect
        debug_flags = args.get("debug_flags", "")
        save_log_path = args.get("save_log")
        if debug_flags and save_log_path:
            debug_log_path = save_log_path.replace(".log", "_debug.log")
            cmd.extend(["-D", debug_log_path])

        snapshot_name = args["snapshot_name"]
        ram_mb = args.get("ram_mb", 64)
        timeout = args.get("timeout", 30)
        interactions = args.get("interactions", [])
        collect_after = args.get("collect_after", 5)
        scsi_drives = args.get("scsi_drives", [])

        proc = None
        serial_sock = None
        transcript = []

        try:
            proc, _stderr_log = _popen_qemu(cmd, tmpdir)

            serial_sock, connect_err = _connect_serial_retry(
                serial_sock_path, proc, stderr_log_path=_stderr_log, cmd=cmd
            )
            if connect_err:
                return connect_err

            transcript.append(f"[RESTORED SNAPSHOT: '{snapshot_name}']\n")

            initial = _collect_serial_output(serial_sock, 2)
            if initial:
                transcript.append(initial.decode("latin-1", errors="replace"))

            interaction_parts, _, _ = _run_serial_interactions(
                serial_sock, interactions, timeout, initial
            )
            transcript.extend(interaction_parts)

            if collect_after > 0:
                remaining = _collect_serial_output(serial_sock, collect_after)
                if remaining:
                    transcript.append(remaining.decode("latin-1", errors="replace"))

        except Exception as e:
            transcript.append(f"\n[ERROR: {e}]\n")

        finally:
            _cleanup_qemu(proc, serial_sock, monitor_sock_path, tmpdir)

        full_transcript = "".join(transcript)
        save_log = args.get("save_log")
        if save_log:
            Path(save_log).write_text(full_transcript)

        output_lines = []
        output_lines.append(f"**Restored snapshot:** `{snapshot_name}`")
        output_lines.append(f"**PROM:** `{prom_name}`")
        output_lines.append(f"**RAM:** {ram_mb}MB")
        if scsi_drives:
            output_lines.append(f"**SCSI Drives:** {len(scsi_drives)}")
        if save_log:
            output_lines.append(f"**Log:** `{save_log}`")
        output_lines.append("")
        output_lines.append("**Serial transcript:**")
        output_lines.extend(_format_transcript(full_transcript))

        return "\n".join(output_lines)

    # ── IP54 PROM Build Tools ──────────────────────────────────────

    elif name == "prom_build":

        from pathlib import Path

        project_root = Path(__file__).parent.parent
        prom_dir = project_root / "prom-building"

        if not prom_dir.exists():
            return f"Error: prom-building directory not found at {prom_dir}"

        target = args.get("target", "all")
        clean = args.get("clean", False)
        jobs = args.get("jobs", 4)

        output_lines = []

        # Clean first if requested
        if clean:
            result = subprocess.run(
                ["make", "clean"],
                cwd=prom_dir,
                capture_output=True,
                text=True,
                timeout=30,
            )
            output_lines.append("**make clean:** done")
            output_lines.append("")

        # Build
        cmd = ["make", f"-j{jobs}", target]
        try:
            result = subprocess.run(
                cmd, cwd=prom_dir, capture_output=True, text=True, timeout=300
            )

            output_lines.append(f"**Command:** `{' '.join(cmd)}`")
            output_lines.append(f"**Exit code:** {result.returncode}")
            output_lines.append("")

            if result.stdout:
                stdout_lines = result.stdout.strip().split("\n")
                if len(stdout_lines) > 40:
                    output_lines.append(f"... ({len(stdout_lines) - 40} lines omitted)")
                output_lines.extend(stdout_lines[-40:])

            if result.stderr:
                stderr_lines = result.stderr.strip().split("\n")
                # Filter out common harmless warnings
                errors = [
                    l
                    for l in stderr_lines
                    if "error:" in l.lower() or "undefined reference" in l.lower()
                ]
                warnings = [
                    l
                    for l in stderr_lines
                    if "warning:" in l.lower() and "error:" not in l.lower()
                ]

                if errors:
                    output_lines.append("")
                    output_lines.append(f"**Errors ({len(errors)}):**")
                    output_lines.append("```")
                    output_lines.extend(errors[:50])
                    if len(errors) > 50:
                        output_lines.append(f"... and {len(errors) - 50} more")
                    output_lines.append("```")

                if warnings and not errors:
                    output_lines.append("")
                    output_lines.append(
                        f"**Warnings:** {len(warnings)} (suppressed, no errors)"
                    )

            if result.returncode == 0:
                output_lines.append("")
                output_lines.append("**Build successful!**")
                # Show binary info
                bin_path = prom_dir / "build" / "ip54.bin"
                elf_path = prom_dir / "build" / "ip54.elf"
                if bin_path.exists():
                    size = bin_path.stat().st_size
                    output_lines.append(f"**Binary:** `{bin_path}` ({size} bytes)")

            return "\n".join(output_lines)

        except subprocess.TimeoutExpired:
            return "Error: Build timed out after 300 seconds"
        except Exception as e:
            return f"Error running make: {e}"

    elif name == "prom_try_compile":

        from pathlib import Path

        project_root = Path(__file__).parent.parent
        prom_dir = project_root / "prom-building"
        source_file = args["file"]

        # Determine if assembly or C
        if source_file.endswith(".s"):
            cmd = ["make", f"try-asm", f"FILE={source_file}"]
        else:
            cmd = ["make", f"try-compile", f"FILE={source_file}"]

        try:
            result = subprocess.run(
                cmd, cwd=prom_dir, capture_output=True, text=True, timeout=60
            )

            output_lines = [f"**File:** `{source_file}`"]
            output_lines.append(f"**Exit code:** {result.returncode}")

            if result.stdout:
                output_lines.append("")
                output_lines.append(result.stdout.strip())

            if result.stderr:
                output_lines.append("")
                output_lines.append("**Compiler output:**")
                output_lines.append("```")
                output_lines.extend(result.stderr.strip().split("\n")[-40:])
                output_lines.append("```")

            return "\n".join(output_lines)

        except Exception as e:
            return f"Error: {e}"

    elif name == "prom_symbols":

        from pathlib import Path

        project_root = Path(__file__).parent.parent
        elf_path = project_root / "prom-building" / "build" / "ip54.elf"

        if not elf_path.exists():
            return "Error: build/ip54.elf not found. Run prom_build first."

        cross_prefix = Path.home() / "cross" / "mips-elf" / "bin" / "mips-elf-"
        nm = f"{cross_prefix}nm"

        sort_flag = "-n" if args.get("sort", "address") == "address" else ""
        undef_flag = "-u" if args.get("undefined_only", False) else ""

        cmd = [nm]
        if sort_flag:
            cmd.append(sort_flag)
        if undef_flag:
            cmd.append(undef_flag)
        cmd.append(str(elf_path))

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            output = result.stdout

            filt = args.get("filter", "")
            if filt:
                output = "\n".join(l for l in output.split("\n") if filt in l)

            lines = output.strip().split("\n")
            output_lines = [f"**Symbols in ip54.elf** ({len(lines)} matches)"]
            if filt:
                output_lines[0] += f" [filter: '{filt}']"
            output_lines.append("```")
            if len(lines) > 100:
                output_lines.extend(lines[:50])
                output_lines.append(f"... ({len(lines) - 100} lines omitted)")
                output_lines.extend(lines[-50:])
            else:
                output_lines.extend(lines)
            output_lines.append("```")

            return "\n".join(output_lines)

        except Exception as e:
            return f"Error: {e}"

    elif name == "prom_disasm":

        from pathlib import Path

        project_root = Path(__file__).parent.parent
        elf_path = project_root / "prom-building" / "build" / "ip54.elf"

        if not elf_path.exists():
            return "Error: build/ip54.elf not found. Run prom_build first."

        cross_prefix = Path.home() / "cross" / "mips-elf" / "bin" / "mips-elf-"
        objdump = f"{cross_prefix}objdump"

        cmd = [objdump, "-d", str(elf_path)]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            full_disasm = result.stdout

            func_name = args.get("function", "")
            address = args.get("address", "")
            context = args.get("context", 20)

            if func_name:
                # Find function and extract it
                lines = full_disasm.split("\n")
                start = None
                output = []
                for i, line in enumerate(lines):
                    if f"<{func_name}>:" in line:
                        start = i
                    if start is not None:
                        output.append(line)
                        # End at next function or empty line after some content
                        if i > start and line.strip() == "" and len(output) > 2:
                            break
                        if i > start + 200:
                            output.append("... (truncated at 200 lines)")
                            break

                if not output:
                    return f"Function '{func_name}' not found. Use prom_symbols to find available functions."

                result_lines = [f"**Disassembly of `{func_name}`:**"]
                result_lines.append("```")
                result_lines.extend(output)
                result_lines.append("```")
                return "\n".join(result_lines)

            elif address:
                # Find lines near address
                addr_int = (
                    int(address, 16) if address.startswith("0x") else int(address, 16)
                )
                addr_str = f"{addr_int:x}"
                lines = full_disasm.split("\n")
                for i, line in enumerate(lines):
                    if addr_str in line and ":" in line:
                        start = max(0, i - context)
                        end = min(len(lines), i + context)
                        result_lines = [f"**Disassembly around `0x{addr_str}`:**"]
                        result_lines.append("```")
                        result_lines.extend(lines[start:end])
                        result_lines.append("```")
                        return "\n".join(result_lines)

                return f"Address 0x{addr_str} not found in disassembly."

            else:
                # Show first 50 lines (entry point area)
                lines = full_disasm.split("\n")[:50]
                result_lines = ["**Disassembly (entry point):**"]
                result_lines.append("```")
                result_lines.extend(lines)
                result_lines.append("```")
                return "\n".join(result_lines)

        except Exception as e:
            return f"Error: {e}"

    elif name == "prom_sections":

        from pathlib import Path

        project_root = Path(__file__).parent.parent
        elf_path = project_root / "prom-building" / "build" / "ip54.elf"

        if not elf_path.exists():
            return "Error: build/ip54.elf not found. Run prom_build first."

        cross_prefix = Path.home() / "cross" / "mips-elf" / "bin" / "mips-elf-"

        output_lines = []

        # Section headers
        result = subprocess.run(
            [f"{cross_prefix}objdump", "-h", str(elf_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output_lines.append("**Section Headers:**")
        output_lines.append("```")
        output_lines.append(result.stdout.strip())
        output_lines.append("```")

        # Size summary
        result = subprocess.run(
            [f"{cross_prefix}size", str(elf_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output_lines.append("")
        output_lines.append("**Size Summary:**")
        output_lines.append("```")
        output_lines.append(result.stdout.strip())
        output_lines.append("```")

        # Binary size
        bin_path = project_root / "prom-building" / "build" / "ip54.bin"
        if bin_path.exists():
            output_lines.append("")
            output_lines.append(
                f"**Binary:** `ip54.bin` ({bin_path.stat().st_size} bytes)"
            )

        return "\n".join(output_lines)

    elif name == "prom_preprocess":

        from pathlib import Path

        project_root = Path(__file__).parent.parent
        prom_dir = project_root / "prom-building"
        cross_prefix = Path.home() / "cross" / "mips-elf" / "bin" / "mips-elf-"
        cc = f"{cross_prefix}gcc"

        source_file = args.get("file", "")
        expression = args.get("expression", "")
        header = args.get("header", "")

        if source_file:
            # Preprocess a file
            cmd = [
                cc,
                "-E",
                "-march=mips3",
                "-mabi=32",
                "-EB",
                "-G",
                "0",
                "-ffreestanding",
                "-D_STANDALONE",
                "-D_KERNEL",
                "-DIP32",
                "-DR4000",
                "-D_MIPSEB",
                "-D_PAGESZ=16384",
                "-D_LANGUAGE_C",
                "-I./compat",
                "-I./include/ip32",
                "-I./include",
                "-I./include/sys",
                "-include",
                "./compat/irix_compat.h",
                source_file,
            ]
            try:
                result = subprocess.run(
                    cmd, cwd=prom_dir, capture_output=True, text=True, timeout=30
                )
                output = result.stdout
                lines = output.split("\n")
                output_lines = [
                    f"**Preprocessed `{source_file}`** ({len(lines)} lines)"
                ]
                # Show last 50 lines (most interesting)
                if len(lines) > 80:
                    output_lines.append(f"... ({len(lines) - 80} lines omitted)")
                output_lines.append("```c")
                output_lines.extend(lines[-80:])
                output_lines.append("```")
                if result.stderr:
                    output_lines.append("")
                    output_lines.append("**Warnings:**")
                    output_lines.append("```")
                    output_lines.extend(result.stderr.strip().split("\n")[-20:])
                    output_lines.append("```")
                return "\n".join(output_lines)
            except Exception as e:
                return f"Error: {e}"

        elif expression:
            # Evaluate a macro expression
            includes = ""
            if header:
                includes = f"#include <{header}>\n"

            test_code = f"{includes}RESULT = {expression}"

            cmd = [
                cc,
                "-E",
                "-",
                "-march=mips3",
                "-mabi=32",
                "-EB",
                "-G",
                "0",
                "-ffreestanding",
                "-D_STANDALONE",
                "-D_KERNEL",
                "-DIP32",
                "-DR4000",
                "-D_MIPSEB",
                "-D_PAGESZ=16384",
                "-D_LANGUAGE_C",
                "-I./compat",
                "-I./include/ip32",
                "-I./include",
                "-I./include/sys",
                "-include",
                "./compat/irix_compat.h",
            ]
            try:
                result = subprocess.run(
                    cmd,
                    cwd=prom_dir,
                    input=test_code,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                # Find the RESULT line in output
                for line in result.stdout.split("\n"):
                    line = line.strip()
                    if line.startswith("RESULT"):
                        return f"**`{expression}`** expands to:\n```\n{line}\n```"

                return f"Could not evaluate expression. Preprocessor output:\n```\n{result.stdout[-500:]}\n```\n\nStderr:\n```\n{result.stderr[-500:]}\n```"
            except Exception as e:
                return f"Error: {e}"

        else:
            return "Error: provide either 'file' or 'expression' parameter"

    # === VM Instance Management ===

    elif name == "vm_instance_create":
        inst_name = args.get("name", "")
        if not inst_name:
            return "Error: name is required"
        try:
            d = vm_instances.create_instance(
                name=inst_name,
                machine=args.get("machine", "indy"),
                ram_mb=args.get("ram_mb", 64),
                irix_version=args.get("irix_version", ""),
                description=args.get("description", ""),
                disk_size_mb=args.get("disk_size_mb", 2048),
            )
            # Apply optional launch defaults
            _dea = args.get("default_extra_args")
            _ds = args.get("default_snapshot")
            _hp = args.get("hostfwd_port")
            if _dea is not None or _ds is not None or _hp is not None:
                vm_instances.update_defaults(
                    inst_name,
                    default_extra_args=_dea,
                    default_snapshot=_ds,
                    hostfwd_port=_hp,
                )
            manifest = vm_instances.load_manifest(inst_name)
            return (
                f"**Instance created:** `{inst_name}`\n"
                f"**Directory:** `{d}`\n"
                f"**Disk:** `{d / 'disk.qcow2'}`\n"
                f"**NVRAM:** `{d / 'nvram.bin'}`\n"
                f"**Machine:** {manifest.get('machine', 'indy')}\n"
                f"**RAM:** {manifest.get('ram_mb', 64)}MB"
            )
        except Exception as e:
            return f"Error creating instance: {e}"

    elif name == "vm_instance_list":
        instances = vm_instances.list_instances()
        if not instances:
            return "No VM instances found. Use `vm_instance_create` or `vm_instance_migrate` to create one."
        lines = ["## VM Instances\n"]
        lines.append("| Name | Machine | IRIX | Snapshots | Disk | NVRAM |")
        lines.append("|------|---------|------|-----------|------|-------|")
        for inst in instances:
            disk_mb = inst["disk_bytes"] / (1024 * 1024) if inst["disk_bytes"] else 0
            lines.append(
                f"| `{inst['name']}` "
                f"| {inst['machine']} "
                f"| {inst['irix_version'] or '-'} "
                f"| {inst['snapshots']} "
                f"| {'%.0fMB' % disk_mb if inst['disk_exists'] else 'missing'} "
                f"| {'yes' if inst['has_nvram'] else 'no'} |"
            )
        if instances:
            lines.append("")
            for inst in instances:
                if inst.get("description"):
                    lines.append(f"- **{inst['name']}:** {inst['description']}")
        return "\n".join(lines)

    elif name == "vm_instance_info":
        inst_name = args.get("name", "")
        if not inst_name:
            return "Error: name is required"
        manifest = vm_instances.load_manifest(inst_name)
        if not manifest:
            return f"Error: instance '{inst_name}' not found or has no manifest"
        lines = [f"## Instance: `{inst_name}`\n"]
        lines.append(f"- **Created:** {manifest.get('created', 'unknown')}")
        lines.append(f"- **Machine:** {manifest.get('machine', 'unknown')}")
        lines.append(f"- **RAM:** {manifest.get('ram_mb', '?')}MB")
        lines.append(f"- **IRIX Version:** {manifest.get('irix_version', '-')}")
        lines.append(f"- **Disk Format:** {manifest.get('disk_format', 'qcow2')}")
        lines.append(f"- **Disk Size:** {manifest.get('disk_size_mb', '?')}MB")
        lines.append(f"- **Description:** {manifest.get('description', '-')}")
        disk_path = vm_instances.get_disk_path(inst_name)
        nvram_path = vm_instances.get_nvram_path(inst_name)
        lines.append(
            f"- **Disk Path:** `{disk_path}` ({'exists' if disk_path.exists() else 'missing'})"
        )
        lines.append(
            f"- **NVRAM Path:** `{nvram_path}` ({'exists' if nvram_path.exists() else 'not yet created'})"
        )
        # Show key NVRAM boot variables if NVRAM exists
        if nvram_path.exists():
            from sgi_mcp.nvram_utils import nvram_read

            nvars = nvram_read(nvram_path)
            if nvars:
                autoload = nvars.get("autoload", "?")
                osfile = nvars.get("osfile", "") or "(empty)"
                console = nvars.get("console", "?")
                osopts = nvars.get("osopts", "") or "(empty)"
                will_boot = "yes" if autoload == "Y" else "no"
                lines.append(f"\n### NVRAM Boot Config")
                lines.append(
                    f"- **autoload:** `{autoload}` — will autoboot: **{will_boot}**"
                )
                lines.append(f"- **osfile:** `{osfile}`")
                lines.append(f"- **console:** `{console}`")
                lines.append(f"- **osopts:** `{osopts}`")
        # Show default launch config
        lines.append(f"\n### Default Launch Config")
        dea = manifest.get("default_extra_args")
        ds = manifest.get("default_snapshot")
        hp = manifest.get("hostfwd_port")
        lines.append(
            f"- **default_extra_args:** `{dea}`"
            if dea
            else "- **default_extra_args:** (none set)"
        )
        lines.append(
            f"- **default_snapshot:** `{ds}`"
            if ds
            else "- **default_snapshot:** (none set)"
        )
        if hp:
            lines.append(
                f"- **hostfwd_port:** `{hp}` → forwarded to guest port 23 (telnet)"
            )
        else:
            lines.append("- **hostfwd_port:** (none set)")
        snapshots = manifest.get("snapshots", [])
        if snapshots:
            lines.append(f"\n### Snapshots ({len(snapshots)})\n")
            for s in snapshots:
                desc = f" — {s['description']}" if s.get("description") else ""
                hw = s.get("hardware")
                if hw:
                    plat = hw.get("platform", "?")
                    mtime = hw.get("qemu_mtime", 0)
                    build_id = hex(mtime)[-6:] if mtime else "?"
                    hw_tag = f" `[{plat}/{build_id}]`"
                else:
                    hw_tag = " `[no hw metadata]`"
                lines.append(f"- **`{s['name']}`** ({s.get('created', '?')}){hw_tag}{desc}")
        else:
            lines.append("\n*No snapshots recorded*")
        return "\n".join(lines)

    elif name == "vm_instance_delete":
        inst_name = args.get("name", "")
        if not inst_name:
            return "Error: name is required"
        _busy = _instance_disk_in_use(inst_name)
        if _busy:
            return (
                f"Error: instance '{inst_name}' is in use by running session '{_busy}'. "
                f"Stop it first (qemu_session_stop) — deleting a disk under a live VM "
                f"corrupts it."
            )
        if vm_instances.delete_instance(inst_name):
            return f"**Deleted instance:** `{inst_name}`"
        else:
            return f"Error: instance '{inst_name}' not found"

    elif name == "vm_instance_update":
        inst_name = args.get("name", "")
        if not inst_name:
            return "Error: name is required"
        manifest = vm_instances.load_manifest(inst_name)
        if not manifest:
            return f"Error: instance '{inst_name}' not found or has no manifest"
        changed = []
        _dea = args.get("default_extra_args")
        _ds = args.get("default_snapshot")
        _hp = args.get("hostfwd_port")
        _desc = args.get("description")
        if _dea is not None or _ds is not None or _hp is not None:
            vm_instances.update_defaults(
                inst_name,
                default_extra_args=_dea,
                default_snapshot=_ds,
                hostfwd_port=_hp,
            )
            if _dea is not None:
                changed.append(f"default_extra_args = `{_dea}`")
            if _ds is not None:
                changed.append(f"default_snapshot = `{_ds}`")
            if _hp is not None:
                changed.append(f"hostfwd_port = `{_hp}`")
        if _desc is not None:
            manifest = vm_instances.load_manifest(inst_name)
            manifest["description"] = _desc
            vm_instances.save_manifest(inst_name, manifest)
            changed.append(f"description = `{_desc}`")
        if not changed:
            return f"No fields to update for `{inst_name}`. Provide at least one of: default_extra_args, default_snapshot, hostfwd_port, description."
        lines = [f"**Updated instance:** `{inst_name}`"]
        for c in changed:
            lines.append(f"- {c}")
        if _ds is not None and _ds:
            lines.append(
                "\n**WARNING:** `default_snapshot` will auto-load on every session start. "
                "Loading snapshots across QEMU builds corrupts the qcow2 disk. "
                "Consider clearing it (`default_snapshot=\"\"`) and using `vm_instance_fork` instead."
            )
        return "\n".join(lines)

    elif name == "vm_instance_migrate":
        inst_name = args.get("name", "")
        disk_path = args.get("disk_path", "")
        if not inst_name:
            return "Error: name is required"
        if not disk_path:
            return "Error: disk_path is required"
        try:
            d = vm_instances.migrate_existing(
                name=inst_name,
                disk_path=disk_path,
                nvram_path=args.get("nvram_path"),
                machine=args.get("machine", "indy"),
                ram_mb=args.get("ram_mb", 64),
                irix_version=args.get("irix_version", ""),
                description=args.get("description", ""),
            )
            # Apply optional launch defaults
            _dea = args.get("default_extra_args")
            _ds = args.get("default_snapshot")
            _hp = args.get("hostfwd_port")
            if _dea is not None or _ds is not None or _hp is not None:
                vm_instances.update_defaults(
                    inst_name,
                    default_extra_args=_dea,
                    default_snapshot=_ds,
                    hostfwd_port=_hp,
                )
            manifest = vm_instances.load_manifest(inst_name)
            snap_count = len(manifest.get("snapshots", []))
            lines = [
                f"**Instance migrated:** `{inst_name}`",
                f"**Directory:** `{d}`",
                f"**Disk:** `{d / 'disk.qcow2'}`",
                f"**Snapshots detected:** {snap_count}",
            ]
            for s in manifest.get("snapshots", []):
                lines.append(f"  - `{s['name']}`")
            return "\n".join(lines)
        except FileNotFoundError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error migrating instance: {e}"

    elif name == "vm_instance_fork":
        from pathlib import Path
        source = args.get("source", "")
        fork_name = args.get("name", "")
        desc = args.get("description", f"Forked from {source}")

        if not source:
            return "Error: source is required"
        if not fork_name:
            return "Error: name is required"

        try:
            src_manifest = vm_instances.load_manifest(source)
            if not src_manifest:
                return f"Error: source instance '{source}' not found or has no manifest"

            src_disk = vm_instances.get_disk_path(source)
            if not src_disk.exists():
                return f"Error: source disk not found: {src_disk}"

            src_nvram = vm_instances.get_nvram_path(source)

            inst_dir = Path(vm_instances.INSTANCES_DIR) / fork_name
            if inst_dir.exists():
                return f"Error: instance '{fork_name}' already exists"
            inst_dir.mkdir(parents=True, exist_ok=False)

            new_disk = inst_dir / "disk.qcow2"
            new_nvram = inst_dir / "nvram.bin"

            # Thin-provisioned qcow2 backed by source disk (absolute path)
            qemu_img = str(_find_build_dir() / "qemu-img")
            r = subprocess.run(
                [qemu_img, "create",
                 "-b", str(src_disk.resolve()),
                 "-F", "qcow2", "-f", "qcow2",
                 str(new_disk)],
                capture_output=True, text=True
            )
            if r.returncode != 0:
                shutil.rmtree(inst_dir, ignore_errors=True)
                return f"Error: qemu-img create failed:\n{r.stderr}"

            if src_nvram.exists():
                shutil.copy(src_nvram, new_nvram)

            from datetime import datetime as _dt
            manifest = {
                "name": fork_name,
                "created": _dt.utcnow().isoformat() + "+00:00",
                "machine": src_manifest.get("machine", "indy"),
                "ram_mb": src_manifest.get("ram_mb", 256),
                "irix_version": src_manifest.get("irix_version", "6.5.5"),
                "disk_format": "qcow2",
                "disk_size_mb": src_manifest.get("disk_size_mb", 4096),
                "description": desc,
                "backing_instance": source,
                "snapshots": [],
                "default_extra_args": src_manifest.get("default_extra_args", ""),
                "default_snapshot": "",
                "hostfwd_port": None,
            }
            vm_instances.save_manifest(fork_name, manifest)

            lines = [
                f"**Forked:** `{source}` → `{fork_name}`",
                f"**Disk:** `{new_disk}` (backed by `{src_disk}`)",
                f"**NVRAM:** copied from source",
                f"**Description:** {desc}",
                f"",
                f"Use `vm_instance_reset(name=\"{fork_name}\")` to discard all changes and re-fork.",
            ]
            return "\n".join(lines)
        except Exception as e:
            return f"Error forking instance: {e}"

    elif name == "vm_instance_reset":
        from pathlib import Path
        reset_name = args.get("name", "")
        if not reset_name:
            return "Error: name is required"
        _busy = _instance_disk_in_use(reset_name)
        if _busy:
            return (
                f"Error: instance '{reset_name}' is in use by running session '{_busy}'. "
                f"Stop it first (qemu_session_stop) — recreating a disk under a live VM "
                f"corrupts it."
            )

        try:
            manifest = vm_instances.load_manifest(reset_name)
            if not manifest:
                return f"Error: instance '{reset_name}' not found or has no manifest"

            disk = vm_instances.get_disk_path(reset_name)
            if not disk.exists():
                return f"Error: disk not found: {disk}"

            # Get backing file from qcow2 metadata
            qemu_img = str(_find_build_dir() / "qemu-img")
            r = subprocess.run(
                [qemu_img, "info", "--output", "json", str(disk)],
                capture_output=True, text=True, check=True
            )
            info = json.loads(r.stdout)
            backing = info.get("backing-filename")
            if not backing:
                return "Error: instance has no backing file (not a forked instance — use vm_instance_fork to create a fork)"

            # Re-copy NVRAM from source if backing_instance is recorded
            source = manifest.get("backing_instance")
            if source:
                src_nvram = vm_instances.get_nvram_path(source)
                if src_nvram.exists():
                    shutil.copy(src_nvram, vm_instances.get_nvram_path(reset_name))

            # Recreate the thin disk
            disk.unlink()
            r2 = subprocess.run(
                [qemu_img, "create",
                 "-b", backing,
                 "-F", "qcow2", "-f", "qcow2",
                 str(disk)],
                capture_output=True, text=True
            )
            if r2.returncode != 0:
                return f"Error: qemu-img create failed:\n{r2.stderr}"

            # Fresh overlay of a (clean) backing — any prior dirty marker no
            # longer applies. This is the canonical recovery for a dirty disk.
            disk_safety.clear_dirty(str(disk))

            # Clear snapshots from manifest
            manifest["snapshots"] = []
            manifest["default_snapshot"] = ""
            vm_instances.save_manifest(reset_name, manifest)

            return f"Reset `{reset_name}`: fresh thin copy from `{backing}`\nAll previous changes discarded (dirty marker cleared)."
        except subprocess.CalledProcessError as e:
            return f"Error: qemu-img info failed: {e.stderr}"
        except Exception as e:
            return f"Error resetting instance: {e}"

    elif name == "qemu_copy_file":
        import re as _re
        import time as _time

        src_session_id = args.get("src_session", "")
        src_path = args.get("src_path", "")
        dst_session_id = args.get("dst_session", "")
        dst_path = args.get("dst_path", "")
        timeout = int(args.get("timeout", 60))

        for sid, label in [(src_session_id, "src_session"), (dst_session_id, "dst_session")]:
            if sid not in _qemu_sessions:
                available = ", ".join(_qemu_sessions.keys()) if _qemu_sessions else "none"
                return f"Error: {label} '{sid}' not found. Active sessions: {available}"
            if not _qemu_sessions[sid].is_running():
                return f"Error: {label} '{sid}' is no longer running"

        if not src_path:
            return "Error: src_path is required"
        if not dst_path:
            return "Error: dst_path is required"

        src_sess = _qemu_sessions[src_session_id]
        dst_sess = _qemu_sessions[dst_session_id]

        src_basename = os.path.basename(src_path)
        tmp_uu_src = "/tmp/_cp_src.uu"
        tmp_uu_dst = "/tmp/_cp_dst.uu"
        dst_dir = os.path.dirname(dst_path) or "/tmp"

        def _wait_sess(sess, pattern: str, to: float):
            end = _time.time() + to
            compiled = _re.compile(pattern)
            while _time.time() < end:
                _time.sleep(0.3)
                if not sess.is_running():
                    break
                with sess.buffer_lock:
                    cur = sess.output_buffer.decode("latin-1", errors="replace")
                if compiled.search(cur):
                    return True, sess.drain_buffer()
            return False, sess.drain_buffer()

        def _escape(s: str) -> str:
            s = s.replace("\\", "\\\\")
            s = s.replace("'", "'\"'\"'")
            return s

        def _build_printf(line: str, path: str, append: bool) -> str:
            escaped = _escape(line)
            redirect = ">>" if append else ">"
            return r"printf '%s\\n' '" + escaped + r"' " + redirect + r" " + path

        # Step 1: Switch source to POSIX sh and uuencode the file
        src_sess.send("exec sh\r")
        _wait_sess(src_sess, r"[#$]\s*$", 5)

        src_path_esc = _escape(src_path)
        src_sess.send(
            f"uuencode '{src_path_esc}' '{src_basename}' > {tmp_uu_src} "
            f"&& echo __UUENC_DONE__\r"
        )
        found, out = _wait_sess(src_sess, "__UUENC_DONE__", timeout)
        if not found:
            return f"Error: uuencode timed out on source:\n{out}"
        if "uuencode:" in out or "cannot open" in out or "No such" in out:
            return f"Error: uuencode failed on source:\n{out}"

        # Step 2: Cat the .uu file from source, collect between sentinels
        src_sess.send(f"echo __UU_START__ && cat {tmp_uu_src} && echo __UU_END__\r")
        end_time = _time.time() + timeout
        raw = ""
        while _time.time() < end_time:
            _time.sleep(0.3)
            with src_sess.buffer_lock:
                raw = src_sess.output_buffer.decode("latin-1", errors="replace")
            if "__UU_END__" in raw:
                break
        src_sess.drain_buffer()

        # Extract content between sentinels
        m = _re.search(r"__UU_START__\r?\n(.*?)__UU_END__", raw, _re.DOTALL)
        if not m:
            return f"Error: could not capture uuencoded data from source (no sentinel match).\nBuffer:\n{raw[-500:]}"

        uu_text = m.group(1)
        # Strip Windows-style CR artifacts
        uu_text = uu_text.replace("\r\n", "\n").replace("\r", "\n")
        uu_lines = [l for l in uu_text.split("\n") if l]

        if not uu_lines:
            return "Error: captured empty uuencoded data"

        # Step 3: Switch dest to POSIX sh and upload .uu file line by line
        dst_sess.send("exec sh\r")
        _wait_sess(dst_sess, r"[#$]\s*$", 5)

        batch_size = 25
        for batch_start in range(0, len(uu_lines), batch_size):
            batch_end = min(batch_start + batch_size, len(uu_lines))
            batch_num = batch_start // batch_size
            sentinel = f"__CPB_{batch_num}__"

            cmds = []
            for idx in range(batch_start, batch_end):
                cmds.append(_build_printf(uu_lines[idx], tmp_uu_dst, append=(idx > 0)))
            cmds.append(f"echo {sentinel}")

            full = r"\r".join(cmds) + r"\r"
            dst_sess.send(full)

            found, _ = _wait_sess(dst_sess, _re.escape(sentinel), timeout)
            if not found:
                return f"Error: timeout uploading batch {batch_num} to destination"

        # Step 4: uudecode on destination
        dst_dir_esc = _escape(dst_dir)
        dst_basename = os.path.basename(dst_path)
        dst_sess.send(
            f"cd '{dst_dir_esc}' && uudecode {tmp_uu_dst} "
            f"&& mv '{dst_basename}' '{_escape(dst_path)}' "
            f"&& echo __CP_DONE__\r"
        )
        found, out = _wait_sess(dst_sess, "__CP_DONE__", timeout)
        if not found or "uudecode:" in out:
            return f"Error: uudecode/move failed on destination:\n{out}"

        return (
            f"Copied `{src_path}` ({src_session_id}) → `{dst_path}` ({dst_session_id})\n"
            f"Via {len(uu_lines)} uuencoded lines."
        )

    # === Boot Harness Tools ===

    elif name == "harness_boot":

        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent))
        from pyirix_qemu.install.irix import full_install_attempt

        # Resolve instance to concrete disk path, NVRAM, and manifest defaults
        instance_name = args.get("instance")
        if instance_name:
            inst_disk = vm_instances.get_disk_path(instance_name)
            inst_nvram = vm_instances.get_nvram_path(instance_name)
            manifest = vm_instances.load_manifest(instance_name)
            if inst_disk.exists():
                args["disk"] = str(inst_disk)
            if manifest:
                args.setdefault("machine", manifest.get("machine", "indy"))
                args.setdefault("ram_mb", manifest.get("ram_mb", 64))

        disk = args.get("disk", str(Path(__file__).parent.parent / "irix_disk.qcow2"))
        cdrom = args.get("cdrom")
        version = args.get("version")
        machine = args.get("machine", "indy")
        ram_mb = args.get("ram_mb", 64)
        reload_miniroot = args.get("reload", False)
        max_wait_secs = args.get("max_wait", 600)
        debug_flags = args.get("debug_flags")
        repeat_threshold = args.get("repeat_threshold", 3)
        transcript_tail = args.get("transcript_tail", 60)

        session_kwargs = {}
        if debug_flags:
            session_kwargs["debug_flags"] = debug_flags
        if repeat_threshold is not None:
            session_kwargs["repeat_threshold"] = repeat_threshold

        # Inject NVRAM path and default_extra_args for instance
        if instance_name:
            inst_nvram = vm_instances.get_nvram_path(instance_name)
            extra_tokens = []
            if manifest:
                dea = manifest.get("default_extra_args", "")
                if dea:
                    import shlex

                    extra_tokens = shlex.split(dea)
            if inst_nvram.exists():
                nvram_args = ["-global", f"sgi-hpc3.nvram-file={inst_nvram}"]
                existing = session_kwargs.get("extra_args", [])
                session_kwargs["extra_args"] = extra_tokens + nvram_args + existing
            elif extra_tokens:
                existing = session_kwargs.get("extra_args", [])
                session_kwargs["extra_args"] = extra_tokens + existing

        result = full_install_attempt(
            disk_path=disk,
            cdrom_path=cdrom,
            version=version,
            machine=machine,
            ram_mb=ram_mb,
            reload=reload_miniroot,
            **session_kwargs,
        )

        lines = []
        status = "SUCCESS" if result["success"] else "FAILED"
        lines.append(f"## Boot Result: **{status}**\n")
        lines.append(f"- **Duration:** {result['duration']:.1f}s")
        lines.append(f"- **Machine:** {machine}")
        lines.append(f"- **IRIX version:** {version or '6.5'}")
        lines.append(f"- **Disk:** {disk}")
        if result["bail_reason"]:
            lines.append(f"- **Bail reason:** {result['bail_reason']}")
        lines.append(f"- **Transcript:** {len(result['transcript'])} chars\n")

        # Show tail of transcript
        tlines = result["transcript"].split("\n")
        if len(tlines) > transcript_tail:
            lines.append(f"*... ({len(tlines) - transcript_tail} lines omitted) ...*\n")
            tlines = tlines[-transcript_tail:]
        lines.append("```")
        lines.extend(tlines)
        lines.append("```")

        return "\n".join(lines)

    elif name == "harness_resume":

        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent))
        from pyirix_qemu.install.irix import iterate_from_snapshot

        # Resolve instance to concrete disk path, NVRAM, and manifest defaults
        instance_name = args.get("instance")
        if instance_name:
            inst_disk = vm_instances.get_disk_path(instance_name)
            inst_nvram = vm_instances.get_nvram_path(instance_name)
            manifest = vm_instances.load_manifest(instance_name)
            if inst_disk.exists():
                args["disk"] = str(inst_disk)
            if manifest:
                args.setdefault("machine", manifest.get("machine", "indy"))
                args.setdefault("ram_mb", manifest.get("ram_mb", 64))

        snapshot = args["snapshot"]
        disk = args.get("disk", str(Path(__file__).parent.parent / "irix_disk.qcow2"))
        cdrom = args.get("cdrom")
        version = args.get("version")
        machine = args.get("machine", "indy")
        ram_mb = args.get("ram_mb", 64)
        transcript_tail = args.get("transcript_tail", 60)

        # Inject NVRAM path and default_extra_args for instance
        session_kwargs = {}
        if instance_name:
            inst_nvram = vm_instances.get_nvram_path(instance_name)
            extra_tokens = []
            if manifest:
                dea = manifest.get("default_extra_args", "")
                if dea:
                    import shlex

                    extra_tokens = shlex.split(dea)
            if inst_nvram.exists():
                nvram_args = ["-global", f"sgi-hpc3.nvram-file={inst_nvram}"]
                session_kwargs["extra_args"] = extra_tokens + nvram_args
            elif extra_tokens:
                session_kwargs["extra_args"] = extra_tokens

        result = iterate_from_snapshot(
            snapshot_name=snapshot,
            disk_path=disk,
            cdrom_path=cdrom,
            version=version,
            machine=machine,
            ram_mb=ram_mb,
            **session_kwargs,
        )

        lines = []
        status = "SUCCESS" if result["success"] else "FAILED"
        lines.append(f"## Resume Result: **{status}**\n")
        lines.append(f"- **Snapshot:** {snapshot}")
        lines.append(f"- **Duration:** {result['duration']:.1f}s")
        if result["bail_reason"]:
            lines.append(f"- **Bail reason:** {result['bail_reason']}")
        lines.append(f"- **Transcript:** {len(result['transcript'])} chars\n")

        tlines = result["transcript"].split("\n")
        if len(tlines) > transcript_tail:
            lines.append(f"*... ({len(tlines) - transcript_tail} lines omitted) ...*\n")
            tlines = tlines[-transcript_tail:]
        lines.append("```")
        lines.extend(tlines)
        lines.append("```")

        return "\n".join(lines)

    elif name == "harness_disk":

        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent))
        from pyirix_qemu import disk_manager

        action = args["action"]

        if action == "create":
            path = args.get(
                "path", str(Path(__file__).parent.parent / "irix_disk.qcow2")
            )
            size_mb = args.get("size_mb", 2048)
            fmt = args.get("format", "qcow2")
            result_path = disk_manager.create_disk(path, size_mb, fmt)
            return f"Created {fmt} disk: **{result_path}** ({size_mb} MB)"

        elif action == "convert":
            src = args.get("path", "")
            if not src:
                return "Error: 'path' is required for convert"
            dst = args.get("dst")
            fmt = args.get("format", "qcow2")
            result_path = disk_manager.convert_disk(src, dst, fmt)
            return f"Converted **{src}** -> **{result_path}** ({fmt})"

        elif action == "info":
            path = args.get("path", "")
            if not path:
                return "Error: 'path' is required for info"
            info = disk_manager.disk_info(path)
            lines = [f"## Disk Info: {path}\n"]
            for k, v in info.items():
                lines.append(f"- **{k}:** {v}")
            return "\n".join(lines)

        elif action == "snapshots":
            path = args.get("path", "")
            if not path:
                return "Error: 'path' is required for snapshots"
            snaps = disk_manager.list_snapshots(path)
            if not snaps:
                return f"No snapshots in {path}"
            lines = [f"## Snapshots in {path}\n"]
            lines.append("| ID | Tag | VM Size | Date | VM Clock |")
            lines.append("|---|---|---|---|---|")
            for s in snaps:
                lines.append(
                    f"| {s['id']} | {s['tag']} | {s['vm_size']} | {s['date']} | {s['vm_clock']} |"
                )
            return "\n".join(lines)

        elif action == "delete_snapshot":
            path = args.get("path", "")
            snap_name = args.get("snapshot_name", "")
            if not path or not snap_name:
                return "Error: 'path' and 'snapshot_name' required for delete_snapshot"
            disk_manager.delete_snapshot(path, snap_name)
            return f"Deleted snapshot **{snap_name}** from {path}"

        elif action == "overlay":
            backing = args.get("backing", "")
            path = args.get("path", "/tmp/irix_test_overlay.qcow2")
            if not backing:
                return "Error: 'backing' is required for overlay"
            result_path = disk_manager.create_backed_disk(backing, path)
            return f"Created overlay: **{result_path}** (backing: {backing})"

        else:
            return f"Error: unknown action '{action}'. Use: create, convert, info, snapshots, delete_snapshot, overlay"

    elif name == "qemu_scsi_trace":



        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent))
        from pyirix_qemu.boot_harness import QEMUSession
        from .scsi_parser import parse_scsi_log
        from .formatters import format_scsi_log_summary

        machine = args.get("machine", "indy")
        ram_mb = args.get("ram_mb", 64)
        max_wait = args.get("max_wait", 120)
        errors_only = args.get("errors_only", False)
        max_commands = args.get("max_commands", 0)
        save_log = args.get("save_log")

        # Build SCSI drive list
        scsi_drives = []
        disk = args.get("disk")
        cdrom = args.get("cdrom")
        if disk:
            scsi_drives.append(disk)
        if cdrom:
            scsi_drives.append(f"{cdrom}:cdrom")

        # Build extra args
        extra_args = []
        extra_args_str = args.get("extra_args", "")
        if extra_args_str:
            extra_args = extra_args_str.split()

        # Create temp file for debug log
        debug_log_fd, debug_log_path = tempfile.mkstemp(
            prefix="qemu_scsi_trace_", suffix=".log"
        )
        os.close(debug_log_fd)

        try:
            with QEMUSession(
                machine=machine,
                ram_mb=ram_mb,
                scsi_drives=scsi_drives,
                debug_flags="unimp",
                debug_log_path=debug_log_path,
                extra_args=extra_args,
            ) as q:
                # Wait for boot to complete or timeout
                q.wait_for(
                    "System Maintenance Menu|Inst>|PANIC", timeout=10, max_wait=max_wait
                )

            # Read the debug log
            debug_content = q.debug_log_content

            # Save log if requested
            if save_log:
                Path(save_log).write_text(debug_content)

            # Parse SCSI events
            summary = parse_scsi_log(
                debug_content,
                max_commands=max_commands,
                errors_only=errors_only,
            )

            output_lines = [
                f"**Machine:** {machine}",
                f"**RAM:** {ram_mb}MB",
            ]
            if scsi_drives:
                output_lines.append(f"**SCSI drives:** {', '.join(scsi_drives)}")
            if save_log:
                output_lines.append(f"**Log saved to:** `{save_log}`")
            output_lines.append("")
            output_lines.append(format_scsi_log_summary(summary))

            return "\n".join(output_lines)

        except Exception as e:
            return f"Error running SCSI trace: {e}"
        finally:
            try:
                os.unlink(debug_log_path)
            except OSError:
                pass

    elif name == "scsi_log_parse":
        from pathlib import Path
        from .scsi_parser import parse_scsi_log
        from .formatters import format_scsi_log_summary

        file_path = args.get("file", "")
        if not file_path:
            return "Error: 'file' parameter is required"

        path = Path(file_path)
        if not path.is_absolute():
            path = Path("/workspace") / file_path
        if not path.exists():
            return f"Error: file not found: {file_path}"

        try:
            log_content = path.read_text(errors="replace")
        except Exception as e:
            return f"Error reading file: {e}"

        errors_only = args.get("errors_only", False)
        max_commands = args.get("max_commands", 0)
        target_filter = args.get("target_filter")
        opcode_filter = args.get("opcode_filter")

        summary = parse_scsi_log(
            log_content,
            max_commands=max_commands,
            errors_only=errors_only,
            target_filter=target_filter,
            opcode_filter=opcode_filter,
        )

        output_lines = [
            f"**File:** `{path}`",
            f"**Size:** {len(log_content):,} bytes",
            "",
            format_scsi_log_summary(summary),
        ]
        return "\n".join(output_lines)

    elif name == "qemu_boot_milestones":




        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent))
        from pyirix_qemu.boot_harness import QEMUSession
        from .boot_milestones import detect_milestones
        from .scsi_parser import parse_scsi_log
        from .formatters import format_boot_report, format_scsi_log_summary

        machine = args.get("machine", "indy")
        ram_mb = args.get("ram_mb", 64)
        max_wait = args.get("max_wait", 120)
        reload_miniroot = args.get("reload", False)

        # Build SCSI drive list
        scsi_drives = []
        disk = args.get("disk")
        cdrom = args.get("cdrom")
        if disk:
            scsi_drives.append(disk)
        if cdrom:
            scsi_drives.append(f"{cdrom}:cdrom")

        # Build extra args
        extra_args = []
        extra_args_str = args.get("extra_args", "")
        if extra_args_str:
            extra_args = extra_args_str.split()

        # Create temp file for debug log
        debug_log_fd, debug_log_path = tempfile.mkstemp(
            prefix="qemu_boot_ms_", suffix=".log"
        )
        os.close(debug_log_fd)

        start_time = time.time()

        try:
            with QEMUSession(
                machine=machine,
                ram_mb=ram_mb,
                scsi_drives=scsi_drives,
                debug_flags="unimp",
                debug_log_path=debug_log_path,
                extra_args=extra_args,
            ) as q:
                # If we have SCSI drives, do interactive boot
                if scsi_drives:
                    # Wait for System Maintenance Menu
                    result = q.wait_for(
                        "Option|System Maintenance", timeout=10, max_wait=max_wait
                    )
                    if result.matched:
                        # Select option 2 (install system software)
                        q.send("2\r")
                        result = q.wait_for(
                            "enter.*to start|press.*enter",
                            timeout=15,
                            max_wait=max_wait,
                        )
                        if result.matched:
                            q.send("\r")
                            result = q.wait_for(
                                "press.*enter|c, f, r", timeout=10, max_wait=max_wait
                            )
                            if result.matched:
                                if reload_miniroot:
                                    q.send("r\r")
                                else:
                                    q.send("c\r")
                                # Wait for boot to progress
                                q.wait_for(
                                    "Inst>|PANIC|panic:", timeout=30, max_wait=max_wait
                                )
                else:
                    # Just boot PROM
                    q.wait_for(
                        "System Maintenance Menu|PANIC", timeout=10, max_wait=max_wait
                    )

                serial_output = q.transcript
                debug_content = ""

            # Read debug log after session closes
            debug_content = q.debug_log_content

            # Detect milestones
            report = detect_milestones(
                serial_output,
                start_time=start_time,
                debug_log=debug_content,
            )

            output_lines = [
                f"**Machine:** {machine}",
                f"**RAM:** {ram_mb}MB",
            ]
            if scsi_drives:
                output_lines.append(f"**SCSI drives:** {', '.join(scsi_drives)}")
            output_lines.append("")
            output_lines.append(format_boot_report(report))

            # Add brief SCSI summary if there were SCSI errors
            if debug_content and report.scsi_error_summary:
                scsi_summary = parse_scsi_log(debug_content, errors_only=True)
                if scsi_summary.failed_commands > 0:
                    output_lines.append("")
                    output_lines.append(
                        f"*({scsi_summary.failed_commands} SCSI errors total — "
                        f"use `scsi_log_parse` or `qemu_scsi_trace` for full details)*"
                    )

            return "\n".join(output_lines)

        except Exception as e:
            return f"Error running boot milestones: {e}"
        finally:
            try:
                os.unlink(debug_log_path)
            except OSError:
                pass

    elif name == "harness_install":

        import importlib
        from pathlib import Path
        from io import StringIO

        # Reload vm_instances so binary-finder picks up the current platform.
        # The MCP server process may have started before build-linux was added
        # to the search order, so ensure we use the latest code on every call.
        importlib.reload(sys.modules["sgi_mcp.vm_instances"])

        version = args.get("version", "6.5")
        instance_name = args.get("instance")
        verify_only = args.get("verify_only", False)

        # If instance specified, create instance dir and use its paths
        if instance_name:
            inst_dir = vm_instances.create_instance(
                name=instance_name,
                machine=args.get("machine", "indy"),
                ram_mb=args.get("ram_mb", 64),
                irix_version=version,
                description=f"IRIX {version} installed via harness_install",
                disk_size_mb=args.get("disk_size_mb", 2048),
            )
            disk = str(vm_instances.get_disk_path(instance_name))
        else:
            disk = args.get(
                "disk", str(Path(__file__).parent.parent / "irix_disk.qcow2")
            )

        sys.path.insert(0, str(Path(__file__).parent.parent))

        # Capture install_irix log output
        log_lines = []
        import importlib
        import pyirix_qemu.disk_manager

        importlib.reload(pyirix_qemu.disk_manager)
        import pyirix_qemu.boot_harness

        importlib.reload(pyirix_qemu.boot_harness)
        import pyirix_qemu.install.irix as installer

        importlib.reload(installer)

        # Monkey-patch the log function to capture output
        orig_log = installer.log

        def _capture_log(msg):
            log_lines.append(msg)
            orig_log(msg)

        installer.log = _capture_log

        disk_size = args.get("disk_size_mb", 2048)
        install_ram = args.get("ram_mb", 64)
        conflict_mode = args.get("conflict_mode", "auto")
        conflict_resolutions = args.get("conflict_resolutions")
        result_data = None
        try:
            install_level = args.get("install_level", "standard")
            inst_debug = args.get("inst_debug", False)
            result_data = installer.install_irix(
                version,
                disk_path=disk,
                verify_only=verify_only,
                instance=instance_name,
                disk_size_mb=disk_size,
                ram_mb=install_ram,
                conflict_mode=conflict_mode,
                conflict_resolutions=conflict_resolutions,
                install_level=install_level,
                inst_debug=inst_debug,
            )
            success = True
            error_msg = ""
        except SystemExit as e:
            success = False
            error_msg = f"Install failed (exit code {e.code})"
        except Exception as e:
            success = False
            error_msg = str(e)
        finally:
            installer.log = orig_log

        output_lines = []

        if conflict_mode == "collect" and result_data:
            # Conflict collection mode — return structured conflict data
            output_lines.append(f"## IRIX {version} Conflict Collection: **DONE**")
            output_lines.append(f"**Disk:** `{disk}`")
            if instance_name:
                output_lines.append(f"**Instance:** `{instance_name}`")
                inst_dir = str(
                    Path(__file__).parent.parent / "vm_instances" / instance_name
                )
                output_lines.append(f"**Conflicts JSON:** `{inst_dir}/conflicts.json`")
                output_lines.append(f"**Raw text:** `{inst_dir}/conflicts_raw.txt`")
                output_lines.append(f"**Snapshot:** `pre_conflict_resolution`")
            output_lines.append("")

            # Summarize conflicts
            conflicts = result_data
            by_type = {}
            for c in conflicts:
                by_type.setdefault(c.get("type", "unknown"), []).append(c)

            output_lines.append(f"**Total conflicts:** {len(conflicts)}")
            for ctype, clist in sorted(by_type.items()):
                output_lines.append(f"- **{ctype}:** {len(clist)}")
            output_lines.append("")
            output_lines.append("**Log:**")
            output_lines.append("```")
            output_lines.extend(log_lines[-40:])
            output_lines.append("```")

        elif success:
            output_lines.append(f"## IRIX {version} Installation: **SUCCESS**")
            output_lines.append(f"**Disk:** `{disk}`")
            if instance_name:
                output_lines.append(f"**Instance:** `{instance_name}`")
            output_lines.append("")
            output_lines.append("**Log:**")
            output_lines.append("```")
            output_lines.extend(log_lines[-60:])
            output_lines.append("```")
        else:
            output_lines.append(f"## IRIX {version} Installation: **FAILED**")
            output_lines.append(f"**Error:** {error_msg}")
            output_lines.append(f"**Disk:** `{disk}`")
            if instance_name:
                output_lines.append(f"**Instance:** `{instance_name}`")
            output_lines.append("")
            output_lines.append("**Log:**")
            output_lines.append("```")
            output_lines.extend(log_lines[-60:])
            output_lines.append("```")

        return "\n".join(output_lines)

    elif name == "harness_addon":

        from pathlib import Path

        instance_name = args.get("instance")
        addon_image = args.get("addon_image")
        addon_name = args.get("addon_name", "addon")
        snapshot_name = args.get("snapshot_name")
        machine = args.get("machine", "indy")
        ram_mb = args.get("ram_mb", 256)
        addon_dirs = args.get("addon_dirs")
        categories = args.get("categories")
        package_name = args.get("package_name")
        version = args.get("version", "6.5")

        # Resolve disk path, NVRAM, and manifest defaults from instance
        nvram_extra = []
        if instance_name:
            disk = str(vm_instances.get_disk_path(instance_name))
            inst_nvram = vm_instances.get_nvram_path(instance_name)
            manifest = vm_instances.load_manifest(instance_name)
            if inst_nvram.exists():
                nvram_extra = ["-global", f"sgi-hpc3.nvram-file={inst_nvram}"]
            if manifest:
                machine = manifest.get("machine", machine)
                ram_mb = manifest.get("ram_mb", ram_mb)
                dea = manifest.get("default_extra_args", "")
                if dea:
                    import shlex

                    nvram_extra = shlex.split(dea) + nvram_extra
        else:
            disk = args.get("base_disk")
            if not disk:
                return "Error: either 'instance' or 'base_disk' must be provided"

        # Resolve addon_image — explicit path, or auto-discover
        if addon_image:
            addon_path = Path(addon_image)
            if not addon_path.is_absolute():
                addon_path = Path(__file__).parent.parent / addon_image
            addon_image = str(addon_path)
        elif package_name or categories:
            # Auto-discover via image catalog
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from pyirix_qemu.catalog.images import scan_software_library

            catalog = scan_software_library()

            if package_name:
                matches = catalog.find_package(package_name, version=version)
                if not matches:
                    return f"Error: no disc image found containing package '{package_name}'"
                matches.sort(key=lambda m: (m.is_combo, m.product_count), reverse=True)
                addon_image = matches[0].path
                addon_name = addon_name if addon_name != "addon" else package_name
            elif categories:
                images = catalog.get_install_set(version, categories)
                if not images:
                    return f"Error: no images found for categories: {categories}"
                addon_image = images[0].path
                addon_name = (
                    addon_name if addon_name != "addon" else images[0].display_name
                )
        else:
            return "Error: must specify addon_image, categories, or package_name"

        sys.path.insert(0, str(Path(__file__).parent.parent))

        # Capture log output
        log_lines = []
        import importlib
        import pyirix_qemu.disk_manager

        importlib.reload(pyirix_qemu.disk_manager)
        import pyirix_qemu.boot_harness

        importlib.reload(pyirix_qemu.boot_harness)
        import pyirix_qemu.install.irix as installer

        importlib.reload(installer)

        orig_log = installer.log
        orig_fail = installer.fail

        def _capture_log(msg):
            log_lines.append(msg)
            orig_log(msg)

        def _capture_fail(msg):
            log_lines.append(f"FAIL: {msg}")
            orig_fail(msg)

        installer.log = _capture_log
        installer.fail = _capture_fail

        try:
            installer.install_addon(
                base_disk=disk,
                addon_image=addon_image,
                output_disk=None,  # In-place mode
                addon_name=addon_name,
                machine=machine,
                snapshot_name=snapshot_name,
                ram_mb=ram_mb,
                addon_dirs=addon_dirs,
                extra_args=nvram_extra if nvram_extra else None,
            )
            success = True
            error_msg = ""
        except SystemExit as e:
            success = False
            error_msg = f"Addon install failed (exit code {e.code})"
        except Exception as e:
            success = False
            error_msg = f"{type(e).__name__}: {e}"
        finally:
            installer.log = orig_log
            installer.fail = orig_fail

        # Record snapshot in instance manifest
        if success and snapshot_name and instance_name:
            try:
                _bin = str(_find_qemu_binary(_find_build_dir()))
                _mtime = 0
                try:
                    _mtime = int(Path(_bin).stat().st_mtime)
                except OSError:
                    pass
                _hw = {
                    "platform": platform.system().lower(),
                    "qemu_binary": _bin,
                    "qemu_mtime": _mtime,
                    "machine": args.get("machine", "indy"),
                    "extra_args": args.get("extra_args", ""),
                }
                vm_instances.add_snapshot(
                    instance_name, snapshot_name, f"After installing {addon_name}",
                    hardware=_hw,
                )
            except Exception:
                pass

        output_lines = []
        if success:
            output_lines.append(f"## Addon Install: **SUCCESS**")
            output_lines.append(f"**Addon:** {addon_name}")
            output_lines.append(f"**Disk:** `{disk}`")
            if instance_name:
                output_lines.append(f"**Instance:** `{instance_name}`")
            if snapshot_name:
                output_lines.append(f"**Snapshot:** `{snapshot_name}`")
            output_lines.append("")
            output_lines.append("**Log:**")
            output_lines.append("```")
            output_lines.extend(log_lines[-40:])
            output_lines.append("```")
        else:
            output_lines.append(f"## Addon Install: **FAILED**")
            output_lines.append(f"**Error:** {error_msg}")
            output_lines.append(f"**Addon:** {addon_name}")
            output_lines.append(f"**Disk:** `{disk}`")
            output_lines.append("")
            output_lines.append("**Log:**")
            output_lines.append("```")
            output_lines.extend(log_lines[-60:])
            output_lines.append("```")

        return "\n".join(output_lines)

    elif name == "harness_addon_live":

        from pathlib import Path

        session_id = args.get("session_id")
        addon_image = args.get("addon_image")
        categories = args.get("categories")
        package_name = args.get("package_name")
        method = args.get("method", "serial")
        host = args.get("host", "localhost")
        port = args.get("port", 2323)
        version = args.get("version", "6.5")

        sys.path.insert(0, str(Path(__file__).parent.parent))

        # Capture log output
        log_lines = []
        import importlib
        import pyirix_qemu.install.irix as installer

        importlib.reload(installer)

        orig_log = installer.log
        orig_fail = installer.fail

        def _capture_log(msg):
            log_lines.append(msg)
            orig_log(msg)

        def _capture_fail(msg):
            log_lines.append(f"FAIL: {msg}")
            raise RuntimeError(msg)

        installer.log = _capture_log
        installer.fail = _capture_fail

        try:
            if method == "serial":
                if not session_id:
                    return "Error: session_id required for serial method"

                # Get the session's QEMUSession object
                from sgi_mcp.sessions import get_session

                sess_info = get_session(session_id)
                if not sess_info:
                    return f"Error: session '{session_id}' not found"

                # For serial, we need to interact via qemu_session_send
                # The live addon function needs a QEMUSession-like object
                from pyirix_qemu.install.irix import IRIXShell

                shell = IRIXShell(method="serial", session_id=session_id)

                # Create a thin wrapper that uses session_send/expect
                class SessionProxy:
                    def __init__(self, sid, send_func):
                        self._sid = sid
                        self._send = send_func
                        self.transcript = ""

                    def send(self, text):
                        self._send(self._sid, text)

                    def wait_for(self, pattern, timeout=5, max_wait=120):
                        import re as _re
                        import time as _time

                        buf = ""
                        deadline = _time.time() + max_wait
                        compiled = _re.compile(pattern)
                        while _time.time() < deadline:
                            chunk = self._send(
                                self._sid,
                                "",
                                expect=pattern,
                                timeout=min(timeout, deadline - _time.time()),
                            )
                            buf += chunk
                            if compiled.search(buf):

                                class R:
                                    pass

                                r = R()
                                r.matched = True
                                r.output = buf
                                r.bail_reason = None
                                return r

                        class R:
                            pass

                        r = R()
                        r.matched = False
                        r.output = buf
                        r.bail_reason = "timeout"
                        return r

                # The serial path for live addon is complex — for now,
                # guide users to use telnet method for live installs
                return (
                    "Error: Serial live addon not yet fully implemented. "
                    "Use method='telnet' with a session that has "
                    "network forwarding (-nic user,model=sgi-hpc3,"
                    "hostfwd=tcp::2323-10.0.2.15:23)"
                )

            elif method == "telnet":
                installer.install_addon_live(
                    addon_image=addon_image,
                    addon_categories=categories,
                    package_name=package_name,
                    method="telnet",
                    host=host,
                    port=port,
                    version=version,
                )
                success = True
                error_msg = ""
            else:
                return f"Error: unknown method '{method}'"

        except RuntimeError as e:
            success = False
            error_msg = str(e)
        except Exception as e:
            success = False
            error_msg = f"{type(e).__name__}: {e}"
        finally:
            installer.log = orig_log
            installer.fail = orig_fail

        output_lines = []
        if success:
            output_lines.append("## Live Addon Install: **SUCCESS**")
            if package_name:
                output_lines.append(f"**Package:** {package_name}")
            if addon_image:
                output_lines.append(f"**Image:** `{addon_image}`")
            output_lines.append(f"**Method:** {method}")
        else:
            output_lines.append("## Live Addon Install: **FAILED**")
            output_lines.append(f"**Error:** {error_msg}")

        output_lines.append("")
        output_lines.append("**Log:**")
        output_lines.append("```")
        output_lines.extend(log_lines[-40:])
        output_lines.append("```")

        return "\n".join(output_lines)

    # ── IRIX Kernel Introspection Tools ──────────────────────────────

    elif name == "irix_kernel_symbols":







        from pathlib import Path

        source = args.get("source", "ram")
        filter_pattern = args.get("filter", "")
        save_to = args.get("save_to", "")
        max_symbols = args.get("max_symbols", 200)

        def _parse_elf_symbols(data: bytes) -> list:
            """Parse ELF symbol table from raw bytes. Handles MIPS32 big-endian ELF."""
            if len(data) < 52:
                return []
            # Check ELF magic
            if data[:4] != b"\x7fELF":
                return []

            ei_class = data[4]  # 1=32-bit, 2=64-bit
            ei_data = data[5]  # 1=LE, 2=BE
            is_64 = ei_class == 2
            is_be = ei_data == 2
            endian = ">" if is_be else "<"

            if is_64:
                # ELF64 header
                e_shoff = struct.unpack(f"{endian}Q", data[40:48])[0]
                e_shentsize = struct.unpack(f"{endian}H", data[58:60])[0]
                e_shnum = struct.unpack(f"{endian}H", data[60:62])[0]
                e_shstrndx = struct.unpack(f"{endian}H", data[62:64])[0]
            else:
                # ELF32 header
                e_shoff = struct.unpack(f"{endian}I", data[32:36])[0]
                e_shentsize = struct.unpack(f"{endian}H", data[46:48])[0]
                e_shnum = struct.unpack(f"{endian}H", data[48:50])[0]
                e_shstrndx = struct.unpack(f"{endian}H", data[50:52])[0]

            if e_shoff == 0 or e_shnum == 0:
                return []
            if e_shoff + e_shnum * e_shentsize > len(data):
                return []

            # Parse section headers to find .symtab and .strtab
            symtab_sh = None
            strtab_sh = None

            # First get section header string table for names
            if e_shstrndx < e_shnum:
                shstr_off = e_shoff + e_shstrndx * e_shentsize
                if is_64:
                    shstr_offset = struct.unpack(
                        f"{endian}Q", data[shstr_off + 24 : shstr_off + 32]
                    )[0]
                    shstr_size = struct.unpack(
                        f"{endian}Q", data[shstr_off + 32 : shstr_off + 40]
                    )[0]
                else:
                    shstr_offset = struct.unpack(
                        f"{endian}I", data[shstr_off + 16 : shstr_off + 20]
                    )[0]
                    shstr_size = struct.unpack(
                        f"{endian}I", data[shstr_off + 20 : shstr_off + 24]
                    )[0]
                shstrtab = data[shstr_offset : shstr_offset + shstr_size]
            else:
                shstrtab = b""

            for i in range(e_shnum):
                sh_off = e_shoff + i * e_shentsize
                if is_64:
                    sh_name = struct.unpack(f"{endian}I", data[sh_off : sh_off + 4])[0]
                    sh_type = struct.unpack(
                        f"{endian}I", data[sh_off + 4 : sh_off + 8]
                    )[0]
                    sh_offset = struct.unpack(
                        f"{endian}Q", data[sh_off + 24 : sh_off + 32]
                    )[0]
                    sh_size = struct.unpack(
                        f"{endian}Q", data[sh_off + 32 : sh_off + 40]
                    )[0]
                    sh_link = struct.unpack(
                        f"{endian}I", data[sh_off + 40 : sh_off + 44]
                    )[0]
                    sh_entsize = struct.unpack(
                        f"{endian}Q", data[sh_off + 56 : sh_off + 64]
                    )[0]
                else:
                    sh_name = struct.unpack(f"{endian}I", data[sh_off : sh_off + 4])[0]
                    sh_type = struct.unpack(
                        f"{endian}I", data[sh_off + 4 : sh_off + 8]
                    )[0]
                    sh_offset = struct.unpack(
                        f"{endian}I", data[sh_off + 16 : sh_off + 20]
                    )[0]
                    sh_size = struct.unpack(
                        f"{endian}I", data[sh_off + 20 : sh_off + 24]
                    )[0]
                    sh_link = struct.unpack(
                        f"{endian}I", data[sh_off + 24 : sh_off + 28]
                    )[0]
                    sh_entsize = struct.unpack(
                        f"{endian}I", data[sh_off + 36 : sh_off + 40]
                    )[0]

                # Get section name
                sec_name = ""
                if sh_name < len(shstrtab):
                    end = (
                        shstrtab.index(b"\x00", sh_name)
                        if b"\x00" in shstrtab[sh_name:]
                        else len(shstrtab)
                    )
                    sec_name = shstrtab[sh_name:end].decode("ascii", errors="replace")

                if sh_type == 2:  # SHT_SYMTAB
                    symtab_sh = (sh_offset, sh_size, sh_link, sh_entsize, sec_name)
                elif sh_type == 3 and sec_name == ".strtab":  # SHT_STRTAB
                    strtab_sh = (sh_offset, sh_size)

            if not symtab_sh:
                return []

            sym_offset, sym_size, sym_strtab_idx, sym_entsize, _ = symtab_sh

            # Get the string table linked from symtab
            if sym_strtab_idx < e_shnum:
                str_sh_off = e_shoff + sym_strtab_idx * e_shentsize
                if is_64:
                    str_offset = struct.unpack(
                        f"{endian}Q", data[str_sh_off + 24 : str_sh_off + 32]
                    )[0]
                    str_size = struct.unpack(
                        f"{endian}Q", data[str_sh_off + 32 : str_sh_off + 40]
                    )[0]
                else:
                    str_offset = struct.unpack(
                        f"{endian}I", data[str_sh_off + 16 : str_sh_off + 20]
                    )[0]
                    str_size = struct.unpack(
                        f"{endian}I", data[str_sh_off + 20 : str_sh_off + 24]
                    )[0]
                strtab_data = data[str_offset : str_offset + str_size]
            elif strtab_sh:
                strtab_data = data[strtab_sh[0] : strtab_sh[0] + strtab_sh[1]]
            else:
                return []

            if sym_entsize == 0:
                sym_entsize = 24 if is_64 else 16

            symbols = []
            num_syms = sym_size // sym_entsize
            for i in range(num_syms):
                off = sym_offset + i * sym_entsize
                if off + sym_entsize > len(data):
                    break

                if is_64:
                    st_name = struct.unpack(f"{endian}I", data[off : off + 4])[0]
                    st_info = data[off + 4]
                    st_other = data[off + 5]
                    st_shndx = struct.unpack(f"{endian}H", data[off + 6 : off + 8])[0]
                    st_value = struct.unpack(f"{endian}Q", data[off + 8 : off + 16])[0]
                    st_size = struct.unpack(f"{endian}Q", data[off + 16 : off + 24])[0]
                else:
                    st_name = struct.unpack(f"{endian}I", data[off : off + 4])[0]
                    st_value = struct.unpack(f"{endian}I", data[off + 4 : off + 8])[0]
                    st_size = struct.unpack(f"{endian}I", data[off + 8 : off + 12])[0]
                    st_info = data[off + 12]
                    st_other = data[off + 13]
                    st_shndx = struct.unpack(f"{endian}H", data[off + 14 : off + 16])[0]

                # Get symbol name
                if st_name < len(strtab_data):
                    end = (
                        strtab_data.index(b"\x00", st_name)
                        if b"\x00" in strtab_data[st_name:]
                        else len(strtab_data)
                    )
                    sym_name = strtab_data[st_name:end].decode(
                        "ascii", errors="replace"
                    )
                else:
                    sym_name = ""

                if not sym_name or st_shndx == 0:  # Skip unnamed and undefined
                    continue

                sym_type = st_info & 0xF
                sym_bind = (st_info >> 4) & 0xF
                type_name = {
                    0: "NOTYPE",
                    1: "OBJECT",
                    2: "FUNC",
                    3: "SECTION",
                    4: "FILE",
                }.get(sym_type, f"TYPE{sym_type}")
                bind_name = {0: "LOCAL", 1: "GLOBAL", 2: "WEAK"}.get(
                    sym_bind, f"BIND{sym_bind}"
                )

                symbols.append(
                    {
                        "name": sym_name,
                        "address": st_value,
                        "size": st_size,
                        "type": type_name,
                        "bind": bind_name,
                    }
                )

            return symbols

        if source == "ram":
            # Boot QEMU and read kernel from guest RAM
            cmd, serial_sock_path, monitor_sock_path, tmpdir, prom_name, err = (
                _build_qemu_launch(args)
            )
            if err:
                return err

            boot_wait = args.get("boot_wait", 120)
            ram_address = int(args.get("ram_address", "0x08000000"), 16)
            interactions = args.get("interactions", [])

            proc = None
            serial_sock = None
            elf_data = None

            try:
                proc, _stderr_log = _popen_qemu(cmd, tmpdir)

                serial_sock, connect_err = _connect_serial_retry(
                    serial_sock_path, proc, stderr_log_path=_stderr_log, cmd=cmd
                )
                if connect_err:
                    return connect_err

                # Collect boot output
                end_time = time.time() + boot_wait
                boot_data = b""
                while time.time() < end_time:
                    remaining = end_time - time.time()
                    if remaining <= 0:
                        break
                    serial_sock.settimeout(min(remaining, 0.5))
                    try:
                        data = serial_sock.recv(4096)
                        if data:
                            boot_data += data
                    except socket.timeout:
                        continue
                    except OSError:
                        break

                # Execute interactions if any
                pending_data = boot_data
                for interaction in interactions:
                    expect_pattern = interaction.get("expect", "")
                    send_text = interaction.get("send", "")
                    expect_timeout = interaction.get("timeout", 30)

                    if expect_pattern:
                        acc = pending_data
                        end_t = time.time() + expect_timeout
                        matched = False
                        try:
                            cpat = re.compile(expect_pattern)
                        except re.error:
                            cpat = None
                        text = acc.decode("latin-1")
                        if cpat and cpat.search(text):
                            matched = True
                        elif not cpat and expect_pattern in text:
                            matched = True

                        while not matched and time.time() < end_t:
                            rem = end_t - time.time()
                            serial_sock.settimeout(min(rem, 0.5))
                            try:
                                d = serial_sock.recv(4096)
                                if d:
                                    acc += d
                                    text = acc.decode("latin-1")
                                    if cpat and cpat.search(text):
                                        matched = True
                                    elif not cpat and expect_pattern in text:
                                        matched = True
                            except socket.timeout:
                                continue
                            except OSError:
                                break
                        pending_data = b""
                        if not matched:
                            return (
                                f"Error: expect pattern '{expect_pattern}' not matched"
                            )

                    if send_text:
                        send_bytes = (
                            send_text.encode("latin-1")
                            .decode("unicode_escape")
                            .encode("latin-1")
                        )
                        serial_sock.sendall(send_bytes)

                # Wait additional time for kernel to load after interactions
                if interactions:
                    time.sleep(min(boot_wait, 30))

                # Now read ELF from guest RAM via monitor
                mon_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                mon_sock.settimeout(5)
                mon_sock.connect(monitor_sock_path)
                try:
                    mon_sock.recv(4096)  # banner
                except socket.timeout:
                    pass

                # Read first 64 bytes to check ELF header
                mon_sock.sendall(f"xp/16wx 0x{ram_address:x}\n".encode())
                time.sleep(0.3)
                header_resp = b""
                try:
                    while True:
                        d = mon_sock.recv(65536)
                        if not d:
                            break
                        header_resp += d
                except socket.timeout:
                    pass

                # Parse hex dump to find ELF magic
                header_text = header_resp.decode("utf-8", errors="replace")
                words = []
                for line in header_text.split("\n"):
                    line = line.strip()
                    if not line or line.startswith("QEMU") or "(qemu)" in line:
                        continue
                    # Parse "0xADDR: 0xVAL 0xVAL ..."
                    parts = line.split(":")
                    if len(parts) >= 2:
                        for w in parts[1].strip().split():
                            w = w.strip()
                            if w.startswith("0x"):
                                try:
                                    words.append(int(w, 16))
                                except ValueError:
                                    pass

                if not words or (words[0] & 0xFFFFFF00) != 0x7F454C00:
                    # 0x7f454c46 = \x7fELF in big-endian word
                    # Try scanning for ELF magic
                    found_elf = False
                    for scan_off in range(0, 0x100000, 0x10000):
                        scan_addr = ram_address + scan_off
                        mon_sock.sendall(f"xp/4wx 0x{scan_addr:x}\n".encode())
                        time.sleep(0.2)
                        resp = b""
                        try:
                            while True:
                                d = mon_sock.recv(65536)
                                if not d:
                                    break
                                resp += d
                        except socket.timeout:
                            pass
                        resp_text = resp.decode("utf-8", errors="replace")
                        for line in resp_text.split("\n"):
                            if "0x7f454c46" in line or "0x7f454c" in line:
                                ram_address = scan_addr
                                found_elf = True
                                break
                        if found_elf:
                            break

                    if not found_elf:
                        mon_sock.sendall(b"quit\n")
                        mon_sock.close()
                        return f"Error: No ELF header found at 0x{ram_address:08x} (scanned +1MB). Kernel may not be loaded yet — try increasing boot_wait."

                # Read ELF in chunks via monitor xp command
                # First read enough to get headers (first 4KB)
                elf_chunks = []
                chunk_size = 256  # words per read (1KB)
                # Read up to 2MB to capture headers + symtab
                total_words = (2 * 1024 * 1024) // 4
                for offset in range(0, total_words, chunk_size):
                    addr = ram_address + offset * 4
                    mon_sock.sendall(f"xp/{chunk_size}wx 0x{addr:x}\n".encode())
                    time.sleep(0.1)
                    resp = b""
                    try:
                        while True:
                            d = mon_sock.recv(65536)
                            if not d:
                                break
                            resp += d
                    except socket.timeout:
                        pass

                    for line in resp.decode("utf-8", errors="replace").split("\n"):
                        line = line.strip()
                        if not line or line.startswith("QEMU") or "(qemu)" in line:
                            continue
                        parts = line.split(":")
                        if len(parts) >= 2:
                            for w in parts[1].strip().split():
                                w = w.strip()
                                if w.startswith("0x"):
                                    try:
                                        val = int(w, 16)
                                        elf_chunks.append(struct.pack(">I", val))
                                    except ValueError:
                                        pass

                mon_sock.sendall(b"quit\n")
                mon_sock.close()

                if elf_chunks:
                    elf_data = b"".join(elf_chunks)

            finally:
                if serial_sock:
                    try:
                        serial_sock.close()
                    except Exception:
                        pass
                if proc:
                    try:
                        proc.kill()
                        proc.wait(timeout=5)
                    except Exception:
                        pass
                for p in [serial_sock_path, monitor_sock_path]:
                    try:
                        os.unlink(p)
                    except Exception:
                        pass
                try:
                    shutil.rmtree(tmpdir, ignore_errors=True)
                except Exception:
                    pass

            if not elf_data:
                return "Error: Failed to read ELF data from guest RAM"

        else:
            # Read from a file on disk
            elf_path = Path(source)
            if not elf_path.is_absolute():
                project_root = Path(__file__).parent.parent
                if (project_root / source).exists():
                    elf_path = project_root / source
            if not elf_path.exists():
                return f"Error: ELF file not found: {source}"
            elf_data = elf_path.read_bytes()

        # Parse symbols
        symbols = _parse_elf_symbols(elf_data)
        if not symbols:
            return "Error: No symbols found in ELF. The binary may be stripped or the ELF data may be incomplete."

        # Apply filter
        if filter_pattern:
            try:
                pat = re.compile(filter_pattern, re.IGNORECASE)
                symbols = [s for s in symbols if pat.search(s["name"])]
            except re.error:
                symbols = [
                    s for s in symbols if filter_pattern.lower() in s["name"].lower()
                ]

        # Sort by address
        symbols.sort(key=lambda s: s["address"])

        # Save if requested
        if save_to:
            save_path = Path(save_to)
            if not save_path.is_absolute():
                save_path = Path(__file__).parent.parent / save_to
            # Save all symbols (not filtered/limited)
            all_symbols = _parse_elf_symbols(elf_data)
            all_symbols.sort(key=lambda s: s["address"])
            save_path.write_text(json.dumps(all_symbols, indent=2))

        # Build output
        output_lines = []
        total = len(symbols)
        output_lines.append(f"**IRIX Kernel Symbols:** {total} found")
        if filter_pattern:
            output_lines.append(f"**Filter:** `{filter_pattern}`")
        if save_to:
            output_lines.append(f"**Saved to:** `{save_to}`")
        output_lines.append("")

        # Key symbols of interest
        key_names = {
            "klogmsgs",
            "putbuf",
            "putbufndx",
            "conbuf",
            "constrlen",
            "idle",
            "swtch",
            "main",
            "mlsetup",
            "cmn_err",
            "panic",
            "exec_common",
            "dksc_strategy",
            "dkscioctl",
            "vfs_mountroot",
            "clkstart",
            "startrtclock",
            "splx",
            "spl0",
        }
        key_found = [s for s in symbols if s["name"] in key_names]
        if key_found and not filter_pattern:
            output_lines.append("### Key Symbols")
            output_lines.append("| Symbol | Address | Type | Size |")
            output_lines.append("|--------|---------|------|------|")
            for s in sorted(key_found, key=lambda x: x["address"]):
                output_lines.append(
                    f"| `{s['name']}` | `0x{s['address']:08x}` | {s['type']} | {s['size']} |"
                )
            output_lines.append("")

        # Type summary
        type_counts = {}
        for s in symbols:
            type_counts[s["type"]] = type_counts.get(s["type"], 0) + 1
        output_lines.append("### Symbol Types")
        for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
            output_lines.append(f"- {t}: {c}")
        output_lines.append("")

        # Show symbols (limited)
        display = symbols[:max_symbols] if max_symbols > 0 else symbols
        output_lines.append(f"### Symbols ({len(display)} of {total})")
        output_lines.append("| Address | Type | Size | Name |")
        output_lines.append("|---------|------|------|------|")
        for s in display:
            output_lines.append(
                f"| `0x{s['address']:08x}` | {s['type']} | {s['size']:>6} | `{s['name']}` |"
            )

        if max_symbols > 0 and total > max_symbols:
            output_lines.append(
                f"\n*({total - max_symbols} more symbols not shown — use filter or increase max_symbols)*"
            )

        return "\n".join(output_lines)

    elif name == "irix_pc_sample":






        from pathlib import Path

        sample_interval_ms = args.get("sample_interval_ms", 500)
        duration_s = args.get("duration_s", 60)
        symbols_file = args.get("symbols_file", "")
        top_n = args.get("top_n", 30)
        boot_wait = args.get("boot_wait", 10)
        interactions = args.get("interactions", [])
        autoload = args.get("autoload", False)

        # Load symbol table if provided
        symbols = []
        if symbols_file:
            sym_path = Path(symbols_file)
            if not sym_path.is_absolute():
                sym_path = Path(__file__).parent.parent / symbols_file
            if sym_path.exists():
                symbols = json.loads(sym_path.read_text())
                symbols.sort(key=lambda s: s["address"])

        # Build function lookup: sorted list of (address, name) for bisect
        func_addrs = []
        func_names = {}
        for s in symbols:
            if s.get("type") in ("FUNC", "NOTYPE") or not s.get("type"):
                addr = s["address"]
                func_addrs.append(addr)
                func_names[addr] = s["name"]
        func_addrs.sort()

        def _lookup_function(pc: int) -> str:
            """Find the function containing PC using bisect."""
            if not func_addrs:
                return f"0x{pc:08x}"


            idx = bisect.bisect_right(func_addrs, pc) - 1
            if idx < 0:
                return f"0x{pc:08x}"
            base = func_addrs[idx]
            name = func_names[base]
            offset = pc - base
            if offset > 0x10000:  # Too far from any known function
                return f"0x{pc:08x}"
            if offset == 0:
                return name
            return f"{name}+0x{offset:x}"

        # Setup QEMU
        cmd, serial_sock_path, monitor_sock_path, tmpdir, prom_name, err = (
            _build_qemu_launch(args)
        )
        if err:
            return err

        proc = None
        serial_sock = None
        mon_sock = None
        pc_samples = []
        reg_snapshots = []  # Store full register snapshots for extra context
        serial_transcript = []

        try:
            proc, _stderr_log = _popen_qemu(cmd, tmpdir)

            serial_sock, connect_err = _connect_serial_retry(
                serial_sock_path, proc, stderr_log_path=_stderr_log, cmd=cmd
            )
            if connect_err:
                return connect_err

            # Collect boot output
            boot_data = _collect_serial_output(serial_sock, boot_wait)
            serial_transcript.append(boot_data.decode("latin-1", errors="replace"))

            # Execute interactions using shared helper
            interaction_parts, _, _ = _run_serial_interactions(
                serial_sock, interactions, 9999, boot_data
            )
            serial_transcript.extend(interaction_parts)

            # Connect to monitor for PC sampling
            mon_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            mon_sock.settimeout(5)
            mon_sock.connect(monitor_sock_path)
            try:
                mon_sock.recv(4096)  # banner
            except socket.timeout:
                pass

            # PC sampling loop
            sample_end = time.time() + duration_s
            sample_interval = sample_interval_ms / 1000.0
            sample_count = 0
            mon_sock.settimeout(2)

            while time.time() < sample_end:
                # Send register dump command
                mon_sock.sendall(b"info registers\n")
                time.sleep(0.3)

                resp = b""
                try:
                    while True:
                        d = mon_sock.recv(65536)
                        if not d:
                            break
                        resp += d
                except socket.timeout:
                    pass

                # Parse PC from register dump
                # QEMU format: "pc=0xffffffff9fc0c3fc" (lowercase, no space)
                reg_text = resp.decode("utf-8", errors="replace")
                pc_match = re.search(
                    r"pc[=\s]+(0x[\da-fA-F]+)", reg_text, re.IGNORECASE
                )
                if pc_match:
                    try:
                        pc = int(pc_match.group(1), 16)
                        # Mask to 32-bit for kseg0/1 comparison
                        # (QEMU sign-extends 32-bit MIPS addresses to 64-bit)
                        pc32 = pc & 0xFFFFFFFF
                        pc_samples.append(pc32)
                        sample_count += 1
                    except ValueError:
                        pass

                # Also capture Cause register for interrupt info
                # QEMU format: "CP0 Status  0x30004801 Cause   0x00000000"
                cause_match = re.search(r"Cause\s+(0x[\da-fA-F]+)", reg_text)
                status_match = re.search(r"Status\s+(0x[\da-fA-F]+)", reg_text)
                if cause_match or status_match:
                    snapshot = {"pc": pc_samples[-1] if pc_samples else 0}
                    if cause_match:
                        try:
                            snapshot["cause"] = int(cause_match.group(1), 16)
                        except ValueError:
                            pass
                    if status_match:
                        try:
                            snapshot["status"] = int(status_match.group(1), 16)
                        except ValueError:
                            pass
                    reg_snapshots.append(snapshot)

                # Also drain serial output
                serial_sock.settimeout(0.01)
                try:
                    sd = serial_sock.recv(4096)
                    if sd:
                        serial_transcript.append(sd.decode("latin-1", errors="replace"))
                except (socket.timeout, OSError):
                    pass

                # Wait for next sample
                remaining = sample_interval - 0.2  # Account for monitor query time
                if remaining > 0:
                    time.sleep(remaining)

        except Exception as e:
            serial_transcript.append(f"\n[ERROR: {e}]\n")

        finally:
            if mon_sock:
                try:
                    mon_sock.sendall(b"quit\n")
                    mon_sock.close()
                except Exception:
                    pass
            if serial_sock:
                try:
                    serial_sock.close()
                except Exception:
                    pass
            if proc:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
            for p in [serial_sock_path, monitor_sock_path]:
                try:
                    os.unlink(p)
                except Exception:
                    pass
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass

        # Build histogram
        if not pc_samples:
            return (
                "Error: No PC samples collected. QEMU may not have started correctly."
            )

        # Map PCs to functions and count
        func_counts = {}
        raw_pc_counts = {}
        for pc in pc_samples:
            func = _lookup_function(pc)
            func_counts[func] = func_counts.get(func, 0) + 1
            raw_pc_counts[pc] = raw_pc_counts.get(pc, 0) + 1

        # Sort by count
        sorted_funcs = sorted(func_counts.items(), key=lambda x: -x[1])
        total_samples = len(pc_samples)

        # Analyze patterns
        unique_pcs = len(raw_pc_counts)
        top_pc = max(raw_pc_counts, key=raw_pc_counts.get)
        top_pc_pct = raw_pc_counts[top_pc] / total_samples * 100

        # Determine likely state
        if top_pc_pct > 80:
            state = "STUCK — single PC dominates (tight loop or idle spin)"
        elif top_pc_pct > 50:
            state = "MOSTLY IDLE — one function dominates, likely scheduler idle"
        elif unique_pcs < 5:
            state = "LOOPING — few unique PCs, likely polling or retry loop"
        else:
            state = "ACTIVE — diverse PCs, kernel is doing work"

        # Check for kseg0/kseg1 addresses
        kseg0_count = sum(1 for pc in pc_samples if 0x80000000 <= pc < 0xA0000000)
        kseg1_count = sum(1 for pc in pc_samples if 0xA0000000 <= pc < 0xC0000000)
        prom_count = sum(1 for pc in pc_samples if 0xBFC00000 <= pc < 0xC0000000)

        output_lines = []
        output_lines.append("# PC Sampling Results")
        output_lines.append("")
        output_lines.append(
            f"**Samples:** {total_samples} over {duration_s}s (interval: {sample_interval_ms}ms)"
        )
        output_lines.append(f"**Unique PCs:** {unique_pcs}")
        output_lines.append(f"**Assessment:** {state}")
        output_lines.append("")

        # Address space breakdown
        output_lines.append("### Address Space")
        if kseg0_count:
            output_lines.append(
                f"- kseg0 (kernel cached): {kseg0_count} ({kseg0_count / total_samples * 100:.1f}%)"
            )
        if kseg1_count:
            output_lines.append(
                f"- kseg1 (uncached): {kseg1_count} ({kseg1_count / total_samples * 100:.1f}%)"
            )
        if prom_count:
            output_lines.append(
                f"- PROM (0xBFC0xxxx): {prom_count} ({prom_count / total_samples * 100:.1f}%)"
            )
        other = total_samples - kseg0_count - kseg1_count
        if other > 0:
            output_lines.append(
                f"- Other: {other} ({other / total_samples * 100:.1f}%)"
            )
        output_lines.append("")

        # Histogram
        output_lines.append("### Function Histogram")
        output_lines.append("| Function | Samples | Pct |")
        output_lines.append("|----------|---------|-----|")
        for func, count in sorted_funcs[:top_n]:
            pct = count / total_samples * 100
            bar = "#" * max(1, int(pct / 2))
            output_lines.append(f"| `{func}` | {count} | {pct:.1f}% {bar} |")

        if len(sorted_funcs) > top_n:
            remaining = sum(c for _, c in sorted_funcs[top_n:])
            output_lines.append(
                f"| *(+{len(sorted_funcs) - top_n} more)* | {remaining} | {remaining / total_samples * 100:.1f}% |"
            )
        output_lines.append("")

        # Top raw PCs (useful when no symbols)
        if not symbols:
            output_lines.append("### Top Raw PCs (no symbol table loaded)")
            output_lines.append("| PC | Count | Pct |")
            output_lines.append("|----|-------|-----|")
            sorted_pcs = sorted(raw_pc_counts.items(), key=lambda x: -x[1])
            for pc, count in sorted_pcs[:20]:
                pct = count / total_samples * 100
                output_lines.append(f"| `0x{pc:08x}` | {count} | {pct:.1f}% |")
            output_lines.append("")
            output_lines.append(
                "*Tip: Use `irix_kernel_symbols` with `save_to` to create a symbol table, then pass it as `symbols_file` for named functions.*"
            )

        # Serial output summary
        full_serial = "".join(serial_transcript)
        if full_serial.strip():
            # Show last few lines
            serial_lines = full_serial.strip().split("\n")
            show = serial_lines[-20:]
            output_lines.append("### Serial Output (last 20 lines)")
            output_lines.append("```")
            output_lines.extend(show)
            output_lines.append("```")

        return "\n".join(output_lines)

    elif name == "irix_kernel_inspect":







        from pathlib import Path

        inspect_targets = args.get(
            "inspect", ["klogmsgs", "putbuf", "spb", "registers"]
        )
        symbols_file = args.get("symbols_file", "")
        boot_wait = args.get("boot_wait", 120)
        interactions = args.get("interactions", [])
        autoload = args.get("autoload", False)

        # Load symbol table
        symbols = {}
        if symbols_file:
            sym_path = Path(symbols_file)
            if not sym_path.is_absolute():
                sym_path = Path(__file__).parent.parent / symbols_file
            if sym_path.exists():
                sym_list = json.loads(sym_path.read_text())
                for s in sym_list:
                    symbols[s["name"]] = s["address"]

        # Override addresses from args
        # Separate-fields klogmsgs layout (IRIX 6.5 IP22)
        klog_buf_addr = (
            int(args["klog_buf_addr"], 16) if args.get("klog_buf_addr") else 0
        )
        klog_writeloc_addr = (
            int(args["klog_writeloc_addr"], 16) if args.get("klog_writeloc_addr") else 0
        )
        klog_size = args.get("klog_size", 2048)
        # Legacy struct-layout klogmsgs
        klogmsgs_addr_val = (
            int(args["klogmsgs_addr"], 16)
            if args.get("klogmsgs_addr")
            else symbols.get("klogmsgs", 0)
        )
        # putbuf: direct buffer address (not a pointer to dereference)
        putbuf_buf_addr = (
            int(args["putbuf_addr"], 16)
            if args.get("putbuf_addr")
            else symbols.get("putbuf", 0)
        )
        putbufndx_addr_val = (
            int(args["putbufndx_addr"], 16)
            if args.get("putbufndx_addr")
            else symbols.get("putbufndx", 0)
        )

        def _virt_to_phys(vaddr: int) -> int:
            """Convert MIPS kseg0/kseg1 virtual address to physical."""
            if 0x80000000 <= vaddr < 0xA0000000:
                return vaddr - 0x80000000
            elif 0xA0000000 <= vaddr < 0xC0000000:
                return vaddr - 0xA0000000
            return vaddr  # Already physical or unmappable

        def _read_phys_memory(mon_sock, phys_addr: int, num_words: int) -> bytes:
            """Read physical memory via QEMU monitor xp command. Returns big-endian bytes."""
            result = b""
            chunk_size = 128  # words per request
            for offset in range(0, num_words, chunk_size):
                n = min(chunk_size, num_words - offset)
                addr = phys_addr + offset * 4
                mon_sock.sendall(f"xp/{n}wx 0x{addr:x}\n".encode())
                time.sleep(0.15)
                resp = b""
                try:
                    while True:
                        d = mon_sock.recv(65536)
                        if not d:
                            break
                        resp += d
                except socket.timeout:
                    pass

                for line in resp.decode("utf-8", errors="replace").split("\n"):
                    line = line.strip()
                    if not line or line.startswith("QEMU") or "(qemu)" in line:
                        continue
                    parts = line.split(":")
                    if len(parts) >= 2:
                        for w in parts[1].strip().split():
                            w = w.strip()
                            if w.startswith("0x"):
                                try:
                                    val = int(w, 16)
                                    result += struct.pack(">I", val)
                                except ValueError:
                                    pass
            return result

        def _read_registers(mon_sock) -> dict:
            """Read CPU registers via monitor."""
            mon_sock.sendall(b"info registers\n")
            time.sleep(0.3)
            resp = b""
            try:
                while True:
                    d = mon_sock.recv(65536)
                    if not d:
                        break
                    resp += d
            except socket.timeout:
                pass

            reg_text = resp.decode("utf-8", errors="replace")
            regs = {}

            # Parse PC - QEMU format: "pc=0xffffffff9fc0c3fc"
            m = re.search(r"pc[=\s]+(0x[\da-fA-F]+)", reg_text, re.IGNORECASE)
            if m:
                regs["PC"] = int(m.group(1), 16) & 0xFFFFFFFF

            # Parse CP0 registers
            # QEMU format: "CP0 Status  0x30004801 Cause   0x00000000 EPC    0x..."
            for cp0_name in [
                "Status",
                "Cause",
                "EPC",
                "BadVAddr",
                "EntryHi",
                "Compare",
                "Count",
                "ErrorEPC",
            ]:
                m = re.search(rf"{cp0_name}\s+(0x[\da-fA-F]+)", reg_text)
                if m:
                    regs[f"CP0_{cp0_name}"] = int(m.group(1), 16)
            # Config is "Config0"
            m = re.search(r"Config0\s+(0x[\da-fA-F]+)", reg_text)
            if m:
                regs["CP0_Config"] = int(m.group(1), 16)

            # Parse GPRs - QEMU format: "at ffffffffa0000000 v0 0000000000000002"
            for gpr in [
                "at",
                "v0",
                "v1",
                "a0",
                "a1",
                "a2",
                "a3",
                "t0",
                "t1",
                "t2",
                "t3",
                "t4",
                "t5",
                "t6",
                "t7",
                "s0",
                "s1",
                "s2",
                "s3",
                "s4",
                "s5",
                "s6",
                "s7",
                "t8",
                "t9",
                "k0",
                "k1",
                "gp",
                "sp",
                "fp",
                "ra",
            ]:
                m = re.search(rf"\b{gpr}\s+([\da-fA-F]{{8,16}})\b", reg_text)
                if m:
                    regs[gpr] = int(m.group(1), 16)

            return regs

        # Setup QEMU
        cmd, serial_sock_path, monitor_sock_path, tmpdir, prom_name, err = (
            _build_qemu_launch(args)
        )
        if err:
            return err

        proc = None
        serial_sock = None
        mon_sock = None
        output_lines = []

        try:
            proc, _stderr_log = _popen_qemu(cmd, tmpdir)

            serial_sock, connect_err = _connect_serial_retry(
                serial_sock_path, proc, stderr_log_path=_stderr_log, cmd=cmd
            )
            if connect_err:
                return connect_err

            # Collect boot output and execute interactions
            boot_data = _collect_serial_output(serial_sock, boot_wait)
            _run_serial_interactions(serial_sock, interactions, 9999, boot_data)

            # Connect to monitor
            mon_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            mon_sock.settimeout(5)
            mon_sock.connect(monitor_sock_path)
            try:
                mon_sock.recv(4096)  # banner
            except socket.timeout:
                pass

            output_lines.append("# IRIX Kernel Inspection Report")
            output_lines.append("")

            # ── Registers ──
            if "registers" in inspect_targets or "pc" in inspect_targets:
                regs = _read_registers(mon_sock)
                if regs:
                    output_lines.append("## CPU Registers")
                    output_lines.append("")
                    if "PC" in regs:
                        pc = regs["PC"]
                        # Determine address space
                        if 0xBFC00000 <= pc < 0xC0000000:
                            space = "PROM"
                        elif 0x80000000 <= pc < 0xA0000000:
                            space = "kseg0 (kernel)"
                        elif 0xA0000000 <= pc < 0xC0000000:
                            space = "kseg1 (uncached)"
                        else:
                            space = "unknown"

                        # Try to look up function
                        func_name = ""
                        if symbols:
                            # Build sorted list for lookup
                            sorted_syms = sorted(
                                [(a, n) for n, a in symbols.items()], key=lambda x: x[0]
                            )


                            addrs = [s[0] for s in sorted_syms]
                            idx = bisect.bisect_right(addrs, pc) - 1
                            if idx >= 0:
                                base, fname = sorted_syms[idx]
                                if pc - base < 0x10000:
                                    offset = pc - base
                                    func_name = (
                                        f"{fname}+0x{offset:x}" if offset else fname
                                    )

                        output_lines.append(
                            f"- **PC:** `0x{pc:08x}` ({space}){' — `' + func_name + '`' if func_name else ''}"
                        )

                    if "sp" in regs:
                        sp = regs["sp"]
                        if 0x80000000 <= sp < 0xA0000000:
                            output_lines.append(
                                f"- **SP:** `0x{sp:08x}` (kernel stack)"
                            )
                        else:
                            output_lines.append(f"- **SP:** `0x{sp:08x}`")

                    if "ra" in regs:
                        output_lines.append(f"- **RA:** `0x{regs['ra']:08x}`")

                    # CP0 registers
                    if "CP0_Cause" in regs:
                        cause = regs["CP0_Cause"]
                        exc_code = (cause >> 2) & 0x1F
                        exc_names = {
                            0: "Int",
                            1: "Mod",
                            2: "TLBL",
                            3: "TLBS",
                            4: "AdEL",
                            5: "AdES",
                            8: "Syscall",
                            9: "Bp",
                            10: "RI",
                            11: "CpU",
                            12: "Ov",
                            15: "FPE",
                        }
                        exc = exc_names.get(exc_code, f"Code{exc_code}")
                        ip_bits = (cause >> 8) & 0xFF
                        output_lines.append(
                            f"- **Cause:** `0x{cause:08x}` (ExcCode={exc}, IP={ip_bits:08b})"
                        )

                    if "CP0_Status" in regs:
                        status = regs["CP0_Status"]
                        ie = "enabled" if status & 1 else "disabled"
                        ksu = (status >> 3) & 3
                        mode = {0: "kernel", 1: "supervisor", 2: "user"}.get(
                            ksu, f"mode{ksu}"
                        )
                        im = (status >> 8) & 0xFF
                        output_lines.append(
                            f"- **Status:** `0x{status:08x}` (IE={ie}, mode={mode}, IM={im:08b})"
                        )

                    if "CP0_EPC" in regs:
                        output_lines.append(f"- **EPC:** `0x{regs['CP0_EPC']:08x}`")

                    if "CP0_Count" in regs:
                        output_lines.append(
                            f"- **Count:** `0x{regs['CP0_Count']:08x}` ({regs['CP0_Count']})"
                        )
                    if "CP0_Compare" in regs:
                        output_lines.append(
                            f"- **Compare:** `0x{regs['CP0_Compare']:08x}`"
                        )

                    output_lines.append("")

            # ── SPB ──
            if "spb" in inspect_targets:
                output_lines.append("## System Parameter Block (SPB)")
                output_lines.append("")
                # SPB is at physical 0x1000
                spb_data = _read_phys_memory(mon_sock, 0x1000, 32)  # 128 bytes
                if len(spb_data) >= 32:
                    sig = struct.unpack(">I", spb_data[0:4])[0]
                    length = struct.unpack(">I", spb_data[4:8])[0]
                    version = struct.unpack(">H", spb_data[8:10])[0]
                    revision = struct.unpack(">H", spb_data[10:12])[0]

                    if sig == 0x53435241:  # "SCRA"
                        output_lines.append(
                            f"- **Signature:** `0x{sig:08x}` (SCRA) — ARCS initialized"
                        )
                    else:
                        sig_ascii = "".join(
                            chr(b) if 32 <= b < 127 else "." for b in spb_data[0:4]
                        )
                        output_lines.append(
                            f"- **Signature:** `0x{sig:08x}` ('{sig_ascii}') — {'valid ARCS' if sig == 0x53435241 else 'NOT ARCS (firmware not initialized?)'}"
                        )

                    output_lines.append(f"- **Length:** {length}")
                    output_lines.append(f"- **Version:** {version}.{revision}")

                    # RestartBlock at offset 12
                    restart = struct.unpack(">I", spb_data[12:16])[0]
                    debug = struct.unpack(">I", spb_data[16:20])[0]
                    gevector = struct.unpack(">I", spb_data[20:24])[0]
                    utlb = struct.unpack(">I", spb_data[24:28])[0]

                    output_lines.append(f"- **RestartBlock:** `0x{restart:08x}`")
                    output_lines.append(f"- **DebugBlock:** `0x{debug:08x}`")
                    output_lines.append(f"- **GEVector:** `0x{gevector:08x}`")
                    output_lines.append(f"- **UTLBMissVector:** `0x{utlb:08x}`")

                    if len(spb_data) >= 40:
                        tv_length = struct.unpack(">I", spb_data[28:32])[0]
                        tv_ptr = struct.unpack(">I", spb_data[32:36])[0]
                        output_lines.append(f"- **TVLength:** {tv_length}")
                        output_lines.append(f"- **TransferVector:** `0x{tv_ptr:08x}`")
                else:
                    output_lines.append("*(Could not read SPB data)*")
                output_lines.append("")

            # ── klogmsgs ──
            if "klogmsgs" in inspect_targets:
                output_lines.append("## Kernel Log Messages (klogmsgs)")
                output_lines.append("")

                if klog_buf_addr or klog_writeloc_addr:
                    # Separate-fields layout (IRIX 6.5 IP22):
                    # buffer pointer at one addr, writeloc at another, separate size
                    buf_addr = klog_buf_addr
                    if not buf_addr:
                        output_lines.append("- *(klog_buf_addr not provided)*")
                    else:
                        # Read writeloc
                        writeloc = 0
                        if klog_writeloc_addr:
                            wl_data = _read_phys_memory(
                                mon_sock, _virt_to_phys(klog_writeloc_addr), 1
                            )
                            if len(wl_data) >= 4:
                                writeloc = struct.unpack(">I", wl_data[0:4])[0]

                        buf_size = klog_size
                        output_lines.append(
                            f"- **Buffer:** `0x{buf_addr:08x}` (size={buf_size})"
                        )
                        output_lines.append(f"- **Write index:** {writeloc}")

                        if writeloc == 0:
                            output_lines.append(
                                "- *(Buffer empty — no kernel messages logged)*"
                            )
                        else:
                            # Read buffer content
                            num_words = (buf_size + 3) // 4
                            buf_data = _read_phys_memory(
                                mon_sock, _virt_to_phys(buf_addr), num_words
                            )
                            if buf_data and len(buf_data) >= min(buf_size, writeloc):
                                mask = buf_size - 1
                                if writeloc <= buf_size:
                                    msg = buf_data[:writeloc]
                                else:
                                    w = writeloc & mask
                                    msg = buf_data[w:buf_size] + buf_data[:w]
                                text = msg.decode("ascii", errors="replace").rstrip(
                                    "\x00"
                                )
                                if text.strip():
                                    output_lines.append(
                                        f"- **Messages ({len(text)} bytes):**"
                                    )
                                    output_lines.append("```")
                                    output_lines.append(text)
                                    output_lines.append("```")
                                else:
                                    output_lines.append(
                                        "- *(Buffer contains only null bytes)*"
                                    )
                            else:
                                output_lines.append("- *(Could not read buffer data)*")
                elif klogmsgs_addr_val:
                    # Legacy struct layout: { uint32 readloc, uint32 writeloc, char buffer[4096] }
                    klog_paddr = _virt_to_phys(klogmsgs_addr_val)
                    klog_data = _read_phys_memory(mon_sock, klog_paddr, 1026)
                    if len(klog_data) >= 8:
                        readloc = struct.unpack(">I", klog_data[0:4])[0]
                        writeloc = struct.unpack(">I", klog_data[4:8])[0]
                        buf_data = klog_data[8:]
                        buf_size = 4096

                        output_lines.append(
                            f"- **Address:** `0x{klogmsgs_addr_val:08x}` (phys `0x{klog_paddr:08x}`)"
                        )
                        output_lines.append(f"- **Read index:** {readloc}")
                        output_lines.append(f"- **Write index:** {writeloc}")

                        if writeloc == 0 and readloc == 0:
                            output_lines.append(
                                "- *(Buffer empty — no kernel messages logged)*"
                            )
                        elif len(buf_data) >= min(buf_size, writeloc):
                            if writeloc <= buf_size:
                                msg = buf_data[readloc:writeloc]
                            else:
                                r = readloc & (buf_size - 1)
                                w = writeloc & (buf_size - 1)
                                if r < w:
                                    msg = buf_data[r:w]
                                else:
                                    msg = buf_data[r:buf_size] + buf_data[:w]
                            text = msg.decode("ascii", errors="replace").rstrip("\x00")
                            if text.strip():
                                output_lines.append(
                                    f"- **Messages ({len(text)} bytes):**"
                                )
                                output_lines.append("```")
                                output_lines.append(text)
                                output_lines.append("```")
                            else:
                                output_lines.append(
                                    "- *(Buffer contains only null bytes)*"
                                )
                        else:
                            output_lines.append("- *(Incomplete buffer read)*")
                    else:
                        output_lines.append("- *(Could not read klogmsgs data)*")
                else:
                    output_lines.append(
                        "- *(klogmsgs address not known — provide klog_buf_addr+klog_writeloc_addr or klogmsgs_addr)*"
                    )
                output_lines.append("")

            # ── putbuf ──
            if "putbuf" in inspect_targets:
                output_lines.append("## Printf Buffer (putbuf)")
                output_lines.append("")

                if putbuf_buf_addr and putbufndx_addr_val:
                    # Read putbufndx (uint32)
                    ndx_paddr = _virt_to_phys(putbufndx_addr_val)
                    ndx_data = _read_phys_memory(mon_sock, ndx_paddr, 1)

                    if len(ndx_data) >= 4:
                        buf_ndx = struct.unpack(">I", ndx_data[0:4])[0]
                        output_lines.append(
                            f"- **putbuf address:** `0x{putbuf_buf_addr:08x}`"
                        )
                        output_lines.append(f"- **putbufndx:** {buf_ndx}")

                        if buf_ndx > 0:
                            # Read the buffer content directly (putbuf_buf_addr IS the buffer)
                            buf_paddr = _virt_to_phys(putbuf_buf_addr)
                            # Buffer size is 4096 (masked with 0xFFF)
                            putbufsz = 4096
                            read_size = min(buf_ndx, putbufsz)
                            num_words = (putbufsz + 3) // 4
                            buf_content = _read_phys_memory(
                                mon_sock, buf_paddr, num_words
                            )
                            if buf_content:
                                mask = putbufsz - 1
                                if buf_ndx <= putbufsz:
                                    text_bytes = buf_content[:buf_ndx]
                                else:
                                    w = buf_ndx & mask
                                    text_bytes = (
                                        buf_content[w:putbufsz] + buf_content[:w]
                                    )
                                text = text_bytes.decode(
                                    "ascii", errors="replace"
                                ).rstrip("\x00")
                                if text.strip():
                                    output_lines.append(
                                        f"- **Content ({len(text)} bytes):**"
                                    )
                                    output_lines.append("```")
                                    if len(text) > 4096:
                                        output_lines.append(
                                            f"... ({len(text) - 4096} bytes earlier) ..."
                                        )
                                        output_lines.append(text[-4096:])
                                    else:
                                        output_lines.append(text)
                                    output_lines.append("```")
                                else:
                                    output_lines.append(
                                        "- *(Buffer contains only null bytes)*"
                                    )
                            else:
                                output_lines.append(
                                    "- *(Could not read buffer content)*"
                                )
                        else:
                            output_lines.append(
                                "- *(putbufndx is 0 — nothing written yet)*"
                            )
                    else:
                        output_lines.append("- *(Could not read putbufndx)*")
                else:
                    output_lines.append(
                        "- *(putbuf/putbufndx addresses not known — provide putbuf_addr and putbufndx_addr)*"
                    )
                output_lines.append("")

        except Exception as e:
            output_lines.append(f"\n**Error during inspection:** {e}")

        finally:
            if mon_sock:
                try:
                    mon_sock.sendall(b"quit\n")
                    mon_sock.close()
                except Exception:
                    pass
            if serial_sock:
                try:
                    serial_sock.close()
                except Exception:
                    pass
            if proc:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
            for p in [serial_sock_path, monitor_sock_path]:
                try:
                    os.unlink(p)
                except Exception:
                    pass
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass

        return "\n".join(output_lines)

    elif name == "irix_quick_inspect":








        from pathlib import Path

        symbols_file = args.get("symbols_file", "")
        wait_for = args.get("wait_for", "audio:.*responding")
        wait_timeout = args.get("wait_timeout", 300)
        post_wait = args.get("post_wait", 5)
        snapshot_name = args.get("snapshot_name", "")

        # Load symbol table
        symbols = {}
        func_addrs = []
        func_names = {}
        if symbols_file:
            sym_path = Path(symbols_file)
            if not sym_path.is_absolute():
                sym_path = Path(__file__).parent.parent / symbols_file
            if sym_path.exists():
                sym_list = json.loads(sym_path.read_text())
                for s in sym_list:
                    symbols[s["name"]] = s["address"]
                    if s.get("type") in ("FUNC", "NOTYPE", "OBJECT") or not s.get(
                        "type"
                    ):
                        func_addrs.append(s["address"])
                        func_names[s["address"]] = s["name"]
                func_addrs.sort()

        # Override addresses — defaults are for IRIX 6.5 IP22 miniroot kernel
        klogmsgs_vaddr = symbols.get("klogmsgs", 0)  # legacy struct layout
        # Separate-fields klogmsgs (IRIX 6.5 IP22 defaults)
        klog_buf_ptr_addr = (
            int(args["klog_buf_addr"], 16) if args.get("klog_buf_addr") else 0x882DA228
        )
        klog_writeloc_addr = (
            int(args["klog_writeloc_addr"], 16)
            if args.get("klog_writeloc_addr")
            else 0x882D66C0
        )
        klog_size = args.get("klog_size", 2048)
        # putbuf defaults for IRIX 6.5 IP22 miniroot
        putbuf_vaddr = (
            int(args["putbuf_addr"], 16) if args.get("putbuf_addr") else 0x882FA438
        )
        putbufndx_vaddr = (
            int(args["putbufndx_addr"], 16)
            if args.get("putbufndx_addr")
            else 0x882FA434
        )
        if args.get("klogmsgs_addr"):
            klogmsgs_vaddr = int(args["klogmsgs_addr"], 16)

        def _v2p(vaddr):
            if 0x80000000 <= vaddr < 0xA0000000:
                return vaddr - 0x80000000
            elif 0xA0000000 <= vaddr < 0xC0000000:
                return vaddr - 0xA0000000
            return vaddr

        def _lookup_func(pc):
            if not func_addrs:
                return ""
            idx = bisect.bisect_right(func_addrs, pc) - 1
            if idx < 0:
                return ""
            base = func_addrs[idx]
            name = func_names[base]
            offset = pc - base
            if offset > 0x10000:
                return ""
            return f"{name}+0x{offset:x}" if offset else name

        def _mon_read_phys(msock, phys_addr, num_words):
            result = b""
            chunk = 128
            for off in range(0, num_words, chunk):
                n = min(chunk, num_words - off)
                addr = phys_addr + off * 4
                msock.sendall(f"xp/{n}wx 0x{addr:x}\n".encode())
                time.sleep(0.15)
                resp = b""
                try:
                    while True:
                        d = msock.recv(65536)
                        if not d:
                            break
                        resp += d
                except socket.timeout:
                    pass
                for line in resp.decode("utf-8", errors="replace").split("\n"):
                    line = line.strip()
                    if not line or line.startswith("QEMU") or "(qemu)" in line:
                        continue
                    parts = line.split(":")
                    if len(parts) >= 2:
                        for w in parts[1].strip().split():
                            w = w.strip()
                            if w.startswith("0x"):
                                try:
                                    result += struct.pack(">I", int(w, 16))
                                except ValueError:
                                    pass
            return result

        # Setup QEMU — handle default drives and snapshot
        project_root = Path(__file__).parent.parent
        scsi_drives = args.get("scsi_drives", [])
        if not scsi_drives:
            default_disk = project_root / "irix_disk.qcow2"
            default_cdrom = (
                project_root
                / "software_library"
                / "irix_6.5.22_images"
                / "IRIX 6.5 Installation Tools June 1998.img"
            )
            if default_disk.exists():
                scsi_drives.append(str(default_disk))
            if default_cdrom.exists():
                scsi_drives.append(str(default_cdrom) + ":cdrom")
            args["scsi_drives"] = scsi_drives

        if snapshot_name:
            args["snapshot"] = snapshot_name

        # Default extra_args for irix_quick_inspect includes icount
        if "extra_args" not in args:
            args["extra_args"] = "-icount shift=0,sleep=off"

        cmd, serial_sock_path, monitor_sock_path, tmpdir, prom_name, err = (
            _build_qemu_launch(args)
        )
        if err:
            return err

        proc = None
        serial_sock = None
        mon_sock = None
        output_lines = []
        serial_text = ""

        try:
            proc, _stderr_log = _popen_qemu(cmd, tmpdir)

            # Connect serial
            serial_sock, connect_err = _connect_serial_retry(
                serial_sock_path, proc, stderr_log_path=_stderr_log, cmd=cmd
            )
            if connect_err:
                return connect_err

            if snapshot_name:
                # Restored from snapshot — just wait briefly then inspect
                boot_data = _collect_serial_output(serial_sock, post_wait)
                serial_text = boot_data.decode("latin-1", errors="replace")
            else:
                # Full boot: collect output, do PROM interactions, wait for kernel
                boot_data = b""

                # Standard PROM interactions
                prom_interactions = [
                    {"expect": "Option", "send": "2\r", "timeout": 40},
                    {"expect": "enter.*to start", "send": "\r", "timeout": 15},
                    {"expect": "press.*enter", "send": "\r", "timeout": 10},
                    {"expect": "c, f, r, or a", "send": "c\r", "timeout": 30},
                ]

                # Collect boot output for 35s (PROM boot)
                boot_data = _collect_serial_output(serial_sock, 35)

                # PROM interactions
                interaction_parts, _, all_ok = _run_serial_interactions(
                    serial_sock, prom_interactions, 9999, boot_data
                )
                if not all_ok:
                    output_lines.append("**Warning:** PROM interaction timed out")

                # Wait for kernel marker
                if wait_for:
                    acc, matched = _expect_serial(serial_sock, wait_for, wait_timeout)
                    if not matched:
                        output_lines.append(
                            f"**Warning:** wait_for pattern '{wait_for}' not matched within {wait_timeout}s"
                        )

                # Post-wait
                if post_wait > 0:
                    _collect_serial_output(serial_sock, post_wait)

                serial_text = ""  # Not needed for output

            # Connect to monitor and inspect everything
            mon_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            mon_sock.settimeout(5)
            mon_sock.connect(monitor_sock_path)
            try:
                mon_sock.recv(4096)
            except socket.timeout:
                pass

            output_lines.append("# IRIX Quick Inspect")
            output_lines.append("")

            # ── Registers ──
            mon_sock.sendall(b"info registers\n")
            time.sleep(0.3)
            resp = b""
            try:
                while True:
                    d = mon_sock.recv(65536)
                    if not d:
                        break
                    resp += d
            except socket.timeout:
                pass
            reg_text = resp.decode("utf-8", errors="replace")

            pc_val = 0
            pc_match = re.search(r"pc[=\s]+(0x[\da-fA-F]+)", reg_text, re.IGNORECASE)
            if pc_match:
                pc_val = int(pc_match.group(1), 16) & 0xFFFFFFFF

            sp_val = 0
            sp_match = re.search(r"\bsp\s+([\da-fA-F]{8,16})\b", reg_text)
            if sp_match:
                sp_val = int(sp_match.group(1), 16) & 0xFFFFFFFF

            ra_val = 0
            ra_match = re.search(r"\bra\s+([\da-fA-F]{8,16})\b", reg_text)
            if ra_match:
                ra_val = int(ra_match.group(1), 16) & 0xFFFFFFFF

            cause_val = 0
            cause_match = re.search(r"Cause\s+(0x[\da-fA-F]+)", reg_text)
            if cause_match:
                cause_val = int(cause_match.group(1), 16)

            status_val = 0
            status_match = re.search(r"Status\s+(0x[\da-fA-F]+)", reg_text)
            if status_match:
                status_val = int(status_match.group(1), 16)

            epc_val = 0
            epc_match = re.search(r"EPC\s+(0x[\da-fA-F]+)", reg_text)
            if epc_match:
                epc_val = int(epc_match.group(1), 16) & 0xFFFFFFFF

            # Classify PC
            if 0xBFC00000 <= pc_val < 0xC0000000:
                space = "PROM"
            elif 0x80000000 <= pc_val < 0xA0000000:
                space = "kernel"
            elif 0xA0000000 <= pc_val < 0xC0000000:
                space = "uncached"
            else:
                space = "???"
            func = _lookup_func(pc_val)

            exc_code = (cause_val >> 2) & 0x1F
            exc_names = {
                0: "Int",
                1: "Mod",
                2: "TLBL",
                3: "TLBS",
                4: "AdEL",
                5: "AdES",
                8: "Syscall",
                9: "Bp",
                10: "RI",
                11: "CpU",
                12: "Ov",
                15: "FPE",
            }
            exc_str = exc_names.get(exc_code, f"Code{exc_code}")
            ip_bits = (cause_val >> 8) & 0xFF
            ie = "on" if status_val & 1 else "off"
            ksu = (status_val >> 3) & 3
            mode = {0: "kernel", 1: "supervisor", 2: "user"}.get(ksu, f"{ksu}")

            output_lines.append("## CPU State")
            output_lines.append(
                f"- **PC:** `0x{pc_val:08x}` ({space}){' `' + func + '`' if func else ''}"
            )
            output_lines.append(
                f"- **SP:** `0x{sp_val:08x}`  **RA:** `0x{ra_val:08x}`{' `' + _lookup_func(ra_val) + '`' if _lookup_func(ra_val) else ''}"
            )
            output_lines.append(f"- **Cause:** ExcCode={exc_str}, IP={ip_bits:08b}")
            output_lines.append(
                f"- **Status:** IE={ie}, mode={mode}, IM={(status_val >> 8) & 0xFF:08b}"
            )
            if epc_val:
                output_lines.append(
                    f"- **EPC:** `0x{epc_val:08x}`{' `' + _lookup_func(epc_val) + '`' if _lookup_func(epc_val) else ''}"
                )
            output_lines.append("")

            # ── SPB ──
            spb_data = _mon_read_phys(mon_sock, 0x1000, 12)
            if len(spb_data) >= 36:
                sig = struct.unpack(">I", spb_data[0:4])[0]
                length = struct.unpack(">I", spb_data[4:8])[0]
                version = struct.unpack(">H", spb_data[8:10])[0]
                revision = struct.unpack(">H", spb_data[10:12])[0]
                tv_ptr = (
                    struct.unpack(">I", spb_data[32:36])[0]
                    if len(spb_data) >= 36
                    else 0
                )
                arcs_ok = sig == 0x53435241
                output_lines.append("## SPB")
                output_lines.append(
                    f"- **ARCS:** {'initialized' if arcs_ok else 'NOT initialized'} (sig=`0x{sig:08x}`)"
                )
                if arcs_ok:
                    output_lines.append(
                        f"- **Version:** {version}.{revision}, TransferVector=`0x{tv_ptr:08x}`"
                    )
            else:
                output_lines.append("## SPB")
                output_lines.append("- *(could not read)*")
            output_lines.append("")

            # ── klogmsgs (separate-fields layout) ──
            output_lines.append("## Kernel Log (klogmsgs)")
            if klog_buf_ptr_addr:
                # Read buffer pointer to get actual buffer address
                ptr_data = _mon_read_phys(mon_sock, _v2p(klog_buf_ptr_addr), 1)
                if len(ptr_data) >= 4:
                    klog_buf_vaddr = struct.unpack(">I", ptr_data[0:4])[0]
                else:
                    klog_buf_vaddr = 0
                # Read writeloc
                writeloc = 0
                if klog_writeloc_addr:
                    wl_data = _mon_read_phys(mon_sock, _v2p(klog_writeloc_addr), 1)
                    if len(wl_data) >= 4:
                        writeloc = struct.unpack(">I", wl_data[0:4])[0]
                output_lines.append(
                    f"- buf=`0x{klog_buf_vaddr:08x}` writeloc={writeloc} size={klog_size}"
                )
                if klog_buf_vaddr and writeloc > 0:
                    num_words = (klog_size + 3) // 4
                    buf = _mon_read_phys(mon_sock, _v2p(klog_buf_vaddr), num_words)
                    if buf:
                        mask = klog_size - 1
                        if writeloc <= klog_size:
                            msg = buf[:writeloc]
                        else:
                            w = writeloc & mask
                            msg = buf[w:klog_size] + buf[:w]
                        text = msg.decode("ascii", errors="replace").rstrip("\x00")
                        if text.strip():
                            output_lines.append("```")
                            output_lines.append(
                                text[-4096:] if len(text) > 4096 else text
                            )
                            output_lines.append("```")
                        else:
                            output_lines.append("- *(nulls only)*")
                    else:
                        output_lines.append("- *(read failed)*")
                elif writeloc == 0:
                    output_lines.append("- *(empty)*")
            elif klogmsgs_vaddr:
                # Legacy struct layout fallback
                klog_paddr = _v2p(klogmsgs_vaddr)
                klog_data = _mon_read_phys(mon_sock, klog_paddr, 1026)
                if len(klog_data) >= 8:
                    readloc = struct.unpack(">I", klog_data[0:4])[0]
                    writeloc = struct.unpack(">I", klog_data[4:8])[0]
                    buf = klog_data[8:]
                    output_lines.append(f"- read={readloc} write={writeloc}")
                    if writeloc == 0 and readloc == 0:
                        output_lines.append("- *(empty)*")
                    elif len(buf) >= min(4096, writeloc):
                        if writeloc <= 4096:
                            msg = buf[readloc:writeloc]
                        else:
                            r = readloc & 4095
                            w = writeloc & 4095
                            msg = buf[r:4096] + buf[:w] if r >= w else buf[r:w]
                        text = msg.decode("ascii", errors="replace").rstrip("\x00")
                        if text.strip():
                            output_lines.append("```")
                            output_lines.append(
                                text[-4096:] if len(text) > 4096 else text
                            )
                            output_lines.append("```")
                        else:
                            output_lines.append("- *(nulls only)*")
                else:
                    output_lines.append("- *(read failed)*")
            else:
                output_lines.append("- *(no klogmsgs address)*")
            output_lines.append("")

            # ── putbuf (direct buffer, not pointer dereference) ──
            if putbuf_vaddr and putbufndx_vaddr:
                ndx_data = _mon_read_phys(mon_sock, _v2p(putbufndx_vaddr), 1)
                output_lines.append("## Printf Buffer (putbuf)")
                if len(ndx_data) >= 4:
                    buf_ndx = struct.unpack(">I", ndx_data[0:4])[0]
                    output_lines.append(f"- buf=`0x{putbuf_vaddr:08x}` ndx={buf_ndx}")
                    if buf_ndx > 0:
                        putbufsz = 4096
                        num_words = (putbufsz + 3) // 4
                        content = _mon_read_phys(
                            mon_sock, _v2p(putbuf_vaddr), num_words
                        )
                        if content:
                            mask = putbufsz - 1
                            if buf_ndx <= putbufsz:
                                text_bytes = content[:buf_ndx]
                            else:
                                w = buf_ndx & mask
                                text_bytes = content[w:putbufsz] + content[:w]
                            text = text_bytes.decode("ascii", errors="replace").rstrip(
                                "\x00"
                            )
                            if text.strip():
                                output_lines.append("```")
                                if len(text) > 4096:
                                    output_lines.append(
                                        f"... ({len(text) - 4096} bytes earlier) ..."
                                    )
                                    output_lines.append(text[-4096:])
                                else:
                                    output_lines.append(text)
                                output_lines.append("```")
                            else:
                                output_lines.append("- *(nulls only)*")
                        else:
                            output_lines.append("- *(read failed)*")
                    else:
                        output_lines.append("- *(ndx=0, nothing written)*")
                else:
                    output_lines.append("- *(read failed)*")
                output_lines.append("")

            # ── Serial tail ──
            if serial_text.strip():
                lines = serial_text.strip().split("\n")
                show = lines[-30:]
                output_lines.append("## Serial Output (last 30 lines)")
                output_lines.append("```")
                output_lines.extend(show)
                output_lines.append("```")

        except Exception as e:
            output_lines.append(f"\n**Error:** {e}")

        finally:
            if mon_sock:
                try:
                    mon_sock.sendall(b"quit\n")
                    mon_sock.close()
                except Exception:
                    pass
            if serial_sock:
                try:
                    serial_sock.close()
                except Exception:
                    pass
            if proc:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
            for p in [serial_sock_path, monitor_sock_path]:
                try:
                    os.unlink(p)
                except Exception:
                    pass
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass

        return "\n".join(output_lines)

    # Ghidra tools are handled by _handle_tool_async (dispatched from call_tool)

    # --- External Library Tools ---
    elif name == "library_scan":


        from pathlib import Path  # _handle_tool has later local Path imports -> Path is
        # function-local throughout; rebind here so this branch's Path() works.
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from pyirix_qemu.catalog.library import LibraryScanner

        lib_path = args.get("path", "")
        if not lib_path:
            return "Error: path is required"
        if not os.path.isdir(lib_path):
            return f"Error: directory not found: {lib_path}"

        scanner = LibraryScanner(lib_path)
        stats = scanner.scan(deep=args.get("deep", False))
        scanner.close()

        lines = [f"## Library Scan Complete"]
        lines.append(f"**Root:** `{lib_path}`")
        lines.append(f"**Total indexed:** {stats['total']}")
        lines.append(f"**New/updated:** {stats.get('new', 0)}")
        lines.append(f"**Unchanged:** {stats.get('unchanged', 0)}")
        lines.append(f"**Removed:** {stats.get('removed', 0)}")
        if stats.get("errors", 0):
            lines.append(f"**Errors:** {stats['errors']}")
        lines.append(f"**Elapsed:** {stats.get('elapsed_seconds', 0)}s")
        return "\n".join(lines)

    elif name == "library_search":

        from pathlib import Path  # _handle_tool has later local Path imports -> Path is
        # function-local throughout; rebind here so this branch's Path() works.
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from pyirix_qemu.catalog.library import LibraryIndex

        project_root = Path(__file__).parent.parent
        db_path = str(project_root / "software_library" / "external_library.db")

        if not os.path.exists(db_path):
            return "Error: no external library index found. Run library_scan first."

        idx = LibraryIndex(db_path)
        query = args.get("query", "")
        category = args.get("category")
        fmt = args.get("format")
        limit = args.get("limit", 30)

        results = idx.search(query, category=category, fmt=fmt, limit=limit)
        idx.close()

        if not results:
            msg = f"No results for '{query}'"
            if category:
                msg += f" (category={category})"
            if fmt:
                msg += f" (format={fmt})"
            return msg

        lines = [f"## Search Results: {len(results)} matches\n"]
        for entry in results:
            lines.append(f"- **{entry.display_name}**")
            lines.append(f"  `{entry.path}`")
            parts = [entry.format, entry.category, entry.size_display]
            if entry.version:
                parts.append(f"v{entry.version}")
            if entry.part_number:
                parts.append(entry.part_number)
            lines.append(f"  {' | '.join(parts)}")
            if entry.notes:
                lines.append(f"  _{entry.notes}_")
            lines.append("")
        return "\n".join(lines)

    elif name == "library_stage":

        from pathlib import Path  # _handle_tool has later local Path imports -> Path is
        # function-local throughout; rebind here so this branch's Path() works.
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from pyirix_qemu.catalog.library import LibraryEntry, stage_file

        source_path = args.get("source_path", "")
        if not source_path:
            return "Error: source_path is required"
        if not os.path.exists(source_path):
            return f"Error: file not found: {source_path}"

        filename = os.path.basename(source_path)
        st = os.stat(source_path)
        entry = LibraryEntry(
            path=source_path,
            filename=filename,
            format="unknown",
            category="unknown",
            size_bytes=st.st_size,
        )

        dest = args.get("dest")
        staged_path = stage_file(entry, dest=dest)
        size_mb = st.st_size / (1024 * 1024)
        return f"Staged `{filename}` ({size_mb:.1f} MB) → `{staged_path}`"

    elif name == "library_info":


        sys.path.insert(0, str(Path(__file__).parent.parent))
        from pyirix_qemu.catalog.library import LibraryIndex

        project_root = Path(__file__).parent.parent
        db_path = str(project_root / "software_library" / "external_library.db")

        if not os.path.exists(db_path):
            return "No external library index found. Run library_scan first."

        idx = LibraryIndex(db_path)
        stats = idx.get_stats()
        idx.close()

        lines = [f"## External Library Index"]
        lines.append(f"**Total entries:** {stats.get('total', 0)}")
        lines.append(f"**Database:** `{db_path}`\n")

        # Categories
        cat_lines = []
        for key, count in sorted(stats.items()):
            if key.startswith("cat:"):
                cat_lines.append(f"  {key[4:]}: {count}")
        if cat_lines:
            lines.append("### Categories")
            lines.extend(cat_lines)
            lines.append("")

        # Formats
        fmt_lines = []
        for key, count in sorted(stats.items()):
            if key.startswith("fmt:"):
                fmt_lines.append(f"  {key[4:]}: {count}")
        if fmt_lines:
            lines.append("### Formats")
            lines.extend(fmt_lines)

        return "\n".join(lines)

    # --- Filesystem Tools ---
    elif name == "fs_info":
        image = args.get("image", "")
        if not image:
            return "Error: image is required"
        return sgi_fs.fs_info(image)

    elif name == "fs_ls":
        image = args.get("image", "")
        if not image:
            return "Error: image is required"
        return sgi_fs.fs_ls(
            image,
            path=args.get("path", "/"),
            recursive=args.get("recursive", True),
            max_entries=args.get("max_entries", 500),
            partition=args.get("partition"),
        )

    elif name == "fs_cat":
        image = args.get("image", "")
        path = args.get("path", "")
        if not image:
            return "Error: image is required"
        if not path:
            return "Error: path is required"
        return sgi_fs.fs_cat(
            image,
            path,
            binary=args.get("binary", False),
            max_size=args.get("max_size", 65536),
            partition=args.get("partition"),
        )

    elif name == "fs_extract":
        image = args.get("image", "")
        dest = args.get("dest", "")
        if not image:
            return "Error: image is required"
        if not dest:
            return "Error: dest is required"
        return sgi_fs.fs_extract(
            image,
            dest,
            path=args.get("path"),
            partition=args.get("partition"),
        )

    elif name == "fs_inject":
        image = args.get("image", "")
        host_path = args.get("host_path", "")
        guest_path = args.get("guest_path", "")
        if not image:
            return "Error: image is required"
        if not host_path:
            return "Error: host_path is required"
        if not guest_path:
            return "Error: guest_path is required"
        return sgi_fs.fs_inject(
            image,
            host_path,
            guest_path,
            uid=args.get("uid", 0),
            gid=args.get("gid", 0),
            mode=args.get("mode"),
        )

    # ── XFS Analysis Tools handlers ─────────────────────────────────────

    elif name == "xfs_superblock":
        image = args.get("image", "")
        if not image:
            return "Error: image is required"
        return sgi_fs.xfs_superblock(image)

    elif name == "xfs_inode":
        image = args.get("image", "")
        inode = args.get("inode")
        if not image:
            return "Error: image is required"
        if inode is None:
            return "Error: inode is required"
        return sgi_fs.xfs_inode(image, int(inode))

    elif name == "xfs_path":
        image = args.get("image", "")
        path = args.get("path", "")
        if not image:
            return "Error: image is required"
        if not path:
            return "Error: path is required"
        return sgi_fs.xfs_path(image, path)

    elif name == "xfs_block":
        image = args.get("image", "")
        fsblock = args.get("fsblock")
        if not image:
            return "Error: image is required"
        if fsblock is None:
            return "Error: fsblock is required"
        return sgi_fs.xfs_block(image, int(fsblock))

    elif name == "xfs_check":
        image = args.get("image", "")
        if not image:
            return "Error: image is required"
        return sgi_fs.xfs_check(image)

    elif name == "xfs_scan":
        image = args.get("image", "")
        if not image:
            return "Error: image is required"
        return sgi_fs.xfs_scan(image)

    elif name == "disk_verify":
        image = args.get("image", "")
        if not image:
            return "Error: image is required"
        return sgi_fs.disk_verify(image)

    elif name == "golden_list":
        return golden_catalog.list_goldens()

    elif name == "golden_snapshot":
        if not args.get("name") or not args.get("source"):
            return "Error: name and source are required"
        return golden_catalog.snapshot_golden(
            args["name"], args["source"], parent=args.get("parent"),
            notes=args.get("notes"), machine=args.get("machine"),
            kernel_md5=args.get("kernel_md5"), verified=args.get("verified"))

    elif name == "golden_register":
        if not args.get("name") or not args.get("file"):
            return "Error: name and file are required"
        return golden_catalog.register_golden(
            args["name"], args["file"], parent=args.get("parent"),
            notes=args.get("notes"), machine=args.get("machine"),
            verified=args.get("verified"), lock=bool(args.get("lock", True)))

    elif name == "golden_fork":
        if not args.get("name") or not args.get("dest"):
            return "Error: name and dest are required"
        return golden_catalog.fork_golden(args["name"], args["dest"])

    elif name == "xfs_repair_superblock":
        image = args.get("image", "")
        field = args.get("field", "")
        value = args.get("value")
        if not image:
            return "Error: image is required"
        if not field:
            return "Error: field is required"
        if value is None:
            return "Error: value is required"
        dry_run = args.get("dry_run", True)
        return sgi_fs.xfs_repair_superblock(image, field, int(value), dry_run=bool(dry_run))

    # ── Live IRIX Kernel Introspection (VMI) handlers ──────────────────

    elif name == "irix_sysinfo":


        session_id = args.get("session_id", "")
        symbols_file = args.get("symbols_file", "")
        include = args.get("include", ["uname", "uptime", "current", "klog"])

        try:
            mon_sock = _vmi_session_monitor(session_id)
        except ValueError as e:
            return f"Error: {e}"

        symbols = _vmi_load_symbols(symbols_file)
        output_lines = []

        try:
            # ── uname ──
            if "uname" in include:
                utsname_addr = symbols.get("utsname", 0)
                if utsname_addr:
                    # struct utsname: 13 fields × 257 bytes each
                    SYS_NMLN = 257
                    num_fields = 7  # sysname..base_rel
                    data = _vmi_read_virt(
                        mon_sock, utsname_addr, (num_fields * SYS_NMLN + 3) // 4
                    )
                    fields = []
                    for i in range(num_fields):
                        start = i * SYS_NMLN
                        chunk = data[start : start + SYS_NMLN]
                        null_pos = chunk.find(b"\x00")
                        if null_pos >= 0:
                            chunk = chunk[:null_pos]
                        fields.append(chunk.decode("ascii", errors="replace"))

                    field_names = [
                        "sysname",
                        "nodename",
                        "release",
                        "version",
                        "machine",
                        "m_type",
                        "base_rel",
                    ]
                    output_lines.append("## System Info (utsname)")
                    output_lines.append("")
                    for fn, fv in zip(field_names, fields):
                        if fv:
                            output_lines.append(f"- **{fn}:** {fv}")
                    output_lines.append("")
                else:
                    output_lines.append("## System Info")
                    output_lines.append(
                        "*Symbol `utsname` not found — provide symbols_file*"
                    )
                    output_lines.append("")

            # ── uptime ──
            if "uptime" in include:
                lbolt_addr = symbols.get("lbolt", 0)
                if lbolt_addr:
                    lbolt = _vmi_read_u32(mon_sock, lbolt_addr)
                    # HZ=100 on IRIX IP22
                    uptime_secs = lbolt // 100
                    days = uptime_secs // 86400
                    hours = (uptime_secs % 86400) // 3600
                    mins = (uptime_secs % 3600) // 60
                    parts = []
                    if days:
                        parts.append(f"{days}d")
                    if hours:
                        parts.append(f"{hours}h")
                    parts.append(f"{mins}m")
                    output_lines.append(f"## Uptime")
                    output_lines.append("")
                    output_lines.append(
                        f"- **Uptime:** {' '.join(parts)} ({lbolt} ticks, HZ=100)"
                    )
                    output_lines.append("")
                else:
                    output_lines.append("## Uptime")
                    output_lines.append("*Symbol `lbolt` not found*")
                    output_lines.append("")

            # ── current process ──
            if "current" in include:
                pdaindr_addr = symbols.get("pdaindr", 0)
                if pdaindr_addr:
                    # pdaindr is array of pda_t pointers. Read pdaindr[0]
                    pda_ptr = _vmi_read_u32(mon_sock, pdaindr_addr)
                    if pda_ptr and 0x80000000 <= pda_ptr < 0xC0000000:
                        # Read p_curproc — search for it relative to PDA start
                        # p_curproc is typically at small offset in pda_t
                        # Read first 256 bytes of PDA and look for kernel pointers
                        pda_data = _vmi_read_virt(mon_sock, pda_ptr, 64)  # 256 bytes
                        # Try known offsets for p_curuthread (offset varies)
                        # Rather than guessing, report the PDA address
                        output_lines.append("## Current CPU State")
                        output_lines.append("")
                        output_lines.append(f"- **PDA[0]:** `0x{pda_ptr:08x}`")

                        # Try to read PC from registers for context
                        pc_val = None
                        try:
                            mon_sock.sendall(b"info registers\n")
                            time.sleep(0.3)
                            resp = b""
                            try:
                                while True:
                                    d = mon_sock.recv(65536)
                                    if not d:
                                        break
                                    resp += d
                            except socket.timeout:
                                pass
                            reg_text = resp.decode("utf-8", errors="replace")


                            m = re.search(
                                r"pc[=\s]+(0x[\da-fA-F]+)", reg_text, re.IGNORECASE
                            )
                            if m:
                                pc_val = int(m.group(1), 16) & 0xFFFFFFFF
                                func = _vmi_lookup_func(symbols, pc_val)
                                output_lines.append(
                                    f"- **PC:** `0x{pc_val:08x}`{' — `' + func + '`' if func else ''}"
                                )
                        except Exception:
                            pass
                        output_lines.append("")
                    else:
                        output_lines.append("## Current CPU State")
                        output_lines.append(
                            f"*pdaindr[0] = 0x{pda_ptr:08x} — not a valid kernel pointer*"
                        )
                        output_lines.append("")
                else:
                    output_lines.append("## Current CPU State")
                    output_lines.append("*Symbol `pdaindr` not found*")
                    output_lines.append("")

            # ── klog ──
            if "klog" in include:
                putbuf_addr = symbols.get("putbuf", 0)
                putbufndx_addr = symbols.get("putbufndx", 0)
                if putbuf_addr and putbufndx_addr:
                    putbufndx = _vmi_read_u32(mon_sock, putbufndx_addr)
                    # putbuf is typically 2048 bytes
                    buf_size = 2048
                    num_words = buf_size // 4
                    buf_data = _vmi_read_virt(mon_sock, putbuf_addr, num_words)

                    if buf_data:
                        # putbuf is a circular buffer, putbufndx is write index
                        idx = putbufndx % buf_size
                        # Reorder: from idx to end, then 0 to idx
                        ordered = buf_data[idx:] + buf_data[:idx]
                        # Strip nulls and decode
                        text = ordered.decode("ascii", errors="replace").replace(
                            "\x00", ""
                        )
                        # Show last 30 lines
                        lines = [l for l in text.split("\n") if l.strip()]
                        last_lines = lines[-30:] if len(lines) > 30 else lines

                        output_lines.append("## Kernel Log (putbuf, last 30 lines)")
                        output_lines.append("")
                        output_lines.append("```")
                        for l in last_lines:
                            output_lines.append(l)
                        output_lines.append("```")
                        output_lines.append("")
                else:
                    output_lines.append("## Kernel Log")
                    output_lines.append("*Symbols `putbuf`/`putbufndx` not found*")
                    output_lines.append("")

        finally:
            mon_sock.close()

        return "\n".join(output_lines) if output_lines else "*No data collected*"

    elif name == "irix_ps":


        session_id = args.get("session_id", "")
        symbols_file = args.get("symbols_file", "")
        max_procs = args.get("max_procs", 100)
        verbose = args.get("verbose", False)

        try:
            mon_sock = _vmi_session_monitor(session_id)
        except ValueError as e:
            return f"Error: {e}"

        symbols = _vmi_load_symbols(symbols_file)
        output_lines = []

        try:
            pidtab_addr = symbols.get("pidtab", 0)
            pidtabsz_addr = symbols.get("pidtabsz", 0)
            pid_base_addr = symbols.get("pid_base", 0)

            if not pidtab_addr or not pidtabsz_addr:
                return "Error: symbols `pidtab` and `pidtabsz` required — provide symbols_file"

            # Read pidtabsz and pidtab pointer
            pidtabsz = _vmi_read_u32(mon_sock, pidtabsz_addr)
            pidtab_ptr = _vmi_read_u32(mon_sock, pidtab_addr)
            pid_base = _vmi_read_u32(mon_sock, pid_base_addr) if pid_base_addr else 0

            if not pidtabsz or not pidtab_ptr:
                return f"Error: pidtabsz={pidtabsz}, pidtab=0x{pidtab_ptr:08x} — kernel may not be fully booted"

            # pid_slot_t is 20 bytes:
            #   offset 0:  ps_pid (int32)
            #   offset 4:  ps_lock (lock_t = uint32)
            #   offset 8:  psu_active (int32)
            #   offset 12: psu_busycnt (int32)
            #   offset 16: ps_chain (pid_entry_t *)
            PID_SLOT_SIZE = 20

            # Limit table size to prevent huge reads
            effective_sz = min(pidtabsz, 4096)
            total_bytes = effective_sz * PID_SLOT_SIZE
            total_words = (total_bytes + 3) // 4

            # Bulk read entire pidtab
            pidtab_data = _vmi_read_virt(mon_sock, pidtab_ptr, total_words)

            if len(pidtab_data) < PID_SLOT_SIZE:
                return f"Error: could not read pidtab at 0x{pidtab_ptr:08x}"

            # ── Calibrate proc_t offsets using PID 1 (init) ──
            # We need to discover: p_pid offset, p_comm offset within proc_t
            # Strategy: find the pidtab slot for PID 1, follow pointers to proc_t,
            # read a big chunk, and search for known patterns

            PSCOMSIZ = 32
            PSARGSZ = 80

            # First pass: find active slots and collect pid_entry pointers
            active_slots = []
            for i in range(effective_sz):
                off = i * PID_SLOT_SIZE
                if off + PID_SLOT_SIZE > len(pidtab_data):
                    break
                ps_pid = struct.unpack(">i", pidtab_data[off : off + 4])[0]
                ps_chain = struct.unpack(">I", pidtab_data[off + 16 : off + 20])[0]
                if ps_chain and 0x80000000 <= ps_chain < 0xC0000000:
                    active_slots.append((i, ps_pid, ps_chain))

            if not active_slots:
                return "Error: no active pid slots found — kernel may not be running"

            # ── Calibration: use PID 0 or PID 1 to discover proc_t layout ──
            calibration = _vmi_calibration_cache.get("default")

            if not calibration:
                # Find slot for PID 1 (init) - most reliable calibration target
                cal_slot = None
                for slot_idx, ps_pid, ps_chain in active_slots:
                    if ps_pid == 1:
                        cal_slot = (slot_idx, ps_pid, ps_chain)
                        break

                if not cal_slot:
                    # Fall back to first active slot
                    cal_slot = active_slots[0]

                cal_pid = cal_slot[1]
                pe_addr = cal_slot[2]

                # Read pid_entry_t (24 bytes):
                #   offset 0:  pe_queue (kqueue_t = next(4) + prev(4) = 8)
                #   offset 8:  pe_pid (pid_t = 4)
                #   offset 12: pe_ubusy (uint32)
                #   offset 16: pe_vproc (vproc_t *)
                #   offset 20: pe_next (pid_entry_t *)
                pe_data = _vmi_read_virt(mon_sock, pe_addr, 6)  # 24 bytes
                if len(pe_data) < 24:
                    return f"Error: cannot read pid_entry at 0x{pe_addr:08x}"

                pe_vproc = struct.unpack(">I", pe_data[16:20])[0]
                if not pe_vproc or not (0x80000000 <= pe_vproc < 0xC0000000):
                    return f"Error: pe_vproc=0x{pe_vproc:08x} invalid for PID {cal_pid}"

                # Read vproc_t — variable size due to BHV_SYNCH.
                # Read 256 bytes to be safe, then scan for bhv_desc_t
                vproc_data = _vmi_read_virt(mon_sock, pe_vproc, 64)  # 256 bytes

                # Find vp_bhvh.bh_first — it's a pointer to a bhv_desc_t
                # which contains bd_pdata (pointer to proc_t) at offset 0.
                # The bhv_head_t starts with bh_first (a pointer) as the first
                # meaningful field after any lock structures.
                # Strategy: scan for kernel pointers that point to a bhv_desc_t
                # whose bd_pdata points to a proc_t containing our calibration PID.
                proc_ptr = 0
                vproc_bhvh_offset = -1

                for scan_off in range(8, min(len(vproc_data) - 4, 200), 4):
                    candidate = struct.unpack(
                        ">I", vproc_data[scan_off : scan_off + 4]
                    )[0]
                    if not (0x80000000 <= candidate < 0xC0000000):
                        continue
                    # Could be bh_first pointer to bhv_desc_t
                    # bhv_desc_t: bd_pdata(4) bd_vobj(4) bd_ops(4) bd_next(4)
                    bhv_data = _vmi_read_virt(mon_sock, candidate, 4)  # 16 bytes
                    if len(bhv_data) < 16:
                        continue
                    bd_pdata = struct.unpack(">I", bhv_data[0:4])[0]
                    bd_vobj = struct.unpack(">I", bhv_data[4:8])[0]
                    # bd_vobj should point back to our vproc
                    if bd_vobj == pe_vproc and (0x80000000 <= bd_pdata < 0xC0000000):
                        proc_ptr = bd_pdata
                        vproc_bhvh_offset = scan_off
                        break

                if not proc_ptr:
                    # Fallback: try inline bhv_desc_t (embedded in vproc_t)
                    # In some configs, bh_first points to an embedded descriptor
                    # right after bhv_head_t. Try each pointer as bd_pdata directly.
                    for scan_off in range(0, min(len(vproc_data) - 4, 200), 4):
                        candidate = struct.unpack(
                            ">I", vproc_data[scan_off : scan_off + 4]
                        )[0]
                        if not (0x80000000 <= candidate < 0xC0000000):
                            continue
                        # Try reading 1024 bytes and look for PID pattern
                        test_data = _vmi_read_virt(mon_sock, candidate, 256)
                        if len(test_data) < 256:
                            continue
                        # Search for cal_pid as big-endian int32
                        pid_bytes = struct.pack(">i", cal_pid)
                        pos = test_data.find(pid_bytes)
                        if pos >= 0 and pos < 200:
                            proc_ptr = candidate
                            break

                if not proc_ptr:
                    return f"Error: could not locate proc_t for PID {cal_pid}"

                # Read a big chunk of proc_t for calibration
                proc_data = _vmi_read_virt(mon_sock, proc_ptr, 384)  # 1536 bytes

                # Find p_pid offset: search for cal_pid as big-endian int32
                pid_bytes = struct.pack(">i", cal_pid)
                p_pid_offset = -1
                # For PID 1 (init), look for pattern: pid(1) ppid(0) — consecutive
                for pos in range(0, min(len(proc_data) - 8, 400), 4):
                    if proc_data[pos : pos + 4] == pid_bytes:
                        ppid = struct.unpack(">i", proc_data[pos + 4 : pos + 8])[0]
                        if cal_pid == 1 and ppid == 0:
                            p_pid_offset = pos
                            break
                        elif cal_pid == 0:
                            p_pid_offset = pos
                            break

                if p_pid_offset < 0:
                    # Looser search — just find the PID value
                    pos = proc_data.find(pid_bytes)
                    if pos >= 0 and pos < 400:
                        p_pid_offset = pos

                if p_pid_offset < 0:
                    return f"Error: could not calibrate p_pid offset in proc_t"

                # Find p_comm: search for process name string
                # For PID 1, look for "init" or "sched" (PID 0)
                p_comm_offset = -1
                if cal_pid == 1:
                    search_names = [b"init\x00", b"/sbin/init\x00", b"irix_init\x00"]
                elif cal_pid == 0:
                    search_names = [b"sched\x00", b"swapper\x00"]
                else:
                    search_names = []

                for search_name in search_names:
                    pos = proc_data.find(search_name)
                    if pos >= 0 and pos < 1500:
                        # p_comm should be at a 4-byte aligned offset
                        # and before p_psargs which follows at p_comm + PSCOMSIZ
                        p_comm_offset = pos
                        break

                # If we found p_comm, p_psargs follows at p_comm + PSCOMSIZ
                p_psargs_offset = p_comm_offset + PSCOMSIZ if p_comm_offset >= 0 else -1

                calibration = {
                    "p_pid_offset": p_pid_offset,
                    "p_comm_offset": p_comm_offset,
                    "p_psargs_offset": p_psargs_offset,
                    "vproc_bhvh_offset": vproc_bhvh_offset,
                    "proc_read_size": max(p_psargs_offset + PSARGSZ + 16, 512)
                    if p_psargs_offset >= 0
                    else 512,
                }
                _vmi_calibration_cache["default"] = calibration

            # ── Main process scan ──
            p_pid_off = calibration["p_pid_offset"]
            p_comm_off = calibration["p_comm_offset"]
            p_psargs_off = calibration["p_psargs_offset"]
            vproc_bhvh_off = calibration["vproc_bhvh_offset"]
            proc_read_words = calibration["proc_read_size"] // 4

            processes = []

            for slot_idx, ps_pid, ps_chain in active_slots:
                if len(processes) >= max_procs:
                    break

                try:
                    # Read pid_entry_t
                    pe_data = _vmi_read_virt(mon_sock, ps_chain, 6)
                    if len(pe_data) < 24:
                        continue
                    pe_vproc = struct.unpack(">I", pe_data[16:20])[0]
                    if not pe_vproc or not (0x80000000 <= pe_vproc < 0xC0000000):
                        continue

                    # Find proc_t via vproc -> bhv_head -> bhv_desc -> bd_pdata
                    proc_ptr = 0

                    if vproc_bhvh_off >= 0:
                        # Use calibrated offset
                        bhvh_first_data = _vmi_read_virt(
                            mon_sock, pe_vproc + vproc_bhvh_off, 1
                        )
                        if len(bhvh_first_data) >= 4:
                            bh_first = struct.unpack(">I", bhvh_first_data[:4])[0]
                            if 0x80000000 <= bh_first < 0xC0000000:
                                bhv_data = _vmi_read_virt(mon_sock, bh_first, 4)
                                if len(bhv_data) >= 4:
                                    proc_ptr = struct.unpack(">I", bhv_data[:4])[0]
                    else:
                        # Brute-force scan vproc for proc_t pointer
                        vp_data = _vmi_read_virt(mon_sock, pe_vproc, 64)
                        for scan_off in range(8, min(len(vp_data) - 4, 200), 4):
                            candidate = struct.unpack(
                                ">I", vp_data[scan_off : scan_off + 4]
                            )[0]
                            if not (0x80000000 <= candidate < 0xC0000000):
                                continue
                            bhv_data = _vmi_read_virt(mon_sock, candidate, 4)
                            if len(bhv_data) >= 8:
                                bd_pdata = struct.unpack(">I", bhv_data[0:4])[0]
                                bd_vobj = struct.unpack(">I", bhv_data[4:8])[0]
                                if bd_vobj == pe_vproc and (
                                    0x80000000 <= bd_pdata < 0xC0000000
                                ):
                                    proc_ptr = bd_pdata
                                    break

                    if not proc_ptr or not (0x80000000 <= proc_ptr < 0xC0000000):
                        continue

                    # Read proc_t fields
                    proc_data = _vmi_read_virt(mon_sock, proc_ptr, proc_read_words)
                    if len(proc_data) < p_pid_off + 4:
                        continue

                    pid = struct.unpack(">i", proc_data[p_pid_off : p_pid_off + 4])[0]
                    ppid = (
                        struct.unpack(">i", proc_data[p_pid_off + 4 : p_pid_off + 8])[0]
                        if p_pid_off + 8 <= len(proc_data)
                        else -1
                    )

                    # Read p_stat (single byte, typically at p_pid_offset - some_offset or nearby)
                    # p_stat is usually a few bytes before p_pid in the struct
                    # For now, report as running since we found it
                    stat = "S"

                    comm = ""
                    psargs = ""
                    if p_comm_off >= 0 and p_comm_off + PSCOMSIZ <= len(proc_data):
                        comm_raw = proc_data[p_comm_off : p_comm_off + PSCOMSIZ]
                        null_pos = comm_raw.find(b"\x00")
                        if null_pos >= 0:
                            comm_raw = comm_raw[:null_pos]
                        comm = comm_raw.decode("ascii", errors="replace")

                    if p_psargs_off >= 0 and p_psargs_off + PSARGSZ <= len(proc_data):
                        psargs_raw = proc_data[p_psargs_off : p_psargs_off + PSARGSZ]
                        null_pos = psargs_raw.find(b"\x00")
                        if null_pos >= 0:
                            psargs_raw = psargs_raw[:null_pos]
                        psargs = psargs_raw.decode("ascii", errors="replace")

                    display_cmd = psargs if psargs else comm if comm else f"[pid {pid}]"
                    processes.append((pid, ppid, stat, display_cmd, comm))

                except Exception:
                    continue

            # Sort by PID
            processes.sort(key=lambda p: p[0])

            # Format output
            if verbose:
                output_lines.append(
                    f"## IRIX Process List ({len(processes)} processes)"
                )
                output_lines.append("")
                output_lines.append("```")
                output_lines.append(f"{'PID':>6}  {'PPID':>6}  S  COMMAND")
                for pid, ppid, stat, cmd, comm in processes:
                    output_lines.append(f"{pid:>6}  {ppid:>6}  {stat}  {cmd}")
                output_lines.append("```")
            else:
                output_lines.append(
                    f"## IRIX Process List ({len(processes)} processes)"
                )
                output_lines.append("")
                output_lines.append("```")
                output_lines.append(f"{'PID':>6}  COMMAND")
                for pid, ppid, stat, cmd, comm in processes:
                    output_lines.append(f"{pid:>6}  {cmd}")
                output_lines.append("```")

            output_lines.append("")
            output_lines.append(
                f"*Calibration: p_pid=+{calibration['p_pid_offset']}, "
                f"p_comm=+{calibration['p_comm_offset']}, "
                f"bhvh=+{calibration['vproc_bhvh_offset']}*"
            )

        except Exception as e:
            output_lines.append(f"Error: {e}")
            import traceback

            output_lines.append(f"```\n{traceback.format_exc()}\n```")
        finally:
            mon_sock.close()

        return "\n".join(output_lines) if output_lines else "*No processes found*"

    elif name == "irix_crash_analyze":
        dump = args.get("dump", "")
        dump_file = args.get("dump_file", "")
        if not dump and dump_file:
            dpath = dump_file
            if not os.path.isabs(dpath):
                dpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), dump_file)
            if not os.path.exists(dpath):
                return f"Error: dump_file not found: {dump_file}"
            with open(dpath, errors="replace") as _f:
                dump = _f.read()
        if not dump.strip():
            return "Error: provide 'dump' (text) or 'dump_file' (path) with a panic/register dump."
        symbols_file = args.get("symbols_file", "ip54_kernel_symbols_golden.json")
        symbols = _vmi_load_symbols(symbols_file)
        if not symbols:
            return (f"Error: no symbols loaded from {symbols_file}. "
                    f"Generate the golden symbol JSON first.")
        return _irix_crash_analyze(dump, symbols, args.get("kernel_elf", ""))

    elif name == "irix_netstat":


        session_id = args.get("session_id", "")
        symbols_file = args.get("symbols_file", "")
        proto = args.get("proto", "all")

        try:
            mon_sock = _vmi_session_monitor(session_id)
        except ValueError as e:
            return f"Error: {e}"

        symbols = _vmi_load_symbols(symbols_file)
        output_lines = []

        TCP_STATES = {
            0: "CLOSED",
            1: "LISTEN",
            2: "SYN_SENT",
            3: "SYN_RCVD",
            4: "ESTABLISHED",
            5: "CLOSE_WAIT",
            6: "FIN_WAIT_1",
            7: "CLOSING",
            8: "LAST_ACK",
            9: "FIN_WAIT_2",
            10: "TIME_WAIT",
        }

        def _walk_inpcb(head_addr: str, proto_name: str, is_tcp: bool) -> list:
            """Walk an inpcb linked list starting from head symbol."""
            conns = []
            head_vaddr = symbols.get(head_addr, 0)
            if not head_vaddr:
                return conns

            # The head is itself an inpcb struct. inp_next at offset 0.
            # Read inp_next from the head to get first real entry.
            head_data = _vmi_read_virt(mon_sock, head_vaddr, 1)
            if len(head_data) < 4:
                return conns

            first_ptr = struct.unpack(">I", head_data[:4])[0]
            if not first_ptr or first_ptr == head_vaddr:
                return conns  # Empty list

            # ── Calibrate inpcb offsets using first entry ──
            # Read a generous chunk and scan for port numbers
            # inpcb has inp_next(4), inp_prev(4), inp_head(4) at the start,
            # then various fields. The inaddrpair (faddr, fport, laddr, lport)
            # is somewhere in the middle.
            # We'll scan for port patterns: network byte order port values
            # For LISTEN sockets, faddr=0, fport=0, lport=known value

            inp_iap_offset = -1
            inp_ppcb_offset = -1

            # Read 256 bytes of first entry for calibration
            cal_data = _vmi_read_virt(mon_sock, first_ptr, 64)  # 256 bytes

            # Scan for inaddrpair pattern: look for a local port (network byte order)
            # Common ports: 23 (telnet=0x0017), 80 (http=0x0050), 514 (syslog=0x0202)
            # The pattern is: faddr(4) fport(2) pad(2) laddr(4) lport(2)
            # or: laddr(4) lport(2) pad(2) faddr(4) fport(2)
            for scan_off in range(12, min(len(cal_data) - 14, 200), 4):
                # Try: faddr(4) fport(2) laddr(4) lport(2) with possible padding
                # IRIX inaddrpair: faddr(4) fport(2) [pad 2] laddr(4) lport(2) [pad 2]
                # Total: 16 bytes
                if scan_off + 16 > len(cal_data):
                    break
                faddr = struct.unpack(">I", cal_data[scan_off : scan_off + 4])[0]
                fport = struct.unpack(">H", cal_data[scan_off + 4 : scan_off + 6])[0]
                laddr = struct.unpack(">I", cal_data[scan_off + 8 : scan_off + 12])[0]
                lport = struct.unpack(">H", cal_data[scan_off + 12 : scan_off + 14])[0]

                # Plausible if lport is a common service port or > 0
                if lport > 0 and lport < 65535:
                    # Validate: for a LISTEN socket, faddr and fport should be 0
                    # For any socket, laddr should be 0.0.0.0 or a valid IP
                    if faddr == 0 or (faddr >> 24) in (10, 127, 172, 192):
                        inp_iap_offset = scan_off
                        break

            if inp_iap_offset < 0:
                # Try alternative layout: BSD-style with separate fields
                # Fall back to trying every 2-byte aligned position for port values
                for scan_off in range(12, min(len(cal_data) - 2, 200), 2):
                    port = struct.unpack(">H", cal_data[scan_off : scan_off + 2])[0]
                    if port in (23, 80, 111, 514, 513, 6000, 7100):
                        # Found a well-known port, work backwards to find the struct start
                        # lport is at some fixed offset within inaddrpair
                        inp_iap_offset = max(0, scan_off - 12)
                        break

            def _fmt_ip(addr):
                return f"{(addr >> 24) & 0xFF}.{(addr >> 16) & 0xFF}.{(addr >> 8) & 0xFF}.{addr & 0xFF}"

            # Walk the list
            current = first_ptr
            visited = {head_vaddr}
            max_entries = 200

            while current and current not in visited and len(conns) < max_entries:
                visited.add(current)
                if not (0x80000000 <= current < 0xC0000000):
                    break

                entry_data = _vmi_read_virt(mon_sock, current, 64)  # 256 bytes
                if len(entry_data) < 12:
                    break

                # inp_next at offset 0
                inp_next = struct.unpack(">I", entry_data[0:4])[0]

                if inp_iap_offset >= 0 and inp_iap_offset + 16 <= len(entry_data):
                    faddr = struct.unpack(
                        ">I", entry_data[inp_iap_offset : inp_iap_offset + 4]
                    )[0]
                    fport = struct.unpack(
                        ">H", entry_data[inp_iap_offset + 4 : inp_iap_offset + 6]
                    )[0]
                    laddr = struct.unpack(
                        ">I", entry_data[inp_iap_offset + 8 : inp_iap_offset + 12]
                    )[0]
                    lport = struct.unpack(
                        ">H", entry_data[inp_iap_offset + 12 : inp_iap_offset + 14]
                    )[0]

                    local = f"{_fmt_ip(laddr)}:{lport}"
                    foreign = (
                        f"{_fmt_ip(faddr)}:{fport}" if fport else f"{_fmt_ip(faddr)}:*"
                    )

                    state = ""
                    if is_tcp:
                        # Find inp_ppcb (tcpcb pointer) in the inpcb.
                        # After inaddrpair: hashflags(2) + pad(2) + u1_socket(4) + u1_ppcb(4)
                        # So inp_ppcb is the SECOND kernel pointer after inaddrpair.
                        ptrs_found = 0
                        for state_off in range(
                            inp_iap_offset + 16,
                            min(len(entry_data) - 4, inp_iap_offset + 80),
                            4,
                        ):
                            ptr = struct.unpack(
                                ">I", entry_data[state_off : state_off + 4]
                            )[0]
                            if 0x80000000 <= ptr < 0xC0000000:
                                ptrs_found += 1
                                if ptrs_found < 2:
                                    continue  # Skip u1_socket, want u1_ppcb
                                # This should be inp_ppcb → tcpcb
                                # tcpcb starts with struct ipovly (20 bytes on 32-bit:
                                # ih_next(4) ih_prev(4) ih_x1(1) ih_pr(1) ih_len(2) ih_src(4) ih_dst(4))
                                # t_state (short) follows at offset 20.
                                tcpcb_data = _vmi_read_virt(
                                    mon_sock, ptr, 16
                                )  # 64 bytes
                                if len(tcpcb_data) >= 24:
                                    # Try t_state at offset 20 (32-bit ipovly) and nearby
                                    for ts_off in (20, 18, 22, 24, 16):
                                        if ts_off + 2 <= len(tcpcb_data):
                                            ts = struct.unpack(
                                                ">H", tcpcb_data[ts_off : ts_off + 2]
                                            )[0]
                                            if ts <= 10:
                                                state = TCP_STATES.get(
                                                    ts, f"state={ts}"
                                                )
                                                break
                                if state:
                                    break
                        if not state:
                            state = "UNKNOWN"

                    conns.append((proto_name, local, foreign, state))

                current = inp_next
                if current == head_vaddr:
                    break  # Circled back to head

            return conns

        try:
            all_conns = []

            if proto in ("all", "tcp"):
                tcp_conns = _walk_inpcb("tcb", "tcp", is_tcp=True)
                all_conns.extend(tcp_conns)

            if proto in ("all", "udp"):
                udp_conns = _walk_inpcb("udb", "udp", is_tcp=False)
                all_conns.extend(udp_conns)

            output_lines.append(
                f"## IRIX Network Connections ({len(all_conns)} entries)"
            )
            output_lines.append("")

            if all_conns:
                output_lines.append("```")
                output_lines.append(
                    f"{'Proto':<6} {'Local Address':<24} {'Foreign Address':<24} State"
                )
                for proto_name, local, foreign, state in all_conns:
                    output_lines.append(
                        f"{proto_name:<6} {local:<24} {foreign:<24} {state}"
                    )
                output_lines.append("```")
            else:
                if not symbols.get("tcb") and not symbols.get("udb"):
                    output_lines.append(
                        "*Symbols `tcb`/`udb` not found — provide symbols_file with network symbols*"
                    )
                else:
                    output_lines.append("*No active connections found*")

        except Exception as e:
            output_lines.append(f"Error: {e}")
            import traceback

            output_lines.append(f"```\n{traceback.format_exc()}\n```")
        finally:
            mon_sock.close()

        return "\n".join(output_lines) if output_lines else "*No data*"

    else:
        return f"Unknown tool: {name}"


async def main():
    """Run the MCP server."""
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream, server.create_initialization_options()
            )
    except (BaseExceptionGroup, ExceptionGroup) as eg:
        # When the MCP client disconnects while a tool call is in progress,
        # the write stream closes and anyio raises ClosedResourceError inside
        # a task group ExceptionGroup.  This is expected — suppress it so
        # the server exits cleanly instead of crashing with a traceback.
        import anyio
        real_errors = []
        for exc in eg.exceptions:
            if isinstance(exc, (anyio.ClosedResourceError, BrokenPipeError)):
                continue
            if isinstance(exc, (BaseExceptionGroup, ExceptionGroup)):
                # Nested groups from MCP session internals
                has_only_closed = all(
                    isinstance(e, (anyio.ClosedResourceError, BrokenPipeError))
                    or (isinstance(e, (BaseExceptionGroup, ExceptionGroup))
                        and all(isinstance(ee, (anyio.ClosedResourceError, BrokenPipeError))
                                for ee in e.exceptions))
                    for e in exc.exceptions
                )
                if has_only_closed:
                    continue
            real_errors.append(exc)
        if real_errors:
            raise BaseExceptionGroup("mcp errors", real_errors) from None


if __name__ == "__main__":


    try:
        asyncio.run(main())
    except BaseException:
        import traceback
        with open("/tmp/sgi_mcp_crash.log", "a") as _f:
            import datetime
            _f.write(f"\n--- {datetime.datetime.now()} ---\n")
            traceback.print_exc(file=_f)
        raise
