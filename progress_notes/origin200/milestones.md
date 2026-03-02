# IP55 Implementation Milestones and Running Notes

## Milestone 1: IP27prom reaches "Starting PROM Boot process"

**Goal**: IP27prom POSTs successfully and prints its first message via IOC3 UART A.

### Prerequisites (Hardware)

- [ ] Hub ASIC MMIO at 0x01000000–0x017FFFFF (PI/MD/IIO/NI sections)
- [ ] IP27prom ROM at 0x1FC00000
- [ ] io6prom pre-loaded at 0x01C00000 (or Bridge flash at 0x08400000)
- [ ] Xbow minimal: widget ID responds correctly (via Hub IIO dispatch)
- [ ] Bridge at 0x08000000: widget ID + PCI config space
- [ ] IOC3 PCI device at Bridge PCI bus slot 0
- [ ] IOC3 UART A at `IOC3_BAR0 + 0x178` (16550 compatible)

### Register Checklist

| Register | Physical | Required Value | Status |
|----------|----------|----------------|--------|
| IP27prom binary | 0x1FC00000 | `ip27prom.img` | [ ] |
| PI_CPU_NUM | 0x01000020 | 0 | [ ] |
| PI_CPU_PRESENT_A | 0x01000040 | 1 | [ ] |
| PI_CPU_PRESENT_B | 0x01000048 | 0 | [ ] |
| PI_CPU_ENABLE_A | 0x01000050 | 1 | [ ] |
| MD_MEMORY_CONFIG | 0x01200018 | from `-m` | [ ] |
| MD_UREG0_0 reads | 0x01220000 | 0x00 | [ ] |
| IIO_WID | 0x01400000 | Hub widget ID | [ ] |
| IIO_ILCSR | 0x01400128 | 0x00002000 | [ ] |
| NI_STATUS_REV_ID | 0x01600000 | link-down + NASID=0 | [ ] |
| Xbow w_id | (Hub IIO dispatch) | 0x1000_006d | [ ] |
| xb_link[0].link_status | (Hub IIO dispatch) | alive bit set | [ ] |
| Bridge w_id at 0x08000004 | 0x08000004 | 0x4c002_06d | [ ] |
| IOC3 PCI vendor/device | 0x08020000 | 0x0003_10A9 | [ ] |
| IOC3 UART A LSR | 0x08100178+0x14 | 0x60 (TX empty) | [ ] |

### Expected Console Output

After successful Milestone 1:
```
Starting PROM Boot process
...
SGI IP27 PROM
...
System Maintenance Menu (or similar)
```

---

## Milestone 2: IO6prom loads and runs

**Goal**: IP27prom loads IO6prom from Bridge flash (or pre-loaded at 0x01C00000),
IO6prom runs and prints version banner.

### Prerequisites

- All Milestone 1 items
- io6prom.img at Bridge flash (0x08400000) or pre-loaded at 0x01C00000
- IO6prom entry point resolves correctly

### Expected Console Output

```
SGI Version X.XX  Jan 1, 1999 10:00:00
Origin 200 IP27
...
```

---

## Milestone 3: IRIX boots on IP55

**Goal**: IRIX 6.5 kernel loads via SCSI and boots to multi-user shell.

### Prerequisites

- All Milestone 2 items
- QLogic 1040B PCI SCSI controller (or equivalent) behind Bridge
- IRIX 6.5 disk image with IP27 kernel
- Hub PI_RT_COUNT timer at 1250 Hz for IRIX scheduling
- Correct ARCS memory descriptors from IO6prom

### Expected Console Output

```
Copyright (c) 1988-1999 Silicon Graphics, Inc.  All Rights Reserved.
...
IRIX Release 6.5 IP27 Version 02131234 System V
...
Hostname: origin200
...
IRIX is ready.
```

---

## Verification Checklist (Documentation Run)

Answers to the plan's verification questions:

### Q: What is the QEMU physical address of every register the IP27prom reads during POST?

See `prom_boot_sequence.md` — "Register Access Sequence Summary" table.
Key registers and their physical addresses documented there.

### Q: What value must each register return for PROM to proceed?

See `prom_boot_sequence.md` — "Known Branch Points" section and the register
summary table. Critical:
- IIO_ILCSR = 0x00002000 (link up)
- NI_STATUS_REV_ID = link-down (bit 29 = 0)
- MD_MEMORY_CONFIG = non-zero (at least one bank populated)

### Q: How does MD_MEMORY_CONFIG encode 512 MB / 1 GB / 4 GB / 8 GB?

See `memory_sizing.md` — "Example Encodings" table:
- 512 MB: `0x00000007` (bank 0 = MD_SIZE_512MB = 7)
- 1 GB: `0x00000008` (bank 0 = MD_SIZE_1GB = 8)  OR `0x0000003f` (2×512MB)
- 4 GB: `0x00011088` (4×1GB) OR `0x00ffffff` (8×512MB)
- 8 GB: `0x01249249` (8×1GB)

### Q: What does NI_SCRATCH_REG1 bit 50 do?

See `hub_asic.md` — NI section. `ADVERT_SN00_MASK = 1ULL << 50` is written
by the PROM to advertise that this node is SN00 (Origin 200) vs SN0 (Origin
2000) to other nodes during multi-node discovery. For single-node, it is a
R/W scratch register initialised to 0; the PROM writes ADVERT_SN00_MASK after
reading `ip27config.mach_type = 1`.

