# Virtual Time Investigation & icount sleep=off Fix

See also: [`indy_boot_milestones.md`](indy_boot_milestones.md) for how this
fits into the overall boot timeline.

## Problem

IRIX 6.5 miniroot boot is extremely slow: the kernel makes only ~1.4 SCSI
commands/second despite running on a powerful host. Device enumeration
("Creating miniroot devices, please wait...") takes 5+ minutes and still
doesn't finish.

## Root Cause

The CPU spends 80%+ of time in the MIPS `WAIT` instruction (kernel idle
loop). Without icount mode, QEMU virtual time tracks wall-clock time:

- The IRIX 100 Hz scheduler timer (PIT Timer 0) fires at most 100 times
  per real second.
- Each scheduling quantum is 10 ms of *real* time.
- Each SCSI operation requires at least one scheduling quantum.
- Result: ~100 operations/second maximum throughput.

## Why `-icount shift=0` Alone Doesn't Help

With `-icount shift=0`, `sleep=on` is the default. When the CPU executes
WAIT and halts, QEMU's `icount_start_warp_timer()` function schedules a
*real-time* warp timer to advance virtual time:

```c
// icount-common.c, sleep=on path:
timer_mod_anticipate(timers_state.icount_warp_timer,
                     clock + deadline);
```

This means virtual time still advances at wall-clock speed during idle
periods — the warp timer fires in real time, so a 10 ms PIT period still
takes 10 ms of wall-clock time.

## The Fix: `-icount shift=0,sleep=off`

With `sleep=off`, the code takes a different path that advances virtual
time *instantly*:

```c
// icount-common.c, sleep=off path:
qatomic_set(&timers_state.qemu_icount_bias,
            timers_state.qemu_icount_bias + deadline);
qemu_clock_notify(QEMU_CLOCK_VIRTUAL);
```

The deadline is added directly to `qemu_icount_bias`, making the next
PIT timer fire immediately in virtual time. The CPU wakes up, processes
the interrupt, and if it WAITs again, the cycle repeats at host speed.

## Bare-Metal Timing Benchmarks

A MIPS assembly test binary (`tests/bare_metal/timing_test.S`) runs 5
timing tests directly on the emulated hardware, outputting results via
Z85C30 UART.

### Results: Default Mode (no icount)

```
TEST COUNT_RATE:      delta=817    iterations=10000   PASS
TEST WAIT_WAKEUP:     delta=16929  expected=10000     tolerance=100000 PASS
TEST PIT_PERIOD:      delta=562612 expected=500000    tolerance=100000 PASS
TEST INST_THROUGHPUT: delta=2460   expected=500       PASS
TEST MEM_THROUGHPUT:  delta=550    iterations=1000    PASS
```

- WAIT wakeup latency: ~17K CP0 Count ticks (nondeterministic)
- PIT period: ~562K ticks (12% above expected 500K)
- Timing varies between runs

### Results: `-icount shift=0,sleep=off`

```
TEST COUNT_RATE:      delta=1500   iterations=10000   PASS
TEST WAIT_WAKEUP:     delta=2      expected=10000     tolerance=100000 PASS
TEST PIT_PERIOD:      delta=500000 expected=500000    tolerance=100000 PASS
TEST INST_THROUGHPUT: delta=50     expected=500       PASS
TEST MEM_THROUGHPUT:  delta=250    iterations=1000    PASS
```

- **WAIT wakeup latency: 2 ticks** (down from 17K — virtually instant)
- **PIT period: exactly 500000 ticks** (perfectly deterministic)
- All timing is deterministic and reproducible

### Key Insight

With `sleep=off`, the PIT timer fires as fast as the CPU can process each
quantum, rather than every 10 ms of real time. This means:

- 100 Hz PIT → 100 scheduling quanta per second (wall-clock limited)
- With sleep=off → scheduling quanta fire at host speed (potentially
  thousands per second)

## PROM Boot: No Effect

Benchmarking confirms that `-icount shift=0,sleep=off` has **no measurable
effect** on PROM boot timing:

