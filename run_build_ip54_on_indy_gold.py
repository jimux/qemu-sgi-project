#!/usr/bin/env python3
"""Build the IP54 PV-driver /unix.new on top of the Indy gold disk.

Path A from progress_notes/ip54_refresh_and_parity_2026-06-18.md:
fork the Indy gold (which has full clogin + 4Dwm + Toolchest + faces
+ proper xdm-config + desktop_eoe content) into the ip54-test slot,
boot on machine=indy, compile the IP54 paravirtual drivers + lboot-
relink the kernel, then save the result as the new IP54 gold v2.

Why this rather than run_m1_kernel_rebuild.py: that script drives
the boot via the serial console looking for a `login:` prompt. The
Indy gold has visuallogin=on which directs login to the X
framebuffer; serial gets a getty on ttyd1 but it appears slowly
(120+ s after PROM) and the legacy script's 150 s polling window
misses it. This driver instead:

  1. Boots and DOESN'T wait for the serial prompt at all.
  2. Polls the host's tcp:2324 forward (telnet → guest:23) every
     5 s until inetd's telnetd comes up — usually around 90-150 s
     into boot for the Indy gold.
  3. Drives the entire build via telnet, which is more reliable
     than the flaky IRIX serial driver.

Final disk lands at `prebuilt_disks/ip54-6.5.5-gold-v2.qcow2` (and
its NVRAM at `.nvram.bin`). Boot on machine=sgi-ip54 should give
the Indy-parity Indigo Magic Desktop (clogin face picker + 4Dwm +
Toolchest + indigo background).
"""

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path("/home/jimmy/qemu-sgi")
sys.path.insert(0, str(PROJECT_ROOT))

# Inputs
INDY_GOLD = PROJECT_ROOT / "prebuilt_disks/irix-6.5.5-complete-fixed.qcow2"
INSTANCE_DIR = PROJECT_ROOT / "vm_instances/ip54-test"
INSTANCE_DISK = INSTANCE_DIR / "disk.qcow2"
TFTP_DIR = PROJECT_ROOT / "ip54_tftp_staging"

# QEMU
QEMU_INDY = PROJECT_ROOT / "qemu/build-linux/qemu-system-mips64"
PROM_INDY = PROJECT_ROOT / "PROM_library/bins/cpu/ip24/Indy_ip24prom.070-9101-011.bin"

# Output gold
OUTPUT_GOLD = PROJECT_ROOT / "prebuilt_disks/ip54-6.5.5-gold-v2.qcow2"

# Telnet hostfwd
TELNET_HOST = "localhost"
TELNET_PORT = 2324

# Serial + monitor sockets
TMP_DIR = Path("/tmp/qemu_build_ip54")


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ── Step 1: Prep disk ────────────────────────────────────────────────


def prep_disk():
    log(f"Forking Indy gold → {INSTANCE_DISK}")
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(INDY_GOLD, INSTANCE_DISK)
    # Verify md5 match
    import hashlib
    h1, h2 = hashlib.md5(), hashlib.md5()
    h1.update(open(INDY_GOLD, "rb").read())
    h2.update(open(INSTANCE_DISK, "rb").read())
    assert h1.hexdigest() == h2.hexdigest(), "disk copy mismatch"
    log(f"  disk md5: {h1.hexdigest()}")


# ── Step 2: Launch QEMU ──────────────────────────────────────────────


def launch_qemu() -> subprocess.Popen:
    log("Launching QEMU machine=indy")
    if TMP_DIR.exists():
        shutil.rmtree(TMP_DIR)
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    # Make sure no stale QEMU
    subprocess.run(["pkill", "-KILL", "-f", "qemu-system-mips64"],
                   capture_output=True)
    time.sleep(2)

    cmd = [
        str(QEMU_INDY),
        "-M", "indy", "-m", "256M",
        "-bios", str(PROM_INDY),
        "-display", "gtk",
        "-chardev", f"socket,id=ser0,path={TMP_DIR}/serial.sock,server=on,wait=off",
        "-serial", "chardev:ser0",
        "-monitor", f"unix:{TMP_DIR}/monitor.sock,server,nowait",
        "-global", "sgi-hpc3.autoload=false",
        "-drive", f"if=scsi,bus=0,unit=1,file={INSTANCE_DISK},format=qcow2,cache=writethrough,file.locking=off",
        "-nic", f"user,tftp={TFTP_DIR},hostfwd=tcp::{TELNET_PORT}-10.0.2.15:23",
    ]
    log(f"  cmd: {' '.join(cmd[:8])} ...")
    log_path = "/tmp/build_ip54.qemu.log"
    err_path = "/tmp/build_ip54.qemu.err"
    p = subprocess.Popen(cmd,
                         stdout=open(log_path, "w"),
                         stderr=open(err_path, "w"))
    log(f"  pid={p.pid}, logs: {log_path}, {err_path}")
    return p


