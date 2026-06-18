"""Phase 3: miniroot — boot the IRIX miniroot, create the root filesystem,
end at the `Inst>` prompt.

Reuses the QEMUSession opened by phases.partition. The expect chain is
identical to the legacy harness — miniroot bootstrapping is stable; the
breakage we care about lives downstream in package selection.

Inputs:
    ctx.live['session']      — open QEMUSession at PROM menu
    ctx.live['version_cfg']  — legacy-shaped cfg dict
    ctx.profile.fs_type      — "xfs" (we pin this for 6.5.5)

Outputs:
    ctx.live['inst']         — InstSession (state=AT_INST) wrapping the session
    ctx.findings['miniroot'] = {fs_type}
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def run(ctx) -> object:
    session = ctx.live.get("session")
    if session is None:
        raise RuntimeError("miniroot: no QEMUSession — partition must run first")

    version_cfg = ctx.live.get("version_cfg") or {}
    # Make sure the cfg has the flags _create_filesystems checks.
    version_cfg.setdefault("has_startup_script", True)   # 6.5.5 has one
    version_cfg.setdefault("has_efs_xfs_choice", False)
    version_cfg.setdefault("has_usr_partition", False)
    version_cfg.setdefault("fs_type", ctx.profile.fs_type or "xfs")

    log.info("miniroot: booting miniroot + creating %s root filesystem",
             version_cfg["fs_type"])

    from pyirix_qemu.install.irix import phase_miniroot
    phase_miniroot(session, version_cfg)
    log.info("miniroot: arrived at Inst> prompt")

    # Wrap the session in InstSession (state = AT_INST). Downstream
    # select/conflicts phases drive it via this object.
    from ..inst_session import InstSession
    inst = InstSession(session)
    ctx.live["inst"] = inst

    ctx.findings["miniroot"] = {
        "fs_type": version_cfg["fs_type"],
    }
    ctx.mark_done("miniroot")
    return ctx
