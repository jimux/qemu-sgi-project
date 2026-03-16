# Plan: IP35 Tezro 4-CPU (IP59) Documentation Run and Implementation Plan

## Context

Pivoting from IP55 (Origin 200/IP27) to IP35 Tezro. The user wants SMP and high RAM
capacity. Goal: **IRIX 6.5 single-user mode via serial console only**, no graphics,
no audio, no keyboard/mouse. Target: **Tezro 4-CPU (IP59_4CPU)** configuration.

**Platform clarification**: "Tezro" and "Origin 3000" are distinct:
- **Origin 3000 (IP33)**: Uses SNAC/"Beast" ASIC, no PROM available, different address space
- **Fuel (IP35)**: Single PIMM (2 CPUs max), XXBow crossbar (part 0xd000)
- **Tezro (IP35)**: Two PIMMs (4 CPUs max), PIC crossbar (part 0xd100, integrates PCI-X)

We target **Tezro 4-CPU** specifically: same Bedrock ASIC as Fuel but with both PI blocks
active, PIC instead of XXBow, and up to 16 GB RAM. The PROM (`ip35prom.img`) supports
all IP35 variants — no different PROM is needed for Tezro.

**Key advantage over Fuel for QEMU**: PIC integrates the PCI-X bridges on-chip, so no
separate XBridge ASIC implementation is needed. PIC = crossbar + PCI host in one device.

---

## Critical Discovery: IP35 Uses SN0-Compatible Addressing

**From ip35prom.img binary analysis** (PROM disassembly at 0xBFC00400):
```
lui   k0, 0x9200
dsll  k0, k0, 16
ori   k0, k0, 0x100
dsll  k0, k0, 16     →  k0 = 0x9200000001000000 (Bedrock IALIAS)
ld    k1, 0x20(k0)   →  READ PI_CPU_NUM at physical 0x01000020
```

The IP35 PROM uses **the same IO_BASE (0x9200000000000000) as IP27**. Bedrock's
physical IALIAS is at **0x01000000**, identical to Hub on IP27. The SN1/ addrs.h in
the IRIX source (IO_BASE = 0x9000100000000000) is for a different "Beast" variant
(Origin 3000/IP33), **not Fuel/Tezro (IP35/Bedrock)**.

All critical Bedrock register offsets match the SNAC/Hub offsets exactly — confirmed
by both binary and snacpiregs.h / snacmdregs.h / snacniregs.h headers in SN1/:
- PI_CPU_NUM = 0x000020, MD_MEMORY_CONFIG = 0x200018, II_ILCSR = 0x400128, NI_STATUS_REV_ID = 0x000000

---

## Available Resources

| Resource | Path | Status |
|----------|------|--------|
| IP35 spec overview | `gathered_documentation/IP35_fuel_tezro_spec_overview.md` | Complete, excellent |
| ip35prom.img | `PROM_library/bins/cpu/ip35/ip35prom.img` | 1.4 MB, JFKSWCSM |
| Fuel flash BINs (×3) | `PROM_library/Fuel_*.BIN` | Raw AM29LV160DT dumps; front_2 = main PROM |
| IRIX SN1 PI headers | `irix/kern/sys/SN/SN1/snacpiregs.h` | Offsets match Bedrock |
| IRIX SN1 MD headers | `irix/kern/sys/SN/SN1/snacmdregs.h` | MD_MEMORY_CONFIG confirmed at 0x200018 |
| IRIX SN1 NI headers | `irix/kern/sys/SN/SN1/snacniregs.h` | NI_SCRATCH_REG0/1 confirmed same offsets |
| IRIX SN1 II headers | `irix/kern/sys/SN/SN1/snacioregs.h` | II_ILCSR = 0x400128, same as Hub IIO |
| IOC3 header | `irix/kern/sys/PCI/ioc3.h` | Same chip as IP27; different UART offset |
| Xbow header | `irix/kern/sys/xtalk/xbow.h` | Same XIO fabric; part num differs |
| Origin 3000 Arch PDF | `gathered_documentation/octane origin/Origin 3000 Architecture 108-0240-002.pdf` | **NOT READABLE** (needs poppler) |
| IP27 implementation notes | `progress_notes/origin200/*.md` | Register maps largely reusable |
| QEMU R14000 CPU | (missing) | Not in QEMU; must add |
| OpenBSD SGI source | (missing) | Not in repo; valuable reference |

---

## PROM Analysis: ip35prom.img

