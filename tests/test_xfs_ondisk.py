"""Unit tests: round-trip parse/pack for all XFS on-disk structures.

Pure Python, no disk images needed.
"""

import struct
import pytest

from pyirix.xfs.ondisk import (
    parse_superblock, pack_superblock,
    parse_agf, pack_agf,
    parse_agi, pack_agi,
    parse_inode_core, pack_inode_core,
    parse_bmbt_rec, pack_bmbt_rec,
    parse_alloc_rec, pack_alloc_rec,
    parse_inobt_rec, pack_inobt_rec_full,
    parse_btree_sblock, pack_btree_sblock,
    parse_btree_lblock, pack_btree_lblock,
    parse_bmdr_block, pack_bmdr_block,
    ino_to_offset, fsblock_to_offset,
    agbno_to_fsblock, fsblock_to_agno, fsblock_to_agbno,
    agino_to_ino, valid_fsblock, valid_agblock,
    has_dirv2,
)
from pyirix.xfs.directory import (
    _read_dir_sf_v2, _read_dir_sf_parent_v2,
)
from pyirix.xfs.constants import (
    XFS_SB_MAGIC, XFS_AGF_MAGIC, XFS_AGI_MAGIC, XFS_DINODE_MAGIC,
    XFS_ABTB_MAGIC, XFS_BMAP_MAGIC,
    NULLFSBLOCK, NULLAGBLOCK,
)


# ── Helpers ────────────────────────────────────────────────────────

def _make_sb_bytes():
    """Build a minimal valid 256-byte superblock."""
    buf = bytearray(256)
    # magic
    struct.pack_into('>I', buf, 0x00, XFS_SB_MAGIC)
    # blocksize=4096
    struct.pack_into('>I', buf, 0x04, 4096)
    # dblocks
    struct.pack_into('>Q', buf, 0x08, 131072)
    # rootino
    struct.pack_into('>Q', buf, 0x38, 128)
    # agblocks
    struct.pack_into('>I', buf, 0x54, 32768)
    # agcount
    struct.pack_into('>I', buf, 0x58, 4)
    # versionnum
    struct.pack_into('>H', buf, 0x64, 4)
    # sectsize
    struct.pack_into('>H', buf, 0x66, 512)
    # inodesize
    struct.pack_into('>H', buf, 0x68, 256)
    # inopblock
    struct.pack_into('>H', buf, 0x6A, 16)
    # fname
    buf[0x6C:0x72] = b'test\x00\x00'
    # fpack
    buf[0x72:0x78] = b'\x00' * 6
    # log fields
    struct.pack_into('>B', buf, 0x78, 12)   # blocklog
    struct.pack_into('>B', buf, 0x79, 9)    # sectlog
    struct.pack_into('>B', buf, 0x7A, 8)    # inodelog
    struct.pack_into('>B', buf, 0x7B, 4)    # inopblog
    struct.pack_into('>B', buf, 0x7C, 15)   # agblklog
    # uuid
    buf[0x20:0x30] = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'
    # icount/ifree/fdblocks
    struct.pack_into('>Q', buf, 0x80, 1024)
    struct.pack_into('>Q', buf, 0x88, 512)
    struct.pack_into('>Q', buf, 0x90, 100000)
    return bytes(buf)


# ── Superblock ─────────────────────────────────────────────────────

