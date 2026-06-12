"""guest_gdb — drive gdb-multiarch against the QEMU guest MIPS64 kernel.

QEMU's gdbstub is fully wired; launch a session with extra_args="-gdb tcp::PORT"
and this connects gdb-multiarch to the live kernel, sets (hardware) breakpoints
at kernel VAs, runs the guest until a breakpoint hits (e.g. `panic`), and dumps
the full register state + a stack scan for return addresses + symbolized PCs.

Symbols come from the disk-kernel JSON (ip54_kernel_symbols_disk.json) — the
running /unix.new — NOT from irix_unix.elf (a different build).  gdb breakpoints
use raw addresses; symbolization is done here in Python.

Typical use (the guest must already be booted+logged-in and about to crash):

    g = GuestGDB(port=1234)
    out = g.catch(breakpoints=["panic"], timeout=120)   # blocks until hit
    print(g.symbolize_dump(out))
"""
import json
import bisect
import subprocess

SYMS_JSON = "/workspace/ip54_kernel_symbols_disk.json"


class SymbolDB:
    """Cached kernel symbol service: name->addr, addr->name+offset."""
    _cache = {}

    def __init__(self, path=SYMS_JSON):
        if path not in SymbolDB._cache:
            d = json.load(open(path))
            funcs = sorted((s["address"] & 0xffffffff, s["name"])
                           for s in d if s.get("type") == "FUNC")
            byname = {s["name"]: (s["address"] & 0xffffffff) for s in d}
            SymbolDB._cache[path] = (funcs, [a for a, _ in funcs], byname)
        self.funcs, self.addrs, self.byname = SymbolDB._cache[path]

    def addr(self, name):
        a = self.byname.get(name)
        if a is None:
            raise KeyError(f"symbol not found: {name}")
        return a

    def lookup(self, addr):
        a = addr & 0xffffffff
        i = bisect.bisect_right(self.addrs, a) - 1
        if i < 0:
            return None
        name, base = self.funcs[i][1], self.funcs[i][0]
        return f"{name}+0x{a - base:x}"

    def is_kernel_text(self, addr):
        a = addr & 0xffffffff
        return 0x88000000 <= a <= 0x88300000


class GuestGDB:
    def __init__(self, port=1234, syms=SYMS_JSON):
        self.port = port
        self.sdb = SymbolDB(syms)

    @staticmethod
    def _sx(addr):
        """Sign-extend a 32-bit KSEG VA to the 64-bit form the n64 ABI expects:
        0x881a34b4 -> 0xffffffff881a34b4.  Leaves already-64-bit values alone."""
        a = addr & 0xffffffffffffffff
        if a < 0x100000000 and (a & 0x80000000):
            a |= 0xffffffff00000000
        return a

    def _resolve(self, bp):
        """Accept a symbol name or a hex/int address; return a gdb break spec.
        Kernel addresses are sign-extended (n64 ABI) so the bp matches $pc."""
        if isinstance(bp, int):
            return f"*0x{self._sx(bp):x}"
        if bp.startswith("0x"):
            return f"*0x{self._sx(int(bp, 16)):x}"
        return f"*0x{self._sx(self.sdb.addr(bp)):x}"

    def catch(self, breakpoints, timeout=180, post_cmds=None, cmdfile="/workspace/_gdb_catch.txt"):
        """Connect, set hbreak(s), continue (blocks until a bp hits or timeout),
        then dump registers + a stack scan.  Returns gdb's stdout."""
        cmds = [
            "set pagination off",
            "set confirm off",
            "set architecture mips:isa64",
            # gdb's MIPS pointer width follows the ABI, not the ISA: the default
            # o32/n32 ABI keeps addresses 32-bit, so gdb zero-extends KSEG0 VAs
            # into unmapped xkphys ("Cannot access memory") and breakpoint
            # planting (a memory write) silently fails.  n64 = 64-bit pointers.
            "set mips abi n64",
            "set endian big",
            f"target remote :{self.port}",
        ]
        for bp in breakpoints:
            cmds.append(f"hbreak {self._resolve(bp)}")
        cmds.append("continue")
        # On breakpoint hit (or async stop), dump everything useful:
        cmds += [
            'echo \\n==== STOPPED ====\\n',
            "info registers",
            'echo \\n==== STACK (256 words from $sp) ====\\n',
            "x/256xw $sp",
            'echo \\n==== code at $pc ====\\n',
            "x/8i $pc",
        ]
        cmds += (post_cmds or [])
        cmds += ["detach", "quit", ""]
        with open(cmdfile, "w") as f:
            f.write("\n".join(cmds))
        try:
            r = subprocess.run(["gdb-multiarch", "-nx", "-batch", "-x", cmdfile],
                               capture_output=True, text=True, timeout=timeout)
            out, err = r.stdout, r.stderr
        except subprocess.TimeoutExpired as e:
            # gdb's `continue` blocked (no breakpoint hit within timeout).
            out = (e.stdout or b"")
            err = (e.stderr or b"")
            out = out.decode() if isinstance(out, bytes) else (out or "")
            err = err.decode() if isinstance(err, bytes) else (err or "")
            out += "\n[GDB TIMEOUT — breakpoint not hit within %ds]" % timeout
        return out + ("\n[GDB STDERR]\n" + err if err.strip() else "")

    def read_word(self, addr, timeout=30, cmdfile="/workspace/_gdb_rw.txt"):
        """One-shot: connect, read a 32-bit word, detach (does NOT stop a
        running guest for long)."""
        cmds = [
            "set pagination off", "set confirm off",
            "set architecture mips:isa64", "set mips abi n64", "set endian big",
            f"target remote :{self.port}",
            f"x/1xw 0x{addr & 0xffffffffffffffff:x}",
            "detach", "quit", "",
        ]
        with open(cmdfile, "w") as f:
            f.write("\n".join(cmds))
        r = subprocess.run(["gdb-multiarch", "-nx", "-batch", "-x", cmdfile],
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout

    def symbolize_dump(self, out):
        """Post-process a catch() dump: annotate every kseg0 word in the stack
        scan with its symbol (return-address candidates), and the PC."""
        import re
        lines = out.splitlines()
        result = []
        for ln in lines:
            result.append(ln)
            # stack words: lines like "0xADDR:  0xWORD 0xWORD 0xWORD 0xWORD"
            words = re.findall(r"0x([0-9a-f]{8})\b", ln)
            syms = []
            for w in words:
                v = int(w, 16)
                if self.sdb.is_kernel_text(v):
                    s = self.sdb.lookup(v)
                    if s:
                        syms.append(f"{w}={s}")
            if syms and ":" in ln:  # a memory/x line
                result.append("        ^syms: " + "  ".join(syms))
        # also pull the pc line
        m = re.search(r"\bpc\s+0x([0-9a-f]+)", out)
        if m:
            result.append(f"\n### PC = {self.sdb.lookup(int(m.group(1), 16))}")
        return "\n".join(result)