| Field | Value |
|-------|-------|
| Container | JFKSWCSM (format byte 0xaa = SN1) |
| Version | 6.170, Aug 6 2003 |
| Code offset | 0x1000 |
| Physical load | 0x1FC00000 (same as all MIPS PROMs) |
| Reset entry | **0xBFC00400** (not 0x800 like IP27!) |
| Code size | 1,473,464 bytes (1.4 MB) |
| CPUs supported | R10K, R12K, R14K, R16K, R18K |
| Build flags | -DIP35 -DSN1 -DSN -DMP -DNUMA_BASE -mips4 -64 |
| Embedded IO7prom | YES — second JFKSWCSM container at offset 0x87ac0 |

The embedded `io7prom` makes IP35 self-contained; unlike IP27 which loaded IO6prom
from Bridge flash, the IP35 PROM carries the I/O board PROM internally.

---

## Key Differences from IP27 (Hub → Bedrock/Tezro)

| Feature | IP27 / Hub | IP35 Tezro / Bedrock |
|---------|-----------|----------------|
| Boot entry | 0xBFC00800 | **0xBFC00400** |
| CPUs/node | 2 (1 PI block) | **4 (2 PI blocks: PI_0, PI_1, both active)** |
| Crossbar | Xbow (part 0x0000) | **PIC 0xd100 (integrates 2 PCI-X buses)** |
| BaseIO widget | Widget 8 | **Widget 15 (0xF)** |
| BaseIO phys | 0x08000000 | **0x0F000000** |
| IOC3 UART A | BAR0 + 0x178 | **BAR0 + 0x20178** (with XOR-3 swizzle) |
| UART clock | 7.3728 MHz, div=48 | ~22 MHz, div≈143 (0x8F) |
| NI_STATUS rev | Hub rev values | **Bedrock rev = 2** |
| Two-stage PROM | IP27prom + IO6prom (Bridge flash) | Integrated; io7prom embedded |
| ARCS | ARCS (32-bit structs) | **ARCS64** (64-bit structs) |
| KLCONFIG | Built by IO6prom after XIO discovery | Built by ip35prom during boot |
| L1 controller | Hub UART (I2C/PCF8584) | Separate embedded controller (ELSC) |
| Max RAM | 8 GB (Hub: 8 banks × 1 GB) | **16 GB (Tezro: 8 banks × 2 GB DDR)** |
| KLCONFIG brd_name | "IP27" | **"IP59_4CPU"** (Tezro 4-CPU) |
| PCI bridge | External Bridge ASIC (XBridge) | **Integrated in PIC — no separate ASIC** |

---

## Critical Gaps (Missing Information)

### Gap 1: Origin 3000 Architecture PDF — BLOCKED
`gathered_documentation/octane origin/Origin 3000 Architecture 108-0240-002.pdf`
Cannot be read without `poppler` (`brew install poppler`). This document has block
diagrams of Bedrock internals and the physical address derivation.

**Workaround**: The PROM binary confirms register addresses. IP27 documentation
is highly applicable since Bedrock uses the same IO_BASE and IALIAS addressing.

### Gap 2: Bedrock NI_STATUS_REV_ID format
What bit field encodes "revision 2"? For Hub (IP27), NI_STATUS_REV_ID has NASID,
link status, and hub revision. For Bedrock, revision must return 2.

**Source needed**: Linux IA-64 SN code (`arch/ia64/include/asm/sn/shubio.h` from
git commit before c6bacd5010ec was removed) or OpenBSD `sys/arch/sgi/sgi/ip35.c`.

### Gap 3: L1 Controller (ELSC) communication
IP35 PROM communicates with an L1 embedded controller (ELSC). Boot log shows
`"C-L1> bedrock ppp"` commands. If PROM hangs waiting for L1 ACK, we need an
ELSC stub. If it times out gracefully, we may not need it.

**Action**: Binary analysis of ip35prom.img to find L1 communication code and
determine if it's blocking or timeout-based. This is the highest-priority unknown.

### Gap 4: KLCONFIG memory address for IP35
Where in physical memory does ip35prom write the KLCONFIG structure? For IP27
we had IP27PROM_PCFG at 0x01B00000. IP35 uses a different address.

**Source**: `irix/kern/sys/SN/klconfig.h`, or SN1 addrs.h continuation.

### Gap 5: ARCS64 structure layout
IP35 uses 64-bit ARCS structures (ARCS64). Structure alignment differs from
32-bit ARCS used on IP27/O2. GXemul needed trial-and-error to get correct offsets.

**Source**: GXemul's `arcbios.c` for reference; kernel's `arcs/types.h`.

### Gap 6: R14000/R16000 CPU model
Not in QEMU. Must add to `target/mips/cpu-defs.c.inc`:
- R14000: PRId = 0x00230000 (needs verification)
- R16000: PRId = 0x00280000 (needs verification — some sources say 0x00250000)
- 64-entry TLB, MIPS IV ISA (same as R10000)

