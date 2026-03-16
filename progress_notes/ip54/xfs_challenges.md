# XFS on IRIX: Challenges and Solutions Reference

This document consolidates lessons learned from working with XFS across multiple
sessions: the IP54 PROM filesystem driver, the `sgi_mcp` Python filesystem tools,
and the qemu-irix userland emulation work.

---

## 1. XFS Superblock Layout

The XFS superblock is sector 0 of each allocation group (AG). The "primary" superblock
(the one the PROM reads) is at LBN 0 of the XFS partition. Fields:

| Offset | Size | Field | Notes |
|--------|------|-------|-------|
| 0 | 4 | `sb_magicnum` | `0x58465342` ('XFSB') |
| 4 | 4 | `sb_blocksize` | Logical block size in bytes |
| 8 | 8 | `sb_dblocks` | Total data blocks (uint64) |
| 16 | 8 | `sb_rblocks` | Realtime blocks (uint64) |
| 24 | 8 | `sb_rextents` | Realtime extents (uint64) |
| 32 | 16 | `sb_uuid` | UUID (char[16]) |
| 48 | 8 | `sb_logstart` | Internal log start block (uint64) |
| 56 | 8 | `sb_rootino` | Root inode number (uint64) |
| 64 | 8 | `sb_rbmino` | Realtime bitmap inode (uint64) |
| 72 | 8 | `sb_rsumino` | Realtime summary inode (uint64) |
| 80 | 4 | `sb_rextsize` | Realtime extent size |
| 84 | 4 | `sb_agblocks` | Blocks per AG |
| 88 | 4 | `sb_agcount` | Number of AGs |
| 92 | 4 | `sb_rbmblocks` | Realtime bitmap blocks |
| 96 | 4 | `sb_logblocks` | Log length in blocks |
| **100** | **2** | **`sb_versionnum`** | **Version + feature flags (CRITICAL)** |
| 102 | 2 | `sb_sectsize` | Sector size in bytes |
| 104 | 2 | `sb_inodesize` | Inode size in bytes (typically 256) |
| 106 | 2 | `sb_inopblock` | Inodes per block |
| 108 | 6 | `sb_fname` | Filesystem name |
| 114 | 6 | `sb_fpack` | Pack name |
| 120 | 1 | `sb_blocklog` | log2(sb_blocksize) |
| 121 | 1 | `sb_sectlog` | log2(sb_sectsize) |
| 122 | 1 | `sb_inodelog` | log2(sb_inodesize) |
| 123 | 1 | `sb_inopblog` | log2(sb_inopblock) |
| 124 | 1 | `sb_agblklog` | log2(sb_agblocks), rounded up |
| 128 | 8 | `sb_icount` | Allocated inode count |
| 136 | 8 | `sb_ifree` | Free inode count |
| 144 | 8 | `sb_fdblocks` | Free data blocks |
| 192 | 1 | `sb_dirblklog` | log2 of directory block size |

**`sizeof(xfs_sb_t)` with O32 ABI = 200 bytes.**

---

## 2. PROM SASH Version Requirements

The IP54 PROM (and SGI SASH bootloader) only accepts XFS volumes up to version 4.
The `XFS_SB_GOOD_SASH_VERSION` macro in `xfs_sb.h`:

```c
#define XFS_SB_VERSION_NUM(sbp)  ((sbp)->sb_versionnum & 0x000f)

#define XFS_SB_GOOD_SASH_VERSION(sbp)   \
    ((((sbp)->sb_versionnum >= XFS_SB_VERSION_1) && \
      ((sbp)->sb_versionnum <= XFS_SB_VERSION_3)) || \
     ((XFS_SB_VERSION_NUM(sbp) == XFS_SB_VERSION_4) && \
      !((sbp)->sb_versionnum & ~XFS_SB_VERSION_OKSASHBITS)))
```

- `XFS_SB_VERSION_OKSASHBITS = 0x3FFF`
- Versions 1-3: accepted unconditionally (no feature bit check)
- Version 4: accepted if ALL bits are within `0x3FFF` (bits 0-13)
- Version 5+: **always rejected** (modern XFS, Linux mkfs.xfs default since ~2013)

### Accepted feature bits (for version 4):

