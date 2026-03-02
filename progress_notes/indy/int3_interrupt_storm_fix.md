# INT3 Local0 Spurious Interrupt Storm Fix

## Date: 2026-02-09

## Problem

After the IRIX 6.5 miniroot kernel completed root mount and XFS recovery,
the system went completely silent. No new klogmsgs, no serial output, no
SCSI commands. The CPU appeared stuck in the idle loop.

## Root Cause

**Spurious LIO_CENTR (bit 1) interrupt in INT3 local0_stat.**

The INT3 local0_stat register had bit 1 (0x02) stuck high. This bit
corresponds to `LIO_CENTR` — the Centronics parallel port interrupt from
the PI1 controller. Since we don't emulate the PI1 hardware, this interrupt
source could never be acknowledged by the kernel.

The IRIX `lcl_stray()` handler ran on every IP2 (LOCAL0) interrupt, counted
the stray, and returned — but never cleared the hardware source. The
interrupt immediately re-asserted, creating an infinite interrupt storm:

```
spl0() → IP2 fires → lcl0_intr() → lcl_stray() → return
→ IP2 still asserted → fires again → repeat 500+ times/second
```

This consumed most CPU time and interfered with thread scheduling in the
idle loop.

## Investigation Journey

1. **PC sampling** showed CPU 100% in idle loop (4 unique PCs in 0x88006e28-0x88007198)
2. **CP0 timer trace** confirmed scheduling clock working (700+ expiries on IP7)
3. **PIT timer investigation** — ruled out: kernel uses R4000 Count/Compare, not 8254 PIT
   - `is_ioc1_flag = 2` (IOC2 boards) → `startrtclock_r4000()` selected
   - PIT counter 0/1 never configured by kernel
4. **INT3 register dump** revealed `local0_stat=0x02, mask=0xa3` → bit 1 pending + enabled
5. **LOCAL0_STAT read trace** confirmed 800+ reads all showing `stat=0x02`
6. Bit 0x02 = LIO_CENTR (parallel port) — not set by our code, source unknown

## Key Discovery: IRIX Uses R4000 Count/Compare Timer

The IRIX kernel on IOC1/IOC2 boards does NOT use the 8254 PIT for scheduling:

```c
// timer.c init_timer()
if (is_ioc1()) {       // true for IOC2 boards (is_ioc1_flag = 2)
    __startrtclock = startrtclock_r4000;  // Uses CP0 Count/Compare
} else {
    __startrtclock = startrtclock_8254;   // Uses PIT (only on old boards)
}
```

The scheduling clock fires on IP7 (CP0 Count/Compare timer interrupt),
NOT IP4/IP5 (PIT timer outputs). The PIT is only used for PROM delay
loops (counter 2).

## Fix

Mask unused interrupt bits in `sgi_hpc3_update_irq()`:

```c
/* Only bits corresponding to emulated hardware should be set */
s->int3_local0_stat &= (INT3_LOCAL0_SCSI0 | INT3_LOCAL0_SCSI1 |
                         INT3_LOCAL0_MAPPABLE0);
```

This ensures that only emulated interrupt sources (SCSI0, SCSI1, mapped
interrupts) can appear in local0_stat. Unimplemented hardware (PI1 parallel
port, ethernet, GIO graphics) cannot cause stray interrupts.

## Verification

- Cause register: `IP=00000000` (was `IP=00000111` — all stray bits gone)
- Status register: `IM=11111111` at WAIT instruction (clean spl0)
- CPU cleanly halted in WAIT with no pending interrupts
- All 505 fast tests pass

## Resolution

The remaining silence after "audio: AES receiver not responding." was
resolved by subsequent fixes: the Z85C30 WR0 register pointer masking
(bits [2:0] not [3:0]) fixed STREAMS TX, and longer boot timeouts with
`-icount shift=0,sleep=off` allowed device enumeration and init to
complete. IRIX 6.5 now boots fully to multi-user login and 4Dwm desktop.
