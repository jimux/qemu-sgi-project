"""Phase 5 (post-verify): addon — fill gaps surfaced by the verifier.

After install + verify, the manifest may report missing files. Many of
those map cleanly to specific IRIX subsystem packages we can install via
the legacy `install_addon` harness, which boots the system and runs
`inst` against the combined image as an addon source.

The mapping from missing files → packages to install is path-pattern
based; new mappings are easy to add as we encounter more gaps.

Inputs:
    ctx.findings['verify']  — must have run; needs required_failures[]
    ctx.disk_path           — installed disk
    profile.media.combined  — combined image to install addons from

Outputs:
    ctx.findings['addon'] = {
        rounds: int,
        installed_subsystems: [str],
        verify_after: {...},        # re-run verify after each round
    }
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


# Map a missing-file path (or regex hit on it) → the IRIX inst selector(s)
# to install. Order: most-specific first. The selectors are passed as
# {"resolutions": []} to install_addon via inst's "install <expr>" syntax.
PATH_TO_SELECTORS: list[tuple[str, list[str]]] = [
    # ── Desktop ──────────────────────────────────────────────────────
    ("/usr/sbin/4Dwm",                    ["4Dwm.sw.4Dwm"]),
    ("/usr/sbin/toolchest",               ["desktop_eoe.sw.toolchest"]),
    ("/usr/Cadmin/lib/cloginlib/",        ["sysadmdesktop.sw.base"]),
    ("/usr/local/lib/faces/",             ["desktop_eoe.sw.faces",
                                           "desktop_eoe.sw.cadmin_faces"]),
    ("/usr/lib/X11/iconlib",              ["desktop_eoe.sw.envm",
                                           "desktop_eoe.sw.iconlib"]),
    ("/usr/lib/desktop/FTRlib",           ["desktop_eoe.sw.FTRlib"]),
    ("/usr/lib/desktop/iconcatalog",      ["desktop_eoe.sw.icons"]),

    # ── Dev headers + crt ───────────────────────────────────────────
    ("/usr/include/Xm/Xm.h",              ["motif_dev.sw.motif"]),
    ("/usr/include/gl/gl.h",              ["gl_dev.sw.gl"]),
    ("/usr/include/GL/gl.h",              ["gl_dev.sw.gl"]),
    ("/usr/include/stdio.h",              ["dev.sw.headers", "c_dev.sw.c"]),
    ("/usr/lib32/mips3/crt1.o",           ["c_dev.sw.c", "dev.sw.base"]),

    # ── Networking ──────────────────────────────────────────────────
    ("/usr/etc/in.telnetd",               ["netman.sw.client"]),
    ("/usr/etc/in.ftpd",                  ["netman.sw.client"]),
    ("/usr/etc/tftp",                     ["nfs.sw.tftp",
                                           "netman.sw.client"]),
]


def _selectors_for_gap(path: str) -> list[str]:
    for prefix, sels in PATH_TO_SELECTORS:
        if path == prefix or path.startswith(prefix):
            return sels
    return []


def _aggregate_selectors(required_failures: list[dict]) -> list[str]:
    """Collect a deduplicated, ordered list of inst selectors that should
    fill the failures we know how to map."""
    seen: set[str] = set()
    out: list[str] = []
    unmapped: list[str] = []
    for f in required_failures:
        path = f.get("path", "")
        sels = _selectors_for_gap(path)
        if not sels:
            unmapped.append(path)
            continue
        for s in sels:
            if s not in seen:
                seen.add(s)
                out.append(s)
    if unmapped:
        log.warning("addon: %d gap(s) had no package mapping (need a rule "
                    "added to PATH_TO_SELECTORS):", len(unmapped))
        for p in unmapped:
            log.warning("    %s", p)
    return out


def run(ctx) -> object:
    verify = ctx.findings.get("verify", {})
    if verify.get("passed"):
        log.info("addon: verify passed; no gaps to fill")
        ctx.findings["addon"] = {"skipped": True}
        ctx.mark_done("addon")
        return ctx
    failures = verify.get("required_failures", []) or []
    if not failures:
        log.info("addon: no required_failures recorded — skipping")
        ctx.findings["addon"] = {"skipped": True}
        ctx.mark_done("addon")
        return ctx

    selectors = _aggregate_selectors(failures)
    if not selectors:
        log.warning("addon: %d failure(s) but none mapped to selectors — "
                    "see warnings above", len(failures))
        ctx.findings["addon"] = {"unmapped_failures": len(failures)}
        ctx.mark_done("addon")
        return ctx

    log.info("addon: %d gap(s) → %d selector(s) to install:",
             len(failures), len(selectors))
    for s in selectors:
        log.info("    %s", s)

    # Resolve the combined image path (legacy install_addon reads it
    # from cfg, so we pass it explicitly via the inst_packages route).
    profile = ctx.profile
    project_root = Path(__file__).resolve().parents[3]
    combined = (profile.media or {}).get("combined", {})
    if isinstance(combined, dict):
        img_rel = combined.get("image", "")
    else:
        img_rel = str(combined)
    addon_image = ""
    if img_rel:
        path = (project_root / "software_library" / img_rel
                if not Path(img_rel).is_absolute() else Path(img_rel))
        if path.exists():
            addon_image = str(path)
    if not addon_image:
        log.error("addon: no combined image to install from")
        ctx.findings["addon"] = {"error": "no combined image"}
        ctx.mark_done("addon")
        return ctx

    # Drive legacy install_addon. It boots the installed disk, mounts the
    # addon image, runs `from <path>` + selectors, `go`, then quits +
    # restarts. Returns when the addon install completes.
    from pyirix_qemu.install.irix import install_addon
    log.info("addon: calling install_addon (image=%s, %d selector(s))",
             addon_image, len(selectors))
    try:
        result = install_addon(
            base_disk=ctx.disk_path,
            addon_image=addon_image,
            output_disk=None,           # in-place
            addon_name="v2_gap_fill",
            install_selectors=selectors,    # ← v2-specific: target gaps
            extra_args=None,
        )
    except Exception as e:
        log.error("addon: install_addon raised %s: %s",
                  type(e).__name__, e)
        ctx.findings["addon"] = {"error": f"{type(e).__name__}: {e}"}
        # Don't mark done — orchestrator should decide if this is fatal
        raise

    log.info("addon: install_addon returned: %r", result)
    ctx.findings["addon"] = {
        "selectors": selectors,
        "addon_image": addon_image,
        "result": str(result),
    }
    ctx.mark_done("addon")

    # Re-run the verifier to see if the addon closed the gaps.
    from .verify import run as verify_run
    # Clear the prior verify finding so verify_run re-executes.
    ctx.completed_phases = [p for p in ctx.completed_phases if p != "verify"]
    ctx.findings.pop("verify", None)
    verify_run(ctx)
    return ctx
