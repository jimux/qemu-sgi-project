#!/usr/bin/env python3
"""host_channel — drive an IRIX guest directly from the host via the QEMU
gdbstub, with no serial console and no TFTP.

The QEMU gdbstub gives the host arbitrary read/write of guest memory (and CPU
state). That is a complete, serial-free, TFTP-free side channel into the guest:

    write_guest_mem(addr, data)     host -> guest RAM   (gdb `restore`)
    read_guest_mem(addr, length)    guest RAM -> host   (gdb `dump`)
    read_kernel_buffer(sym)         read a kernel global (e.g. the message buf)

Addresses are MIPS KSEG0/KSEG1 (0x8xxxxxxx / 0xAxxxxxxx); they are sign-extended
to the n64 form the IRIX kernel runs in. Use an UNCACHED KSEG1 address
(0xA0000000 | phys) for RAM the guest will also touch — QEMU/TCG has no cache
model so this is purely about matching the guest's own view.

This is the host half of the "direct channel" explored in
progress_notes/direct_host_channel.md.

The RECOMMENDED high-level interface is the `Gateway` class (bottom of this
file): it drives the tiny portable userland agent `gwagent.c` over this same gdb
channel to run commands and transfer files binary-exact, with no serial/TFTP/
device/kernel-patch, identically on Indy and IP54. The low-level mem R/W and the
pvuart RX-inject helpers below remain useful primitives.
"""

import os
import subprocess
import tempfile

GDB = "gdb-multiarch"

_PREAMBLE = [
    "set pagination off",
    "set confirm off",
    "set architecture mips:isa64",
    "set mips abi n64",
    "set endian big",
]


def _sx(addr):
    """Sign-extend a 32-bit KSEG address to the 64-bit n64 form."""
    addr &= 0xFFFFFFFFFFFFFFFF
    if addr < 0x100000000 and (addr & 0x80000000):
        addr |= 0xFFFFFFFF00000000
    return addr