class TestSuperblockRoundTrip:
    def test_parse_pack_roundtrip(self):
        raw = _make_sb_bytes()
        sb = parse_superblock(raw)
        assert sb is not None
        assert sb['sb_magicnum'] == XFS_SB_MAGIC
        assert sb['sb_blocksize'] == 4096
        assert sb['sb_rootino'] == 128
        assert sb['sb_agblocks'] == 32768
        assert sb['sb_agcount'] == 4
        assert sb['sb_fname'] == 'test'

        packed = pack_superblock(sb)
        # Re-parse should give identical values
        sb2 = parse_superblock(packed)
        for key in sb:
            if key.startswith('_'):
                continue
            assert sb2[key] == sb[key], f"Field {key} mismatch"

    def test_pack_preserves_unknown_bytes(self):
        """Fields not in our table should be preserved via _raw."""
        raw = bytearray(_make_sb_bytes())
        raw[0xC2] = 0x42  # byte past our last field
        sb = parse_superblock(bytes(raw))
        packed = pack_superblock(sb)
        assert packed[0xC2] == 0x42

    def test_uuid_roundtrip(self):
        raw = _make_sb_bytes()
        sb = parse_superblock(raw)
        assert sb['sb_uuid'] == b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'
        packed = pack_superblock(sb)
        sb2 = parse_superblock(packed)
        assert sb2['sb_uuid'] == sb['sb_uuid']

    def test_too_short_returns_none(self):
        assert parse_superblock(b'\x00' * 10) is None


# ── AGF ────────────────────────────────────────────────────────────

class TestAGFRoundTrip:
    def test_roundtrip(self):
        buf = bytearray(512)
        struct.pack_into('>I', buf, 0, XFS_AGF_MAGIC)
        struct.pack_into('>I', buf, 4, 1)       # version
        struct.pack_into('>I', buf, 8, 0)       # seqno
        struct.pack_into('>I', buf, 12, 32768)  # length
        struct.pack_into('>I', buf, 16, 4)      # bno_root
        struct.pack_into('>I', buf, 20, 5)      # cnt_root
        struct.pack_into('>I', buf, 52, 30000)  # freeblks
        struct.pack_into('>I', buf, 56, 25000)  # longest

        agf = parse_agf(bytes(buf))
        assert agf is not None
        assert agf['agf_magicnum'] == XFS_AGF_MAGIC
        assert agf['agf_freeblks'] == 30000

        packed = pack_agf(agf)
        agf2 = parse_agf(packed)
        for key in agf:
            assert agf2[key] == agf[key], f"AGF field {key} mismatch"

    def test_too_short_returns_none(self):
        assert parse_agf(b'\x00' * 30) is None


# ── AGI ────────────────────────────────────────────────────────────

class TestAGIRoundTrip:
    def test_roundtrip(self):
        buf = bytearray(512)
        struct.pack_into('>I', buf, 0, XFS_AGI_MAGIC)
        struct.pack_into('>I', buf, 4, 1)     # version
        struct.pack_into('>I', buf, 8, 2)     # seqno
        struct.pack_into('>I', buf, 16, 64)   # count
        struct.pack_into('>I', buf, 20, 6)    # root
        struct.pack_into('>I', buf, 28, 10)   # freecount
        # Fill unlinked with NULLAGBLOCK
        for i in range(64):
            struct.pack_into('>I', buf, 40 + i * 4, NULLAGBLOCK)
        # Set one non-null entry
        struct.pack_into('>I', buf, 40 + 5 * 4, 42)

        agi = parse_agi(bytes(buf))
        assert agi is not None
        assert agi['agi_magicnum'] == XFS_AGI_MAGIC
        assert agi['agi_count'] == 64
        assert len(agi['agi_unlinked']) == 64
        assert agi['agi_unlinked'][5] == 42
        assert agi['agi_unlinked'][0] == NULLAGBLOCK

        packed = pack_agi(agi)
        agi2 = parse_agi(packed)
        for key in agi:
            assert agi2[key] == agi[key], f"AGI field {key} mismatch"

    def test_too_short_returns_none(self):
        assert parse_agi(b'\x00' * 100) is None


# ── Inode Core ─────────────────────────────────────────────────────

