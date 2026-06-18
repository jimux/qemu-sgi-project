"""Phase 7: verify — run the completeness manifest against the
finished install. Fail loudly if anything required is missing.

Two modes:
    - host (default): use HostBackend against the disk image. Fast, no
      booting required. Catches file-presence gaps. Cannot check
      processes (must_be_running entries are skipped).
    - guest: cold-boot the disk image, run a telnet check against the
      live guest. Catches process gaps too. Slow.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..completeness.check import HostBackend, verify as run_check, Report

log = logging.getLogger(__name__)


def run(ctx, *, mode: str = "host") -> object:
    profile = ctx.profile
    if not profile.manifest:
        log.warning("verify: no manifest in profile — skipping")
        ctx.findings["verify"] = {"skipped": True}
        ctx.mark_done("verify")
        return ctx

    # Resolve manifest path (relative paths are relative to install_v2/).
    install_v2_root = Path(__file__).resolve().parents[1]
    manifest_path = Path(profile.manifest)
    if not manifest_path.is_absolute():
        manifest_path = install_v2_root / profile.manifest
    if not manifest_path.exists():
        raise FileNotFoundError(f"verify: manifest not found: {manifest_path}")

    log.info("verify: %s mode, manifest=%s, disk=%s",
             mode, manifest_path, ctx.disk_path)

    if mode == "host":
        backend = HostBackend(ctx.disk_path)
        report: Report = run_check(backend, str(manifest_path))
    else:
        raise NotImplementedError(
            "verify(mode='guest') requires a booted-from-disk QEMUSession + "
            "telnet — wire after kernel phase is ported.")

    log.info("verify: %s", report.summary())
    ctx.findings["verify"] = {
        "mode": mode,
        "manifest": str(manifest_path),
        "passed": report.passed,
        "summary": report.summary(),
        "required_failures": [
            {"path": r.path, "why": r.why, "detail": r.detail}
            for r in report.required_failures
        ],
    }
    if not report.passed:
        # Still mark phase done — the orchestrator decides whether
        # an incomplete install is fatal (it should be).
        log.warning("verify: %d required check(s) FAILED",
                    len(report.required_failures))
    ctx.mark_done("verify")
    return ctx
