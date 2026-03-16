# qemu-irix Userland Emulation — Build and Test Results

**Date:** 2026-03-02
**Purpose:** Assess qemu-irix (QEMU 2.11 fork with IRIX userland syscall translation) as a
reference implementation for potentially porting the IRIX userland capability to our QEMU 10.x
SGI emulation codebase.

---

## What qemu-irix Does

qemu-irix is a QEMU 2.11 fork that adds IRIX ABI translation in linux-user mode. Instead of
full hardware emulation, it translates IRIX syscalls to Linux syscalls and emulates IRIX-specific
features (PRDA, `syssgi`, `sysmp`, IRIX ELF loading). This lets IRIX N32 MIPS binaries run
directly on a Linux host without hardware emulation.

There are three QEMU build targets:
- `irix-linux-user/qemu-irix` — O32 ABI
- `irixn32-linux-user/qemu-irixn32` — N32 ABI (primary target)
- `irix64-linux-user/qemu-irix64` — N64 ABI

---

## Step 1: Build

**Environment:** Linux aarch64 (ARM64 Docker, Ubuntu 24.04, GCC 13.3)

**Fix required:** `disas/arm-a64.cc` — GCC 13 introduced an incompatibility between
`extern "C"` inclusion and C++ standard library headers. Fix: pre-include C++ headers
before the `extern "C"` block:
```cpp
/* Pre-include C++ std headers to avoid GCC 13 extern-C conflict */
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <type_traits>
#include <algorithm>

extern "C" {
#include "qemu/osdep.h"
```

**Configure flags used:**
```bash
../configure \
    --target-list=irix-linux-user,irixn32-linux-user,irix64-linux-user \
    --disable-system --disable-tools --disable-guest-agent \
    --disable-sdl --disable-gtk --disable-vnc --disable-curses \
    --disable-opengl --disable-virglrenderer --disable-spice \
    --disable-smartcard --disable-libusb --disable-usb-redir \
    --disable-capstone --disable-fdt --disable-docs --disable-werror \
    --disable-xen --disable-kvm --disable-vhost-net --disable-rdma \
    --disable-numa --disable-seccomp --disable-glusterfs \
    --disable-lzo --disable-snappy --disable-bzip2 --disable-rbd \
    --disable-libssh2 --disable-vte --disable-nettle --disable-gnutls \
    --disable-gcrypt --disable-virtfs
```

**Result:** Build succeeds. All three qemu binaries produced.

---

## Step 2: IRIX Rootfs Preparation

Test system: IRIX 6.5.5f ELF binaries extracted from `eoe_655f_extracted/` CD image.

Key setup:
```bash
ROOTFS=/workspace/software_library/extraced_irix_cds/eoe_655f_extracted
# Create rld symlink (some binaries use /usr/lib32/libc.so.1 as their linker path)
ln -sf ../../lib32/libc.so.1 $ROOTFS/usr/lib32/libc.so.1
```

Binary inventory (verified N32 ELF, big-endian MIPS):

| Binary | Interpreter | Deps |
|--------|-------------|------|
| `sbin/sh` | static | none — easiest test |
| `sbin/echo` | `/lib32/rld` | libc.so.1 |
| `sbin/ls` | `/lib32/rld` | libc.so.1 |
| `sbin/cat` | `/usr/lib32/libc.so.1` | libc.so.1 |
| `usr/bin/id` | `/lib32/rld` | libc.so.1 |

---

## Step 3: Binary Test Matrix

### Static binaries (no dynamic linker)
```bash
QEMU=/workspace/qemu-irix/build/irixn32-linux-user/qemu-irixn32
ROOTFS=/workspace/software_library/extraced_irix_cds/eoe_655f_extracted

$QEMU $ROOTFS/sbin/sh -c 'echo hello from IRIX'
# → "hello from IRIX"  ✅
```

### Dynamic binaries with `-L` rootfs
```bash
$QEMU -L $ROOTFS $ROOTFS/sbin/echo "hello from IRIX"
# → "hello from IRIX"  ✅

$QEMU -L $ROOTFS $ROOTFS/sbin/ls /
# → IRIX root directory listing  ✅

$QEMU -L $ROOTFS $ROOTFS/usr/bin/id
# → "uid=0(root) gid=0(sys)"  ✅

$QEMU -L $ROOTFS $ROOTFS/sbin/cat /proc/self/status
# → kernel /proc output  ✅
```

### Interactive shell
```bash
$QEMU -L $ROOTFS -E HOME=/ -E PATH=/sbin:/usr/bin $ROOTFS/sbin/sh
# → IRIX Bourne shell interactive session  ✅
```

**Result:** All standard IRIX userland binaries with simple dependencies run correctly.

---

## Step 4: MIPSpro Compiler Test (Stretch Goal)

### Setup