---

## What IS Known (No Gaps)

- **Physical address map**: Same as IP27. IALIAS at 0x01000000. Widget 15 at 0x0F000000.
- **PI register offsets**: PI_CPU_NUM=0x20, PI_CPU_PRESENT_A=0x40, PI_RT_COUNT=0x030100 — identical to Hub.
- **MD_MEMORY_CONFIG**: At 0x200018 — identical to Hub. Same encoding expected.
- **II_ILCSR**: At 0x400128 — identical to Hub. Must return link-up bit.
- **NI_STATUS_REV_ID**: At NI base + 0x000000 — identical to Hub.
- **Xbow link_status bit 31**: LINK_ALIVE bit — same as IP27.
- **Widget 15 at 0x0F000000**: Bridge/PIC with PCI config at +0x20000.
- **IOC3 PCI identity**: vendor 0x10A9, device 0x0003 — same chip.
- **IOC3 UART A**: BAR0 + 0x20178 (byte-swizzled XOR 3 for BE MIPS).
- **PROM loads at 0x1FC00000**: Standard MIPS reset vector — same as all MIPS.
- **XXBow part number**: 0xd000 for Fuel, 0xd100 for Tezro.
- **KLCONFIG magic**: 0xbeedbabe.
- **Boot entry at 0xBFC00400**: Confirmed from binary — first real code.

---

## Documentation Run: Files to Produce

Create `progress_notes/ip35/` with:

1. **`architecture_overview.md`** — IP35 vs IP27 comparison, Bedrock sections, topology
2. **`bedrock_asic.md`** — PI_0/PI_1 register maps, MD encoding, II/NI details, NI rev
3. **`xxbow_bridge.md`** — XXBow vs PIC identification, widget 15 layout, IOC3 discovery
4. **`ioc3_ip35.md`** — UART at 0x20178, byte-swizzle, baud init, SIO_CR sequence
5. **`physical_address_map.md`** — Full address map (extends IP27 version)
6. **`prom_boot_sequence.md`** — ip35prom boot trace from binary + L1 controller analysis
7. **`klconfig.md`** — KLCONFIG structure, memory address, required board entries for IRIX
8. **`arcs64.md`** — ARCS64 function vector, 64-bit struct layout, SPB address
9. **`l1_controller.md`** — ELSC interface, whether PROM blocks on L1

Binary cross-checks using MCP prom analysis against ip35prom.img:
- Confirm PI_CPU_NUM is first hardware register read (binary confirmed reset at 0x400)
- Find and analyze L1 communication code — blocking or timeout?
- Find KLCONFIG write address
- Find IOC3 UART initialization sequence
- Find ip35config structure location in PROM

---

## Implementation Plan (Phased)

### Phase 1: CPU + Machine skeleton
- Add R14000 CPU model to `target/mips/cpu-defs.c.inc` (start from R10000, change PRId/Config)
- Create `qemu/hw/mips/sgi_ip35.c` machine definition
  - Load ip35prom.img at physical 0x1FC00000
  - Map DRAM from 0x00000000
  - Instantiate Bedrock, XXBow, Bridge, IOC3 devices

### Phase 2: Bedrock ASIC (`qemu/hw/misc/sgi_bedrock.c`)
Reuse IP27 Hub progress notes for register offsets (all identical):

| Register | Physical | Required Value |
|----------|----------|----------------|
| PI_CPU_NUM | 0x01000020 | 0 (CPU A) |
| PI_CPU_PRESENT_A | 0x01000040 | 1 |
| PI_CPU_PRESENT_B | 0x01000048 | 0 or 1 |
| PI_CPU_ENABLE_A | 0x01000050 | 1 |
| PI_RT_COUNT | 0x01030100 | free-running |
| PI_RT_COMPARE_A | 0x01000108 | compare target |
| MD_MEMORY_CONFIG | 0x01200018 | encode from -m |
| II_ILCSR | 0x01400128 | 0x00002000 (link up) |
| NI_STATUS_REV_ID | 0x01600000 | rev=2, NASID=0, link-down |

**Tezro**: Both PI_0 and PI_1 active — 4 CPUs total.
- PI_0: CPUs 0 and 1 (CPU_PRESENT_A/B = 1, CPU_ENABLE_A/B = 1)
- PI_1: CPUs 2 and 3 (same register set at PI_1 base offset)
- PI_1 base = PI_0 base + some offset (to determine from binary analysis)

