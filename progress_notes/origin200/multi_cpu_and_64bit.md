# Multi-CPU and 64-Bit Architecture

## True 64-Bit Kernel

Unlike the O2 (IP32) which uses n32 ABI (32-bit pointers despite 64-bit CPU),
the Origin 200 (IP27) IRIX kernel uses the **N64 ABI**:

- True 64-bit pointers and `long` type
- Full 64-bit virtual address space
- IRIX reports `irix64` uname rather than `irix`
- Kernel compiled with `-mips4 -64`
- Removes the O2's practical ~1 GB user-space limit

For QEMU: no ABI differences in the CPU model — both n32 and N64 run on the
same MIPS64 hardware. The CPU type selection matters only for QEMU's `-cpu`
option to ensure correct MIPS IV instruction support.

**QEMU CPU flag**: `-cpu MIPS64R2-generic` or `-cpu mips64dspr2-mips64`.
The R10000 is MIPS IV (not MIPS64 in the ISA sense, but QEMU's `mips64` target
handles MIPS IV). Cross-check the actual cpu model used in QEMU's MIPS target:
look at `target/mips/cpu.c` for available CPU models.

---

## Hub PI: Two CPUs Per Node

The Hub supports up to 2 CPUs (A and B), called "slices". Each CPU has its
own PI resources: interrupt masks, RT compare, error stack, etc.

### CPU Detection Registers

| Register | Description | Single-CPU boot value |
|----------|-------------|-----------------------|
| `PI_CPU_PRESENT_A` (0x01000040) | CPU A physically present | 1 |
| `PI_CPU_PRESENT_B` (0x01000048) | CPU B physically present | 0 |
| `PI_CPU_ENABLE_A` (0x01000050) | CPU A enabled | 1 |
| `PI_CPU_ENABLE_B` (0x01000058) | CPU B enabled | 0 |
| `PI_CPU_NUM` (0x01000020) | Which CPU is running (0=A, 1=B) | 0 (CPU A reading) |

For a 2-CPU boot: both PRESENT and ENABLE bits are 1, and `PI_CPU_NUM`
returns 0 for CPU A and 1 for CPU B (per-CPU local read).

### Local Arbitration Protocol

When both CPUs power on, they run the same PROM code in parallel. The PROM
arbitrates for "local master" using `PI_RT_COMPARE_A/B` as shared progress
indicators:

```c
/* CPU A uses PI_RT_COMPARE_A; CPU B uses PI_RT_COMPARE_B */
/* PROM writes PLED_LOCALARB (progress code) to its own register */
/* Then waits LOCAL_ARB_TIMEOUT (200000 loops) for peer to also write it */
/* First CPU to survive becomes master; disables the other's PI_CPU_ENABLE */
```

In QEMU with 2 vCPUs:
- Both vCPUs start at reset vector (0xBFC00000)
- `PI_CPU_NUM` must return different values per CPU
  - QEMU challenge: this register normally returns a static value
  - Option A: CPU A always returns 0, CPU B always returns 1
    (requires each vCPU to have different Hub PI register views)
  - Option B: Start only CPU A; enable CPU B later via `PI_CPU_ENABLE_B` write
    (simpler for initial implementation)

**Recommendation for initial IP55**: implement single-CPU only.
Set `PI_CPU_PRESENT_B = 0`. The PROM sees single-CPU and skips arbitration.

---

## CPU B Startup (For Future 2-CPU Support)

In a 2-CPU system, the master CPU (A) starts CPU B by:

```c
/* Write to enable CPU B — this should trigger CPU B to start executing */
SD(LOCAL_HUB(PI_CPU_ENABLE_B), 1);
```

CPU B starts spinning in the PROM's slave loop (`IP27PROM_SLAVELOOP` at
0xBFC00010), waiting for the master to post a launch entry.

**QEMU SMP implementation notes**:
1. CPU B starts running in "spin" state at reset
2. When `PI_CPU_ENABLE_B` receives write of 1, QEMU resumes vCPU 1 from halt
3. vCPU 1 reads `PI_CPU_NUM` → returns 1
4. Both vCPUs execute PROM slave/master loops
5. The launch mechanism uses memory-based mailboxes (KLD_LAUNCH in kldir)

This is a future milestone. For now, a single vCPU is sufficient.

---

## Hub PI_RT_COUNT: The Scheduling Clock

### Frequency and Behavior

```c
/* From ip27config.h */
#define IP27_RTC_FREQ  1250  /* 800ns cycle time = 1250 Hz */
```

