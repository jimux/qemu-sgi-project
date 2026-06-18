"""Fully automated IRIX installation for QEMU SGI emulation.

Handles disk creation, partitioning with fx, filesystem creation,
package installation, kernel build, and reboot verification.

Disc image paths are resolved dynamically via image_catalog.py, which
scans software_library/ for .img/.iso files. Hardcoded paths in VERSIONS
are kept as fallbacks when the catalog doesn't find a match.

Usage:
    python3 -m tools.install_irix 5.3
    python3 -m tools.install_irix 6.2
    python3 -m tools.install_irix 6.5
    python3 -m tools.install_irix 6.5.5
    python3 -m tools.install_irix 5.3 --disk custom.qcow2
    python3 -m tools.install_irix 6.2 --verify-only

Supported versions: 5.3, 6.2, 6.5, 6.5.5
"""

import argparse
import json
import os
import re
import sys
import time

from pathlib import Path

from pyirix_qemu.boot_harness import QEMUSession, PROJECT_ROOT
from pyirix_qemu.disk_manager import create_disk
from pyirix_qemu.catalog.images import (
    scan_software_library, resolve_images, ImageCatalog,
    CATEGORY_OS_BASE, CATEGORY_OS_OVERLAY, CATEGORY_DEV_COMPILER,
    CATEGORY_DEV_TOOLS, CATEGORY_APPLICATIONS, CATEGORY_DEMOS,
    CATEGORY_NETWORKING,
)

# NVRAM filenames per machine type (must match sgi_indy.c)
NVRAM_FILES = {
    "indy": "sgi_indy_nvram.bin",
    "indigo2": "sgi_indigo2_nvram.bin",
    "indigo2-r10k": "sgi_indigo2_r10k_nvram.bin",
    "indigo2-r8k": "sgi_indigo2_r8k_nvram.bin",
    "indigo": "sgi_indigo_nvram.bin",
}

# ── Version-specific configuration ──────────────────────────────────────────

# ── Version-specific configuration ──────────────────────────────────────────
#
# Non-path configuration per IRIX version. Disc image paths are resolved
# dynamically by _resolve_version_images() using image_catalog.py.
# Hardcoded fallback paths ("cdroms", "extra_cds", "combined_image") are
# kept for backward compatibility when image discovery doesn't find a match.

VERSIONS = {
    "5.3": {
        "machine": "indigo2",
        "cdroms": [
            str(PROJECT_ROOT / "software_library") + "/"
            "IRIX 5.3 All Indigo2 IMPACT - 812-0119-010.efs.img",
        ],
        "default_disk": str(PROJECT_ROOT / "irix53_disk.qcow2"),
        "fs_type": "efs",
        "needs_rulesoverride": True,
        "has_usr_partition": True,    # Separate /usr on partition 6
        "has_startup_script": False,
        "has_efs_xfs_choice": False,
        "snapshot_booted": "irix53_booted",
        "uname_pattern": r"IRIX.*5\.3",
    },
    "6.2": {
        "machine": "indigo2",
        "cdroms": [
            str(PROJECT_ROOT / "software_library") + "/irix_6.2_images/"
            "IRIX 6.2 (Part 1 of 2) - 812-0469-001.efs.img",
        ],
        "default_disk": str(PROJECT_ROOT / "irix62_disk.qcow2"),
        "fs_type": "efs",
        "needs_rulesoverride": False,
        "has_usr_partition": False,   # Single root partition
        "has_startup_script": True,
        "has_efs_xfs_choice": True,
        "snapshot_booted": "irix62_booted",
        "uname_pattern": r"IRIX.*6\.2",
    },
    "6.5.5": {
        "machine": "indy",
        "cdroms": [
            # Boot CD on SCSI target 4 (sashARCS + miniroot)
            str(PROJECT_ROOT / "software_library") + "/irix_6.5.5_images/"
            "IRIX 6.5.5 Installation Tools and Overlays (1 of 2) - 812-0818-005.efs.img",
            # Foundation 1 on SCSI target 5
            str(PROJECT_ROOT / "software_library") + "/"
            "IRIX 6.5 Foundation 1.img",
        ],
        # Combined distribution image — all CDs' dist/ in one EFS image.
        # Foundation + Overlays for base OS; dev tools come via addon pass.
        "combined_image": str(PROJECT_ROOT / "software_library"
                              / "prepackaged_combo_discs"
                              / "IRIX_6.5.5_full_with_MIPSpro_and_demos_patched.img"),
        "critical_packages": [
            "eoe.sw.base",
            "desktop_eoe.sw.toolchest",
            "desktop_eoe.sw.envm",
            "x_eoe.sw.Server",
            "sysadmdesktop.sw.base",   # provides /usr/Cadmin/bin/clogin
            "4Dwm.sw.4Dwm",            # window manager
        ],
        # essential_packages: explicitly installed by name in Pass 1
        # (Foundation-only pass) before the general `install *`, to
        # guarantee they are selected and committed even if `install *`
        # alone would fail due to overlay-vs-foundation prereq cascades.
        "essential_packages": [
            "motif_eoe",  # Motif runtime — Foundation pass must commit this
                          # before overlay packages can satisfy their prereq
        ],
        "default_disk": str(PROJECT_ROOT / "prebuilt_disks" / "irix-6.5.5-base.qcow2"),
        "fs_type": "xfs",
        "needs_rulesoverride": False,
        "has_usr_partition": False,
        "has_startup_script": True,
        "has_efs_xfs_choice": False,
        "needs_second_cd": True,
        "snapshot_booted": "irix655_booted",
        "uname_pattern": r"IRIX.*6\.5",
    },
    "6.5": {
        "machine": "indy",
        "cdroms": [
            # Boot CD — always SCSI target 4 (sash/miniroot boot).
            # Use Overlays 1 (which doubles as "Install Tools and Overlays")
            # instead of the old June 1998 InstTools.  The Overlays 1 CD has
            # sashARCS + miniroot with a newer inst (6.5.22) that resolves
            # overlay-vs-foundation incompatibilities correctly.  The old inst
            # cannot upgrade eoe.sw.base when il_eoe packages from Foundation 2
            # are already installed, cascading to 40+ skipped packages.
            str(PROJECT_ROOT / "software_library") + "/irix_6.5.22_images/"
            "IRIX 6.5.22 Overlays 1 of 3.img",
            # First data CD — SCSI target 5 (stays in drive for initial boot)
            str(PROJECT_ROOT / "software_library") + "/irix_6.5.22_images/"
            "IRIX 6.5 Foundation 1.img",
        ],
        # Additional CDs swapped into SCSI target 5 via QEMU monitor.
        # Only 2 CD-ROMs can be attached simultaneously (3+ causes
        # PROM hang during SCSI probe).
        # Order: F2, O2, O3, Apps, InstTools.
        # _build_cd_sequence() reorders: F1, F2, Boot(O1), O2, O3, Apps, IT.
        # Overlays 1 (boot CD on target 4) is installed after F2 to provide
        # eoe.sw.base overlay version before remaining CDs need it.
        "extra_cds": [
            str(PROJECT_ROOT / "software_library") + "/irix_6.5.22_images/"
            "IRIX 6.5 Foundation 2.img",
            str(PROJECT_ROOT / "software_library") + "/irix_6.5.22_images/"
            "IRIX 6.5.22 Overlays 2 of 3.img",
            str(PROJECT_ROOT / "software_library") + "/irix_6.5.22_images/"
            "IRIX 6.5.22 Overlays 3 of 3.img",
            str(PROJECT_ROOT / "software_library") + "/irix_6.5.22_images/"
            "SGI IRIX 6.5 Applications 2004 April.img",
            str(PROJECT_ROOT / "software_library") + "/irix_6.5.22_images/"
            "IRIX 6.5 Installation Tools June 1998.img",
        ],
        # Combined distribution image — all 6 CDs' dist/ in one EFS image.
        # Eliminates CD swapping: inst sees ALL packages simultaneously,
        # resolving cross-CD dependency issues (47+ skipped packages).
        # Built by extracting dist/ from each CD's EFS image and combining.
        "combined_image": str(PROJECT_ROOT / "software_library"
                              / "prepackaged_combo_discs"
                              / "IRIX_6.5.22_combined_dist.img"),
        # Packages required for a functional desktop. Missing ones are
        # logged as warnings during Phase 5 verification.
        "critical_packages": [
            "desktop_eoe.sw.toolchest",
            "desktop_eoe.sw.envm",
            "desktop_eoe.sw.Desks",
            "desktop_eoe.sw.control_panels",
            "desktop_base.sw.dso",
            "desktop_base.sw.utilities",
            "eoe.sw.base",
            "compiler_eoe.sw.unix",
            "compiler_eoe.sw.lib",
        ],
        "default_disk": str(PROJECT_ROOT / "irix65_disk.qcow2"),
        "fs_type": "xfs",
        "needs_rulesoverride": False,
        "has_usr_partition": False,
        "has_startup_script": True,
        "has_efs_xfs_choice": False,
        "needs_second_cd": True,
        "snapshot_booted": "irix65_booted",
        "uname_pattern": r"IRIX.*6\.5",
    },
}


def _resolve_version_images(version, cfg, categories=None):
    """Resolve disc image paths dynamically from software_library.

    Uses image_catalog to discover available images. If discovery finds
    a combo image or boot CD, updates cfg in-place with the discovered
    paths. Falls back to hardcoded paths in cfg if discovery fails.

    Args:
        version: IRIX version string
        cfg: Mutable copy of VERSIONS[version]
        categories: Additional image categories to include

    Returns:
        The (possibly updated) cfg dict
    """
    try:
        resolved = resolve_images(version, categories=categories)
    except Exception:
        # Discovery failed — use hardcoded paths
        return cfg

    # Update combined_image if discovery found one
    if resolved.combo_image:
        existing = cfg.get("combined_image", "")
        if not existing or not os.path.exists(existing):
            cfg["combined_image"] = resolved.combo_image.path
            log(f"  Discovered combo image: {resolved.combo_image.display_name}")

    # Update boot CD if discovery found one and hardcoded is missing
    if resolved.boot_cd and cfg.get("cdroms"):
        if not os.path.exists(cfg["cdroms"][0]):
            cfg["cdroms"][0] = resolved.boot_cd.path
            log(f"  Discovered boot CD: {resolved.boot_cd.display_name}")

    # Update foundation CDs if hardcoded ones are missing
    if resolved.foundation_cds and len(cfg.get("cdroms", [])) > 1:
        if not os.path.exists(cfg["cdroms"][1]):
            for fc in resolved.foundation_cds:
                if "foundation 1" in fc.display_name.lower():
                    cfg["cdroms"][1] = fc.path
                    log(f"  Discovered Foundation 1: {fc.display_name}")
                    break

    return cfg


def log(msg):
    print(f"[install] {msg}", flush=True)


