# IP35 Tezro Implementation Status

## Overview
Plan mode implementation for SGI IP35 (Tezro 4-CPU) QEMU emulation targeting IRIX 6.5 single-user mode.

## Completed Analysis

### 1. PROM Binary Analysis
- ✅ ip35prom.img analyzed (1.4 MB, JFKSWCSM format)
- ✅ Entry point confirmed: 0xBFC00400
- ✅ L1 controller has timeout handling (not blocking)
- ✅ Uses IOC3 UART for console
- ✅ Platform: SN1/Bedrock (not SN0/Hub)

### 2. Hardware Documentation
- ✅ Bedrock register map extracted from IRIX SN1 headers
- ✅ PI_0/PI_1, MD, II, NI register offsets documented
- ✅ KLCONFIG structure and address defined in klconfig.h

### 3. Address Maps
- IO_BASE: 0x9000100000000000 (uncached XKPHYS)
- IALIAS: 0x01000000 (relative to IO_BASE)
- Bedrock registers at 0x01000000-0x017FFFFF
- Widget 15 (PIC/XXBow) at 0x0F000000

### 4. Key Unknowns Identified
1. PI_1 base offset (PI_0 + ?)
2. KLCONFIG exact write address in PROM
3. ARCS64 structure layout differences from 32-bit ARCS

## Documentation Files Created

```
progress_notes/ip35/
├── binary_analysis_summary.md    - PROM analysis results
├── implementation_plan.md        - Implementation phases
├── register_map.md               - Bedrock register offsets
└── STATUS.md                     - This file
```

## Next Steps

### Phase 1: Complete Documentation
1. Disassemble IP35 PROM code paths for:
   - UART init sequence (confirm 0x20178 offset)
   - KLCONFIG write address
   - Bedrock register access patterns

2. Study existing implementations:
   - QEMU IP27 (sgi_ip27.c)
   - GXemul ARCS64 implementation
   - Linux IA-64 SN code (shubio.h)

### Phase 2: Start Implementation
1. Add R14000/R16000 CPU model to QEMU
2. Create Bedrock device skeleton (sgi_bedrock.c)
3. Implement PIC crossbar + PCI bridge (sgi_pic.c)

## Files to Create

```
qemu/
├── hw/mips/sgi_ip35.c          (new - machine definition)
├── hw/misc/sgi_bedrock.c       (new - Bedrock ASIC)
├── hw/misc/sgi_pic.c           (new - PIC crossbar + PCI host)
└── hw/misc/sgi_ioc3.c          (new -IOC3 serial console)

target/mips/
└── cpu-defs.c.inc              (modified - add R14000/R16000)
```

## Key Hardware Specifications

### IP35 Tezro (4-CPU)
- CPUs: 4 × R14000/R16000 (2 per PI block)
- RAM: Up to 16 GB
- Crossbar: PIC (part 0xd100)
- BaseIO widget: 15 (0x0F000000)
- Revision: Bedrock rev 2

### IP35 Fuel (2-CPU, for reference)
- CPUs: 2 × R14000/R16000 (1 PI block)
- RAM: Up to 4 GB
- Crossbar: XXBow (part 0xd000)
- Same PROM supports both

## Critical Register Values for Boot

| Register | Physical Address | Required Value |
|----------|------------------|----------------|
| PI_CPU_NUM | 0x01000020 | 0 (CPU A) |
| PI_CPU_PRESENT_A | 0x01000040 | 1 |
| PI_CPU_ENABLE_A | 0x01000050 | 1 |
| II_ILCSR | 0x01400128 | 0x2000 (link up) |
| NI_STATUS_REV_ID | 0x01600000 | revision=2 |

## Success Criteria

### Milestone 1: PROM POST
- IP35prom boots to serial console
- All Bedrock registers return valid values

### Milestone 2: ARCS Prompt  
- PROM reaches maintenance menu
- KLCONFIG built correctly

### Milestone 3: IRIX Single-User
- Kernel loads and boots
- Interactive serial shell
- 4 CPUs detected

## References

- `tezro-plan.md` - Original implementation plan
- `gathered_documentation/IP35_fuel_tezro_spec_overview.md` - Hardware spec
- `software_library/irix-657m-source/irix/kern/sys/SN/SN1/*.h` - IRIX headers
- `progress_notes/origin200/*.md` - IP27 implementation notes

---
**Status**: Documentation run complete. Ready to begin implementation.
**Last Updated**: 2026-02-22
