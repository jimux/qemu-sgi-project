# IP54 Keyboard/Mouse Input — 8042 + stock pckm via poll-dispatch

Date: 2026-06-09 (Milestone 1 of the boot-to-desktop effort)

## Problem

The IP54 desktop (Xsgi/4Dwm, Phase 13) rendered but was non-interactive:
IP54 has no HPC3/IOC2, so the stock `pckm` driver's 8042 accesses at
PA 0x1FBD9843/0x1FBD9847 landed in a `TYPE_UNIMPLEMENTED_DEVICE` region
(reads 0), and `newport_sendkey`/`newport_mouse` injected into a PS/2
queue wired to nothing.

## Design — reuse everything that already works

Three verified facts made the cheap path possible:

1. **The stock IP22 `pckm.o` is already linked into the IP54 kernel**
   (`IP54.sm: INCLUDE: pckm`). Its `early_pckminit()` probes the 8042 at
   *compile-time-constant* addresses (no hwgraph parent needed), and
   `pckminit()` creates `/dev/input/keyboard` + `/dev/input/mouse` — these
   nodes always existed on IP54; data just never arrived.
2. **The 8042 emulation already exists** in `sgi_hpc3.c` and is
   byte-for-byte proven against this exact pckm.o on the Indy machine.
3. **pckm pushes raw PS/2 set-3 scancodes upstream as a STREAMS driver**;
   the `keyboard`/`mouse` idev modules (also in IP54.sm) convert to shmiq
   events for Xsgi. No custom event protocol needed anywhere.

### QEMU side: `hw/misc/sgi_ioc2_kbd.c` (new device `sgi-ioc2-kbd`)

Standalone sysbus transplant of the sgi_hpc3.c 8042 state machine +
embedded `sgi-ps2-kbd` (realtime typematic subtype) + PS/2 mouse.
8-byte MMIO region mapped in `sgi_ip54pv.c`:

    sysbus_mmio_map_overlap(..., 0x1FBD9840, 1);   /* beats unimp region */

Status register semantics preserved exactly: pckm keys on
`status & (SR_OBF|SR_MSFULL)` = 0x21. IRQ output exists but is left
unconnected (see below). Kconfig: `SGI_IOC2_KBD` selects `PS2` +
`SGI_HPC3`; selected by `SGI_IP54PV`.

### Kernel side: poll-dispatch shim in `pvuart_cn.c` (~30 lines)

IP54's custom `mlreset()` never runs `lclvec_init`, so the INT3/LIO
hardware-interrupt path is dead — and would be UNSAFE anyway
(`pckm_intr` takes `pckm_mutex`; not hard-interrupt-safe on this kernel,
same failure class as calling `ether_input()` from pvnet_intr).

Instead the existing 50Hz `du_poll()` callout drains the controller:

    if (pckm_kbdexists() && lcl2vec_tbl[5].isr != lcl2vec_tbl[6].isr)
        while ((*(volatile u_char *)0xBFBD9847 & 0x21) && --budget)
            lcl2vec_tbl[5].isr(lcl2vec_tbl[5].arg, 0);   /* == pckm_intr */

Callout context is mutex-safe (pckm's own `pckm_reinit` dtimeout proves
it). Budget 32 bytes / 20ms = 1.6 KB/s ≫ keyboard + 50Hz mouse demand.

## Key discovery: calling a static ISR via the vector table

`pckm_intr` is **static** — can't link to it. But
`setlclvector(VECTOR_KBDMS=20, pckm_intr, 0)` installs it into the
global `lcl2vec_tbl`. Disassembling `setlclvector` in the shipped
kernel.o (capstone, unix.new @ 0x88007970) pinned the real layout:

- Entry stride: **32 bytes** (`(vec & 7) << 5`); fields: `isr` @ +0,
  `arg` @ +4, `bit` @ +8, `thd_flags` @ +0xc, rest thd_int_t padding.
  (Do NOT trust `sizeof(lclvec_t)` from headers — thd_int_t size varies
  with ITHREAD_LATENCY compile flags.)
