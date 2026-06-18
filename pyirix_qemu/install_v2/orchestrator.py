"""Top-level driver for install_v2.

Loads a profile + policy, walks the phase list, snapshots progress, and
gates success on the completeness verifier.

CLI:

    python3 -m pyirix_qemu.install_v2.orchestrator \\
        --profile irix_6_5_5_dev \\
        --instance ip54-fresh \\
        [--resume <state.json>] \\
        [--skip-phase partition,miniroot]   # e.g. when iterating verify

    python3 -m pyirix_qemu.install_v2.orchestrator verify \\
        --disk vm_instances/ip54-test/disk.qcow2.golden \\
        --manifest pyirix_qemu/install_v2/completeness/manifest_6_5_5.yaml

Phases run in this order:

    prepare → partition → miniroot → select → conflicts → kernel → verify

The orchestrator persists ctx.to_dict() after each phase to
`<log_dir>/state.json` so partial runs can resume.
"""

from __future__ import annotations

import argparse
import logging
import sys
from importlib import import_module
from pathlib import Path

from .context import InstallContext, load_profile, load_policy

log = logging.getLogger(__name__)


PHASE_ORDER = [
    "prepare",   # disk + nvram (v2-native)
    "install",   # legacy install_irix end-to-end (lift-and-shift)
    "verify",    # v2 manifest check (v2-native; the value-add)
    "addon",     # fill gaps from verify (v2-native; targeted selectors)
    # promote happens in install() after PHASE_ORDER if verify passed
]


def _run_phase(name: str, ctx: InstallContext) -> InstallContext:
    log.info("=" * 60)
    log.info("PHASE: %s", name)
    log.info("=" * 60)
    mod = import_module(f"pyirix_qemu.install_v2.phases.{name}")
    return mod.run(ctx)


def install(profile_name: str, *, instance: str = "",
            disk_path: str = "", log_dir: str = "",
            skip_phases: list[str] | None = None,
            resume: str = "") -> InstallContext:
    skip = set(skip_phases or [])

    if resume:
        ctx = InstallContext.load(resume)
        # Re-load the profile/policy in case they were edited since.
        ctx.profile = load_profile(ctx.profile.name or profile_name)
        ctx.policy = load_policy(ctx.profile.conflict_policy or "default")
        log.info("orchestrator: resuming from %s, completed=%s",
                 resume, ctx.completed_phases)
    else:
        ctx = InstallContext(
            profile=load_profile(profile_name),
            policy=load_policy(
                load_profile(profile_name).conflict_policy or "default"),
            disk_path=disk_path,
            instance=instance,
            log_dir=log_dir or _default_log_dir(profile_name, instance),
        )
        Path(ctx.log_dir).mkdir(parents=True, exist_ok=True)

    state_file = Path(ctx.log_dir) / "state.json"

    try:
        for phase in PHASE_ORDER:
            if phase in ctx.completed_phases:
                log.info("orchestrator: skipping %s (already completed)", phase)
                continue
            if phase in skip:
                log.info("orchestrator: skipping %s (explicit --skip-phase)", phase)
                continue
            ctx = _run_phase(phase, ctx)
            ctx.save(state_file)
    finally:
        # Ensure the QEMUSession is cleaned up even on mid-run failure.
        # Without this, a stuck install would leak the QEMU process and
        # leave the disk image locked.
        sess = ctx.live.pop("session", None)
        if sess is not None and not getattr(sess, "_closed", True):
            log.info("orchestrator: cleaning up live QEMUSession")
            try:
                sess.__exit__(None, None, None)
            except Exception as e:
                log.warning("orchestrator: session cleanup raised: %s", e)
        ctx.save(state_file)

    # Post-success: promote the disk to a gold image if verify passed AND
    # the profile asked for promotion via `output_disk`.
    verify_result = ctx.findings.get("verify", {})
    if (verify_result.get("passed") and ctx.profile.output_disk and
            ctx.disk_path and ctx.disk_path != ctx.profile.output_disk):
        _promote_to_gold(ctx)

    return ctx


def _promote_to_gold(ctx: InstallContext) -> None:
    """Copy the verified-complete install disk to the profile's
    output_disk path. Caller-set .prev backup if a previous gold exists."""
    src = Path(ctx.disk_path)
    dst = Path(ctx.profile.output_disk)
    if not dst.is_absolute():
        project_root = Path(__file__).resolve().parents[2]
        dst = project_root / dst

    if not src.exists():
        log.warning("promote: source disk missing: %s", src)
        return

    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        backup = dst.with_suffix(dst.suffix + ".prev")
        log.info("promote: backing up existing gold to %s", backup)
        import shutil
        shutil.move(str(dst), str(backup))

    import shutil
    log.info("promote: copying verified disk %s → %s", src, dst)
    shutil.copy2(str(src), str(dst))
    ctx.findings["promote"] = {"gold_image": str(dst)}


def _default_log_dir(profile_name: str, instance: str) -> str:
    project_root = Path(__file__).resolve().parents[2]
    base = project_root / "install_logs"
    tag = instance or profile_name
    # Deterministic filename — caller can pick the dir if they want
    # timestamping. Avoid Date.now/now() to keep this resume-friendly.
    return str(base / tag)


# ── CLI ──────────────────────────────────────────────────────────────────────


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="IRIX install orchestrator (v2)")
    sub = ap.add_subparsers(dest="cmd")

    p_inst = sub.add_parser("install", help="run the full install pipeline")
    p_inst.add_argument("--profile", required=True,
                        help="profile name or path (e.g. irix_6_5_5_dev)")
    p_inst.add_argument("--instance", default="",
                        help="VM instance name (vm_instances/<name>/)")
    p_inst.add_argument("--disk", default="",
                        help="override disk path")
    p_inst.add_argument("--log-dir", default="",
                        help="override log/state directory")
    p_inst.add_argument("--skip-phase", action="append", default=[],
                        help="phase name to skip (repeatable)")
    p_inst.add_argument("--resume", default="",
                        help="resume from state.json path")
    p_inst.add_argument("--verbose", "-v", action="store_true")

    p_verify = sub.add_parser("verify",
                              help="just run the completeness check")
    p_verify.add_argument("--disk", required=True)
    p_verify.add_argument("--manifest", required=True)
    p_verify.add_argument("--json", action="store_true")
    p_verify.add_argument("--verbose", "-v", action="store_true")

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2

    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.cmd == "install":
        ctx = install(args.profile,
                      instance=args.instance,
                      disk_path=args.disk,
                      log_dir=args.log_dir,
                      skip_phases=args.skip_phase,
                      resume=args.resume)
        verify_result = ctx.findings.get("verify", {})
        if verify_result.get("passed"):
            log.info("INSTALL COMPLETE: %s", verify_result.get("summary"))
            return 0
        log.error("INSTALL INCOMPLETE: %s", verify_result.get("summary"))
        for f in verify_result.get("required_failures", []):
            log.error("  MISSING %s — %s [%s]",
                      f["path"], f["why"], f.get("detail", ""))
        return 1

    if args.cmd == "verify":
        from .completeness.check import _main as verify_main
        cli = ["--backend", "host",
               "--disk", args.disk,
               "--manifest", args.manifest]
        if args.json:
            cli.append("--json")
        return verify_main(cli)

    return 2


if __name__ == "__main__":
    sys.exit(_main())
