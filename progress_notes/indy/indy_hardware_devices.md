# Indy Hardware Devices

Detailed notes on each hardware device implemented for the SGI Indy (IP24)
emulation. Covers bugs found and fixed, register layouts, and behavioral
details learned from debugging.

---

## 1. Newport Graphics (REX3)

### DCB Mode Register Bit Layout

The DCB (Display Control Bus) mode register controls sub-device access.
Initially implemented with completely wrong bit positions. Correct layout
(matching MAME `newport.cpp:4274-4305`):

```
bits [1:0]   data width
bit  2       enable data packing
bit  3       enable CRS auto-increment
bits [6:4]   CRS (register select)
bits [10:7]  slave address
bit  11      sync ACK enable
bit  12      async ACK enable
bits [17:13] CS width (5 bits)
bits [22:18] CS hold (5 bits)
bits [27:23] CS setup (5 bits)
bit  28      swap byte ordering
```

VC2 (slave 0) appeared to work before the fix because all slave bits = 0
regardless of extraction. Other devices (XMAP, CMAP, RAMDAC) were misrouted.

### Sub-Word MMIO Access

The PROM reads XMAP FIFO status via `lbu` (byte load). With
`impl.min_access_size = 4` and `DEVICE_BIG_ENDIAN`, QEMU's access widening
extracts the MSB via right-shift, but the handler returned the value in the
LSB.

**Fix:** Changed `impl.min_access_size` to 1 and added explicit byte
extraction:

```c
if (size < 4) {
    unsigned shift = (4 - size - (byte_offset & 3)) * 8;
    val = (val >> shift) & ((1U << (size * 8)) - 1);
}
```

### CMAP Status Register

PROM polls CMAP CRS=4 bit 3 for readiness. MAME initializes `m_status = 8`
(bit 3 set). Our handler only had CRS 0/1/2; CRS 4 (status) and CRS 6
(revision = 0xa1) were missing or at wrong positions.

### Register Write Masks

The PROM's `test_rex3()` function writes 4 test patterns (0xffffffff,
0xaaaaaaaa, 0x55555555, 0x00000000) to ~45 registers and reads them back
with expected masks. Key registers and their correct masks:

| Register | Offset | Mask | Bits |
|----------|--------|------|------|
| LSMODE | 0x0008 | 0x0fffffff | 28 |
| ALPHAREF | 0x0020 | 0x000000ff | 8 |
| XSAVE | 0x0110 | 0x0000ffff | 16 |
| BRESD | 0x0118 | 0x07ffffff | 27 |
| BRESS1 | 0x011c | 0x0001ffff | 17 |
| BRESOCTINC1 | 0x0120 | 0x070fffff | 27 sparse |
| BRESRNDINC2 | 0x0124 | 0xff1fffff | 32 sparse |
| BRESE1 | 0x0128 | 0x0000ffff | 16 |
| BRESS2 | 0x012c | 0x03ffffff | 26 |
| COLORRED | 0x0200 | 0x00ffffff | 24 |
| COLORALPHA | 0x0204 | 0x000fffff | 20 |
| COLORGREEN | 0x0208 | 0x000fffff | 20 |
| COLORBLUE | 0x020c | 0x000fffff | 20 |
| WRITEMASK | 0x0220 | 0x00ffffff | 24 |
| TOPSCAN | 0x1320 | 0x000003ff | 10 |
| CLIPMODE | 0x1328 | 0x00001fff | 13 |

### Slope Sign-Magnitude Conversion

The PROM's `test_rex3_slopecolor()` expects slope registers to convert from
two's complement (write) to sign-magnitude (readback):

```c
static uint32_t newport_twos_to_sm(uint32_t data, int nbits)
{
    uint32_t sign = 1U << (nbits - 1);
    uint32_t mask = sign - 1;
    if (data & 0x80000000) {
        return sign | ((-data) & mask);
    } else {
        return data & mask;
    }
}
```

Affected registers: SLOPERED (24-bit), SLOPEALPHA (20-bit), SLOPEGREEN
(20-bit), SLOPEBLUE (20-bit), SLOPEREDCOPY (24-bit).

---

## 2. WD33C93 SCSI Controller

### Address Register Auto-Increment

The WD33C93 automatically increments the address register after each data
read/write, except for COMMAND and AUXILIARY_STATUS registers. Without this,
transfer count registers weren't set correctly and the PROM got stuck in a
polling loop.

### Status Code in TARGET_LUN Register

`wd33c93_command_complete()` stores the SCSI device status in TARGET_LUN
register bits [4:0], preserving TLV (bit 7). This matches MAME
`wd33c9x.cpp:1136-1137`. The WD33C93 SCSI_STATUS register itself always
reports SELECT_TRANSFER_SUCCESS — the actual device status lives in
TARGET_LUN.

### COMMAND_PHASE Register

Tracks through the lifecycle:
- `0x10` (CP_BYTES_0): After building CDB
- `0x30` (TRANSFER_COUNT): When data transfer starts
- `0x60` (COMMAND_COMPLETE): When command finishes

IRIX kernel reads this to track command progress.

### TRANSFER_INFO Command Path