def mon_send(cmd: str):
    """Send one command via QEMU monitor socket."""
    s = socket.socket(socket.AF_UNIX)
    s.connect(str(TMP_DIR / "monitor.sock"))
    s.settimeout(2)
    try:
        while True:
            if b"(qemu)" in s.recv(4096):
                break
    except socket.timeout:
        pass
    s.sendall(cmd.encode() + b"\n")
    time.sleep(0.3)
    try:
        while True:
            if b"(qemu)" in s.recv(4096):
                break
    except socket.timeout:
        pass
    s.close()


def press_start_system():
    """Drive PROM menu to start system boot (key '1' on the framebuffer
    + Enter, since indy with -display gtk shows PROM on the gtk window,
    but with the default NVRAM may also accept serial input)."""
    # Try BOTH paths — sendkey for PS/2 input, plus serial newline poke
    log("Sending PROM 'Start System' via PS/2 + serial")
    mon_send("sendkey 1")
    mon_send("sendkey ret")
    # Also send via serial in case console=d
    try:
        s = socket.socket(socket.AF_UNIX)
        s.connect(str(TMP_DIR / "serial.sock"))
        s.settimeout(2)
        s.sendall(b"1\r")
        time.sleep(0.3)
        s.close()
    except Exception as e:
        log(f"  serial poke skipped: {e}")


# ── Step 3: Wait for telnet ──────────────────────────────────────────


def wait_for_telnet(max_wait: int = 600) -> bool:
    log(f"Waiting up to {max_wait}s for inetd/telnetd on {TELNET_HOST}:{TELNET_PORT}")
    deadline = time.time() + max_wait
    last_log = time.time() - 30
    while time.time() < deadline:
        try:
            s = socket.socket()
            s.settimeout(3)
            s.connect((TELNET_HOST, TELNET_PORT))
            s.close()
            log(f"  telnet up after {int(time.time() - (deadline - max_wait))}s")
            return True
        except Exception:
            pass
        if time.time() - last_log > 30:
            log(f"  still waiting ({int(time.time() - (deadline - max_wait))}s elapsed)")
            last_log = time.time()
        time.sleep(5)
    log("  TIMED OUT")
    return False


# ── Step 4: Drive build via telnet ───────────────────────────────────


