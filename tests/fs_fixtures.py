"""Synthetic EFS / V1-XFS image corpus for pyirix tests.

Every image here is built from scratch by pyirix itself (no external mkfs, no
real disk needed), so the suite is fully reproducible and always runs. Provides
both *valid* images (with a known mix of files/dirs/symlinks) and *corrupted*
variants exercising the repair paths.

Each builder takes a destination path and returns it. The KNOWN_* dicts describe
the expected contents so tests can assert exact round-trips.
"""

import os
import struct

from pyirix.efs import mkfs_efs
from pyirix.efs.reader import find_efs_partition, EFS_BLOCK_SIZE
from pyirix.xfs.mkfs import mkfs_xfs

# ── Known contents of the valid images ──────────────────────────────

# A multi-extent file (spans several 512-byte EFS basic blocks).
EFS_BIG = b"".join(bytes([i & 0xFF]) * 700 for i in range(6))   # 4200 bytes

KNOWN_EFS_FILES = {
    "/etc/motd": b"Welcome to synthetic IRIX\n",
    "/etc/hosts": b"127.0.0.1 localhost\n",
    "/usr/share/data.bin": EFS_BIG,
}
KNOWN_EFS_SYMLINKS = {"/etc/rc": "/etc/motd"}
KNOWN_EFS_DIRS = ["/empty"]

XFS_BIG = b"abcd" * 4096          # 16 KiB -> multiple 4 KiB blocks
KNOWN_XFS_FILES = {
    "/etc/motd": b"hello irix\n",
    "/etc/group": b"sys::0:root\n",
    "/var/big.dat": XFS_BIG,
}
KNOWN_XFS_DIRS = ["/usr", "/usr/lib"]


# ── Valid builders ──────────────────────────────────────────────────

def build_valid_efs(path):
    mkfs_efs(path, files=KNOWN_EFS_FILES, symlinks=KNOWN_EFS_SYMLINKS,
             dirs=KNOWN_EFS_DIRS)
    return path


def build_valid_xfs(path, agcount=1, size_mb=16):
    """A V1-directory XFS, then populate it via pyirix.xfs write ops."""
    from pyirix.xfs.image import open_disk_image, find_xfs_partition
    from pyirix.xfs.superblock import read_superblock
    from pyirix.xfs.operations import mkdir, create_file

    mkfs_xfs(path, size_mb=size_mb, agcount=agcount)
    # Every directory that must exist (explicit dirs + every file's ancestors),
    # ordered shallow-to-deep so parents are created before children.
    needed = set(KNOWN_XFS_DIRS)
    for p in KNOWN_XFS_FILES:
        parts = [c for c in p.split("/") if c][:-1]
        for i in range(1, len(parts) + 1):
            needed.add("/" + "/".join(parts[:i]))
    with open_disk_image(path, writable=True) as f:
        po, _ = find_xfs_partition(f)
        sb = read_superblock(f, po)
        for d in sorted(needed, key=lambda x: x.count("/")):
            mkdir(f, po, sb, d)
        for p, data in KNOWN_XFS_FILES.items():
            create_file(f, po, sb, p, data, mode=0o644)
    return path


# ── Corruption helpers ──────────────────────────────────────────────

def _xfs_part_offset():
    # mkfs_xfs places the XFS partition at sector 64.
    return 64 * 512


def corrupt_xfs_bad_version(path, size_mb=16):
    """Set a SASH-rejected feature bit (0x8000) in the version number.
    Repairable: repair_version_bits."""
    build_valid_xfs(path, size_mb=size_mb)
    po = _xfs_part_offset()
    with open(path, "r+b") as f:
        f.seek(po + 0x64)
        ver = struct.unpack(">H", f.read(2))[0]
        f.seek(po + 0x64)
        f.write(struct.pack(">H", ver | 0x8000))
    return path


def corrupt_xfs_wiped_primary_sb(path):
    """Zero the primary superblock. Repairable from the AG1 secondary."""
    build_valid_xfs(path, agcount=2, size_mb=32)
    po = _xfs_part_offset()
    with open(path, "r+b") as f:
        f.seek(po)
        f.write(b"\x00" * 512)
    return path


def corrupt_xfs_truncated(path):
    """Truncate mid-filesystem. Not repairable; check must FAIL gracefully."""
    build_valid_xfs(path)
    with open(path, "r+b") as f:
        f.truncate(_xfs_part_offset() + 4096)   # only the first block survives
    return path


def corrupt_efs_bad_sb_magic(path):
    """Clobber the primary superblock magic. Repairable from the replica."""
    build_valid_efs(path)
    with open(path, "r+b") as f:
        part = find_efs_partition(f)
        po = part[0]
        f.seek(po + EFS_BLOCK_SIZE + 28)
        f.write(b"\xde\xad\xbe\xef")
    return path


def corrupt_efs_bad_checksum(path):
    """Flip a byte so the superblock checksum no longer matches (magic intact)."""
    build_valid_efs(path)
    with open(path, "r+b") as f:
        part = find_efs_partition(f)
        po = part[0]
        # bump fs_tfree (offset 48) by 1 without fixing the checksum
        f.seek(po + EFS_BLOCK_SIZE + 48)
        val = struct.unpack(">i", f.read(4))[0]
        f.seek(po + EFS_BLOCK_SIZE + 48)
        f.write(struct.pack(">i", val + 1))
    return path


def corrupt_efs_no_partition(path):
    """No volume header, no EFS magic anywhere — find_efs_partition returns None."""
    with open(path, "wb") as f:
        f.write(b"\x55" * (1024 * 1024))
    return path


# ── One-call corpus builder ─────────────────────────────────────────

def build_corpus(dest_dir):
    """Build the whole corpus under dest_dir. Returns {name: path}."""
    os.makedirs(dest_dir, exist_ok=True)
    j = lambda n: os.path.join(dest_dir, n)
    paths = {
        "efs_valid": build_valid_efs(j("efs_valid.img")),
        "xfs_valid": build_valid_xfs(j("xfs_valid.img")),
        "xfs_valid_2ag": build_valid_xfs(j("xfs_valid_2ag.img"), agcount=2, size_mb=32),
        "xfs_bad_version": corrupt_xfs_bad_version(j("xfs_bad_version.img")),
        "xfs_wiped_sb": corrupt_xfs_wiped_primary_sb(j("xfs_wiped_sb.img")),
        "xfs_truncated": corrupt_xfs_truncated(j("xfs_truncated.img")),
        "efs_bad_magic": corrupt_efs_bad_sb_magic(j("efs_bad_magic.img")),
        "efs_bad_checksum": corrupt_efs_bad_checksum(j("efs_bad_checksum.img")),
        "efs_no_partition": corrupt_efs_no_partition(j("efs_no_partition.img")),
    }
    return paths
