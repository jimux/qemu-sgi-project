# Memory Sizing: How IRIX Detects RAM on IP27

## Overview

On Indy/O2, the PROM probes physical DRAM by writing test patterns to detect
bank sizes. On Origin 200, the same principle applies via the Hub MD back-door
memory space. The result is stored in `MD_MEMORY_CONFIG`, which IRIX reads
directly during boot to determine total available RAM.

## MD_MEMORY_CONFIG Register

**Physical address (NASID 0)**: 0x01200018
**Register offset from Hub base**: `MD_MEMORY_CONFIG = 0x200018`

The register is a 64-bit value. In M-mode (Origin 200), 8 banks are supported.
Each bank occupies 3 bits at shift `MMC_BANK_SHFT(bank) = bank * 3`:

```
bits [2:0]   bank 0 (bottommost address range)
bits [5:3]   bank 1
bits [8:6]   bank 2
bits [11:9]  bank 3
bits [14:12] bank 4
bits [17:15] bank 5
bits [20:18] bank 6
bits [23:21] bank 7
bit  [28]    MMC_DIR_PREMIUM (premium directory DIMMs)
```

### Bank Size Encoding (`MD_SIZE_*`)

From `irix/kern/sys/SN/SN0/hubmd.h`:

```c
#define MD_SIZE_EMPTY   0    /* bank absent */
#define MD_SIZE_8MB     1
#define MD_SIZE_16MB    2
#define MD_SIZE_32MB    3    /* broken in Hub 1, OK in Hub 2 */
#define MD_SIZE_64MB    4
#define MD_SIZE_128MB   5
#define MD_SIZE_256MB   6
#define MD_SIZE_512MB   7
#define MD_SIZE_1GB     8
#define MD_SIZE_2GB     9
#define MD_SIZE_4GB     10

/* Helper: size code → bytes */
#define MD_SIZE_BYTES(size)  ((size) == 0 ? 0 : 0x400000L << (size))
/* 1→4MB<<1=8MB, 2→4MB<<2=16MB, ..., 8→4MB<<8=1GB, 10→4MB<<10=4GB */
```

### Example Encodings

| RAM Size | Bank Config | MD_MEMORY_CONFIG Value |
|----------|-------------|------------------------|
| 128 MB | 1×128MB bank 0 | `MD_SIZE_128MB << 0 = 0x00000005` |
| 256 MB | 1×256MB bank 0 | `MD_SIZE_256MB << 0 = 0x00000006` |
| 512 MB | 1×512MB bank 0 | `MD_SIZE_512MB << 0 = 0x00000007` |
| 1 GB | 2×512MB (banks 0,1) | `(7 << 0) | (7 << 3) = 0x0000003f` |
| 1 GB | 1×1GB bank 0 | `MD_SIZE_1GB << 0 = 0x00000008` |
| 4 GB | 8×512MB | `0x00ffffff` (all banks at 7) |
| 4 GB | 4×1GB | `(8<<0)|(8<<3)|(8<<6)|(8<<9) = 0x00011088` |
| 8 GB | 8×1GB | `(8<<0)|(8<<3)|...|(8<<21) = 0x01249249` |

### QEMU Encoding Function

```c
/*
 * Encode QEMU -m RAM size into MD_MEMORY_CONFIG.
 * Strategy: fill banks greedily with largest power-of-two that fits.
 * Prefer fewer, larger banks (cleaner for IRIX memory map).
 */
static uint64_t hub_md_mem_config(uint64_t ram_bytes)
{
    /* MD_SIZE values → bank sizes in bytes */
    static const uint64_t bank_sizes[] = {
        [MD_SIZE_EMPTY]  = 0,
        [MD_SIZE_8MB]    = 8ULL   << 20,
        [MD_SIZE_16MB]   = 16ULL  << 20,
        [MD_SIZE_32MB]   = 32ULL  << 20,
        [MD_SIZE_64MB]   = 64ULL  << 20,
        [MD_SIZE_128MB]  = 128ULL << 20,
        [MD_SIZE_256MB]  = 256ULL << 20,
        [MD_SIZE_512MB]  = 512ULL << 20,
        [MD_SIZE_1GB]    = 1024ULL << 20,
        [MD_SIZE_2GB]    = 2048ULL << 20,
        [MD_SIZE_4GB]    = 4096ULL << 20,
    };

    uint64_t cfg = 0;
    uint64_t remaining = ram_bytes;
    int bank = 0;

    while (remaining > 0 && bank < MD_MEM_BANKS /* 8 */) {
        int sz;
        /* Find largest bank size that fits in remaining RAM */
        for (sz = MD_SIZE_4GB; sz >= MD_SIZE_8MB; sz--) {
            if (bank_sizes[sz] <= remaining) break;
        }
        if (sz < MD_SIZE_8MB) break;  /* less than 8MB remaining */

        cfg |= ((uint64_t)sz << (bank * 3));
        remaining -= bank_sizes[sz];
        bank++;
    }

    return cfg;
}
```

**Note**: Real hardware probes via back-door memory (mdir_config in mdir.c).
QEMU bypasses probing by pre-computing and returning the correct value when
the PROM reads MD_MEMORY_CONFIG. The PROM will write to MD_MEMORY_CONFIG
during probing (setting all banks to max), then read back to verify, then
write the final detected value. QEMU's write handler should update the stored
value; reads always return the current stored value.

