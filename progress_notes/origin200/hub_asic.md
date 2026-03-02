# Hub ASIC Register Reference for IP55/QEMU

The Hub ASIC is the central chip of the Origin 200. It has four functional
sections: PI (Processor Interface), MD (Memory/Directory), IIO (IO Interface),
and NI (Network Interface). All registers are 64-bit, 8-byte aligned.

## Physical Address Layout (NASID 0, M-mode)

The Hub is widget 1 in the XIO fabric. `IALIAS_BASE = NODE_SWIN_BASE(0, 1)`.
In physical address terms (M-mode, NASID 0):

```
0x01000000  Hub PI  section (0x200000 bytes)
0x01200000  Hub MD  section (0x200000 bytes)
0x01400000  Hub IIO section (0x200000 bytes)
0x01600000  Hub NI  section (0x080000 bytes)
```

Offsets below are absolute physical addresses for NASID 0. In the IRIX source,
registers are accessed via `LOCAL_HUB(reg_offset)` or `REMOTE_HUB(nasid, reg_offset)`.
The symbolic offset (e.g., `PI_CPU_NUM = 0x000020`) is added to the Hub base.

---

## PI Section (Processor Interface) — base 0x01000000

**Critical registers for QEMU boot (must return correct values):**

| Register | Offset | Physical (NASID 0) | Description | QEMU value |
|----------|--------|---------------------|-------------|------------|
| PI_CPU_NUM | 0x000020 | 0x01000020 | CPU slice number (0=CPU A, 1=CPU B) | 0 (CPU A reads this) |
| PI_CPU_PRESENT_A | 0x000040 | 0x01000040 | CPU A present | 1 (present) |
| PI_CPU_PRESENT_B | 0x000048 | 0x01000048 | CPU B present | 0 or 1 (config) |
| PI_CPU_ENABLE_A | 0x000050 | 0x01000050 | CPU A enabled | 1 |
| PI_CPU_ENABLE_B | 0x000058 | 0x01000058 | CPU B enabled | 0 (1-CPU) or 1 (2-CPU) |
| PI_INT_PEND0 | 0x000098 | 0x01000098 | Interrupt pending 0 (read) | 0 at reset |
| PI_INT_PEND1 | 0x0000a0 | 0x010000a0 | Interrupt pending 1 (read) | 0 at reset |
| PI_INT_MASK0_A | 0x0000a8 | 0x010000a8 | Int mask 0 for CPU A | R/W |
| PI_INT_MASK1_A | 0x0000b0 | 0x010000b0 | Int mask 1 for CPU A | R/W |
| PI_INT_MASK0_B | 0x0000b8 | 0x010000b8 | Int mask 0 for CPU B | R/W |
| PI_INT_MASK1_B | 0x0000c0 | 0x010000c0 | Int mask 1 for CPU B | R/W |
| PI_RT_COUNT | 0x030100 | 0x01030100 | Real-time counter (free-running) | Incrementing 64-bit |
| PI_RT_COMPARE_A | 0x000108 | 0x01000108 | RT compare for CPU A (→ IP8) | R/W |
| PI_RT_COMPARE_B | 0x000110 | 0x01000110 | RT compare for CPU B (→ IP8) | R/W |
| PI_RT_EN_A | 0x000140 | 0x01000140 | RT interrupt enable for CPU A | R/W |
| PI_RT_EN_B | 0x000148 | 0x01000148 | RT interrupt enable for CPU B | R/W |
| PI_HARDRESET_BIT | 0x020068 | 0x01020068 | Cleared by SW on soft reset | R/W |

**Local arbitration protocol (dual-CPU boot):**

During boot, both CPUs use `PI_RT_COMPARE_A` and `PI_RT_COMPARE_B` as
progress indicators (`PLED_LOCALARB`). The PROM writes its progress code to
its own compare register, then waits for the peer's compare register to
change. After timeout, the surviving CPU disables the other via
`PI_CPU_ENABLE_B` (or A) and proceeds as sole master.

**PI_RT_COUNT scheduling clock:**

- Rate: `IP27_RTC_FREQ = 1250 Hz` (800ns cycle) — from ip27config.h
- Fires interrupt IP8 (`SR_IBIT8`) when `PI_RT_COUNT >= PI_RT_COMPARE_A/B`
- IRIX IP27 kernel uses this for `startrtclock()` (NOT the 8254 PIT)
- QEMU: implement as a QEMU timer ticking at 1250 Hz virtual time

