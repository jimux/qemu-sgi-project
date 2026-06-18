#!/usr/bin/env python3
"""Run JUST the v2 verify+addon+promote phases against the existing
ip54-fresh disk. Used after the install phase has already produced a
standard-level baseline — skip the 13-min install and go straight to
gap-filling with the patched install_addon."""

import json, logging, sys, time, traceback
from pathlib import Path

sys.path.insert(0, "/home/jimmy/qemu-sgi")

from pyirix_qemu.install_v2.context import (
    InstallContext, load_profile, load_policy)
from pyirix_qemu.install_v2.phases import verify, addon
from pyirix_qemu.install_v2.orchestrator import _promote_to_gold


def banner(msg):
    t = time.strftime("%H:%M:%S")
    print(f"[{t}] {'='*64}\n[{t}] {msg}\n[{t}] {'='*64}", flush=True)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S", stream=sys.stdout, force=True,
    )

    banner("v2 addon-only run — fill gaps in existing ip54-fresh install")

    PROJECT_ROOT = Path("/home/jimmy/qemu-sgi")
    DISK = PROJECT_ROOT / "vm_instances/ip54-fresh/disk.qcow2"
    if not DISK.exists():
        print(f"FATAL: disk missing: {DISK}", flush=True)
        sys.exit(2)

    prof = load_profile("irix_6_5_5_dev")
    pol = load_policy("default")
    ctx = InstallContext(
        profile=prof, policy=pol,
        disk_path=str(DISK), instance="ip54-fresh",
        log_dir=str(PROJECT_ROOT / "install_logs/ip54-fresh"),
        completed_phases=["prepare", "install"],
    )
    Path(ctx.log_dir).mkdir(parents=True, exist_ok=True)

    try:
        # 1. Verify against current disk → populates required_failures
        banner("PHASE: verify (initial)")
        verify.run(ctx)
        if ctx.findings.get("verify", {}).get("passed"):
            print("Initial verify already passes — promoting", flush=True)
            _promote_to_gold(ctx)
            print("### DONE OK", flush=True)
            sys.exit(0)

        # 2. Addon — install missing packages
        banner("PHASE: addon")
        addon.run(ctx)

        # addon.run already re-runs verify internally; check final result.
        verify_after = ctx.findings.get("verify", {})
        print("=== STRUCTURED_SUMMARY === " + json.dumps({
            "disk": ctx.disk_path,
            "passed": verify_after.get("passed", False),
            "summary": verify_after.get("summary", ""),
            "required_failures": verify_after.get("required_failures", []),
            "addon_findings": ctx.findings.get("addon", {}),
        }), flush=True)

        if verify_after.get("passed"):
            banner("VERIFY PASSED — promoting to gold image")
            _promote_to_gold(ctx)
            gold = ctx.findings.get("promote", {}).get("gold_image", "")
            print(f"GOLD IMAGE: {gold}", flush=True)
            print("### DONE OK", flush=True)
            sys.exit(0)
        else:
            banner("STILL INCOMPLETE after addon")
            for f in verify_after.get("required_failures", []):
                print(f"  - {f['path']} — {f.get('detail', '?')}", flush=True)
            print("### DONE WITH GAPS", flush=True)
            sys.exit(1)
    except Exception as e:
        print(f"FATAL: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()
