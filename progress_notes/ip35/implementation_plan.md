# IP35 Tezro Implementation Plan

## Goals
1. IRIX 6.5 single-user mode via serial console (4 CPUs, 8GB RAM)
2. Eventually full desktop (graphics, audio, networking, input)

## Critical Unknowns to Resolve

### 1. L1 Controller (ELSC) - COMPLETED
PROM has timeout handling for ELSC communication. Does not block indefinitely.

### 2. KLCONFIG Memory Address - PENDING
Where in physical memory does ip35prom write KLCONFIG structure?

### 3. ARCS64 Structure Layout - PENDING
64-bit ARCS structures differ from 32-bit ARCS used on IP27/O2.

### 4. Bedrock Register Details - PENDING
Confirm PI_1 base offset and any differences from IP27 Hub.

## Implementation Phases

### Phase 1: CPU + Machine Skeleton
- Add R14000/R16000 CPU to `target/mips/cpu-defs.c.inc`
- Create `qemu/hw/mips/sgi_ip35.c` machine definition
- Update `qemu/hw/mips/Kconfig`, `meson.build`

### Phase 2: Bedrock ASIC
- Create `qemu/hw/misc/sgi_bedrock.c` with PI_0/PI_1, MD, II, NI
- Register map (all offsets from IP27 Hub but Bedrock-specific):
  - PI_0: 0x01000000 (CPU A/B slices)
  - PI_1: ? (need to determine offset)
  - MD: 0x01200000
  - II: 0x01400000  
  - NI: 0x01600000

### Phase 3: PIC Crossbar + PCI Bridge
- Create `qemu/hw/misc/sgi_pic.c`
- PIC at widget 15 (0x0F000000)
- Part number: 0xd100 for Tezro
- Integrated PCI-X bus 0

### Phase 4: IOC3 Serial Console
- Create/extend `qemu/hw/misc/sgi_ioc3.c`
- UART A at BAR0 + 0x20178 (byte-swizzle XOR 3)
- Baud divisor: 0x8F for 9600 baud at ~22 MHz

### Phase 5: KLCONFIG + ARCS64
- Pre-populate in machine init
- KLCONFIG magic: 0xbeedbabe
- Board name: "IP59_4CPU" for Tezro 4-CPU
- ARCS64 at 0x80001000

### Phase 6: Boot Storage
- QLogic QL12160 SCSI on PCI bus
- SGI DVH disk label support
- IRIX kernel via sash64

## Current Status

- ✅ IP35 PROM binary analyzed
- ✅ L1 controller timeout handling confirmed
- ⏳ KLCONFIG address pending disassembly
- ⏳ Bedrock register details pending verification

## Next Steps

1. Disassemble IP35 PROM to find:
   - UART initialization code
   - KLCONFIG write location
   - Bedrock register access patterns

2. Check IRIX SN1 headers for:
   - KLCONFIG memory address
   - Bedrock register offsets (snacpiregs.h, snacmdregs.h)
   - ARCS64 structure definitions

3. Start Bedrock device skeleton with PI_0/PI_1 stubs
