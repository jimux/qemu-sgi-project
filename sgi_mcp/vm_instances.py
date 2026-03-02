"""VM instance management for SGI QEMU emulation.

Provides organized storage for disk images, NVRAM files, and metadata
in a vm_instances/ directory with per-instance subdirectories.

Directory structure:
    vm_instances/
        {instance_name}/
            disk.qcow2          # Primary disk image
            nvram.bin            # NVRAM file
            manifest.json        # Instance metadata
"""

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

INSTANCES_DIR = Path(__file__).parent.parent / "vm_instances"


def get_instance_dir(name: str) -> Path:
    """Return the instance directory path, creating it if needed."""
    d = INSTANCES_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_manifest(name: str) -> dict:
    """Read manifest.json for an instance. Returns empty dict if missing."""
    manifest_path = INSTANCES_DIR / name / "manifest.json"
    if manifest_path.exists():
        with open(manifest_path) as f:
            return json.load(f)
    return {}


def save_manifest(name: str, manifest: dict):
    """Write manifest.json with pretty-print formatting."""
    d = get_instance_dir(name)
    manifest_path = d / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")


def create_instance(name: str, machine: str = "indy", ram_mb: int = 64,
                    irix_version: str = "", description: str = "",
                    disk_size_mb: int = 2048) -> Path:
    """Create a new VM instance with disk image and manifest.

    Returns the instance directory path.
    """
    d = get_instance_dir(name)
    disk_path = d / "disk.qcow2"

    # Create qcow2 disk image
    if not disk_path.exists():
        project_root = Path(__file__).parent.parent
        qemu_img = _find_qemu_img(project_root)
        subprocess.run(
            [str(qemu_img), "create", "-f", "qcow2", str(disk_path),
             f"{disk_size_mb}M"],
            check=True, capture_output=True
        )

    # Create NVRAM with sane defaults so the instance is bootable
    nvram_path = d / "nvram.bin"
    if not nvram_path.exists():
        from sgi_mcp.nvram_utils import nvram_create_defaults, MACHINE_NVRAM_REV
        rev = MACHINE_NVRAM_REV.get(machine, 8)
        nvram_create_defaults(nvram_path, revision=rev, autoload=False)

    manifest = {
        "name": name,
        "created": datetime.now(timezone.utc).isoformat(),
        "machine": machine,
        "ram_mb": ram_mb,
        "irix_version": irix_version,
        "disk_size_mb": disk_size_mb,
        "disk_format": "qcow2",
        "description": description,
        "snapshots": [],
    }
    save_manifest(name, manifest)
    return d


def update_defaults(name: str, *,
                    default_extra_args: str = None,
                    default_snapshot: str = None,
                    hostfwd_port: int = None):
    """Patch just the launch-defaults fields in a manifest."""
    manifest = load_manifest(name)
    if default_extra_args is not None:
        manifest["default_extra_args"] = default_extra_args
    if default_snapshot is not None:
        manifest["default_snapshot"] = default_snapshot
    if hostfwd_port is not None:
        manifest["hostfwd_port"] = int(hostfwd_port)
    save_manifest(name, manifest)


def add_snapshot(name: str, snapshot_name: str, description: str = "", hardware: dict | None = None):
    """Append a snapshot entry to the instance manifest.

    ``hardware`` is an optional dict of QEMU launch metadata recorded at
    save time so that qemu_session_start can validate compatibility before
    attempting -loadvm.  Keys of interest:
        platform    — "darwin" | "linux"
        qemu_binary — absolute path to the QEMU binary
        qemu_mtime  — integer mtime of that binary (build fingerprint)
        machine     — -M value (e.g. "indy")
        extra_args  — extra QEMU arguments in effect when snapshot was saved
    """
    manifest = load_manifest(name)
    if not manifest:
        return

    snapshots = manifest.setdefault("snapshots", [])
    # Update existing entry if snapshot name matches
    for snap in snapshots:
        if snap.get("name") == snapshot_name:
            snap["description"] = description
            snap["created"] = datetime.now(timezone.utc).isoformat()
            if hardware:
                snap["hardware"] = hardware
            save_manifest(name, manifest)
            return

    entry = {
        "name": snapshot_name,
        "created": datetime.now(timezone.utc).isoformat(),
        "description": description,
    }
    if hardware:
        entry["hardware"] = hardware
    snapshots.append(entry)
    save_manifest(name, manifest)


def update_installation_info(name: str, info: dict):
    """Merge installation info into the instance manifest.

    Stores CD order, skipped packages, critical missing packages,
    and install time under the "installation" key.
    """
    manifest = load_manifest(name)
    if not manifest:
        return
    manifest["installation"] = info
    save_manifest(name, manifest)


def remove_snapshot(name: str, snapshot_name: str):
    """Remove a snapshot entry from the instance manifest."""
    manifest = load_manifest(name)
    if not manifest:
        return
    snapshots = manifest.get("snapshots", [])
    manifest["snapshots"] = [s for s in snapshots if s.get("name") != snapshot_name]
    save_manifest(name, manifest)


