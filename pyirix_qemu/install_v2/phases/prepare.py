"""Phase 1: prepare — make a clean VM instance and fresh disk image.

Inputs (from ctx):
    profile.machine, profile.ram_mb, profile.disk_size_mb, profile.fs_type
    profile.output_disk                 (or ctx.disk_path override)
    instance                            (optional, e.g. "ip54-test")

Side effects:
    - Creates/clears vm_instances/<instance>/ if instance was given
    - Creates a fresh qcow2 disk at ctx.disk_path of profile.disk_size_mb
    - Removes stale NVRAM for the target machine
    - Writes ctx.findings['prepare'] = {disk_path, disk_size_mb, nvram_cleared: [paths]}
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


# Mirror of pyirix_qemu.install.irix.NVRAM_FILES — kept local to avoid the
# legacy import. Add machines as we support them.
NVRAM_FILES = {
    "indy": "sgi_indy_nvram.bin",
    "indigo2": "sgi_indigo2_nvram.bin",
    "indigo2-r10k": "sgi_indigo2_r10k_nvram.bin",
    "indigo2-r8k": "sgi_indigo2_r8k_nvram.bin",
    "indigo": "sgi_indigo_nvram.bin",
}


def run(ctx) -> object:
    profile = ctx.profile
    project_root = Path(__file__).resolve().parents[3]

    # Resolve target disk path.
    disk_path = ctx.disk_path or profile.output_disk
    if not disk_path:
        raise ValueError("prepare: no disk path; set ctx.disk_path or "
                         "profile.output_disk")
    disk_path = str(project_root / disk_path) if not os.path.isabs(disk_path) else disk_path
    ctx.disk_path = disk_path

    # Per-instance directory.
    if ctx.instance:
        inst_dir = project_root / "vm_instances" / ctx.instance
        inst_dir.mkdir(parents=True, exist_ok=True)
        # If the orchestrator gave a relative output disk, normalize it
        # into the instance dir so installs don't trample each other.
        if not os.path.isabs(profile.output_disk):
            disk_path = str(inst_dir / "disk.qcow2")
            ctx.disk_path = disk_path

    # Remove the existing disk image so the install starts from scratch —
    # idempotent reset.
    if os.path.exists(disk_path):
        log.info("prepare: removing existing disk %s", disk_path)
        os.remove(disk_path)

    # Create the qcow2.
    import subprocess
    log.info("prepare: creating fresh qcow2 disk %s (%d MB)",
             disk_path, profile.disk_size_mb)
    Path(disk_path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["qemu-img", "create", "-f", "qcow2",
         disk_path, f"{profile.disk_size_mb}M"],
        check=True, capture_output=True,
    )

    # Remove stale NVRAM (legacy harness lesson: leftover osopts=INST
    # causes sash to auto-chain into the miniroot).
    nvram_name = NVRAM_FILES.get(profile.machine,
                                 f"sgi_{profile.machine}_nvram.bin")
    nvram_candidates = [
        project_root / nvram_name,
        project_root / "qemu" / "build" / nvram_name,
        project_root / "qemu" / "build-linux" / nvram_name,
        project_root / "qemu" / "build-mac" / nvram_name,
    ]
    if ctx.instance:
        nvram_candidates.append(
            project_root / "vm_instances" / ctx.instance / "nvram.bin")
    cleared: list[str] = []
    for p in nvram_candidates:
        if p.exists():
            p.unlink()
            cleared.append(str(p))
            log.info("prepare: removed stale NVRAM %s", p)

    ctx.findings["prepare"] = {
        "disk_path": disk_path,
        "disk_size_mb": profile.disk_size_mb,
        "nvram_cleared": cleared,
    }
    ctx.mark_done("prepare")
    return ctx