| Config | Default | icount sleep=off |
|--------|---------|-------------------|
| No devices, 64MB | 30.51s | 30.52s |
| Disk + CD-ROM | 120.59s | 120.61s |

The PROM never executes WAIT — all delays are calibrated polling loops
(escape countdown, SCSI target probe timeouts). These are wall-clock bound
regardless of icount settings. See `benchmark_results.md` for full data.

## Recommended Launch Flags

For fastest IRIX **kernel** boot (no effect on PROM phase):
```
-icount shift=0,sleep=off
```

For miniroot boot via MCP:
```
qemu_serial_interact
  extra_args="-icount shift=0,sleep=off"
  scsi_drives=["/workspace/irix_disk.img",
               "software_library/irix_6.5.22_images/IRIX 6.5 Installation Tools June 1998.img:cdrom"]
  timeout=120
```

## The Tradeoff: What sleep=off Breaks

`sleep=off` decouples virtual time from real time during idle periods. The effect is
asymmetric:

- **During active computation** (no WAIT): virtual time ≈ real time
- **During WAIT** (CPU idle): virtual time races ahead; zero real time passes

Anything that depends on real-time pacing is affected:

| What breaks | Why |
|---|---|
| `ping`, TCP retransmit, ARP timeout | `select()` timeout expires in virtual time before SLIRP delivers the reply in real time |
| `xclock`, `sleep()`, `usleep()` | Kernel timers are virtual-time-based; they fire far sooner in real time than intended |
| Animations paced by `usleep` | Frames advance at virtual-time rate, not wall-clock rate |
| X key autorepeat | Repeat timer fires many times per real keypress (fixed with `xset r off` in `Xsetup_0`) |
| Audio timing | Sample delivery timing would be wrong if HAL2 DMA were active |

What doesn't break:

- **CPU-bound computation** — no WAIT, so virtual time and real time track at roughly
  the same ratio during active work
- **File I/O correctness** — data integrity is fine; only timing is affected
- **What you see in VNC/SDL** — QEMU's host display refresh timer uses
  `QEMU_CLOCK_REALTIME`, so the display updates at ~30 fps real-time regardless of
  how many frames the guest renders in virtual time

## Networking with Different icount Settings

The networking behavior differs significantly depending on icount settings:

| Setting | Network Behavior | When to Use |
|---|---|---|
| **No icount** | Works correctly. SLIRP timing matches real time. | Interactive use after installation |
| **`sleep=on`** (default) | Works correctly. WAIT waits in real time, keeping SLIRP synced. | Interactive use; slower but correct timing |
| **`sleep=off`** | **Breaks**. Virtual time races ahead of SLIRP's real-time responses. Ping shows 100% packet loss. | Fast install/boot only — **not for interactive use** |

### Why sleep=off breaks networking

