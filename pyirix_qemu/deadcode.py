"""pyirix/deadcode.py — Archived dead/superseded code.

This file exists for reference only. Do not import from it.
Each section preserves the original source of a deprecated module
with a header explaining why it was retired.

Sections:
  1. irix_installer     — merged into pyirix/install/irix.py
  2. extract_all_cds    — superseded by pyirix/efs/extract.py
  3. install_mipspro    — superseded by harness_addon / install_addon()
  4. verify_mipspro     — one-off diagnostic, hardcoded /workspace paths
  5. dist_check         — overlaps with pyirix/dist/analyzer.py + dist/combine.py
"""

# ══════════════════════════════════════════════════════════════════════════════
# SECTION: irix_installer
# Retired: merged into pyirix/install/irix.py (full_install_attempt,
# iterate_from_snapshot, boot_to_prom_menu, install_miniroot,
# wait_for_installer)
# The functions boot_to_prom_menu, install_miniroot, wait_for_installer,
# full_install_attempt, and iterate_from_snapshot are now in install/irix.py.
# ══════════════════════════════════════════════════════════════════════════════

"""Automated IRIX install recipes using the boot harness.

Provides high-level functions for navigating PROM menus, booting the miniroot,
and reaching the installer prompt, with smart bail-on-error handling.
"""

import time
from pathlib import Path

from pyirix.boot_harness import QEMUSession, WaitResult, PROJECT_ROOT

_sw = PROJECT_ROOT / "software_library"

DEFAULT_CDROM_65 = str(
    _sw / "irix_6.5.22_images" / "IRIX 6.5 Installation Tools June 1998.img"
)
DEFAULT_CDROM_62 = str(
    _sw / "irix_6.2_images" / "IRIX 6.2 (Part 1 of 2) - 812-0469-001.efs.img"
)
DEFAULT_CDROM_62_PART2 = str(
    _sw / "irix_6.2_images" / "IRIX 6.2 (Part 2 of 2) - 812-0470-001.efs.img"
)
DEFAULT_CDROM_53 = str(
    _sw / "IRIX 5.3 All Indigo2 IMPACT - 812-0119-010.efs.img"
)
DEFAULT_FOUNDATION1_65 = str(
    _sw / "irix_6.5.22_images" / "IRIX 6.5 Foundation 1.img"
)

# Map version shorthand to CD-ROM path(s)
IRIX_VERSIONS = {
    "6.5": [DEFAULT_CDROM_65, DEFAULT_FOUNDATION1_65],
    "6.2": [DEFAULT_CDROM_62],
    "5.3": [DEFAULT_CDROM_53],
}

# Bail patterns specific to IRIX boot
IRIX_BAIL_PATTERNS = [
    r"Illegal field in CDB.*Illegal field in CDB",
    r"scsi bus reset",
    r"Cannot mount root",
    r"vfs_mountroot.*failed",
]


def boot_to_prom_menu(session, timeout=5, max_wait=60):
    """Wait for PROM System Maintenance Menu.

    Handles 'press any key' prompts if autoboot fails.

    Returns:
        WaitResult from the final wait.
    """
    # First try to catch the maintenance menu directly
    result = session.wait_for(
        r"(System Maintenance|Option\?|Enter.*to start)",
        timeout=timeout, max_wait=max_wait
    )

    if result.matched and "press" in result.output.lower():
        session.send("\r")
        result = session.wait_for(r"Option\?", timeout=timeout, max_wait=30)

    return result


def install_miniroot(session, reload=False, timeout=5, max_wait=120):
    """Navigate PROM installer flow to boot the miniroot kernel.

    Steps: Option 2 -> Enter (confirm) -> Enter (continue) -> c or r

    Args:
        session: Active QEMUSession.
        reload: If True, send 'r' to reload miniroot instead of 'c' for continue.
        timeout: Idle timeout for each step.
        max_wait: Max wait per step.

    Returns:
        WaitResult from the final step (kernel boot output begins).
    """
    # Select option 2: Install System Software
    session.send("2\r")
    result = session.wait_for(r"enter.*to start|press.*enter",
                              timeout=timeout, max_wait=max_wait)
    if not result.matched:
        return result

    # Press enter to start
    session.send("\r")
    result = session.wait_for(r"press.*enter|c,.*f,.*r,.*or.*a",
                              timeout=timeout, max_wait=max_wait)
    if not result.matched:
        return result

    # If we got another "press enter" prompt, send enter again
    if "press" in result.output.lower() and "c," not in result.output.lower():
        session.send("\r")
        result = session.wait_for(r"c,.*f,.*r,.*or.*a",
                                  timeout=timeout, max_wait=max_wait)
        if not result.matched:
            return result

    # Send c (continue) or r (reload)
    cmd = "r" if reload else "c"
    session.send(f"{cmd}\r")

    # Wait for kernel to start loading — look for IRIX kernel banner or loader output
    result = session.wait_for(
        r"IRIX Release|Loading.*kernel|Obtaining.*from|Starting up the system",
        timeout=timeout, max_wait=max_wait
    )
    return result


def wait_for_installer(session, timeout=5, max_wait=600,
                       bail_patterns=None):
    """Wait for installer prompt after miniroot boots.

    Waits for 'Inst>' or '#' shell prompt, auto-bailing on SCSI errors,
    panics, repeated lines, etc.

    Args:
        session: Active QEMUSession.
        timeout: Idle timeout.
        max_wait: Absolute max wait.
        bail_patterns: Additional bail patterns.

    Returns:
        WaitResult — matched=True if we reached a prompt.
    """
    extra_bail = IRIX_BAIL_PATTERNS + (bail_patterns or [])
    return session.wait_for(
        r"(Inst>|^#\s*$|inst>|miniroot>)",
        timeout=timeout, max_wait=max_wait,
        bail_on=extra_bail
    )


def full_install_attempt(disk_path, cdrom_path=None, version=None,
                         machine="indy", ram_mb=64, reload=False,
                         **session_kwargs):
    """Complete install attempt from disk to installer prompt.

    Args:
        disk_path: Path to disk image (qcow2 recommended for snapshots).
        cdrom_path: Path to IRIX install CD image. Overrides version.
        version: IRIX version shorthand ('6.5', '6.2', '5.3'). Default '6.5'.
        machine: QEMU machine type.
        ram_mb: RAM in MB.
        reload: If True, send 'r' to reload miniroot.
        **session_kwargs: Extra args passed to QEMUSession.

    Returns:
        dict with keys: success, transcript, bail_reason, duration
    """
    if cdrom_path:
        cdrom_list = [cdrom_path]
    else:
        cdrom_list = IRIX_VERSIONS.get(version or "6.5", [DEFAULT_CDROM_65])
    scsi_drives = [disk_path] + [f"{cd}:cdrom" for cd in cdrom_list]

    start = time.time()
    result_info = {
        "success": False,
        "transcript": "",
        "bail_reason": None,
        "duration": 0,
    }

    try:
        with QEMUSession(
            machine=machine, ram_mb=ram_mb,
            scsi_drives=scsi_drives, **session_kwargs
        ) as q:
            # Boot to PROM menu
            result = boot_to_prom_menu(q, timeout=5, max_wait=60)
            if not result.matched:
                result_info["bail_reason"] = f"PROM menu: {result.bail_reason}"
                result_info["transcript"] = q.transcript
                result_info["duration"] = time.time() - start
                return result_info

            # Navigate installer flow
            result = install_miniroot(q, reload=reload)
            if not result.matched:
                result_info["bail_reason"] = f"miniroot boot: {result.bail_reason}"
                result_info["transcript"] = q.transcript
                result_info["duration"] = time.time() - start
                return result_info

            # Wait for installer prompt
            result = wait_for_installer(q, timeout=5, max_wait=600)
            result_info["success"] = result.matched
            result_info["bail_reason"] = result.bail_reason
            result_info["transcript"] = q.transcript
            result_info["duration"] = time.time() - start

            # Save snapshot if we reached the installer
            if result.matched:
                try:
                    q.save_snapshot("at_installer_prompt")
                except Exception:
                    pass  # Snapshot is optional

    except Exception as e:
        result_info["bail_reason"] = f"exception: {e}"
        result_info["duration"] = time.time() - start

    return result_info


