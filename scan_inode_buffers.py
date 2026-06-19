#!/usr/bin/env python3
"""Walk every allocated inode chunk in an XFS image and verify each inode's
magic. This catches the kind of block-level inode-buffer corruption the
IRIX kernel reports as 'Bad magic # 0x0 in XFS inode buffer ...' — the
exact symptom on our current IP54 golden disk at block 5270224.

For each AG:
  - read AGI
  - walk the inobt B+tree
  - for each record (a chunk of 64 inodes):
      - for every inode allocated (bit clear in ir_free):
          - read the inode buffer
          - check di_magic == XFS_DINODE_MAGIC ('IN' = 0x494e)
          - report mismatches with byte offsets

Output:
  [PASS] all NNNN allocated inodes have valid 'IN' magic
  [FAIL] inode INO at offset 0xXXX: magic 0xZZZZ (expected 0x494e)
"""
import sys
from pathlib import Path

from pyirix.xfs.image import open_disk_image, find_xfs_partition
from pyirix.xfs.superblock import read_superblock
from pyirix.xfs.ialloc import read_agi, _inobt_cursor
from pyirix.xfs.ondisk import parse_inobt_rec, agino_to_ino
from pyirix.xfs.constants import (
    XFS_DINODE_MAGIC, XFS_INODES_PER_CHUNK, SECTOR_SIZE,
)


def scan_disk(path: str, check_free=True):
    """Walk every inode slot in every allocated inode chunk.

    The IRIX kernel's xfs_inobp_bwcheck() validates EVERY inode in a buffer
    (allocated or free) on bwrite — free inodes should still have di_magic=
    'IN' since xfs_ialloc_ag_alloc initializes the whole chunk. So we need
    to check all 64 slots per chunk, not just allocated ones.
    """
    print(f"\n=== {path} ===")
    bad_alloc = []
    bad_free = []
    total_allocated = 0
    total_free = 0
    total_chunks = 0
    with open_disk_image(path) as f:
        po, _ = find_xfs_partition(f)
        sb = read_superblock(f, po)
        inode_size = sb['sb_inodesize']
        agblocks = sb['sb_agblocks']
        bsize = sb['sb_blocksize']
        for agno in range(sb['sb_agcount']):
            agi = read_agi(f, po, sb, agno)
            if agi is None:
                print(f"  AG {agno}: no AGI"); continue
            cur = _inobt_cursor(f, po, sb, agno, agi)
            for rec_data in cur.walk_all():
                rec = parse_inobt_rec(rec_data)
                total_chunks += 1
                startino_in_ag = rec['ir_startino']
                free = rec['ir_free']
                for bit in range(XFS_INODES_PER_CHUNK):
                    is_free = bool(free & (1 << bit))
                    if is_free:
                        total_free += 1
                    else:
                        total_allocated += 1
                    if is_free and not check_free:
                        continue
                    agino = startino_in_ag + bit
                    fs_ino = agino_to_ino(sb, agno, agino)
                    inode_bytes_in_ag = agino * inode_size
                    block_in_ag = inode_bytes_in_ag // bsize
                    offset_in_block = inode_bytes_in_ag % bsize
                    fs_block = agno * agblocks + block_in_ag
                    byte_pos = po + fs_block * bsize + offset_in_block
                    f.seek(byte_pos)
                    raw = f.read(inode_size)
                    if len(raw) < 4:
                        (bad_free if is_free else bad_alloc).append(
                            (fs_ino, byte_pos, b"<short>"))
                        continue
                    magic = int.from_bytes(raw[0:2], 'big')
                    if magic != XFS_DINODE_MAGIC:
                        # Also pick up next_unlinked field (offset 56 in dinode_t
                        # for V1; right after di_core which is 96 bytes for V2 +
                        # additional pad. Just grab uint32 at off 96.)
                        next_unlinked = int.from_bytes(raw[96:100], 'big') if len(raw) >= 100 else 0
                        entry = (fs_ino, byte_pos, raw[:8], magic, fs_block,
                                 next_unlinked)
                        (bad_free if is_free else bad_alloc).append(entry)
    print(f"  Chunks: {total_chunks}; allocated inodes: {total_allocated}; "
          f"free slots: {total_free}")
    bad_total = len(bad_alloc) + len(bad_free)
    if bad_alloc:
        print(f"  CORRUPT (allocated): {len(bad_alloc)} inodes with bad magic")
        for entry in bad_alloc[:5]:
            print(f"    ino={entry[0]} byte=0x{entry[1]:x} magic=0x{entry[3]:04x}")
    if bad_free:
        print(f"  CORRUPT (free slots): {len(bad_free)} free-inode slots with bad magic")
        for entry in bad_free[:10]:
            print(f"    ino={entry[0]} byte=0x{entry[1]:x} magic=0x{entry[3]:04x} "
                  f"first8={entry[2].hex()}")
        if len(bad_free) > 10:
            print(f"    ... and {len(bad_free)-10} more free-slot bad-magic entries")
    if bad_total == 0:
        print(f"  CLEAN: all inode slots (allocated AND free) have magic 'IN' (0x494e)")
    return len(bad_alloc), len(bad_free)


def main():
    if len(sys.argv) < 2:
        # Default: scan all candidates
        candidates = [
            "vm_instances/ip54-test/disk.qcow2.golden",
            "vm_instances/ip54-fresh/disk.qcow2",
            "vm_instances/ip54-fresh/disk.qcow2.before_replace",
            "vm_instances/ip54-fresh/disk.qcow2.pre_inject",
            "prebuilt_disks/ip54-6.5.5-gold.qcow2",
            "prebuilt_disks/irix-6.5.5-complete.qcow2",
            "prebuilt_disks/irix-6.5.5-complete-fixed.qcow2",
            "prebuilt_disks/irix-6.5.5-base.qcow2",
        ]
    else:
        candidates = sys.argv[1:]

    summary = {}
    for p in candidates:
        if not Path(p).exists():
            print(f"\n=== {p} ===")
            print("  NOT FOUND")
            continue
        try:
            ba, bf = scan_disk(p)
            summary[p] = (ba, bf)
        except Exception as e:
            print(f"  ERROR scanning: {e}")
            summary[p] = (-1, -1)

    print("\n=== SUMMARY ===")
    for p, (ba, bf) in summary.items():
        if ba == -1:
            status = "ERR"
        elif ba == 0 and bf == 0:
            status = "CLEAN"
        else:
            status = f"{ba} bad-allocated / {bf} bad-free"
        print(f"  {p}: {status}")


if __name__ == "__main__":
    main()