---

## MD Section (Memory/Directory) — base 0x01200000

**Critical registers for QEMU boot:**

| Register | Offset | Physical (NASID 0) | Description | QEMU value |
|----------|--------|---------------------|-------------|------------|
| MD_MEMORY_CONFIG | 0x200018 | 0x01200018 | Memory bank size configuration | Encode from -m |
| MD_REFRESH_CONTROL | 0x200010 | 0x01200010 | DRAM refresh control | R/W (ignore) |
| MD_MOQ_SIZE | 0x200020 | 0x01200020 | Message output queue size | R/W stub |
| MD_MEM_DIMM_INIT | 0x200028 | 0x01200028 | DIMM mode init | W-only stub |
| MD_DIR_DIMM_INIT | 0x200030 | 0x01200030 | Dir DIMM mode init | W-only stub |
| MD_LED0 | 0x220050 | 0x01220050 | LED register 0 (Origin 2000 only) | W-only, ignore |
| MD_LED1 | 0x220058 | 0x01220058 | LED register 1 | W-only, ignore |
| MD_UREG0_0 | 0x220000 | 0x01220000 | I2C / Hub UART register 0 | PCF8584 stub |
| MD_UREG0_1 | 0x220008 | 0x01220008 | I2C / Hub UART register 1 | PCF8584 stub |
| MD_UREG1_0 | 0x220100 | 0x01220100 | LED on SN00 (vs MD_LED0 on SN0) | W-only, ignore |
| MD_MLAN_CTL | 0x2000a8 | 0x010200a8 | MicroLAN (1-wire NIC) control | Return 0 |

**MD_MEMORY_CONFIG encoding:**

Defined in `irix/kern/sys/SN/SN0/hubmd.h`. In M-mode there are 8 banks.
Each bank occupies 3 bits: `MMC_BANK_SHFT(b) = b * 3`.

```
Bits [2:0]   = bank 0 size
Bits [5:3]   = bank 1 size
Bits [8:6]   = bank 2 size
...
Bits [23:21] = bank 7 size
Bit  [28]    = MMC_DIR_PREMIUM (1 = premium DIMMs)
```

Size encoding (`MD_SIZE_*`):

| Value | Size |
|-------|------|
| 0 | Empty (bank absent) |
| 1 | 8 MB |
| 2 | 16 MB |
| 3 | 32 MB |
| 4 | 64 MB |
| 5 | 128 MB |
| 6 | 256 MB |
| 7 | 512 MB |
| 8 | 1 GB |
| 9 | 2 GB |
| 10 | 4 GB |

`MD_SIZE_BYTES(size) = (size == 0) ? 0 : 0x400000L << size`

**QEMU MD_MEMORY_CONFIG construction:**

```c
/* Pack ram_bytes into MD_MEMORY_CONFIG: 8 banks, 3 bits each */
/* Fill banks greedily from largest to smallest power-of-two */
uint64_t sgi_hub_md_mem_config(uint64_t ram_bytes) {
    /* Size table: value → bytes */
    static const uint64_t sz_bytes[] = {
        0,          /* 0 = empty */
        8*1024*1024,    /* 1 = 8 MB */
        16*1024*1024,   /* 2 = 16 MB */
        32*1024*1024,   /* 3 = 32 MB */
        64*1024*1024,   /* 4 = 64 MB */
        128*1024*1024,  /* 5 = 128 MB */
        256*1024*1024,  /* 6 = 256 MB */
        512*1024*1024,  /* 7 = 512 MB */
        1024*1024*1024ULL, /* 8 = 1 GB */
        2048*1024*1024ULL, /* 9 = 2 GB */
        4096*1024*1024ULL, /* 10 = 4 GB */
    };
    uint64_t cfg = 0;
    int bank = 0;
    uint64_t remaining = ram_bytes;

    while (remaining > 0 && bank < 8) {
        /* Find largest fitting power-of-two bank size */
        int sz;
        for (sz = 10; sz >= 1; sz--)
            if (sz_bytes[sz] <= remaining)
                break;
        if (sz == 0) break;
        cfg |= ((uint64_t)sz << (bank * 3));
        remaining -= sz_bytes[sz];
        bank++;
    }
    return cfg;
}
```

**Hub MD I2C UART (`MD_UREG0_0`):**