### Phase 3: PIC at widget 15 (`sgi_pic.c` — replaces separate crossbar + bridge)
**Tezro advantage**: PIC integrates both the XIO crossbar and PCI-X host bridges:
- PIC at widget 0 (crossbar role, dispatched via Bedrock II):
  - Widget ID = 0x1d100000 (part **0xd100**, rev 1) for Tezro
  - Port 15 link_status = 0x80000000 (alive); all others = 0
- PIC at widget 15 (PCI host role, physical 0x0F000000):
  - Widget ID = PIC-as-bridge (part 0xd100 in bridge mode)
  - Integrated PCI-X bus 0: IOC3 at device 0, QLogic SCSI at device 1
  - PCI config at 0x0F020000 (same +0x20000 offset as Bridge)
- **No separate XBridge ASIC needed** — simpler than Fuel's XXBow + XBridge

### Phase 4: IOC3 serial console
Key difference from IP27:
- UART A base: BAR0 + 0x20178 (not BAR0 + 0x178)
- BE MIPS byte access XOR 3 (`__swizzle_addr_b`)
- Baud divisor: 0x8F (143) for 9600 baud at ~22 MHz

### Phase 5: KLCONFIG + ARCS64
Pre-populate in machine init (avoid requiring PROM to succeed at full HW discovery):
- KLCONFIG header at known IP35 address with magic 0xbeedbabe
- lboard_t for CPU board (**brd_name="IP59_4CPU"** for Tezro 4-CPU)
  - 4 klcpu_t components (R16000 PRId, 400 MHz, 2 MB L2 cache)
- lboard_t for I/O brick (KLTYPE_IXBRICK for Tezro IXbrick)
  - klbridge_t component for PIC
  - klioc3_t component for IOC3
- ARCS64 SPB at 0x80001000 with 64-bit struct layout
- Memory: report up to 16 GB (8 banks × 2 GB) via both KLCONFIG and ARCS64 descriptors

### Phase 6: QLogic QL12160 SCSI + IRIX boot
- PCI device on Bridge bus (PCI ID 0x1077:0x1016)
- SGI DVH disk label support
- IRIX kernel load via sash64

---

## Key Risks

1. **L1 controller blocking**: If ip35prom hangs without ELSC responses, need a UART
   stub. Binary analysis will determine severity. Highest priority unknown.

2. **ARCS64 structure misalignment**: 64-bit ARCS structs have subtle alignment
   differences. GXemul experienced this pain; expect trial-and-error.

3. **TLB accuracy**: Consistent failure point across all SGI emulation projects.
   R14000 TLB must be precise. MAME's note about LL sign-extension also applies.

4. **KLCONFIG address unknown**: If PROM writes KLCONFIG and we must match exactly
   what IRIX kernel expects, need the exact physical address. Can binary-analyze.

---

## Immediate Next Steps (Documentation Run First)

1. **`brew install poppler`** — enables reading Origin 3000 Architecture PDF.

2. **Binary analysis of ip35prom.img**:
   - Find L1 communication code (is it blocking?)
   - Find KLCONFIG write address
   - Confirm IOC3 UART init sequence at offset 0x20178
   - Find ip35config structure

3. **Fetch Linux IA-64 SN source**: `arch/ia64/include/asm/sn/shubio.h` (from Linux
   git before commit c6bacd5010ec) for Bedrock NI_STATUS_REV_ID bit layout.

4. **Target variant**: Fuel (XXBow, simpler) is recommended as first target.
   Tezro (PIC) follows naturally — same PROM, same IRIX path, different widget ID.

---

## File Additions

| File | Type | Notes |
|------|------|-------|
| `qemu/hw/mips/sgi_ip35.c` | New machine | IP35 Tezro 4-CPU machine (`sgi-tezro`) |
| `qemu/hw/misc/sgi_bedrock.c` | New device | Bedrock ASIC (PI_0/PI_1/MD/II/NI) |
| `qemu/include/hw/misc/sgi_bedrock.h` | New header | |
| `qemu/hw/misc/sgi_pic.c` | New device | PIC crossbar + integrated PCI-X host |
| `qemu/include/hw/misc/sgi_pic.h` | New header | |
| `qemu/hw/misc/sgi_ioc3.c` | New device | IOC3 (UART A/B + stubs) |
| `target/mips/cpu-defs.c.inc` | Modified | Add R14000 / R16000 |
| `qemu/hw/mips/meson.build` | Modified | Add ip35/tezro |
| `qemu/hw/mips/Kconfig` | Modified | Add ip35/tezro |
| `qemu/hw/misc/meson.build` | Modified | Add bedrock, pic, ioc3 |
| `progress_notes/ip35/*.md` | New docs | 9-file documentation run |
