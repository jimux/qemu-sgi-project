---
# IP30 (SGI Octane) Emulation Bringup Notes

## Status: HEART/BRIDGE Responding — Serial Blocked on IOC3 UART

## What Works

- **Machine type registered:** `qemu-system-mips64 -M octane` launches correctly.
- **1 MB PROM loads:** IP30 PROM rev 4.9 image loads at `0x1FC00000`.
- **PROM executes:** CPU initializes (Performance counter MTCOs visible in `-d unimp`).
- **HEART responds:** PROM reads HEART_PRID (offset 0x50000) → 0 (1 CPU). HEART_STATUS
  (offset 0x80) → 8 (widget ID 8). Both correct.
- **BRIDGE responds:** PROM probes IOC3 via BRIDGE PCI config (offset 0x22004/0x22010)
  and devio window (0x600028/0x600034/0x60003c). No bus errors.
- **Exception handler set:** PROM writes `0x9fc16314` to HEART_TRIGGER — a PROM
  address used as a software exception/interrupt vector.

## Key Discoveries and Fixes

### 1. Wrong Physical Addresses (Root Cause of Initial Bus Error Loop)

The original `sgi_octane.c` had the Indy/HPC-style addresses for HEART and BRIDGE.
The Octane uses an XIO widget bus with different physical addresses:

| Device | Wrong (initial) | Correct |
|--------|----------------|---------|
| HEART PIU | `0x1FF00000` | `0x0FF00000` |
| BRIDGE | `0x1F400000` | `0x1F000000` |

The PROM accesses HEART_PRID at `0x0FF50000` (KSEG1: `0xAFF50000`) on boot.
With HEART at the wrong address, every access was a data bus error, causing an
infinite exception handler loop (PROM at 0x9fc00880 → handler at 0xbfc00380 → repeat).

**Reference:** IRIX `sys/RACER/heart.h`: `HEART_PIU_BASE = 0x0FF00000`.
`HEART_BASE (XIO widget 8) = MAIN_WIDGET(8) = 0x18000000` (used for XIO config,
not the PIU registers). The PROM uses the PIU address.

### 2. BRIDGE Size Too Small

Original `BRIDGE_REG_SIZE = 0x10000` (64 KB). Expanded to `0xC00000` (12 MB) to
cover the full widget 0xF window (`0x1F000000`–`0x1FBFFFFF`) containing:
- Bridge control registers (first 64 KB)
- IOC3 PCI config at BRIDGE+0x022000
- IOC3 devio window at BRIDGE+0x600000 (IOC3 serial, kbd, mouse, ethernet)

PROM flash at `0x1FC00000` = BRIDGE+0xC00000 is mapped separately as a ROM region
and does not overlap with the BRIDGE device (BRIDGE covers up to `0x1FBFFFFF`).

### 3. HEART_STATUS Widget ID

HEART_STATUS bits [3:0] = HEART_STAT_WIDGET_ID. On Octane, HEART is hardwired to
XIO port 8. Reset value was 0; fixed to 8. The PROM reads this to confirm it's
talking to HEART (and not a phantom bus response).

### 4. PROM Size: 1 MB (not 512 KB)

IP30 PROM is 1 MB. The initial `OCTANE_PROM_SIZE = 512 * KiB` caused
`load_image_targphys` to fail silently, producing "Could not load PROM image".

### 5. GBE Removed from Octane Machine

The original `sgi_octane.c` instantiated the GBE (O2 framebuffer) device. The
Octane uses MGRAS/Impact graphics, not GBE. The GBE device was removed from the
machine init. `SGI_GBE` remains in the Kconfig `select` list (for future cleanup).

### 6. Xbow Stub Added

Added `create_unimplemented_device("xbow", 0x10000000, 16 * MiB)` to catch PROM
widget enumeration probes at Xbow widget 0. Prevents bus errors during XIO discovery.

## Memory Map (Corrected)

```
0x00000000           Unimplemented stub (null-pointer catch)
0x0FF00000-0x0FF6FFFF  HEART PIU (processor registers, 0x70000 bytes)
0x10000000-0x10FFFFFF  Xbow crossbar widget 0 (stub, 16 MB)
0x1F000000-0x1FBFFFFF  BRIDGE widget 0xF (12 MB covers PCI/IOC3/devio)
  +0x022000             IOC3 PCI config space
  +0x600000             IOC3 devio window (serial, kbd, mouse, NIC)
    +0x20178            IOC3 UART A (ns16550-compatible)
0x1FC00000-0x1FCFFFFF  PROM flash (1 MB, BRIDGE+0xC00000)
0x20000000+            System RAM (XKPHYS access for >512 MB)
```

## Next Steps

### IOC3 UART Stub (blocks serial output)

The PROM initializes IOC3 serial via:
1. PCI config writes to enable bus mastering (BRIDGE+0x22004, 0x22010)
2. Devio writes to IOC3 SIO registers (BRIDGE+0x600028, 0x600034, 0x60003c)
3. UART writes to the 16550-compatible UART (expected at BRIDGE+0x620178)

Without a responding UART, the PROM stalls before outputting its banner.

**Minimal stub needed:** Map a `serial_mm_init` or custom device at the IOC3 UART
address that handles TX writes by forwarding to stdio. IOC3 UART register spacing
may be non-standard (need to verify from `sys/PCI/ioc3.h`).

### HEART TRIGGER Register

The PROM writes `0x9fc16314` to HEART_TRIGGER. This appears to set an exception/
interrupt handler address. Understand if HEART_TRIGGER is used to generate a test
interrupt during POST (which would require implementing the timer or a software IRQ).

### Memory Controller (HEART MEMCFG)

HEART_MEMCFG registers need to be initialized to reflect the emulated RAM size so
the PROM can size memory correctly. Similar to the MC MEMCFG implementation for Indy.
Currently all MEMCFG registers reset to 0 (no valid banks), which the PROM may
interpret as 0 MB RAM.

## IOC3 Register Reference (from IRIX sys/PCI/ioc3.h)

```c
#define IOC3_SIO_IR         0x000  /* SIO interrupt register */
#define IOC3_SIO_IEC        0x004  /* interrupt enable clear */
#define IOC3_SIO_IES        0x008  /* interrupt enable set */
#define IOC3_SIO_CR_A       0x020  /* Channel A control */
#define IOC3_MISC_REGS      0x028  /* Misc register offset */
/* UART A base (ns16550-compatible): IOC3_devio_base + 0x20178 */
```

## PROM Boot Sequence Observed

```
[CPU init] Performance counter MTCOs (very early, disables perf counting)
[HEART]    read PRID → 0 (1 CPU, CPU 0)
[HEART]    read STATUS → 8 (widget ID OK)
[HEART]    read/write MODE (enable caches, interrupts)
[HEART]    write IMR registers (mask setup)
[BRIDGE]   read PCI config device 2 (IOC3 discovery)
[BRIDGE]   write PCI config (enable bus master, set BAR)
[BRIDGE]   write devio 0x600028/34/3c (IOC3 SIO init)
[HEART]    write TRIGGER ← 0x9fc16314 (exception handler)
[STALL]    PROM waits for IOC3 UART ready → hangs
```
---
