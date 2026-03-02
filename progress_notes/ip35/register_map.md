# IP35 Bedrock Register Map

Based on IRIX 6.5.7m SN1 headers.

## Physical Address Layout (NASID 0, M-mode)

SN1 uses IO_BASE at 0x9000100000000000 (uncached XKPHYS).
Bedrock registers are accessed via IALIAS at 0x01000000 (relative to IO_BASE).

```
0x01000000  Bedrock PI_0 section (0x400000 bytes)
0x01200000  Bedrock MD section (0x200000 bytes)  
0x01400000  Bedrock II section (0x200000 bytes)
0x01600000  Bedrock NI section (0x200000 bytes)
```

## PI Section (Processor Interface)

### PI_0 Base: 0x01000000
### PI_1 Base: ? (need to determine offset - likely 0x200000 or similar)

| Register | Offset (from PI base) | Function |
|----------|----------------------|----------|
| PI_CPU_NUM | 0x000020 | CPU slice number (0=CPU A, 1=CPU B) |
| PI_CALAIS_SIZE | 0x000028 | Cached alias size |
| PI_CPU_PRESENT_A | 0x000040 | CPU A present (read-only) |
| PI_CPU_PRESENT_B | 0x000048 | CPU B present (read-only) |
| PI_CPU_ENABLE_A | 0x000050 | CPU A enabled (R/W) |
| PI_CPU_ENABLE_B | 0x000058 | CPU B enabled (R/W) |
| PI_INT_PEND0 | 0x000098 | Interrupt pending group 0 |
| PI_INT_PEND1 | 0x0000A0 | Interrupt pending group 1 |
| PI_INT_MASK0_A | 0x0000A8 | Int mask 0 for CPU A |
| PI_INT_MASK1_A | 0x0000B0 | Int mask 1 for CPU A |
| PI_INT_MASK0_B | 0x0000B8 | Int mask 0 for CPU B |
| PI_INT_MASK1_B | 0x0000C0 | Int mask 1 for CPU B |
| PI_CC_PEND_SET_A | 0x0000C8 | CC interrupt pending for CPU A |
| PI_CC_PEND_SET_B | 0x0000D0 | CC interrupt pending for CPU B |
| PI_RT_COUNT | 0x030100 | Real-time counter (free-running) |
| PI_RT_COMPARE_A | 0x000108 | RT compare for CPU A |
| PI_RT_COMPARE_B | 0x000110 | RT compare for CPU B |

## MD Section (Memory/Directory)

**Base: 0x01200000**

| Register | Offset | Function |
|----------|--------|----------|
| MD_MEMORY_CONFIG | 0x200018 | Memory bank size configuration |

## II Section (I/O Interface)

**Base: 0x01400000**

| Register | Offset | Function |
|----------|--------|----------|
| II_WID | 0x400000 | Widget identification |
| II_ILCSR | 0x400128 | LLP control/status (link up = 0x2000) |

## NI Section (Network Interface)

**Base: 0x01600000**

| Register | Offset | Function |
|----------|--------|----------|
| NI_STATUS_REV_ID | 0x000000 | Hub revision, NASID, link status |
| NI_SCRATCH_REG0 | 0x000100 | Scratch register 0 |
| NI_SCRATCH_REG1 | 0x000108 | Scratch register 1 |

## KLCONFIG Address

```
KLDIR_OFFSET = 0x2000
KLDIR_ADDR(nasid) = TO_NODE_UNCAC(nasid, 0x2000)
KLCONFIG_OFFSET(nasid) = KLD_KLCONFIG(nasid)->offset
```

For IP35 Prom, KLCONFIG is written at a known NASID-relative offset.

## Key Differences from IP27 (SN0/Hub)

1. **PI_1 block**: Bedrock has two PI blocks (PI_0 and PI_1), each with identical register sets
2. **NI_STATUS_REV_ID**: Must return revision 2 for Bedrock (vs Hub rev values)
3. **Max CPUs**: 4 CPUs (2 per PI block) vs IP27's 2 CPUs
4. **Max RAM**: 16 GB (Tezro) vs IP27's 8 GB

## Crossbar/PCI Configuration

**PIC (Tezro)**: Part number 0xd100
**XXBow (Fuel)**: Part number 0xd000

Both use widget 15 at physical address 0x0F000000 for Bridge/PIC.
