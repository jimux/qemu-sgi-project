# IP54 second-wave crash — REAL root cause: shared-lib data-segment page never populated

**Date:** 2026-06-19 (evening)
**Supersedes the cache-alias/pvdisk theory in** `ip54_libpthread_got_zero_2026-06-19.md`
(see the CORRECTION section there) **and** `ip54_4dwm_continuation_2026-06-19.md`.

## The decisive evidence

The xdm `/core` (saved at `/tmp/qrt/core_core`, parsed with the correct
`core.out.h` layout this time) crashed at:

```
name = xdm    args = /usr/bin/X11/xdm    sigcause = 11 (SIGSEGV)
EPC      = 0x0c22faac   (libpthread.so + 0xfaac, in _SGIPT_stk_fork_child)
CAUSE    = 0x10000008   (ExcCode 2 = TLBL — TLB load miss / page not mapped)
BADVADDR = 0x00014b94
t9 = 0   gp = 0xc24c600   ra = 0xc22bdf4 (_SGIPT_pt_fork_child+0xb0)
```

`t9=0` because the `lw t9, -32392(gp)` at `_SGIPT_pt_fork_child+0x30`
(libpthread+0xbd74) read libpthread's local **GOT[90]** at
`gp-0x7e88 = 0xc244778` and got 0. The PIC self-reference prologue in
the callee then computes `at = t9 + 0x1cb60 = 0x1cb60`, loads from
`0x14b94` → unmapped → SIGSEGV.

## The core's own memory image settles it

The IRIX coreout carries the process's dumped pages (vmaps). xdm's
libpthread **data segment** is vmap[4]: `va=0xc244000 len=0x1000
type=8 (VMAPFILE) flags=1 (VDUMPED)`. Reading that dumped page:

```
xdm in-mem libpthread data page (0xc244000):    0 / 4096 bytes nonzero   (ALL ZERO)
on-disk libpthread.so data page (file +0x14000): 2021 / 4096 bytes nonzero
```

The on-disk page holds the GOT template (`0fb60150 0c220000 0c230000
0c240000 …` and the local-function pointers `0c22f9d8 0c22faa0 …`).
**In xdm's memory that entire page is zero** — including the GOT header
words that come verbatim from the file *before* rld applies any
relocation. So this is not an rld-relocation problem: the file's data
segment content was **never brought into the page at all**.

## What this is — and isn't

- **NOT cache aliasing.** QEMU's TCG models no CPU caches; a guest
  `dki_dcache_wbinval` is a no-op. (The earlier `CACH_OTHER_COLORS`
  pvdisk patch was reverted — `irix-ip54` ef2c04d.)
- **NOT pvdisk read corruption.** pvdisk returns on-disk content
  faithfully (verified: abs LBA 6436658 read back byte-identical; the
  BDRD-MIX "evidence" was a false positive on legit ASCII "IN…").
- **It IS a kernel demand-paging populate bug.** The file-backed
  private mapping of a shared library's data segment, on first fault,
  is getting a zero-filled page that is never populated from the file.

## Why it's intermittent (and why the live capture "disagreed")

Earlier I broke live at libpthread+0xbd74 in a fork-child and saw
GOT[90] = 0xc22faa0 (correct). That process had the data page **already
resident** — faulted in by an earlier process and inherited/shared
correctly. xdm's crashing fork-child instead hit a **fresh** mapping of
that page that the fault path left zero-filled.

