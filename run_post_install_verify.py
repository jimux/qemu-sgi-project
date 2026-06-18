#!/usr/bin/env python3
"""Post-install verifier + promoter.

The legacy install_irix() writes the disk to cfg["default_disk"] =
prebuilt_disks/irix-6.5.5-base.qcow2 regardless of the `instance` arg
(the instance arg controls snapshot/NVRAM bookkeeping, not the disk
path). So `run_complete_install.py`'s phase-2 lookup at
vm_instances/ip54-fresh/disk.qcow2 will miss.

This script:
  1. Reads the disk at the legacy path
  2. Runs the install_v2 completeness verifier
  3. If complete: promotes to prebuilt_disks/irix-6.5.5-complete.qcow2
     AND copies into vm_instances/ip54-fresh/disk.qcow2 for fork-ability
  4. If not: prints the gaps

Run with:
    python3 run_post_install_verify.py
"""

import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path("/home/jimmy/qemu-sgi")
sys.path.insert(0, str(PROJECT_ROOT))

INSTALL_DISK = PROJECT_ROOT / "prebuilt_disks" / "irix-6.5.5-base.qcow2"
NEW_GOLDEN = PROJECT_ROOT / "prebuilt_disks" / "irix-6.5.5-complete.qcow2"
INSTANCE_DISK = PROJECT_ROOT / "vm_instances" / "ip54-fresh" / "disk.qcow2"
MANIFEST = (PROJECT_ROOT
            / "pyirix_qemu/install_v2/completeness/manifest_6_5_5.yaml")


def main():
    if not INSTALL_DISK.exists():
        print(f"ERROR: install disk not found: {INSTALL_DISK}", flush=True)
        sys.exit(2)

    size_mb = INSTALL_DISK.stat().st_size / 1024 / 1024
    print(f"Verifying {INSTALL_DISK} ({size_mb:.1f} MB)", flush=True)

    from pyirix_qemu.install_v2.completeness.check import (
        HostBackend, verify)

    backend = HostBackend(str(INSTALL_DISK))
    try:
        report = verify(backend, str(MANIFEST))
    finally:
        backend.close()

    print("")
    report.print()
    print("")
    print("SUMMARY:", report.summary(), flush=True)

    summary_obj = {
        "disk": str(INSTALL_DISK),
        "passed": report.passed,
        "required_failures": [
            {"path": r.path, "why": r.why, "detail": r.detail}
            for r in report.required_failures
        ],
        "summary": report.summary(),
    }
    print("=== STRUCTURED_SUMMARY === " + json.dumps(summary_obj),
          flush=True)

    if not report.passed:
        print(f"\nINCOMPLETE — {len(report.required_failures)} required gap(s).",
              flush=True)
        print("Run targeted addon installs for the missing subsystems "
              "before promoting.", flush=True)
        sys.exit(1)

    # Promote.
    NEW_GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    print(f"\nCOMPLETE — promoting to {NEW_GOLDEN}", flush=True)
    if NEW_GOLDEN.exists():
        backup = NEW_GOLDEN.with_suffix(NEW_GOLDEN.suffix + ".prev")
        print(f"  backing up: {backup}", flush=True)
        shutil.move(str(NEW_GOLDEN), str(backup))
    shutil.copy2(str(INSTALL_DISK), str(NEW_GOLDEN))
    print(f"  copied to:  {NEW_GOLDEN}", flush=True)

    INSTANCE_DISK.parent.mkdir(parents=True, exist_ok=True)
    if INSTANCE_DISK.exists():
        INSTANCE_DISK.unlink()
    print(f"  fork copy:  {INSTANCE_DISK}", flush=True)
    shutil.copy2(str(INSTALL_DISK), str(INSTANCE_DISK))

    print("\n### DONE OK", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