- lcl2 vectors index from **entry [1]**: vec 20 → `lcl2vec_tbl[5]`
  (= symbol + 0xA0). Confirmed against the static initializer: every
  entry [1..8] inits to `lcl_stray` (0x88008a20) with `arg = vec+1`;
  entry [5] has arg=0x15=21 ✓.
- **Registration gate**: entry [6] (vector 21) is never registered on
  IP54, so `[5].isr != [6].isr` ⇔ pckm has installed its handler.
  No hardcoded addresses, robust across kernel relinks.

## Operational lessons (hard-won today)

- **NEVER SIGKILL a live IRIX guest.** A session script that exited
  without `init 0` left QEMU orphaned; the next `qemu_session_start`
  SIGKILLed it mid-write and corrupted irix655-dev's root XFS
  (dinode 6291585 btree extents → boot PANIC). All session scripts now
  do `sync; init 0` + `qemu_session_stop` in a `finally:`.
- **ip54-test was a COW overlay over irix655-dev's disk** — booting the
  backing image read-write invalidates the overlay. Both ip54-test
  qcow2s (disk + golden) are now FLATTENED standalone images
  (overlay .bak copies kept). The old "boot irix655-dev with ip54 disk
  as unit 2" workflow was a standing corruption hazard.
- **pyirix.xfs READ mis-assembles multi-extent files** (cat of 13964-byte
  pvfb.o returns 14983 scrambled bytes). Until fixed, don't extract
  multi-extent files offline. Recompile from `ip54_tftp_staging/`
  sources instead — that's the authoritative build input anyway.
- **Compile directly on the ip54-test disk booted on Indy**: it's a fork
  of irix655-dev, so MIPSpro 7.2.1 + the IP54 build tree are onboard,
  and /unix.new lands directly on the target disk.
- ip54-test's `/var/sysgen/boot` (→ /usr/cpu/sysgen/IP22boot) had STALE
  `pvfb.o` (5720B pre-GfxRegisterBoard) and `pvaudio.o` — the fork
  predates Phase 10b/12. Any lboot on this disk must first refresh
  pvfb.o/pvaudio.o (recompiled from staging) or graphics regress.

## Verification — PASSED (2026-06-09)

1. **Raw scancodes** (run_m1_verify.py): `dd if=/dev/input/keyboard
   bs=1 count=6 | od -b` + `newport_sendkey text="ab"` returned
   `034 360 034 062 360 062` — set-3 make/break for 'a','b'. Mouse
   3-byte PS/2 packet likewise captured from /dev/input/mouse.
2. **Keyboard → X client** (run_m1_baretest.py): bare Xsgi (no WM,
   PointerRoot focus), xterm at +0+0 running `cat > /tmp/typed.txt`,
   pointer swept into the window, `newport_sendkey text="hello kbd\n"`
   → file contains exactly `hello kbd`; screendump
   `framebuffers/m1_bare_xterm.png` shows the text + cursor.
3. **Mouse → X**: clicking the desktop xterm flipped its 4Dwm title
   bar color (focus change) — visible in
   `framebuffers/m1_desktop_{baseline,interact}.png` diff.

### Caveats found during verification

- **Full-desktop typing test failed but NOT due to input**: with
  4Dwm + toolchest running, a probe `xterm -e touch /file` never ran
  its child, while the same command works under bare X. Suspect 4Dwm
  window-placement/map interaction, not pty (pty verified fine) and
  not keyboard (bare-X PASS). Revisit when xdm session testing starts.
- `xset q` segfaults (Memory fault) — same known N32-binary crash
  class as configmail/lp/cron; xdpyinfo, xterm, 4Dwm, toolchest work.
- **Recurring shutdown panic**: `init 0` after an X session panics
  ("Fatal error on root filesystem" / "stack underflow/overflow")
  AFTER sync — disk survives (xfs_check clean every time, 5/5 PASS).
  Probably X teardown vs pvfb/shmiq during kill-all. Worth its own
  investigation before calling boot-cycle stability done.
