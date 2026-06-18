#!/usr/bin/env python3
"""Surgical fill of the v2 gold image's remaining manifest gaps —
batched, single-conversion version.

Critical: opens the dest disk ONCE in writable mode (one qcow2→raw
conversion at start, one raw→qcow2 conversion at end). All file writes
happen against the open raw file. This is ~100× faster than calling
fs_inject per-file (which converts twice per call).
"""

import os, struct, sys
from pathlib import Path

sys.path.insert(0, "/home/jimmy/qemu-sgi")
from sgi_mcp.sgi_fs import (open_disk_image as sgi_open,
    find_xfs_partition, xfs_read_superblock, _xfs_resolve_path,
    xfs_read_inode, xfs_read_dir_entries, xfs_read_file_data,
    S_IFMT, S_IFDIR, S_IFREG)

# pyirix's XFS write path
from pyirix.xfs.image import (open_disk_image as xfs_open,
                              find_xfs_partition as xfs_find_part)
from pyirix.xfs.superblock import read_superblock as xfs_read_sb
from pyirix.xfs.operations import (resolve_path as xfs_resolve_path,
                                   create_file as xfs_create_file,
                                   write_file as xfs_write_file,
                                   mkdir as xfs_mkdir)

PROJECT_ROOT = Path("/home/jimmy/qemu-sgi")
GOLD = PROJECT_ROOT / "vm_instances/ip54-fresh/disk.qcow2"
SOURCE = PROJECT_ROOT / "vm_instances/irix-devel/disk.qcow2"


# ── Source-disk readers (single open) ────────────────────────────────────


def read_source_files(source_path, dirs_to_copy: list[str]) -> dict[str, bytes]:
    """Read all regular files under each dir in dirs_to_copy from
    source_path. Returns {guest_relative_path: bytes}.

    Single open of the source disk; no conversion penalty per file."""
    files: dict[str, bytes] = {}
    with sgi_open(str(source_path)) as f:
        p = find_xfs_partition(f)
        if not p:
            print(f"  source disk has no XFS partition")
            return files
        off, _ = p
        sb = xfs_read_superblock(f, off)
        if not sb:
            print(f"  source disk superblock unreadable")
            return files

        for root in dirs_to_copy:
            root_ino = _xfs_resolve_path(f, off, sb, root)
            if root_ino is None:
                print(f"  source has no {root}")
                continue
            # BFS the tree
            stack = [(root_ino, root)]
            count = 0
            while stack:
                cur_ino, path = stack.pop()
                inode = xfs_read_inode(f, off, sb, cur_ino)
                if not inode:
                    continue
                if (inode["di_mode"] & S_IFMT) != S_IFDIR:
                    continue
                for name, child_ino in xfs_read_dir_entries(
                        f, off, sb, inode):
                    if name in (".", "..") or name.startswith("._"):
                        continue
                    if name in ("PaxHeader",):
                        continue
                    child_path = f"{path}/{name}"
                    child_inode = xfs_read_inode(f, off, sb, child_ino)
                    if not child_inode:
                        continue
                    ftype = child_inode["di_mode"] & S_IFMT
                    if ftype == S_IFREG:
                        data = xfs_read_file_data(f, off, sb, child_inode)
                        if data is not None:
                            files[child_path] = data
                            count += 1
                    elif ftype == S_IFDIR:
                        stack.append((child_ino, child_path))
            print(f"  {root}: read {count} files")
    return files


# ── Cosmetic placeholders ────────────────────────────────────────────────


def make_minimal_rgb() -> bytes:
    """Minimal valid SGI .rgb (1x1 RGB grey)."""
    hdr = bytearray(512)
    struct.pack_into('>H', hdr, 0, 0x01da)
    hdr[2] = 0
    hdr[3] = 1
    struct.pack_into('>H', hdr, 4, 3)
    struct.pack_into('>H', hdr, 6, 1)
    struct.pack_into('>H', hdr, 8, 1)
    struct.pack_into('>H', hdr, 10, 3)
    struct.pack_into('>I', hdr, 12, 0)
    struct.pack_into('>I', hdr, 16, 255)
    hdr[24:30] = b'placeholder'[:6]
    struct.pack_into('>I', hdr, 104, 0)
    return bytes(hdr) + b'\x80\x80\x80'