Before IOC3 UART is initialised, the PROM uses `MD_UREG0_0` as an I2C
interface to an external PCF8584 I2C controller chip (on the SN00 board)
which bridges to a UART. This path is only used for very early diagnostics.

For QEMU, `MD_UREG0_0` writes can be silently ignored (no early output) or
handled with a stub that acknowledges I2C status. The PROM only uses it
briefly; once it reaches `ioc3uart_init()`, all output goes through IOC3.

Key I2C reset sequence observed in early PROM code:
```c
SD(LOCAL_HUB(MD_UREG0_0), reset_val);  /* Reset I2C controller */
```

For boot-critical path: return 0x00 (idle status) on reads from MD_UREG0_0.

---

## IIO Section (IO Interface) — base 0x01400000

**Critical registers for QEMU boot:**

| Register | Offset | Physical (NASID 0) | Description | QEMU value |
|----------|--------|---------------------|-------------|------------|
| IIO_WID | 0x400000 | 0x01400000 | Hub widget ID | `0xc101_<mfgr>_<rev>` |
| IIO_WSTAT | 0x400008 | 0x01400008 | Widget status | 0 (no errors) |
| IIO_WCR | 0x400020 | 0x01400020 | Widget control | R/W |
| IIO_WRTO | 0x400028 | 0x01400028 | Widget request timeout | R/W stub |
| IIO_IOWA | 0x400110 | 0x01400110 | Outbound widget access enable | R/W |
| IIO_ILCSR | 0x400128 | 0x01400128 | LLP control/status | 0x2000 (link up) |
| IIO_ILLR | 0x400130 | 0x01400130 | LLP log | 0 |
| IIO_IIDSR | 0x400138 | 0x01400138 | Interrupt destination | R/W |
| IIO_PRTE_0 | 0x400308 | 0x01400308 | PIO read table entry 0 | R/W |
| IIO_PRTE(n) | 0x400308+n*8 | ... | PRTE n (n=0..6) | R/W |

**IIO_WID (Hub widget ID):**

Widget ID register format (from `irix/kern/sys/xtalk/xwidget.h`):
```
bits [31:28] = revision (4-bit)
bits [27:12] = part number (16-bit): HUB_WIDGET_PART_NUM = 0xc101
bits [11:1]  = manufacturer ID (11-bit)
bit  [0]     = 1 (always)
```

QEMU value: `0x0000c101000` with `rev=2` and `mfgr=0x36` (SGI):
```c
#define HUB_WIDGET_PART_NUM  0xc101
#define HUB_WIDGET_MFG_NUM   0x036
#define HUB_WIDGET_REV       2       /* Hub 2.4 */

hub_wid = (HUB_WIDGET_REV    << 28) |
          (HUB_WIDGET_PART_NUM << 12) |
          (HUB_WIDGET_MFG_NUM  <<  1) |
          1;
/* = 0x2c101_06d (approximately) */
```

**IIO_ILCSR (LLP Control/Status Register):**

The PROM checks `IIO_LLP_CSR_IS_UP = 0x00002000` (bit 13) in this register
to verify the XIO link to Xbow is operational. For QEMU:
- Return `0x00002000` (link-up bit set) to allow the PROM to proceed
- This is safe even without a real Xbow implementation (PROM just uses it
  as a gate check before attempting XIO probes)

**IIO_PRTE (PIO Read Table Entry):**

The ITTE/PRTE maps large ("big") window widget accesses. `IIO_WIDPRTE(widget)`
maps widget ID to PRTE index: `IIO_PRTE(widget - 8)`.

For NASID 0, widget 8 (Bridge): `IIO_WIDPRTE(8) = IIO_PRTE(0)`.
The PRTE encodes the target widget's address and access rights. QEMU should
make these R/W registers that default to 0.

---

## NI Section (Network Interface) — base 0x01600000

**Critical registers for QEMU (SN00 single-node):**

| Register | Offset | Physical (NASID 0) | Description | QEMU value |
|----------|--------|---------------------|-------------|------------|
| NI_STATUS_REV_ID | 0x600000 | 0x01600000 | Hub rev, NASID, link status | See below |
| NI_PORT_RESET | 0x600008 | 0x01600008 | NI reset register | R/W |
| NI_SCRATCH_REG0 | 0x600100 | 0x01600100 | Scratch register 0 | R/W, init 0 |
| NI_SCRATCH_REG1 | 0x600108 | 0x01600108 | Scratch register 1 (SN00 bit) | R/W, init 0 |

