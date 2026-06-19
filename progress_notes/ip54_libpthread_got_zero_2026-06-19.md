# IP54 second-wave root cause — libpthread GOT entries reading as zero at runtime

**Date:** 2026-06-19 (late session)
**Companion to:** `ip54_4dwm_continuation_2026-06-19.md`

## The smoking gun

By extracting a fresh `/core` (xdm crash) from `vm_instances/ip54-test/disk.qcow2`
via the pyirix XFS reader, parsing the IRIX coreout format
(magic `0xbabec0bb`, section type 0xb at file offset 0x238 = GPR dump,
type 1 at offset 0x438 = special registers), and reading EPC + register
state, I pinned the crash to:

```
EPC = 0x0c22faac  (libpthread.so + 0xfaac)
```

Cross-referencing with `mips-linux-gnu-objdump -d` of the libpthread.so
extracted from the same disk:

```
0c22faa0 <_SGIPT_stk_fork_child>:
 c22faa0:  3c070002    lui    a3, 0x2
 c22faa4:  24e7cb60    addiu  a3, a3, -13472   ; a3 = 0x1cb60
 c22faa8:  03270821    addu   at, t9, a3       ; at = t9 + 0x1cb60
 c22faac:  8c268034    lw     a2, -32716(at)   ; *** CRASH ***
```

This is the MIPS PIC self-reference prologue: function entry expects
`t9 = &_SGIPT_stk_fork_child` so the lui/addiu encodes
`(GOT_base - function_address)`, and `addu at, t9, immediate`
recovers GOT_base.

The core's GPR dump says `t9 = 0`. So `at = 0 + 0x1cb60 = 0x1cb60`,
`at - 0x7fcc = 0x14b94`, which is unmapped — SIGSEGV.

## The caller

`_SGIPT_stk_fork_child` was reached via an intra-library short branch
from `_SGIPT_pt_fork_child` at libpthread + 0xbdec:

```
0c22bd44 <_SGIPT_pt_fork_child>:                ; pthread_atfork CHILD handler
 c22bd44:  addiu  sp, sp, -64                   ; standard PIC prologue
 ...
 c22bd70:  addu   gp, t9, t8                    ; gp = t9 (=_SGIPT_pt_fork_child) + 0x208BC
 c22bd74:  lw     t9, -32392(gp)                ; *** load t9 from GOT[90] ***
 ...
 c22bdec:  bgezall zero, 0x0c22faa0             ; intra-library BAL
 c22bdf0:  sw     s1, -31816(gp)                ; delay slot
```

The `lw t9, -32392(gp)` reads from `gp - 0x7E88`. With `gp = 0xc24c600`
that's address `0xc244778` — i.e. `PLTGOT (0xc244610) + 0x168` = GOT
local-entry index 90 in libpthread's GOT.

## What's at GOT[90] on disk vs. at runtime

Read directly from the extracted libpthread.so:

```
GOT[ 89] @ 0x0c244774 = 0x0c22f9d8   _SGIPT_stk_free
GOT[ 90] @ 0x0c244778 = 0x0c22faa0   _SGIPT_stk_fork_child  ← correct
GOT[ 91] @ 0x0c24477c = 0x0c22fb0c   next fn
```

At runtime, after the `lw` at 0xc22bd74 executed, t9 was **zero**.

So the on-disk GOT entry is correct, but the in-memory GOT page has
zeros at that offset. The `lw` returns 0 → t9 = 0 → SIGSEGV in callee.

## Why this happens — pvdisk read-zero, mmap edition

The IP54 emulated pvdisk has a known
[pvdisk_read_fragility_fix](pvdisk_read_fragility_fix.md) pattern:
PIO reads can return zeros where the on-disk content is non-zero,
because the kernel-VA dcache isn't invalidated before the user-side read.

The existing dcache-wbinval fix in `pvdisk.c` `pvdiskstrategy` covers
the **buffer-cache PIO read path** used by XFS metadata access. But
`mmap()` of a shared library's data segment uses a **different code
path**: VFS page-cache reads filled via `bread()` / `XFS_BMAP_read` /
`pvdisk_strategy` from a different entry point.

If the in-memory GOT page (loaded when rld mmapped libpthread's data
segment) is zero-fill instead of the on-disk content, every gp-relative
load that targets a still-zero GOT entry returns 0. The first time the
library tries to use one of those entries as a function pointer or a
data address — SIGSEGV.

The earlier SYSLOG corroborates the pattern:

```
WARNING: Bad next_unlinked field (0) in XFS inode buffer 0x886b7150
WARNING: Bad magic # 0x0 in XFS inode buffer 0x886b7150
WARNING: Filesystem "/": corrupt, unmount and run xfs_repair
```

These XFS in-memory corruptions appear *after* the trap cascade, and
the on-disk filesystem is verified clean. Same fingerprint:
zero-fill on memory pages that should hold real data.

## Why init / login work but the X session chain doesn't

- `init` (PID 1) is dynamic but the early-boot exec runs from disk
  blocks that are still in the buffer cache from PROM/SASH and pre-XFS
  setup; the page-cache vs. buffer-cache divergence hasn't started yet.
- `getty` / `login` (also dynamic) — similar: low-traffic VFS state.
- The X session chain (xdm forking many short-lived children, each
  re-mmaping libc/libpthread/libX11) hammers the VFS page cache. At
  some point a page allocation for the GOT comes from a page that
  the pvdisk read filled with zeros.

