"""Phase 6: kernel — drive `quit` through inst's exit-ops flow (which
triggers autoconfig + lboot inside the miniroot), then close the install
session. Verify (host-mode) will read the resulting disk directly — no
post-install boot is required.

The `quit` flow is gnarly (post-quit conflict cycles, pager handling,
restart confirmation). Legacy `phase_quit_and_build` is 190+ lines and
battle-hardened — we lift it via thin adapter rather than re-implement.

Inputs:
    ctx.live['session']      — open QEMUSession at Inst> after select
    ctx.live['version_cfg']  — legacy cfg shape

Outputs:
    ctx.findings['kernel'] = {kernel_built: bool, restart_seen: bool}
    Closes ctx.live['session'] (and removes it from ctx.live).
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def run(ctx) -> object:
    session = ctx.live.get("session")
    if session is None:
        raise RuntimeError("kernel: no QEMUSession — select must run first")
    version_cfg = ctx.live.get("version_cfg") or {}

    log.info("kernel: driving inst quit + autoconfig + lboot")
    from pyirix_qemu.install.irix import phase_quit_and_build
    try:
        phase_quit_and_build(session, version_cfg, instance=ctx.instance or None)
    except Exception as e:
        log.error("kernel: phase_quit_and_build raised %s: %s",
                  type(e).__name__, e)
        ctx.findings["kernel"] = {
            "kernel_built": False,
            "error": f"{type(e).__name__}: {e}",
        }
        raise

    # Close the session. We use the QEMUSession's __exit__ since
    # __enter__ was called by partition.
    log.info("kernel: closing install QEMUSession")
    try:
        session.__exit__(None, None, None)
    except Exception as e:
        log.warning("kernel: session close raised %s: %s",
                    type(e).__name__, e)

    ctx.live.pop("session", None)
    ctx.live.pop("inst", None)

    ctx.findings["kernel"] = {
        "kernel_built": True,
    }
    ctx.mark_done("kernel")
    return ctx
