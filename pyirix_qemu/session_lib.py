"""session_lib — a reusable driver for IP54/IRIX QEMU debug sessions.

Consolidates the boilerplate that was copy-pasted across ~10 run_a_*.py
scripts this session (boot, serial login, run_until marker loops with three
incompatible signatures, screendump, init-0 shutdown, disk restore+inject,
orphan-QEMU cleanup).

Runs INSIDE the Docker dev container (it calls sgi_mcp.server._handle_tool
directly).  Typical use:

    from pyirix_qemu.session_lib import IRIXSession, prepare_instance

    prepare_instance("ip54-test", bank="allfix", inject=[
        ("/workspace/ip54_tftp_staging/desktop_config/inject/Xsession.dt",
         "/var/X11/xdm/Xsession.dt"),
        ("/tmp/flag_on", "/etc/config/desktop"),
    ])

    with IRIXSession(machine="sgi-ip54", ram_mb=256, instance="ip54-test",
                     extra_args="--trace mips_mmu_wildfault") as s:
        s.await_login()
        s.login()                       # serial root shell
        r = s.run_until("ps -ef > /tmp/ps; echo PS_'OK'\\n", "PS_OK")
        if r.panicked: ...
        s.screendump("session")
        # __exit__ runs init 0 + stop + verifies no orphan QEMU

The context manager ALWAYS shuts the guest down cleanly and kills any
orphan QEMU on exit — even on exception — which is the safety rule that
the ad-hoc scripts kept violating.
"""
import os
import re
import sys
import time
import glob
import shutil
import subprocess

sys.path.insert(0, "/workspace")
from sgi_mcp.server import _handle_tool  # noqa: E402

# ── default panic / liveness markers ────────────────────────────────────
PANIC_MARKERS = ("PANIC", "assertion failed", "KERNEL FAULT",
                 "Software detected SEGV", "DOUBLE PANIC")
HALT_MARKERS = ("going down", "halted", ">> ", "maintenance", "Powering",
                "Restarting", "okay to power off", "PROM Monitor")
VM_DIR = "/workspace/vm_instances"
FB_DIR = "/workspace/framebuffers"


def _tool(name, args):
    r = _handle_tool(name, args)
    return r if isinstance(r, str) else str(r)


def guest_lines(result):
    """Extract only the guest-output (code-block) portion of an MCP
    session_send result, so a marker never matches the **Sent:** echo."""
    out, in_block = [], False
    for ln in result.splitlines():
        if ln.startswith("```"):
            in_block = not in_block
            continue
        if in_block:
            out.append(ln)
    return "\n".join(out)


def kill_orphans(verbose=True):
    """Reliably kill any qemu-system-mips64 processes (the scripts leak
    these when TaskStop'd).  Returns the list of pids killed."""
    try:
        out = subprocess.run(["pgrep", "-f", "qemu-system-mips64"],
                             capture_output=True, text=True).stdout
    except Exception:
        return []
    pids = [p for p in out.split() if p.isdigit()]
    for p in pids:
        try:
            os.kill(int(p), 9)
        except Exception:
            pass
    if pids and verbose:
        print(f"[session_lib] killed orphan QEMU pids: {pids}")
    return pids


class RunResult:
    """Unified result for run_until — replaces the 3 incompatible
    signatures (str / (str,bool) / silent-str)."""
    __slots__ = ("output", "found", "panicked")

    def __init__(self, output, found, panicked):
        self.output = output
        self.found = found
        self.panicked = panicked

    def __bool__(self):
        return self.found

    def __contains__(self, s):
        return s in self.output