| Bit | Value | Name | Meaning |
|-----|-------|------|---------|
| 4 | 0x0010 | ATTRBIT | Extended attributes |
| 5 | 0x0020 | NLINKBIT | 32-bit link count |
| 6 | 0x0040 | QUOTABIT | Disk quota |
| 7 | 0x0080 | ALIGNBIT | Inode alignment |
| 8 | 0x0100 | DALIGNBIT | Data alignment |
| 9 | 0x0200 | SHAREDBIT | Shared filesystem |
| 12 | 0x1000 | EXTFLGBIT | Extent flag |
| 13 | 0x2000 | DIRV2BIT | Directory v2 format |

### Common version values on IRIX disks:

- `0x1094` = version 4 + ALIGNBIT(0x80) + ATTRBIT(0x10) + EXTFLGBIT(0x1000) → **ACCEPTED**
- `0x2004` = version 4 + DIRV2BIT(0x2000) → **ACCEPTED** (bits 0-13 only)
- `0xb004` = version 4 + bits 14-15 set → **REJECTED** (outside 0x3FFF)
- `0x0005` = version 5 → **REJECTED** (version_num=5, not 4)

### Repair: force versionnum for PROM compatibility

If a disk has an incompatible version, patch 2 bytes at superblock offset 100:
```bash
# In Python (after reading raw disk at XFS partition offset):
# patch bytes at (partition_lba * 512) + 100
```

Use the `xfs_repair_superblock` MCP tool.

---

## 3. V1 vs V2 XFS Directory Formats

IRIX 6.5 uses XFS V1 directory format (sometimes called "XFS version 1 directories"
internally). Modern Linux XFS uses dir2 format exclusively. Key differences:

### Shortform directories

**V1 (IRIX):** 9-byte header
```
[0..7]  parent inode (uint64)
[8]     entry count (uint8)
--- entries: ---
[0..7]  inode number (uint64)
[8]     name length (uint8)
[9..N]  name bytes (no null terminator)
```

**dir2/V2:** Variable-length header
```
[0]     entry count (uint8)
[1]     i8count (entries with 8-byte inodes) (uint8)
[2..5]  parent inode (uint32) or [2..9] (uint64) if i8count > 0
--- entries: ---
[0..3]  inode (uint32) or [0..7] (uint64) if 8-byte inode
[N]     name length (uint8)
[N+1..] name bytes
[last]  file type (uint8, in newer versions)
```

### Leaf blocks

**V1 (IRIX):** Magic `0xfeeb` stored at byte **8** (inside `xfs_da_blkinfo_t` header),
not at byte 0. The block layout is:
```
[0..7]   xfs_da_blkinfo_t: forw(4), back(4), magic(2), pad(2) -- magic at offset 8!
[8..9]   0xfeeb
[10..]   hash+nameidx entries, then names at end of block
```

**dir2 block/data:** Magic at byte **0**:
- `XFS_DIR2_BLOCK_MAGIC = 0x58443242` ('XD2B') — combined block
- `XFS_DIR2_DATA_MAGIC  = 0x58443244` ('XD2D') — data block
- `XFS_DIR2_FREE_MAGIC  = 0x58443246` ('XD2F') — freespace block

### Detection code in sgi_fs.py

The Python reader in `sgi_mcp/sgi_fs.py` handles both:
- `_xfs_read_dir_sf()`: V1 shortform with 9-byte header
- `_xfs_parse_dir_v1_leaf()`: V1 leaf with `0xfeeb` at byte 8
- `_xfs_parse_dir_data_block()`: dir2 block/data format

**Critical fix (from qemu_irix_userland work):** The original Python reader looked
for magic at byte 0 of leaf blocks, which is wrong for V1. Fix: check bytes 8-9
for `0xfeeb` before checking byte 0 for dir2 magic.

---

## 4. AG (Allocation Group) Math

XFS divides the filesystem into AGs (typically 4-8). All block addresses and inode
numbers encode the AG number.

### fsblock to disk byte offset

```python
def fsblock_to_byte(sb, fsblock):
    agblklog = sb['agblklog']
    agno  = fsblock >> agblklog
    agbno = fsblock & ((1 << agblklog) - 1)
    disk_block = agno * sb['agblocks'] + agbno
    return part_offset + disk_block * sb['blocksize']
```

### inode number to disk byte offset

```python
def ino_to_byte(sb, ino):
    inopblog  = sb['inopblog']
    agblklog  = sb['agblklog']
    agno  = ino >> (agblklog + inopblog)
    agbno = (ino >> inopblog) & ((1 << agblklog) - 1)
    off   = ino & ((1 << inopblog) - 1)
    disk_block = agno * sb['agblocks'] + agbno
    return part_offset + disk_block * sb['blocksize'] + off * sb['inodesize']
```