The `PI_RT_COUNT` register at 0x01030100 is a **free-running 64-bit counter**
that increments at 1250 Hz. It is used by the IRIX IP27 kernel for:
- Scheduling clock (`startrtclock_r4000()` equivalent → `startrtclock_ip27()`)
- RTC time tracking
- PROM timing (`rtc_time()` in PROM source)

### Interrupt Path

When `PI_RT_COUNT >= PI_RT_COMPARE_A`, an interrupt fires on CPU A as **IP8**
(MIPS CP0 cause bit SR_IBIT8). This is external interrupt level 6 in MIPS
terminology.

```
PI_RT_EN_A (0x01000140) = 1  → enable RT interrupt for CPU A
PI_RT_COMPARE_A (0x01000108) → set next deadline
```

IRIX programs `PI_RT_EN_A = 1` then sets `PI_RT_COMPARE_A = PI_RT_COUNT + N`
to schedule the next tick. After each interrupt, it re-arms the compare.

### QEMU Implementation

```c
/* QEMU timer implementation */
#define PI_RT_FREQ_HZ  1250

/* In Hub PI state: */
QEMUTimer *rt_timer;
uint64_t rt_count;     /* free-running counter */
uint64_t rt_compare_a; /* CPU A compare value */

/* rt_count increments at 1250 Hz */
/* When rt_count wraps past rt_compare_a → assert IP8 to vCPU 0 */
/* Re-arm timer for next 800µs */
```

**Important**: With `-icount shift=0,sleep=off`, virtual time races through
WAIT instructions. This makes the 1250 Hz timer fire extremely rapidly in
wall-clock time. For IRIX networking this is problematic (same as O2 with
icount). See the CLAUDE.md note: **do NOT use `-icount shift=0,sleep=off`
with Origin 200 serial sessions** after networking is enabled.

### Difference from O2 / Indy

- **Indy (IP22/IP24)**: IRIX uses R4000 Count/Compare (CP0) for scheduling
- **O2 (IP32)**: Uses R10000 Count/Compare via CRIME timer integration
- **Origin 200 (IP27)**: Uses Hub `PI_RT_COUNT` (not CP0 Count/Compare)
  - IP27 kernel checks `is_ip27()` at boot and uses Hub timer path
  - CP0 Count/Compare still present but not used as primary scheduler

---

## IRIX Kernel ABI on IP27

### N64 vs N32

| | Indy/O2 | Origin 200 |
|-|---------|------------|
| Kernel ABI | 32-bit (o32) / n32 | N64 |
| `sizeof(long)` | 4 / 4 bytes | 8 bytes |
| `sizeof(pointer)` | 4 / 4 bytes | 8 bytes |
| Max RAM visible | ~3.8 GB | >8 GB per node |
| uname -m | IP22 / IP32 | IP27 |

### QEMU CPU Model

The R10000 is MIPS IV. QEMU's MIPS64 target supports MIPS IV. Use:
```
-cpu MIPS64R2-generic   # or whatever QEMU 10.x exposes for MIPS64
```

Check `qemu/target/mips/cpu.c` for the list of available CPU models.
The key requirement: support 64-bit mode (SR_KX), MIPS IV instructions
(FP, LL/SC, etc.), and hardware TLB operations.

### ip27config `r10k_mode` Boot Configuration

The PROM reads `ip27c_r10k_mode` from the ip27config structure at boot and
programs the R10000 configuration register accordingly. This controls:
- Secondary cache size
- Bus clock ratio
- Cache line size

In QEMU, these are irrelevant (QEMU handles caches internally). The PROM
write to the R10000 config register will simply be ignored.

---

## Summary: QEMU Milestones for Multi-CPU

### Milestone 1 (single vCPU)
- `PI_CPU_PRESENT_B = 0`
- `PI_CPU_NUM = 0`
- `PI_RT_COUNT`: free-running counter at 1250 Hz
- Sufficient to boot IRIX to multi-user

### Future (dual vCPU)
- `PI_CPU_PRESENT_B = 1`
- `PI_CPU_NUM` returns 0 or 1 per-vCPU
- Write to `PI_CPU_ENABLE_B = 1` → resume vCPU 1
- KLD launch mechanism in DRAM for CPU B bootstrap

## Sources

- `irix/kern/sys/SN/SN0/hubpi.h` — PI register definitions
- `irix/kern/sys/SN/SN0/ip27config.h` — IP27_RTC_FREQ, r10k_mode
- `stand/arcs/IP27prom/main.c` — arb_local_master() protocol
- `gathered_documentation/techpubs/007-3410-001 *.pdf` — Programmer's Reference