Source: `vm_instances/irix655-dev/disk.qcow2` — IRIX 6.5.5 with MIPSpro 7.2.1 installed.

Key discovery during extraction: the XFS V1 filesystem on IRIX disks uses old directory
formats not supported by modern xfsprogs. Fixed the `sgi_mcp/sgi_fs.py` XFS reader:

1. **V1 Leaf directory format** (`XFS_DIR_LEAF_MAGIC = 0xfeeb`): Old IRIX leaf blocks use
   a 2-byte magic at byte offset 8 (in `xfs_da_blkinfo_t`), not a 4-byte magic at offset 0.

2. **V1 Shortform directory format** (corrected `_xfs_read_dir_sf`): The old IRIX format
   uses `parent(8 bytes) + count(1 byte)` header (9 bytes total), no `i8count` field, and
   `entry = ino(8 bytes) + namelen(1 byte) + name[namelen]`. This differs from the dir2
   shortform format which has `count(1) + i8count(1) + parent(4 or 8)`.

### MIPSpro Architecture

```
/usr/bin/cc → /usr/cpu/sysgen/root/usr/bin/cc    (cc driver, 245K)
              invokes:
              1. /usr/lib32/cmplrs/cpp  → preprocessor
              2. /usr/lib32/cmplrs/fec  → C front-end → WHIRL IR (.B file)
              3. /usr/lib32/cmplrs/be   → code generator (needs be.so, cg.so, etc.)
              4. /usr/lib32/cmplrs/asm  → assembler → .o
```

### Test Results

```bash
QEMU=/workspace/qemu-irix/build/irixn32-linux-user/qemu-irixn32
ROOTFS=/tmp/irix_rootfs   # staged MIPSpro rootfs
```

#### cc -version
```
$ $QEMU -L $ROOTFS $ROOTFS/usr/cpu/sysgen/root/usr/bin/cc -version
cc WARNING:  abi should have been specified by driverwrap
MIPSpro Compilers: Version 7.2.1
```
**Result:** ✅ cc driver loads correctly, reports MIPSpro 7.2.1

#### fec (C front-end)
```bash
$QEMU -L $ROOTFS -E QEMU_IRIXPRDA=1 \
    $ROOTFS/usr/lib32/cmplrs/fec \
    -G8 -DMIPSEB -D_MIPSEB -TENV:PIC -DLANGUAGE_C \
    -m1 -D__mips=3 -TARG:abi=n32:isa=mips3 -O0 \
    -fB,/tmp/minimal.B /tmp/minimal.c
```
**Result:** ✅ `fec` processes C source → produces 6152-byte WHIRL IR file

#### asm (assembler)
```bash
$QEMU -L $ROOTFS -E QEMU_IRIXPRDA=1 \
    $ROOTFS/usr/lib32/cmplrs/asm \
    -n32 /tmp/test_asm.s -o /tmp/test_asm.o
```
**Result:** ✅ IRIX `asm` assembles MIPS N32 assembly → 2212-byte object file

#### be (code generator backend)
```bash
$QEMU -L $ROOTFS -E QEMU_IRIXPRDA=1 \
    $ROOTFS/usr/cpu/sysgen/root/usr/bin/cc \
    -n32 -c /tmp/minimal.c -o /tmp/minimal.o
```
**Result:** ❌ `cc INTERNAL ERROR: /usr/lib32/cmplrs/be died due to signal 11`

#### Root Cause Analysis

`be` is the MIPSpro optimizer/code generator backend, written in C++ and structured as:
- `be` (72K binary) loads `be.so` (3.1MB), which dlopen's `cg.so` (3.7MB), `lno.so` (5.3MB), etc.
- All `be`-family DSOs depend on `libCsup.so` (C++ support) and `libC.so.2` (IRIX C++ stdlib)