### Extent record decoding (packed 128-bit)

```python
def decode_extent(high, low):
    # 128-bit packed: [flag:1][startoff:54][startblock:52][blockcount:21]
    flag       = (high >> 31) & 1
    startoff   = ((high & 0x7FFFFFFF) << 23) | (low >> 41)
    startblock = (low >> 21) & 0x1FFFFF_FFFFFFFF  # 52 bits... see full formula
    blockcount = low & 0x1FFFFF
    return flag, startoff, startblock, blockcount
```

---

## 5. Reading qcow2 Disks Without qemu-img dd

`qemu-img dd skip=N` does not support large sector offsets (N > some threshold)
on the version of QEMU we use. Options:

### Option A: Python qcow2 L1/L2 table reader (no external tools)

```python
import struct, os

def read_qcow2_sector(path, lba):
    with open(path, 'rb') as f:
        hdr = f.read(104)
    cluster_bits = struct.unpack_from('>I', hdr, 20)[0]
    l1_off = struct.unpack_from('>Q', hdr, 40)[0]
    l1_size = struct.unpack_from('>I', hdr, 36)[0]
    cs = 1 << cluster_bits
    l2_bits = cluster_bits - 3  # L2 entries = cluster_size / 8

    virt = lba * 512
    l1_idx = virt >> (cluster_bits + l2_bits)
    l2_idx = (virt >> cluster_bits) & ((1 << l2_bits) - 1)
    byte_in_cluster = virt & (cs - 1)

    with open(path, 'rb') as f:
        f.seek(l1_off + l1_idx * 8)
        l2_entry_ptr = struct.unpack('>Q', f.read(8))[0] & ~(1 << 63)
        f.seek(l2_entry_ptr + l2_idx * 8)
        cluster_ptr = struct.unpack('>Q', f.read(8))[0] & ~(1 << 63)
        f.seek(cluster_ptr + byte_in_cluster)
        return f.read(512)
```

### Option B: Use sgi_fs.py open_disk_image()

`sgi_mcp/sgi_fs.py` has `open_disk_image(path)` which transparently converts
qcow2 to raw via `qemu-img convert` into a temp file, then returns a file handle
to the raw data. This handles all seeks correctly.

### Option C: Full qemu-img convert

```bash
/workspace/qemu-sgi-repo/build-new/qemu-img convert \
    -f qcow2 -O raw input.qcow2 output.raw
dd if=output.raw bs=512 skip=266240 count=1 | xxd | head -8
```

---

## 6. Struct Layout Sensitivity (PROM xfs_sb_t)

The IP54 PROM's `xfs_sb_t` layout depends on compilation ABI:

- With O32/ABI32 (`_MIPS_SIM = _MIPS_SIM_ABI32 = 1`): `XFS_BIG_FILESYSTEMS = 0`
  - Memory-based block types (`xfs_fsblock_t`, `xfs_rfsblock_t`) → `uint32_t`
  - Disk-based block types (`xfs_drfsbno_t`, `xfs_dfsbno_t`) → `uint64_t` always
  - `xfs_ino_t` → `uint64_t` always
  - **`sizeof(xfs_sb_t) = 200`**
- With N32 or N64: `XFS_BIG_FILESYSTEMS = 1`, memory types also become 64-bit,
  struct grows larger → **struct would NOT match on-disk layout for some fields**

The IP54 PROM is compiled O32, so the struct matches the on-disk layout correctly.

**Verification test:** `tests/test_xfs_struct.py::TestXfsSbLayout` confirms all
field offsets match expected on-disk positions by compiling a C file with the
cross-compiler and reading `__builtin_offsetof` values from the `.data` section.

---

## 7. Known Issues and Status

| Issue | Status | Tool/Fix |
|-------|--------|----------|
| V1 leaf directory magic `0xfeeb` at byte 8 | **Fixed** in sgi_fs.py | `_xfs_parse_dir_v1_leaf` |
| V1 shortform 9-byte header | **Fixed** in sgi_fs.py | `_xfs_read_dir_sf` |
| PROM reads ver=0x0100 (sb_inodesize) instead of 0x1094 | **Open** | See xfs_prom_struct_debug.md |
| qemu-img dd fails for large LBAs | **Workaround** | Python qcow2 reader / qemu-img convert |
| Version 5 XFS not bootable from PROM/SASH | **By design** | Use `xfs_repair_superblock` to downgrade |
| No XFS write support in Python tools | **By design** | `xfs_repair_superblock` patches raw bytes only |