def iterate_from_snapshot(snapshot_name, disk_path, cdrom_path=None,
                          version=None, machine="indy", ram_mb=64,
                          **session_kwargs):
    """Resume from a saved snapshot and wait for installer prompt.

    Much faster than re-booting from PROM.

    Returns:
        dict with keys: success, transcript, bail_reason, duration
    """
    if cdrom_path:
        cdrom_list = [cdrom_path]
    else:
        cdrom_list = IRIX_VERSIONS.get(version or "6.5", [DEFAULT_CDROM_65])
    scsi_drives = [disk_path] + [f"{cd}:cdrom" for cd in cdrom_list]

    start = time.time()
    result_info = {
        "success": False,
        "transcript": "",
        "bail_reason": None,
        "duration": 0,
    }

    try:
        with QEMUSession(
            machine=machine, ram_mb=ram_mb,
            scsi_drives=scsi_drives,
            snapshot=snapshot_name,
            **session_kwargs
        ) as q:
            # Collect initial output after snapshot restore
            q.collect(duration=2)

            # Wait for installer prompt
            result = wait_for_installer(q, timeout=5, max_wait=600)
            result_info["success"] = result.matched
            result_info["bail_reason"] = result.bail_reason
            result_info["transcript"] = q.transcript
            result_info["duration"] = time.time() - start

    except Exception as e:
        result_info["bail_reason"] = f"exception: {e}"
        result_info["duration"] = time.time() - start

    return result_info


def main():
    """CLI entry point for IRIX install operations."""
    import argparse

    parser = argparse.ArgumentParser(
        description="IRIX install automation for QEMU SGI emulation"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # boot — full install attempt
    p_boot = sub.add_parser("boot", help="Boot and attempt to reach installer")
    p_boot.add_argument("--disk", default=str(PROJECT_ROOT / "irix_disk.qcow2"),
                        help="Disk image path")
    p_boot.add_argument("--cdrom", default=None,
                        help="CD-ROM image path (overrides --version)")
    p_boot.add_argument("--version", choices=["6.5", "6.2", "5.3"], default=None,
                        help="IRIX version (default: 6.5)")
    p_boot.add_argument("--machine", default="indy", help="Machine type")
    p_boot.add_argument("--ram", type=int, default=64, help="RAM in MB")
    p_boot.add_argument("--reload", action="store_true",
                        help="Send 'r' to reload miniroot instead of 'c'")

    # resume — from snapshot
    p_resume = sub.add_parser("resume", help="Resume from a saved snapshot")
    p_resume.add_argument("--snapshot", required=True, help="Snapshot name")
    p_resume.add_argument("--disk", default=str(PROJECT_ROOT / "irix_disk.qcow2"),
                          help="Disk image path")
    p_resume.add_argument("--cdrom", default=None,
                          help="CD-ROM image path (overrides --version)")
    p_resume.add_argument("--version", choices=["6.5", "6.2", "5.3"], default=None,
                          help="IRIX version (default: 6.5)")
    p_resume.add_argument("--machine", default="indy", help="Machine type")
    p_resume.add_argument("--ram", type=int, default=64, help="RAM in MB")

    args = parser.parse_args()

    if args.command == "boot":
        ver = args.version or ("custom" if args.cdrom else "6.5")
        print(f"Booting {args.machine} with IRIX {ver}, disk={args.disk}")
        result = full_install_attempt(
            disk_path=args.disk, cdrom_path=args.cdrom,
            version=args.version,
            machine=args.machine, ram_mb=args.ram, reload=args.reload
        )
        _print_result(result)

    elif args.command == "resume":
        print(f"Resuming from snapshot '{args.snapshot}'")
        result = iterate_from_snapshot(
            snapshot_name=args.snapshot,
            disk_path=args.disk, cdrom_path=args.cdrom,
            version=args.version,
            machine=args.machine, ram_mb=args.ram
        )
        _print_result(result)


def _print_result(result):
    """Print install attempt result."""
    status = "SUCCESS" if result["success"] else "FAILED"
    print(f"\n{'='*60}")
    print(f"Result: {status}")
    print(f"Duration: {result['duration']:.1f}s")
    if result["bail_reason"]:
        print(f"Bail reason: {result['bail_reason']}")
    print(f"Transcript length: {len(result['transcript'])} chars")
    print(f"{'='*60}")

    # Show last 40 lines of transcript
    lines = result["transcript"].split("\n")
    if len(lines) > 40:
        print(f"\n... ({len(lines) - 40} lines omitted) ...\n")
        lines = lines[-40:]
    for line in lines:
        print(line)


if __name__ == "__main__":
    main()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION: extract_all_cds
# Retired: superseded by pyirix/efs/extract.py (extract_cd_set.py); targets
# irix65_dist_staging/ which is an older staging directory
# The functions extract_with_efs_reader, extract_with_efsextract, check_status,
# and main are now handled by pyirix/efs/extract.py.
# ══════════════════════════════════════════════════════════════════════════════

#!/usr/bin/env python3
"""Extract dist/ directories from all IRIX 6.5 CD images.

Uses tools/efs_reader.py for EFS images (Applications, InstTools) and
efsextract as a fallback. Foundation and Overlay CDs are ISO 9660 format
and are handled by efsextract.

Usage:
    python3 tools/extract_all_cds.py
    python3 tools/extract_all_cds.py --check     # Just verify what exists
"""

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IMAGES_DIR = PROJECT_ROOT / "software_library" / "irix_6.5.22_images"
EXTRACT_BASE = PROJECT_ROOT / "software_library" / "irix65_dist_staging"
EFSEXTRACT = PROJECT_ROOT / "tools" / "efsextract" / "efsextract"

# CD definitions: (image_filename, extract_dirname, is_efs)
# is_efs=True means SGI disk label + EFS partition
# is_efs=False means ISO 9660 (handled by efsextract -L detection)
CDS = [
    ("IRIX 6.5 Foundation 1.img",
     "6.5-foundation-1", True),
    ("IRIX 6.5 Foundation 2.img",
     "6.5-foundation-2", True),
    ("IRIX 6.5.22 Overlays 1 of 3.img",
     "irix-6.5.22-overlay-1", True),
    ("IRIX 6.5.22 Overlays 2 of 3.img",
     "irix-6.5.22-overlay-2", True),
    ("IRIX 6.5.22 Overlays 3 of 3.img",
     "irix-6.5.22-overlay-3", True),
    ("SGI IRIX 6.5 Applications 2004 April.img",
     "6.5-applications-2004", True),
]


def extract_with_efs_reader(image_path, dest_dir):
    """Extract dist/ from an EFS disk image using our Python reader."""
    from pyirix.efs.reader import (find_efs_partition, read_superblock,
                                   extract_recursive, EFS_ROOT_INODE)

    with open(image_path, 'rb') as f:
        result = find_efs_partition(f)
        if not result:
            print(f"  No EFS partition found, trying efsextract...")
            return extract_with_efsextract(image_path, dest_dir)

        part_offset, part_size = result
        sb = read_superblock(f, part_offset)
        if not sb:
            print(f"  Invalid EFS superblock, trying efsextract...")
            return extract_with_efsextract(image_path, dest_dir)

        os.makedirs(dest_dir, exist_ok=True)
        stats = extract_recursive(f, part_offset, sb, EFS_ROOT_INODE,
                                  '/', str(dest_dir), path_filter='dist')
        return stats['files'] + stats['symlinks']


def extract_with_efsextract(image_path, dest_dir):
    """Extract from a disk image using the efsextract C tool."""
    if not EFSEXTRACT.exists():
        print(f"  ERROR: efsextract not found at {EFSEXTRACT}")
        return 0

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            [str(EFSEXTRACT), str(image_path)],
            cwd=tmpdir, capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"  efsextract failed: {result.stderr}")
            return 0

        # efsextract extracts everything into CWD
        src_dist = Path(tmpdir) / "dist"
        if not src_dist.exists():
            print(f"  No dist/ directory found in extraction")
            return 0

        # Move dist/ to destination
        os.makedirs(dest_dir, exist_ok=True)
        dest_dist = Path(dest_dir) / "dist"
        if dest_dist.exists():
            import shutil
            shutil.rmtree(dest_dist)

        import shutil
        shutil.copytree(str(src_dist), str(dest_dist), symlinks=True)

        count = sum(1 for _ in dest_dist.rglob('*') if _.is_file())
        return count