**NI_STATUS_REV_ID bit fields:**

From `irix/kern/sys/SN/SN0/hubni.h`:
```
bits [30]    = NSRI_8BITMODE (LLP mode)
bits [29]    = NSRI_LINKUP (1 = link up; for SN00: 0 = link DOWN)
bits [28]    = NSRI_DOWNREASON (0=failed, 1=never came out of reset)
bits [18]    = NSRI_MORENODES (0=more memory, 1=more nodes)
bits [17]    = NSRI_REGIONSIZE (0=coarse, 1=fine)
bits [16:8]  = NSRI_NODEID (NASID; 0 for single node)
bits [7:4]   = NSRI_REV (Hub chip revision; use 6 = Hub 2.4)
bits [3:0]   = NSRI_CHIPID (chip type; use 1 for Hub)
```

For a single-node SN00:
```c
#define HUB_CHIPID   1
#define HUB_REV_2_4  6

ni_status = (0 << 29)           /* LINKUP = 0 (no inter-node link) */
          | (1 << 28)           /* DOWNREASON = 1 (never reset) */
          | (0 << 18)           /* MORENODES = 0 (more memory mode) */
          | (0 << 17)           /* REGIONSIZE = 0 (coarse) */
          | (0 << 8)            /* NASID = 0 */
          | (HUB_REV_2_4 << 4) /* rev = 6 */
          | HUB_CHIPID;         /* chipid = 1 */
/* = 0x0010000061 */
```

**NI_SCRATCH_REG1 (ADVERT_SN00_MASK):**

During multi-node discovery, each node writes advertisement data to
`NI_SCRATCH_REG1` including bit 50 (`ADVERT_SN00_MASK = 1ULL << 50`) if it is
an SN00 (Origin 200) rather than an SN0 (Origin 2000) node.

```c
#define ADVERT_SN00_SHFT  50
#define ADVERT_SN00_MASK  (1ULL << ADVERT_SN00_SHFT)
```

This is written by the PROM after reading `mach_type` from the ip27config
embedded in the PROM flash. For QEMU, `NI_SCRATCH_REG1` should be a R/W
register initialised to 0. The PROM will write `ADVERT_SN00_MASK` into it
after reading ip27config.

## What Must Work vs. What Can Be Stubbed

**Must work for Milestone 1 (PROM POST to serial console):**
- `PI_CPU_NUM` → 0
- `PI_CPU_PRESENT_A` → 1
- `MD_MEMORY_CONFIG` → correctly encode `-m` RAM size
- `MD_UREG0_0` reads → return 0 (idle I2C)
- `IIO_ILCSR` → `IIO_LLP_CSR_IS_UP` set
- `IIO_WID` → valid Hub widget ID
- `NI_STATUS_REV_ID` → valid with NASID=0 and link-down

**Can return 0 / be R/W stubs:**
- `PI_INT_MASK*` — PROM sets these; OK as R/W
- `IIO_IOWA` — outbound widget access; R/W
- `IIO_PRTE_*` — translation table; R/W
- `IIO_IIDSR` — interrupt destination; R/W
- `MD_REFRESH_CONTROL` — PROM sets; OK as R/W
- `NI_SCRATCH_REG0/1` — PROM writes; R/W

**Interrupt routing:**

Hub PI maps XIO interrupts into the CPU via `PI_INT_PEND0/1` and
`PI_INT_MASK0/1`. The scheduling clock interrupt (PI_RT_COUNT comparison) maps
to `PI_INT_PEND0` bit (`L5 = IP8`). Bridge XIO interrupts map to bits in
`PI_INT_PEND0/1` as well. In QEMU, only the RT counter interrupt is critical
for IRIX scheduling; the rest can be stubbed initially.

## Sources

- `irix/kern/sys/SN/SN0/hubpi.h` — PI register offsets
- `irix/kern/sys/SN/SN0/hubmd.h` — MD register offsets, MD_SIZE_* encoding
- `irix/kern/sys/SN/SN0/hubio.h` — IIO register offsets, HUB_WIDGET_PART_NUM
- `irix/kern/sys/SN/SN0/hubni.h` — NI register offsets, NI_STATUS_REV_ID bits
- `stand/arcs/IP27prom/ip27prom.h` — ADVERT_SN00_SHFT (bit 50)
- `irix/kern/sys/SN/SN0/ip27config.h` — IP27_RTC_FREQ = 1250 Hz, mach_type