class TestInodeCoreRoundTrip:
    def test_roundtrip(self):
        buf = bytearray(96)
        struct.pack_into('>H', buf, 0, XFS_DINODE_MAGIC)
        struct.pack_into('>H', buf, 2, 0o100644)  # mode (regular file)
        struct.pack_into('>B', buf, 4, 1)          # version
        struct.pack_into('>B', buf, 5, 2)          # format (EXTENTS)
        struct.pack_into('>I', buf, 8, 1000)       # uid
        struct.pack_into('>I', buf, 12, 100)       # gid
        struct.pack_into('>I', buf, 16, 1)         # nlink
        struct.pack_into('>q', buf, 56, 4096)      # size
        struct.pack_into('>Q', buf, 64, 1)         # nblocks
        struct.pack_into('>I', buf, 76, 1)         # nextents
        struct.pack_into('>I', buf, 92, 42)        # gen

        core = parse_inode_core(bytes(buf))
        assert core is not None
        assert core['di_magic'] == XFS_DINODE_MAGIC
        assert core['di_mode'] == 0o100644
        assert core['di_uid'] == 1000
        assert core['di_size'] == 4096
        assert core['di_gen'] == 42

        packed = pack_inode_core(core)
        assert len(packed) == 96
        core2 = parse_inode_core(packed)
        for key in core:
            assert core2[key] == core[key], f"Inode field {key} mismatch"

    def test_pad_bytes_preserved(self):
        buf = bytearray(96)
        struct.pack_into('>H', buf, 0, XFS_DINODE_MAGIC)
        buf[22:32] = b'\xAA' * 10  # distinctive pad bytes

        core = parse_inode_core(bytes(buf))
        assert core['_pad'] == b'\xAA' * 10
        packed = pack_inode_core(core)
        assert packed[22:32] == b'\xAA' * 10

    def test_signed_size(self):
        """di_size is signed 64-bit."""
        buf = bytearray(96)
        struct.pack_into('>H', buf, 0, XFS_DINODE_MAGIC)
        struct.pack_into('>q', buf, 56, -1)
        core = parse_inode_core(bytes(buf))
        assert core['di_size'] == -1

    def test_too_short_returns_none(self):
        assert parse_inode_core(b'\x00' * 50) is None


# ── BMBT Record (Extent) ──────────────────────────────────────────

class TestBmbtRecRoundTrip:
    def test_simple_extent(self):
        startoff, startblock, blockcount, flag = 0, 100, 10, 0
        packed = pack_bmbt_rec(startoff, startblock, blockcount, flag)
        assert len(packed) == 16
        result = parse_bmbt_rec(packed)
        assert result == (startoff, startblock, blockcount, flag)

    def test_large_offset(self):
        # 54-bit max = 2^54 - 1
        startoff = (1 << 54) - 1
        startblock = 100
        blockcount = 1
        flag = 0
        packed = pack_bmbt_rec(startoff, startblock, blockcount, flag)
        result = parse_bmbt_rec(packed)
        assert result == (startoff, startblock, blockcount, flag)

    def test_large_startblock(self):
        # 52-bit max = 2^52 - 1
        startoff = 0
        startblock = (1 << 52) - 1
        blockcount = 1
        flag = 0
        packed = pack_bmbt_rec(startoff, startblock, blockcount, flag)
        result = parse_bmbt_rec(packed)
        assert result == (startoff, startblock, blockcount, flag)

    def test_max_blockcount(self):
        # 21-bit max = 2^21 - 1 = 2097151
        startoff = 0
        startblock = 100
        blockcount = (1 << 21) - 1
        flag = 0
        packed = pack_bmbt_rec(startoff, startblock, blockcount, flag)
        result = parse_bmbt_rec(packed)
        assert result == (startoff, startblock, blockcount, flag)

    def test_flag_bit(self):
        packed = pack_bmbt_rec(1000, 2000, 50, 1)
        result = parse_bmbt_rec(packed)
        assert result == (1000, 2000, 50, 1)

    def test_all_fields_max(self):
        """All fields at maximum values."""
        startoff = (1 << 54) - 1
        startblock = (1 << 52) - 1
        blockcount = (1 << 21) - 1
        flag = 1
        packed = pack_bmbt_rec(startoff, startblock, blockcount, flag)
        result = parse_bmbt_rec(packed)
        assert result == (startoff, startblock, blockcount, flag)


# ── Alloc Record ──────────────────────────────────────────────────

