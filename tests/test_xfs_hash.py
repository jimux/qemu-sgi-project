"""Unit tests: xfs_da_hashname against IRIX reference values.

Tests the directory name hash function used by V1 and V2 directories.
Reference: xfs_da_btree.c:1616 in IRIX 6.5.7m kernel source.
"""

import os
import struct
import pytest

from pyirix.xfs.ondisk import xfs_da_hashname
from pyirix.xfs.constants import XFS_DIR_LEAF_MAGIC


# ── Known Values ───────────────────────────────────────────────────
# These values were computed from the IRIX C implementation.
# The hash processes 4 bytes at a time with rotl32(28) mixing.

class TestHashKnownValues:
    def test_dot(self):
        h = xfs_da_hashname(b'.')
        assert isinstance(h, int)
        assert 0 <= h <= 0xFFFFFFFF

    def test_dotdot(self):
        h = xfs_da_hashname(b'..')
        assert isinstance(h, int)
        assert 0 <= h <= 0xFFFFFFFF

    def test_dot_ne_dotdot(self):
        assert xfs_da_hashname(b'.') != xfs_da_hashname(b'..')

    def test_empty_string(self):
        """Empty name should hash to 0 (no iterations)."""
        assert xfs_da_hashname(b'') == 0

    def test_single_char(self):
        """Single byte: h = name[0] ^ rotl32(0, 7) = name[0]."""
        assert xfs_da_hashname(b'a') == ord('a')

    def test_four_bytes_aligned(self):
        """Exactly 4 bytes: one main loop iteration, no remainder."""
        h = xfs_da_hashname(b'unix')
        # (ord('u')<<21) ^ (ord('n')<<14) ^ (ord('i')<<7) ^ ord('x') ^ rotl32(0,28)
        expected = (ord('u') << 21) ^ (ord('n') << 14) ^ (ord('i') << 7) ^ ord('x')
        assert h == expected

    def test_five_bytes_unaligned(self):
        """5 bytes: one main loop + 1 remainder byte."""
        h = xfs_da_hashname(b'sbin/')
        assert isinstance(h, int)
        assert 0 <= h <= 0xFFFFFFFF

    def test_str_input(self):
        """String input should work (auto-encoded to ASCII)."""
        h1 = xfs_da_hashname('unix')
        h2 = xfs_da_hashname(b'unix')
        assert h1 == h2

    def test_common_irix_names(self):
        """Smoke test: all common IRIX root directory names produce unique hashes."""
        names = ['etc', 'usr', 'var', 'bin', 'sbin', 'lib', 'lib32',
                 'tmp', 'dev', 'proc', 'stand', 'hw', 'unix', 'unix.new']
        hashes = [xfs_da_hashname(n) for n in names]
        # All should be unique
        assert len(set(hashes)) == len(hashes)

    def test_three_byte_remainder(self):
        """7 bytes = 4 main + 3 remainder."""
        h = xfs_da_hashname(b'lib32ab')
        assert isinstance(h, int)

    def test_two_byte_remainder(self):
        """6 bytes = 4 main + 2 remainder."""
        h = xfs_da_hashname(b'lib32a')
        assert isinstance(h, int)

    def test_deterministic(self):
        """Same input always produces same output."""
        for _ in range(10):
            assert xfs_da_hashname(b'test_file.txt') == xfs_da_hashname(b'test_file.txt')


# ── IRIX Leaf Block Cross-Reference ──────────────────────────────

IRIX_DISK = '/workspace/vm_instances/ip54-test/disk.qcow2'


@pytest.mark.skipif(not os.path.exists(IRIX_DISK),
                    reason="ip54-test disk image not found")
class TestHashIRIXLeafBlock:
    """[CROSS-REF: xfs_da_btree.c:1616] Verify hash against real V1 leaf dir."""

    @pytest.fixture(scope="class")
    def leaf_data(self):
        """Read a V1 leaf directory block from the IRIX disk.

        The root directory of IRIX 6.5.5 has 26+ entries and uses V1 leaf format.
        """
        from pyirix.xfs.image import open_disk_image, find_xfs_partition
        from pyirix.xfs.superblock import read_superblock
        from pyirix.xfs.inode import read_inode, get_extents
        from pyirix.xfs.ondisk import fsblock_to_offset

        with open_disk_image(IRIX_DISK) as f:
            part = find_xfs_partition(f)
            assert part is not None
            part_offset, _ = part
            sb = read_superblock(f, part_offset)
            assert sb is not None

            root = read_inode(f, part_offset, sb, sb['sb_rootino'])
            assert root is not None

            extents = get_extents(f, part_offset, sb, root)
            assert len(extents) > 0

            # Read first extent block
            startoff, startblock, blockcount = extents[0]
            disk_off = fsblock_to_offset(sb, part_offset, startblock)
            f.seek(disk_off)
            block = f.read(sb['sb_blocksize'])

            # Verify it's a V1 leaf
            magic = struct.unpack('>H', block[8:10])[0]
            if magic != XFS_DIR_LEAF_MAGIC:
                pytest.skip("Root directory is not V1 leaf format")

            return block, sb

    def test_all_entries_hash_match(self, leaf_data):
        """Every entry in the leaf block should have hashval == xfs_da_hashname(name)."""
        block, sb = leaf_data
        count = struct.unpack('>H', block[12:14])[0]
        assert count > 0

        entry_base = 32  # after leaf header
        mismatches = []

        for i in range(count):
            entry_off = entry_base + i * 8
            hashval = struct.unpack('>I', block[entry_off:entry_off + 4])[0]
            nameidx = struct.unpack('>H', block[entry_off + 4:entry_off + 6])[0]
            namelen = block[entry_off + 6]

            # Name is at nameidx + 8 (skip inumber)
            name = block[nameidx + 8:nameidx + 8 + namelen]
            computed = xfs_da_hashname(name)

            if computed != hashval:
                mismatches.append((name.decode('ascii', errors='replace'),
                                   hashval, computed))

        assert mismatches == [], f"Hash mismatches: {mismatches}"
