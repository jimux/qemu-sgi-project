# Physical Address Map for IP55 (QEMU, NASID 0, M-mode)

## SN0 Address Space Model

SN0 uses a **NASID-based** physical address space. In M-mode:
- `NODE_SIZE_BITS = 32`: each node gets 4 GB of address space
- `NASID_SHFT = 32`: NASID is encoded in physical address bits [39:32]
- For NASID 0: the entire 4 GB node-local space sits at physical 0x00000000

The MIPS64 CPU in R10000 (XKPHYS) uses 64-bit physical addresses. The upper
bits of the XKPHYS virtual address select the cache coherency domain and are
stripped to form the physical address:

```
UNCAC_BASE  = 0x9600000000000000  (uncached, non-write-coalescing)
CAC_BASE    = 0xa800000000000000  (cached)
HSPEC_BASE  = 0x9000000000000000  (hardware special)
IO_BASE     = 0x9200000000000000  (IO space)
MSPEC_BASE  = 0x9400000000000000  (memory special / hub backdoor)
```

`TO_PHYS(va) = va & 0x00000000FFFFFFFF` (for NASID 0, 32-bit node space)

## QEMU Physical Memory Regions (NASID 0)

| Physical Range | Size | Device | Notes |
|----------------|------|--------|-------|
| 0x00000000 – RAM_TOP | Up to 3 GB | DRAM | Main system memory |
| 0x01000000 – 0x011FFFFF | 2 MB | Hub PI registers | |
| 0x01200000 – 0x013FFFFF | 2 MB | Hub MD registers | |
| 0x01300000 – 0x013FFFFF | 1 MB | IP27prom FLASH_HDR area | Overlaps MD; see note |
| 0x01400000 – 0x015FFFFF | 2 MB | Hub IIO registers | |
| 0x01500000 – 0x015FFFFF | 1 MB | DIAG_BASE | PROM scratch |
| 0x01600000 – 0x016FFFFF | 1 MB | Hub NI registers | |
| 0x01800000 – 0x019FFFFF | 2 MB | ROUTE_BASE / IP27PROM_CORP | PROM scratch |
| 0x01A00000 – 0x01AFFFFF | 1 MB | IP27PROM_BASE (PROM in DRAM) | After decompression |
| 0x01B00000 – 0x01BFFFFF | 1 MB | IP27PROM_PCFG | PROM klconfig |
| 0x01BD0000 – 0x01BDFFFF | 64 KB | IP27PROM_ERRDMP / CONSOLE area | |
| 0x01BE0000 – 0x01BEFFFF | 64 KB | IP27PROM_STACK_A | PROM stack CPU A |
| 0x01BF0000 – 0x01BFFFFF | 64 KB | IP27PROM_STACK_B | PROM stack CPU B |
| **0x01C00000** – 0x01FFFFFF | 4 MB | **IO6PROM_BASE** | IO6prom loaded here |
| 0x02000000 – 0x02FFFFFF | 16 MB | FREEMEM_BASE | IRIX kernel load area |
| 0x08000000 – 0x08FFFFFF | 16 MB | Bridge (widget 8 SWIN) | |
| 0x08000000 – 0x0801FFFF | 128 KB | Bridge local registers | |
| 0x08020000 – 0x0803FFFF | 128 KB | Bridge PCI config space | |
| 0x08100000 – 0x081FFFFF | 1 MB | IOC3 BAR0 (PCI mem) | UART, ETH, etc. |
| 0x08400000 – 0x087FFFFF | 4 MB | Bridge flash (IO6prom storage) | |
| **0x1FC00000** – 0x1FC7FFFF | 512 KB | IP27prom (LBOOT flash) | Reset vector |

*Note: Addresses 0x01000000–0x017FFFFF are used by Hub registers (IALIAS)
but also overlap with what the prom header calls "MISC_PROM_BASE" at
0x01300000. In practice, the PROM loads itself into DRAM first and then the
Hub register region replaces what would otherwise be DRAM. QEMU must NOT
map DRAM over the Hub register region.*

## Address Derivations

### Hub IALIAS (widget 1)

```
NODE_SWIN_BASE(nasid=0, widget=1)
  = NODE_IO_BASE(0) + (1 << SWIN_SIZE_BITS)
  = IO_BASE + 0 + (1 << 24)
  = IO_BASE + 0x01000000

TO_PHYS(IO_BASE + 0x01000000)
  = 0x0000000001000000
  = physical 0x01000000  ✓
```

### Bridge (widget 8)

```
NODE_SWIN_BASE(nasid=0, widget=8)
  = NODE_IO_BASE(0) + (8 << 24)
  = IO_BASE + 0x08000000

TO_PHYS → physical 0x08000000  ✓
```

### IP27prom Reset Vector

The MIPS reset vector is at COMPAT_K1 `0xBFC00000` = physical `0x1FC00000`.
In the Hub's memory map, this physical address falls within the LBOOT range:

```
LBOOT_BASE (physical) = HSPEC 0x10000000 offset = ?
```

In HSPEC space: `HSPEC_BASE = 0x9000000000000000`.
`LBOOT_BASE = HSPEC_BASE + 0x10000000 = 0x9000000010000000`.