## What pieces line up

- `/sbin/test` (dynamic) crashes → xdm-errors says "X[10]: 261
  Memory fault" — line 10 of `/usr/bin/X11/X` is `if [ -x ... ]`
- `/etc/chkconfig` (dynamic) crashes → `/var/tmp/core` has comm
  "chkconfig" with the windowsystem args
- `xdm` itself crashes → `/core` has comm "xdm"
- Static `/sbin/sh` does NOT crash (no shared library exec path)
- `/sbin/init` and `/usr/bin/login` (also dynamic) work because they
  ran before the page-cache divergence began
- All dynamic binaries in the X session chain that reach
  pthread-atfork-handler invocation crash at `_SGIPT_stk_fork_child`

## Concrete kernel-side fix

The `pvdisk_read_fragility_fix` covers `pvdiskstrategy` PIO reads.
The mmap path uses the same `pvdiskstrategy` ultimately, BUT the
data being read goes into VFS page-cache pages whose **virtual**
addresses are different from the buffer-cache VA. The dcache-wbinval
must use the **right VA** for the actual page being filled.

Two places to instrument:

1. **`pvdiskstrategy` audit** — confirm the dcache-wbinval is called
   for the buffer head's `bp_un.b_addr` rather than a fixed
   buffer-cache VA. If the b_addr is the page-cache page, the
   existing fix already covers it. If not — extend.

2. **`xfs_buf_read` page-cache path** — when XFS reads a page directly
   into the page cache (for mmap), the read may not go through
   `pvdiskstrategy` at all but a faster page-cache `xfs_buf_read_uncached`
   path. Add the same dcache-wbinval there.

For diagnostic before the fix:

- Add a kernel-side BDRD-PAGE counter that fires whenever a sector
  read into a user-mapped page returns all zeros. If the counter
  goes up during xdm session startup, we have direct confirmation.
- The newly-committed BDRD-MIX detector in `sgi_bootdisk.c` catches
  the inode-buffer pattern; an analogous BDRD-LIBDATA detector
  could catch this.

## Operational path forward

1. Verify the runtime GOT[90] is actually zero by reading
   `0xc244778` via QEMU gdb stub during a fresh crash, before
   the page is recycled.
2. If confirmed, audit the kernel mmap → pvdisk read path for
   missing dcache-wbinval coverage.
3. Re-test by booting with the fix; second-wave crashes should
   disappear, clogin should NOT fall through to bare xdm, and
   Xsession.dt should reach 4Dwm.

## Why this is THE root cause

Every second-wave crash signature this session has matched the
"reads return zero where the disk has data" pattern:

- `/core` xdm: t9=0 after gp-relative GOT load → pvdisk zero
- csh `lb t0, 0(a1)` with a1=NULL: a1 loaded from somewhere that's zero
- sh jump-to-0: ra or function pointer loaded zero
- "server open failed for ," empty display string: argv parsed from
  zeros where the env-string should be

It's one bug, one path, multiple symptoms. The MO_UNALN fix handled
the case where userland reads unaligned data and the kernel can't
emulate the trap. The remaining wave is all about **zero where
data should be**, and it's all the pvdisk read-fragility — just
in an mmap code path the existing fix doesn't cover.

## Files / artifacts

- `/tmp/qrt/binaries/HIT_libpthread.so` — extracted libpthread (offline read)
- `/tmp/qrt/core_core` — xdm /core file with the crash data
- `/tmp/qrt/core_var_tmp_core` — chkconfig /var/tmp/core (similar pattern)
- `/tmp/qrt/binaries/lib32_rld` — IRIX runtime dynamic linker
- This note.

---

## ⚠️ CORRECTION (2026-06-19, later same day) — root cause DISPROVEN

The cache-alias / pvdisk-read-zero explanation above is **wrong**. Two
findings overturned it:

1. **QEMU TCG models no CPU caches.** `dki_dcache_wbinval` is a
   functional no-op in emulation; a guest dcache flush cannot change
   what a load returns. Emulated memory is coherent, so the
   VIPT-color-alias mechanism described above cannot exist in QEMU.

2. **The BDRD-MIX detector was a false positive.** Reading the flagged
   sector (abs LBA 6436658) straight from the disk image returns
   `494e444f` in slot 0 and zeros in slot 256 — byte-for-byte what
   QEMU's sgi_bootdisk returned. pvdisk faithfully reproduces on-disk
   content; it does not zero-fill. The "IN" magic heuristic simply
   matched the legitimate ASCII bytes `IN` ("INDO…").

The `irix-ip54` `CACH_OTHER_COLORS` commit (9d2c50f) was reverted in
**ef2c04d** — it was theoretically void and also panicked the kernel
at boot (icmn_err spinner at 0x88203c28).

The crash *signature* (xdm core EPC = libpthread+0xfaac, t9=0 from a
GOT load) is still real and still blocks 4Dwm. The actual cause is
OPEN — see the memory note `ip54-libpthread-got-zero-root-cause` for
the remaining candidates (rld relocation, offline-core mis-parse,
kernel VM/TLB mapping, sproc/shared-arena). Next step must be LIVE
gdb against the running guest, not another offline-core / pvdisk
theory.
