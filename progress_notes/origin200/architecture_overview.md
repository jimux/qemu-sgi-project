# IP55 Architecture Overview: SGI Origin 200 (SN00)

## What IP55 Is

"IP55" is our internal QEMU machine name for the SGI Origin 200, which is an
SN00 (single-node SN0) system. The Origin 200 (and earlier Origin 2000 rack)
use the same ASIC set; the Origin 200 is simply one node operating standalone.

| Feature | O2 (IP32) | Indy (IP24) | Origin 200 (IP55) |
|---------|-----------|-------------|-------------------|
| CPU | R10000 | R4600 | R10000/R12000 |
| Max RAM | 1 GB (CRIME) | 256 MB (MC) | 8 GB (Hub MD) |
| Bus | MACE/CRIME | GIO64 | XIO (Crosstalk) |
| ABI | n32 (limited) | 32-bit | N64 (true 64-bit) |
| SMP | No | No | 2 CPUs/node |

## SN00 vs SN0

- **SN0** (Origin 2000): Multi-node NUMA system with Router ASICs linking
  multiple nodes. Each node has a Hub, 2 CPUs, and DRAM. Up to 128 nodes.
- **SN00** (Origin 200): Single-node system. No Router ASICs. No NUMAlink
  network interface needed. The NI section exists in the Hub ASIC but the
  link is never brought up. Identified by `ip27config.mach_type = 1`
  (`SN00_MACH_TYPE`).

The `SN00` macro in IRIX source (`irix/kern/sys/SN/SN0/ip27config.h`) reads from
`IP27CONFIG_SN00_ADDR = LBOOT_BASE + 0x60 + 48`, which is inside the PROM flash.
The actual ip27prom.img binary for Origin 200 encodes `mach_type=1` there.

## Component Diagram

```
                     ┌─────────────────────────────┐
  CPU A (R10000) ───►│          Hub ASIC           │
  CPU B (R10000) ───►│                             │
                     │  PI  │  MD  │  IIO  │  NI  │
                     │      │      │       │      │
                     │      │      │  XIO  │  NI  │◄── (link down: SN00)
                     └──────┴──┬───┴───┬───┴──────┘
                               │       │
                            DRAM      XIO link
                         (8 banks)     │
                               │     Xbow (w0)
                               │       │
                               │     Bridge (w8)
                               │       │ PCI bus
                               │       ├── IOC3 (UART A/B, Ethernet)
                               │       └── QL (SCSI)
```

## CPU: R10000/R12000

- 64-bit MIPS IV architecture, -mips4 -64 ISA
- True 64-bit virtual address space (no n32 limitation)
- Two CPUs supported per Hub node
- QEMU CPU type: `mips64dspr2-mips64` or similar 64-bit MIPS model
- R10000 `freq_cpu` typical: 175–250 MHz (SN00); R12000: up to 400 MHz

## Memory: Hub MD Section

- **MD_MEMORY_CONFIG** register at MD base + 0x000018 (Hub-relative: 0x200018)
- 8 banks in M-mode (`MD_MEM_BANKS = 8`), 3 bits per bank
- Bank sizes: 0=empty, 1=8MB, 2=16MB, 3=32MB, 4=64MB, 5=128MB, 6=256MB,
  7=512MB, 8=1GB, 9=2GB, 10=4GB
- Maximum: 8 banks × 1 GB = 8 GB per node
- Real Origin 200 ships with 32-512 MB; QEMU can expose up to 8 GB

## Bus: XIO (Crosstalk)

XIO replaces GIO64 (Indy) and MACE/CRIME (O2). It is a packet-switched
fabric with widget IDs 0–15. Each widget gets a 16 MB "small window" (SWIN).

| Widget | Device | Physical base (NASID 0, M-mode) |
|--------|--------|----------------------------------|
| 0 | Xbow itself | Special: accessed via big window |
| 1 | Hub IIO (IALIAS) | 0x01000000 |
| 8 | Bridge (IO6 card) | 0x08000000 |

The Hub IIO section is widget 1. The Xbow is widget 0 (self). The Bridge
connects to PCI and hosts IOC3 (UART/Ethernet/PS2/RTC) and the IO6 PROM flash.

## Two-Stage PROM

Unlike Indy (single PROM image), Origin 200 uses a two-stage boot:

1. **IP27prom** at 0xBFC00000 (physical 0x1FC00000, in Hub LBOOT space):
   - Runs from reset vector, initialises Hub PI/MD/IIO, sizes DRAM
   - Uses Hub MD I2C UART (`MD_UREG0_0`) for very early diagnostic output
   - Discovers XIO topology: finds Xbow → Bridge → IOC3
   - Loads IO6prom from Bridge flash into RAM at 0x01C00000
   - Jumps to IO6prom

2. **IO6prom** at physical 0x01C00000 (`IO6PROM_BASE`):
   - Initialises IOC3 UART A (real serial console, 9600 baud)
   - Provides ARCS firmware interface
   - Loads IRIX kernel via SCSI/network

The ip27prom.img binary is 913 KB; io6prom.img is 365 KB.

## IRIX Target: 6.5.x

Same IRIX 6.5 overlay releases work on IP27 as on IP32 (O2). The IP27
kernel is a true N64 binary (not n32). `hinv` will show "IP27" board type.
Memory is reported via Hub MD_MEMORY_CONFIG rather than CRIME registers.

## No GIO64, No CRIME, No MACE

The Origin 200 shares NO hardware with Indy or O2. Key difference points:
- No Newport/GE11 graphics (Origin 200 uses IMPACT or Odyssey graphics boards,
  but base console is text via IOC3 UART — IRIX headless or with graphics card)
- No HAL2 audio (separate audio in XIO expansion)
- No Seeq Ethernet (IOC3 has integrated 10/100 Ethernet)
- The SCSI controller is a QLogic 1040B on PCI, behind Bridge, not WD33C93

## Sources

- `irix/kern/sys/SN/SN0/ip27config.h` — SN00 mach_type, RTC frequency
- `irix/kern/sys/SN/SN0/addrs.h` — Address space macros (LBOOT, IALIAS, etc.)
- `stand/arcs/IP27prom/main.c` — Boot sequence orchestration
- `stand/arcs/IP27prom/mdir.c` — Memory sizing (mdir_config)
- `stand/arcs/IP27prom/ioc3uart.c` — IOC3 UART initialization