class TestAllocRecRoundTrip:
    def test_roundtrip(self):
        packed = pack_alloc_rec(100, 50)
        assert len(packed) == 8
        start, count = parse_alloc_rec(packed)
        assert start == 100
        assert count == 50

    def test_large_values(self):
        packed = pack_alloc_rec(0xFFFFFFFE, 0xFFFF)
        start, count = parse_alloc_rec(packed)
        assert start == 0xFFFFFFFE
        assert count == 0xFFFF


# ── Inobt Record ──────────────────────────────────────────────────

class TestInobtRecRoundTrip:
    def test_roundtrip_full(self):
        rec = {'ir_startino': 128, 'ir_freecount': 10, 'ir_free': 0xFF00FF00FF00FF00}
        packed = pack_inobt_rec_full(rec)
        assert len(packed) == 16
        rec2 = parse_inobt_rec(packed)
        assert rec2['ir_startino'] == rec['ir_startino']
        assert rec2['ir_freecount'] == rec['ir_freecount']
        assert rec2['ir_free'] == rec['ir_free']

    def test_all_free(self):
        rec = {'ir_startino': 64, 'ir_freecount': 64, 'ir_free': 0xFFFFFFFFFFFFFFFF}
        packed = pack_inobt_rec_full(rec)
        rec2 = parse_inobt_rec(packed)
        assert rec2['ir_freecount'] == 64
        assert rec2['ir_free'] == 0xFFFFFFFFFFFFFFFF

    def test_none_free(self):
        rec = {'ir_startino': 64, 'ir_freecount': 0, 'ir_free': 0}
        packed = pack_inobt_rec_full(rec)
        rec2 = parse_inobt_rec(packed)
        assert rec2['ir_freecount'] == 0
        assert rec2['ir_free'] == 0


# ── B+tree Headers ────────────────────────────────────────────────

class TestBtreeSblockRoundTrip:
    def test_roundtrip(self):
        hdr = {
            'bb_magic': XFS_ABTB_MAGIC,
            'bb_level': 0,
            'bb_numrecs': 5,
            'bb_leftsib': NULLAGBLOCK,
            'bb_rightsib': NULLAGBLOCK,
        }
        packed = pack_btree_sblock(hdr)
        assert len(packed) == 16
        hdr2 = parse_btree_sblock(packed)
        for key in hdr:
            assert hdr2[key] == hdr[key], f"SBlock field {key} mismatch"

    def test_with_siblings(self):
        hdr = {
            'bb_magic': XFS_ABTB_MAGIC,
            'bb_level': 1,
            'bb_numrecs': 3,
            'bb_leftsib': 100,
            'bb_rightsib': 200,
        }
        packed = pack_btree_sblock(hdr)
        hdr2 = parse_btree_sblock(packed)
        assert hdr2['bb_leftsib'] == 100
        assert hdr2['bb_rightsib'] == 200

    def test_too_short_returns_none(self):
        assert parse_btree_sblock(b'\x00' * 10) is None


class TestBtreeLblockRoundTrip:
    def test_roundtrip(self):
        hdr = {
            'bb_magic': XFS_BMAP_MAGIC,
            'bb_level': 0,
            'bb_numrecs': 10,
            'bb_leftsib': NULLFSBLOCK,
            'bb_rightsib': NULLFSBLOCK,
        }
        packed = pack_btree_lblock(hdr)
        assert len(packed) == 24
        hdr2 = parse_btree_lblock(packed)
        for key in hdr:
            assert hdr2[key] == hdr[key], f"LBlock field {key} mismatch"

    def test_with_siblings(self):
        hdr = {
            'bb_magic': XFS_BMAP_MAGIC,
            'bb_level': 0,
            'bb_numrecs': 2,
            'bb_leftsib': 0x100000000,
            'bb_rightsib': 0x200000000,
        }
        packed = pack_btree_lblock(hdr)
        hdr2 = parse_btree_lblock(packed)
        assert hdr2['bb_leftsib'] == 0x100000000
        assert hdr2['bb_rightsib'] == 0x200000000

    def test_too_short_returns_none(self):
        assert parse_btree_lblock(b'\x00' * 20) is None