def _run_gdb(cmds, port, timeout=60):
    body = "\n".join(_PREAMBLE + [f"target remote :{port}"] + cmds +
                     ["detach", "quit", ""])
    with tempfile.NamedTemporaryFile("w", suffix=".gdb", delete=False) as f:
        f.write(body)
        path = f.name
    try:
        r = subprocess.run([GDB, "-nx", "-batch", "-x", path],
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout + r.stderr
    finally:
        os.unlink(path)


def write_guest_mem(addr, data, port=1234, timeout=60):
    """Write `data` (bytes) into guest memory at `addr`. Host -> guest, no
    serial, no TFTP. Returns the gdb output."""
    with tempfile.NamedTemporaryFile("wb", suffix=".bin", delete=False) as f:
        f.write(data)
        binpath = f.name
    try:
        start = _sx(addr)
        end = start + len(data)
        return _run_gdb([f"restore {binpath} binary 0x{start:x}",
                         f"x/4xb 0x{start:x}"], port, timeout)
    finally:
        os.unlink(binpath)


def read_guest_mem(addr, length, port=1234, timeout=60):
    """Read `length` bytes from guest memory at `addr`. Guest -> host. Returns
    bytes (or b'' on failure)."""
    out = tempfile.mktemp(suffix=".bin")
    start = _sx(addr)
    end = start + length
    _run_gdb([f"dump binary memory {out} 0x{start:x} 0x{end:x}"], port, timeout)
    if os.path.exists(out):
        data = open(out, "rb").read()
        os.unlink(out)
        return data
    return b""


def read_word(addr, port=1234, timeout=30):
    """Read a 32-bit big-endian word at `addr`."""
    d = read_guest_mem(addr, 4, port, timeout)
    return int.from_bytes(d, "big") if len(d) == 4 else None


def read_kernel_buffer(ptr_sym_addr, size_sym_addr, port=1234, timeout=30):
    """Read a kernel message-style buffer: ptr_sym_addr holds a pointer to the
    buffer, size_sym_addr holds its length. Returns the raw bytes. Lets the
    host capture kernel console state with no serial."""
    bufptr = read_word(ptr_sym_addr, port, timeout)
    size = read_word(size_sym_addr, port, timeout)
    if not bufptr or not size:
        return b""
    return read_guest_mem(bufptr, min(size, 65536), port, timeout)


# ── In-guest kernel hook mailbox ─────────────────────────────────────
# The PROM patches clock() to service this mailbox each tick (see
# prom-building/src/fw/ip54_stubs.c "Host-channel mailbox hook" and
# progress_notes/direct_host_channel.md). Layout in guest RAM:
#   HOOK_MBOX + 0 : byte  — console-out request (host sets; hook emits + clears)
#   HOOK_MBOX + 4 : word  — heartbeat counter (hook increments every tick)
HOOK_MBOX = 0x88054D40

import time as _time


def hook_heartbeat(port=1234, timeout=20):
    """Read the kernel hook's heartbeat counter (proves the hook runs, no serial)."""
    return read_word(HOOK_MBOX + 4, port, timeout)


def hook_putc(ch, port=1234, timeout=20):
    """Queue one byte for the kernel hook to emit to the console.
    `ch` is an int (0-255) or 1-char str."""
    if isinstance(ch, str):
        ch = ord(ch)
    write_guest_mem(HOOK_MBOX, bytes([ch & 0xFF]), port, timeout)


def hook_puts(s, port=1234, per_byte_wait=0.15, timeout=20):
    """Send a string through the kernel hook one byte at a time, waiting for the
    hook to consume (zero) each byte before sending the next."""
    data = s.encode("latin-1") if isinstance(s, str) else bytes(s)
    for b in data:
        hook_putc(b, port, timeout)
        # wait for the hook to clear the mailbox byte (consumed it)
        for _ in range(20):
            if (read_guest_mem(HOOK_MBOX, 1, port, timeout) or b"\x00")[0] == 0:
                break
            _time.sleep(per_byte_wait)


# ── Console input injection + file transfer (no serial, no TFTP) ─────
# The QEMU sgi-pvuart device has an RX-inject register at PA 0x1F62017C
# (KSEG1 0xBF62017C, offset 4). A write there pushes a byte into the UART RX
# FIFO as if received, so the guest's normal console-input path delivers it.
# The host writes that register via the gdbstub — a binary-clean input channel
# with no serial backend. Combined with the booted shell (process context), it
# commits files to the real filesystem without TFTP.
PVUART_RXINJECT = 0xBF62017C


def inject_input(data, port=1234, timeout=120):
    """Push bytes into the guest console input via the pvuart RX-inject register
    (gdb MMIO writes). `data` is bytes/str. The FIFO holds 8192 bytes; for more,
    call repeatedly, letting the guest drain between calls."""
    if isinstance(data, str):
        data = data.encode("latin-1")
    a = _sx(PVUART_RXINJECT)
    cmds = ["set *(unsigned char *)0x%x = %d" % (a, b) for b in data]
    return _run_gdb(cmds, port, timeout)


def push_text_file(content, guest_path, port=1234, settle=1.0, dd="/sbin/dd"):
    """Transfer a text file to the guest filesystem with no serial/TFTP, by
    driving the shell over the RX-inject channel. Uses `dd ... count=N` (exits
    after exactly N bytes — no EOF/signal needed, which matters because the
    minimal single-user tty has VEOF/VINTR disabled). Full paths because the
    shell's PATH is empty. Content should be newline-terminated text (the cooked
    tty delivers input line-buffered); for binary, raw tty mode is required.
    """
    if isinstance(content, str):
        content = content.encode("latin-1")
    n = len(content)
    inject_input("%s of=%s bs=1 count=%d 2>/dev/null\n" % (dd, guest_path, n), port)
    _time.sleep(settle)
    CH = 4000   # stay within the 8192-byte RX FIFO; drain between chunks
    for i in range(0, n, CH):
        inject_input(content[i:i + CH], port)
        _time.sleep(settle)
    return  # dd exits on its own after N bytes


def push_binary_file(content, guest_path, port=1234, settle=1.5,
                     dd="/sbin/dd", stty="/sbin/stty"):
    """Binary-clean file transfer: put the tty in raw mode for the duration of
    the `dd ... count=N` read, so control bytes (CR/NL, ^S/^Q, ^D, NUL, 0xFF)
    pass through untouched, then restore the tty. No serial backend, no TFTP.
    (For the cleanest/fastest binary path, see the RAM-agent design in
    progress_notes/direct_host_channel.md — this avoids needing raw mode.)"""
    if isinstance(content, str):
        content = content.encode("latin-1")
    n = len(content)
    # One cooked-mode command line: raw -> dd reads N raw bytes -> restore.
    cmd = "%s raw -echo; %s of=%s bs=1 count=%d 2>/dev/null; %s sane\n" % (
        stty, dd, guest_path, n, stty)
    inject_input(cmd, port)
    _time.sleep(settle)
    CH = 4000
    for i in range(0, n, CH):
        inject_input(content[i:i + CH], port)
        _time.sleep(settle)
    return


import binascii


def _uuencode(data, name):
    out = b"begin 644 " + name.encode("latin-1") + b"\n"
    for i in range(0, len(data), 45):
        out += binascii.b2a_uu(data[i:i + 45])
    out += binascii.b2a_uu(b"") + b"end\n"
    return out


def push_file(content, guest_path, port=1234, settle=1.0,
              uudecode="/usr/bsd/uudecode", tmp="/tmp/_xfer.uu"):
    """Binary-exact file transfer to the guest fs — no TFTP, no 8-bit tty issues.

    The raw tty path is not 8-bit clean on this console (high bytes get mangled),
    so we uuencode host-side (pure 7-bit printable), push that text over the
    RX-inject channel, and uudecode in the guest. Verified byte-exact by cksum.
    (The RAM-buffer agent in progress_notes/direct_host_channel.md is the cleaner
    no-tty alternative; this works with stock IRIX tools.)"""
    if isinstance(content, str):
        content = content.encode("latin-1")
    uu = _uuencode(content, guest_path)
    push_binary_file(uu, tmp, port, settle=settle)   # uu is 7-bit -> clean
    _time.sleep(settle)
    inject_input("%s %s\n" % (uudecode, tmp), port)
    _time.sleep(settle * 2)


# ── Portable userland-agent gateway ──────────────────────────────────
# gwagent.c spins on a one-page mailbox+buffer it keeps TLB-resident; the host
# drives it purely over the gdb memory channel (no serial, no TFTP, no device,
# no kernel patch -> works on Indy, IP54, any SGI machine running this userland).
# The agent prints the runtime address of its shared page to /tmp/gwaddr at
# startup. Protocol: host writes the command block + sets `cmd`; the agent does
# the work, writes `status` (1 ok, <0 error), then clears `cmd` to 0. `magic`
# lets the host confirm the agent's address space is the current CPU context
# before trusting a read (in multi-user another process may be current).
import struct as _struct


class Gateway:
    # struct gw field offsets (see gwagent.c)
    O_MAGIC = 0
    O_SEQ = 4
    O_CMD = 8
    O_ARG = 12
    O_STATUS = 16
    O_PATH = 24
    PATH_SZ = 256
    O_DATA = 24 + 256          # 280
    DATA_SZ = 65536           # must equal DATA_SZ in gwagent.c (TLB-bounded)
    MAGIC = 0x47574159          # 'GWAY'
    # command codes
    PING, RUN, OPEN_W, WRITE, CLOSE, OPEN_R, READ = 1, 2, 3, 4, 5, 6, 7

    def __init__(self, base, port=1234):
        self.base = base
        self.port = port

    @classmethod
    def attach(cls, port=1234, base=0x10013000, scan=True):
        """Connect to a running gwagent. IRIX n32 executables load at fixed VAs,
        so the shared page is reliably 0x10013000 for this agent; if `scan`, fall
        back to probing page boundaries for the GW_MAGIC marker (handles a
        differently-sized rebuild). Returns a Gateway or None."""
        gw = cls(base, port)
        if gw._rdw(cls.O_MAGIC) == cls.MAGIC:
            return gw
        if scan:
            for a in range(0x10010000, 0x10040000, 0x1000):
                gw = cls(a, port)
                if gw._rdw(cls.O_MAGIC) == cls.MAGIC:
                    return gw
        return None

    def _rd(self, off, n):
        return read_guest_mem(self.base + off, n, self.port)

    def _wr(self, off, data):
        write_guest_mem(self.base + off, data, self.port)

    def _rdw(self, off):
        return read_word(self.base + off, self.port)

    def _wrw(self, off, val):
        self._wr(off, _struct.pack(">I", val & 0xFFFFFFFF))

    def _sstatus(self):
        s = self._rdw(self.O_STATUS)
        if s is None:
            return None
        return s - (1 << 32) if s & 0x80000000 else s

    def is_current(self, tries=30, wait=0.1):
        """True once the agent's address space is the current CPU context."""
        for _ in range(tries):
            if self._rdw(self.O_MAGIC) == self.MAGIC:
                return True
            _time.sleep(wait)
        return False

    def seq(self):
        """Agent heartbeat — advances every spin while the agent is live/current."""
        return self._rdw(self.O_SEQ)

    def _exec(self, cmd, timeout_s=30, wait=0.1):
        """Issue a command; wait for the agent to clear cmd and return status."""
        self._wrw(self.O_CMD, cmd)
        for _ in range(int(timeout_s / wait)):
            _time.sleep(wait)
            if self._rdw(self.O_MAGIC) != self.MAGIC:
                continue                       # agent not current; retry
            if self._rdw(self.O_CMD) == 0:
                return self._sstatus()
        return None

    def _exec_ok(self, cmd, timeout_s=30, retries=6):
        """_exec that retries on transient failure (agent momentarily not the
        current process at the gdb stop). Safe to retry: a non-1 result means the
        agent never cleared cmd, so the op (read/write) did not happen and the
        file offset did not advance. Returns True iff the op completed ok."""
        st = self._exec(cmd, timeout_s)
        tries = 0
        while st != 1 and tries < retries:
            tries += 1
            st = self._exec(cmd, timeout_s)
        return st == 1

    def ping(self):
        st = self._exec(self.PING)
        return st, self._rdw(self.O_ARG)

    def run(self, command, timeout_s=30):
        """Run a shell command in the guest; return (status, stdout bytes).
        Output is whatever fits in one buffer (DATA_SZ-1); pipe through head for
        more, or use pull_file on a redirected file."""
        b = command.encode("latin-1") if isinstance(command, str) else command
        b = b[:self.DATA_SZ - 1]
        self._wr(self.O_DATA, b)
        self._wrw(self.O_ARG, len(b))
        st = self._exec(self.RUN, timeout_s)
        n = self._rdw(self.O_ARG) or 0
        out = self._rd(self.O_DATA, min(n, self.DATA_SZ)) if n else b""
        return st, out

    def _setpath(self, path):
        p = path.encode("latin-1") if isinstance(path, str) else path
        self._wr(self.O_PATH, p[:self.PATH_SZ - 1] + b"\x00")

    def push_file(self, content, guest_path, timeout_s=30):
        """Binary-exact host->guest file write via the agent. Chunks through the
        resident page; no tty, no encoding."""
        if isinstance(content, str):
            content = content.encode("latin-1")
        self._setpath(guest_path)
        if not self._exec_ok(self.OPEN_W, timeout_s):
            return False
        for i in range(0, len(content), self.DATA_SZ):
            chunk = content[i:i + self.DATA_SZ]
            self._wr(self.O_DATA, chunk)
            self._wrw(self.O_ARG, len(chunk))
            if not self._exec_ok(self.WRITE, timeout_s):
                self._exec(self.CLOSE, timeout_s)
                return False
        return self._exec_ok(self.CLOSE, timeout_s)

    def pull_file(self, guest_path, timeout_s=30, max_bytes=64 * 1024 * 1024):
        """Binary-exact guest->host file read via the agent."""
        self._setpath(guest_path)
        if not self._exec_ok(self.OPEN_R, timeout_s):
            return None
        out = b""
        while len(out) < max_bytes:
            if not self._exec_ok(self.READ, timeout_s):
                break                          # genuine error after retries
            n = self._rdw(self.O_ARG) or 0
            if n == 0:
                break                          # read() == 0 -> EOF (short reads OK)
            out += self._rd(self.O_DATA, n)
        self._exec(self.CLOSE, timeout_s)
        return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=1234)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write"); w.add_argument("addr"); w.add_argument("file")
    r = sub.add_parser("read"); r.add_argument("addr"); r.add_argument("len", type=int)
    r.add_argument("--out")
    a = ap.parse_args()
    if a.cmd == "write":
        print(write_guest_mem(int(a.addr, 0), open(a.file, "rb").read(), a.port))
    else:
        data = read_guest_mem(int(a.addr, 0), a.len, a.port)
        if a.out:
            open(a.out, "wb").write(data); print(f"wrote {len(data)} bytes to {a.out}")
        else:
            print(data)