---

## How the PROM Detects Memory

From `stand/arcs/IP27prom/mdir.c` (`mdir_config()`):

```c
void mdir_config(nasid_t nasid, u_short *prem_mask)
{
    /* 1. Save current MD_MEMORY_CONFIG */
    old_cfg = LD(REMOTE_HUB(nasid, MD_MEMORY_CONFIG));

    /* 2. Set all banks to max (512MB) + premium to allow full probe */
    SD(REMOTE_HUB(nasid, MD_MEMORY_CONFIG), old_cfg | MMC_BANK_ALL_MASK | MMC_DIR_PREMIUM);

    /* 3. For each bank 0..7: probe via back-door memory */
    for (bank = 0; bank < MD_MEM_BANKS; bank++) {
        base = NODE_UNCAC_BASE(nasid) + (bank << MD_BANK_SHFT);
        size = size_back_door(base, &premium);  /* non-destructive probe */
        new_cfg |= size << MMC_BANK_SHFT(bank);
        ...
    }

    /* 4. Write final config */
    SD(REMOTE_HUB(nasid, MD_MEMORY_CONFIG), new_cfg);
}
```

The back-door probe (`size_back_door()`) writes test patterns to the Hub MD
back-door access path (HSPEC BDPRT/BDDIR space) and detects aliasing to
determine bank size. In QEMU, we don't implement back-door memory; instead,
the pre-computed `MD_MEMORY_CONFIG` persists through these writes and the
PROM's final write of `new_cfg` sets the "authoritative" value.

For QEMU correctness: the PROM's computed `new_cfg` **should match** what
QEMU's `hub_md_mem_config()` pre-computed. If they diverge (because the PROM
probed 0 banks), the PROM will print "No memory found" and hang. The PROM
reads back MD_MEMORY_CONFIG after writing `new_cfg`, so our read-back of the
PROM's own write will satisfy this check.

**However**: the back-door probing uses Node uncached memory (`NODE_UNCAC_BASE`)
which accesses physical DRAM. If QEMU maps DRAM correctly, the test pattern
writes/reads may work naturally. But the back-door address translation
(BDPRT_ENTRY macro) is complex — the simpler path is to ensure DRAM is
present and the PROM's probe detects it correctly at the target addresses.

---

## IRIX Memory Detection (szmem equivalent)

After IO6prom loads, IRIX `szmem()` for IP27 reads `MD_MEMORY_CONFIG` directly:

```c
/* IRIX kernel IP27 memory sizing (schematic) */
uint64_t mc = LD(REMOTE_HUB(nasid, MD_MEMORY_CONFIG));
for (bank = 0; bank < 8; bank++) {
    int sz = (mc >> (bank * 3)) & 7;
    if (sz != MD_SIZE_EMPTY)
        total += MD_SIZE_BYTES(sz);
}
```

This is analogous to O2 reading CRIME's MEM_BANK_CTRL registers. No probing
needed at kernel time — the PROM has already established the correct value.

## Memory Ceiling Analysis

### Per Node (NASID 0)

| Configuration | Total RAM |
|---------------|-----------|
| 8 × 1 GB banks | 8 GB |
| 8 × 512 MB banks | 4 GB |
| 4 × 1 GB banks | 4 GB |
| 2 × 512 MB banks | 1 GB |
| 1 × 512 MB bank | 512 MB |

**Practical maximum for emulation**: 8 GB per node. The MD_MEMORY_CONFIG
only has 10 distinct size values, with MD_SIZE_4GB being the largest single
bank at 4 GB. With 8 banks of 4 GB: 32 GB — but this is unrealistic for
Origin 200. Cap QEMU at 8 GB (8 × 1 GB = MD_SIZE_1GB per bank).

### Multi-Node (Future)

For multi-node SN0 (not SN00), additional nodes at NASID > 0 each have their
own MD_MEMORY_CONFIG. The kernel maps memory from all nodes. For our SN00
single-node implementation, this is not relevant.

### Can QEMU go above 8 GB for SN00?

No — `MD_MEMORY_CONFIG` physically cannot encode more than 8 banks × 4 GB
= 32 GB. For a realistic Origin 200, 8 GB is the hardware ceiling. IRIX on
IP27 with a single node supports up to 8 GB RAM natively (and up to 64 GB
total with 8 nodes, but SN00 has only 1 node).

---

## ARCS Memory Descriptors

After IO6prom loads and before handing control to IRIX, it provides an ARCS
memory descriptor table. These descriptors tell the kernel which physical
memory ranges are:
- `FreeMemory`: Available for IRIX
- `FirmwareTemporary`: Used by IO6prom (can be reclaimed)
- `FirmwarePermanent`: Boot data structures (must be preserved)
- `BadMemory`: Failed or absent regions

For QEMU, IO6prom will build these descriptors by reading MD_MEMORY_CONFIG
and consulting the kldir. The `FREEMEM_BASE = 0x02000000` is the base address
IO6prom marks as the start of free memory for kernel loading.

## Sources

- `irix/kern/sys/SN/SN0/hubmd.h` — MD_SIZE_*, MMC_BANK_SHFT, MD_MEM_BANKS
- `stand/arcs/IP27prom/mdir.c` — mdir_config(), size_back_door()
- `irix/kern/sys/SN/SN0/addrs.h` — IO6PROM_BASE, FREEMEM_BASE
