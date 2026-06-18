#!/usr/bin/env python3
"""Assemble a minimal single-user (run level S/1) IRIX root filesystem from
scratch, using only pyirix tooling.

Demonstrates that the EFS/XFS tooling is complete enough to manually build a
bootable system: mkfs a fresh XFS, copy the kernel + static /sbin binaries out
of a real IRIX disk, write a minimal /etc/inittab, and create the /dev device
nodes and symlinks a console boot needs.

Usage:
    python3 -m pyirix_qemu.build_minimal_root \
        --source vm_instances/ip54-test/disk.qcow2 \
        --out /tmp/minroot.img

The result is an SGI-volume-header disk image whose XFS root passes IRIX's own
xfs_check. (Making the PROM *boot* it additionally needs the volume-header sash
loader + NVRAM, which is the disk/PROM layer, not the filesystem tooling.)
"""

import argparse
import struct

from pyirix.xfs.image import open_disk_image, find_xfs_partition
from pyirix.xfs.mkfs import mkfs_xfs
from pyirix.xfs.superblock import read_superblock
from pyirix.xfs.operations import (
    resolve_path, resolve_path_follow_links, mkdir, create_file,
    create_symlink, mknod, read_dev,
)
from pyirix.xfs.inode import read_inode, read_file_data, read_symlink
from pyirix.xfs.constants import S_IFMT, S_IFCHR, S_IFBLK, S_IFLNK, S_IFREG

# Files copied verbatim from the source disk. /unix.new is the IP54-patched
# kernel the bootloader loads. /sbin/init is dynamically linked (n32) so it
# needs the runtime linker /lib32/rld and /lib32/libc.so.1; /sbin/sh is static.
COPY_FILES = [
    "/unix.new",
    "/sbin/init", "/sbin/sh",
    "/lib32/rld", "/lib32/libc.so.1",
]

# Character device nodes a console single-user boot needs. Dev words are read
# from the source disk so they match what the IRIX kernel expects.
DEV_NODES = ["/dev/console", "/dev/null", "/dev/tty", "/dev/systty", "/dev/zero"]

# Symlinks under /dev (target read from source).
DEV_SYMLINKS = ["/dev/root", "/dev/swap"]

# A minimal inittab: straight to a single-user shell on the console.
MIN_INITTAB = b"""\
is:S:initdefault:
su:S:wait:/sbin/sh </dev/console >/dev/console 2>&1
"""


def _src_open(path):
    f = open_disk_image(path)
    fh = f.__enter__()
    po, _ = find_xfs_partition(fh)
    sb = read_superblock(fh, po)
    return f, fh, po, sb


def build(source, out, size_mb=64, with_volume_header=True):
    # Read everything we need from the source first.
    sctx = open_disk_image(source)
    sf = sctx.__enter__()
    spo, _ = find_xfs_partition(sf)
    ssb = read_superblock(sf, spo)

    files = {}
    for p in COPY_FILES:
        ino = resolve_path_follow_links(sf, spo, ssb, p)
        if ino is None:
            raise SystemExit(f"source missing {p}")
        inode = read_inode(sf, spo, ssb, ino)
        files[p] = (read_file_data(sf, spo, ssb, inode), inode['di_mode'] & 0o7777)

    devs = {}
    for p in DEV_NODES:
        ino = resolve_path(sf, spo, ssb, p)
        if ino is None:
            print(f"  (source lacks {p}, skipping)")
            continue
        inode = read_inode(sf, spo, ssb, ino)
        t = inode['di_mode'] & S_IFMT
        if t not in (S_IFCHR, S_IFBLK):
            print(f"  ({p} is not a device node on source, skipping)")
            continue
        devs[p] = (inode['di_mode'], t, read_dev(sf, spo, ssb, p))

    links = {}
    for p in DEV_SYMLINKS:
        ino = resolve_path(sf, spo, ssb, p)
        if ino is not None:
            inode = read_inode(sf, spo, ssb, ino)
            if (inode['di_mode'] & S_IFMT) == S_IFLNK:
                links[p] = read_symlink(sf, spo, ssb, inode)
    sctx.__exit__(None, None, None)

    total = sum(len(d) for d, _ in files.values())
    print(f"source read: {len(files)} files ({total} bytes), "
          f"{len(devs)} device nodes, {len(links)} symlinks")

    # Build the fresh root.
    mkfs_xfs(out, size_mb=size_mb, agcount=1, with_volume_header=with_volume_header)
    with open_disk_image(out, writable=True) as df:
        part = find_xfs_partition(df)
        po = part[0] if part else 0          # raw image -> superblock at offset 0
        sb = read_superblock(df, po)

        # /hw is the mount point for hwgfs (the hardware graph); without it the
        # kernel logs "Unable to mount hwgfs error = 2" and device paths like
        # /hw/scsi_ctlr/... and the /dev/root -> /hw/disk/root link don't resolve.
        for d in ("/sbin", "/etc", "/dev", "/var", "/tmp", "/lib32", "/hw", "/proc"):
            mkdir(df, po, sb, d, mode=0o755)

        for p, (data, mode) in files.items():
            create_file(df, po, sb, p, data, mode=mode)
            print(f"  + {p} ({len(data)} bytes)")

        create_file(df, po, sb, "/etc/inittab", MIN_INITTAB, mode=0o644)
        print("  + /etc/inittab (minimal single-user)")

        for p, (mode, _t, dev) in devs.items():
            mknod(df, po, sb, p, mode, dev)
            print(f"  + {p} (dev {dev:#010x})")

        for p, target in links.items():
            create_symlink(df, po, sb, p, target)
            print(f"  + {p} -> {target}")

        # The kernel icode exec's /etc/init (see kern/ml/csu.s); on real disks
        # it is a symlink to ../sbin/init. Without it PID 1 exec fails with
        # ENOENT -> "PANIC: init died (what=0x2)".
        create_symlink(df, po, sb, "/etc/init", "../sbin/init")
        print("  + /etc/init -> ../sbin/init")

    import os
    print(f"\nwrote {out} ({os.path.getsize(out)} bytes)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True, help="real IRIX disk to copy kernel/binaries from")
    ap.add_argument("--out", required=True, help="output disk image")
    ap.add_argument("--size-mb", type=int, default=64)
    ap.add_argument("--raw", action="store_true",
                    help="raw XFS partition (byte 0 = superblock; for xfs_check)")
    a = ap.parse_args()
    build(a.source, a.out, a.size_mb, with_volume_header=not a.raw)


if __name__ == "__main__":
    main()
