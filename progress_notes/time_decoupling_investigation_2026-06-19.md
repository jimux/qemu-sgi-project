# Decoupling IRIX system time from QEMU CPU clock — investigation

## The current coupling

In QEMU's MIPS target, the CP0 COUNT/COMPARE timer pair is the
hardware tick source for IRIX. The implementation in
`qemu-sgi-repo/target/mips/system/cp0_timer.c` ties everything to
`QEMU_CLOCK_VIRTUAL`:

```c
static uint32_t cpu_mips_get_count_val(CPUMIPSState *env)
{
    int64_t now_ns;
    now_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    return env->CP0_Count +
            (uint32_t)clock_ns_to_ticks(env->count_clock, now_ns);
}

static void cpu_mips_timer_update(CPUMIPSState *env)
{
    uint64_t now_ns, next_ns;
    uint32_t wait;
    now_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    wait = env->CP0_Compare - cpu_mips_get_count_val(env);
    if (!wait) wait = UINT32_MAX;
    next_ns = now_ns + clock_ticks_to_ns(env->count_clock, wait);
    timer_mod(env->timer, next_ns);
}
```

`env->timer` itself is `timer_new_ns(QEMU_CLOCK_VIRTUAL, ...)`.

The COUNT register and the COMPARE-fires-IRQ7 deadline are both
measured in **virtual time**. The frequency conversion goes
`cpu->clock` (default `CPU_FREQ_HZ_DEFAULT`) → `cpu->count_div`
(divide by `CCRes` from the CPU model) → `env->count_clock`. For a
175 MHz R4400 with CCRes=2, CP0 ticks at ~87.5 MHz of *virtual* time.

### What "virtual time" actually means here

QEMU has three clocks (`include/qemu/timer.h`):

- **`QEMU_CLOCK_REALTIME`** — host wall-clock monotonic (CLOCK_MONOTONIC)
- **`QEMU_CLOCK_VIRTUAL`** — driven by VM execution; with `-icount`, it
  is tied to instruction count and skews from wall time. **Without**
  `-icount`, VIRTUAL == REALTIME, so the timer fires roughly at wall
  rate IF the CPU can keep up. If the CPU is faster than the modeled
  frequency, it idles via WAIT and VIRTUAL still advances roughly at
  wall rate (sleep=on). If `-icount sleep=off`, VIRTUAL races during
  idle.
- **`QEMU_CLOCK_HOST`** — host wall-clock (CLOCK_REALTIME), jumpy when
  the host clock is set.

### Why the user sees "smooth UI ↔ time accelerates" coupling

The trade-off boils down to:

- **No `-icount`**, default `sleep=on`: VIRTUAL tracks wall-time
  monotonically. UI smoothness depends on the CPU keeping up — if a
  scene needs more host CPU than the modeled 87.5 MHz worth of work
  per ms, frames stutter. Networking RTTs are wall-real.
- **`-icount shift=0,sleep=off`**: every emulated CPU cycle advances
  VIRTUAL by 1 tick worth of ns, but during idle, VIRTUAL races
  forward (no sleep). UI is smooth (CPU goes full tilt), but TCP
  retransmit/animations think a wall second is a few ms (or vice
  versa), so networking dies and animations zoom.
- **`-icount shift=auto`**: QEMU rebases shift to track wall time;
  closer but still couples UI speed to virtual time.

The fundamental cause: **CP0_Count and CP0_Compare-IRQ7 use the same
clock that paces sleeps and observable time.** The kernel's `lbolt`,
networking timeouts, scheduler quantum, and animation frame advance
all run off the same timer interrupt.

## The decoupling approach

### Option A — Direct: switch CP0 timer to QEMU_CLOCK_REALTIME

The minimum-change route. In `cp0_timer.c`, swap every
`qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL)` to
`qemu_clock_get_ns(QEMU_CLOCK_REALTIME)`, and create `env->timer`
with `timer_new_ns(QEMU_CLOCK_REALTIME, ...)`.

Effects:

- CP0_Count advances at real-wall rate (87.5 MHz worth of ticks per
  wall second, regardless of how many host CPU cycles were spent
  executing the guest).
- CP0_Compare-IRQ7 fires at real-wall intervals (`lbolt++` happens
  100×/wall-second).
- IRIX's scheduler quantum, networking timeouts, `gettimeofday()` (if
  derived from `lbolt`/`Count`), animations — all driven by wall time.
- If the host can't keep up with the CPU's modeled rate, the guest
  will *appear to run more slowly* (instructions per wall second are
  bounded by host speed) but wall time keeps moving at the right rate.
- If the host is faster than the modeled CPU, the CPU idles
  (`mips_tr_translate_insn` handles WAIT specifically) and we don't
  burn host cycles needlessly.

The catch: existing kernel patches (`patches/phase2/clock_patch.c` etc.)
were written for the OPPOSITE problem (virtual time racing under
icount sleep=off). With the REALTIME approach those patches become
no-ops — `qemu_rt_ctr` reads the same wall time as the kernel's own
`lbolt`. They don't conflict; they just become redundant.

Risk: any guest code that reads CP0_Count expecting CPU-cycle-level
precision (e.g. spin-locks calibrating with the COUNT register) sees
ticks happening more sparsely than it expects when the host can't
keep up. Most IRIX kernel uses are fine — they use COUNT for time, not
for cycle-accurate measurements.

### Option B — Split: keep COUNT virtual, drive COMPARE-IRQ7 from realtime

