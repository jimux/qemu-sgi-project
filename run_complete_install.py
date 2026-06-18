#!/usr/bin/env python3
"""End-to-end complete IRIX 6.5.5 install.

Strategy: drive the proven `install_irix()` (partition + miniroot + inst +
kernel build + boot verify) with the most-coverage settings, then layer
the new install_v2 completeness verifier on top. Surface the manifest
gaps; if everything passes, promote the disk to the new golden.

Lands at /home/jimmy/qemu-sgi/vm_instances/ip54-fresh/disk.qcow2. The
existing ip54-test golden is NOT touched — it stays usable for IP54
kernel work until the fresh install is verified complete.

Run with:
    python3 run_complete_install.py 2>&1 | tee /tmp/install_complete.log

Or backgrounded (the typical path — install takes 30-90 min):
    nohup python3 run_complete_install.py > /tmp/install_complete.log 2>&1 &
"""

import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

PROJECT_ROOT = Path("/home/jimmy/qemu-sgi")
sys.path.insert(0, str(PROJECT_ROOT))

INSTANCE = "ip54-fresh"
RAM_MB = 256
DISK_MB = 4096            # 4 GB — fits Indigo Magic + MIPSpro + dev + demos
INSTALL_LEVEL = "default"  # "everything available" rather than SGI's stripped "standard"
CONFLICT_MODE = "auto"     # use the legacy blind resolve for the first pass;
                           # the verifier tells us what got skipped

MANIFEST = (PROJECT_ROOT
            / "pyirix_qemu/install_v2/completeness/manifest_6_5_5.yaml")
NEW_GOLDEN = (PROJECT_ROOT
              / "prebuilt_disks/irix-6.5.5-complete.qcow2")


def log(msg):
    """Stamped print → flushed immediately. Background-runs need this."""
    t = time.strftime("%H:%M:%S")
    print(f"[{t}] {msg}", flush=True)


def banner(msg):
    log("=" * 64)
    log(msg)
    log("=" * 64)


def run_install():
    banner(f"PHASE 1 — full IRIX 6.5.5 install into {INSTANCE}")
    log(f"  RAM:           {RAM_MB} MB")
    log(f"  disk:          {DISK_MB} MB")
    log(f"  install_level: {INSTALL_LEVEL}")
    log(f"  conflict_mode: {CONFLICT_MODE}")

    # Late import — install module reloads sgi_mcp.* per-call; avoid
    # importing at top level to keep state clean.
    import pyirix_qemu.install.irix as installer

    # Stamp installer logs into our stream too so the unified log shows
    # everything in one place.
    orig_log = installer.log

    def _capture(msg):
        log(f"INSTALLER: {msg}")
        try:
            orig_log(msg)
        except Exception:
            pass

    installer.log = _capture

    t0 = time.time()
    result = installer.install_irix(
        "6.5.5",
        instance=INSTANCE,
        ram_mb=RAM_MB,
        disk_size_mb=DISK_MB,
        conflict_mode=CONFLICT_MODE,
        install_level=INSTALL_LEVEL,
        inst_debug=False,
    )
    elapsed = time.time() - t0
    log(f"install_irix returned in {elapsed:.0f}s; result={result!r}")
    return result


def run_verifier(disk_path):
    banner("PHASE 2 — verify completeness against the new manifest")
    log(f"  disk:     {disk_path}")
    log(f"  manifest: {MANIFEST}")

    from pyirix_qemu.install_v2.completeness.check import (
        HostBackend, verify, Report)

    backend = HostBackend(str(disk_path))
    try:
        report: Report = verify(backend, str(MANIFEST))
    finally:
        backend.close()

    log("")
    report.print()
    log("")
    log("SUMMARY: " + report.summary())
    return report


def promote_to_golden(disk_path):
    banner(f"PHASE 3 — promote to {NEW_GOLDEN}")
    NEW_GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    if NEW_GOLDEN.exists():
        backup = NEW_GOLDEN.with_suffix(NEW_GOLDEN.suffix + ".prev")
        log(f"  backing up existing golden -> {backup}")
        shutil.move(str(NEW_GOLDEN), str(backup))
    log(f"  copy: {disk_path} -> {NEW_GOLDEN}")
    shutil.copy2(str(disk_path), str(NEW_GOLDEN))
    log(f"  done: {NEW_GOLDEN} ({NEW_GOLDEN.stat().st_size:,} bytes)")


def main():
    banner("Complete IRIX 6.5.5 install — fresh install + manifest verify")
    log(f"  project root: {PROJECT_ROOT}")
    log(f"  target inst:  {INSTANCE}")

    try:
        run_install()
    except SystemExit as e:
        log(f"FATAL: install_irix exited: {e}")
        traceback.print_exc()
        sys.exit(2)
    except Exception as e:
        log(f"FATAL: install_irix raised: {type(e).__name__}: {e}")
        traceback.print_exc()
        sys.exit(2)

    disk_path = PROJECT_ROOT / "vm_instances" / INSTANCE / "disk.qcow2"
    if not disk_path.exists():
        log(f"FATAL: expected disk not found: {disk_path}")
        sys.exit(2)

    try:
        report = run_verifier(disk_path)
    except Exception as e:
        log(f"FATAL: verifier raised: {type(e).__name__}: {e}")
        traceback.print_exc()
        sys.exit(3)

    # Always print a final structured summary line that's easy to grep for.
    summary_obj = {
        "instance": INSTANCE,
        "disk": str(disk_path),
        "passed": report.passed,
        "required_failures": [
            {"path": r.path, "why": r.why, "detail": r.detail}
            for r in report.required_failures
        ],
        "summary": report.summary(),
    }
    log("=== STRUCTURED_SUMMARY === " + json.dumps(summary_obj))

    if report.passed:
        log("INSTALL COMPLETE — verifier says all required entries present.")
        promote_to_golden(disk_path)
        log("### DONE OK")
        sys.exit(0)

    log("INSTALL INCOMPLETE — verifier found gaps. Required failures:")
    for f in report.required_failures:
        log(f"  - {f.path}  ({f.detail or 'missing'}) — {f.why}")
    log("### DONE WITH GAPS — see required_failures above")
    log("Next step: run targeted addon installs for the missing subsystems.")
    sys.exit(1)


if __name__ == "__main__":
    main()
