# IP54 MO_UNALN breakthrough — clogin dialog now accepts login

**Date:** 2026-06-18 (late session)

## TL;DR — Major progress

Changed QEMU `target/mips/tcg/translate.c` to default unaligned MIPS
loads to silently succeed (MO_UNALN) instead of raising AdEL
(MO_ALIGN). On `sgi-ip54` with this change:

- **clogin's "Welcome to IRIS — IRIX 6.5" Motif dialog now renders**
  (was reverting to bare "X Window System" before)
- **Login field accepts "root" + Enter** without crashing
- **Password prompt appears** and accepts empty password
- **kernel ALERT trap rate dropped to zero** (klogpp not invoked)
- **No more Xsession.dt SIGSEGV cascade** in SYSLOG

What still happens:

- xkbcomp still crashes (`Xsgi0: Couldn't load XKB keymap, falling back
  to pre-XKB keymap` x2)
- xrdb crashes (new core file at `/core`)
- Session ultimately reverts to plain xdm dialog instead of reaching
  the 4Dwm desktop

So the MO_UNALN fix is the right first step but a SECOND class of
crashes remains. Likely a separate root cause (heap corruption from a
different unaligned write or signal-frame issue).

## The change

`qemu-sgi-repo/target/mips/tcg/translate.c` line 15121:

Before:

```c
ctx->default_tcg_memop_mask = (!(ctx->insn_flags & ISA_NANOMIPS32) &&
                              (ctx->insn_flags & (ISA_MIPS_R6 |
                              INSN_LOONGSON3A))) ? MO_UNALN : MO_ALIGN;
```

After:

```c
ctx->default_tcg_memop_mask = (ctx->insn_flags & ISA_NANOMIPS32) ? MO_ALIGN :
                              MO_UNALN;
```

Effect: for MIPS-III/IV/64 (including our `sgi-ip54` machine, which
uses an R5000-class core via `cpu R5000`), default lw/sw/etc no longer
require natural alignment. The TCG slow path silently does the
unaligned access via byte ops instead of trapping. Explicit unaligned
opcodes (lwl, lwr, swl, swr) and atomics (ll, sc, lld, scd) are
unaffected by this default — they use their own MemOp flags.

## Why this fixes most crashes

Captured live before the fix: `iaf/scheme` SIGBUS at `epc 0xfa3933c`
(libc `_malloc+0x29c`), faulting on `lw at, 8(v1)` with `v1 =
0x100225e1` (odd). The compiler was emitting

```
fa39334: lw    v1, 0(s1)         # v1 = *(s1)
fa39338: addu  v1, v1, s1        # v1 += s1
fa3933c: lw    at, 8(v1)         # crash — v1 isn't 4-byte aligned
```

`*(s1)` returned a free-list pointer with the in-use bit (0x1) ORed
in. The library normally `and`s the bit out, but in this branch the
mask was missing or out of order. On real MIPS-III this would trigger
the kernel's AdEL handler, which IRIX implements as an in-kernel
emulator that lwl/lwr-merges the access and resumes. The IP54
emulated kernel skips that path and just sends SIGBUS/SIGSEGV.

With MO_UNALN, QEMU emulates the unaligned access transparently —
exactly matching what the kernel would have produced. Userspace
survives.

## Diagnostic chain

1. SIGBUS hit live in SYSLOG with EPC inside libc `_malloc`.
2. `iaf/scheme`, `csh`, `xkbcomp`, `Xlogin/sh` all crashed similarly
   during X session startup.
3. Disasm pinned the faulting instruction as a `lw` to an unaligned
   address — a userland code-quality issue that real MIPS-III kernels
   handle transparently.
4. QEMU's MIPS-III translate path defaulted to MO_ALIGN, so QEMU
   raised AdEL → IP54 kernel delivers SIGBUS → klogpp fails to
   process the warning → cascade.
5. Switching MO_UNALN → silent handling → no AdEL trap → no SIGBUS →
   userspace continues.

## What's not yet fixed

Even with MO_UNALN, *xrdb* and the *sh* spawned to run xkbcomp still
crash with cores left under `/core` and `/var/tmp/core`. These were
identified as:

| Path                     | Binary    |
|--------------------------|-----------|
| `/core`                  | xrdb      |
| `/var/tmp/core`          | sh -c xkbcomp …|

The crashes happen AFTER Xsession starts. Likely candidates:

- **A different unaligned operation** that doesn't go through the
  default memop mask — e.g. `lwl`/`lwr` pair where the user code is
  performing its own unaligned access on a struct field
- **Signal-frame layout mismatch** — signal delivery in QEMU may not
  match what the IRIX kernel expects
- **A subtle TLS / shared-arena issue** on R5000 process startup
  that's separate from alignment

## Next investigation steps

1. Disasm xrdb at the faulting PC — extract registers from the core
   file. Check if it's another unaligned access or something else
   entirely.
2. Inspect the BSD-derived `xrdb.c` source if available — it's small.
3. Look at xkbcomp's first dynamic library load (libxkbfile) — the
   crash may be in rld's relocation walker.
4. If the next crash is also a malloc-style corruption, look for the
   actual user-VA cache alias issue (the dki_dcache_wbinval fix only
   covered kernel-VA in pvdiskstrategy).

## Boot recipe with the new build

```bash
cd /home/jimmy/qemu-sgi/qemu-sgi-repo/build-linux
ninja qemu-system-mips64

cd /home/jimmy/qemu-sgi
cp vm_instances/ip54-test/disk.qcow2.indigo_magic_dialog \
   vm_instances/ip54-test/disk.qcow2
python3 -c "
from sgi_mcp.nvram_utils import nvram_write_var
nvram_write_var('vm_instances/ip54-test/nvram.bin', 'console', 'd')
"

env IP54_CAUSE_IP5_COUNT_PA=0x0829fee0 \
  qemu-sgi-repo/build-linux/qemu-system-mips64 \
    -M sgi-ip54 -bios PROM_library/bins/cpu/ip54/ip54.bin -m 256M \
    -L qemu-sgi-repo/build-linux/pc-bios -display none \
    -chardev socket,id=ser0,path=/tmp/q/serial.sock,server=on,wait=off \
    -serial chardev:ser0 \
    -monitor unix:/tmp/q/monitor.sock,server,nowait \
    -drive if=mtd,file=vm_instances/ip54-test/disk.qcow2,format=qcow2 \
    -nic user,tftp=ip54_tftp_staging,hostfwd=tcp::2324-10.0.2.15:23 \
    -audiodev pa,id=aud0 -global sgi-pvaudio.audiodev=aud0 \
    -gdb tcp::1234 &

# Wait ~2-3 min then drive via QEMU monitor sendkey: root + Enter +
# (Enter for empty password). Welcome to IRIS dialog appears, fields
# accept input cleanly.
```

The MO_UNALN diff is also the right change for any user trying to
boot any IRIX 6.5 image on MIPS-III sgi-ip54 — it should be the
default going forward.
