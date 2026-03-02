"""Disk creation, conversion, and snapshot management for QEMU SGI emulation.

Wraps qemu-img operations with defaults suited to SGI/IRIX work.
"""

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def _is_native_binary(path) -> bool:
    """Return True if the binary matches the current OS (ELF on Linux, Mach-O on macOS)."""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
        if sys.platform == "darwin":
            return magic in (b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
                              b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf",
                              b"\xca\xfe\xba\xbe")
        return magic == b"\x7fELF"
    except OSError:
        return False


def _find_qemu_img():
    """Find qemu-img, checking all known build directories."""
    for subdir in ("build-mac", "build-linux", "build"):
        p = PROJECT_ROOT / "qemu" / subdir / "qemu-img"
        if p.exists() and _is_native_binary(p):
            return str(p)
    return "qemu-img"  # fall back to PATH

QEMU_IMG = _find_qemu_img()


def _run_qemu_img(*args, check=True):
    """Run qemu-img with given arguments."""
    cmd = [QEMU_IMG] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"qemu-img failed: {result.stderr.strip()}")
    return result


def create_disk(path, size_mb=2048, fmt="qcow2"):
    """Create a new disk image.

    Args:
        path: Output file path.
        size_mb: Disk size in megabytes.
        fmt: Image format — 'qcow2' (snapshot-capable) or 'raw'.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _run_qemu_img("create", "-f", fmt, str(path), f"{size_mb}M")
    return str(path)


def convert_disk(src, dst=None, fmt="qcow2"):
    """Convert a disk image between formats.

    Args:
        src: Source image path.
        dst: Destination path. If None, replaces extension based on fmt.
        fmt: Target format — 'qcow2' or 'raw'.
    """
    src = Path(src)
    if dst is None:
        ext = ".qcow2" if fmt == "qcow2" else ".img"
        dst = src.with_suffix(ext)
    else:
        dst = Path(dst)

    src_fmt = "qcow2" if src.suffix == ".qcow2" else "raw"
    _run_qemu_img("convert", "-f", src_fmt, "-O", fmt, str(src), str(dst))
    return str(dst)


def disk_info(path):
    """Get disk image info as a dict."""
    result = _run_qemu_img("info", "--output=json", str(path))
    return json.loads(result.stdout)


def list_snapshots(disk_path):
    """List snapshots in a qcow2 image.

    Returns list of dicts with id, tag, vm-size, date, vm-clock fields.
    """
    result = _run_qemu_img("snapshot", "-l", str(disk_path), check=False)
    if result.returncode != 0:
        return []

    snapshots = []
    # Parse tabular output: ID TAG VM SIZE DATE VM CLOCK
    lines = result.stdout.strip().split("\n")
    for line in lines:
        # Skip header lines
        if not line or line.startswith("Snapshot") or line.startswith("ID") or line.startswith("--"):
            continue
        parts = line.split()
        if len(parts) >= 6:
            snapshots.append({
                "id": parts[0],
                "tag": parts[1],
                "vm_size": parts[2],
                "date": f"{parts[3]} {parts[4]}",
                "vm_clock": parts[5] if len(parts) > 5 else "",
            })
    return snapshots


def delete_snapshot(disk_path, name):
    """Delete a snapshot from a qcow2 image."""
    _run_qemu_img("snapshot", "-d", name, str(disk_path))


def create_backed_disk(backing_file, overlay_path):
    """Create a qcow2 overlay backed by another image.

    Writes go to the overlay; the backing file stays clean.
    Useful for disposable test iterations.

    Args:
        backing_file: Path to the base image (qcow2 or raw).
        overlay_path: Path for the new overlay image.
    """
    backing = Path(backing_file).resolve()
    overlay = Path(overlay_path)
    overlay.parent.mkdir(parents=True, exist_ok=True)

    backing_fmt = "qcow2" if backing.suffix == ".qcow2" else "raw"
    _run_qemu_img("create", "-f", "qcow2",
                  "-b", str(backing), "-F", backing_fmt,
                  str(overlay))
    return str(overlay)


def main():
    """CLI entry point for disk management."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Disk management for QEMU SGI emulation"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # create
    p_create = sub.add_parser("create", help="Create a new disk image")
    default_disk = str(PROJECT_ROOT / "irix_disk.qcow2")
    p_create.add_argument("--path", default=default_disk,
                          help=f"Output path (default: {default_disk})")
    p_create.add_argument("--size", type=int, default=2048,
                          help="Size in MB (default: 2048)")
    p_create.add_argument("--format", choices=["qcow2", "raw"], default="qcow2",
                          help="Image format (default: qcow2)")

    # convert
    p_convert = sub.add_parser("convert", help="Convert between disk formats")
    p_convert.add_argument("src", help="Source image")
    p_convert.add_argument("--dst", help="Destination path (auto-generated if omitted)")
    p_convert.add_argument("--format", choices=["qcow2", "raw"], default="qcow2",
                          help="Target format (default: qcow2)")

    # info
    p_info = sub.add_parser("info", help="Show disk image info")
    p_info.add_argument("path", help="Disk image path")

    # snapshots
    p_snap = sub.add_parser("snapshots", help="List snapshots in a qcow2 image")
    p_snap.add_argument("path", help="Disk image path")

    # delete-snapshot
    p_dsnap = sub.add_parser("delete-snapshot", help="Delete a snapshot")
    p_dsnap.add_argument("path", help="Disk image path")
    p_dsnap.add_argument("name", help="Snapshot name to delete")

    # overlay
    p_overlay = sub.add_parser("overlay", help="Create a qcow2 overlay for clean iteration")
    p_overlay.add_argument("backing", help="Backing file path")
    p_overlay.add_argument("--overlay", default="/tmp/irix_test_overlay.qcow2",
                           help="Overlay path (default: /tmp/irix_test_overlay.qcow2)")

    args = parser.parse_args()

    if args.command == "create":
        path = create_disk(args.path, args.size, args.format)
        print(f"Created {args.format} disk: {path} ({args.size} MB)")

    elif args.command == "convert":
        dst = convert_disk(args.src, args.dst, args.format)
        print(f"Converted {args.src} -> {dst} ({args.format})")

    elif args.command == "info":
        info = disk_info(args.path)
        for k, v in info.items():
            print(f"  {k}: {v}")

    elif args.command == "snapshots":
        snaps = list_snapshots(args.path)
        if not snaps:
            print("No snapshots found.")
        else:
            for s in snaps:
                print(f"  {s['id']:>3}  {s['tag']:<20}  {s['vm_size']:>8}  {s['date']}  {s['vm_clock']}")

    elif args.command == "delete-snapshot":
        delete_snapshot(args.path, args.name)
        print(f"Deleted snapshot '{args.name}' from {args.path}")

    elif args.command == "overlay":
        path = create_backed_disk(args.backing, args.overlay)
        print(f"Created overlay: {path} (backing: {args.backing})")


if __name__ == "__main__":
    main()
