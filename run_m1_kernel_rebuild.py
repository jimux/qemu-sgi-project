#!/usr/bin/env python3
"""M1 kernel rebuild — compile the 5 IP54 paravirtual drivers + lboot-relink
the IRIX kernel for our sgi-ip54 QEMU machine.

Sources come from the irix-ip54 repo (../irix-ip54/...) via the symlink
farm in ip54_tftp_staging/ that mirrors irix-ip54's SGI-style layout into
the flat filenames TFTP expects:
  ip54_tftp_staging/pvfb.c    -> ../irix-ip54/m/irix/kern/io/pvfb.c
  ip54_tftp_staging/IP54.sm   -> ../irix-ip54/sysgen/system/IP54.sm
  ip54_tftp_staging/master.d  -> ../irix-ip54/m/irix/kern/master.d
  (etc; see irix-ip54/BUILDING.md)
The build harness here is qemu-sgi-specific (drives a machine=indy session
through the MCP server); the in-guest pieces (setup_ip54.sh, cc_wrapper.sh,
Makefile.ip54, khdrs.tar) live in irix-ip54/scripts/.

Strategy: boot ip54-test on machine=indy (it's a fork of irix655-dev, so
MIPSpro 7.2.1 and the IP54 build tree are onboard). /unix.new lands directly
on the target disk — no second disk, no extraction.

ALWAYS shuts down cleanly (init 0) in a finally block: SIGKILLing a live
IRIX guest corrupted irix655-dev's root XFS in an earlier session.
"""
import sys, time, re
sys.path.insert(0, "/workspace")
from sgi_mcp.server import _handle_tool

SID = None

def tool(name, args):
    r = _handle_tool(name, args)
    return r if isinstance(r, str) else str(r)

def send(text, wait=5, label=""):
    r = tool("qemu_session_send", {"session_id": SID, "text": text, "wait": wait})
    print(f"--- send {label or text.strip()[:60]!r}")
    print(r)
    sys.stdout.flush()
    return r

def run_until(cmd, marker, polls=40, label=""):
    """Send cmd, then poll until marker appears in the ACCUMULATED output
    (the marker often arrives in the initial send's window)."""
    out = send(cmd, 5, label=label or cmd.strip()[:50])
    for i in range(polls):
        if marker in out:
            return out
        time.sleep(3)
        out += send("\n", wait=5, label=f"poll {i} for {marker}")
    if marker in out:
        return out
    raise RuntimeError(f"FATAL: marker {marker} not seen ({label})")

CC_BASE = ("/usr/cpu/sysgen/root/usr/bin/cc -c -n32 -mips3 -O2 -G 8 "
           "-non_shared -TENV:kernel -DIP54 -D_KERNEL")

print("### starting session")
r = tool("qemu_session_start", {
    "machine": "indy",
    "ram_mb": 256,
    "boot_wait": 35,
    "autoload": True,
    "scsi_drives": [
        "/workspace/vm_instances/ip54-test/disk.qcow2",
    ],
    "extra_args": "-nic user,tftp=/workspace/ip54_tftp_staging",
})
print(r)
m = re.search(r"`([0-9a-f]{4,})`", r)
if not m:
    raise SystemExit("FATAL: no session id")
SID = m.group(1)
print(f"### session {SID}")