class IRIXSession:
    def __init__(self, machine="sgi-ip54", ram_mb=256, boot_wait=30,
                 prom="/workspace/PROM_library/bins/cpu/ip54/ip54.bin",
                 instance="ip54-test", extra_args="", echo=True,
                 debug_flags=None, log_file=None):
        self.machine = machine
        self.ram_mb = ram_mb
        self.boot_wait = boot_wait
        self.prom = prom
        self.instance = instance
        self.extra_args = extra_args
        self.echo = echo
        self.debug_flags = debug_flags   # e.g. "unimp" — opens QEMU's -D logfile
        self.log_file = log_file         # path receiving QEMU -d/-D + qemu_log()
        self.sid = None
        self.have_shell = False

    # ── lifecycle ──────────────────────────────────────────────────────
    def boot(self):
        args = {"machine": self.machine, "ram_mb": self.ram_mb,
                "boot_wait": self.boot_wait, "prom": self.prom}
        if self.instance:
            args["instance"] = self.instance
        if self.extra_args:
            args["extra_args"] = self.extra_args
        if self.debug_flags:
            args["debug_flags"] = self.debug_flags
        if self.log_file:
            args["log_file"] = self.log_file
        r = _tool("qemu_session_start", args)
        self._p(r)
        m = re.search(r"`([0-9a-f]{4,})`", r)
        if not m:
            raise RuntimeError(f"no session id in start result:\n{r[:500]}")
        self.sid = m.group(1)
        return self

    def __enter__(self):
        return self.boot()

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.have_shell:
                self.shutdown()
        except Exception as e:
            self._p(f"[session_lib] shutdown error: {e}")
        try:
            if self.sid:
                self._p(_tool("qemu_session_stop", {"session_id": self.sid}))
        except Exception as e:
            self._p(f"[session_lib] stop error: {e}")
        kill_orphans()
        return False  # don't suppress exceptions

    # ── I/O primitives ─────────────────────────────────────────────────
    def _p(self, *a):
        if self.echo:
            print(*a)
            sys.stdout.flush()

    def send(self, text, wait=5, label=""):
        r = _tool("qemu_session_send",
                  {"session_id": self.sid, "text": text, "wait": wait})
        g = guest_lines(r)
        self._p(f"--- send {label or text.strip()[:60]!r}")
        self._p(g if g.strip() else "(no guest output)")
        return g

    def monitor(self, command):
        r = _tool("qemu_session_monitor",
                  {"session_id": self.sid, "command": command})
        self._p(f"### monitor[{command[:40]}]: {r.strip()[:200]}")
        return r

    def sendkey(self, text):
        return _tool("newport_sendkey", {"session_id": self.sid, "text": text})

    def mouse(self, dx=0, dy=0, dz=0, buttons=0):
        return _tool("newport_mouse", {"session_id": self.sid, "dx": dx,
                                       "dy": dy, "dz": dz, "buttons": buttons})

    def run_until(self, cmd, marker, polls=12, wait=5, label=""):
        """Send cmd, poll (sending newlines) until `marker` appears or a
        panic is seen.  ONE canonical signature → RunResult."""
        out = self.send(cmd, max(wait, 6), label=label or cmd.strip()[:50])
        for i in range(polls):
            if any(m in out for m in PANIC_MARKERS):
                return RunResult(out, marker in out, True)
            if marker in out:
                return RunResult(out, True, False)
            time.sleep(2)
            out += "\n" + self.send("\n", wait=wait, label=f"poll {i} {label}")
        panicked = any(m in out for m in PANIC_MARKERS)
        return RunResult(out, marker in out, panicked)

    # ── high-level steps ───────────────────────────────────────────────
    def await_login(self, polls=50, wait=6):
        for i in range(polls):
            r = self.send("\n", wait=wait, label="await boot")
            if "login:" in r:
                return r
        raise RuntimeError("no login prompt")

    def login(self):
        """Serial login: root → (TERM) → exec sh; confirms SHELL_OK."""
        time.sleep(2)
        r = self.send("root\n", 8, label="serial login")
        for i in range(6):
            if "TERM" in r or "#" in r or "Password" in r:
                break
            r = self.send("\n", wait=5)
        if "Password" in r:
            r = self.send("\n", 6, label="empty password")
        if "TERM" in r:
            self.send("\n", 5, label="accept TERM")
        self.send("exec sh\n", 5)
        ok = self.run_until("echo SHELL_'OK'\n", "SHELL_OK", 8, label="shell ok")
        self.have_shell = ok.found
        if not ok.found:
            raise RuntimeError("serial shell did not come up")
        return self

    def set_env(self, display=":0.0", home="/"):
        self.send(f"DISPLAY={display} ; export DISPLAY ; "
                  f"PATH=$PATH:/usr/bin/X11:/usr/sbin:/usr/lib/desktop ; "
                  f"export PATH\n", 4)
        self.send(f"HOME={home} ; export HOME\n", 4)

    def screendump(self, tag, archive_dir=FB_DIR):
        path = f"{archive_dir}/{tag}.png"
        r = _tool("newport_screendump",
                  {"session_id": self.sid, "output_path": path})
        self._p(f"### screendump[{tag}]: {r.strip()[:160]}")
        return path

    def shutdown(self):
        self.send("sync ; sync ; init 0\n", 5)
        for i in range(24):
            r = self.send("\n", wait=5, label="await shutdown")
            if any(m in r or m.lower() in r.lower() for m in HALT_MARKERS):
                break
            time.sleep(3)
        time.sleep(4)
        self.have_shell = False


# ── disk-state helper ───────────────────────────────────────────────────
def prepare_instance(instance, bank="allfix", inject=None, verify=True,
                     restore_nvram=True):
    """cp <instance>/disk.qcow2.<bank> → disk.qcow2 (+ nvram), then inject a
    list of (host_path, guest_path) files, optionally verifying each landed
    as FMT_EXTENTS.  Replaces the inline restore+inject preamble."""
    d = f"{VM_DIR}/{instance}"
    disk = f"{d}/disk.qcow2"
    src = f"{d}/disk.qcow2.{bank}"
    if not os.path.exists(src):
        raise FileNotFoundError(f"disk bank not found: {src}")
    shutil.copy(src, disk)
    if restore_nvram:
        for nv in (f"{d}/nvram.bin.{bank}", f"{d}/nvram.bin.golden"):
            if os.path.exists(nv):
                shutil.copy(nv, f"{d}/nvram.bin")
                break
    print(f"[session_lib] restored {instance} from .{bank}")
    for host, guest in (inject or []):
        r = _tool("fs_inject",
                  {"image": disk, "host_path": host, "guest_path": guest})
        ok = "Overwrote" in r or "Created" in r or "wrote" in r.lower()
        print(f"[session_lib] inject {guest}: {'ok' if ok else r[:80]}")
        if verify:
            v = _tool("xfs_path", {"image": disk, "path": guest})
            t = [l for l in v.splitlines() if "Type:" in l]
            fmt_ok = t and "FMT_EXTENTS" in t[0]
            print(f"[session_lib]   verify {guest}: "
                  f"{t[0].strip() if t else 'MISSING'}"
                  f"{'' if fmt_ok else '  <-- NOT FMT_EXTENTS!'}")
    return disk