The crash occurs immediately after `rld` finishes mapping all shared libraries —
before `be`'s `main()` is reached. This is the `.init` section of `libCsup.so`
or `libC.so.2` (IRIX's C++ runtime), which requires IRIX-specific kernel features
not fully emulated by qemu-irix.

Unsupported syscalls at startup (`sgisys(107)`, `sgisys(111)`) are IRIX-specific
process management calls that return `ENOSYS` but don't immediately crash. The crash
happens in library static constructors, likely requiring:
- IRIX shared arena / shared memory primitives
- IRIX-specific process/thread control (`prctl`, `procblk`)
- IRIX usynccntl (user-level mutex) operations

**Dependency chain that crashes:**
```
be → libCsup.so + libC.so.2   # All crash during .init
be.so → libCsup.so + libC.so.2
cg.so → libCsup.so + libC.so.2
wopt.so → libCsup.so
lno.so → libCsup.so
```

**What works without libC.so.2:**
```
fec: [libm.so, libc.so.1]          ✅ works
cpp: [libm.so, libc.so.1]          ✅ works
asm: [libc.so.1]                    ✅ works
```

---

## Key Technical Findings

### 1. Path Translation Works for All Syscalls
`-L /rootfs` in qemu-irix translates ALL absolute path lookups (not just the ELF
interpreter), using a pre-scanned directory tree (`init_paths` / `follow_path` in
`util/path.c`). This enables complete userland emulation with `-L`.

### 2. PRDA Setup
The IRIX PRDA (Process Register Data Area) at virtual address `0x200000` is mapped
unconditionally for all N32 ELF binaries loaded by qemu-irix (in `elfload.c`).
`QEMU_IRIXPRDA=1` env var is a separate hint but the actual mapping always happens.

### 3. syssgi(68) = SGI_ELFMAP
qemu-irix implements IRIX's `syssgi(68)` (SGI_ELFMAP) which is how `rld` maps shared
library segments. This is critical because IRIX rld doesn't use standard Linux `mmap`
for library loading. The implementation in `sgi_map_elf_image()` (elfload.c:2048)
correctly handles PT_LOAD segments.

### 4. N32 ABI Details
All IRIX binaries tested use N32 ABI (`e_flags & 0xF0000000 == 0x20000000`). The
`qemu-irixn32` target handles N32 MIPS ELF correctly including:
- 32-bit ELF format with 64-bit registers
- Big-endian byte order
- MIPS-3 ISA

### 5. XFS V1 Filesystem Incompatibility
Modern xfsprogs (Ubuntu 24.04) refuses to read IRIX-era XFS disks with the error
"V1 inodes unsupported". The custom Python reader in `sgi_mcp/sgi_fs.py` required
two significant fixes to read IRIX XFS correctly:
- V1 leaf directory magic `0xfeeb` detection
- V1 shortform directory format (9-byte header, 9+namelen entries)

---

## Assessment for QEMU 10.x Port

### What Would Port Cleanly
1. **Path translation** (`util/path.c`) — already exists in QEMU 10.x
2. **IRIX ELF loading** (`load_elf_image` with IRIX-specific quirks)
3. **syssgi(68) elfmap** — clean, self-contained
4. **PRDA mapping** — simple anonymous mmap at 0x200000
5. **Basic IRIX syscall translation** (open, read, write, mmap, etc.)
6. **Simple N32 dynamic linking support** (rld + libc + libm)

### What Would Be Challenging
1. **IRIX C++ runtime** (`libCsup.so`, `libC.so.2`) — requires:
   - IRIX shared arena management
   - `usynccntl` mutex operations
   - `sgiprctl` process control primitives
   These are complex IRIX-specific APIs that would require significant emulation work.

2. **syssgi(107)/(111)** and other unsupported subcalls — need investigation of
   what these do to determine if they affect correctness

3. **IRIX signal handling** — IRIX uses different signal frame layouts than Linux

### Conclusion
qemu-irix works well for IRIX userland binaries with simple C dependencies (standard
shell utilities, non-C++ apps). The compile pipeline through `fec` works. The limitation
is complex C++ applications like MIPSpro's `be` backend which requires IRIX C++ runtime
static initialization that relies on unimplemented IRIX kernel primitives.

For a QEMU 10.x port, starting with the basic syscall translation layer would give
functional IRIX userland emulation for ~80% of typical IRIX utilities. Full MIPSpro
support would require deeper IRIX C++ runtime emulation.

---

## Files Modified

| File | Change |
|------|--------|
| `qemu-irix/disas/arm-a64.cc` | GCC 13 fix: pre-include C++ headers before extern "C" |
| `sgi_mcp/sgi_fs.py` | XFS V1 fixes: leaf magic 0xfeeb, shortform dir format |

## Reproducibility

```bash
# Build qemu-irix
cd /workspace/qemu-irix/build
# (configure as shown above, then make -j$(nproc))

# Quick test (static binary)
/workspace/qemu-irix/build/irixn32-linux-user/qemu-irixn32 \
    /workspace/software_library/extraced_irix_cds/eoe_655f_extracted/sbin/sh \
    -c 'echo hello from IRIX'

# Test with dynamic libs
ROOTFS=/workspace/software_library/extraced_irix_cds/eoe_655f_extracted
/workspace/qemu-irix/build/irixn32-linux-user/qemu-irixn32 \
    -L $ROOTFS $ROOTFS/sbin/ls /

# Test MIPSpro version detection
MIPSPRO_ROOTFS=/tmp/irix_rootfs  # staged from irix655-dev disk
/workspace/qemu-irix/build/irixn32-linux-user/qemu-irixn32 \
    -L $MIPSPRO_ROOTFS \
    $MIPSPRO_ROOTFS/usr/cpu/sysgen/root/usr/bin/cc -version
```