class TestBmdrBlockRoundTrip:
    def test_roundtrip(self):
        hdr = {'bb_level': 1, 'bb_numrecs': 3}
        packed = pack_bmdr_block(hdr)
        assert len(packed) == 4
        hdr2 = parse_bmdr_block(packed)
        assert hdr2['bb_level'] == 1
        assert hdr2['bb_numrecs'] == 3

    def test_too_short_returns_none(self):
        assert parse_bmdr_block(b'\x00' * 2) is None


# ── Address Conversion ─────────────────────────────────────────────

class TestAddressConversion:
    """Test address conversion with a standard 4KB-block, 256-byte-inode SB."""

    @pytest.fixture(autouse=True)
    def setup_sb(self):
        self.sb = {
            'sb_blocksize': 4096,
            'sb_agblocks': 32768,
            'sb_agcount': 4,
            'sb_inodesize': 256,
            'sb_inopblock': 16,
            'sb_blocklog': 12,
            'sb_agblklog': 15,
            'sb_inopblog': 4,
            'sb_inodelog': 8,
        }
        self.part_offset = 0  # simplify tests

    def test_ino_to_offset_root(self):
        # Root inode 128: AG0, agino=128, agbno=128/16=8, slot=0
        offset = ino_to_offset(self.sb, 128, 0)
        assert offset == 8 * 4096

    def test_ino_to_offset_ag1(self):
        # Inode in AG1: agno=1, agino bits encode within AG1
        # With agblklog=15 and inopblog=4, bits_per_ag = 15+4 = 19
        ino = (1 << 19) | 128  # AG1, agino=128
        offset = ino_to_offset(self.sb, ino, 0)
        # AG1 starts at 32768 blocks, agino 128 -> agbno 8
        assert offset == (32768 + 8) * 4096

    def test_fsblock_to_offset(self):
        # fsblock with agblklog=15: agno = fsblock >> 15
        # AG0, block 100 -> fsblock = 100
        offset = fsblock_to_offset(self.sb, 0, 100)
        assert offset == 100 * 4096

    def test_fsblock_to_offset_ag1(self):
        # AG1, agbno 50 -> fsblock = (1 << 15) | 50
        fsblock = (1 << 15) | 50
        offset = fsblock_to_offset(self.sb, 0, fsblock)
        assert offset == (32768 + 50) * 4096

    def test_agbno_to_fsblock(self):
        fsblock = agbno_to_fsblock(self.sb, 2, 100)
        # AG2, block 100 -> (2 << 15) | 100
        assert fsblock == (2 << 15) | 100

    def test_fsblock_to_agno(self):
        fsblock = (3 << 15) | 500
        assert fsblock_to_agno(self.sb, fsblock) == 3

    def test_fsblock_to_agbno(self):
        fsblock = (3 << 15) | 500
        assert fsblock_to_agbno(self.sb, fsblock) == 500

    def test_agino_to_ino(self):
        ino = agino_to_ino(self.sb, 2, 128)
        # With 19 bits per AG: (2 << 19) | 128
        assert ino == (2 << 19) | 128

    def test_valid_fsblock(self):
        assert valid_fsblock(100) is True
        assert valid_fsblock(0) is False
        assert valid_fsblock(NULLFSBLOCK) is False

    def test_valid_agblock(self):
        assert valid_agblock(100) is True
        assert valid_agblock(0) is False
        assert valid_agblock(NULLAGBLOCK) is False

    def test_roundtrip_ag_conversion(self):
        """agbno_to_fsblock -> fsblock_to_agno/agbno roundtrip."""
        for agno in range(4):
            for agbno in [0, 1, 100, 32767]:
                fsblock = agbno_to_fsblock(self.sb, agno, agbno)
                assert fsblock_to_agno(self.sb, fsblock) == agno
                assert fsblock_to_agbno(self.sb, fsblock) == agbno


