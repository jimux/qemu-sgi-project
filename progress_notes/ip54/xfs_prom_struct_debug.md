# IP54 PROM XFS: Struct Layout Debugging Session

## Problem Statement

The IP54 PROM boots to the System Maintenance Menu and attempts to load
`/unix.new` from the IRIX disk. The XFS filesystem check (`_xfs_checkfs` in
`src/libsc/xfs/xfs.c`) fails with:

```
[xfs] magic=0x58465342 ver=0x100
[xfs] magic/ver check FAILED
```

The XFS superblock magic is correct (`XFSB` = `0x58465342`), but `ver=0x100`
is wrong. The correct version from the actual on-disk data at bytes 100-101 is
`0x1094` (XFS version 4 with feature bits ALIGN+ATTR+EXTFLG, accepted by PROM
SASH version check).

`0x0100` corresponds to disk bytes **104-105**, which is `sb_inodesize = 256`.
The PROM is reading from byte offset 104 instead of 100.

---

## Investigation Steps

### Step 1: Add raw-byte diagnostics to xfs.c

Two `printf` statements were added to `src/libsc/xfs/xfs.c`:

**In `_xfs_get_superblock`** (after disk read, before `bcopy`):
```c
printf("[xfs] buf[100..101]=0x%02x%02x sizeof_sb=%d\n",
       (unsigned)(unsigned char)buf[100],
       (unsigned)(unsigned char)buf[101],
       (int)sizeof(xfs_sb_t));
```

**In `_xfs_checkfs`** (replacing the simple magic/ver print):
```c
printf("[xfs] off_ver=%d sbp[100..101]=0x%02x%02x magic=0x%x ver=0x%x\n",
       (int)__builtin_offsetof(xfs_sb_t, sb_versionnum),
       (unsigned)(unsigned char)((char*)sbp)[100],
       (unsigned)(unsigned char)((char*)sbp)[101],
       (unsigned)sbp->sb_magicnum,
       (unsigned)sbp->sb_versionnum);
```

These diagnostics show: (a) what bytes the disk actually has at offset 100,
(b) what bytes the struct buffer has at offset 100 after `bcopy`, and (c)
what `offsetof` thinks the field offset is at compile time.

### Step 2: Confirm disk data is correct

Used a Python qcow2 L1/L2 table reader (implemented inline in the test) to
read the XFS partition directly from the qcow2 disk image without `qemu-img dd`.

Result: bytes 96-107 of the XFS superblock = `00 00 04 90 10 94 02 00 01 00 00 10`

| Bytes | Offset | Value | Field |
|-------|--------|-------|-------|
| `00 00 04 90` | 96 | 0x0490 | `sb_logblocks` |
| `10 94` | 100 | **0x1094** | `sb_versionnum` ✓ |
| `02 00` | 102 | 512 | `sb_sectsize` |
| `01 00` | 104 | **0x0100** = 256 | `sb_inodesize` |
| `00 10` | 106 | 16 | `sb_inopblock` |

Disk data is correct. `0x1094` is at bytes 100-101 as expected.

### Step 3: Confirm struct layout is correct (cross-compiled test)

Created `tests/test_xfs_struct.py` with `TestXfsSbLayout` which:
1. Compiles a C source with `mips-elf-gcc -march=mips3 -mabi=32 -EB -G 0`
   using `__builtin_offsetof` for every `xfs_sb_t` field
2. Extracts values from `.data` section via `mips-elf-objdump -j .data -s`
3. Compares against expected on-disk offsets

**All 9 struct layout tests PASSED:**
- `sb_versionnum` at offset **100** ✓
- `sizeof(xfs_sb_t)` = **200** ✓
- `XFS_BIG_FILESYSTEMS` = **0** ✓ (correct for O32 ABI)
- All `uint64_t` disk fields at expected 8-byte-aligned positions ✓

### Step 4: Version check logic confirmed correct

`TestVersionCheckLogic` (8 pure-Python tests, all pass):
- `0x1094` → ACCEPTED (version_num=4, all feature bits within `0x3FFF` mask)
- `0x0100` → REJECTED (version_num=0, not 1-3 or 4)

---

## Remaining Mystery

**The contradiction:**
- Struct layout test: `sb_versionnum` at byte offset **100**
- Disk data: bytes 100-101 = **0x1094** (correct)
- PROM output: `ver=0x100` = disk bytes 104-105 (`sb_inodesize`)

The PROM is somehow accessing offset **104** instead of **100**, even though the
`offsetof` test shows the field is at offset 100.

---

## Next Steps to Resolve

### Primary: Disassemble the compiled PROM xfs.o

```bash
/opt/cross/mips-elf/bin/mips-elf-objdump -d \
    /workspace/prom-building/build/libsc/xfs/xfs.o \
    | grep -A 120 "<_xfs_checkfs>"
```

Look for the `lhu` instruction that loads `sb_versionnum`. The instruction will
have a constant byte offset:
- If offset = `0x64` (100 decimal) → struct access is correct; bug is in the
  read path (disk read fetching wrong sector?) or `bcopy` (wrong size/overlap?)
- If offset = `0x68` (104 decimal) → GCC is generating wrong offset; the struct
  layout in the PROM build context differs from the test context

**Note:** The PROM build uses `-G 8` (8-byte GP-relative threshold for small-data
optimizations), while the test used `-G 0`. This is unlikely to affect struct
field offsets (GP-relative addressing affects global variable placement, not
struct member offsets), but it's worth verifying.

### Alternative hypothesis: `malbuf` allocation alignment

`_xfs_get_superblock` calls `dmabuf_malloc(sizeof(xfs_sb_t))` to get `buf`.
If `dmabuf_malloc` returns memory with a specific alignment requirement and
`bcopy` interprets sizes differently (e.g., copies only 96 bytes instead of
200), the first 100 bytes would be correct but the version field at byte 100
would be uninitialized or carry stale data.

Check: add `printf("[xfs] buf=%p\n", buf)` and verify the returned address
is reasonable, and add a sanity check `printf("[xfs] buf[0..3]=0x%08x\n", ...)`.

---

## Files Modified

| File | Change |
|------|--------|
| `src/libsc/xfs/xfs.c` | Added `buf[100..101]` and `offsetof`/raw-byte diagnostics |
| `tests/test_xfs_struct.py` | New — struct layout, disk data, and version logic tests |

---

## Tests

```bash
cd /workspace/prom-building
python3 -m pytest tests/test_xfs_struct.py -v
```

Pass/fail summary:
- `TestXfsSbLayout` (9 tests): **all PASS** (requires cross-compiler)
- `TestVersionCheckLogic` (8 tests): **all PASS** (pure Python, no deps)
- `TestDiskXfsSuperblock` (5 tests): **SKIP** (requires ip54-test disk at expected path)
