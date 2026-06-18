#!/usr/bin/env python3
"""Drive the v2 install harness end-to-end against the irix_6_5_5_dev profile.

This is the GOAL-MEETING run: v2 orchestrator owns the install (its own
phases, its own conflict policy, its own completeness verifier). On
success, the disk is promoted to prebuilt_disks/irix-6.5.5-complete.qcow2.

Run with:
    QEMU_DISPLAY=gtk python3 run_v2_install.py 2>&1 | tee /tmp/v2_install.log

Or backgrounded:
    QEMU_DISPLAY=gtk nohup python3 run_v2_install.py > /tmp/v2_install.log 2>&1 &
"""

import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path

PROJECT_ROOT = Path("/home/jimmy/qemu-sgi")
sys.path.insert(0, str(PROJECT_ROOT))

PROFILE = "irix_6_5_5_dev"
INSTANCE = "ip54-fresh"

# Make sure QEMU_DISPLAY is honored — log loudly if it's missing.
if not os.environ.get("QEMU_DISPLAY"):
    print("WARNING: QEMU_DISPLAY not set; QEMU will run headless. "
          "Set QEMU_DISPLAY=gtk to see the install window.", flush=True)


def banner(msg):
    t = time.strftime("%H:%M:%S")
    print(f"[{t}] {'=' * 64}", flush=True)
    print(f"[{t}] {msg}", flush=True)
    print(f"[{t}] {'=' * 64}", flush=True)


def main():
    banner("v2 install harness — complete IRIX desktop gold image")
    print(f"  profile:  {PROFILE}", flush=True)
    print(f"  instance: {INSTANCE}", flush=True)
    print(f"  QEMU_DISPLAY={os.environ.get('QEMU_DISPLAY', '(unset)')}",
          flush=True)

    # Set up logging so we see what the orchestrator does.
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
        stream=sys.stdout,
    )

    from pyirix_qemu.install_v2.orchestrator import install

    t0 = time.time()
    try:
        ctx = install(profile_name=PROFILE, instance=INSTANCE)
    except SystemExit as e:
        print(f"FATAL: orchestrator SystemExit: {e}", flush=True)
        traceback.print_exc()
        sys.exit(2)
    except Exception as e:
        print(f"FATAL: orchestrator raised {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        sys.exit(2)
    elapsed = time.time() - t0

    banner(f"v2 orchestrator returned in {elapsed:.0f}s")

    verify = ctx.findings.get("verify", {})
    promote = ctx.findings.get("promote", {})
    select = ctx.findings.get("select", {})

    # Structured summary — single line, easy to grep / monitor.
    summary = {
        "profile": PROFILE,
        "instance": INSTANCE,
        "disk": ctx.disk_path,
        "elapsed_s": int(elapsed),
        "completed_phases": ctx.completed_phases,
        "select_findings": select,
        "verify_passed": verify.get("passed", False),
        "verify_summary": verify.get("summary", ""),
        "required_failures": verify.get("required_failures", []),
        "gold_image": promote.get("gold_image", ""),
    }
    print("=== STRUCTURED_SUMMARY === " + json.dumps(summary), flush=True)

    if verify.get("passed") and promote.get("gold_image"):
        print(f"\nGOLD IMAGE: {promote['gold_image']}", flush=True)
        print("### DONE OK", flush=True)
        sys.exit(0)

    if verify.get("passed"):
        print("\nVERIFY PASSED but no gold-image promote happened "
              "(check profile.output_disk).", flush=True)
        print("### DONE OK (no promote)", flush=True)
        sys.exit(0)

    print("\nINCOMPLETE — verify FAILED. Required gaps:", flush=True)
    for f in verify.get("required_failures", []):
        print(f"  - {f['path']}  ({f.get('detail', 'missing')}) — {f['why']}",
              flush=True)
    print("### DONE WITH GAPS", flush=True)
    sys.exit(1)


if __name__ == "__main__":
    main()