### Q: What is the Xbow widget ID and which registers does PROM read to verify the XIO link?

See `xbow_and_bridge.md`:
- Xbow widget ID: part_num=0x0000, at Xbow SWIN base + 0x04
- PROM reads `xb_link[port].link_status` for each port (8-15)
- **Alive bit = bit 31 (MSB)** of link_status — confirmed from `xbow.h` bitfield:
  `alive:1` is first field in big-endian struct → bit 31. QEMU returns `0x80000000`.
- Then reads widget ID at the discovered widget's SWIN base + 0x04

### Q: What is the Bridge widget ID and where is IO6prom stored?

See `xbow_and_bridge.md`:
- Bridge widget ID: part_num=0xc002, at Bridge SWIN (0x08000000) + 0x04
- IO6prom in Bridge flash: physical 0x08400000 (Bridge window + 0x400000)
- **Bridge PCI config base**: `BRIDGE_CONFIG_BASE = 0x20000` — confirmed from `bridge.h`
  → Physical 0x08020000 for slot 0 (IOC3). ✓

### Q: What is the IOC3 UART register layout and which UART does IP27prom use?

See `ioc3.md`:
- UART A is used (not B) for console output
- UART A base: `IOC3_BAR0 + 0x178` (in standalone/PROM mode)
- 16550 compatible, 4-byte-stride registers
- Baud: 9600, divisor 48 (clock = 7.3728 MHz)

### Q: How are two CPUs woken?

See `multi_cpu_and_64bit.md`:
- CPU B enabled by writing 1 to `PI_CPU_ENABLE_B` (0x01000058)
- CPU B detects `PI_CPU_NUM = 1` and proceeds through slave path
- Master (CPU A) posts launch entry in KLD_LAUNCH mailbox
- For Milestone 1: single CPU only (`PI_CPU_PRESENT_B = 0`)

### Q: What Hub PI_RT_COUNT frequency does IRIX expect, and how does it map to IP8?

See `multi_cpu_and_64bit.md`:
- Frequency: 1250 Hz (800ns period), from `IP27_RTC_FREQ` in ip27config.h
- Interrupt line: IP8 (MIPS CP0 SR_IBIT8 = external interrupt 6)
- Enable: `PI_RT_EN_A = 1`
- Trigger: when `PI_RT_COUNT >= PI_RT_COMPARE_A`
- IRIX re-arms compare after each tick

---

## File Tree

```
progress_notes/origin200/
├── architecture_overview.md    — What IP55 is, SN00 topology, two-stage PROM
├── hub_asic.md                 — PI/MD/IIO/NI register maps, boot-critical regs
├── xbow_and_bridge.md          — Xbow layout, Bridge registers, IO6prom flash
├── ioc3.md                     — IOC3 register map, UART init sequence
├── physical_address_map.md     — Definitive QEMU physical address layout
├── prom_boot_sequence.md       — Step-by-step IP27prom boot trace
├── multi_cpu_and_64bit.md      — SMP, RT counter, N64 kernel ABI
├── memory_sizing.md            — MD_MEMORY_CONFIG encoding, IRIX szmem
└── milestones.md               — This file: milestone checklist and Q&A
```

## Binary Cross-Check Results (ip27prom.img)

All cross-checks have been run against `PROM_library/bins/cpu/ip27/ip27prom.img`
(JFKSWCSM container, version 6.150, Sep 29 2003, 912760 bytes, code at offset 0x1000).

- [x] **Reset vector**: Exception table at 0xBFC00000; boot entry `J 0xBFC00800`
      First boot instruction at 0xBFC00800: clears $k0/$k1, sets SR_KX=1 (bit 7)
- [x] **PI_CPU_NUM first read confirmed**: Binary shows `ld $k1, 0x20($k0)` where
      $k0 = 0x9200000001000000 (Hub PI SWIN base). PI_CPU_NUM offset 0x20 confirmed.
- [x] **PI_RT_COMPARE_A write = 0x5e (PLED_LOCALARB)**: Confirmed in binary immediately
      after PI_CPU_NUM read. CPU-specific write (A or B) via $k1 = CPU_NUM × 8 offset.
- [x] **ip27config at PROM offset 0x60**: Found at file offset 0x1060 (code byte 0x60).
      `mach_type = 1` (SN00) confirmed. Accessible at LBOOT_BASE+0x60 AND 0xBFC00060.
      QEMU must map PROM at both physical 0x1FC00000 AND 0x10000000.
- [x] **Xbow link_status bit 31 = alive bit**: Confirmed from `xbow.h` bitfield:
      `alive:1` is the first field → bit 31 (MSB) in big-endian. QEMU must set bit 31
      for port 8 (Bridge). Return value: `0x80000000` or any value with bit 31 set.
- [x] **Bridge PCI config at BRIDGE_CONFIG_BASE = 0x20000**: Confirmed from `bridge.h`.
      `#define BRIDGE_CONFIG_BASE 0x20000`. Physical for slot 0: 0x08020000. ✓
- [x] **845 functions** detected in ip27prom.img; "Starting PROM Boot process" string
      at PROM vaddr 0xBFC5EE90; IOC3 UART diagnostic strings confirmed.
- [ ] `ghidra_decompile ioc3uart_init` — full UART init sequence decompilation
      (lower priority — source code in `ioc3uart.c` already detailed)