If kernel uses SELECT_ATN + TRANSFER_INFO (split operation) instead of the
combined SELECT_ATN_XFER, the TRANSFER_INFO handler needs a code path to
build and execute a SCSI request when `current_req` is NULL but
`current_dev` is set from a prior SELECT.

### CD-ROM Boot Sequence

1. INQUIRY (target 4) → 36 bytes, device type 5 (CD-ROM)
2. MODE SELECT → blocksize 2048→512, max_lba recalculated
3. READ(10) LBA=0 → SGI volume header (512 bytes)
4. READ(10) LBA=0xa873 → sashARCS first sector (COFF header)
5. Multiple READ(10) → loads sashARCS sections (~316KB)
6. sashARCS executes, performs its own SCSI cycle
7. sashARCS autoboot fails → install CD has no kernel in partition 0

---

## 3. HPC3 DMA

### EOX Drain

After the main DMA transfer loop exits (`async_len == 0`), zero-count
EOX (End-of-transfer) terminal descriptors must still be processed to
properly clear `scsi_dma_active` and the ENABLE bit. Without this drain
loop: `PANIC: SCSI DMA in progress bit never cleared`.

### XIE Interrupt Routing

When the HPC3 DMA descriptor chain hits XIE (interrupt-on-end):
1. Set `intstat` and `scsi_ctrl[ch] |= IRQ`
2. Set `INT3_LOCAL1_HPC_DMA` (0x10) in LOCAL1 status
3. Call `sgi_hpc3_update_irq()` to route to CPU

On SCSI CTRL register read, when IRQ bit auto-clears and intstat becomes 0,
clear the LOCAL1 HPC_DMA bit. Same clearing on INTSTAT write-to-clear.

---

## 4. INT3 Interrupt Controller

### Centralized Cascade Architecture

The INT3 has three interrupt cascade levels:

1. **Map status → Map mask → MAPPABLE bits in Local status**
   - `map_status & map_mask0` → set/clear MAPPABLE0 in `local0_stat`
   - `map_status & map_mask1` → set/clear MAPPABLE1 in `local1_stat`

2. **Local status → Local mask → CPU IRQ**
   - `local0_stat & local0_mask` → assert/deassert IP2
   - `local1_stat & local1_mask` → assert/deassert IP3

3. **PIT timers bypass INT3 entirely** (direct to CPU)
   - Timer0 → IP4, Timer1 → IP5 (**VERIFIED** — MAME cross-ref)

### Three Bugs Fixed

**Bug 1:** PIT timer callbacks set `int3_map_status |= INT3_MAP_TIMER0/TIMER1`.
Per MAME `ioc2.cpp:210-226`, PIT timers go directly to CPU IRQ lines,
bypassing INT3. The map_status bits caused spurious cascades when the kernel
enabled map_mask0.

**Bug 2:** No centralized cascade function. Individual sources manually
manipulated both map_status and local0_stat, leading to inconsistent state.

**Bug 3:** MAP_MASK writes didn't re-evaluate the cascade. If map_status had
pending bits when the kernel enabled mask bits, the cascade wouldn't fire.

### Correct Interrupt Routing Table

| Source | CPU IRQ | MAME Reference | IRIX Handler |
|--------|---------|----------------|--------------|
| INT3 Local0 | IP2 | `ioc2.cpp:268-284` cascade | `lcl0_intr` |
| INT3 Local1 | IP3 | `ioc2.cpp:268-284` cascade | `lcl1_intr` |
| PIT Timer 0 | IP4 | `ioc2.cpp:210-226` direct | `clock()` |
| PIT Timer 1 | IP5 | `ioc2.cpp:210-226` direct | `ackkgclock()` |
| Bus error | IP6 | — | `buserror_intr` |
| CP0 Count/Compare | IP7 | — | `r4kcount_intr` |

**Assumption status:**
- PIT Timer 0 fires at 100 Hz: **VERIFIED** (bare-metal benchmark)
- INT3 cascade per MAME `ioc2.cpp:268-284`: **VERIFIED** (kernel boots past idle)
- PIT timers bypass INT3 entirely: **VERIFIED** (MAME cross-ref)

---

## 5. Serial (Z85C30 SCC)

### RR0/RR1 Register Pointer

The Z85C30 uses an indirect register access model. Writing to the command
register sets a pointer; the next read returns the selected register.

**Bug:** The PROM writes `1` to select RR1, then reads status. Our handler
always returned RR0 (`0x04`, TX buffer empty). RR0 bit 0 = 0 (no RX data),
so the PROM's "All Sent" check loop never exited.

**Fix:** Track the register pointer per port. RR0 returns TX buffer empty
(bit 2). RR1 returns All Sent (bit 0 = TX complete/idle).

---

## 6. MODE SELECT max_lba Bug (Upstream QEMU)

In `qemu/hw/scsi/scsi-disk.c`, MODE SELECT changes `blocksize` (e.g.,
2048→512 for CD-ROM) but did not recalculate `max_lba`. The SGI PROM uses
MODE SELECT to switch CD-ROM block size to 512, then issues READ commands
with 512-byte LBA addressing. With stale `max_lba`, reads near the end
of the disc would fail.

**Fix:** After MODE SELECT changes blocksize, recalculate:
`max_lba = (blk_get_geometry() / (blocksize/512)) - 1`

This is a genuine bug in upstream QEMU.