def fail(msg):
    print(f"[FAIL] {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def expect(session, pattern, timeout=5, max_wait=120, label=""):
    """Wait for a pattern, abort on failure."""
    result = session.wait_for(pattern, timeout=timeout, max_wait=max_wait)
    if not result.matched:
        reason = result.bail_reason or "timeout"
        fail(f"Expected {label or pattern!r} but got: {reason}\n"
             f"Last output:\n{result.output[-500:]}")
    return result


# ── Standalone fixes ────────────────────────────────────────────────────────

def apply_xdm_fixes(q):
    """Apply xdm fixes to a running IRIX system at a root shell.

    Can be called standalone against any QEMUSession that has a root
    shell prompt, or from phase_verify_boot during installation.

    Applies two fixes:
    1. Write complete xdm-config unconditionally with three required lines:
       - loginProgram: routes xdm to Xlogin, which invokes clogin for
         the Indigo Magic visual login user-picker.
       - grabServer: False — prevents xdm from blocking on XGrabServer()
         when keyboard/mouse hardware isn't fully emulated.
       - authorize: false — allows clogin to connect to Xsgi at boot.
         Without this, XAUTHORITY path mismatch causes "client rejected"
         because Xlogin expects /usr/lib/X11/xdm/xdm-auth-$dpy but xdm
         writes to /var/X11/xdm/authdir/.
       xdm-config is delivered empty by x_eoe.sw; the sysadmdesktop
       exitop (configClogin/EZsetup) that would populate it never runs
       in the miniroot environment. The old sed approach silently did
       nothing on the empty file (sed exits 0 on empty input).
    2. Xsetup_0 with 'xset r off' — disables X autorepeat which fires
       rapidly under -icount shift=0,sleep=off (virtual time races
       through WAIT idle). VNC clients handle their own repeat.
    3. chkconfig visuallogin on + windowsystem on — ensures xdm routes
       to clogin (Indigo Magic user-picker). visuallogin=on is the
       chkconfig flag that Xlogin checks; without it, Xlogin falls back
       to the standard xdm dialog even with loginProgram set correctly.

    Returns True if both fixes applied successfully.
    """
    ok = True

    # Fix 1: Write complete xdm-config unconditionally.
    # xdm-config is delivered empty by x_eoe.sw; the sysadmdesktop exitop
    # that would populate it (configClogin via EZsetup first-boot wizard)
    # never runs in the miniroot environment.  sed on an empty file produces
    # empty output with exit code 0 — the old approach silently did nothing.
    # Write the three required lines directly with sequential echo commands
    # (no heredoc — heredocs caused serial hangs in testing):
    #   loginProgram  → routes xdm to Xlogin, which invokes clogin
    #   grabServer    → prevents xdm blocking on XGrabServer() in emulator
    #   authorize     → allows clogin to connect to Xsgi (XAUTHORITY path
    #                   mismatch causes "client rejected" without this)
    xdm_cfg = "/var/X11/xdm/xdm-config"
    q.send(f"echo 'DisplayManager._0.loginProgram:    /var/X11/xdm/Xlogin' > {xdm_cfg}\r")
    q.wait_for(r"#", timeout=5, max_wait=10)
    q.send(f"echo 'DisplayManager.grabServer:      False' >> {xdm_cfg}\r")
    q.wait_for(r"#", timeout=5, max_wait=10)
    q.send(f"echo 'DisplayManager._0.authorize: false' >> {xdm_cfg} && echo XDM_FIX_OK\r")
    result = q.wait_for(r"XDM_FIX_OK|#", timeout=5, max_wait=10)
    if "XDM_FIX_OK" in result.output:
        log("  Written xdm-config (loginProgram + grabServer + authorize)")
    else:
        log("  Warning: xdm-config write may not have applied")
        ok = False

    # Fix 3: Ensure visuallogin and windowsystem chkconfig flags are on.
    # visuallogin=on routes xdm to clogin (Indigo Magic user-picker) instead
    # of the standard xdm login dialog. windowsystem=on is the prerequisite
    # that enables the graphical login path at all. Both are typically on after
    # a Desktop install, but verify/set explicitly in case they were not written
    # by the installer.
    q.send("chkconfig visuallogin on && chkconfig windowsystem on && echo CLOGIN_FLAGS_OK\r")
    result = q.wait_for(r"CLOGIN_FLAGS_OK|#", timeout=5, max_wait=10)
    if "CLOGIN_FLAGS_OK" in result.output:
        log("  Set chkconfig visuallogin + windowsystem on")
    else:
        log("  Warning: chkconfig visuallogin/windowsystem may not have applied")
        ok = False

    # Fix 2: Disable X autorepeat via Xsetup_0
    # Use /bin/sh -c to avoid csh history expansion on '!' in shebang.
    # xdm runs Xsetup with /bin/sh regardless, so the shebang is optional
    # but included for correctness.
    q.send("/bin/sh -c '"
           "echo \"#!/bin/sh\" > /var/X11/xdm/Xsetup_0 "
           "&& echo /usr/bin/X11/xset r off >> /var/X11/xdm/Xsetup_0 "
           "&& chmod 755 /var/X11/xdm/Xsetup_0"
           "' && echo XSET_FIX_OK\r")
    result = q.wait_for(r"XSET_FIX_OK|#", timeout=5, max_wait=10)
    if "XSET_FIX_OK" in result.output:
        log("  Disabled X autorepeat (Xsetup_0)")
    else:
        log("  Warning: X autorepeat fix may not have applied")
        ok = False

    return ok


# ── Phase 1: Partition with fx ──────────────────────────────────────────────

def phase_partition(session, version_cfg):
    """Boot sash from CD, run fx to partition the disk."""
    log("Phase 1: Partitioning disk with fx")

    # Wait for PROM menu
    expect(session, r"Option\?", timeout=5, max_wait=150,
           label="PROM System Maintenance Menu")
    log("  PROM menu reached")

    # Enter Command Monitor
    session.send("5\r")
    expect(session, r">>", timeout=5, max_wait=10, label="Command Monitor")

    # Boot sashARCS from CD volume header
    session.send("boot -f dksc(0,4,8)sashARCS\r")
    expect(session, r"sash:", timeout=5, max_wait=120, label="sash prompt")
    log("  sash loaded from CD")

    # Run fx from CD filesystem
    session.send("dksc(0,4,7)stand/fx.ARCS -x\r")
    expect(session, r'fx:.*"device-name"', timeout=5, max_wait=60,
           label="fx device prompt")

    # Accept defaults: device=dksc, ctlr=0, drive=1
    session.send("\r")
    expect(session, r"ctlr", timeout=5, max_wait=10)
    session.send("\r")
    expect(session, r"drive", timeout=5, max_wait=10)
    session.send("\r")
    expect(session, r"fx>", timeout=5, max_wait=60, label="fx main menu")
    log("  fx opened disk")

    # Repartition as root drive (skips exercise — unnecessary for virtual disks,
    # and fx auto's surface scan takes 20+ min on 8GB disks)
    session.send("r\r")
    expect(session, r"fx/repartition>", timeout=5, max_wait=30,
           label="fx repartition menu")
    session.send("ro\r")
    expect(session, r"type of data partition", timeout=5, max_wait=30,
           label="fx rootdrive type")
    session.send("\r")  # accept default (xfs)
    expect(session, r"Continue\?", timeout=5, max_wait=30,
           label="fx repartition confirm")
    session.send("yes\r")
    expect(session, r"fx/repartition>", timeout=5, max_wait=60,
           label="fx repartition complete")

    # Write partition table to disk
    session.send("/\r")  # back to main menu
    expect(session, r"fx>", timeout=5, max_wait=10, label="fx main menu")
    session.send("l\r")  # label menu
    expect(session, r"fx/label>", timeout=5, max_wait=10, label="fx label menu")
    session.send("sy\r")  # sync label to disk
    expect(session, r"fx/label>", timeout=5, max_wait=60, label="fx label sync")
    session.send("/\r")  # back to main menu
    expect(session, r"fx>", timeout=5, max_wait=10,
           label="fx main menu after label")
    log("  Disk partitioned")

    # Exit fx → back to PROM menu
    session.send("exi\r")
    expect(session, r"Option\?", timeout=5, max_wait=30,
           label="PROM menu after fx")
    log("  Phase 1 complete")


# ── Phase 2: Boot miniroot and create filesystems ───────────────────────────

def phase_miniroot(session, version_cfg):
    """Boot the miniroot from CD and create filesystems."""
    log("Phase 2: Booting miniroot and creating filesystems")

    # Select option 2: Install System Software
    session.send("2\r")
    expect(session, r"enter.*to start|<enter>", timeout=5, max_wait=120,
           label="install source selection")

    # Accept default CD-ROM source
    session.send("\r")
    result = session.wait_for(
        r"Insert.*CD|Copying|press.*enter",
        timeout=5, max_wait=30
    )
    if result.matched and "Insert" in result.output:
        session.send("\r")

    # Wait for miniroot to copy and kernel to boot
    # Look for c/f/r/a prompt (IRIX 6.5), filesystem creation prompt, or installer
    result = expect(session, r"c,.*f,.*r,.*or.*a|Make new file system|Inst>|inst>",
                    timeout=10, max_wait=600,
                    label="miniroot boot / filesystem prompt")

    # Handle IRIX 6.5 miniroot continue/fix/reload prompt
    if "c," in result.output and "Make new file system" not in result.output:
        session.send("c\r")
        result = expect(session, r"Make new file system|Inst>|inst>",
                        timeout=10, max_wait=600,
                        label="filesystem prompt after miniroot continue")

    if "Make new file system" in result.output:
        _create_filesystems(session, version_cfg)

    log("  Phase 2 complete")


def _create_filesystems(session, version_cfg):
    """Handle filesystem creation prompts."""
    log("  Creating root filesystem")

    # Create root filesystem (partition 0)
    session.send("yes\r")
    expect(session, r"Are you sure", timeout=5, max_wait=15)
    session.send("y\r")

    if version_cfg["has_efs_xfs_choice"]:
        expect(session, r"efs.*xfs", timeout=5, max_wait=15,
               label="EFS/XFS choice")
        session.send(f"{version_cfg['fs_type']}\r")

    # IRIX 6.5 XFS: handle block size prompt before next stage
    if not version_cfg["has_efs_xfs_choice"]:
        result = session.wait_for(
            r"[Bb]lock size|Make new file system.*s6|Inst>|inst>|startup script|distribution",
            timeout=10, max_wait=120
        )
        if result.matched and "lock size" in result.output:
            log("  Selecting 4096-byte XFS block size")
            session.send("4096\r")
            # Now wait for the next stage
            result = session.wait_for(
                r"Make new file system.*s6|Inst>|inst>|startup script|distribution",
                timeout=10, max_wait=120
            )
    else:
        # Wait for either /usr filesystem prompt or installer
        result = session.wait_for(
            r"Make new file system.*s6|Inst>|inst>|startup script|distribution",
            timeout=10, max_wait=120
        )

    if result.matched and "Make new file system" in result.output:
        # IRIX 5.3 has a separate /usr partition
        log("  Creating /usr filesystem")
        session.send("yes\r")
        expect(session, r"Are you sure", timeout=5, max_wait=15)
        session.send("y\r")
        # Wait for installer to start
        session.wait_for(r"Inst>|inst>|startup script|distribution",
                         timeout=10, max_wait=120)

    if version_cfg["has_startup_script"]:
        _skip_startup_script(session)


def _dismiss_pager(session, max_pages=20):
    """Dismiss 'more?' pager prompts until a real prompt appears.

    Uses 'n' (not 'q') to stop the pager. The pager accepts both
    'q' and 'n' to stop, but if 'q' leaks past the pager to the Inst>
    prompt, it triggers the 'quit' command — causing cascading failures.
    'n' leaking to Inst> just produces "n is not an item" (harmless).
    """
    for _ in range(max_pages):
        result = session.wait_for(
            r"more\?|Inst>|Please enter|Do you want|yes/no|choice",
            timeout=5, max_wait=15
        )
        if not result.matched or "more?" not in result.output:
            return result
        session.send("n\r")
    return result


def _wait_for_inst_prompt(session, timeout=5, max_wait=30):
    """Wait for Inst> prompt, dismissing pagers, prompts, and quit confirmations.

    Uses space for pager dismissal. Handles:
    - Admin> by sending "return" to escape back to Inst>.
    - Interrupt> (from Ctrl-C) by sending "1" (stop) to return to Inst>.
    - "Please enter a choice" prompts by sending "2" (postpone/skip).
    - "Install software from:" by sending "done" to return to Inst>.
    - "Selections unchanged" / "Invalid choice": returns immediately (stale
      conflict numbers — caller must re-collect before retrying).
    """
    for _ in range(30):
        result = session.wait_for(
            r"Inst>|Admin>|Interrupt>|more\?|really want to quit|yes.*or.*no|"
            r"not an item|enter a choice|Please enter|"
            r"Install software from|already been opened|answer.*yes|"
            r"Selections unchanged|Invalid choice|type \? or <Enter>",
            timeout=timeout, max_wait=max_wait
        )
        if not result.matched:
            return result
        if "Admin>" in result.output:
            # Accidentally entered Admin submenu — escape back to Inst>
            session.send("return\r")
            result = session.wait_for(r"Inst>", timeout=3, max_wait=30)
            if result.matched:
                return result
            continue
        if "Selections unchanged" in result.output or \
           "Invalid choice" in result.output:
            # Conflict numbers are stale; caller must re-collect before retrying
            return result
        if "Interrupt>" in result.output:
            # Ctrl-C put inst into interrupt mode — send "1" (stop) to
            # terminate the current command and return to Inst>
            session.send("1\r")
            result = session.wait_for(r"Inst>", timeout=3, max_wait=30)
            if result.matched:
                return result
            continue
        if "Install software from" in result.output:
            # Stuck in multi-CD "Install software from:" prompt.
            # Wait for the closing ']' of the default path, then send
            # "done" to return to Inst>.
            session.wait_for(r"\]", timeout=5, max_wait=30)
            session.send("done\r")
            session.wait_for(r"Inst>|more\?", timeout=3, max_wait=30)
            continue
        if "already been opened" in result.output or \
           "answer" in result.output and "yes" in result.output:
            # "This CD has already been opened" — answer yes
            session.send("yes\r")
            session.wait_for(r"Inst>|more\?", timeout=3, max_wait=30)
            continue
        if "enter a choice" in result.output or \
           "Please enter" in result.output:
            # Conflict stream prompt (maintenance vs feature) or similar.
            # Choose option 2 to postpone/skip.
            session.send("2\r")
            session.wait_for(r"Inst>|Reading product|more\?", timeout=3, max_wait=30)
            continue
        if "more?" in result.output:
            session.send(" ")   # space advances pager; outer loop handles next state
            continue
        if "really want to quit" in result.output or \
           ("yes" in result.output and "no" in result.output):
            session.send("no\r")
            result = session.wait_for(r"Inst>", timeout=3, max_wait=30)
            if result.matched:
                return result
            continue
        if "not an item" in result.output:
            # Stray character leaked to Inst> — wait for prompt
            continue
        # Got Inst>
        return result
    return result


# All vars confirmed in inst(1) man page as "transient hidden" preferences.
# error.log_verbosity / fatal.log_verbosity default to 2 (no-ops, but harmless).
_INST_DEBUG_SETTINGS = [
    ("debug",                    "true"),
    ("checkpoint_debug",         "true"),
    ("display_subtasks",         "true"),
    ("explorer_debug",           "true"),
    ("tape_debug",               "true"),
    ("rules_debug",              "true"),
    ("rules_nonbootable_ok",     "true"),
    ("rules_verbose_debug",      "true"),
    ("file_debug",               "all"),
    ("error.display_verbosity",  "2"),
    ("error.log_verbosity",      "2"),
    ("fatal.display_verbosity",  "2"),
    ("fatal.log_verbosity",      "2"),
]


def _set_inst_verbosity(session, debug=False):
    """Enter inst Admin menu, optionally set debug preferences, then return to Inst>.

    When debug=True, sends 'set <var> <val>' for each entry in _INST_DEBUG_SETTINGS.
    All vars are confirmed in inst(1) man page as transient hidden preferences.
    Older inst versions may print "No preference <var>" for unknown vars — harmless.

    The admin submenu prints a numbered list with a 'more?' pager before
    showing the Admin> prompt. We must dismiss each page before sending
    'return'. The exit command back to Inst> is 'return' (item 21), not
    'end' or 'quit'.
    """
    session.send("admin\r")
    # Dismiss the admin menu's 'more?' pager before Admin> appears.
    # Send space only when more? appears WITHOUT Admin> on the same chunk —
    # avoids accidentally sending space after Admin> is already shown.
    for _ in range(10):
        result = session.wait_for(r"Admin>|more\?", timeout=5, max_wait=30)
        if not result.matched:
            break
        if "more?" in result.output and "Admin>" not in result.output:
            session.send(" ")   # advance pager — Admin> not yet shown
            continue
        break  # Admin> reached (pager may be done on same chunk)
    if debug:
        log("  Enabling inst debug settings via Admin menu")
        for var, val in _INST_DEBUG_SETTINGS:
            session.send(f"set {var} {val}\r")
            session.wait_for(r"Admin>", timeout=3, max_wait=10)
    # 'return' exits the Admin submenu back to Inst> (item 21 in the menu)
    session.send("return\r")
    session.wait_for(r"Inst>", timeout=3, max_wait=15)


# ── Conflict collection and structured resolution ────────────────────────────

# Packages that form the OS foundation and must never be deselected when
# resolving incompatibility conflicts. If a conflict is between one of these
# and any other package, always deselect the other.
_CORE_PACKAGES = frozenset({
    "eoe", "eoe.sw", "eoe.sw.base",
    "irix_dev", "irix_dev.sw", "irix_dev.sw.headers",
    "x_eoe", "x_eoe.sw",
    "motif_eoe", "motif_eoe.sw",
    "compiler_eoe", "compiler_eoe.sw",
    "ftn_eoe", "ftn_eoe.sw",
    "c++_eoe", "c++_eoe.sw",
    "desktop_eoe", "desktop_eoe.sw",
    "4Dwm", "4Dwm.sw",
    "ViewKit_eoe", "ViewKit_eoe.sw",
    "insight_base", "insight_base.sw",
})


def _is_core_package(pkg_name):
    """True if pkg_name is a foundational OS package that must never be deselected."""
    return (pkg_name in _CORE_PACKAGES or
            any(pkg_name.startswith(c + ".") for c in _CORE_PACKAGES))


def _select_conflict_option(conflict, allow_also_install=False):
    """Choose the best option label for a parsed conflict dict.

    Rules applied in order:
    1. Never choose open_dist — we already have all CDs open; adding another
       distribution mid-install breaks the automated flow.
    2. Never choose also_install unless explicitly allowed — it opens the
       distribution selector dialog and can cascade into more conflicts when
       the prerequisite CD is not in our open set.
    3. For missing_prereq: deselect the package that needs the missing
       prerequisite (always do_not_install the overlay).
    4. For incompatible: deselect the non-core package. If both are core or
       both are non-core, deselect the subject (first-listed) package.
    5. For cannot_remove: choose 'remove' if available, else 'do_not_remove'.
    6. For required/unknown: first safe option.

    Returns the option label string (e.g. "1a"), or None if no options.
    """
    opts = conflict.get("options", [])
    conflict_type = conflict.get("type", "unknown")

    excluded_actions = {"open_dist"}
    if not allow_also_install:
        excluded_actions.add("also_install")

    safe = [o for o in opts if o.get("action") not in excluded_actions]
    fallback = safe[0] if safe else (opts[0] if opts else None)
    if not fallback:
        return None

    if conflict_type == "missing_prereq":
        # The subject needs a prerequisite.  If also_install is permitted,
        # prefer it: the prereq is almost certainly in an already-open
        # distribution (e.g. Foundation CD mounted alongside Overlays).
        # Inst's "insert another CD" wording is generic boilerplate — it
        # does NOT mean the dist is unavailable when already opened.
        # Deselecting the overlay (do_not_install) is only the right move
        # when also_install is explicitly disallowed (e.g. quit phase).
        if allow_also_install:
            for o in safe:
                if o.get("action") == "also_install":
                    return o["label"]
        for o in safe:
            if o.get("action") == "do_not_install":
                return o["label"]
        return fallback["label"]

    if conflict_type == "incompatible":
        do_not = [o for o in safe if o.get("action") == "do_not_install"]
        if len(do_not) >= 2:
            # Multiple do_not_install options: prefer deselecting the non-core one.
            for o in do_not:
                pkg = o.get("package", "")
                if pkg and not _is_core_package(pkg):
                    return o["label"]
            # Both are core (unusual) — fall back to first.
        if do_not:
            return do_not[0]["label"]
        return fallback["label"]

    if conflict_type == "cannot_remove":
        for o in safe:
            if o.get("action") == "remove":
                return o["label"]
        for o in safe:
            if o.get("action") == "do_not_remove":
                return o["label"]
        return fallback["label"]

    if conflict_type in ("required", "unknown"):
        for o in safe:
            if o.get("action") == "do_not_install":
                return o["label"]
        return fallback["label"]

    return fallback["label"]


def _collect_all_conflicts(session):
    """Send `conflicts` to inst and paginate through the full output.

    Uses space to advance the pager (same as inst's default).  Stops as
    soon as Inst> appears so stray pager advances don't hit the main menu.

    Returns the raw text of all conflicts, or empty string if none.
    """
    _wait_for_inst_prompt(session, timeout=3, max_wait=10)
    session.send("conflicts\r")

    all_text = ""
    for _ in range(200):  # up to 200 pages
        result = session.wait_for(
            r"Inst>|more\?|[Nn]o conflicts|or try .help conflicts",
            timeout=3, max_wait=60
        )
        all_text += result.output
        if not result.matched:
            break
        if "no conflict" in result.output.lower():
            break
        if "Inst>" in result.output:
            break  # Back at prompt — stop immediately, don't send more input
        if "more?" in result.output:
            session.send(" ")  # space advances pager; avoids 'y not a menu item'
            continue
        break

    return all_text


def parse_conflicts(text):
    """Parse inst conflict output into structured data.

    Returns a list of dicts, each representing one conflict:
    {
        "number": 1,
        "type": "incompatible" | "missing_prereq" | "required",
        "description": "x_dev.sw.dev (1274627333) is incompatible with ...",
        "subject": "x_dev.sw.dev",
        "subject_version": "1274627333",
        "against": "eoe.sw.base",        # for incompatible
        "against_version": "1285719131",  # for incompatible
        "options": [
            {"label": "1a", "action": "do_not_install",
             "package": "x_dev.sw.dev", "version": "1274627333"},
            {"label": "1b", "action": "do_not_install",
             "package": "eoe.sw.base", "version": "1285719131"},
            {"label": "1c", "action": "open_dist"},
        ]
    }
    """
    conflicts = []

    # Strip ANSI escapes, command echo, and inst chrome
    text = re.sub(r'\x1b\[[0-9;]*m', '', text)

    # Split into conflict blocks. Each conflict starts with a description
    # line followed by indented option lines (  Na. ...).
    # Conflicts are separated by blank lines.
    lines = text.splitlines()

    current_desc = []
    current_options = []
    current_number = 0

    def flush_conflict():
        nonlocal current_desc, current_options, current_number
        if not current_desc or not current_options:
            current_desc = []
            current_options = []
            return

        desc_text = " ".join(current_desc)
        conflict = _parse_one_conflict(desc_text, current_options,
                                       current_number)
        if conflict:
            conflicts.append(conflict)
        current_desc = []
        current_options = []

    in_pager = False  # True after seeing "more?" — suppress blank lines
    for line in lines:
        stripped = line.strip()

        # Skip empty, chrome, prompts
        if not stripped:
            if in_pager:
                # Blank/whitespace lines right after "more?" are terminal
                # padding, not conflict block separators. Skip them.
                continue
            flush_conflict()
            continue
        if stripped.startswith("Inst>") or stripped.startswith("conflicts"):
            continue
        if "help conflicts" in stripped:
            continue
        # Strip "more? (h=help)" prefix — it can appear on the same line
        # as option text (e.g. "more? (h=help)    4a. Do not install ...")
        if stripped.startswith("more?"):
            in_pager = True
            stripped = re.sub(r'^more\?\s*(\(h=help\))?\s*', '', stripped)
            if not stripped:
                continue
        else:
            in_pager = False
        if re.match(r'^\d+\.\s+(from|open|install|keep|remove|go|conflicts'
                    r'|step|list|view|quit|admin|return|help|sh|set)',
                    stripped):
            # inst menu items
            continue
        if stripped.startswith("Reading product") or \
           stripped.startswith("Skipping product"):
            continue

        # Option line: "  Na. Do not install ..." or "  Na. Also install ..."
        opt_match = re.match(
            r'^\s*(\d+)([a-z])\.\s+(.*)', stripped
        )
        if opt_match:
            num = int(opt_match.group(1))
            letter = opt_match.group(2)
            opt_text = opt_match.group(3).strip()
            current_number = num
            current_options.append({
                "label": f"{num}{letter}",
                "text": opt_text,
            })
            continue

        # Description line (not indented option)
        # Could be: "X (VER) is incompatible with Y (VER)"
        #           "X cannot be installed because of missing prerequisites:"
        #           "X is required and must be installed"
        if re.search(r'is incompatible with|cannot be installed|'
                     r'is required.*must be installed|'
                     r'is installed but is missing prerequisites|'
                     r'cannot be removed because', stripped):
            flush_conflict()
            current_desc = [stripped]
        elif current_desc:
            # Continuation of a multi-line description
            current_desc.append(stripped)

    flush_conflict()

    return conflicts


def _parse_one_conflict(desc_text, raw_options, number):
    """Parse a single conflict description + options into a dict."""
    conflict = {
        "number": number,
        "description": desc_text,
        "options": [],
    }

    # Determine conflict type
    if "is incompatible with" in desc_text:
        conflict["type"] = "incompatible"
        m = re.match(
            r'(\S+)\s+\((\d+)\)\s+is incompatible with\s+(\S+)\s*\((\d+)\)',
            desc_text
        )
        if m:
            conflict["subject"] = m.group(1)
            conflict["subject_version"] = m.group(2)
            conflict["against"] = m.group(3)
            conflict["against_version"] = m.group(4)
        else:
            # Partial parse
            m2 = re.match(r'(\S+)\s+\((\d+)\)\s+is incompatible with\s+(\S+)',
                          desc_text)
            if m2:
                conflict["subject"] = m2.group(1)
                conflict["subject_version"] = m2.group(2)
                conflict["against"] = m2.group(3)
                conflict["against_version"] = ""

    elif "cannot be installed" in desc_text or \
            "is installed but is missing prerequisites" in desc_text:
        conflict["type"] = "missing_prereq"

    elif "cannot be removed because" in desc_text:
        conflict["type"] = "cannot_remove"
        # "Overlay product PKG (VER) cannot be installed ..."
        m = re.match(
            r'(?:Overlay product\s+|base product\s+)?(\S+)\s+'
            r'(?:\(\d+\)\s+)?cannot be installed',
            desc_text
        )
        if m:
            conflict["subject"] = m.group(1)
        conflict["subject_version"] = ""
        conflict["against"] = ""
        conflict["against_version"] = ""

    elif "is required" in desc_text:
        conflict["type"] = "required"
        m = re.match(r'(\S+)\s+is required', desc_text)
        if m:
            conflict["subject"] = m.group(1)
        conflict["subject_version"] = ""
        conflict["against"] = ""
        conflict["against_version"] = ""

    else:
        conflict["type"] = "unknown"
        conflict["subject"] = ""
        conflict["subject_version"] = ""
        conflict["against"] = ""
        conflict["against_version"] = ""

    # Parse options
    for raw_opt in raw_options:
        label = raw_opt["label"]
        text = raw_opt["text"]

        opt = {"label": label, "text": text}

        if text.startswith("Do not install"):
            opt["action"] = "do_not_install"
            m = re.match(r'Do not install\s+(\S+)\s*\((\d+)\)', text)
            if m:
                opt["package"] = m.group(1)
                opt["version"] = m.group(2)
            else:
                m2 = re.match(r'Do not install\s+(\S+)', text)
                opt["package"] = m2.group(1) if m2 else ""
                opt["version"] = ""
        elif text.startswith("Do not remove"):
            opt["action"] = "do_not_remove"
            m = re.match(r'Do not remove\s+(\S+)\s*\((\d+)\)', text)
            if m:
                opt["package"] = m.group(1)
                opt["version"] = m.group(2)
            else:
                m2 = re.match(r'Do not remove\s+(\S+)', text)
                opt["package"] = m2.group(1) if m2 else ""
                opt["version"] = ""
        elif text.startswith("Remove"):
            opt["action"] = "remove"
            m = re.match(r'Remove\s+(\S+)\s*\((\d+)\)', text)
            if m:
                opt["package"] = m.group(1)
                opt["version"] = m.group(2)
            else:
                m2 = re.match(r'Remove\s+(\S+)', text)
                opt["package"] = m2.group(1) if m2 else ""
                opt["version"] = ""
        elif text.startswith("Also install"):
            opt["action"] = "also_install"
            m = re.match(r'Also install\s+(\S+)\s*\((\d+)', text)
            if m:
                opt["package"] = m.group(1)
                opt["version"] = m.group(2)
            else:
                m2 = re.match(r'Also install\s+(\S+)', text)
                opt["package"] = m2.group(1) if m2 else ""
                opt["version"] = ""
        elif "Open new distribution" in text:
            opt["action"] = "open_dist"
            opt["package"] = ""
            opt["version"] = ""
        else:
            opt["action"] = "unknown"
            opt["package"] = ""
            opt["version"] = ""

        conflict["options"].append(opt)

    return conflict


def save_conflicts(conflicts, path):
    """Save parsed conflicts to a JSON file."""
    with open(path, "w") as f:
        json.dump(conflicts, f, indent=2)
    log(f"  Saved {len(conflicts)} conflicts to {path}")


def load_conflict_resolutions(path):
    """Load conflict resolution decisions from a JSON file.

    Preferred format — matched by package name:
    {
        "resolutions": [
            {"package": "dps_eoe.sw.dpsfonts", "action": "do_not_install"},
            {"package": "xpdf.sw.xpdf", "action": "do_not_install"},
            ...
        ]
    }

    Or the shorthand format using inst's native syntax:
    {
        "commands": ["conflicts 1a 2a 3a 4a", "conflicts 5a 6a 7a 8a"]
    }
    """
    with open(path) as f:
        return json.load(f)


def _apply_conflict_resolutions(session, decisions):
    """Apply conflict resolution decisions to inst.

    Accepts either:
    - {"commands": ["conflicts 1a 2a 3a", ...]} — raw inst commands
    - {"resolutions": [...]} — structured, matched by package name

    Structured resolution format (preferred):
    [
        {"package": "dps_eoe.sw.dpsfonts", "action": "do_not_install"},
        {"package": "mozilla.sw.mips4", "action": "do_not_install"},
        {"package": "some.pkg", "action": "also_install"},
    ]

    Because inst's conflict numbers are dynamic (they change as conflicts
    are resolved), we re-read the live conflict list, match each resolution
    by package name (the conflict's subject field), find the option with the
    matching action, and use that option's inst-assigned label.

    Returns (success, remaining_text) where remaining_text is the output
    after applying all decisions (may still contain unresolved conflicts).
    """
    if "commands" in decisions:
        # Raw inst commands — send verbatim
        for cmd in decisions["commands"]:
            _wait_for_inst_prompt(session, timeout=3, max_wait=10)
            time.sleep(0.3)
            session.send(f"{cmd}\r")
            for _ in range(30):
                result = session.wait_for(
                    r"Inst>|more\?", timeout=10, max_wait=60
                )
                if "more?" in result.output:
                    session.send("n\r")
                    continue
                break
    elif "resolutions" in decisions:
        resolutions = decisions["resolutions"]
        # Build a lookup: package_name -> desired action
        desired = {}
        for r in resolutions:
            pkg = r.get("package", "")
            action = r.get("action", "do_not_install")
            if pkg:
                desired[pkg] = action

        # Re-read live conflicts so we use current inst numbering
        raw_text = _collect_all_conflicts(session)
        live_conflicts = parse_conflicts(raw_text)

        # Match each live conflict to a resolution by subject package name
        labels_to_send = []
        for conflict in live_conflicts:
            subject = conflict.get("subject", "")
            if subject not in desired:
                continue
            want_action = desired[subject]
            # Find the option with matching action
            matched_label = None
            for opt in conflict.get("options", []):
                if opt.get("action") == want_action:
                    matched_label = opt["label"]
                    break
            if matched_label is None and conflict["options"]:
                # Fallback: if action not found, use first option ('a')
                matched_label = conflict["options"][0]["label"]
                log(f"  WARNING: action '{want_action}' not found for "
                    f"{subject}, falling back to {matched_label}")
            if matched_label:
                labels_to_send.append(matched_label)

        # Send in batches of 10 to avoid line buffer overflow
        for i in range(0, len(labels_to_send), 10):
            batch = " ".join(labels_to_send[i:i+10])
            _wait_for_inst_prompt(session, timeout=3, max_wait=10)
            time.sleep(0.3)
            session.send(f"conflicts {batch}\r")
            for _ in range(30):
                result = session.wait_for(
                    r"Inst>|more\?", timeout=10, max_wait=60
                )
                if "more?" in result.output:
                    session.send("n\r")
                    continue
                break

    # Check if conflicts remain
    remaining = _collect_all_conflicts(session)
    has_conflicts = ("is incompatible" in remaining or
                     "cannot be installed" in remaining or
                     "is required" in remaining)
    return not has_conflicts, remaining


def _collect_conflicts_output(session, timeout=60):
    """Send 'conflicts' and paginate through the complete listing.

    Uses space to advance each page of the pager.  Returns all accumulated
    serial output (may span many pages).
    """
    session.send("conflicts\r")
    all_output = ""
    for _ in range(100):  # up to 100 pager pages
        result = session.wait_for(
            r"Inst>|more\?",
            timeout=10, max_wait=timeout,
        )
        all_output += result.output
        if not result.matched or "Inst>" in result.output:
            break
        # more? — advance pager with space
        session.send(" ")
    return all_output


def _parse_conflict_choices(text):
    """Parse inst conflicts output into per-conflict option lists.

    Returns dict: conflict_num (int) -> list of (letter, action, desc)
    where action is one of:
        'also_install'  — option text starts with "Also install"
        'remove'        — option text starts with "Remove"
        'do_not_remove' — option text contains "Do not remove"
        'other'         — anything else (e.g. "Do not install")
    """
    conflicts = {}
    for line in text.splitlines():
        m = re.match(r'\s+(\d+)([a-z])\.\s+(.*)', line)
        if not m:
            continue
        num, letter, desc = int(m.group(1)), m.group(2), m.group(3).strip()
        desc_lower = desc.lower()
        if 'also install' in desc_lower:
            action = 'also_install'
        elif desc_lower.startswith('remove'):
            action = 'remove'
        elif 'do not remove' in desc_lower:
            action = 'do_not_remove'
        else:
            action = 'other'
        conflicts.setdefault(num, []).append((letter, action, desc))
    return conflicts


def _resolve_conflicts(session, max_rounds=50, allow_also_install=False,
                       prefer_also_install=False, default_option="1a",
                       also_install_max_rounds=10):
    """Type-aware conflict resolver using the structured parse_conflicts() parser.

    Uses _select_conflict_option() for all choice selection logic:
      missing_prereq  — prefer also_install for the first also_install_max_rounds
                        rounds (pulls in foundation bases for overlay packages),
                        then fall back to do_not_install to stop cascade growth.
      cannot_remove   — remove the blocking dependent ('remove' option)
      incompatible    — do_not_install the non-core package; if ambiguous,
                        do_not_install the subject (first-listed)
      required        — do_not_install if possible, else first safe option
      unknown         — first safe option

    open_dist and also_install options are excluded by default. Pass
    allow_also_install=True to permit also_install for the first
    also_install_max_rounds rounds (only useful when the prerequisite package
    is actually present in our open distributions).

    The `prefer_also_install` and `default_option` parameters are kept for
    call-site compatibility but are no longer used.

    Returns (resolved, skipped) where resolved is True if all conflicts
    resolved (or none existed), and skipped is a list of removed package
    names.
    """
    skipped = []

    for round_num in range(max_rounds):
        # Ensure we're at a clean Inst> prompt before querying.
        _wait_for_inst_prompt(session, timeout=3, max_wait=15)

        # Collect the full paginated conflict listing using the rich pager.
        all_output = _collect_all_conflicts(session)

        # Handle "enter a choice" / "Please enter" interstitials that
        # appear when inst wants to address an incompatibility inline.
        if "enter a choice" in all_output or "Please enter" in all_output:
            incompat_pkgs = _extract_incompatible_packages(all_output)
            if incompat_pkgs:
                # Postpone (option 2), then remove the blocking packages
                # so the new version can install.
                session.send("2\r")
                time.sleep(0.3)
                _wait_for_inst_prompt(session, timeout=5, max_wait=15)
                for pkg in incompat_pkgs:
                    log(f"    Removing incompatible: {pkg}")
                    session.send(f"remove {pkg}\r")
                    _wait_for_inst_prompt(session, timeout=5, max_wait=15)
            else:
                session.send("1\r")
                time.sleep(0.3)
            continue

        # Done if no conflicts remain.
        lower = all_output.lower()
        if "no conflict" in lower or not all_output.strip():
            return True, skipped

        conflicts = parse_conflicts(all_output)
        if not conflicts:
            return True, skipped

        # ── Type-aware resolution ──────────────────────────────────────────
        # Build one conflicts command covering all current conflicts.
        choices = []
        for c in conflicts:
            if not c.get("options"):
                continue

            # Limit also_install to the first N rounds to prevent infinite
            # cascade: each also_install adds new packages that themselves
            # have missing_prereq conflicts, which would loop forever.
            round_allow_also = (
                allow_also_install and round_num < also_install_max_rounds
            )
            label = _select_conflict_option(c, allow_also_install=round_allow_also)
            if label is None:
                continue
            choices.append(label)

            # Track which package was deselected for the caller.
            selected_opt = next(
                (o for o in c["options"] if o["label"] == label), None
            )
            if selected_opt:
                pkg = selected_opt.get("package", "")
                if pkg and pkg not in skipped:
                    skipped.append(pkg)

        if not choices:
            log(f"  WARNING: No actionable choices found after "
                f"{round_num + 1} rounds")
            break

        # Send at most 20 choices per outer iteration.  After inst processes
        # a conflicts command it renumbers the remaining items starting from 1,
        # so any indices beyond the first batch become invalid immediately.
        # The outer loop re-collects with fresh numbers on every iteration.
        BATCH = 20
        batch = choices[:BATCH]
        remaining = max(0, len(choices) - BATCH)
        log(f"  Resolving {len(choices)} conflict(s) "
            f"({remaining} will be re-collected after this batch)")
        batch_str = " ".join(batch)
        log(f"    conflicts {batch_str}")
        _wait_for_inst_prompt(session, timeout=3, max_wait=10)
        session.send(f"conflicts {batch_str}\r")
        # After processing a batch, inst may:
        #   a) Return to Inst> immediately (no cascade conflicts)
        #   b) Show new (cascade) conflicts through the MORE pager
        #   c) Show "Install software from:" if a 'b' (also_install) choice
        #      triggered an interactive distribution selection menu
        # We must handle the pager here; if "more?" is not consumed, the
        # next _collect_all_conflicts will send "conflicts\r" to the pager
        # which garbles the output and causes _resolve_conflicts to return
        # True prematurely (false "no conflicts remaining").
        for _ in range(120):
            _result = session.wait_for(
                r"Inst>|Install software from|more\?",
                timeout=10, max_wait=60
            )
            if not _result.matched:
                break
            if "more?" in _result.output:
                session.send(" ")  # advance the pager
                continue
            if "Install software from" in _result.output:
                session.wait_for(r"\]", timeout=5, max_wait=10)
                session.send("done\r")
                _wait_for_inst_prompt(session, timeout=5, max_wait=60)
            break  # got Inst>

    return False, skipped


def _extract_incompatible_packages(output):
    """Extract package names that are blocking due to incompatibility.

    Parses lines like:
      eoe.sw.base (1289434520) is incompatible with media_warehouse.sw.viewers_movie
      (1274627334)

    Returns a list of the *old* packages (the ones after "incompatible with")
    that should be removed to allow the new version to install.
    """
    blockers = []
    for m in re.finditer(
        r'is incompatible with\s+(\S+)',
        output
    ):
        pkg = m.group(1)
        # Skip hardware incompatibility ("incompatible with your hardware")
        if pkg in ("your",):
            continue
        if pkg not in blockers:
            blockers.append(pkg)
    return blockers


def _skip_startup_script(session):
    """Skip the IRIX 6.2/6.5 startup script prompts and READMEs."""
    # First, dismiss any README/intro pager
    result = _dismiss_pager(session)

    if not result.matched or "Inst>" in result.output:
        return

    # Handle startup script choice menu (IRIX 6.2/6.5)
    if result.matched and "Please enter" in result.output:
        # Send "2" (Ignore/skip) — harmless if 6.2 already auto-started
        session.send("2\r")

    # Skip COFF check
    for _ in range(5):
        result = session.wait_for(
            r"Do you want|yes/no|Inst>|Distribution|press.*ENTER|completed",
            timeout=5, max_wait=30
        )
        if not result.matched:
            break
        if "Inst>" in result.output:
            return
        if "ENTER" in result.output or "completed" in result.output:
            session.send("\r")
            continue
        if "yes/no" in result.output or "Do you want" in result.output:
            session.send("no\r")
            continue
        break

    # Final wait for Inst> prompt
    session.wait_for(r"Inst>", timeout=5, max_wait=60)


# ── Phase 3: Install packages ──────────────────────────────────────────────

def phase_install(session, version_cfg, conflict_mode="auto",
                  conflict_resolutions=None, install_level="standard",
                  inst_debug=False):
    """Select and install packages.

    Args:
        conflict_mode: "auto", "collect", or "apply". See
            _install_from_combined() for details.
        conflict_resolutions: Resolution decisions for "apply" mode.
        install_level: "standard" (recommended) or "default" (everything).

    Returns:
        - conflict_mode="auto": list of skipped package names
        - conflict_mode="collect": list of parsed conflict dicts
        - conflict_mode="apply": list of skipped package names
    """
    log("Phase 3: Installing packages")

    if version_cfg["needs_rulesoverride"]:
        log("  Enabling rulesoverride (IMPACT CD compatibility)")
        session.send("admin\r")
        expect(session, r"Admin>", timeout=5, max_wait=10)
        session.send("set rulesoverride on\r")
        expect(session, r"Admin>", timeout=5, max_wait=10)
        session.send("return\r")
        expect(session, r"Inst>", timeout=5, max_wait=10)

        # Reset and select default packages
        session.send("keep *\r")
        expect(session, r"Inst>", timeout=5, max_wait=10)
        session.send("install default\r")
        expect(session, r"Inst>", timeout=5, max_wait=30)

    if version_cfg.get("needs_second_cd"):
        # Check for combined distribution image first.
        # The combined image has all 7 CDs' dist/ files in one EFS image,
        # eliminating CD swapping and cross-CD dependency issues.
        combined = version_cfg.get("combined_image", "")
        use_combined = (combined and os.path.exists(combined)
                        and not version_cfg.get("_no_combined"))
        if use_combined:
            log(f"  Combined image found: {os.path.basename(combined)}")
            return _install_from_combined(
                session, version_cfg,
                conflict_mode=conflict_mode,
                conflict_resolutions=conflict_resolutions,
                install_level=install_level,
                inst_debug=inst_debug)

        # Fallback: install one CD at a time to avoid mid-install CD swaps.
        # QEMU's SCSI media change via monitor `change` command causes inst's
        # verify_volume() to hang permanently during `go`. Instead, we open
        # each CD's distributions, select standard packages, and install.
        # After each `go`, packages from that CD are installed. `install
        # standard` is smart enough to not re-select already-installed packages.
        return _install_cd_by_cd(session, version_cfg, inst_debug=inst_debug)

    # Single-CD install (IRIX 5.3, 6.2)
    session.send("go\r")
    log("  Running go...")

    # Handle conflicts, pagers, and wait for completion
    _wait_for_install_complete(session, version_cfg)
    return []


def _build_cd_sequence(version_cfg):
    """Build the ordered list of CDs for scanning and installation.

    Returns list of (cd_image_path, cd_name, is_on_target4) tuples.
    Order: Foundation 1, Foundation 2, Boot CD (Overlays 1 on target 4),
           Overlays 2-3, Applications, InstTools.

    Overlays 1 must be installed before Overlays 2/3 and Applications
    because it contains eoe.sw.base (overlay version 1289434520).
    Without it, all overlay and application packages that depend on the
    overlay eoe.sw.base get skipped as "missing prerequisites".
    """
    cdroms = version_cfg["cdroms"]
    extra_cds = version_cfg.get("extra_cds", [])
    cd_sequence = []

    # Foundation 1 (already on SCSI target 5 at boot)
    if len(cdroms) > 1:
        cd_sequence.append((cdroms[1],
                            os.path.basename(cdroms[1]).replace(".img", ""),
                            False))

    # Foundation 2 (first extra CD)
    if extra_cds:
        cd_sequence.append((extra_cds[0],
                            os.path.basename(extra_cds[0]).replace(".img", ""),
                            False))

    # Boot CD (Overlays 1 on target 4) — install EARLY so eoe.sw.base
    # overlay version is available for all subsequent CDs
    cd_sequence.append((cdroms[0],
                        os.path.basename(cdroms[0]).replace(".img", ""),
                        True))

    # Remaining extra CDs (Overlays 2-3, Applications, InstTools)
    for cd_path in extra_cds[1:]:
        cd_sequence.append((cd_path,
                            os.path.basename(cd_path).replace(".img", ""),
                            False))

    return cd_sequence


def _mount_cd(session, cd_path, cd_name, is_target4, need_swap,
              tolerant=False):
    """Mount a CD at /CDROM. Handles umount, media swap, and mount.

    Args:
        session: QEMUSession
        cd_path: Path to CD image file
        cd_name: Human-readable CD name (for logging)
        is_target4: True if CD is on SCSI target 4 (boot CD)
        need_swap: True if we need to swap media on target 5
        tolerant: If True, return False on mount failure instead of crashing

    Returns:
        True if mount succeeded, False if tolerant=True and mount failed.
    """
    # Ensure we're at Inst> first (handles stray quit prompts)
    _wait_for_inst_prompt(session, timeout=5, max_wait=15)

    session.send("sh\r")
    result = session.wait_for(r"#", timeout=5, max_wait=10)
    if not result.matched:
        if tolerant:
            log(f"    WARNING: shell not available, skipping {cd_name}")
            return False
        fail(f"Expected '#' but got: {result.bail_reason}")
    session.send("umount /CDROM 2>/dev/null; true\r")
    session.wait_for(r"#", timeout=5, max_wait=10)

    if is_target4:
        # Boot CD is permanently on SCSI target 4
        session.send("mount -r /dev/dsk/dks0d4s7 /CDROM\r")
        result = session.wait_for(r"#", timeout=10, max_wait=60)
        if not result.matched:
            if tolerant:
                log(f"    WARNING: mount target 4 timed out, skipping")
                session.send("\x03")  # Ctrl-C to cancel hung mount
                session.wait_for(r"Inst>|Interrupt>", timeout=3, max_wait=15)
                session.send("exit\r")
                _wait_for_inst_prompt(session, timeout=5, max_wait=15)
                return False
            fail(f"Expected '#' but got: {result.bail_reason}")
        session.send("exit\r")
        expect(session, r"Inst>", timeout=5, max_wait=10)
    elif need_swap:
        # Swap media on target 5 via QEMU monitor
        session.send("exit\r")
        expect(session, r"Inst>", timeout=5, max_wait=10)
        log(f"    Swapping CD...")
        resp = session.change_media(5, cd_path)
        log(f"    Swap response: {resp.strip()!r}")
        time.sleep(2)
        session.send("sh\r")
        expect(session, r"#", timeout=5, max_wait=10)
        session.send("mount -r /dev/dsk/dks0d5s7 /CDROM\r")
        result = session.wait_for(r"#", timeout=10, max_wait=60)
        if not result.matched:
            if tolerant:
                log(f"    WARNING: mount target 5 timed out, skipping")
                session.send("\x03")
                session.wait_for(r"Inst>|Interrupt>", timeout=3, max_wait=15)
                session.send("exit\r")
                _wait_for_inst_prompt(session, timeout=5, max_wait=15)
                return False
            fail(f"Expected '#' but got: {result.bail_reason}")
        session.send("exit\r")
        expect(session, r"Inst>", timeout=5, max_wait=10)
    else:
        # First CD on target 5 — already attached
        session.send("mount -r /dev/dsk/dks0d5s7 /CDROM\r")
        expect(session, r"#", timeout=5, max_wait=30)
        session.send("exit\r")
        expect(session, r"Inst>", timeout=5, max_wait=10)

    return True


def _from_dist(session, cd_name):
    """Set /CDROM/dist as the active distribution source with `from`.

    Returns True if distribution was set successfully, False if empty.
    """
    session.send("from /CDROM/dist\r")
    dist_empty = False
    for _ in range(30):
        result = session.wait_for(
            r"Inst>|more\?|Please enter|startup script|"
            r"Distribution is empty|Cannot read|"
            r"Install software from|enter a choice|"
            r"switch distributions|already been opened|"
            r"yes.*or.*no|answer.*yes",
            timeout=10, max_wait=120
        )
        if not result.matched:
            break
        if "Distribution is empty" in result.output or \
           "Cannot read" in result.output:
            if not dist_empty:
                log(f"    WARNING: Distribution empty or unreadable")
            dist_empty = True
            # Don't return yet — inst may show "Install software from:"
            # multi-CD prompt that we need to dismiss with "done".
            continue
        if "more?" in result.output:
            session.send("n\r")
            continue
        if "switch distributions" in result.output:
            # "Do you really want to switch distributions?" — yes
            session.send("y\r")
            continue
        if "already been opened" in result.output or \
           "answer" in result.output and "yes" in result.output:
            # "This CD has already been opened. Open again?" — yes.
            # Happens when boot CD is also used as an install source.
            session.send("yes\r")
            continue
        if "yes" in result.output and "no" in result.output:
            # Generic yes/no prompt — answer yes
            session.send("yes\r")
            continue
        if "Install software from" in result.output:
            # "This CD is part of a set" prompt asking for another CD.
            # Send "done" to proceed with just this CD.
            session.send("done\r")
            continue
        if "Please enter" in result.output or \
           "startup script" in result.output or \
           "enter a choice" in result.output:
            # Stream selection (maintenance/feature) or startup script.
            # Choose option 2 (feature stream / skip script).
            session.send("2\r")
            continue
        break  # Got Inst>
    return not dist_empty


def _install_per_cd(session, cd_sequence, inst_debug=False):
    """Phase C: Install packages one CD at a time.

    For each CD: mount it, set it as the active distribution with `from`,
    re-select standard packages (so inst knows which packages to read
    from this CD), and run `go`.

    After each `go`, installed packages are on disk. Subsequent CDs
    see those packages as already installed, resolving cross-CD deps
    progressively.

    Returns list of package names skipped due to conflict resolution.
    """
    log("  Phase C: Installing packages per-CD")
    total = len(cd_sequence)
    all_skipped = []

    # Enable verbose installer output so exitops and file operations are
    # visible in the transcript (helps debug issues like the empty xdm-config
    # that sysadmdesktop exitop should have written).
    _set_inst_verbosity(session, debug=inst_debug)

    for seq_idx, (cd_path, cd_name, is_target4) in enumerate(cd_sequence):
        log(f"  [install {seq_idx+1}/{total}] {cd_name}")

        # Mount the CD
        need_swap = not is_target4 and seq_idx > 0
        _mount_cd(session, cd_path, cd_name, is_target4, need_swap)

        # Set this CD as the active distribution source
        if not _from_dist(session, cd_name):
            log(f"    Skipping {cd_name} (distribution unreadable)")
            continue
        log(f"    Set as active distribution")

        # Select standard packages from this CD
        session.send("keep *\r")
        _wait_for_inst_prompt(session, timeout=5, max_wait=15)
        session.send("install standard\r")
        _wait_for_inst_prompt(session, timeout=5, max_wait=30)
        session.send("install prereqs\r")
        _wait_for_inst_prompt(session, timeout=5, max_wait=30)

        # Resolve any remaining conflicts — allow also_install because all
        # distributions are open during the main install phase.
        resolved, skipped = _resolve_conflicts(session, allow_also_install=True)
        if not resolved:
            log(f"    WARNING: could not resolve all conflicts")
        if skipped:
            log(f"    Skipped {len(skipped)} packages due to conflicts")
            all_skipped.extend(skipped)

        # Ensure we're at Inst> before sending go
        _wait_for_inst_prompt(session, timeout=5, max_wait=15)

        # Run installation for this CD
        session.send("go\r")
        log(f"    Running go...")

        _wait_for_cd_install(session, cd_name)
        log(f"    Complete")

    return all_skipped


def _reconciliation_pass(session, cd_sequence):
    """Reconciliation: re-check earlier CDs for packages skipped due to ordering.

    After all CDs are installed, some packages from earlier CDs may
    have been skipped because their prerequisites (on later CDs) weren't
    installed yet. Iterate through all CDs again, selecting standard
    packages and installing any that are now installable.

    Returns list of package names skipped due to conflict resolution.
    """
    if not cd_sequence:
        return []

    log("  Reconciliation: checking for packages skipped due to ordering")
    all_skipped = []

    for seq_idx, (cd_path, cd_name, is_target4) in enumerate(cd_sequence):
        # Skip Foundation CDs during reconciliation.  Foundation packages
        # are base versions superseded by overlays.  Re-selecting them
        # causes incompatibilities with already-installed overlay packages
        # (e.g. eoe.sw.base overlay vs compiler_eoe.sw.unix foundation).
        cd_basename = os.path.basename(cd_path).lower()
        if "foundation" in cd_basename:
            log(f"    [{seq_idx+1}/{len(cd_sequence)}] {cd_name}")
            log(f"      Skipping (foundation CD superseded by overlays)")
            continue

        log(f"    [{seq_idx+1}/{len(cd_sequence)}] {cd_name}")

        need_swap = not is_target4 and seq_idx > 0
        if not _mount_cd(session, cd_path, cd_name, is_target4, need_swap,
                         tolerant=True):
            log(f"      Skipping (mount failed)")
            continue

        if not _from_dist(session, cd_name):
            log(f"      Skipping (distribution unreadable)")
            continue

        session.send("keep *\r")
        _wait_for_inst_prompt(session, timeout=5, max_wait=15)
        session.send("install standard\r")
        _wait_for_inst_prompt(session, timeout=5, max_wait=30)
        session.send("install prereqs\r")
        _wait_for_inst_prompt(session, timeout=5, max_wait=30)

        resolved, skipped = _resolve_conflicts(session)
        if not resolved:
            log(f"      WARNING: could not resolve all conflicts")
        if skipped:
            log(f"      Skipped {len(skipped)} packages due to conflicts")
            all_skipped.extend(skipped)

        _wait_for_inst_prompt(session, timeout=5, max_wait=15)

        session.send("go\r")
        log(f"      Running go...")

        _wait_for_cd_install(session, cd_name)

    log("  Reconciliation complete")
    return all_skipped


def _install_from_combined(session, version_cfg, conflict_mode="auto",
                           conflict_resolutions=None,
                           install_level="standard", inst_debug=False):
    """Install IRIX packages from a single combined distribution image.

    The combined image contains dist files in one EFS filesystem,
    attached as SCSI target 2. Supports two layouts:

    - Single-dist: all files in /mnt/dist (legacy dedup layout)
    - Per-CD: each CD's files in /mnt/<cd_name>/ subdirectories.
      inst opens each subdirectory as a separate distribution.

    Args:
        conflict_mode: How to handle conflicts:
            "auto" — blindly resolve with _resolve_conflicts (legacy)
            "collect" — collect all conflicts, save to JSON, save snapshot,
                        and return without installing. Returns the parsed
                        conflicts list instead of skipped packages.
            "apply" — apply resolutions from conflict_resolutions dict,
                      then proceed with install.
        conflict_resolutions: Dict with resolution decisions (for "apply"
            mode). See _apply_conflict_resolutions() for format.

    Returns:
        - conflict_mode="auto": list of skipped package names
        - conflict_mode="collect": list of parsed conflict dicts
        - conflict_mode="apply": list of skipped package names
    """
    log("  Installing from combined distribution image (single-pass)")

    # Shell out to mount the combined image (SCSI target 2, partition 7)
    _wait_for_inst_prompt(session, timeout=5, max_wait=15)
    session.send("sh\r")
    expect(session, r"#", timeout=5, max_wait=10, label="shell prompt")

    session.send("mkdir -p /mnt 2>/dev/null; true\r")
    session.wait_for(r"#", timeout=5, max_wait=10)

    session.send("mount -r /dev/dsk/dks0d2s7 /mnt\r")
    result = session.wait_for(r"#", timeout=10, max_wait=60)
    if not result.matched:
        log("  WARNING: mount of combined image timed out")
        session.send("\x03")  # Ctrl-C
        session.wait_for(r"Inst>|Interrupt>", timeout=3, max_wait=15)
        session.send("exit\r")
        _wait_for_inst_prompt(session, timeout=5, max_wait=15)
        log("  Falling back to CD-by-CD installation")
        return _install_cd_by_cd(session, version_cfg, inst_debug=inst_debug)

    # Detect layout: old (single /mnt/dist) vs new (per-CD subdirectories).
    # Use `test -d; echo $?` to avoid false detection from command echo —
    # the serial console echoes the full command text, so markers like
    # "SINGLE_DIST" would appear in the echo regardless of the result.
    session.send("test -d /mnt/dist; echo LAYOUT_$?\r")
    result = session.wait_for(r"#", timeout=5, max_wait=10)

    if "LAYOUT_0" in result.output:
        # Old format — single /mnt/dist directory
        log("  Detected single-dist layout")
        session.send("ls /mnt/dist | wc -l\r")
        result = session.wait_for(r"#", timeout=5, max_wait=10)
        log(f"  Mount verified: {result.output.strip()}")

        session.send("exit\r")
        expect(session, r"Inst>", timeout=5, max_wait=10,
               label="Inst> after mount")

        log("  Setting /mnt/dist as distribution source")
        if not _from_dist_path(session, "/mnt/dist"):
            log("  WARNING: combined distribution unreadable, falling back")
            return _install_cd_by_cd(session, version_cfg, inst_debug=inst_debug)
    else:
        # New format — per-CD subdirectories
        log("  Detected per-CD layout")
        session.send("ls /mnt\r")
        result = session.wait_for(r"#", timeout=5, max_wait=10)

        # Parse directory names from ls output (skip command echo and prompt)
        lines = result.output.strip().splitlines()
        dirs = []
        for line in lines:
            for token in line.split():
                token = token.strip()
                if token and not token.startswith('.') \
                   and token not in ('lost+found', 'ls', '/mnt', '#') \
                   and not token.startswith('/'):
                    dirs.append(token)

        # Sort directories by priority: foundation and overlay CDs first
        # (needed for base OS), then dev tool CDs (the reason for this
        # install), then everything else.  This ensures the most important
        # distributions get opened first if inst has a simultaneous limit.
        def _cd_priority(name):
            n = name.lower()
            if 'foundation' in n:
                return (0, name)
            if 'overlay' in n or 'install-tools' in n:
                return (1, name)
            if 'mipspro' in n:
                return (2, name)
            if 'dev' in n:
                return (3, name)
            if 'prodev' in n:
                return (4, name)
            # Low priority — applications and NFS are often redundant
            return (5, name)

        dirs.sort(key=_cd_priority)

        log(f"  Found {len(dirs)} CD directories (priority order):")
        for d in dirs:
            log(f"    {d}")

        # Classify dirs into four installation passes:
        #   Pass 1 — Foundation: base OS (motif NOT in Foundation CDs)
        #   Pass 2 — Overlays (6.5.5): both Overlay motif AND Overlay 4Dwm/
        #            desktop_eoe/sysadmdesktop are in the same dists, so motif
        #            prereq is satisfied within the pass — no "from additional
        #            distribution" conflict possible.
        #   Pass 3 — Apps + Demos + NFS + ProDev: hardware-specific packages
        #            (O2/OCTANE/6.2-era) cause many conflicts, but desktop
        #            packages are already installed (committed) so they cannot
        #            be deselected by cascade.
        #   Pass 4 — MIPSpro + Dev tools: compiler and dev libraries.
        #
        # Passes are identified by dir name patterns.  6.5.5-specific dirs
        # are the Overlay CDs; Foundation dirs say "Foundation"; MIPSpro dirs
        # say "mipspro", "development_libraries", or "compiler_execution".
        # Everything else is Apps/Demos/NFS/ProDev.
        def _dir_pass(name):
            n = name.lower()
            # Pass 1: Foundation OS + Foundation-era Applications (June 1998)
            if 'foundation' in n or 'june' in n:
                return 1
            # Pass 4: Compiler/dev toolchain (no desktop deps)
            if any(x in n for x in ['mipspro', 'development_libraries',
                                      'compiler_execution']):
                return 4
            # Pass 2: 6.5.5 Overlay CDs + August 1999 Applications overlay
            # Keep Overlay motif (motif_eoe_655m) and Overlay 4Dwm (4Dwm_655m)
            # together so their inter-dependency resolves in one go.
            if '6.5.5' in n or 'august' in n:
                return 2
            return 3  # demos, prodev, nfs, other older CDs

        pass1_dirs = [d for d in dirs if _dir_pass(d) == 1]
        pass2_dirs = [d for d in dirs if _dir_pass(d) == 2]
        pass3_dirs = [d for d in dirs if _dir_pass(d) == 3]
        pass4_dirs = [d for d in dirs if _dir_pass(d) == 4]

        log(f"  Four-pass install: {len(pass1_dirs)} Foundation+June98Apps, "
            f"{len(pass2_dirs)} Overlays+Aug99Apps, {len(pass3_dirs)} Demos+NFS, "
            f"{len(pass4_dirs)} MIPSpro+Dev")

        if conflict_mode == "auto":
            session.send("exit\r")
            expect(session, r"Inst>", timeout=5, max_wait=10,
                   label="Inst> after mount")

            def _open_dir_group(group_dirs, first_in_session):
                """Open a list of dirs. first_in_session=True → first uses `from`."""
                opened = 0
                is_first = first_in_session
                for dir_name in group_dirs:
                    path = f"/mnt/{dir_name}"
                    if is_first:
                        log(f"  Setting {path} as distribution source")
                        if not _from_dist_path(session, path):
                            log(f"    WARNING: {path} unreadable, skipping")
                            continue
                        is_first = False
                        opened += 1
                    else:
                        time.sleep(1)
                        session.collect(duration=1)
                        result = session.wait_for(
                            r"Inst>|Install software from",
                            timeout=5, max_wait=60
                        )
                        if result.matched and \
                                "Install software from" in result.output:
                            session.wait_for(r"\]", timeout=5, max_wait=10)
                            session.send("done\r")
                            _wait_for_inst_prompt(session, timeout=5,
                                                  max_wait=60)
                        log(f"  Opening additional distribution: {path}")
                        if not _open_dist_path(session, path):
                            log(f"    WARNING: {path} unreadable/empty, skipping")
                        else:
                            opened += 1
                return opened

            def _run_pass(pass_dirs, pass_num):
                """Open dirs for one pass, select, resolve conflicts, run go.

                All passes run within the SAME inst session.  inst's
                in-memory installation history (what was committed by each
                `go`) persists across close/open operations, so a later
                pass sees previously installed packages as "installed" and
                won't raise "missing prerequisites" conflicts for them.

                After each `go`, the distributions for this pass are closed
                individually so the next pass starts with only its own
                sources open.  Do NOT quit/restart inst between passes —
                that loses the in-memory history and causes the next pass to
                see Foundation packages as uninstalled.
                """
                if not pass_dirs:
                    log(f"  Pass {pass_num}: no directories, skipping")
                    return []

                # Pass 1 uses `from` for its first dir (sets the initial
                # primary distribution and runs the startup script).
                # Passes 2+ use `open` for ALL dirs — `from` does a fresh
                # start that closes all currently-open distributions,
                # which would make inst "forget" Foundation packages for
                # prerequisite checking.  Using `open` keeps Foundation
                # dists open alongside the new Overlay/App/MIPSpro dists.
                n_opened = _open_dir_group(pass_dirs,
                                           first_in_session=(pass_num == 1))
                log(f"  Pass {pass_num}: opened {n_opened} distributions")
                if n_opened == 0:
                    log(f"  Pass {pass_num}: WARNING no distributions "
                        f"opened, skipping")
                    return []

                # Clean Inst> before selection
                session.send("\r")
                _wait_for_inst_prompt(session, timeout=5, max_wait=60)

                # Deselect everything before our explicit install.  The
                # `from` startup script for Overlay CDs pre-selects the
                # feature-stream packages; `keep *` clears that slate so
                # our `install *` (or install default) is the only
                # selection source.
                session.send("keep *\r")
                _wait_for_inst_prompt(session, timeout=10, max_wait=30)

                # Enable verbose output so exitops are visible in logs.
                _set_inst_verbosity(session, debug=inst_debug)

                # Explicitly install essential packages in Pass 1 before
                # the general `install *`.  This guarantees e.g. motif_eoe
                # (Foundation version) is selected and committed in Pass 1
                # so that Pass 2's overlay packages find their prerequisite
                # satisfied without triggering the "also install from
                # additional distribution" conflict.
                if pass_num == 1:
                    for pkg in version_cfg.get("essential_packages", []):
                        log(f"  Pass 1: explicitly installing essential: {pkg}")
                        session.send(f"install {pkg}\r")
                        _wait_for_inst_prompt(session, timeout=10, max_wait=60)

                # Package selection
                if install_level == "all":
                    session.send("install *\r")
                elif install_level == "default":
                    session.send("install default\r")
                else:
                    session.send("install standard\r")
                _wait_for_inst_prompt(session, timeout=10, max_wait=120)
                session.send("install prereqs\r")
                _wait_for_inst_prompt(session, timeout=10, max_wait=120)

                # Conflict resolution — never use also_install (b options).
                # Pass 1 opens Foundation dists only; any `b` option asking
                # to fetch from a dist not yet open would trigger an
                # "Install software from:" deadlock.  Pass 2+ don't need
                # `b` either: Foundation is already committed so overlay
                # prereqs are satisfied and no `b` choices arise.
                resolved, pass_skipped = _resolve_conflicts(
                    session, allow_also_install=False)
                if not resolved:
                    log(f"  Pass {pass_num}: WARNING could not resolve all "
                        f"conflicts")
                if pass_skipped:
                    log(f"  Pass {pass_num}: skipped {len(pass_skipped)} "
                        f"packages")

                # Critical package check before Pass 2's go.
                # Pass 1 installs Foundation; the same inst session then
                # knows Foundation packages are installed.  Pass 2 opens
                # ONLY Overlay dists.  Since inst sees Foundation as
                # installed (in-memory history), the Overlay motif
                # prerequisite is satisfied and 4Dwm / desktop_eoe /
                # sysadmdesktop should be selected.
                if pass_num == 2:
                    _critical = version_cfg.get("critical_packages", [])
                    if _critical:
                        _unselected = []
                        for pkg in _critical:
                            _wait_for_inst_prompt(session, timeout=3,
                                                  max_wait=10)
                            session.send(f"list {pkg}\r")
                            result = session.wait_for(r"Inst>", timeout=5,
                                                      max_wait=15)
                            if not re.search(
                                    rf'(?:^[iI][ 	]|^[ 	]+S[ 	]).*{re.escape(pkg)}',
                                    result.output, re.MULTILINE):
                                _unselected.append(pkg)
                                log(f"  WARNING: critical package not "
                                    f"selected: {pkg}")
                        if _unselected:
                            fail(
                                f"Critical packages not selected for "
                                f"installation in Pass 2 (Overlays) — "
                                f"Foundation in-memory state should have "
                                f"satisfied prerequisites: "
                                f"{', '.join(sorted(_unselected))}. "
                                f"Re-run with conflict_mode='collect' to "
                                f"inspect conflicts."
                            )
                        log(f"  All {len(_critical)} critical packages "
                            f"selected")

                # Run go
                _wait_for_inst_prompt(session, timeout=5, max_wait=15)
                session.send("go\r")
                log(f"  Pass {pass_num}: running go...")
                _wait_for_cd_install(session, f"pass-{pass_num}")
                log(f"  Pass {pass_num} complete")

                # Snapshot after each pass so we can resume if a later
                # pass fails.  Non-fatal — warn and continue.
                try:
                    snap_name = f"pass{pass_num}_complete"
                    session.save_snapshot(snap_name)
                    log(f"  Saved snapshot: {snap_name}")
                except Exception as _snap_err:
                    log(f"  WARNING: could not save snapshot: {_snap_err}")

                # Do NOT close distributions between passes.  inst tracks
                # prerequisite satisfaction through *open* distributions —
                # when a dist is closed, inst stops using it to satisfy
                # prerequisites, even for packages already installed from
                # it.  Closing Foundation after Pass 1 causes Pass 2 to
                # report "missing prerequisite: motif_eoe.sw.eoe (Foundation
                # version)" and deselect 4Dwm, desktop_eoe, sysadmdesktop.
                # Leaving Foundation open lets inst see Foundation motif as
                # both installed AND available, so Overlay motif's prereq is
                # satisfied and the desktop packages install correctly.
                return pass_skipped

            # Two-pass install:
            #   Pass 1 — Foundation (+ June 1998 Apps): commits Foundation
            #            motif_eoe so overlay packages' prereq is satisfied.
            #   Pass 2 — Everything else: Overlays, Aug 1999 Apps, MIPSpro,
            #            Demos.  Foundation dists remain OPEN (we never used
            #            `from` for Pass 2 dirs, only `open`), so inst can
            #            see Foundation packages as both installed and
            #            available, satisfying overlay motif's prereq.
            non_foundation_dirs = pass2_dirs + pass3_dirs + pass4_dirs
            log(f"  Two-pass install: {len(pass1_dirs)} Foundation+June98, "
                f"{len(non_foundation_dirs)} Overlays+Apps+Dev")
            all_skipped = []
            all_skipped += _run_pass(pass1_dirs, 1)
            all_skipped += _run_pass(non_foundation_dirs, 2)
            log("  Combined installation complete")
            return all_skipped

        # collect/apply modes: use existing single-pass dir-opening approach.
        # Foundation + Overlay dists open simultaneously so conflict
        # collection/resolution sees the full picture at once.
        session.send("exit\r")
        expect(session, r"Inst>", timeout=5, max_wait=10,
               label="Inst> after mount")

        # Open distributions with synchronization between each.
        # inst has a limit of ~5 simultaneous distributions; beyond that
        # it shows an interactive "Install software from:" menu.  We
        # handle that in _dist_command, but to minimize problems we also
        # sync (drain + wait for clean Inst>) between each open.
        first = True
        opened = 0
        for dir_name in dirs:
            path = f"/mnt/{dir_name}"
            if first:
                log(f"  Setting {path} as distribution source")
                if not _from_dist_path(session, path):
                    log(f"    WARNING: {path} unreadable, skipping")
                    continue
                first = False
                opened += 1
            else:
                # Synchronize: drain any pending output and wait for
                # a clean Inst> before sending the next open command.
                # After overlay CDs, inst may show an "Install software
                # from:" interactive menu instead of Inst>.  We must
                # handle that by sending "done" to dismiss the menu,
                # NOT a bare \r which would accept the default and
                # re-open the previous distribution.
                time.sleep(1)
                session.collect(duration=1)
                # Wait for either Inst> or the "Install software from" menu
                result = session.wait_for(
                    r"Inst>|Install software from",
                    timeout=5, max_wait=60
                )
                if result.matched and "Install software from" in result.output:
                    # Dismiss the interactive distribution menu
                    session.wait_for(r"\]", timeout=5, max_wait=10)
                    session.send("done\r")
                    _wait_for_inst_prompt(session, timeout=5, max_wait=60)

                log(f"  Opening additional distribution: {path}")
                if not _open_dist_path(session, path):
                    log(f"    WARNING: {path} unreadable/empty, skipping")
                else:
                    opened += 1

        log(f"  Opened {opened} distributions")
        if first:
            # No distributions loaded at all
            log("  WARNING: no distributions loaded, falling back")
            return _install_cd_by_cd(session, version_cfg, inst_debug=inst_debug)

    # Ensure we're at a clean Inst> before selecting packages.
    # Send a bare Enter to elicit a fresh Inst> prompt, then wait with a
    # generous idle timeout (5s) so all product-description bursts settle.
    session.send("\r")
    _wait_for_inst_prompt(session, timeout=5, max_wait=60)

    # Enable verbose installer output so exitops and file operations are
    # visible in the transcript (helps debug issues like the empty xdm-config
    # that sysadmdesktop exitop should have written).
    _set_inst_verbosity(session, debug=inst_debug)

    # Select packages
    session.send("keep *\r")
    _wait_for_inst_prompt(session, timeout=10, max_wait=30)
    if install_level == "all":
        log("  Selecting ALL subsystems (install *)")
        session.send("install *\r")
    elif install_level == "default":
        log("  Selecting ALL packages (install default)")
        session.send("install default\r")
    else:
        session.send("install standard\r")
    _wait_for_inst_prompt(session, timeout=10, max_wait=120)
    session.send("install prereqs\r")
    _wait_for_inst_prompt(session, timeout=10, max_wait=120)

    # --- Pre-compute keep set from host-side dist metadata ---
    # If version_cfg["_dist_dirs"] lists host-side extracted dist directories,
    # analyse them with irix_dist_parser to find hw-excluded and version-range
    # conflicts and send `keep <sub>` for each one *before* `go` runs.
    # This eliminates conflicts proactively; _resolve_conflicts() below remains
    # as a safety net for anything the static analysis misses.
    _pre_dist_dirs = version_cfg.get("_dist_dirs", [])
    if _pre_dist_dirs:
        try:
            from pyirix.dist.parser import Corpus, HardwareConfig as _HWCfg
            _corpus = Corpus()
            _corpus.load_dirs([Path(d) for d in _pre_dist_dirs])
            _hw = _HWCfg.for_target(version_cfg.get("machine", "indy"))
            _report = _corpus.conflicts(_hw)
            if _report.keep_set:
                log(f"  Pre-computed {len(_report.keep_set)} subsystems to keep "
                    f"(hw-excluded or unresolvable prereqs)")
                for _sub in sorted(_report.keep_set):
                    session.send(f"keep {_sub}\r")
                    _wait_for_inst_prompt(session, timeout=3, max_wait=10)
        except Exception as _e:
            log(f"  NOTE: dist pre-analysis skipped: {_e}")

    # Handle conflicts based on mode
    if conflict_mode == "collect":
        # Collect all conflicts, save to JSON, save snapshot, return
        log("  Collecting conflicts (collect mode)...")
        raw_text = _collect_all_conflicts(session)
        conflicts = parse_conflicts(raw_text)
        log(f"  Found {len(conflicts)} conflicts")

        # Summarize by type
        by_type = {}
        for c in conflicts:
            by_type.setdefault(c.get("type", "unknown"), []).append(c)
        for ctype, clist in sorted(by_type.items()):
            log(f"    {ctype}: {len(clist)}")

        # Save conflicts and raw text to instance dir or workspace
        conflict_dir = version_cfg.get("_conflict_dir",
                                       str(PROJECT_ROOT))
        conflicts_path = os.path.join(conflict_dir, "conflicts.json")
        save_conflicts(conflicts, conflicts_path)

        raw_path = os.path.join(conflict_dir, "conflicts_raw.txt")
        with open(raw_path, "w") as f:
            f.write(raw_text)
        log(f"  Saved raw conflict text to {raw_path}")

        # Save snapshot so we can resume
        try:
            session.save_snapshot("pre_conflict_resolution")
            log("  Saved snapshot: pre_conflict_resolution")
        except Exception as e:
            log(f"  WARNING: could not save snapshot: {e}")

        return conflicts  # Return conflicts instead of skipped list

    elif conflict_mode == "apply":
        if not conflict_resolutions:
            log("  WARNING: apply mode but no resolutions provided")
            return []

        log(f"  Applying conflict resolutions...")
        resolved, remaining = _apply_conflict_resolutions(
            session, conflict_resolutions)
        if not resolved:
            remaining_conflicts = parse_conflicts(remaining)
            log(f"  WARNING: {len(remaining_conflicts)} conflicts remain "
                f"after applying resolutions")

    else:
        # Legacy auto mode — allow also_install during main install because
        # all distributions are open.  Missing prereqs (e.g. desktop_eoe.sw.envm
        # needed by sysadmdesktop.sw.base) get resolved via option-b rather than
        # silently dropped.  The quit phase explicitly passes allow_also_install=False.
        resolved, skipped = _resolve_conflicts(session, allow_also_install=True)
        if not resolved:
            log("  WARNING: could not resolve all conflicts")
        if skipped:
            log(f"  Skipped {len(skipped)} packages due to conflicts")

    # Pre-install critical package check — before go, so we fail fast.
    # Ask inst directly what's selected: "i" prefix = selected for install.
    # This catches packages that conflict resolution silently deselected.
    _critical = version_cfg.get("critical_packages", [])
    if _critical and conflict_mode != "collect":
        _unselected = []
        for pkg in _critical:
            _wait_for_inst_prompt(session, timeout=3, max_wait=10)
            session.send(f"list {pkg}\r")
            result = session.wait_for(r"Inst>", timeout=5, max_wait=15)
            # inst list output: "i N  pkg.name [flags]  size  desc"
            # Selection marker is at col 0; status code (N/U/S/D) follows.
            if not re.search(rf'(?:^[iI][ 	]|^[ 	]+S[ 	]).*{re.escape(pkg)}',
                             result.output, re.MULTILINE):
                _unselected.append(pkg)
                log(f"  WARNING: critical package not selected: {pkg}")
        if _unselected:
            # Conflict resolution may have dropped both overlay AND foundation
            # versions of a subsystem together.  Explicitly re-install each
            # missing package: if the overlay was deselected, inst should fall
            # back to the Foundation version (which has no motif prereq).
            # Then resolve any new conflicts (no also_install — avoid cascade).
            log(f"  Re-selecting {len(_unselected)} critical packages "
                f"(Foundation fallback)...")
            for pkg in _unselected:
                _wait_for_inst_prompt(session, timeout=3, max_wait=10)
                session.send(f"install {pkg}\r")
            _wait_for_inst_prompt(session, timeout=5, max_wait=15)
            _resolve_conflicts(session, allow_also_install=False)

            # Re-check after the forced re-select.
            still_unselected = []
            for pkg in _unselected:
                _wait_for_inst_prompt(session, timeout=3, max_wait=10)
                session.send(f"list {pkg}\r")
                result = session.wait_for(r"Inst>", timeout=5, max_wait=15)
                if not re.search(rf'(?:^[iI][ 	]|^[ 	]+S[ 	]).*{re.escape(pkg)}',
                                 result.output, re.MULTILINE):
                    still_unselected.append(pkg)
                    log(f"  WARNING: critical package still unselected: {pkg}")
            if still_unselected:
                fail(
                    f"Critical packages not selected for installation after "
                    f"conflict resolution and re-install attempt: "
                    f"{', '.join(sorted(still_unselected))}. "
                    f"Re-run with conflict_mode='collect' to inspect conflicts."
                )
            log(f"  All {len(_critical)} critical packages selected "
                f"after re-select — proceeding")
        else:
            log(f"  All {len(_critical)} critical packages selected — proceeding")

    # Install
    _wait_for_inst_prompt(session, timeout=5, max_wait=15)
    session.send("go\r")
    log("  Running go (single-pass install)...")

    _wait_for_cd_install(session, "combined-dist")
    log("  Combined installation complete")

    return [] if conflict_mode == "apply" else skipped


def _dist_command(session, command, dist_path):
    """Send a from/open command to inst and handle prompts.

    Args:
        session: QEMUSession
        command: "from" (set active distribution) or "open" (add source)
        dist_path: Path to distribution directory

    Returns True if distribution was loaded successfully.

    Note: "Skipping product X (already open)" messages are NORMAL during
    `open` commands — they mean the product was already loaded from a
    previously opened distribution. Only "Distribution is empty" and
    "Cannot read" indicate actual errors.
    """
    session.send(f"{command} {dist_path}\r")
    dist_empty = False
    path_resent = False
    for _ in range(120):
        result = session.wait_for(
            r"Inst>|more\?|Please enter|startup script|"
            r"Distribution is empty|Cannot read|"
            r"Install software from|enter a choice|"
            r"switch distributions|already been opened|"
            r"yes.*or.*no|answer.*yes|"
            r"Done\.",
            timeout=10, max_wait=300
        )
        if not result.matched:
            break
        if "Distribution is empty" in result.output or \
           "Cannot read" in result.output:
            if not dist_empty:
                log(f"    WARNING: Distribution empty or unreadable")
            dist_empty = True
            continue
        if "Done." in result.output and "Inst>" not in result.output:
            # "Reading product descriptions .. 100% Done." — wait for Inst>
            continue
        if "more?" in result.output:
            session.send("n\r")
            continue
        if "switch distributions" in result.output:
            session.send("y\r")
            continue
        if "already been opened" in result.output or \
           "answer" in result.output and "yes" in result.output:
            session.send("yes\r")
            continue
        if "yes" in result.output and "no" in result.output:
            session.send("yes\r")
            continue
        if "Install software from" in result.output:
            # inst hit the simultaneous distribution limit and is showing
            # an interactive menu: "Previous installation sites: ..."
            # followed by "Install software from: [default_path]".
            # We must wait for the closing ']' of the default path before
            # sending our path, otherwise inst receives our \r before
            # finishing the prompt and accepts the default instead.
            bracket = session.wait_for(r"\]", timeout=5, max_wait=30)
            time.sleep(0.3)  # let inst settle after rendering prompt
            if not path_resent:
                session.send(f"{dist_path}\r")
                path_resent = True
            else:
                # Already sent the path — send "done" to break out
                session.send("done\r")
            continue
        if "Please enter" in result.output or \
           "startup script" in result.output or \
           "enter a choice" in result.output:
            session.send("2\r")
            continue
        break  # Got Inst>
    return not dist_empty


def _from_dist_path(session, dist_path):
    """Set an arbitrary dist path as the active distribution source.

    Returns True if distribution was set successfully.
    """
    return _dist_command(session, "from", dist_path)


def _open_dist_path(session, dist_path):
    """Open an additional distribution source in inst.

    Returns True if distribution was loaded successfully.
    """
    return _dist_command(session, "open", dist_path)


def _install_cd_by_cd(session, version_cfg, inst_debug=False):
    """Install IRIX 6.5 packages one CD at a time with reconciliation.

    For each CD: mount it, set as active distribution with `from`,
    select standard packages, resolve conflicts, and `go`. Per-CD `go`
    is required because QEMU's SCSI media change causes inst's
    verify_volume() to hang during a running `go`.

    After all CDs are installed, a reconciliation pass re-checks each CD
    to install any packages that were skipped due to cross-CD prerequisite
    ordering (e.g., Foundation 1's x_eoe.sw.xdps needs Foundation 2's
    dps_eoe.sw.dpsfonts — on the first pass, F2 isn't installed yet, so
    xdps gets deselected; the reconciliation pass picks it up).

    Returns list of all skipped package names from conflict resolution.
    """
    cd_sequence = _build_cd_sequence(version_cfg)
    total = len(cd_sequence)
    log(f"  CD sequence ({total} CDs):")
    for i, (_, name, t4) in enumerate(cd_sequence):
        target = "target 4" if t4 else "target 5"
        log(f"    {i+1}. {name} ({target})")

    # Install packages one CD at a time
    skipped = _install_per_cd(session, cd_sequence, inst_debug=inst_debug)

    # Reconciliation: re-check all CDs for packages skipped due to ordering
    recon_skipped = _reconciliation_pass(session, cd_sequence)
    skipped.extend(recon_skipped)

    if skipped:
        log(f"  Total skipped packages: {len(skipped)}")
    return skipped


def _wait_for_cd_install(session, cd_name):
    """Wait for a single-CD `go` to complete.

    Handles conflicts (auto-resolves by deselecting), pagers, and progress.
    Does NOT handle mid-install CD swaps — each `go` should only need
    the currently mounted CD. If inst asks for a different CD, we log a
    warning and the `go` is considered done (packages from that CD will
    be installed when we get to it).
    """
    # The `go` command may hit conflicts, show pagers, or start installing.
    for attempt in range(5):
        for _ in range(20):
            result = session.wait_for(
                r"Inst>|Installations.*successful|Installing|more\?|"
                r"Pre-installation|Conflicts must be resolved|"
                r"no changes|N selections|Nothing to install|Nothing selected|"
                r"Insert.*CD|insert.*CD|Please mount|Cannot read|"
                r"really want to quit|Please answer|"
                r"enter a choice|Interrupt>",
                timeout=30, max_wait=1800
            )
            if not result.matched:
                fail(f"Installation stalled on {cd_name}: {result.bail_reason}\n"
                     f"Last output:\n{result.output[-500:]}")
            if "Interrupt>" in result.output:
                # Post-install script failed — send "3" (continue) to skip
                # the failing script and keep installing remaining packages.
                log(f"    WARNING: post-install script error, continuing")
                session.send("3\r")
                continue
            if "really want to quit" in result.output:
                session.send("no\r")
                continue
            if "Please answer" in result.output and \
               "enter a choice" not in result.output:
                session.send("no\r")
                continue
            if "enter a choice" in result.output:
                # Conflict stream prompt (maintenance vs feature, or
                # "address conflicts now" vs "postpone"). Choose option 2
                # to postpone — skipped packages caught in reconciliation.
                session.send("2\r")
                continue
            if "more?" in result.output and "Installing" not in result.output:
                session.send("n\r")
                continue
            break

        if "Conflicts must be resolved" in result.output:
            # Auto-resolve by deselecting conflicting packages
            log(f"    Resolving conflicts...")
            # First, dismiss any remaining pager output from the conflict list
            _wait_for_inst_prompt(session, timeout=5, max_wait=30)
            # Resolve all conflicts (skipped packages already tracked by caller)
            _resolve_conflicts(session)
            _wait_for_inst_prompt(session, timeout=5, max_wait=15)
            session.send("go\r")
            log(f"    Retrying go after conflict resolution...")
            continue

        if "no changes" in result.output or "N selections" in result.output or \
           "Nothing to install" in result.output or \
           "Nothing selected" in result.output:
            # Nothing to install from this CD (already up to date)
            log(f"    Nothing to install")
            if "Inst>" not in result.output:
                session.wait_for(r"Inst>", timeout=5, max_wait=30)
            return

        if "Insert" in result.output or "insert" in result.output or \
           "Please mount" in result.output or "Cannot read" in result.output:
            # inst wants a different CD — this shouldn't happen in CD-by-CD
            # mode. Send Ctrl-C to abort, then use _wait_for_inst_prompt
            # to handle the Interrupt> menu (sends "1" to stop).
            log(f"    WARNING: inst requested a different CD (skipping)")
            session.send("\x03")  # Ctrl-C
            session.wait_for(r"Inst>|Interrupt>", timeout=3, max_wait=15)
            _wait_for_inst_prompt(session, timeout=10, max_wait=30)
            return

        # "Installing" or "Installations successful" — good
        break

    # Wait for installation to complete.
    # Exit-commands run post-install scripts that can be silent for minutes —
    # especially for large installs where motif/X11/4Dwm exit-commands run
    # make, lp, and license-manager setup.  600s idle timeout prevents a
    # spurious fail() during those quiet stretches.
    if "Installations" not in result.output and "Inst>" not in result.output:
        while True:
            result = session.wait_for(
                r"Inst>|Installations.*successful|Installing|"
                r"exit-commands|Checking dependencies|Calculating sizes|"
                r"Insert.*CD|insert.*CD|Please mount|Cannot read|"
                r"Interrupt>",
                timeout=600, max_wait=7200
            )
            if not result.matched:
                fail(f"Installation stalled on {cd_name}: {result.bail_reason}\n"
                     f"Last output:\n{result.output[-500:]}")
            if "Interrupt>" in result.output:
                log(f"    WARNING: post-install script error, continuing")
                session.send("3\r")
                continue
            if "Insert" in result.output or "insert" in result.output or \
               "Please mount" in result.output or "Cannot read" in result.output:
                log(f"    WARNING: inst requested a different CD (skipping)")
                session.send("\x03")  # Ctrl-C
                session.wait_for(r"Inst>|Interrupt>", timeout=3, max_wait=15)
                _wait_for_inst_prompt(session, timeout=10, max_wait=30)
                return
            if "Installing" in result.output and \
               "Installations" not in result.output:
                continue
            if ("exit-commands" in result.output or
                    "Checking dependencies" in result.output or
                    "Calculating sizes" in result.output) and \
                    "Installations" not in result.output and \
                    "Inst>" not in result.output:
                # Progress output from post-install phase — keep waiting.
                continue
            break

    if "Inst>" not in result.output:
        session.wait_for(r"Inst>", timeout=5, max_wait=30)


def _wait_for_install_complete(session, version_cfg=None):
    """Handle the `go` command for single-CD installs (IRIX 5.3, 6.2)."""
    for attempt in range(3):
        for _ in range(20):
            result = session.wait_for(
                r"Inst>|Installations.*successful|Installing|more\?|"
                r"Pre-installation|Conflicts must be resolved",
                timeout=30, max_wait=1800
            )
            if not result.matched:
                fail(f"Installation stalled: {result.bail_reason}\n"
                     f"Last output:\n{result.output[-500:]}")
            if "more?" in result.output and "Installing" not in result.output:
                session.send("n\r")
                continue
            break

        if "Conflicts must be resolved" in result.output:
            log("  Resolving conflicts (deselecting unavailable packages)")
            _wait_for_inst_prompt(session, timeout=5, max_wait=30)
            _resolve_conflicts(session)  # skipped tracking not needed for single-CD
            session.send("go\r")
            log("  Retrying go after conflict resolution...")
            continue

        break

    if "Installations" not in result.output and "Inst>" not in result.output:
        while True:
            result = session.wait_for(
                r"Inst>|Installations.*successful|Installing",
                timeout=120, max_wait=3600
            )
            if not result.matched:
                fail(f"Installation stalled: {result.bail_reason}\n"
                     f"Last output:\n{result.output[-500:]}")
            if "Installing" in result.output and \
               "Installations" not in result.output:
                continue
            break

    log("  Installation successful")

    if "Inst>" not in result.output:
        session.wait_for(r"Inst>", timeout=5, max_wait=30)


# ── Phase 4: Quit installer and build kernel ────────────────────────────────

def phase_quit_and_build(session, version_cfg, instance=None):
    """Quit the installer, trigger autoconfig/kernel build via restart.

    The actual kernel link (lboot) runs inside the miniroot's restart
    sequence — after the user sends "yes" to the "Ready to restart?"
    prompt, not during inst's quit.  So we must send "yes" and wait
    for the kernel to boot (confirming /unix was written to disk).
    We don't wait for the login prompt because SCSI CD-ROM errors
    often stall or slow the boot; Phase 5 does a clean cold boot
    without CDs instead.
    """
    log("Phase 4: Building kernel and restarting")

    session.send("quit\r")

    # Handle quit confirmation and post-quit prompts (pager, conflicts, etc.)
    # Exitops (ELF inventory, rqs, kernel build) can take minutes — use long timeouts.
    #
    # Post-quit conflict strategy:
    #   When `quit` shows incompatible packages, the conflict text appears in
    #   the *pager output* before Inst> — not as a fresh `conflicts` reply.
    #   We accumulate pager chunks in _quit_buf so the full conflict listing
    #   is available when Inst> finally appears, then parse and resolve from
    #   that buffer.  A fresh `conflicts` command is the fallback.
    #
    #   Critically: do NOT send `go` after conflict resolution in the quit
    #   cycle.  `go` re-runs exitops which re-selects the same packages,
    #   creating an infinite loop (especially with `install *`).  After
    #   resolving/keeping, retry `quit` directly.
    _incompatible_pkgs_seen: set = set()  # packages known to cycle back
    _incompatible_round = 0
    _quit_buf = ""  # accumulates pager text from the current quit attempt
    for _ in range(100):  # generous limit; each round uses several iterations
        result = session.wait_for(
            r"really want to quit|Do you really|more\?|Ready to restart|Restart\?|Inst>|"
            r"ERROR: Conflicts must be resolved",
            timeout=5, max_wait=600
        )
        if not result.matched:
            break
        if "really" in result.output:
            pending = parse_conflicts(_quit_buf)
            if pending:
                log(f"  Conflicts remain ({len(pending)}) — resolving before accepting quit")
                _quit_buf = ""
                _resolve_conflicts(session, allow_also_install=False)
                session.send("quit\r")
            else:
                session.send("yes\r")
                _quit_buf = ""
            continue
        if "more?" in result.output and "Inst>" not in result.output:
            # Accumulate this pager page and advance.
            _quit_buf += result.output
            session.send(" ")
            continue
        if "ERROR: Conflicts" in result.output:
            # inst refused to quit because conflicts remain — resolve then
            # retry quit directly (no `go` — that re-runs exitops and
            # re-proposes the same packages).
            # Never use also_install here: no CDs are open in the quit phase,
            # so also_install → "done" gives inst nothing to work with and
            # cascades into more unresolvable conflicts. Always deselect.
            _quit_buf = ""
            _resolve_conflicts(session, allow_also_install=False)
            session.send("quit\r")
            continue
        if "Inst>" in result.output:
            # Accumulate the last pager page (may be blank + Inst>).
            _quit_buf += result.output

            # inst's quit flow: after showing its conflict summary it prints
            # Inst> and then IMMEDIATELY asks "Do you really want to quit?".
            # These arrive in separate serial chunks, so the outer wait_for
            # fires on Inst> before the y/n line arrives.  Peek briefly for
            # the confirmation prompt before doing anything else.
            peek = session.wait_for(
                r"really want to quit|Do you really|more\?|Ready to restart|Restart\?",
                timeout=2, max_wait=5
            )
            if peek.matched:
                if "really" in peek.output or "Do you really" in peek.output:
                    pending = parse_conflicts(_quit_buf)
                    if pending:
                        log(f"  Conflicts in quit pager ({len(pending)}) — resolving before quit")
                        _quit_buf = ""
                        _resolve_conflicts(session, allow_also_install=False)
                        session.send("quit\r")
                    else:
                        session.send("yes\r")
                        _quit_buf = ""
                    continue
                if "more?" in peek.output:
                    _quit_buf += peek.output
                    session.send(" ")
                    continue
                if "Ready" in peek.output or "Restart" in peek.output:
                    # Quit succeeded — break out to the restart handler.
                    result = peek
                    break

            _incompatible_round += 1

            # Parse conflicts from the accumulated quit pager output first.
            # A fresh `conflicts` command returns empty after quit processes
            # its conflict list, so the accumulated buffer is the only source.
            conflicts = parse_conflicts(_quit_buf)
            if not conflicts:
                # Fallback: ask inst directly (handles non-pager Inst> case).
                raw = _collect_all_conflicts(session)
                conflicts = parse_conflicts(raw)
            _quit_buf = ""  # reset for the next quit attempt

            if not conflicts:
                # No conflicts — quit cleanly.
                session.send("quit\r")
                continue

            log(f"  Post-install round {_incompatible_round}: "
                f"{len(conflicts)} incompatible package(s)")

            # Collect the subject package names that keep cycling back.
            subjects = {c.get("subject", "") for c in conflicts if c.get("subject")}
            new_cycles = subjects - _incompatible_pkgs_seen
            _incompatible_pkgs_seen |= subjects

            if new_cycles or _incompatible_round == 1:
                # First pass or new packages: resolve with 1a (do not install).
                # In the quit phase we are DONE installing — always deselect
                # (option a) rather than trying to pull in more packages from
                # CDs we don't have (option b = "also install from additional
                # distribution" would cascade into more unresolvable conflicts).
                _resolve_conflicts(session, default_option="1a",
                                   prefer_also_install=False)
            else:
                # Same packages keep cycling — use `keep` to deselect them.
                # `keep` overrides `install *`; `remove` does not.
                log(f"  Cycling packages — keeping (deselecting): "
                    f"{', '.join(sorted(_incompatible_pkgs_seen))}")
                for pkg in sorted(_incompatible_pkgs_seen):
                    _wait_for_inst_prompt(session, timeout=3, max_wait=10)
                    session.send(f"keep {pkg}\r")
                # Wait for Inst> from the last keep before quitting —
                # without this, the outer loop sees that Inst>, calls
                # _collect_all_conflicts (sends 'conflicts\r'), and inst
                # interprets it as a y/n answer to the quit confirmation.
                _wait_for_inst_prompt(session, timeout=3, max_wait=15)

            # Retry quit directly — no `go`, which would re-run exitops and
            # re-select the same packages, creating an infinite loop.
            session.send("quit\r")
            continue
        # "Ready to restart" or "Restart?" matched
        break

    if not result.matched or ("Ready" not in result.output and "Restart" not in result.output):
        # Didn't get restart prompt yet — wait for kernel build
        result = expect(session, r"Ready to restart|Restart\?",
                        timeout=30, max_wait=600,
                        label="kernel build / restart prompt")

    # Send "yes" to restart — this triggers lboot which builds /unix
    session.send("yes\r")
    log("  Restarting (triggers kernel build)")

    # Wait for kernel boot confirmation — "IRIX Release" or "Starting up"
    # confirms /unix was built and written to disk.  Don't wait for login
    # because SCSI CD-ROM errors often stall the boot.
    result = session.wait_for(
        r"IRIX Release|Starting up the system|sash not found",
        timeout=60, max_wait=300
    )
    if result.matched:
        if "sash not found" in result.output:
            raise RuntimeError(
                "sash not found in volume header — bootloader not installed. "
                "Install is incomplete; packages were likely left unresolved. "
                "Check the transcript for conflict resolution failures."
            )
        log("  Kernel built and booting from disk")
    else:
        log(f"  WARNING: kernel boot not confirmed ({result.bail_reason})")
        log("  Phase 5 will attempt cold boot verification")


# ── Phase 5: Verify cold boot from disk ─────────────────────────────────────

def phase_verify_boot(version_cfg, disk_path, instance=None, ram_mb=None):
    """Stop the install session and cold-boot from disk to verify.

    Returns list of missing critical package names (empty if all present).
    """
    log("Phase 5: Verifying cold boot from disk")

    machine = version_cfg["machine"]
    critical_missing = []
    with QEMUSession(
        machine=machine,
        ram_mb=ram_mb or 64,
        scsi_drives=[disk_path],
        extra_args=["-icount", "shift=0,sleep=off"],
        repeat_threshold=0,  # Disable repeat detection during boot
    ) as q:
        # The harness forces autoload=false, so we land at the PROM menu.
        # Select option 1 to boot from disk.
        # max_wait=180: PROM POST + SCSI probe can take 90s+ on slow systems.
        expect(q, r"Option\?", timeout=15, max_wait=180,
               label="PROM System Maintenance Menu")
        q.send("1\r")
        log("  Selected 'Start System' from PROM menu")

        # Stage 2: Confirm the kernel loaded and early init started.
        # "The system is coming up." is printed by /etc/init.d/sysetup
        # immediately after the kernel enters user-space init — before any
        # long-running daemons start.  Seeing it means the disk is good and
        # we're in IRIX user-space; we just need to wait for the rest.
        # Also accept "c, f, or a" (stale miniroot state flag) and "login:"
        # in case the system boots exceptionally fast.
        # max_wait=300: SCSI load of a large qcow2 + kernel decompress can take 90s.
        log("  Waiting for kernel to load...")
        result = q.wait_for(
            r"The system is coming up\.|c, f, or a|login:|IRIX Release",
            timeout=90, max_wait=300
        )
        if not result.matched:
            fail(f"Kernel did not load from disk: {result.bail_reason}\n"
                 f"Last output:\n{result.output[-500:]}")

        if "c, f, or a" in result.output:
            # The first cold boot after install may show a "miniroot install
            # failed" prompt if the miniroot state flag wasn't cleared.
            log("  Detected stale miniroot state — fixing")
            q.send("f\r")
            result = q.wait_for(r"Option\?", timeout=15, max_wait=180)
            if result.matched:
                q.send("1\r")
                log("  Re-selected 'Start System' after miniroot state fix")
            result = q.wait_for(
                r"The system is coming up\.|login:|IRIX Release",
                timeout=90, max_wait=300
            )
            if not result.matched:
                fail(f"Kernel did not load after miniroot fix: {result.bail_reason}\n"
                     f"Last output:\n{result.output[-500:]}")

        log("  Kernel loaded, system initializing...")

        # Stage 3: Wait for the login prompt.
        # IRIX runs init scripts (network, license servers, xdm, etc.)
        # that can go completely silent for 60–90s on slow systems.
        # idle timeout=120s: survive these quiet periods without bailing.
        # max_wait=900: full installs have many startup daemons (license,
        # llbd, glbd, Internet Gateway, etc.) that produce continuous output
        # for several minutes on top of a ~90s wall-clock PROM POST.  Any
        # serial output resets the idle timer, so max_wait is the only
        # backstop; it must be long enough to cover the worst case.
        # We skip this wait if login: already appeared in the kernel-load output.
        if "login:" not in result.output:
            result = q.wait_for(
                r"login:",
                timeout=120, max_wait=900
            )
            if not result.matched:
                log(f"  WARNING: login prompt not reached ({result.bail_reason})")
                log(f"  Last output: {result.output[-200:]!r}")
                log("  Phase 5 inconclusive — disk may still be good; verify manually")
                return critical_missing
        log("  Login prompt reached on cold boot")

        # Log in as root
        q.send("root\r")
        result = q.wait_for(r"TERM|#", timeout=5, max_wait=30)
        if result.matched and "TERM" in result.output:
            q.send("\r")
            q.wait_for(r"#", timeout=5, max_wait=10)

        # Run uname
        q.send("uname -a\r")
        result = expect(q, r"#", timeout=5, max_wait=10, label="uname output")

        # Parse uname output: skip the echo line, find the IRIX line
        uname_line = ""
        for line in result.output.strip().splitlines():
            line = line.strip()
            if line.startswith("IRIX"):
                uname_line = line
                break
        if re.search(version_cfg["uname_pattern"], result.output):
            log(f"  Verified: {uname_line or 'IRIX version matched'}")
        else:
            log(f"  WARNING: uname output doesn't match expected pattern")
            log(f"  Output: {result.output.strip()}")

        # Run df
        q.send("df -k\r")
        result = expect(q, r"#", timeout=5, max_wait=10, label="df output")
        for line in result.output.strip().splitlines():
            if "/" in line and "%" in line:
                log(f"  {line.strip()}")

        # Check critical packages
        critical_packages = version_cfg.get("critical_packages", [])
        if critical_packages:
            log("  Checking critical packages...")
            for pkg in critical_packages:
                q.send(f"sh -c 'versions {pkg} 2>&1'\r")
                result = q.wait_for(r"#", timeout=5, max_wait=10)
                # `versions` outputs "I  <pkg>  <date>  <desc>" for installed,
                # or empty output (just the command echo) for missing.
                # Must check for "I  <pkg>" pattern — not just pkg name,
                # which appears in the command echo regardless.
                installed = bool(re.search(
                    rf'^I\s+{re.escape(pkg)}',
                    result.output, re.MULTILINE
                ))
                if not installed:
                    critical_missing.append(pkg)
                    log(f"  WARNING: missing critical package: {pkg}")
            if not critical_missing:
                log(f"  All {len(critical_packages)} critical packages present")


        apply_xdm_fixes(q)

        # Bake persistent networking into the disk for Docker appliance use.
        # IRIX's /etc/init.d/network reads these config files at boot:
        #   - /etc/hosts: hostname → IP mapping (line 342 in network script)
        #   - /etc/config/ifconfig-1.options: interface options (line 434)
        #   - /etc/config/static-route.options: default route (line 547)
        # Verified against: software_library/irix-655-source/m/eoe/cmd/
        #   initpkg/init.d/network
        log("  Configuring persistent networking...")

        # /etc/hosts: add hostname → IP mapping (idempotent)
        q.send("sh -c \"grep -q '10.0.2.15' /etc/hosts 2>/dev/null "
               "|| echo '10.0.2.15 IRIS' >> /etc/hosts\"; "
               "echo HOSTS_OK\r")
        result = q.wait_for(r"HOSTS_OK|#", timeout=5, max_wait=10)
        if "HOSTS_OK" in result.output:
            log("  Set /etc/hosts: 10.0.2.15 IRIS")

        # /etc/config/ifconfig-1.options: interface netmask
        q.send("echo 'netmask 255.255.255.0' > /etc/config/ifconfig-1.options "
               "&& echo IFCFG_OK\r")
        result = q.wait_for(r"IFCFG_OK|#", timeout=5, max_wait=10)
        if "IFCFG_OK" in result.output:
            log("  Set ifconfig-1.options")

        # /etc/config/static-route.options: default route
        # Uses literal $ROUTE and $QUIET — these are variables in the
        # IRIX /etc/init.d/network script that are preset at runtime.
        q.send("echo '$ROUTE $QUIET add default 10.0.2.2' "
               "> /etc/config/static-route.options "
               "&& echo ROUTE_OK\r")
        result = q.wait_for(r"ROUTE_OK|#", timeout=5, max_wait=10)
        if "ROUTE_OK" in result.output:
            log("  Set static-route.options")

        # Enable networking at boot
        q.send("chkconfig network on && echo NET_ON\r")
        result = q.wait_for(r"NET_ON|#", timeout=5, max_wait=10)
        if "NET_ON" in result.output:
            log("  Enabled network chkconfig")

        # rsh trust: allow remote shell as root (for Docker exec dispatcher)
        q.send("echo 'localhost root' > /etc/hosts.equiv "
               "&& echo '+ root' >> /etc/hosts.equiv "
               "&& echo RSH_OK\r")
        result = q.wait_for(r"RSH_OK|#", timeout=5, max_wait=10)
        if "RSH_OK" in result.output:
            log("  Configured /etc/hosts.equiv for rsh")

        # Ensure inetd enabled (provides rsh/telnet daemons)
        q.send("chkconfig inetd on && echo INETD_OK\r")
        result = q.wait_for(r"INETD_OK|#", timeout=5, max_wait=10)
        if "INETD_OK" in result.output:
            log("  Enabled inetd chkconfig")

        log("  Persistent networking configured")

        # Flush guest filesystem to disk before taking snapshots.
        # The preceding echo/chkconfig commands write to the IRIX page cache
        # but QEMU's savevm captures disk blocks, not RAM — without sync the
        # xdm-config and chkconfig files arrive as zeros in the snapshot.
        q.send("sync && echo SYNC_OK\r")
        result = q.wait_for(r"SYNC_OK|#", timeout=10, max_wait=30)
        if "SYNC_OK" in result.output:
            log("  Filesystem synced before snapshot")
        else:
            log("  Warning: sync may not have completed")

        # Save install_complete snapshot (clean boot, all verification done)
        try:
            q.save_snapshot("install_complete")
            log("  Saved snapshot: install_complete")
            if instance:
                from sgi_mcp.vm_instances import add_snapshot
                add_snapshot(instance, "install_complete",
                             "Post-install verified boot with xdm fix and networking")
        except Exception as e:
            log(f"  Warning: could not save install_complete snapshot: {e}")

        # Save booted snapshot
        try:
            q.save_snapshot(version_cfg["snapshot_booted"])
            log(f"  Saved snapshot: {version_cfg['snapshot_booted']}")
            if instance:
                from sgi_mcp.vm_instances import add_snapshot
                add_snapshot(instance, version_cfg["snapshot_booted"],
                             "Running IRIX with root shell, xdm fix applied, "
                             "persistent networking and rsh configured")
        except Exception as e:
            log(f"  Warning: could not save snapshot: {e}")

    # Configure NVRAM for autoboot so the instance boots to IRIX directly
    try:
        from sgi_mcp.nvram_utils import nvram_write_var
        nvram_path = disk_path.replace(".qcow2", "").replace(".img", "")
        # Find the NVRAM file that was used during this boot session
        nvram_candidates = []
        if instance:
            from sgi_mcp.vm_instances import get_nvram_path
            nvram_candidates.append(str(get_nvram_path(instance)))
        machine = version_cfg.get("machine", "indy")
        nvram_file = NVRAM_FILES.get(machine, f"sgi_{machine}_nvram.bin")
        nvram_candidates.append(str(PROJECT_ROOT / nvram_file))
        for build_name in ("build", "build-mac"):
            nvram_candidates.append(str(PROJECT_ROOT / "qemu" / build_name / nvram_file))

        nvram_configured = False
        nvram_written = None
        for nvram_candidate in nvram_candidates:
            if os.path.exists(nvram_candidate):
                autoboot_vars = {
                    "autoload": "Y",
                    "syspart": "scsi(0)disk(1)rdisk(0)partition(8)",
                    "ospart": "scsi(0)disk(1)rdisk(0)partition(0)",
                    "osloader": "sash",
                    "osfile": "/unix",
                    "osopts": "",
                    "console": "d",
                }
                for var, val in autoboot_vars.items():
                    nvram_write_var(nvram_candidate, var, val)
                log(f"  Configured NVRAM for autoboot: {nvram_candidate}")
                nvram_written = nvram_candidate
                nvram_configured = True
                break
        if not nvram_configured:
            log("  Warning: no NVRAM file found to configure autoboot")

        # If the NVRAM was written to a project-root path (because the
        # instance NVRAM didn't exist yet), copy it to the instance directory
        # so future sessions pick it up automatically.
        if nvram_written and instance:
            from sgi_mcp.vm_instances import get_nvram_path
            instance_nvram = str(get_nvram_path(instance))
            if nvram_written != instance_nvram and not os.path.exists(instance_nvram):
                import shutil
                shutil.copy2(nvram_written, instance_nvram)
                log(f"  Copied NVRAM to instance: {instance_nvram}")
    except Exception as e:
        log(f"  Warning: could not configure NVRAM for autoboot: {e}")

    log("  Phase 5 complete — installation verified")
    return critical_missing


# ── Main entry point ────────────────────────────────────────────────────────

def install_irix(version, disk_path=None, verify_only=False, instance=None,
                  no_combined=False, disk_size_mb=2048, ram_mb=None,
                  conflict_mode="auto", conflict_resolutions=None,
                  install_level="standard", inst_debug=False):
    """Run the full IRIX installation pipeline.

    Args:
        version: IRIX version string ("5.3", "6.2", "6.5")
        disk_path: Path to disk image (created if not exists)
        verify_only: If True, skip install and just verify boot
        instance: VM instance name — records snapshots in manifest
        no_combined: If True, force CD-swap path even if combined image exists
        disk_size_mb: Disk image size in megabytes (default 2048)
        ram_mb: RAM size in megabytes (default 64)
        conflict_mode: "auto" (legacy), "collect" (stop at conflicts),
            or "apply" (use provided resolutions)
        conflict_resolutions: Resolution decisions for "apply" mode
        install_level: Package selection level: "standard" (recommended subset)
            or "default" (everything available)
        inst_debug: If True, enable inst internal debug logging via Admin menu
    """
    if version not in VERSIONS:
        fail(f"Unsupported version: {version}. Choose from: {', '.join(VERSIONS)}")

    cfg = dict(VERSIONS[version])  # Mutable copy — never mutate the global
    if no_combined:
        cfg["_no_combined"] = True

    # Dynamic image resolution — discovers images from software_library/
    _resolve_version_images(version, cfg)

    disk_path = disk_path or cfg["default_disk"]
    machine = cfg["machine"]

    log(f"{'='*60}")
    log(f"IRIX {version} Installation")
    log(f"  Machine: {machine}")
    log(f"  RAM:     {ram_mb or 64}MB")
    log(f"  Disk:    {disk_path} ({disk_size_mb}MB)")
    total_cds = len(cfg['cdroms']) + len(cfg.get('extra_cds', []))
    log(f"  CDs:     {total_cds}")
    log(f"{'='*60}")

    start_time = time.time()

    if verify_only:
        critical_missing = phase_verify_boot(cfg, disk_path, instance=instance,
                                             ram_mb=ram_mb)
        elapsed = time.time() - start_time
        if critical_missing:
            log(f"WARNING: Missing critical packages: {', '.join(critical_missing)}")
        log(f"Verification complete in {elapsed:.0f}s")
        return

    # Remove stale NVRAM to prevent osopts=INST from previous installs
    # causing sash to auto-chain into miniroot recovery mode
    nvram_file = NVRAM_FILES.get(machine, f"sgi_{machine}_nvram.bin")
    nvram_cleanup_paths = [str(PROJECT_ROOT / nvram_file),
                           str(PROJECT_ROOT / "qemu" / "build" / nvram_file),
                           str(PROJECT_ROOT / "qemu" / "build-mac" / nvram_file)]
    if instance:
        nvram_cleanup_paths.append(str(PROJECT_ROOT / "vm_instances" / instance / "nvram.bin"))
    for nvram_path in nvram_cleanup_paths:
        if os.path.exists(nvram_path):
            os.remove(nvram_path)
            log(f"  Removed stale NVRAM: {nvram_path}")

    # Create fresh disk (remove old one first to ensure clean state)
    if os.path.exists(disk_path):
        os.remove(disk_path)
    log(f"Creating {disk_size_mb}MB qcow2 disk image")
    create_disk(disk_path, size_mb=disk_size_mb, fmt="qcow2")

    # Build SCSI drive list: disk + CD-ROMs + optional combined image
    scsi_drives = [disk_path] + [f"{cd}:cdrom" for cd in cfg["cdroms"]]
    combined = cfg.get("combined_image", "")
    use_combined = (combined and os.path.exists(combined)
                    and not cfg.get("_no_combined"))
    if use_combined:
        # Combined image attached as a read-only data disk (SCSI target 2).
        # It appears after the install disk (target 0/1) and before
        # the CD-ROMs (target 4/5).  Read-only so savevm snapshots work
        # (QEMU requires all writable devices to support snapshots).
        scsi_drives.insert(1, f"{combined}:ro")
        log(f"  Combined image: {os.path.basename(combined)}")

    # Phases 1-4 run in a single QEMU session (writes must persist)
    # Note: do NOT use -icount during install — it can interfere with exitops
    # (sash volume header installation). icount is only used for Phase 5 boot.
    transcript_path = disk_path + ".transcript.log"
    serial_log_path = disk_path + ".serial.log"
    log(f"  Live serial log: tail -f {serial_log_path}")
    skipped_packages = []
    session_ram = ram_mb or 64
    with QEMUSession(
        machine=machine,
        ram_mb=session_ram,
        scsi_drives=scsi_drives,
        repeat_threshold=0,  # Disable repeat detection during install
        serial_log_path=serial_log_path,
    ) as q:
        try:
            # Set conflict output directory for collect mode
            if instance:
                cfg = dict(cfg)  # Don't mutate global
                inst_dir = str(PROJECT_ROOT / "vm_instances" / instance)
                cfg["_conflict_dir"] = inst_dir

            phase_partition(q, cfg)
            phase_miniroot(q, cfg)
            result = phase_install(q, cfg,
                                   conflict_mode=conflict_mode,
                                   conflict_resolutions=conflict_resolutions,
                                   install_level=install_level,
                                   inst_debug=inst_debug)

            if conflict_mode == "collect":
                # In collect mode, phase_install returns conflicts list.
                # Save snapshot and transcript, then return the conflicts
                # for the caller to analyze.
                log("  Collect mode: stopping after conflict collection")
                if instance:
                    from sgi_mcp.vm_instances import add_snapshot
                    add_snapshot(instance, "pre_conflict_resolution",
                                "Inst at conflict resolution point — "
                                "distributions loaded, packages selected")
                return result  # list of conflict dicts

            skipped_packages = result

            phase_quit_and_build(q, cfg, instance=instance)
        finally:
            # Always save transcript for debugging
            try:
                with open(transcript_path, "w") as f:
                    f.write(q.transcript)
                log(f"  Transcript saved to {transcript_path}")
            except Exception:
                pass

    # Parse installed packages from transcript — inst logs a line for each
    # subsystem it installs: "Installing new versions of selected <pkg> subsystems"
    initial_installed_packages = re.findall(
        r'Installing new versions of selected (\S+) subsystems',
        q.transcript,
    )
    log(f"  Recorded {len(initial_installed_packages)} installed subsystems from transcript")

    # Build CD order names for manifest (needed regardless of Phase 5 outcome)
    cd_order = []
    for cd_path in cfg["cdroms"]:
        cd_order.append(os.path.basename(cd_path).replace(".img", ""))
    for cd_path in cfg.get("extra_cds", []):
        cd_order.append(os.path.basename(cd_path).replace(".img", ""))

    # Phase 5: cold boot verification (new session, disk only).
    # Wrap in try/finally so manifest is always written — even if Phase 5
    # calls fail() → sys.exit(1), the finally block still runs before the
    # SystemExit propagates, preserving the 519+ installed package names.
    critical_missing = []
    try:
        critical_missing = phase_verify_boot(cfg, disk_path, instance=instance,
                                             ram_mb=session_ram)
    finally:
        elapsed = time.time() - start_time
        if instance:
            try:
                from sgi_mcp.vm_instances import update_installation_info
                update_installation_info(instance, {
                    "cd_order": cd_order,
                    "initial_installed_packages": initial_installed_packages,
                    "critical_missing": critical_missing,
                    "install_time_s": int(elapsed),
                })
            except Exception as e:
                log(f"  Warning: could not save installation info: {e}")

    log(f"{'='*60}")
    log(f"IRIX {version} installation complete in {elapsed:.0f}s")
    log(f"  Disk: {disk_path}")
    log(f"  Snapshot: {cfg['snapshot_booted']}")
    if skipped_packages:
        log(f"  Skipped packages: {len(skipped_packages)}")
    if critical_missing:
        log(f"  WARNING: Missing critical packages: {', '.join(critical_missing)}")
    log(f"{'='*60}")


def install_addon(base_disk, addon_image, output_disk=None, addon_name="addon",
                   machine="indy", snapshot_name=None, ram_mb=256,
                   addon_dirs=None, extra_args=None,
                   install_selectors=None):
    """Install additional packages onto an existing IRIX disk.

    If output_disk is provided, copies base_disk to output_disk first.
    If output_disk is None, operates directly on base_disk (in-place mode).

    Supports two addon image layouts:
    - Single-dist: all files in /mnt/dist (legacy dedup layout)
    - Per-CD: each CD's files in /mnt/<cd_name>/ subdirectories

    Args:
        base_disk: Path to existing IRIX qcow2 disk image
        addon_image: Path to combined EFS dist image with addon packages
        output_disk: Path for the output disk image (copy of base + addon).
            If None, operates directly on base_disk (in-place).
        addon_name: Human-readable name for the addon (for logging)
        machine: QEMU machine type
        snapshot_name: Snapshot name to save after install (optional)
        ram_mb: RAM size in MB for booting
        addon_dirs: List of subdirectory names to open from a per-CD layout.
            If None, opens all directories. Useful when the image contains
            base OS packages that would conflict with an already-installed system.
        extra_args: Additional QEMU arguments (list of strings, e.g. NVRAM path).
    """
    import shutil

    work_disk = output_disk or base_disk

    log(f"{'='*60}")
    log(f"Installing addon: {addon_name}")
    log(f"  Base disk:   {base_disk}")
    log(f"  Addon image: {addon_image}")
    if output_disk:
        log(f"  Output disk: {output_disk}")
    else:
        log(f"  Mode: in-place (modifying base disk directly)")
    log(f"{'='*60}")

    start_time = time.time()

    if not os.path.exists(base_disk):
        fail(f"Base disk not found: {base_disk}")
    if not os.path.exists(addon_image):
        fail(f"Addon image not found: {addon_image}")

    # Copy base disk to output path (skip if in-place)
    if output_disk:
        log("Copying base disk...")
        os.makedirs(os.path.dirname(output_disk) or '.', exist_ok=True)
        shutil.copy2(base_disk, output_disk)
        log(f"  Copied to {output_disk}")

    # Boot from work disk + addon image as second SCSI disk
    scsi_drives = [work_disk, addon_image]

    session_extra = ["-icount", "shift=0,sleep=off"]
    if extra_args:
        session_extra.extend(extra_args)

    with QEMUSession(
        machine=machine,
        ram_mb=ram_mb,
        scsi_drives=scsi_drives,
        extra_args=session_extra,
        repeat_threshold=0,
    ) as q:
        # Wait for PROM menu and boot from disk
        expect(q, r"Option\?", timeout=5, max_wait=150,
               label="PROM System Maintenance Menu")
        q.send("1\r")
        log("  Booting from disk...")

        # Wait for login prompt — maximalist installs can have long gaps
        # between the last service startup message and the login prompt
        expect(q, r"login:", timeout=60, max_wait=600,
               label="login prompt")
        log("  Login prompt reached")

        # Log in as root
        q.send("root\r")
        result = q.wait_for(r"TERM|#", timeout=5, max_wait=30)
        if result.matched and "TERM" in result.output:
            q.send("\r")
            q.wait_for(r"#", timeout=5, max_wait=10)

        # Mount the addon dist image (SCSI target 2, partition 7)
        log("  Mounting addon dist image...")
        q.send("mkdir -p /mnt 2>/dev/null; true\r")
        q.wait_for(r"#", timeout=5, max_wait=10)

        q.send("mount -r /dev/dsk/dks0d2s7 /mnt\r")
        result = q.wait_for(r"#", timeout=10, max_wait=60)
        if not result.matched:
            fail(f"mount timed out: {result.bail_reason}")

        # Detect layout: single /mnt/dist vs per-CD subdirectories
        q.send("test -d /mnt/dist; echo LAYOUT_$?\r")
        result = q.wait_for(r"#", timeout=5, max_wait=10)

        remaining_dirs = []  # Per-CD dirs to open after first inst -f

        if "LAYOUT_0" in result.output:
            # Single-dist layout
            log("  Detected single-dist layout")
            q.send("ls /mnt/dist | wc -l\r")
            result = q.wait_for(r"#", timeout=5, max_wait=10)
            log(f"  Addon dist files: {result.output.strip()}")

            log("  Starting inst...")
            q.send("inst -f /mnt/dist\r")
        else:
            # Per-CD subdirectory layout
            log("  Detected per-CD layout")
            q.send("ls /mnt\r")
            result = q.wait_for(r"#", timeout=5, max_wait=10)

            # Parse directory names — filter out shell prompt artifacts
            lines = result.output.strip().splitlines()
            dirs = []
            for line in lines:
                for token in line.split():
                    token = token.strip()
                    if token and not token.startswith('.') \
                       and token not in ('lost+found', 'ls', '/mnt', '#') \
                       and not token.startswith('/') \
                       and not re.match(r'^\d+#?$', token) \
                       and token != 'IRIS':
                        dirs.append(token)

            # Filter to specific directories if requested
            if addon_dirs:
                dirs = [d for d in dirs if d in addon_dirs]

            # Sort: foundation/overlay first, then dev, then rest
            def _cd_priority(name):
                n = name.lower()
                if 'foundation' in n:
                    return (0, name)
                if 'overlay' in n or 'install-tools' in n:
                    return (1, name)
                if 'mipspro' in n:
                    return (2, name)
                if 'dev' in n:
                    return (3, name)
                return (4, name)

            dirs.sort(key=_cd_priority)
            log(f"  Found {len(dirs)} CD directories:")
            for d in dirs:
                log(f"    {d}")

            if not dirs:
                fail("No distribution directories found in addon image")

            # Open first directory with inst -f
            first_path = f"/mnt/{dirs[0]}"
            log(f"  Starting inst with {first_path}...")
            q.send(f"inst -f {first_path}\r")
            remaining_dirs = dirs[1:]

        # Wait for inst to read distribution and show prompt
        for _ in range(30):
            result = q.wait_for(
                r"Inst>|more\?|Please enter|startup script|"
                r"Install software from|enter a choice|"
                r"already been opened|yes.*or.*no|answer.*yes",
                timeout=10, max_wait=120
            )
            if not result.matched:
                break
            if "more?" in result.output:
                q.send("n\r")
                continue
            if "Install software from" in result.output:
                q.send("done\r")
                continue
            if "already been opened" in result.output or \
               "answer" in result.output and "yes" in result.output:
                q.send("yes\r")
                continue
            if "yes" in result.output and "no" in result.output:
                q.send("yes\r")
                continue
            if "Please enter" in result.output or \
               "startup script" in result.output or \
               "enter a choice" in result.output:
                q.send("2\r")
                continue
            break  # Got Inst>

        if not result.matched or "Inst>" not in result.output:
            fail(f"inst did not reach Inst> prompt: {result.output[-300:]}")

        log("  Inst prompt reached")

        # Open remaining per-CD distributions
        for dir_name in remaining_dirs:
            path = f"/mnt/{dir_name}"
            q.send("\r")
            _wait_for_inst_prompt(q, timeout=5, max_wait=60)

            log(f"  Opening additional distribution: {path}")
            if not _open_dist_path(q, path):
                log(f"    WARNING: {path} unreadable/empty, skipping")

        if remaining_dirs:
            # Wait for clean Inst> after opening all distributions — use 5s
            # idle timeout so product-description bursts from multiple opens
            # fully settle before we proceed to package selection.
            q.send("\r")
            _wait_for_inst_prompt(q, timeout=5, max_wait=60)

        # Select packages. Two paths:
        #   install_selectors=None (default) — legacy "install default +
        #   keep *_eoe + install prereqs" pattern.
        #   install_selectors=[...]         — target specific packages.
        #
        # CRITICAL: use inst's `replace` (not `install`) for the targeted
        # case. `install <pkg>` is a no-op when /var/inst/<pkg> already
        # exists in the package database — and on this addon-boot it
        # WILL exist for many packages that the base install registered
        # but never actually extracted files for (the silent-deselect
        # artifact of the legacy install_level cascade). `replace`
        # forces a reinstall: removes the package's tracked files,
        # extracts the new copies from the open dists.
        if install_selectors:
            q.send("keep *\r")
            _wait_for_inst_prompt(q, timeout=5, max_wait=60)
            for sel in install_selectors:
                # Try install first (handles never-installed packages),
                # then replace (handles already-registered packages).
                # The two combined cover both states.
                q.send(f"install {sel}\r")
                _wait_for_inst_prompt(q, timeout=5, max_wait=60)
                q.send(f"replace {sel}\r")
                _wait_for_inst_prompt(q, timeout=5, max_wait=60)
            q.send("install prereqs\r")
            _wait_for_inst_prompt(q, timeout=5, max_wait=60)
        else:
            q.send("install default\r")
            _wait_for_inst_prompt(q, timeout=5, max_wait=60)
            for keep_pat in ["*_eoe*", "*_eoe_*", "inst_dev*"]:
                q.send(f"keep {keep_pat}\r")
                _wait_for_inst_prompt(q, timeout=5, max_wait=30)
            q.send("install prereqs\r")
            _wait_for_inst_prompt(q, timeout=5, max_wait=60)

        # Resolve conflicts — use "1b" (install prerequisite) for addons,
        # since we want to keep the new packages and pull in dependencies.
        resolved, skipped = _resolve_conflicts(q, default_option="1b")
        if not resolved:
            log("  WARNING: could not resolve all conflicts")
        if skipped:
            log(f"  Skipped {len(skipped)} packages due to conflicts")

        # Install
        _wait_for_inst_prompt(q, timeout=5, max_wait=15)
        q.send("go\r")
        log("  Running go...")

        _wait_for_cd_install(q, addon_name)
        log("  Installation complete")

        # Quit inst
        q.send("quit\r")
        for _ in range(20):
            result = q.wait_for(
                r"really want to quit|Do you really|more\?|Ready to restart|"
                r"Restart\?|Inst>|#",
                timeout=30, max_wait=600
            )
            if not result.matched:
                break
            if "really" in result.output:
                q.send("yes\r")
                continue
            if "more?" in result.output:
                q.send("n\r")
                continue
            if "Inst>" in result.output:
                q.send("quit\r")
                continue
            if "Ready" in result.output or "Restart" in result.output:
                # Don't restart — we just want to save and exit
                q.send("no\r")
                q.wait_for(r"#", timeout=5, max_wait=30)
                break
            if "#" in result.output:
                break

        # Save snapshot if requested
        if snapshot_name:
            try:
                q.save_snapshot(snapshot_name)
                log(f"  Saved snapshot: {snapshot_name}")
            except Exception as e:
                log(f"  Warning: could not save snapshot: {e}")

    elapsed = time.time() - start_time
    log(f"{'='*60}")
    log(f"Addon {addon_name} installed in {elapsed:.0f}s")
    log(f"  Disk: {work_disk}")
    if skipped:
        log(f"  Skipped: {len(skipped)} packages")
    log(f"{'='*60}")


# ── IRIXShell: transport abstraction for serial/telnet ────────────────

class IRIXShell:
    """Abstraction over serial (QEMUSession) and telnet I/O for IRIX.

    Provides a unified send/wait_for interface so install logic doesn't
    need to know the transport.  Serial mode wraps a running MCP session
    via qemu_session_send. Telnet mode opens a raw socket to the IRIX
    telnet port.
    """

    def __init__(self, method="serial", session_id=None,
                 host="localhost", port=2323, timeout=10):
        """Initialize an IRIX shell connection.

        Args:
            method: "serial" or "telnet"
            session_id: MCP session ID (for serial method)
            host: Hostname for telnet
            port: Port for telnet (default 2323 = SLIRP forwarded)
            timeout: Default timeout for operations
        """
        self.method = method
        self.session_id = session_id
        self.host = host
        self.port = port
        self.timeout = timeout
        self._telnet = None
        self._transcript = ""

    def connect(self):
        """Establish connection (telnet only — serial is already connected)."""
        if self.method == "telnet":
            import socket
            self._telnet = socket.create_connection(
                (self.host, self.port), timeout=self.timeout)
            self._telnet.settimeout(self.timeout)
            # Wait for login prompt
            data = self._recv_until(r"login:", timeout=30)
            self._transcript += data
            self.send("root\r")
            data = self._recv_until(r"TERM|#", timeout=15)
            self._transcript += data
            if "TERM" in data:
                self.send("\r")
                data = self._recv_until(r"#", timeout=10)
                self._transcript += data

    def send(self, text):
        """Send text to the IRIX shell."""
        if self.method == "serial":
            # Use qemu_session_send via MCP — the caller passes a
            # session wrapper, not raw MCP calls
            if hasattr(self, '_session') and self._session:
                self._session.send(text)
        elif self.method == "telnet":
            if self._telnet:
                self._telnet.sendall(text.encode("ascii", errors="replace"))

    def wait_for(self, pattern, timeout=5, max_wait=120):
        """Wait for a regex pattern in output.

        Returns an object with .matched (bool) and .output (str).
        """
        if self.method == "serial":
            if hasattr(self, '_session') and self._session:
                return self._session.wait_for(
                    pattern, timeout=timeout, max_wait=max_wait)
        elif self.method == "telnet":
            data = self._recv_until(pattern, timeout=max_wait)
            self._transcript += data
            matched = bool(re.search(pattern, data))

            class Result:
                pass
            r = Result()
            r.matched = matched
            r.output = data
            r.bail_reason = None if matched else "timeout"
            return r

        # Fallback
        class Result:
            pass
        r = Result()
        r.matched = False
        r.output = ""
        r.bail_reason = "no transport"
        return r

    def _recv_until(self, pattern, timeout=30):
        """Receive data from telnet until pattern matches or timeout."""
        import select
        import time as _time

        buf = ""
        deadline = _time.time() + timeout
        compiled = re.compile(pattern)

        while _time.time() < deadline:
            remaining = deadline - _time.time()
            if remaining <= 0:
                break
            ready, _, _ = select.select([self._telnet], [], [],
                                        min(remaining, 1.0))
            if ready:
                try:
                    chunk = self._telnet.recv(4096).decode(
                        "ascii", errors="replace")
                    if not chunk:
                        break
                    buf += chunk
                    if compiled.search(buf):
                        return buf
                except (OSError, ConnectionError):
                    break
        return buf

    def close(self):
        """Close the connection."""
        if self._telnet:
            try:
                self._telnet.close()
            except Exception:
                pass
            self._telnet = None

    @classmethod
    def from_qemu_session(cls, session):
        """Create an IRIXShell wrapping a QEMUSession (serial mode)."""
        shell = cls(method="serial")
        shell._session = session
        return shell

    @classmethod
    def from_telnet(cls, host="localhost", port=2323):
        """Create an IRIXShell using telnet to a running IRIX instance."""
        shell = cls(method="telnet", host=host, port=port)
        shell.connect()
        return shell


def install_addon_live(session_id=None, addon_image=None,
                       addon_categories=None, package_name=None,
                       method="serial", host="localhost", port=2323,
                       machine="indy", version="6.5"):
    """Install packages on a running IRIX instance.

    Can discover the right disc image automatically by package name
    or category, then connect to the running IRIX session (via serial
    or telnet) and run inst to install.

    Args:
        session_id: Existing qemu_session ID (for serial method).
            If None with method="serial", creates a new QEMUSession.
        addon_image: Explicit image path. If None, auto-discovers
            based on addon_categories or package_name.
        addon_categories: List of categories to find images for
            (e.g., ["dev_compiler", "demos"]).
        package_name: Package name to search for (e.g., "netscape").
            Uses image_catalog.find_package() to locate the disc image.
        method: "serial" or "telnet"
        host/port: For telnet method
        machine: QEMU machine type
        version: IRIX version for image discovery
    """
    start_time = time.time()

    # Step 1: Resolve the addon disc image
    if addon_image is None:
        catalog = scan_software_library()

        if package_name:
            # Search for package by name
            matches = catalog.find_package(package_name, version=version)
            if not matches:
                fail(f"No disc image found containing package '{package_name}'")
            # Prefer combo images and larger images
            matches.sort(key=lambda m: (
                m.is_combo,
                m.product_count,
            ), reverse=True)
            addon_image = matches[0].path
            log(f"Found '{package_name}' on: {matches[0].display_name}")
            if len(matches) > 1:
                log(f"  Also available on: "
                    f"{', '.join(m.display_name for m in matches[1:3])}")

        elif addon_categories:
            # Find images by category
            images = catalog.get_install_set(version, addon_categories)
            if not images:
                fail(f"No images found for categories: {addon_categories}")
            # Use the first/best match
            addon_image = images[0].path
            log(f"Using image: {images[0].display_name}")

        else:
            fail("Must specify addon_image, addon_categories, or package_name")

    if not os.path.exists(addon_image):
        fail(f"Addon image not found: {addon_image}")

    log(f"{'='*60}")
    log(f"Live addon installation")
    log(f"  Image: {os.path.basename(addon_image)}")
    log(f"  Method: {method}")
    log(f"{'='*60}")

    # Step 2: Connect to the running IRIX instance
    # For serial method, the caller must provide a QEMUSession or session_id.
    # The MCP harness_addon_live tool handles creating the shell from a
    # session_id. For direct Python usage, pass a QEMUSession via
    # install_addon_live_with_session().
    if method == "telnet":
        shell = IRIXShell.from_telnet(host=host, port=port)
        log(f"  Connected via telnet to {host}:{port}")

        try:
            _run_live_inst(shell, addon_image)
        finally:
            shell.close()
    else:
        fail("Serial method requires a QEMUSession — use "
             "install_addon_live_with_session() or the MCP tool")

    elapsed = time.time() - start_time
    log(f"Live addon install complete in {elapsed:.0f}s")


def install_addon_live_with_session(session, addon_image=None,
                                    addon_categories=None,
                                    package_name=None, version="6.5"):
    """Install packages on a running IRIX via an existing QEMUSession.

    Same as install_addon_live() but takes a QEMUSession directly
    instead of a transport specification.
    """
    start_time = time.time()

    # Resolve addon image
    if addon_image is None:
        catalog = scan_software_library()

        if package_name:
            matches = catalog.find_package(package_name, version=version)
            if not matches:
                fail(f"No disc image found containing package '{package_name}'")
            matches.sort(key=lambda m: (m.is_combo, m.product_count),
                         reverse=True)
            addon_image = matches[0].path
            log(f"Found '{package_name}' on: {matches[0].display_name}")
        elif addon_categories:
            images = catalog.get_install_set(version, addon_categories)
            if not images:
                fail(f"No images found for categories: {addon_categories}")
            addon_image = images[0].path
            log(f"Using image: {images[0].display_name}")
        else:
            fail("Must specify addon_image, addon_categories, or package_name")

    if not os.path.exists(addon_image):
        fail(f"Addon image not found: {addon_image}")

    log(f"Live addon installation via serial")
    log(f"  Image: {os.path.basename(addon_image)}")

    shell = IRIXShell.from_qemu_session(session)
    _run_live_inst(shell, addon_image)

    elapsed = time.time() - start_time
    log(f"Live addon install complete in {elapsed:.0f}s")


def _run_live_inst(shell, addon_image):
    """Run inst on a live IRIX system to install from a mounted disc image.

    Expects the shell to be logged in as root at a '#' prompt.
    The addon_image must already be attached as a SCSI drive (target 2)
    or accessible via NFS/network.

    For SCSI-attached images: mounts /dev/dsk/dks0d2s7 at /mnt.
    Then runs inst -f /mnt/dist (single-dist) or opens per-CD subdirs.
    """
    # Mount the addon image
    shell.send("mkdir -p /mnt 2>/dev/null; true\r")
    shell.wait_for(r"#", timeout=5, max_wait=10)

    shell.send("mount -r /dev/dsk/dks0d2s7 /mnt\r")
    result = shell.wait_for(r"#", timeout=10, max_wait=60)
    if not result.matched:
        log("WARNING: mount timed out — image may not be attached as SCSI target 2")
        return

    # Detect layout
    shell.send("test -d /mnt/dist; echo LAYOUT_$?\r")
    result = shell.wait_for(r"#", timeout=5, max_wait=10)

    if "LAYOUT_0" in result.output:
        dist_path = "/mnt/dist"
        log("  Single-dist layout detected")
    else:
        # Per-CD layout — find first directory
        shell.send("ls /mnt\r")
        result = shell.wait_for(r"#", timeout=5, max_wait=10)
        dirs = []
        for line in result.output.strip().splitlines():
            for token in line.split():
                token = token.strip()
                if token and not token.startswith('.') \
                   and token not in ('lost+found', 'ls', '/mnt', '#') \
                   and not token.startswith('/') \
                   and not re.match(r'^\d+#?$', token) \
                   and token != 'IRIS':
                    dirs.append(token)

        if not dirs:
            log("WARNING: no distribution directories found")
            shell.send("umount /mnt\r")
            shell.wait_for(r"#", timeout=5, max_wait=10)
            return

        dist_path = f"/mnt/{dirs[0]}"
        log(f"  Per-CD layout: {len(dirs)} directories")

    # Start inst
    log(f"  Running inst -f {dist_path}")
    shell.send(f"inst -f {dist_path}\r")

    # Wait for Inst> prompt, handling pagers and prompts
    for _ in range(30):
        result = shell.wait_for(
            r"Inst>|more\?|Please enter|startup script|"
            r"Install software from|enter a choice|"
            r"already been opened|yes.*or.*no",
            timeout=10, max_wait=120
        )
        if not result.matched:
            break
        if "more?" in result.output:
            shell.send("n\r")
            continue
        if "Install software from" in result.output:
            shell.send("done\r")
            continue
        if "already been opened" in result.output:
            shell.send("yes\r")
            continue
        if "yes" in result.output and "no" in result.output:
            shell.send("yes\r")
            continue
        if "Please enter" in result.output or \
           "startup script" in result.output or \
           "enter a choice" in result.output:
            shell.send("2\r")
            continue
        break  # Got Inst>

    log("  Inst prompt reached")

    # Select default packages, keeping base OS
    shell.send("install default\r")
    shell.wait_for(r"Inst>", timeout=10, max_wait=120)
    for keep_pat in ["*_eoe*", "*_eoe_*", "inst_dev*"]:
        shell.send(f"keep {keep_pat}\r")
        shell.wait_for(r"Inst>", timeout=5, max_wait=30)
    shell.send("install prereqs\r")
    shell.wait_for(r"Inst>", timeout=5, max_wait=60)

    # Install
    shell.send("go\r")
    log("  Running go...")

    # Wait for completion (simplified — no conflict resolution for live)
    for _ in range(60):
        result = shell.wait_for(
            r"Inst>|Installations.*successful|Installing|more\?|"
            r"Conflicts must be resolved|Interrupt>",
            timeout=60, max_wait=3600
        )
        if not result.matched:
            break
        if "Interrupt>" in result.output:
            shell.send("3\r")  # Continue past script errors
            continue
        if "more?" in result.output:
            shell.send("n\r")
            continue
        if "Conflicts must be resolved" in result.output:
            log("  Auto-resolving conflicts...")
            shell.send("conflicts 1a\r")
            shell.wait_for(r"Inst>|no conflicts", timeout=10, max_wait=60)
            shell.send("go\r")
            continue
        if "Installing" in result.output and \
           "Installations" not in result.output:
            continue
        break

    log("  Installation complete")

    # Quit inst
    shell.send("quit\r")
    for _ in range(10):
        result = shell.wait_for(
            r"really want to quit|more\?|Ready to restart|Restart\?|#",
            timeout=30, max_wait=120
        )
        if not result.matched:
            break
        if "really" in result.output:
            shell.send("yes\r")
            continue
        if "more?" in result.output:
            shell.send("n\r")
            continue
        if "Ready" in result.output or "Restart" in result.output:
            shell.send("no\r")
            shell.wait_for(r"#", timeout=5, max_wait=30)
            break
        if "#" in result.output:
            break

    # Unmount
    shell.send("umount /mnt 2>/dev/null; true\r")
    shell.wait_for(r"#", timeout=5, max_wait=10)
    log("  Cleanup done")


def main():
    parser = argparse.ArgumentParser(
        description="Fully automated IRIX installation for QEMU SGI emulation"
    )
    parser.add_argument("version", choices=list(VERSIONS.keys()),
                        help="IRIX version to install")
    parser.add_argument("--disk",
                        help="Disk image path (default: version-specific)")
    parser.add_argument("--verify-only", action="store_true",
                        help="Skip install, just verify existing disk boots")
    parser.add_argument("--no-combined", action="store_true",
                        help="Force legacy CD-swap path even if combined "
                             "distribution image exists")

    args = parser.parse_args()
    install_irix(args.version, disk_path=args.disk,
                 verify_only=args.verify_only,
                 no_combined=args.no_combined)


if __name__ == "__main__":
    main()


# ── Simple boot helpers (merged from irix_installer.py) ───────────────────────
_sw = PROJECT_ROOT / "software_library"

DEFAULT_CDROM_65 = str(_sw / "irix_6.5.22_images" / "IRIX 6.5 Installation Tools June 1998.img")
DEFAULT_CDROM_62 = str(_sw / "irix_6.2_images" / "IRIX 6.2 (Part 1 of 2) - 812-0469-001.efs.img")
DEFAULT_CDROM_53 = str(_sw / "IRIX 5.3 All Indigo2 IMPACT - 812-0119-010.efs.img")
DEFAULT_FOUNDATION1_65 = str(_sw / "irix_6.5.22_images" / "IRIX 6.5 Foundation 1.img")

IRIX_VERSIONS = {
    "6.5": [DEFAULT_CDROM_65, DEFAULT_FOUNDATION1_65],
    "6.2": [DEFAULT_CDROM_62],
    "5.3": [DEFAULT_CDROM_53],
}

IRIX_BAIL_PATTERNS = [
    r"Illegal field in CDB.*Illegal field in CDB",
    r"scsi bus reset",
    r"Cannot mount root",
    r"vfs_mountroot.*failed",
]


def boot_to_prom_menu(session, timeout=5, max_wait=60):
    result = session.wait_for(r"(System Maintenance|Option\?|Enter.*to start)", timeout=timeout, max_wait=max_wait)
    if result.matched and "press" in result.output.lower():
        session.send("\r")
        result = session.wait_for(r"Option\?", timeout=timeout, max_wait=30)
    return result


def install_miniroot(session, reload=False, timeout=5, max_wait=120):
    session.send("2\r")
    result = session.wait_for(r"enter.*to start|press.*enter", timeout=timeout, max_wait=max_wait)
    if not result.matched:
        return result
    session.send("\r")
    result = session.wait_for(r"press.*enter|c,.*f,.*r,.*or.*a", timeout=timeout, max_wait=max_wait)
    if not result.matched:
        return result
    if "press" in result.output.lower() and "c," not in result.output.lower():
        session.send("\r")
        result = session.wait_for(r"c,.*f,.*r,.*or.*a", timeout=timeout, max_wait=max_wait)
        if not result.matched:
            return result
    session.send(f"{'r' if reload else 'c'}\r")
    return session.wait_for(r"IRIX Release|Loading.*kernel|Obtaining.*from|Starting up the system", timeout=timeout, max_wait=max_wait)


def wait_for_installer(session, timeout=5, max_wait=600, bail_patterns=None):
    return session.wait_for(r"(Inst>|^#\s*$|inst>|miniroot>)", timeout=timeout, max_wait=max_wait, bail_on=IRIX_BAIL_PATTERNS + (bail_patterns or []))


def full_install_attempt(disk_path, cdrom_path=None, version=None, machine="indy", ram_mb=64, reload=False, **session_kwargs):
    import time as _time
    cdrom_list = [cdrom_path] if cdrom_path else IRIX_VERSIONS.get(version or "6.5", [DEFAULT_CDROM_65])
    scsi_drives = [disk_path] + [f"{cd}:cdrom" for cd in cdrom_list]
    start = _time.time()
    result_info = {"success": False, "transcript": "", "bail_reason": None, "duration": 0}
    try:
        with QEMUSession(machine=machine, ram_mb=ram_mb, scsi_drives=scsi_drives, **session_kwargs) as q:
            r = boot_to_prom_menu(q)
            if not r.matched:
                result_info.update(bail_reason=f"PROM menu: {r.bail_reason}", transcript=q.transcript, duration=_time.time()-start)
                return result_info
            r = install_miniroot(q, reload=reload)
            if not r.matched:
                result_info.update(bail_reason=f"miniroot boot: {r.bail_reason}", transcript=q.transcript, duration=_time.time()-start)
                return result_info
            r = wait_for_installer(q)
            result_info.update(success=r.matched, bail_reason=r.bail_reason, transcript=q.transcript, duration=_time.time()-start)
            if r.matched:
                try: q.save_snapshot("at_installer_prompt")
                except Exception: pass
    except Exception as e:
        result_info.update(bail_reason=f"exception: {e}", duration=_time.time()-start)
    return result_info


def iterate_from_snapshot(snapshot_name, disk_path, cdrom_path=None, version=None, machine="indy", ram_mb=64, **session_kwargs):
    import time as _time
    cdrom_list = [cdrom_path] if cdrom_path else IRIX_VERSIONS.get(version or "6.5", [DEFAULT_CDROM_65])
    scsi_drives = [disk_path] + [f"{cd}:cdrom" for cd in cdrom_list]
    start = _time.time()
    result_info = {"success": False, "transcript": "", "bail_reason": None, "duration": 0}
    try:
        with QEMUSession(machine=machine, ram_mb=ram_mb, scsi_drives=scsi_drives, snapshot=snapshot_name, **session_kwargs) as q:
            q.collect(duration=2)
            r = wait_for_installer(q)
            result_info.update(success=r.matched, bail_reason=r.bail_reason, transcript=q.transcript, duration=_time.time()-start)
    except Exception as e:
        result_info.update(bail_reason=f"exception: {e}", duration=_time.time()-start)
    return result_info