This matches the whole second-wave pattern:
- static `/sbin/sh` never crashes — it maps no shared-library data segment.
- dynamic X-session clients (chkconfig, test, xrdb, xkbcomp, Xt/Motif,
  xdm's own fork-children) crash — each maps several shlib data
  segments, and some of those pages come up zero.
- `date`/`ls` from a serial shell worked because their shlib data pages
  happened to be already resident (libc faulted early in boot).

## NARROWED HARD: only libpthread's data page, only in the fork-child

Scanning ALL vmaps in xdm's core for nonzero content:

```
[ 4] va=0x0c244000 VMAPFILE  nonzero=0/4096      <-- libpthread DATA: ALL ZERO
[ 6] va=0x0f5bb000 VMAPFILE  nonzero=9875/16384  libXaw  data  ✓ populated
[ 8] va=0x0f5f5000 VMAPFILE  nonzero=2097/4096   libXmu  data  ✓
[10] va=0x0f694000 VMAPFILE  nonzero=10796/16384 libXt   data  ✓
[14] va=0x0f7e9000 VMAPFILE  nonzero=9895/16384  libX11  data  ✓
... every other shlib data segment is populated; ONLY libpthread's is zero.
```

So it is **not** a general COW/demand-paging failure. It is specific to
**libpthread's data page (0xc244000), first-faulted inside a forked child.**

Mechanism that fits all evidence:
- xdm is a daemon that never calls pthread routines itself, so it **never
  faults libpthread's data page** — that page stays mapped-but-unfaulted
  in the parent (demand).
- Every *other* shlib data page WAS faulted by xdm before forking, so the
  child inherits them correctly (COW of a resident page).
- xdm `fork()`s to spawn a session; in the **child**, libpthread's
  registered **atfork CHILD handler** `_SGIPT_pt_fork_child` runs and is
  the *first* code to touch 0xc244000.
- That **first fault of the inherited-but-never-faulted file-backed page,
  in the forked child**, returns a zero page instead of reading the file.

The libpthread data-seg disk sectors WERE read once (correct bytes) — by
some other process earlier in boot — so the page cache holds the right
data, but the child's fault does not use it and does not re-read.

⇒ Root cause is a **fork-inheritance bug for not-yet-resident file-backed
private mappings**: the child's first fault of such a page fails to
populate it from the vnode/page-cache. Stock IRIX VM code, but triggered
by the IP54 paging path.

## (earlier framing) the read HAPPENS and is correct — it's COW

Traced QEMU's `sgi_bootdisk` read log against libpthread's on-disk
data-segment extent. libpthread.so is XFS inode 12593266, single extent
(fileblk 0 → fsb 788522, 39 blocks). Data segment (file off 0x14000,
vaddr 0xc244000) = **absolute disk sectors 6374896–6374903**.

The log shows those 8 sectors were each read **exactly once** since
boot, and pvdisk returned the **correct on-disk bytes**:

```
BDRD sec=6374896 d=70746872   (on-disk first4 = 70746872 "pthr", 424/512 nonzero) ✓
BDRD sec=6374900 d=0fb5458c   (on-disk 0fb5458c, 354/512 nonzero)                 ✓
```

So: the disk read happens, returns faithful data, and populates the
page cache. The **first** process to fault the page gets correct data.
xdm faulted **later**, which triggered **no new read** (only one read
per sector total) — yet xdm's mapping of 0xc244000 is all zero.

⇒ The bug is **not** the read and **not** pvdisk. It is **copy-on-write
of the file-backed `MAP_PRIVATE` shared-library data page**: a process
that faults *after* the page is already cached gets a zero COW page
instead of a copy of the cached file content. First-faulter correct,
later-faulters zero — exactly the observed per-process intermittency.

In QEMU (coherent memory, no caches) a COW bcopy from the correct
page-cache page MUST yield correct bytes, so the fault path is either
(a) COWing from the wrong/zero source page, or (b) installing a fresh
anonymous zero page for the later faulter instead of COWing from the
vnode page-cache page at all.

## Where the bug lives — next investigation

Now that the read is confirmed correct, the bug is in the **COW
fault path** for an already-cached file-backed page. Stock IRIX:
`vfault()` (kernel 0x881f6e40) → for a write to a MAP_PRIVATE page that
is resident-and-clean in the vnode page cache → allocate a private
page, **copy the cached page into it** (`pagecopy`/`bcopy` via
`cow_copy`/`anon` logic), remap COW. The later faulter ends up with a
zero page, so either the copy source is wrong/zero or no copy is done.

Concrete next diagnostics (deterministic, no GUI racing):
- Boot `console=d`, get a serial shell. Run a tiny dynamic program
  TWICE back-to-back; the 2nd run is the "later faulter". Under gdb,
  read its libpthread data page (0xc244000) — expect zero on the 2nd
  run, correct on the 1st. That reproduces the COW bug in isolation,
  far easier than racing xdm.
- Single-step `vfault` on the 2nd run's write-fault to 0xc244000 and
  watch whether it calls the page-copy path and what source page it
  reads. Source page phys addr vs the page-cache page phys addr tells
  us if it's case (a) wrong source or (b) no copy.
- Inspect `pfdat`/`pvdata` for the cached page: if the IP54 port mis-set
  the page's `pf_flags`/`pf_pageno`/vnode linkage, COW would pick a
  wrong source. The pvdisk PIO read path populates pages without the
  usual DMA completion; check that it marks the page-cache page
  P_DONE/valid with the right vnode/offset so COW recognizes it.

The last point is the most promising lead: **pvdiskstrategy is a PIO
driver that fills pages via CPU bcopy**, unlike a DMA driver. If the
page-cache page it fills isn't being marked valid/clean with correct
vnode metadata, the COW path for later faulters may not recognize it as
a valid copy source and instead hands out a zero page. This ties the
COW symptom back to the PIO-vs-DMA difference of the pvdisk port — a
plausible, IP54-specific, fixable cause.

## Status

Root cause localized to **shared-library data-segment demand-paging not
populating the page from the file** (kernel VM, not cache/pvdisk). The
crash signature (libpthread GOT[90]=0 → t9=0 → SIGSEGV) is a *symptom*
of the zero data page. Fixing the demand-paging populate path should
clear the entire second-wave cascade and let clogin/Xsession/4Dwm
proceed.

## Artifacts
- `/tmp/qrt/core_core` — the xdm core (definitive zero-page evidence)
- `/tmp/qrt/binaries/HIT_libpthread.so` — on-disk libpthread for comparison
- core.out.h: `software_library/irix-655-source/m/root/usr/include/core.out.h`
- eframe layout: `software_library/.../sys/reg.h` (EF_EPC = idx 36, 8-byte regs)