def check_status():
    """Check which CDs are already extracted."""
    print("CD extraction status:")
    for img_name, dir_name, is_efs in CDS:
        img_path = IMAGES_DIR / img_name
        dest_dir = EXTRACT_BASE / dir_name
        dist_dir = dest_dir / "dist"

        img_exists = img_path.exists()
        dist_exists = dist_dir.exists()
        file_count = 0
        if dist_exists:
            file_count = sum(1 for f in dist_dir.iterdir() if f.is_file())

        status = "OK" if file_count > 0 else "MISSING"
        img_status = "" if img_exists else " [IMAGE NOT FOUND]"

        print(f"  [{status:7s}] {dir_name:30s} "
              f"files={file_count:4d}{img_status}")


def main():
    if "--check" in sys.argv:
        check_status()
        return

    print(f"Extracting IRIX 6.5 CDs to {EXTRACT_BASE}")
    print()

    for img_name, dir_name, is_efs in CDS:
        img_path = IMAGES_DIR / img_name
        dest_dir = EXTRACT_BASE / dir_name
        dist_dir = dest_dir / "dist"

        if not img_path.exists():
            print(f"SKIP {dir_name}: image not found at {img_path}")
            continue

        # Check if already extracted
        if dist_dir.exists():
            file_count = sum(1 for f in dist_dir.iterdir() if f.is_file())
            if file_count > 10:
                print(f"SKIP {dir_name}: already extracted ({file_count} files)")
                continue

        print(f"Extracting {dir_name}...")
        print(f"  Image: {img_path}")
        print(f"  Dest:  {dest_dir}")

        count = extract_with_efs_reader(str(img_path), str(dest_dir))
        print(f"  Extracted {count} files")
        print()

    print("Done. Run with --check to verify.")


if __name__ == '__main__':
    main()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION: install_mipspro
# Retired: superseded by harness_addon / pyirix.install.irix.install_addon();
# also hardcodes sys.path.insert(0, '/workspace')
# The install flow (inst admin/pager/relnotes/from/go) is now driven by
# install_addon() in pyirix/install/irix.py via the MCP harness_addon tool.
# ══════════════════════════════════════════════════════════════════════════════

#!/usr/bin/env python3
"""Install MIPSPro dev tools onto an existing IRIX 6.5.5 base disk.

v4 - The correct approach:
1. Start inst WITHOUT -f (no dist loaded = no release notes trigger)
2. Enter Admin menu, disable relnotes and pager
3. Return to main menu, THEN load dist with 'from'
4. Select packages and go
5. With pager disabled, no more character-bleeding issues
"""

import os
import re
import signal
import subprocess
import sys
import time

from pyirix.boot_harness import QEMUSession

MIPSPRO_DISK = 'prebuilt_disks/irix-6.5.5-mipspro.qcow2'
MIPSPRO_DIST = ('software_library/prepackaged_combo_discs/'
                'MIPSpro_7.4_and_Development_Libraries_combined_dist.img')

SHELL_PROMPT = r'IRIS\s+\d+#'


def cleanup_stale():
    try:
        result = subprocess.run(['pgrep', '-f', 'qemu.*mips'],
                                capture_output=True, text=True)
        pids = [int(p) for p in result.stdout.strip().split('\n') if p.strip()]
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
                log(f"  Killed stale pid {pid}")
            except ProcessLookupError:
                pass
        if pids:
            time.sleep(2)
    except Exception:
        pass


def log(msg):
    print(f"[mipspro] {msg}", flush=True)


def clean(text):
    return re.sub(r'\x1b\[[^a-zA-Z]*[a-zA-Z]', '', text).replace('\r', '').strip()


def wait_prompt(q, prompt_re, timeout=10, max_wait=60):
    """Wait for a specific prompt, handling pagers and common prompts."""
    for _ in range(50):
        r = q.wait_for(
            prompt_re + r'|more\?|Install software from|'
            r'enter a choice|Please enter|Interrupt>|'
            r'really want to quit|already been opened',
            timeout=timeout, max_wait=max_wait
        )
        if not r.matched:
            return r
        if 'more?' in r.output:
            q.send(' ')  # SPACE to page (safe everywhere)
            time.sleep(0.5)
            continue
        if 'Install software from' in r.output:
            q.send('done\r')
            time.sleep(0.5)
            continue
        if 'enter a choice' in r.output or 'Please enter' in r.output:
            q.send('2\r')
            time.sleep(0.3)
            continue
        if 'Interrupt>' in r.output:
            q.send('3\r')
            time.sleep(0.3)
            continue
        if 'really want to quit' in r.output:
            q.send('no\r')
            time.sleep(0.3)
            continue
        if 'already been opened' in r.output:
            q.send('yes\r')
            time.sleep(0.3)
            continue
        return r
    return r