The Hub routes HSPEC physical accesses to the boot ROM hardware. The MIPS
compat-K1 address 0xBFC00000 (physical 0x1FC00000) falls within HSPEC physical
range where the Hub maps the flash PROM. In QEMU, this means:

**Map the IP27prom image at physical 0x1FC00000 as a ROM region.**

**Binary analysis confirmed (from ip27prom.img):**

The ip27config structure is embedded in the PROM flash at byte offset 0x60
from the code section start. This means:
- Flash byte 0x60 = ip27config (with `mach_type=1` = SN00 confirmed)
- The Hub makes this same flash byte accessible at TWO virtual addresses:
  1. `LBOOT_BASE + 0x60` = HSPEC 0x9000000010000060 → physical 0x10000060
  2. `0xBFC00060` = COMPAT K1 → physical 0x1FC00060

Both address the same flash byte because the Hub routes the LBOOT flash window
to two different physical address ranges (HSPEC base and COMPAT K1 space).

**QEMU implementation**: Map the ip27prom.img at **both** physical locations:
- 0x1FC00000 — for MIPS reset vector (standard COMPAT K1/K0 access)
- 0x10000000 — for HSPEC LBOOT access (ip27config reads via LBOOT_BASE + 0x60)

Both regions serve the same ROM data. The ip27config at byte +0x60 is then
accessible as physical 0x10000060 (HSPEC) and 0x1FC00060 (COMPAT K1). No
separate "ip27config region" is needed — the full PROM image covers it.

**ip27config field at offset +0x28** from struct start contains `mach_type=1`
(SN00_MACH_TYPE). The PROM checks this early in boot to determine SN00 vs SN0.

### IO6prom Load Address

```c
#define IO6PROM_BASE    PHYS_TO_K0(0x01c00000)   /* physical 0x01C00000 */
#define IO6PROM_SIZE    0x400000                  /* 4 MB max */
```

The IP27prom decompresses io6prom.img from Bridge flash into this DRAM region.
After loading, it jumps to the IO6prom entry point. For QEMU, pre-loading
io6prom.img at physical 0x01C00000 (as initial DRAM contents) is the simplest
approach.

## Xbow Physical Address Conflict

Widget 0 (Xbow) is a special case. The standard SWIN formula would give:
`widget 0 × 16 MB = 0x00000000`, which conflicts with DRAM.

Real SN0 hardware resolves this through the Hub IIO section: the Hub
intercepts accesses in its IO address space (HSPEC/IO virtual addresses)
before they reach physical DRAM. There is no physical conflict because the
Hub's HSPEC IO space is a separate address domain from DRAM.

**QEMU resolution**: Xbow is not directly mapped in the physical DRAM address
space. Instead:
- The Hub IIO MMIO handler (for physical 0x01400000–0x015FFFFF) dispatches
  XIO widget accesses internally
- When the PROM reads a widget ID for widget 0, the Hub IIO handler returns
  the Xbow widget ID value directly
- No separate Xbow MMIO region is registered at physical 0x00000000

This matches real hardware behavior where the Hub translates XIO access
requests through its internal routing rather than direct physical memory access.

## DRAM Layout

DRAM starts at physical 0x00000000. The PROM uses specific DRAM regions
for temporary storage, stacks, and passing data to IO6prom/IRIX:

```
0x00000000  Reset-time exception handlers
0x01000000  Hub registers (NOT DRAM — Hub MMIO)
0x01C00000  IO6prom loaded here
0x02000000  FREEMEM_BASE — IRIX kernel loaded here
...
RAM_TOP     End of physical DRAM
```

QEMU should map DRAM from 0x00000000 to `min(ram_size, 0x00FFFFFF)` (first
region below Hub registers), then continue from 0x01D00000 (after IO6prom)
up to the full ram_size, skipping the Hub register and Bridge regions.

Simpler alternative: map all DRAM from 0x00000000 continuously, then overlay
Hub and Bridge regions as higher-priority MMIO regions. QEMU's memory map
handles MMIO overlay over RAM transparently.

## Summary Table

| Physical | Device | Access |
|----------|--------|--------|
| 0x00000000 | DRAM | R/W |
| 0x01000000 | Hub PI | MMIO |
| 0x01200000 | Hub MD | MMIO |
| 0x01400000 | Hub IIO | MMIO |
| 0x01600000 | Hub NI | MMIO |
| 0x01C00000 | IO6prom (in DRAM) | R/W initially, ROM after load |
| 0x08000000 | Bridge registers | MMIO |
| 0x08020000 | Bridge PCI config | MMIO (PCI cfg access) |
| 0x08100000 | IOC3 BAR0 | MMIO |
| 0x08400000 | Bridge flash (IO6prom source) | ROM |
| 0x10000000 | IP27prom flash (LBOOT/HSPEC alias) | ROM — same image as 0x1FC00000; ip27config at +0x60 |
| 0x1FC00000 | IP27prom flash (COMPAT K1 reset vector) | ROM — primary PROM region |

## Sources

- `irix/kern/sys/SN/SN0/addrs.h` — NODE_SIZE_BITS, SWIN_SIZE_BITS, LBOOT_BASE
- `irix/kern/sys/SN/addrs.h` — IO_BASE, HSPEC_BASE, TO_PHYS macros
- `irix/kern/sys/SN/SN0/ip27config.h` — IO6PROM_BASE, IP27PROM_BASE addresses
