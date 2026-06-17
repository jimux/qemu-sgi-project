# CP0_Cause cross-thread race — ROOT CAUSE of the IP54 fragility (FIXED)

Date: 2026-06-10

## Symptom catalog (all one bug)

For months, sgi-ip54 sessions degraded under load: random userland
SIGSEGVs (xset, configmail, lp, cron, mediad — "all N32 binaries
crash", March notes), rc0 K-script Memory-fault cascades at shutdown,
`cc` intermittently exiting 32, `uname`/`id` misbehaving, kernel
"stack underflow/overflow" / "KERNEL FAULT" panics at init 0, and
Xsession.dt traps escalating to kernel faults. The same disk ran
flawlessly on the Indy machine — the decisive clue.

## The race

Two threads RMW `env->CP0_Cause` concurrently:

- **iothread (BQL held)**: `cpu_mips_irq_request` (every qemu_irq
  line: HEART IP3/IP4, pvrex3 VRINT 60Hz, COMPARE/IP7) — and, worst,
  the IP54 pvclock callbacks setting/clearing Cause SW2 **200×/sec**.
- **vCPU thread (NO BQL)**: `helper_mtc0_cause`/`helper_mtc0_compare`
  → `cpu_mips_store_cause/compare`, which read the WHOLE register and
  write the WHOLE register back (only masked bits change, but the
  write-back is full-width).

A guest `mtc0 cause` between an iothread read-modify-write loses one
side's update: a just-asserted hardware IP bit is erased (lost
interrupt) or a just-cleared one resurrected (spurious interrupt mid
exception-delivery → corrupted dispatch → processes resuming at EPC 0,
interrupt-stack overflows).

Why IP54 and not Indy: IRIX's softint machinery writes Cause SW bits
on **every clock tick** (pokesoftclk), and the IP54 pvclock wrote SW2
from the host 200×/sec — guest and host writes were synchronized by
construction. Indy has no host-side SW-bit writer and far fewer Cause
RMWs/sec, so its exposure was statistically negligible (the latent
race exists in upstream QEMU for any MIPS guest that hammers Cause).

## The fix (two parts, both required)

1. `hw/mips/sgi_ip54pv.c`: pvclock raise/lower callbacks no longer
   touch `env` from the iothread — they `async_run_on_cpu()` work
   items that run on the vCPU thread at a TB boundary.
2. `target/mips/tcg/system/cp0_helper.c`: `helper_mtc0_cause`,
   `helper_mttc0_cause`, `helper_mtc0_compare` now take the BQL around
   the store, serializing guest RMWs against all iothread writers.
   (Part 1 alone was insufficient — confirmed by a still-panicking
   run between the two fixes.)

## Verification

Login-test run (boot → xdm → xlogin login → Xsession spawn ×3 →
init 0): **0 PANICs, 0 Memory faults**. Pre-fix the identical scenario
produced `ALERT: Xsession.dt trap → PANIC: KERNEL FAULT` (twice) and
rc0 cascades + shutdown panics in essentially every X-touching session.

Possibly upstreamable: the helper-side BQL is generic target/mips
correctness, not IP54-specific.

## What remains (separate bug, now isolated & reproducible)

`Xsession.dt` deterministically traps at `epc 0x0 ra 0x0` (signal 11
held) — three consecutive spawns, same trap, kernel now survives it
and xdm retries. Same family as rc0 scripts crashing at fixed line
numbers and `xterm -e` failing under the desktop: a daemon-spawned
shell context (no controlling tty?) jumps through NULL, while
interactive serial shells never do. Reproducer for next session:
serial shell → `DISPLAY=:0 ; sh /var/X11/xdm/Xsession.dt` (or `sh -x`
to find the line), then bisect the script construct. Suspect a stubbed
IP54 kernel path (ip54_stubs.c) returning garbage to a libc/job
control call used only in that context.