def main():
    cleanup_stale()
    log("=== MIPSPro Install v4 ===")

    # Fresh disk copy
    log("  Copying fresh base disk...")
    subprocess.run(['cp', '/workspace/prebuilt_disks/irix-6.5.5-base.qcow2',
                    '/workspace/' + MIPSPRO_DISK], check=True)

    with QEMUSession(
        machine='indy', ram_mb=256,
        scsi_drives=[MIPSPRO_DISK, MIPSPRO_DIST],
        repeat_threshold=0,
    ) as q:
        # Boot
        r = q.wait_for(r'Option\?', timeout=5, max_wait=180)
        if not r.matched:
            log("FATAL: PROM not reached")
            return False
        log("  PROM menu")
        q.send('1\r')

        r = q.wait_for(r'login:', timeout=60, max_wait=1200)
        if not r.matched:
            log("FATAL: Login not reached")
            return False
        log("  Login prompt")
        q.send('root\r')
        r = q.wait_for(r'TERM|' + SHELL_PROMPT, timeout=10, max_wait=60)
        if 'TERM' in r.output:
            q.send('\r')
            q.wait_for(SHELL_PROMPT, timeout=5, max_wait=60)

        time.sleep(5)
        q.send('\r')
        q.wait_for(SHELL_PROMPT, timeout=5, max_wait=30)
        log("  Shell ready")

        # Mount dist
        q.send('mkdir -p /mnt && mount -r /dev/dsk/dks0d2s7 /mnt\r')
        q.wait_for(SHELL_PROMPT, timeout=10, max_wait=30)
        log("  Dist mounted")

        # Start inst WITHOUT -f (no dist = no release notes trigger)
        log("  Starting inst (no dist)...")
        q.send('inst\r')
        r = wait_prompt(q, r'Inst>', timeout=15, max_wait=60)
        if 'Inst>' not in r.output:
            log("FATAL: inst not started")
            return False
        log("  Inst> reached")
        time.sleep(1)

        # Enter Admin menu to disable pager and release notes
        log("  Configuring admin settings...")
        q.send('admin\r')
        r = wait_prompt(q, r'Admin>', timeout=10, max_wait=30)
        if 'Admin>' not in r.output:
            log("WARNING: Admin> not reached, trying anyway")

        time.sleep(0.5)
        q.send('config relnotes off\r')
        r = wait_prompt(q, r'Admin>', timeout=10, max_wait=30)
        log("    relnotes off")

        time.sleep(0.5)
        q.send('config pager off\r')
        r = wait_prompt(q, r'Admin>', timeout=10, max_wait=30)
        log("    pager off")

        time.sleep(0.5)
        q.send('return\r')
        r = wait_prompt(q, r'Inst>', timeout=10, max_wait=30)
        log("  Back at Inst>")
        time.sleep(0.5)

        # NOW load the distribution (with pager/relnotes disabled!)
        log("  Loading distribution: from /mnt/dist")
        q.send('from /mnt/dist\r')
        # With relnotes off, this should just load and return to Inst>
        # But 'from' may trigger "Install software from:" overlay prompt
        r = wait_prompt(q, r'Inst>', timeout=60, max_wait=300)
        log("  Distribution loaded")
        time.sleep(1)

        # Select packages
        packages = 'c_dev c++_dev compiler_dev c_fe c++_fe compiler_eoe dev irix_dev'
        log(f"  Selecting: install {packages}")
        q.send(f'install {packages}\r')
        r = wait_prompt(q, r'Inst>', timeout=30, max_wait=120)
        log("  Packages selected")
        time.sleep(0.5)

        # Verify selection with list
        log("  Verifying with list...")
        q.send('list\r')
        r = wait_prompt(q, r'Inst>', timeout=30, max_wait=120)
        list_out = r.output
        install_count = 0
        for line in list_out.split('\n'):
            c = clean(line)
            if c and re.match(r'^[iI]\s', c):
                install_count += 1
        log(f"  {install_count} items marked for install")

        if install_count == 0:
            # Show list output for debugging
            for line in list_out.split('\n')[:20]:
                c = clean(line)
                if c and 'type' not in c.lower() and 'menu' not in c.lower():
                    log(f"    LIST| {c[:80]}")

            # Try install * as fallback
            log("  Trying install * as fallback...")
            q.send('install *\r')
            r = wait_prompt(q, r'Inst>', timeout=30, max_wait=120)

            q.send('list\r')
            r = wait_prompt(q, r'Inst>', timeout=30, max_wait=120)
            install_count = 0
            for line in r.output.split('\n'):
                c = clean(line)
                if c and re.match(r'^[iI]\s', c):
                    install_count += 1
            log(f"  After install *: {install_count} items")

        # Resolve conflicts
        log("  Resolving conflicts...")
        for round_num in range(50):
            time.sleep(0.3)
            q.send('conflicts 1a\r')
            r = wait_prompt(q, r'Inst>', timeout=10, max_wait=30)
            out = r.output.lower()
            if 'no conflict' in out or 'invalid' in out:
                log(f"    No conflicts (round {round_num})")
                break
            for m in re.finditer(r'Do not install\s+(\S+)', r.output):
                log(f"    Skipped: {m.group(1)}")

        # Run go
        log("  *** Running go ***")
        time.sleep(0.5)
        q.send('go\r')

        # Wait for go to complete (very long timeouts)
        install_started = False
        install_success = False
        for i in range(200):
            r = q.wait_for(
                r'Inst>|Installations.*successful|Installing|'
                r'Pre-installation|Resolve conflicts|Conflicts must|'
                r'no changes|Nothing to install|Nothing selected|'
                r'Install software from|Reading|Upgrading|Removing|'
                r'Checking|Interrupt>|enter a choice|'
                r'really want to quit',
                timeout=120, max_wait=3600
            )
            if not r.matched:
                log(f"  go step {i}: timeout")
                break

            if 'Interrupt>' in r.output:
                log(f"  go step {i}: post-install error, continuing")
                q.send('3\r')
                continue
            if 'enter a choice' in r.output:
                q.send('2\r')
                continue
            if 'really want to quit' in r.output:
                q.send('no\r')
                continue
            if 'Install software from' in r.output:
                q.send('done\r')
                time.sleep(0.3)
                continue

            if 'Resolve conflicts' in r.output or \
               'Conflicts must' in r.output:
                log(f"  go step {i}: conflicts, resolving...")
                wait_prompt(q, r'Inst>', timeout=5, max_wait=30)
                for rnd in range(30):
                    q.send('conflicts 1a\r')
                    r2 = wait_prompt(q, r'Inst>', timeout=10, max_wait=30)
                    if 'no conflict' in r2.output.lower() or \
                       'invalid' in r2.output.lower():
                        break
                q.send('go\r')
                log("    Retrying go...")
                continue

            if 'no changes' in r.output or 'Nothing' in r.output:
                log(f"  go step {i}: NOTHING TO INSTALL")
                break

            if 'Installing' in r.output and 'Installations' not in r.output:
                if not install_started:
                    log(f"  go step {i}: INSTALLING!")
                    install_started = True
                pct = re.search(r'(\d+)%', r.output)
                if pct:
                    log(f"    Progress: {pct.group(1)}%")
                continue

            if 'Upgrading' in r.output or 'Removing' in r.output or \
               'Reading' in r.output or 'Pre-installation' in r.output or \
               'Checking' in r.output:
                if not install_started:
                    log(f"  go step {i}: pre-install/reading")
                    install_started = True
                continue

            if 'Installations' in r.output and 'successful' in r.output.lower():
                log(f"  go step {i}: *** SUCCESSFUL ***")
                install_success = True
                break

            if 'Inst>' in r.output:
                if install_started:
                    log(f"  go step {i}: back at Inst> after install")
                    install_success = True
                else:
                    log(f"  go step {i}: back at Inst> (no install)")
                    # Show why
                    for line in r.output.split('\n')[-8:]:
                        c = clean(line)
                        if c and 'Inst>' not in c:
                            log(f"    | {c[:80]}")
                break

        if not install_success and not install_started:
            # Last resort: quit triggers install
            log("  Trying quit method (inst installs on quit)...")
            q.send('quit\r')
            for _ in range(200):
                r = q.wait_for(
                    r'really want to quit|Installations.*successful|Installing|'
                    r'Upgrading|Pre-installation|Checking|Reading|Removing|'
                    r'Ready to restart|Restart|Interrupt>|' + SHELL_PROMPT,
                    timeout=120, max_wait=3600
                )
                if not r.matched:
                    log("  quit: timeout")
                    break
                if 'really' in r.output:
                    q.send('yes\r')
                    continue
                if 'Interrupt>' in r.output:
                    q.send('3\r')
                    continue
                if 'Installing' in r.output and 'Installations' not in r.output:
                    if not install_started:
                        log("  quit: INSTALLING!")
                        install_started = True
                    pct = re.search(r'(\d+)%', r.output)
                    if pct:
                        log(f"    quit progress: {pct.group(1)}%")
                    continue
                if 'Upgrading' in r.output or 'Pre-installation' in r.output or \
                   'Checking' in r.output or 'Reading' in r.output or \
                   'Removing' in r.output:
                    if not install_started:
                        install_started = True
                    continue
                if 'Installations' in r.output and 'successful' in r.output.lower():
                    log("  quit: *** SUCCESSFUL ***")
                    install_success = True
                    continue  # Keep waiting for shell prompt
                if 'Ready to restart' in r.output or 'Restart' in r.output:
                    q.send('\r')
                    continue
                if re.search(SHELL_PROMPT, r.output):
                    log("  quit: at shell prompt")
                    break
        else:
            # Normal quit after successful go
            log("  Quitting inst...")
            q.send('quit\r')
            for _ in range(30):
                r = q.wait_for(
                    r'really want to quit|Installations.*successful|'
                    r'Ready to restart|Restart|Inst>|' + SHELL_PROMPT + r'|'
                    r'Installing|Upgrading|Interrupt>',
                    timeout=120, max_wait=3600
                )
                if not r.matched:
                    break
                if 'really' in r.output:
                    q.send('yes\r')
                    continue
                if 'Interrupt>' in r.output:
                    q.send('3\r')
                    continue
                if 'Installing' in r.output or 'Upgrading' in r.output:
                    continue
                if 'Installations' in r.output:
                    continue
                if 'Restart' in r.output or 'Ready' in r.output:
                    q.send('\r')
                    continue
                if 'Inst>' in r.output:
                    q.send('quit\r')
                    continue
                if re.search(SHELL_PROMPT, r.output):
                    break

        # Make sure we're at shell prompt
        if not re.search(SHELL_PROMPT, r.output):
            q.wait_for(SHELL_PROMPT, timeout=120, max_wait=600)
        log("  At shell prompt")

        # Verify
        time.sleep(2)
        q.send('\r')
        q.wait_for(SHELL_PROMPT, timeout=5, max_wait=15)

        q.send('cc -version 2>&1\r')
        r = q.wait_for(SHELL_PROMPT, timeout=10, max_wait=30)
        log(f"  cc: {clean(r.output)[:200]}")

        q.send('which cc 2>&1\r')
        r = q.wait_for(SHELL_PROMPT, timeout=10, max_wait=30)
        log(f"  which cc: {clean(r.output)[:100]}")

        q.send('df -k /\r')
        r = q.wait_for(SHELL_PROMPT, timeout=10, max_wait=30)
        log(f"  df: {clean(r.output)[:200]}")

        q.send('sync; sync; halt\r')
        time.sleep(5)

    # Phase 2: Snapshot
    cleanup_stale()
    log("=== Phase 2: Save snapshot ===")

    with QEMUSession(
        machine='indy', ram_mb=256,
        scsi_drives=[MIPSPRO_DISK],
        extra_args=['-icount', 'shift=0,sleep=off'],
        repeat_threshold=0,
    ) as q:
        r = q.wait_for(r'Option\?', timeout=5, max_wait=180)
        if not r.matched:
            log("FATAL: Phase 2 PROM failed")
            return False
        q.send('1\r')

        r = q.wait_for(r'login:', timeout=30, max_wait=600)
        if not r.matched:
            log("FATAL: Phase 2 login failed")
            return False
        q.send('root\r')
        r = q.wait_for(r'TERM|' + SHELL_PROMPT, timeout=10, max_wait=60)
        if 'TERM' in r.output:
            q.send('\r')
            q.wait_for(SHELL_PROMPT, timeout=5, max_wait=30)

        time.sleep(3)
        q.send('\r')
        q.wait_for(SHELL_PROMPT, timeout=5, max_wait=30)

        q.save_snapshot('irix655_mipspro')
        log("  Snapshot saved!")

        q.send('cc -version 2>&1\r')
        r = q.wait_for(SHELL_PROMPT, timeout=5, max_wait=15)
        log(f"  VERIFY cc: {clean(r.output)[:200]}")

        q.send('df -k /\r')
        r = q.wait_for(SHELL_PROMPT, timeout=5, max_wait=15)
        log(f"  VERIFY df: {clean(r.output)[:200]}")

    log("=== MIPSPro installation complete! ===")
    return True