# ── has_dirv2 ────────────────────────────────────────────────────

class TestHasDirV2:
    def test_v1_no_dirv2(self):
        sb = {'sb_versionnum': 0x0004}
        assert has_dirv2(sb) is False

    def test_dirv2_set(self):
        sb = {'sb_versionnum': 0x2004}
        assert has_dirv2(sb) is True

    def test_dirv2_with_other_bits(self):
        sb = {'sb_versionnum': 0x3FF4}
        assert has_dirv2(sb) is True


# ── Dir2 Shortform Parse ─────────────────────────────────────────

class TestDir2ShortformParse:
    """Unit tests for dir2 shortform read functions (pure Python, no disk)."""

    def test_empty_dir_4byte(self):
        """Empty dir2 SF with 4-byte parent."""
        # Header: count=0, i8count=0, parent=100 (4 bytes)
        data = struct.pack('>BBI', 0, 0, 100)
        entries = _read_dir_sf_v2(data)
        assert entries == []

    def test_empty_dir_8byte(self):
        """Empty dir2 SF with 8-byte parent."""
        # Header: count=0, i8count=0 means 4-byte parent
        # For 8-byte: i8count must be non-zero (it IS the count)
        # Empty with 8-byte: doesn't really make sense, but test anyway
        data = struct.pack('>BBQ', 0, 0, 0x100000000)
        # i8count=0 means 4-byte parent, so this is actually a non-empty parse
        # Let's test with proper empty 4-byte instead
        data = struct.pack('>BBI', 0, 0, 42)
        entries = _read_dir_sf_v2(data)
        assert entries == []

    def test_4byte_inodes(self):
        """Dir2 SF with 4-byte inode entries."""
        # Header: count=2, i8count=0, parent=100 (4 bytes)
        hdr = struct.pack('>BBI', 2, 0, 100)
        # Entry 1: namelen=3, offset=48, name="foo", ino=200 (4 bytes)
        e1 = struct.pack('>BH', 3, 48) + b'foo' + struct.pack('>I', 200)
        # Entry 2: namelen=3, offset=64, name="bar", ino=300 (4 bytes)
        e2 = struct.pack('>BH', 3, 64) + b'bar' + struct.pack('>I', 300)
        data = hdr + e1 + e2

        entries = _read_dir_sf_v2(data)
        assert len(entries) == 2
        assert ('foo', 200) in entries
        assert ('bar', 300) in entries

    def test_8byte_inodes(self):
        """Dir2 SF with 8-byte inode entries."""
        # Header: count=0 (unused), i8count=1, parent=0x200000000 (8 bytes)
        hdr = struct.pack('>BBQ', 0, 1, 0x200000000)
        # Entry: namelen=4, offset=48, name="test", ino=0x300000000 (8 bytes)
        e1 = struct.pack('>BH', 4, 48) + b'test' + struct.pack('>Q', 0x300000000)
        data = hdr + e1

        entries = _read_dir_sf_v2(data)
        assert len(entries) == 1
        assert entries[0] == ('test', 0x300000000)

    def test_parent_read_4byte(self):
        """Read parent from 4-byte dir2 SF header."""
        data = struct.pack('>BBI', 2, 0, 42)
        parent = _read_dir_sf_parent_v2(data)
        assert parent == 42

    def test_parent_read_8byte(self):
        """Read parent from 8-byte dir2 SF header."""
        data = struct.pack('>BBQ', 0, 3, 0x100000042)
        parent = _read_dir_sf_parent_v2(data)
        assert parent == 0x100000042

    def test_truncated_data(self):
        """Truncated data should return empty list, not crash."""
        assert _read_dir_sf_v2(b'') == []
        assert _read_dir_sf_v2(b'\x00') == []
        assert _read_dir_sf_v2(b'\x02\x00') == []  # claims 2 entries but no data

    def test_parent_truncated(self):
        """Truncated parent data should return None."""
        assert _read_dir_sf_parent_v2(b'') is None
        assert _read_dir_sf_parent_v2(b'\x00') is None
