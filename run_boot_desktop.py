#!/usr/bin/env python3
"""Boot the v2 gold image and watch for the Indigo Magic Desktop to come up.

Boots `vm_instances/ip54-desktop-test/disk.qcow2` with the GTK display
visible. Drives the serial console only to confirm boot progression
(PROM → kernel → init → xdm/clogin); the desktop should appear in the
GTK window once xdm starts. Captures a framebuffer screenshot once the
boot stalls (whether at clogin or elsewhere) for inspection.

Run with: QEMU_DISPLAY=gtk python3 run_boot_desktop.py
"""
import sys, time, re, signal
from pathlib import Path

sys.path.insert(0, "/home/jimmy/qemu-sgi")
from pyirix_qemu.boot_harness import QEMUSession

DISK = "/home/jimmy/qemu-sgi/vm_instances/ip54-desktop-test/disk.qcow2"

# Milestones we expect to see during a healthy boot to clogin:
MILESTONES = [
    (r"System Maintenance Menu",      "PROM menu"),
    (r"Starting up",                  "kernel boot"),
    (r"IRIX Release",                 "kernel up"),
    (r"checkquota",                   "fsck/checkquota"),
    (r"Setting hostname",             "init scripts start"),
    (r"Mounting filesystems",         "mount /usr/etc"),
    (r"swap added",                   "swap"),
    (r"sendmail|Internet",            "network up"),
    (r"Starting xdm",                 "xdm starting"),
    (r"login:",                       "console login prompt"),
]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    log("Booting v2 gold-image fork (Indigo Magic Desktop target)")
    log(f"  disk: {DISK}")

    # NOTE: -display gtk is set via QEMU_DISPLAY env (boot_harness.py:144).
    with QEMUSession(
        machine="indy",
        ram_mb=256,
        scsi_drives=[DISK],
        extra_args=["-icount", "shift=0,sleep=off"],
        repeat_threshold=0,
    ) as q:
        log("QEMU session open — watching for boot milestones")

        # PROM menu → select "Start system" (option 1)
        result = q.wait_for(r"Option\?", timeout=10, max_wait=180)
        if not result.matched:
            log("FATAL: no PROM menu within 180s")
            return 2
        log("  PROM menu reached")
        q.send("1\r")

        # Watch for each milestone. As one appears, log it and move on.
        seen = set()
        deadline = time.time() + 600   # 10 min total budget to reach login
        last_log_time = time.time()
        while time.time() < deadline:
            remaining = deadline - time.time()
            patterns = "|".join(p for p, _ in MILESTONES if p not in seen)
            if not patterns:
                log("All milestones seen — boot reached login prompt")
                break
            try:
                r = q.wait_for(patterns, timeout=10, max_wait=min(60, remaining))
            except Exception as e:
                log(f"wait_for error: {e}")
                break
            if not r.matched:
                # No milestone in this window — check if we're idle but
                # the framebuffer might show clogin already.
                if time.time() - last_log_time > 30:
                    log(f"  (idle waiting; {int(remaining)}s left)")
                    last_log_time = time.time()
                continue
            # Identify which milestone fired
            for pat, name in MILESTONES:
                if pat in seen: continue
                if re.search(pat, r.output):
                    log(f"  ✓ {name}")
                    seen.add(pat)
                    last_log_time = time.time()
                    if name == "console login prompt":
                        log("Console login appeared — clogin may already be up "
                            "on the GTK framebuffer.")
                        # Don't log in here — clogin is the GUI login; the
                        # serial console "login:" is for fallback.
                    break

        log("=== boot phase done; observed milestones ===")
        for pat, name in MILESTONES:
            status = "✓" if pat in seen else "X"
            log(f"  {status} {name}")

        # Stay alive — let the user interact with the GTK desktop.
        # Send Ctrl-C to this script (SIGINT) to initiate clean shutdown.
        log("")
        log("================================================================")
        log("Desktop should now be visible in the QEMU GTK window.")
        log("If clogin is up: login as 'root' (no password by default).")
        log("Send SIGINT (Ctrl-C) to this script for a clean shutdown.")
        log("================================================================")

        # Hold open while watching for crashes / panics
        try:
            while True:
                time.sleep(5)
                # Watch for panic patterns
                r = q.wait_for(r"panic|PANIC|out of memory|kernel.*bug",
                               timeout=2, max_wait=3)
                if r.matched:
                    log(f"⚠️  Possible kernel issue: {r.output[-200:]}")
        except KeyboardInterrupt:
            log("SIGINT received — initiating clean shutdown")
            # Send Ctrl-C to break any login prompt, then sync + halt
            q.send("\x03")
            time.sleep(1)
            q.send("root\r")
            time.sleep(2)
            q.send("\r")  # TERM
            time.sleep(1)
            q.send("sync ; init 0\r")
            log("  Sent: sync ; init 0")
            for _ in range(30):
                time.sleep(2)
                r = q.wait_for(r"okay to power off|halted|Power down",
                               timeout=1, max_wait=2)
                if r.matched:
                    log("  Halt complete — closing")
                    break
            return 0


if __name__ == "__main__":
    sys.exit(main())