if __name__ == '__main__':
    try:
        success = main()
    except Exception as e:
        log(f"EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        success = False
    sys.exit(0 if success else 1)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION: verify_mipspro
# Retired: one-off diagnostic script; hardcodes /workspace paths and
# prebuilt_disks/irix-6.5.5-mipspro.qcow2
# Verification is now done inline after harness_addon() or via the MCP
# qemu_session_send tool with the running session.
# ══════════════════════════════════════════════════════════════════════════════

#!/usr/bin/env python3
"""Quick verification that MIPSPro cc works on the installed disk."""

import sys
import time

from pyirix.boot_harness import QEMUSession

DISK = 'prebuilt_disks/irix-6.5.5-mipspro.qcow2'


def log(msg):
    print(f"[verify] {msg}", flush=True)


def run_cmd(q, cmd, wait=3):
    """Send a command and capture output until # prompt."""
    q.send(cmd + '\r')
    time.sleep(wait)
    r = q.wait_for(r'#', timeout=10, max_wait=30)
    return r.output


def main():
    log("Booting from snapshot...")
    with QEMUSession(
        machine='indy', ram_mb=256,
        scsi_drives=[DISK],
        extra_args=['-icount', 'shift=0,sleep=off'],
        snapshot='irix655_mipspro',
        repeat_threshold=0,
    ) as q:
        log("Snapshot loaded, waiting for prompt...")
        time.sleep(5)
        q.send('\r')
        r = q.wait_for(r'#', timeout=10, max_wait=60)
        log(f"  Initial output: {repr(r.output[-200:])}")
        log(f"  Matched: {r.matched}")

        if not r.matched:
            # Try login
            log("  Trying login...")
            r = q.wait_for(r'login:|#', timeout=60, max_wait=300)
            log(f"  Got: {repr(r.output[-200:])}")
            if 'login' in r.output:
                q.send('root\r')
                r = q.wait_for(r'TERM|#', timeout=15, max_wait=60)
                if 'TERM' in r.output:
                    q.send('\r')
                r = q.wait_for(r'#', timeout=10, max_wait=60)
            time.sleep(3)
            q.send('\r')
            r = q.wait_for(r'#', timeout=10, max_wait=30)

        log("Shell ready!")

        # Run verification commands
        out = run_cmd(q, 'uname -a')
        log(f"uname: {out.strip()}")

        out = run_cmd(q, 'cc -version 2>&1')
        log(f"cc -version: {out.strip()}")

        out = run_cmd(q, 'which cc 2>&1')
        log(f"which cc: {out.strip()}")

        out = run_cmd(q, 'ls -la /usr/bin/cc 2>&1')
        log(f"ls cc: {out.strip()}")

        out = run_cmd(q, 'versions c_dev c_fe compiler_eoe 2>&1')
        log(f"versions: {out.strip()}")

        # Try compile
        run_cmd(q, "echo 'int main(){return 0;}' > /tmp/test.c")
        out = run_cmd(q, 'cc -o /tmp/test /tmp/test.c 2>&1; echo RC=$?', wait=5)
        log(f"compile: {out.strip()}")

        out = run_cmd(q, 'df -k /')
        log(f"df: {out.strip()}")

    log("Done!")


if __name__ == '__main__':
    main()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION: dist_check
# Retired: overlaps with pyirix/dist/analyzer.py + pyirix/dist/combine.py;
# not on active harness_install call path
# The extraction logic moved to pyirix/efs/extract.py; the IDB parsing and
# conflict/overlap analysis live in pyirix/dist/analyzer.py; the combining
# pipeline is in pyirix/dist/combine.py.
# ══════════════════════════════════════════════════════════════════════════════

#!/usr/bin/env python3
"""Extract IRIX CD images and analyze distribution content in one step.

Takes arbitrary image paths (.img, .iso, .efs.img, .tar, .tar.gz),
extracts them, discovers all dist content by scanning for .idb files,
and runs conflict analysis filtered by target IRIX version.

Usage:
    # Extract + analyze
    python3 tools/dist_check.py \\
        "software_library/IRIX 6.5 Foundation 1.img" \\
        "software_library/dev CDs/mipspro/MIPSpro C++ Compiler 7.4 - 812-0400-010.efs.img"

    # With version filtering and persistent target
    python3 tools/dist_check.py \\
        --version 6.5 --platform ip24 --target /tmp/my_extraction \\
        path1.img path2.tar.gz path3.iso

    # Analyze already-extracted directory (no images needed)
    python3 tools/dist_check.py --target /path/to/already/extracted
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

# Add tools/ to path for sibling imports
TOOLS_DIR = Path(__file__).resolve().parent

from pyirix.efs.reader import (
    find_efs_partition, read_superblock, extract_recursive, EFS_ROOT_INODE,
)
from pyirix.dist.analyzer import parse_idb_subsystems


# ── Phase 1: Extraction ─────────────────────────────────────────────

# Compound extensions to strip when deriving subdir names, longest first
COMPOUND_EXTENSIONS = ['.efs.img', '.tar.gz', '.iso', '.img', '.tgz', '.tar']


def derive_subdir_name(image_path):
    """Derive a clean subdirectory name from an image filename.

    Strips compound extensions and replaces spaces/problematic chars
    with underscores.
    """
    name = Path(image_path).name
    name_lower = name.lower()
    for ext in COMPOUND_EXTENSIONS:
        if name_lower.endswith(ext):
            name = name[:-len(ext)]
            break
    # Replace spaces and other problematic characters
    name = re.sub(r'[^\w.-]', '_', name)
    # Collapse multiple underscores
    name = re.sub(r'_+', '_', name).strip('_')
    return name


def extract_efs_image(image_path, dest_dir):
    """Extract an EFS image into dest_dir.

    Returns (ok, file_count) tuple.
    """
    try:
        with open(image_path, 'rb') as f:
            result = find_efs_partition(f)
            if not result:
                return False, 0

            part_offset, part_size = result
            sb = read_superblock(f, part_offset)
            if not sb:
                return False, 0

            dest_dir.mkdir(parents=True, exist_ok=True)
            stats = extract_recursive(f, part_offset, sb, EFS_ROOT_INODE,
                                      '/', str(dest_dir))
            total = stats['files'] + stats['symlinks']
            return stats['errors'] == 0, total
    except Exception as e:
        print(f"    Error reading {image_path}: {e}", file=sys.stderr)
        return False, 0


def extract_tar_gz(image_path, dest_dir):
    """Extract a .tar.gz or .tgz archive into dest_dir.

    Returns (ok, file_count) tuple.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["tar", "xzf", str(image_path), "-C", str(dest_dir)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"    tar error: {result.stderr.strip()}", file=sys.stderr)
        return False, 0

    count = sum(1 for _ in dest_dir.rglob('*') if _.is_file())
    return True, count


def extract_tar(image_path, dest_dir):
    """Extract a plain .tar archive into dest_dir, auto-flattening nesting.

    If the tar extracts into exactly one top-level directory, move its
    contents up to dest_dir (removes unnecessary nesting level).

    Returns (ok, file_count) tuple.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["tar", "xf", str(image_path), "-C", str(dest_dir)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"    tar error: {result.stderr.strip()}", file=sys.stderr)
        return False, 0

    # Auto-flatten: if exactly one top-level directory, move its contents up
    top_entries = list(dest_dir.iterdir())
    if len(top_entries) == 1 and top_entries[0].is_dir():
        nested_dir = top_entries[0]
        # Move all children up
        for child in list(nested_dir.iterdir()):
            child.rename(dest_dir / child.name)
        nested_dir.rmdir()

    count = sum(1 for _ in dest_dir.rglob('*') if _.is_file())
    return True, count


def extract_iso9660(image_path, dest_dir):
    """Extract an ISO 9660 image by mounting it (macOS hdiutil).

    Falls back to 7z if hdiutil is unavailable.
    Returns (ok, file_count) tuple.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    mount_point = None
    try:
        # Try hdiutil (macOS)
        mount_point = tempfile.mkdtemp(prefix='iso_mount_')
        result = subprocess.run(
            ["hdiutil", "attach", str(image_path),
             "-readonly", "-nobrowse", "-mountpoint", mount_point],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            # hdiutil failed — try 7z as fallback
            os.rmdir(mount_point)
            mount_point = None
            result = subprocess.run(
                ["7z", "x", f"-o{dest_dir}", str(image_path)],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                print(f"    ISO extract failed (no hdiutil/7z)",
                      file=sys.stderr)
                return False, 0
            count = sum(1 for _ in dest_dir.rglob('*') if _.is_file())
            return True, count

        # hdiutil succeeded — copy contents from mount point
        for item in Path(mount_point).iterdir():
            src = str(item)
            dst = str(dest_dir / item.name)
            if item.is_dir():
                shutil.copytree(src, dst, symlinks=True,
                                dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)

        count = sum(1 for _ in dest_dir.rglob('*') if _.is_file())
        return True, count

    except FileNotFoundError:
        print(f"    ISO extract failed (hdiutil/7z not found)",
              file=sys.stderr)
        return False, 0
    except subprocess.TimeoutExpired:
        print(f"    ISO mount timed out", file=sys.stderr)
        return False, 0
    finally:
        if mount_point and Path(mount_point).is_mount():
            subprocess.run(["hdiutil", "detach", mount_point, "-quiet"],
                           capture_output=True, timeout=15)
        if mount_point and Path(mount_point).exists():
            try:
                os.rmdir(mount_point)
            except OSError:
                pass


def detect_format(image_path):
    """Auto-detect image format from filename.

    Returns one of: 'tar.gz', 'tar', 'efs'
    """
    name_lower = str(image_path).lower()
    if name_lower.endswith('.tar.gz') or name_lower.endswith('.tgz'):
        return 'tar.gz'
    if name_lower.endswith('.tar'):
        return 'tar'
    # Everything else (.img, .iso, .efs.img) — try as EFS first,
    # with ISO 9660 fallback for .iso files
    return 'efs'


def extract_image(image_path, target_dir):
    """Extract a single image into a subdirectory of target_dir.

    Returns (subdir_name, format, ok, file_count).
    """
    subdir_name = derive_subdir_name(image_path)
    dest_dir = Path(target_dir) / subdir_name

    # Skip if already has files
    if dest_dir.exists() and any(dest_dir.rglob('*')):
        count = sum(1 for _ in dest_dir.rglob('*') if _.is_file())
        return subdir_name, 'cached', True, count

    fmt = detect_format(image_path)
    if fmt == 'tar.gz':
        ok, count = extract_tar_gz(image_path, dest_dir)
    elif fmt == 'tar':
        ok, count = extract_tar(image_path, dest_dir)
    else:
        ok, count = extract_efs_image(image_path, dest_dir)
        if not ok:
            # EFS failed — try ISO 9660 for .iso files (or any image)
            ok, count = extract_iso9660(image_path, dest_dir)
            if ok:
                fmt = 'iso'
            else:
                fmt = 'efs?'

    return subdir_name, fmt.upper(), ok, count


def run_extraction(image_paths, target_dir):
    """Phase 1: Extract all images into target_dir.

    Returns list of (index, subdir_name, format, ok, file_count).
    """
    results = []
    for i, img_path in enumerate(image_paths, 1):
        img = Path(img_path)
        if not img.exists():
            print(f"  [{i}] {img.name}  NOT FOUND", file=sys.stderr)
            results.append((i, img.name, '???', False, 0))
            continue

        subdir_name, fmt, ok, count = extract_image(img, target_dir)
        results.append((i, subdir_name, fmt, ok, count))

    return results


# ── Phase 2: Discover dist content ──────────────────────────────────

def discover_dist_locations(target_dir, version_filter=None):
    """Walk extraction directory, find every directory containing .idb files.

    Returns list of (cd_name, dist_path, classification, idb_count).
    """
    target = Path(target_dir)
    if not target.exists():
        return []

    # Collect all dirs containing .idb files
    raw_locations = []  # (cd_name, abs_path, idb_count)
    for root, dirs, files in os.walk(target):
        idb_files = [f for f in files if f.endswith('.idb')]
        if not idb_files:
            continue

        root_path = Path(root)
        # cd_name = first path component under target
        try:
            rel = root_path.relative_to(target)
        except ValueError:
            continue
        parts = rel.parts
        if not parts:
            continue
        cd_name = parts[0]

        raw_locations.append((cd_name, root_path, len(idb_files)))

    # Version filtering: exclude dirs named dist6.X where X doesn't match
    excluded_versions = set()
    if version_filter:
        filtered = []
        for cd_name, dist_path, idb_count in raw_locations:
            dirname = dist_path.name
            # Match dist6.X patterns
            m = re.match(r'^dist(\d+\.\d+)$', dirname)
            if m:
                dist_ver = m.group(1)
                if not version_filter.startswith(dist_ver):
                    excluded_versions.add(dirname)
                    continue
            filtered.append((cd_name, dist_path, idb_count))
        raw_locations = filtered

    # Dedup pass 1: if both cd_dir/ root AND cd_dir/dist/ have .idb files,
    # prefer cd_dir/dist/ and skip root
    deduped = {}
    for cd_name, dist_path, idb_count in raw_locations:
        try:
            rel = dist_path.relative_to(target)
        except ValueError:
            continue

        key = (cd_name, dist_path)
        # Check if this is a root-level match that has a dist/ child
        rel_parts = rel.parts
        if len(rel_parts) == 1:
            # This is the CD root — check if there's also a dist/ subdir
            child_dist = dist_path / 'dist'
            if any(cd == cd_name and dp == child_dist
                   for cd, dp, _ in raw_locations):
                continue  # Skip root, prefer dist/
        deduped[key] = (cd_name, dist_path, idb_count)

    # Dedup pass 2: SGI CDs often have both cd/dist6.X/ and cd/dist/dist6.X/
    # with identical content. When both exist with the same file listing,
    # keep only the one inside dist/ to avoid double-counting.
    to_remove = set()
    for (cd_name, dist_path) in list(deduped.keys()):
        rel = dist_path.relative_to(target)
        parts = rel.parts
        # Look for pattern: cd_name/dist6.X (depth 2, sibling of dist/)
        if len(parts) == 2 and re.match(r'^dist\d', parts[1]):
            # Check if cd_name/dist/dist6.X also exists
            nested = target / parts[0] / 'dist' / parts[1]
            nested_key = (cd_name, nested)
            if nested_key in deduped:
                # Both exist — compare file listings
                root_files = set(f.name for f in dist_path.iterdir()
                                 if f.is_file())
                nested_files = set(f.name for f in nested.iterdir()
                                   if f.is_file())
                if root_files == nested_files or root_files <= nested_files:
                    # Root is subset or equal — drop it
                    to_remove.add((cd_name, dist_path))
    for key in to_remove:
        del deduped[key]

    # Classify and build results
    results = []
    for (cd_name, dist_path), (_, _, idb_count) in sorted(deduped.items()):
        classification = classify_dist_dir(dist_path, target)
        rel = dist_path.relative_to(target)
        results.append((cd_name, str(rel), classification, idb_count))

    return results, excluded_versions


def classify_dist_dir(dist_path, target_dir):
    """Classify a dist directory by its name/role."""
    dirname = dist_path.name.lower()
    rel = dist_path.relative_to(target_dir)
    rel_str = str(rel).lower()

    if dirname == 'dist' or re.match(r'^dist\d', dirname):
        return 'dist'
    if dirname == 'dist_modules':
        return 'dist_modules'
    if dirname in ('install', 'installation'):
        return 'install'
    if dirname == 'dev':
        return 'dev'
    if dirname == 'extras':
        return 'extras'
    if dirname == 'unbundled':
        return 'unbundled'
    if dirname in ('trix', 'tardist'):
        return 'trix'
    if dirname == 'installtools':
        return 'installtools'
    # If it's the CD root directory itself
    if len(rel.parts) == 1:
        return 'root'
    return 'other'


# ── Phase 3: Analysis ───────────────────────────────────────────────

def analyze_distributions(target_dir, dist_locations):
    """Run subsystem catalog, filename conflict, and overlap analysis.

    dist_locations: list of (cd_name, rel_path, classification, idb_count)

    Returns (catalog, filename_conflicts, subsystem_overlaps).
    """
    target = Path(target_dir)

    # Build (cd_name, dist_path) tuples for dist_analyzer functions
    dist_dirs = []
    for cd_name, rel_path, classification, idb_count in dist_locations:
        abs_path = target / rel_path
        # Use the relative path as display name for clarity
        dist_dirs.append((rel_path, abs_path))

    # A. Subsystem catalog
    catalog = defaultdict(lambda: {"sources": []})
    for display_name, dist_dir in dist_dirs:
        dist_path = Path(dist_dir)
        for idb_path in sorted(dist_path.glob("*.idb")):
            subsystems = parse_idb_subsystems(str(idb_path))
            for subsys_name, info in subsystems.items():
                catalog[subsys_name]["sources"].append({
                    "cd": display_name,
                    "idb": idb_path.name,
                    "files": info["files"],
                    "size": info["total_size"],
                })

    # B. Filename conflicts
    file_map = defaultdict(list)
    for display_name, dist_dir in dist_dirs:
        dist_path = Path(dist_dir)
        if not dist_path.exists():
            continue
        for f in dist_path.iterdir():
            if f.is_file():
                file_map[f.name].append(display_name)

    filename_conflicts = []
    for filename, sources in sorted(file_map.items()):
        if len(sources) > 1:
            filename_conflicts.append((filename, sources))

    # C. Subsystem overlaps
    subsystem_overlaps = []
    for subsys, info in sorted(catalog.items()):
        sources = info["sources"]
        if len(sources) > 1:
            subsystem_overlaps.append((subsys, sources))

    return dict(catalog), filename_conflicts, subsystem_overlaps


# ── Output formatting ───────────────────────────────────────────────

def print_report(extraction_results, dist_locations, excluded_versions,
                 catalog, filename_conflicts, subsystem_overlaps,
                 version=None, platform=None):
    """Print the formatted analysis report."""
    print()
    print("=== IRIX Distribution Check ===")
    header_parts = []
    if platform:
        header_parts.append(f"Platform: {platform}")
    if version:
        header_parts.append(f"Version: {version}")
    if header_parts:
        print("    ".join(header_parts))
    print()

    # ── Extraction results
    if extraction_results:
        print("── Extraction ─────────────────────────────────")
        for idx, subdir, fmt, ok, count in extraction_results:
            status = "OK" if ok else "FAIL"
            if fmt == 'cached':
                status = "cached"
                fmt = ""
            count_str = f"({count} files)" if count else ""
            print(f"  [{idx}] {subdir:40s} {fmt:5s} {status} {count_str}")
        print()

    # ── Dist locations
    if dist_locations:
        excluded_str = ""
        if excluded_versions:
            excluded_str = f" (excluding {', '.join(sorted(excluded_versions))})"
        print(f"── Dist Locations{excluded_str} ──")
        for cd_name, rel_path, classification, idb_count in dist_locations:
            print(f"  {rel_path:50s} {idb_count:3d} idb   {classification}")
        print()

    # ── Subsystem summary
    total_entries = sum(len(info["sources"]) for info in catalog.values())
    unique_subsystems = len(catalog)
    location_count = len(dist_locations)
    print("── Subsystem Summary ──────────────────────────")
    print(f"  Total: {total_entries} entries across {location_count} locations")
    print(f"  Unique: {unique_subsystems} subsystems")
    print()

    # ── Filename conflicts
    if filename_conflicts:
        print(f"── Filename Conflicts ({len(filename_conflicts)}) "
              "────────────────────")
        for filename, sources in filename_conflicts:
            sources_str = ", ".join(sources)
            print(f"  {filename}: {sources_str}")
        print()
    else:
        print("── Filename Conflicts: none ────────────────────")
        print()

    # ── Subsystem overlaps
    if subsystem_overlaps:
        print(f"── Subsystem Overlaps ({len(subsystem_overlaps)}) "
              "────────────────────")
        for subsys, sources in subsystem_overlaps:
            parts = []
            for s in sources:
                parts.append(f"{s['cd']}({s['files']})")
            print(f"  {subsys}: {', '.join(parts)}")
        print()
    else:
        print("── Subsystem Overlaps: none ────────────────────")
        print()


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Extract IRIX CD images and analyze distribution content",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  %(prog)s "Foundation 1.img" "MIPSpro C++.efs.img"
  %(prog)s --version 6.5 --target /tmp/extract path1.img path2.tar.gz
  %(prog)s --target /path/to/already/extracted
""",
    )
    parser.add_argument('images', nargs='*', metavar='IMAGE',
                        help='Image files to extract (.img, .iso, .efs.img, '
                             '.tar, .tar.gz)')
    parser.add_argument('--target', metavar='DIR',
                        help='Extraction target directory '
                             '(default: auto-cleaned tempdir)')
    parser.add_argument('--version', metavar='VER',
                        help='Target IRIX version (e.g., 6.5). Excludes '
                             'non-matching versioned dist dirs')
    parser.add_argument('--platform', metavar='PLAT',
                        help='Target platform (e.g., ip24). Informational')
    parser.add_argument('--keep', action='store_true',
                        help='Keep temp extraction dir after analysis '
                             '(no-op if --target)')
    args = parser.parse_args()

    # Validate: need either images or an existing --target
    if not args.images and not args.target:
        parser.error("provide image paths, or --target with an existing "
                     "extraction directory")
    if not args.images and args.target and not Path(args.target).exists():
        parser.error(f"--target {args.target} does not exist and no images "
                     "provided to extract")

    # Determine target directory
    use_tempdir = False
    if args.target:
        target_dir = Path(args.target)
        target_dir.mkdir(parents=True, exist_ok=True)
    else:
        tmpdir = tempfile.mkdtemp(prefix='dist_check_')
        target_dir = Path(tmpdir)
        use_tempdir = True

    try:
        # Phase 1: Extract
        extraction_results = []
        if args.images:
            print(f"Extracting {len(args.images)} image(s) to {target_dir}")
            extraction_results = run_extraction(args.images, target_dir)
            failed = sum(1 for _, _, _, ok, _ in extraction_results if not ok)
            if failed:
                print(f"\nWarning: {failed} extraction(s) failed",
                      file=sys.stderr)

        # Phase 2: Discover
        dist_locations, excluded_versions = discover_dist_locations(
            target_dir, version_filter=args.version)

        if not dist_locations:
            print("\nNo dist content found (no .idb files in any directory)")
            return 1

        # Phase 3: Analyze
        catalog, filename_conflicts, subsystem_overlaps = \
            analyze_distributions(target_dir, dist_locations)

        # Output
        print_report(
            extraction_results, dist_locations, excluded_versions,
            catalog, filename_conflicts, subsystem_overlaps,
            version=args.version, platform=args.platform,
        )

        if use_tempdir and args.keep:
            print(f"Extraction kept at: {target_dir}")
        elif use_tempdir:
            print(f"(temp dir will be cleaned up)")

        return 0

    finally:
        if use_tempdir and not args.keep:
            shutil.rmtree(target_dir, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main() or 0)