ok = False
try:
    # Boot lands either at a login prompt (multi-user) or a root shell
    # (single-user, initdefault=s on this disk).
    state = None
    for i in range(30):
        r = send("\n", wait=5, label="await login/shell")
        if "login:" in r:
            state = "login"
            break
        if re.search(r"(^|\n)#\s*$", r) or "Single-user" in r or "INIT: SINGLE USER" in r:
            state = "shell"
            break
    if state == "login":
        r = send("root\n", 5)
        for i in range(5):
            if "TERM" in r:
                break
            r = send("\n", wait=5, label="await TERM")
        send("\n", 5, label="accept vt100")
        send("exec sh\n", 5)
    elif state == "shell":
        send("exec sh\n", 5)
    else:
        raise RuntimeError("FATAL: no login prompt or shell")

    r = send("echo SHELL_OK_$$\n", 5)
    if "SHELL_OK_" not in r:
        raise RuntimeError("FATAL: Bourne shell not active")

    # Fetch sources + cc wrapper from TFTP staging
    send("ifconfig ec0 10.0.2.15 netmask 255.255.255.0 up\n", 5)
    send("cd /tmp\n", 3)
    send("tftp 10.0.2.2\n", 4)
    send("binary\n", 3)
    send("get pvuart_cn.c /tmp/pvuart_cn.c\n", 5); time.sleep(8)
    send("get pvfb.c /tmp/pvfb.c\n", 5); time.sleep(8)
    send("get pvaudio.c /tmp/pvaudio.c\n", 5); time.sleep(8)
    send("get if_pvnet.c /tmp/if_pvnet.c\n", 5); time.sleep(8)
    send("get pvdisk.c /tmp/pvdisk.c\n", 5); time.sleep(8)
    send("get khdrs.tar /tmp/khdrs.tar\n", 5); time.sleep(15)
    send("get cc_wrapper.sh /tmp/cc\n", 5); time.sleep(5)
    send("quit\n", 3)
    send("chmod +x /tmp/cc\n", 3)
    send("cd /tmp ; tar xf khdrs.tar 2>/dev/null ; echo TAR_DONE\n", 8)
    r = send("ls -l /tmp/pvuart_cn.c /tmp/if_pvnet.c /tmp/pvdisk.c /tmp/cc\n", 5)

    # Compile the five drivers.  if_pvnet needs the khdrs staging tree
    # FIRST in the include path (net/raw.h stub with the RAW_HDRPAD fix
    # shadows /usr/include) + _PAGESZ.
    EXTRA = {"if_pvnet": "-D_PAGESZ=16384 -I/tmp/khdrs"}
    for obj in ("pvuart_cn", "pvfb", "pvaudio", "if_pvnet", "pvdisk"):
        extra = EXTRA.get(obj, "")
        out = run_until(f"{CC_BASE} {extra} -I/usr/include "
                        f"/tmp/{obj}.c -o /tmp/{obj}.o ; "
                        f"echo CC_{obj}_RC=$?\n",
                        f"CC_{obj}_RC=", polls=24, label=f"compile {obj}")
        if f"CC_{obj}_RC=0" not in out:
            raise RuntimeError(f"FATAL: compile of {obj} failed")

    r = send("ls -l /tmp/pvuart_cn.o /tmp/pvfb.o /tmp/pvaudio.o "
             "/tmp/if_pvnet.o /tmp/pvdisk.o\n", 5)

    # Install objects (keep .prev copies)
    run_until("cd /var/sysgen/boot ; "
              "for f in pvuart_cn pvfb pvaudio if_pvnet pvdisk ; do "
              "cp $f.o $f.o.prev 2>/dev/null ; cp /tmp/$f.o $f.o ; done ; "
              "ls -l pvuart_cn.o if_pvnet.o pvdisk.o ; echo INSTRC=$?\n",
              "INSTRC=", polls=8, label="install objects")

    # Relink kernel (cc_wrapper at /tmp/cc per IP54.sm CC: line)
    out = run_until("cd / ; /usr/sbin/lboot -s /var/sysgen/system/IP54.sm "
                    "-u /unix.new ; echo LBRC=$?\n",
                    "LBRC=", polls=120, label="lboot")
    if "LBRC=0" not in out:
        raise RuntimeError("FATAL: lboot failed")

    send("ls -l /unix.new ; touch -t 203001010000 /unix.new ; sync\n", 5)
    ok = True
finally:
    # ALWAYS shut down cleanly — never leave the guest to be SIGKILLed.
    try:
        send("sync ; init 0\n", 5)
        for i in range(24):
            r = send("\n", wait=5, label="await shutdown")
            if ("System going down" in r or "Powering" in r
                    or "halted" in r.lower() or "okay to power off" in r.lower()
                    or "maintenance" in r.lower() or ">> " in r
                    or "PROM Monitor" in r):
                break
            time.sleep(3)
        time.sleep(5)
    except Exception as e:
        print(f"shutdown error: {e}")
    print("### stopping session")
    print(tool("qemu_session_stop", {"session_id": SID}))

print("### DONE OK" if ok else "### DONE WITH ERRORS")
sys.exit(0 if ok else 1)