Slightly more complex but preserves spin-loop calibration. Make
CP0_Count read real-time-paced ticks (so kernel observes real
elapsed time) BUT have the timer interrupt fire on a separate
realtime-based schedule, decoupled from the COMPARE value the kernel
writes. This means COUNT-COMPARE difference is informational only,
not a deadline.

This is closer to what real PCs do with HPET vs TSC: HPET = HZ
interrupts; TSC = cycle counter. The kernel reads TSC for fine
timing, HPET fires at fixed intervals.

To implement: add a separate periodic timer that fires at the
configured HZ (100 for IRIX), and make CP0_Count continue advancing
at the COUNT frequency in real time.

### Option C — Hybrid: per-clock subsystem control

For some subsystems we want virtual (CPU benchmarks, cycle
profiling), for most we want realtime. Provide a runtime knob:

```
-machine sgi-ip54,timer-source=realtime
```

vs

```
-machine sgi-ip54,timer-source=virtual
```

Implement as a machine property that maps to a `MipsClockSource`
enum used by `cp0_timer.c`.

## Recommended path

**Option A**, as a switchable flag, defaulting to `realtime` on
`sgi-ip54`. Real R4000-class hardware behaves this way: COUNT
advances at a fixed (hardware) rate regardless of how busy the CPU
is — the CPU may be in WAIT, fielding interrupts, or running flat
out; the timer counter keeps marching. QEMU's VIRTUAL clock under
`-icount sleep=off` is actually *less faithful* to real hardware
behaviour than realtime.

Concrete diff sketch:

```c
/* qemu-sgi-repo/target/mips/system/cp0_timer.c */

/* New helper to pick the right clock — for ip54 we want REALTIME so
 * that the guest's perception of time is independent of how fast or
 * slow we're executing CPU code. */
static QEMUClockType mips_count_clock_type(CPUMIPSState *env)
{
    /* Hook a per-CPU property here in a real patch.  For now: */
    return QEMU_CLOCK_REALTIME;
}

static uint32_t cpu_mips_get_count_val(CPUMIPSState *env)
{
    int64_t now_ns = qemu_clock_get_ns(mips_count_clock_type(env));
    return env->CP0_Count +
            (uint32_t)clock_ns_to_ticks(env->count_clock, now_ns);
}
```

… and analogous changes in `cpu_mips_timer_update`, `cpu_mips_store_count`,
`cpu_mips_stop_count`, plus `env->timer = timer_new_ns(REALTIME, ...)`
in `cpu_mips_clock_init`.

### Side effects to verify

1. **The existing `qemu_rt_ctr` patch** (`hw/misc/sgi_mc.c`'s
   MC_REALTIME_CTR @ phys 0x1fa00050) becomes redundant; can leave it
   in place since the kernel patches read it.
2. **The pvclock-cause-race fix** from `progress_notes/ip54/cause_race_fix.md`
   stays. Both are needed.
3. **icount sleep=off accelerated UI**: with realtime, the user can
   just NOT use `-icount` (or use `-icount shift=auto,sleep=on`) — UI
   smoothness becomes a function of "is the host fast enough" rather
   than a knob coupled to wall time.
4. **Networking**: the slirp backend uses REALTIME timers anyway, so
   it already sees wall time. With this change the kernel side
   aligns.
5. **TCG translation latency**: each MMIO access calls into the
   device tree, which uses REALTIME timers. With this change the
   timer source already matches; no additional yields needed.

## What to test once implemented

| Test | Pass criteria |
|------|---------------|
| `ping -c 5 10.0.2.2` from guest | RTTs in human-perceptible ms (not microseconds, not seconds) |
| Move host cursor over GTK window | Guest cursor tracks at 60 Hz |
| `date; sleep 5; date` in guest shell | Reports 5 sec wall elapsed, regardless of CPU load |
| Heavy CPU work in guest while pinging from outside | RTTs stay roughly stable (the CPU running flat out doesn't dilate time) |
| `time uname` repeated | Wall-time `real` matches wall stopwatch |

The KEY test: **animations and timed events advance at the same rate
whether the host is fast (e.g., idle workstation) or slow (e.g.,
loaded laptop with thermal throttle).**

## What I'd write next

A single QEMU patch:

1. Add `count_clock_type` field to `CPUMIPSState` (default `QEMU_CLOCK_REALTIME`).
2. Touch `cp0_timer.c` to use it.
3. Add machine option in `hw/mips/sgi_ip54pv.c` to set it (or just
   hardcode REALTIME for IP54).
4. Re-run the existing kernel-side patches (`clock_patch.c`,
   `ptimers_patch.c`, `select_patch.c`) — verify they still build but
   their gate becomes a fast-path; no harm.

Then rebuild QEMU, boot, and run the pass-criteria tests above.

## Reference: existing related work

- `progress_notes/kernel_patch.md` / memory `kernel_patch.md` — the
  qemu_rt_ctr fast-path inside the IRIX kernel; relevant to the
  current `-icount` workaround but becomes redundant with this fix.
- `progress_notes/ip54/cause_race_fix.md` — orthogonal: it fixes
  guest/host concurrent writes to CP0_Cause, which is correctness
  regardless of which clock source the COMPARE timer uses.
- `patches/phase2/clock_patch.c`, `ptimers_patch.c`, `select_patch.c` —
  the runtime gates that work around virtual-time racing; will become
  no-ops once COMPARE is realtime-driven.

## Status

Investigation done. Concrete diff sketch above. The actual patch is
the obvious next step but I'm pausing here to record findings before
touching code, given the complexity of the broader work in flight.