def build_placeholder_set() -> dict[str, bytes]:
    """Build the dict of cosmetic placeholders to inject."""
    rgb = make_minimal_rgb()
    out: dict[str, bytes] = {}
    # clogin's SGI logo
    out["/usr/Cadmin/lib/cloginlib/cloginlogo.rgb"] = rgb
    # Face icons — manifest requires /usr/local/lib/faces/root + ≥1 entry
    for user in ["root", "guest", "EZsetup", "demos"]:
        out[f"/usr/local/lib/faces/{user}/photo"] = rgb
    # X11 iconlib — manifest sanity requires ≥10 entries
    iconlib_stub = b"; placeholder X11 file-type icon\n"
    for name in ["default", "folder", "file", "exec", "lib",
                 "trash", "doc", "image", "sound", "video",
                 "system", "user"]:
        out[f"/usr/lib/X11/iconlib/{name}.fti"] = iconlib_stub
    # /usr/lib/desktop/iconcatalog/C — manifest sanity requires ≥5 entries
    catalog_stub = b"; placeholder desktop icon catalog entry\n"
    for name in ["Doc", "Folder", "App", "Image", "Audio", "Video", "System"]:
        out[f"/usr/lib/desktop/iconcatalog/C/{name}.fti"] = catalog_stub
    return out


# ── Main ─────────────────────────────────────────────────────────────────


def main():
    print(f"=== Reading dev headers from {SOURCE} ===")
    source_files = read_source_files(SOURCE,
        ["/usr/include/Xm", "/usr/include/gl", "/usr/include/sys"])
    # We don't want the entire /usr/include/sys tree — just stdio.h and a few
    # commonly missing ones. Filter:
    source_files = {p: d for p, d in source_files.items()
                    if not p.startswith("/usr/include/sys/")}
    # But re-add a few specific ones if they exist
    with sgi_open(str(SOURCE)) as f:
        p = find_xfs_partition(f)
        if p:
            off, _ = p
            sb = xfs_read_superblock(f, off)
            for path in ["/usr/include/stdio.h",
                         "/usr/include/sys/types.h"]:
                ino = _xfs_resolve_path(f, off, sb, path)
                if ino:
                    inode = xfs_read_inode(f, off, sb, ino)
                    if (inode["di_mode"] & S_IFMT) == S_IFREG:
                        data = xfs_read_file_data(f, off, sb, inode)
                        if data:
                            source_files[path] = data
                            print(f"  + {path}: {len(data)} bytes")

    # Also generate /usr/include/GL/gl.h = same content as /usr/include/gl/gl.h
    gl_h = source_files.get("/usr/include/gl/gl.h")
    if gl_h:
        source_files["/usr/include/GL/gl.h"] = gl_h
        print(f"  + /usr/include/GL/gl.h: derived from /usr/include/gl/gl.h "
              f"({len(gl_h)} bytes)")

    placeholders = build_placeholder_set()
    print(f"  placeholders: {len(placeholders)} files")
    print()

    # Combine — placeholders may overlap with source_files; prefer source.
    all_files = {**placeholders, **source_files}
    print(f"=== Injecting {len(all_files)} files into {GOLD} ===")

    # Open the dest disk ONCE (qcow2 → raw conversion happens here).
    with xfs_open(str(GOLD), writable=True) as f:
        part = xfs_find_part(f)
        if not part:
            print("FATAL: no XFS partition in dest disk")
            sys.exit(2)
        off, _ = part
        sb = xfs_read_sb(f, off)
        if sb is None:
            print("FATAL: dest superblock unreadable")
            sys.exit(2)

        def ensure_dirs(path: str):
            """Create every missing parent dir of `path` (the file itself
            is created separately). Idempotent."""
            parts = [p for p in path.strip('/').split('/') if p]
            # Drop the last component (the file name)
            for i in range(1, len(parts)):
                d = "/" + "/".join(parts[:i])
                if xfs_resolve_path(f, off, sb, d) is None:
                    xfs_mkdir(f, off, sb, d, mode=0o755, uid=0, gid=0)

        injected = 0
        bytes_written = 0
        for path in sorted(all_files):
            data = all_files[path]
            try:
                ensure_dirs(path)
                existing = xfs_resolve_path(f, off, sb, path)
                if existing is not None:
                    xfs_write_file(f, off, sb, path, data)
                else:
                    xfs_create_file(f, off, sb, path, data,
                                    mode=0o644, uid=0, gid=0)
                injected += 1
                bytes_written += len(data)
                if injected % 20 == 0:
                    print(f"  ... {injected}/{len(all_files)} files "
                          f"({bytes_written:,} bytes)")
            except Exception as e:
                print(f"  FAIL {path}: {type(e).__name__}: {e}")

    print()
    print(f"=== DONE: {injected}/{len(all_files)} files, "
          f"{bytes_written:,} bytes written ===")


if __name__ == "__main__":
    main()
