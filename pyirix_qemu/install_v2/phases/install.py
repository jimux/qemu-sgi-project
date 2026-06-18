"""Phase 1+2+3: install — drive the proven legacy install_irix() through
to a booted disk.

This replaces the v2-native partition/miniroot/select/kernel phase
chain. Multi-pass inst-driving + conflict-cycle handling + the
post-quit restart prompt are intricate and battle-hardened in the legacy
harness; re-implementing them in v2 introduces risk far out of
proportion to the benefit. v2 instead OWNS the layers where novelty
matters: the declarative profile (mapped to legacy kwargs here), the
honest completeness verifier (next phase), and the gold-image
promotion (orchestrator).

The legacy harness is called with `install_level="default"` (everything
available) plus the profile's instance/RAM/disk-size knobs. The known
"eoe.sw.base cycle" bug in legacy is partially mitigated by a focused
patch in inst_safety.py applied at import time.

Inputs:
    ctx.profile   — version, machine, ram_mb, disk_size_mb, install_level
    ctx.instance  — VM instance name

Outputs:
    ctx.disk_path                  — points at the produced disk
    ctx.findings['install'] = {legacy_result, skipped_packages}
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def run(ctx) -> object:
    profile = ctx.profile

    # Map profile → legacy install_irix kwargs. Pass disk_path
    # EXPLICITLY so install_irix uses the prepare phase's freshly
    # created disk (rather than its cfg["default_disk"] default).
    #
    # install_level: STANDARD, not default. `default` (= everything
    # available) triggers a conflict cascade on `eoe.sw.base` that the
    # legacy harness can't escape (96+ post-quit cycles, never reaches
    # the restart prompt, no kernel built). `standard` is the curated
    # SGI preset — produces a bootable baseline that the v2 addon phase
    # then completes with targeted selectors for any gaps the verifier
    # surfaces.
    kwargs = dict(
        version=profile.version,
        disk_path=ctx.disk_path,
        instance=ctx.instance,
        ram_mb=profile.ram_mb,
        disk_size_mb=profile.disk_size_mb,
        conflict_mode="auto",
        install_level="standard",
        inst_debug=False,
    )

    log.info("install: calling legacy install_irix(%s)",
             ", ".join(f"{k}={v!r}" for k, v in kwargs.items()))

    # Apply the inst-safety patches BEFORE importing the installer —
    # they modify _select_conflict_option to refuse core-package drops.
    from .. import inst_safety
    inst_safety.apply()

    import pyirix_qemu.install.irix as installer
    # Pipe installer logs through our logger so the unified v2 log
    # captures the install transcript at INFO level.
    orig_log = installer.log

    def _capture(msg: str) -> None:
        log.info("legacy: %s", msg)
        try:
            orig_log(msg)
        except Exception:
            pass

    installer.log = _capture
    try:
        result = installer.install_irix(**kwargs)
    finally:
        installer.log = orig_log

    # The legacy harness writes to its default_disk path; resolve and
    # record so verify/promote can find it.
    project_root = Path(__file__).resolve().parents[3]
    if ctx.instance:
        ctx.disk_path = str(project_root / "vm_instances" / ctx.instance
                            / "disk.qcow2")
    else:
        from pyirix_qemu.install.irix import VERSIONS
        ctx.disk_path = VERSIONS[profile.version]["default_disk"]

    ctx.findings["install"] = {
        "legacy_kwargs": kwargs,
        "legacy_result": result if isinstance(result, (str, int, list, dict, bool)) else str(result),
        "disk_path": ctx.disk_path,
    }
    ctx.mark_done("install")
    return ctx