def list_instances() -> list[dict]:
    """Return summary info for all instances."""
    if not INSTANCES_DIR.exists():
        return []
    results = []
    for entry in sorted(INSTANCES_DIR.iterdir()):
        if not entry.is_dir():
            continue
        manifest = load_manifest(entry.name)
        disk_path = entry / "disk.qcow2"
        disk_size = disk_path.stat().st_size if disk_path.exists() else 0
        results.append({
            "name": entry.name,
            "machine": manifest.get("machine", "unknown"),
            "irix_version": manifest.get("irix_version", ""),
            "description": manifest.get("description", ""),
            "snapshots": len(manifest.get("snapshots", [])),
            "disk_exists": disk_path.exists(),
            "disk_bytes": disk_size,
            "has_nvram": (entry / "nvram.bin").exists(),
        })
    return results


def get_disk_path(name: str) -> Path:
    """Return the disk image path for an instance."""
    return INSTANCES_DIR / name / "disk.qcow2"


def get_nvram_path(name: str) -> Path:
    """Return the NVRAM file path for an instance."""
    return INSTANCES_DIR / name / "nvram.bin"


def delete_instance(name: str) -> bool:
    """Delete an instance directory and all contents. Returns True if deleted."""
    d = INSTANCES_DIR / name
    if d.exists():
        shutil.rmtree(d)
        return True
    return False


def migrate_existing(name: str, disk_path: str, nvram_path: str = None,
                     machine: str = "indy", ram_mb: int = 64,
                     irix_version: str = "", description: str = "") -> Path:
    """Move existing disk/NVRAM files into a new instance directory.

    Files are moved (not copied) to avoid duplication. Returns instance dir.
    """
    d = get_instance_dir(name)
    src_disk = Path(disk_path)
    dest_disk = d / "disk.qcow2"

    if not src_disk.exists():
        raise FileNotFoundError(f"Disk image not found: {disk_path}")

    # Move disk image
    if not dest_disk.exists():
        shutil.move(str(src_disk), str(dest_disk))

    # Move NVRAM if provided
    if nvram_path:
        src_nvram = Path(nvram_path)
        if src_nvram.exists():
            shutil.move(str(src_nvram), str(d / "nvram.bin"))

    # Detect disk info
    disk_size_mb = 0
    disk_format = "qcow2" if dest_disk.suffix == ".qcow2" else "raw"
    try:
        project_root = Path(__file__).parent.parent
        qemu_img = _find_qemu_img(project_root)
        result = subprocess.run(
            [str(qemu_img), "info", "--output=json", str(dest_disk)],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            info = json.loads(result.stdout)
            disk_size_mb = info.get("virtual-size", 0) // (1024 * 1024)
            disk_format = info.get("format", disk_format)
    except Exception:
        pass

    # Detect existing snapshots
    snapshots = _detect_qcow2_snapshots(dest_disk)

    manifest = {
        "name": name,
        "created": datetime.now(timezone.utc).isoformat(),
        "machine": machine,
        "ram_mb": ram_mb,
        "irix_version": irix_version,
        "disk_size_mb": disk_size_mb,
        "disk_format": disk_format,
        "description": description,
        "snapshots": snapshots,
    }
    save_manifest(name, manifest)
    return d


def _detect_qcow2_snapshots(disk_path: Path) -> list[dict]:
    """Detect existing snapshots in a qcow2 image."""
    snapshots = []
    try:
        project_root = Path(__file__).parent.parent
        qemu_img = _find_qemu_img(project_root)
        result = subprocess.run(
            [str(qemu_img), "snapshot", "-l", str(disk_path)],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().split("\n"):
                # Lines look like: 1 install_complete   0 ...
                parts = line.split()
                if len(parts) >= 2 and parts[0].isdigit():
                    snap_name = parts[1]
                    snapshots.append({
                        "name": snap_name,
                        "created": datetime.now(timezone.utc).isoformat(),
                        "description": "(migrated from existing disk)",
                    })
    except Exception:
        pass
    return snapshots


def _is_native_binary(path: Path) -> bool:
    """Return True if the binary is executable on the current OS."""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
        import sys as _sys
        if _sys.platform == "darwin":
            return magic in (b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
                              b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf",
                              b"\xca\xfe\xba\xbe")
        return magic == b"\x7fELF"
    except OSError:
        return False


def _find_qemu_img(project_root: Path) -> Path:
    """Find qemu-img binary, preferring the platform-native build."""
    for build_name in ("build-mac", "build-linux", "build"):
        p = project_root / "qemu" / build_name / "qemu-img"
        if p.exists() and p.is_file() and _is_native_binary(p):
            return p
    # Fall back to system qemu-img
    return Path("qemu-img")