def drive_build():
    log("Connecting via telnet to drive kernel build")
    from pyirix_qemu.irix_telnet import IRIXTelnet
    t = IRIXTelnet(host=TELNET_HOST, port=TELNET_PORT, timeout=30)
    t.login(user="root")
    log("  logged in as root")
    t.run("exec /bin/sh", timeout=10)

    # Pull all the IP54 source files via tftp
    log("Fetching IP54 driver sources via tftp")
    t.run("ifconfig ec0 10.0.2.15 netmask 255.255.255.0 up", timeout=10)
    t.run("cd /tmp", timeout=5)

    # Use a script-style heredoc to drive tftp (one connection, many gets)
    tftp_cmds = "\n".join([
        "binary",
        "get pvuart_cn.c /tmp/pvuart_cn.c",
        "get pvfb.c /tmp/pvfb.c",
        "get pvaudio.c /tmp/pvaudio.c",
        "get if_pvnet.c /tmp/if_pvnet.c",
        "get pvdisk.c /tmp/pvdisk.c",
        "get khdrs.tar /tmp/khdrs.tar",
        "get cc_wrapper.sh /tmp/cc",
        "quit",
        "",
    ])
    # Send tftp interactively
    t.send("tftp 10.0.2.2\n")
    time.sleep(2)
    t.send(tftp_cmds.replace("\n", "\n"))   # newlines become Enters
    time.sleep(60)        # let all the gets complete (khdrs.tar is 8 MB)

    log("Verifying source files arrived")
    rc, out = t.run("ls -la /tmp/pvuart_cn.c /tmp/pvfb.c /tmp/pvaudio.c "
                    "/tmp/if_pvnet.c /tmp/pvdisk.c /tmp/khdrs.tar /tmp/cc",
                    timeout=15)
    print(out)

    t.run("chmod +x /tmp/cc", timeout=5)
    t.run("cd /tmp && tar xf khdrs.tar 2>/dev/null && echo TAR_DONE",
          timeout=30)

    # Compile the 5 drivers
    log("Compiling drivers")
    cc_base = ("/usr/cpu/sysgen/root/usr/bin/cc -c -n32 -mips3 -O2 -G 8 "
               "-non_shared -TENV:kernel -DIP54 -D_KERNEL")
    extra = {"if_pvnet": "-D_PAGESZ=16384 -I/tmp/khdrs"}
    for obj in ("pvuart_cn", "pvfb", "pvaudio", "if_pvnet", "pvdisk"):
        ext = extra.get(obj, "")
        cmd = (f"{cc_base} {ext} -I/usr/include "
               f"/tmp/{obj}.c -o /tmp/{obj}.o ; echo CC_{obj}_RC=$?")
        log(f"  compile {obj}")
        rc, out = t.run(cmd, timeout=120)
        print(out)
        if f"CC_{obj}_RC=0" not in out:
            raise RuntimeError(f"compile of {obj} failed; output:\n{out}")

    log("Installing .o files into /var/sysgen/boot/")
    rc, out = t.run(
        "cd /var/sysgen/boot ; "
        "for f in pvuart_cn pvfb pvaudio if_pvnet pvdisk ; do "
        "  cp $f.o $f.o.prev 2>/dev/null ; cp /tmp/$f.o $f.o ; "
        "done ; echo INSTRC=$?", timeout=30)
    print(out)
    if "INSTRC=0" not in out:
        raise RuntimeError(f"object install failed:\n{out}")

    log("Running lboot to relink the kernel")
    rc, out = t.run(
        "cd / ; /usr/sbin/lboot -s /var/sysgen/system/IP54.sm "
        "-u /unix.new ; echo LBRC=$?", timeout=600)
    print(out[-3000:])
    if "LBRC=0" not in out:
        raise RuntimeError(f"lboot failed:\n{out[-2000:]}")

    log("Verifying /unix.new")
    rc, out = t.run("ls -l /unix.new", timeout=10)
    print(out)

    log("Sync + clean shutdown")
    t.run("sync ; sync ; sync", timeout=20)
    t.send("init 0\n")
    t.close()


# ── Step 5: Halt + save ──────────────────────────────────────────────


def wait_for_halt(p: subprocess.Popen, timeout: int = 120):
    log(f"Waiting up to {timeout}s for QEMU to exit cleanly")
    try:
        p.wait(timeout=timeout)
        log(f"  QEMU exited with code {p.returncode}")
    except subprocess.TimeoutExpired:
        log("  TIMED OUT waiting for QEMU; SIGKILL")
        p.kill()
        p.wait(timeout=5)


def save_gold():
    log(f"Saving result as {OUTPUT_GOLD}")
    OUTPUT_GOLD.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(INSTANCE_DISK, OUTPUT_GOLD)
    log(f"  done ({OUTPUT_GOLD.stat().st_size:,} bytes)")


# ── Main ─────────────────────────────────────────────────────────────


def main():
    log("=" * 64)
    log("Path A: build IP54 kernel on top of Indy gold")
    log("=" * 64)
    prep_disk()
    p = launch_qemu()
    try:
        # Need to start the system from PROM. With autoload=false, PROM
        # waits for a key. Send "1" + Enter after a brief delay.
        time.sleep(15)        # wait for PROM menu to appear
        press_start_system()

        if not wait_for_telnet(max_wait=600):
            raise RuntimeError("telnet never came up — boot stalled")

        drive_build()

        # init 0 sent. Wait for QEMU to exit cleanly.
        wait_for_halt(p, timeout=180)
    finally:
        if p.poll() is None:
            log("forcing QEMU exit")
            p.kill()
            p.wait(timeout=5)

    save_gold()
    log("=" * 64)
    log(f"### DONE — IP54 gold v2 at {OUTPUT_GOLD}")
    log("=" * 64)


if __name__ == "__main__":
    main()