SLIRP (QEMU's user-mode networking) delivers packets in real time. When IRIX sends a ping:

1. IRIX kernel calls `select()` with a 1-second timeout (virtual time)
2. Kernel enters WAIT idle loop while waiting for reply
3. With `sleep=off`, virtual time jumps forward instantly during WAIT
4. The 1-second virtual timeout expires in **microseconds** of real time
5. SLIRP's reply arrives ~5ms later in real time, but ping has already timed out

This is a timing mismatch, not a correctness issue. The reply does arrive:
`netstat -s -p icmp` shows `echo reply: N received, 0 bad checksums`.
But ping's receive loop has already exited.

### Recommended workflow

1. **Installation and boot** with `-icount shift=0,sleep=off`
   - Fast: install completes in minutes instead of hours
   - Networking doesn't need to work during install

2. **Save snapshot** at a usable state

3. **Resume without icount** for interactive use
   ```python
   qemu_session_start(instance="irix65-desktop", snapshot="irix655_booted")
   # no -icount in extra_args
   ```
   - Networking works, animations run at real speed, `ping` succeeds
   - Everything runs at correct timing relative to real time

### Concrete networking example

An ICMP echo reply returns from SLIRP in ~5 ms of real time. Ping's 1-second
`select()` timeout is measured in virtual time. While ping's kernel thread waits for
the reply, the guest CPU is in WAIT — so virtual time races forward. The 1-second
virtual budget expires long before SLIRP delivers the real-time reply.

The reply does arrive: `netstat -s -p icmp` shows `echo reply: N received, 0 bad
checksums`. But ping's receive loop has already timed out and exited, reporting 100%
packet loss.

## Virtualization vs. Emulation

This timing problem doesn't occur with VMware, VirtualBox, Hyper-V, or QEMU+KVM
because those use **hardware-assisted virtualization** (Intel VT-x / AMD-V). Guest
instructions execute directly on the host CPU at native speed. There is no instruction
counting, no virtual/real-time split — the guest's TSC reads the host's real TSC, and
time is real time by construction.

QEMU+TCG (what we use for MIPS) is different: it **translates MIPS instructions to
x86 instructions** in software. The guest runs slower than real hardware, so some form
of timer correction is required.

QEMU offers three modes for handling virtual time in TCG:

| Mode | How virtual time advances | Timers correct? | Speed |
|---|---|---|---|
| Default (no icount) | Loosely tracks real time; timers fire at real-time intervals | Yes | Slowest (scheduling-limited) |
| `-icount shift=N,sleep=on` | Instruction-counted, but WAIT waits in real time | Yes | Same as default |
| `-icount shift=N,sleep=off` | Instruction-counted; WAIT skips instantly | No — races ahead | Fastest for idle-heavy |

The default mode is what GXemul and MAME use: everything runs timing-correctly, just
slower than real hardware. `sleep=off` is a deliberate optimization for idle-heavy
workloads (IRIX boot, package install) where skipping WAIT periods dramatically
improves throughput. It is not intended for interactive use.

## icount shift as a CPU Frequency Knob

`shift=N` sets the virtual time cost per instruction:

| shift | ns/instruction | Virtual CPU frequency |
|---|---|---|
| 0 | 1 ns | 1 GHz |
| 1 | 2 ns | 500 MHz |
| 3 | 8 ns | 125 MHz |
| — | 6.67 ns | 150 MHz (real R4400) |

In principle, calibrating `shift` to match actual TCG throughput would make virtual
time track real time — the same idea as "running the CPU faster." The problem is that
throughput is not constant: a WAIT-heavy idle desktop, a disk-heavy package install,
and an X11-heavy rendering workload all have very different instruction rates.

QEMU provides `-icount shift=N,align=on` to handle this dynamically: it monitors the
drift between virtual and real time and inserts synthetic pauses when virtual time races
too far ahead. This is more correct than `sleep=off` for interactive use, but it works
by slowing things down to match real time — not by speeding up emulation — so it
eliminates the boot/install speedup.

### Why we can't lie to IRIX about CPU frequency

The SGI Memory Controller has an **RPSS register** (Real-time Programmable
System-Synth) that ticks at a fixed 1 MHz, independent of CPU speed. IRIX calibrates
the actual CP0 Count rate against RPSS at boot to determine true CPU frequency:

```c
// Simplified: IRIX measures CP0 ticks per RPSS tick to compute cpufreq
cpufreq = measure_cp0_per_rpss() * RPSS_HZ;
```

If we set the PROM environment to claim a 1 GHz CPU but CP0 Count only advances at
its actual virtual-time rate, IRIX detects the discrepancy and uses the measured value.
RPSS in our emulation (`sgi_mc.c`) is driven by `QEMU_CLOCK_VIRTUAL`, so both clocks
race equally under `sleep=off` and the ratio (measured CPU frequency) is unchanged.

## Recommended Workflow

Use icount only for the phases where it helps, and save a snapshot before interactive use:

1. **Install and initial boot** with `-icount shift=0,sleep=off`
   Fast, timing-incorrect. Acceptable because we don't need real-time correctness
   during package installation or filesystem setup.

2. **Save snapshot** at a usable state (xdm login screen, root shell)
   ```python
   qemu_session_snapshot(session_id=sid, instance="irix655-full",
                         description="post-install login")
   ```

3. **Resume without icount** for interactive use
   ```python
   qemu_session_start(instance="irix655-full", snapshot="irix655_booted")
   # no -icount in extra_args
   ```
   Networking works, `xclock` ticks correctly, `ping` succeeds, animations run at the
   right speed.

This is already the pattern for the `irix655-desktop` instance: icount was used during
install and the initial boot, then `irix655_devtools` and `irix655_booted` snapshots
were saved for interactive resume without icount.

## Test Coverage

### Fast Source-Analysis Tests (`tests/test_virtual_time.py`, 11 tests)

Verify QEMU source code patterns critical for virtual time:

| Test | What it verifies |
|------|-----------------|
| `test_wait_sets_halted` | `helper_wait` sets `cs->halted = 1` |
| `test_wait_raises_hlt` | `helper_wait` raises `EXCP_HLT` |
| `test_cpu_has_work_checks_interrupts` | Wakeup checks `CPU_INTERRUPT_HARD` |
| `test_cpu_has_work_checks_enabled` | Wakeup checks `hw_interrupts_enabled` |
| `test_pit_uses_virtual_clock` | PIT timers use `QEMU_CLOCK_VIRTUAL` |
| `test_pit_clock_is_1mhz` | `PIT_NS_PER_TICK = 1000` |
| `test_cp0_count_uses_virtual_clock` | CP0 Count uses `QEMU_CLOCK_VIRTUAL` |
| `test_icount_sleep_off_instant_warp` | `!icount_sleep` does immediate bias update |
| `test_icount_sleep_on_schedules_warp_timer` | `icount_sleep` uses `timer_mod_anticipate` |
| `test_cpu_clock_100mhz` | `clock_set_hz(cpuclk, 100000000)` |
| `test_ccres_divider` | `clock_set_mul_div(count_div, CCRes, 1)` |

### Slow Integration Tests (`tests/test_cpu_timing.py`, 19 tests)

Run bare-metal binary on QEMU, parse serial output:

| Test | What it validates |
|------|------------------|
| `test_bare_metal_builds` (3) | Makefile, binary, size |
| `test_count_rate` (3) | CP0 Count advances with loop iterations |
| `test_wait_wakeup` (2) | Compare interrupt wakes CPU from WAIT |
| `test_pit_period` (2) | PIT period matches programmed count |
| `test_inst_throughput` (2) | 1000 NOPs produce measurable Count delta |
| `test_mem_throughput` (2) | Memory loads produce measurable timing |
| `test_icount_sleep_off` (2) | Tests complete with icount sleep=off |
| `test_output_format` (3) | All 5 tests present, parseable output |

## Hardware Timing Chain

```
Host CPU
  └─ QEMU TCG (translates MIPS → host instructions)
       └─ QEMU_CLOCK_VIRTUAL (virtual time, ns)
            ├─ cpu-refclk: 100 MHz
            │    └─ clk-div-count: ÷ CCRes (÷2)
            │         └─ clk-count: 50 MHz → CP0 Count register
            │              └─ CP0 Compare → IP7 interrupt
            └─ PIT timers: 1 MHz (PIT_NS_PER_TICK=1000)
                 ├─ Timer 0 → IP4 (scheduling clock, 100 Hz in IRIX)
                 └─ Timer 1 → IP5 (profiling clock)
```

With `sleep=off`, QEMU_CLOCK_VIRTUAL jumps forward instantly during WAIT,
so the entire chain runs as fast as the host can execute the non-idle
portions of the guest code.

## Files

| File | Purpose |
|------|---------|
| `tests/bare_metal/timing_test.S` | MIPS assembly timing benchmark |
| `tests/bare_metal/link.ld` | Linker script (text at 0xBFC00000) |
| `tests/bare_metal/Makefile` | Build with mips-elf cross-compiler |
| `tests/test_virtual_time.py` | Fast source-analysis tests (11) |
| `tests/test_cpu_timing.py` | Slow integration tests (19) |
| `tests/conftest.py` | Added `icount_source`, `exception_source` fixtures |
| `tests/helpers/qemu_runner.py` | Added `run_bare_metal()` method |
