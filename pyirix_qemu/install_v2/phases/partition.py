"""Phase 2: partition — boot sash from boot CD, run fx, fresh partition.

Opens the long-lived QEMUSession that miniroot + select + conflicts will
share. The expect chain is identical to the legacy harness's
`phase_partition()` — that protocol is stable and not where our breakage
lives — so we lift it via a thin adapter rather than re-implement.

Inputs (from ctx):
    profile.machine, .ram_mb
    profile.media (combined image and/or cdroms list)
    disk_path                  (fresh qcow2 from prepare)

Side effects:
    ctx.live['session']   — QEMUSession at PROM menu after partition
    ctx.live['version_cfg'] — legacy-shaped cfg dict (for downstream phases)
    ctx.findings['partition'] = {scheme}
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _resolve_cdrom_paths(profile, project_root: Path) -> tuple[list[str], str]:
    """Map profile.media into (cdrom_paths, combined_image).

    Returns paths that can be attached to QEMUSession.scsi_drives via the
    `:cdrom` / `:ro` suffix convention. Combined image is a raw EFS file,
    NOT a CD-ROM, but we still attach it as a SCSI disk on target 2.
    """
    media = profile.media or {}
    library = project_root / "software_library"

    cdroms = []
    for cd in media.get("cdroms", []) or []:
        img_rel = cd.get("image") if isinstance(cd, dict) else cd
        if not img_rel:
            continue
        path = library / img_rel if not Path(img_rel).is_absolute() else Path(img_rel)
        if not path.exists():
            log.warning("partition: missing CD image %s — skipping", path)
            continue
        cdroms.append(str(path))

    combined_image = ""
    combined = media.get("combined") if isinstance(media, dict) else None
    if combined:
        img_rel = combined.get("image") if isinstance(combined, dict) else combined
        if img_rel:
            path = library / img_rel if not Path(img_rel).is_absolute() else Path(img_rel)
            if path.exists():
                combined_image = str(path)
            else:
                log.warning("partition: combined image not found at %s", path)

    return cdroms, combined_image


def _build_scsi_drives(disk_path: str, cdroms: list[str],
                       combined_image: str) -> list[str]:
    """Compose the scsi_drives list QEMUSession expects.

    Convention (from boot_harness):
        plain path           → SCSI target N as disk
        path + ":cdrom"      → SCSI target as CD-ROM
        path + ":ro"         → SCSI target as read-only disk

    The install harness attaches:
        target 1: the fresh disk
        target 2: combined dist image (raw EFS, read-only data disk)
        target 4: boot CD (sashARCS + miniroot)
        target 5: secondary CD (Foundation 1)

    QEMUSession auto-assigns SCSI targets:
        plain disks   → 1, 2, 3, ... (next_disk_id)
        ":cdrom"      → 4, 5, 6, ... (next_cdrom_id)
        ":ro"         → readonly disk (still counts as disk for targeting)
    So we hand it drives in ORDER and don't include placeholders — every
    entry must point to a real file.

    Final layout: target 1 = fresh disk, 2 = combined image (:ro),
    4 = boot CD, 5 = foundation 1.
    """
    drives = [disk_path]
    if combined_image:
        drives.append(f"{combined_image}:ro")
    # Up to 2 CD-ROMs — PROM probes 3+ unreliably on indy, per legacy.
    if cdroms:
        drives.append(f"{cdroms[0]}:cdrom")
    if len(cdroms) > 1:
        drives.append(f"{cdroms[1]}:cdrom")
    return drives


def _profile_to_legacy_cfg(profile) -> dict:
    """Build a legacy-shaped version_cfg dict from the v2 profile.

    The legacy phase_partition/_miniroot funcs read just a couple of
    fields from cfg — mostly `machine` and partitioning details. We give
    them a minimal compatible dict; v2 owns everything else.
    """
    return {
        "machine": profile.machine,
        "fs_type": profile.fs_type,
        "has_usr_partition": False,
        "has_efs_xfs_choice": False,
        "needs_rulesoverride": False,
    }


def run(ctx) -> object:
    project_root = Path(__file__).resolve().parents[3]
    profile = ctx.profile

    if not ctx.disk_path:
        raise RuntimeError("partition: ctx.disk_path is empty — run prepare first")

    cdroms, combined = _resolve_cdrom_paths(profile, project_root)
    if not cdroms:
        raise RuntimeError(
            "partition: profile has no usable cdroms — at least the boot CD "
            "(sashARCS+miniroot) must resolve")
    log.info("partition: %d CD(s), combined=%s",
             len(cdroms), bool(combined))

    scsi_drives = _build_scsi_drives(ctx.disk_path, cdroms, combined)
    for i, d in enumerate(scsi_drives):
        log.info("  target %d: %s", i + 1, d or "(unused)")

    # Open the QEMU session — this is the one that lives through inst.
    from pyirix_qemu.boot_harness import QEMUSession
    session_kwargs = dict(
        machine=profile.machine,
        ram_mb=profile.ram_mb,
        scsi_drives=scsi_drives,
        repeat_threshold=0,
    )
    log.info("partition: opening QEMUSession (machine=%s, ram=%dM)",
             profile.machine, profile.ram_mb)
    session = QEMUSession(**session_kwargs).__enter__()
    ctx.live["session"] = session
    ctx.live["version_cfg"] = _profile_to_legacy_cfg(profile)
    ctx.live["cdroms"] = cdroms
    ctx.live["combined_image"] = combined

    # Drive partition via the legacy expect chain. Stable protocol; we lift.
    from pyirix_qemu.install.irix import phase_partition
    phase_partition(session, ctx.live["version_cfg"])

    ctx.findings["partition"] = {
        "scheme": (profile.partitioning or {}).get("scheme", "single_root"),
        "cdroms": cdroms,
        "combined_image": combined,
    }
    ctx.mark_done("partition")
    return ctx
