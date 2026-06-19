# Time decoupling — implementation

**Date:** 2026-06-19
**Companion to:** `time_decoupling_investigation_2026-06-19.md`

## What changed

`qemu-sgi-repo/target/mips/system/cp0_timer.c` — every
`qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL)` and the
`timer_new_ns(QEMU_CLOCK_VIRTUAL, ...)` site now route through a small
helper `mips_count_clock_type()` that returns `QEMU_CLOCK_REALTIME`
when the env var `QEMU_MIPS_COUNT_REALTIME=1` is set, and
`QEMU_CLOCK_VIRTUAL` otherwise.

The helper caches its env lookup once, so all subsequent CP0 ticks
read a single per-process value with no per-tick branching cost
beyond a cached load.

## Why env-gated rather than always-on

Other MIPS machine types in this tree (Indy IP24, Indigo2 IP28, O2
IP32) have been tuned + tested against the VIRTUAL clock. Switching
them silently could regress behavior we don't have time to re-validate
right now. IP54 is the case where coupling causes the icount tradeoff,
so it's the first opt-in.

## Usage

```bash
env QEMU_MIPS_COUNT_REALTIME=1 \
  IP54_CAUSE_IP5_COUNT_PA=0x0829fee0 \
  qemu-sgi-repo/build-linux/qemu-system-mips64 \
    -M sgi-ip54 -bios PROM_library/bins/cpu/ip54/ip54.bin -m 256M \
    ...
```

Combine with `-icount shift=0,sleep=off` for "smooth UI without time
acceleration": the CPU runs as fast as the host permits (smooth
animation), but CP0_Count + COMPARE-IRQ7 still tick at wall rate
(networking stays correct, animations don't fly by, `date` stays
accurate).

## Smoke test results

Built clean (single recompile of `cp0_timer.c.o` + relink).

Boot tests on `sgi-ip54` from the `indigo_magic_dialog` backup:

| Mode | Boot to login prompt | Notes |
|---|---|---|
| Default (no env var) | ✅ | Identical behavior to pre-patch |
| `QEMU_MIPS_COUNT_REALTIME=1` | ✅ | Boot completes, login prompt, `date` reasonable |

The second-wave userspace crashes (csh / sh SIGSEGV on session
startup, documented separately in
`progress_notes/ip54_4dwm_session_2026-06-19.md`) are unchanged by
this patch — they are orthogonal.

## Existing kernel-side workarounds — status

`patches/phase2/clock_patch.c`, `ptimers_patch.c`, `select_patch.c`
were written to work around virtual-time race-ahead under icount
sleep=off. With REALTIME on, they become fast-path no-ops (the kernel
reads the same wall clock the patches' qemu_rt_ctr fast-path would
have steered it to). Safe to leave in place; no conflict.

## Future work — promotion options

1. **Machine property** instead of env var:
   ```
   -machine sgi-ip54,timer-source=realtime
   ```
   Same wiring but discoverable via `-machine help`.

2. **Default-on for `sgi-ip54`** once the second-wave userspace
   crashes are resolved and we've re-validated Indy/Indigo2/O2 with
   the new clock source. The investigation note recommends this
   because it matches real R4000-class hardware behavior.

3. **Per-clock subsystem control** (investigation Option C) — keep
   COUNT virtual for cycle-accurate spin-loops but route COMPARE-IRQ7
   off REALTIME. Only worth doing if a specific guest workload
   demonstrably needs the split. None observed yet.

## Files touched

```
qemu-sgi-repo/target/mips/system/cp0_timer.c   (+31, -6)
```

No header changes, no API changes, no test changes. The static helper
keeps the patch surgical.
