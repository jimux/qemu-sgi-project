# SGI O2 (IP32) Hardware Reference for Emulation

Consolidated from internal SGI ASIC specifications, IRIX 6.5.7m kernel source,
IP32 PROM source, and MAME skeleton implementation.

**Sources:**
- CRIME Architecture spec (236pp)
- MACE spec (196pp)
- GBE ASIC spec (50pp)
- VICE spec (307pp)
- O2 UMA tech report (63pp)
- `irix/kern/sys/crime.h`, `mace.h`, `IP32.h`
- `IP32prom/include/sys/crime_gbe.h`, `mooseaddr.h`
- `prom-building/include/ip32/sys/crimereg.h`, `mvpregs.h`
- `prom-building/include/arcs_sys/mace_regs*.h`
- `irix/kern/ml/MOOSEHEAD/IP32init.c`, `IP32intr.c`, `IP32clock.c`, `IP32err.c`
- MAME `crime.cpp`, `mace.cpp`
- `ip32prom-decompiler/doc/reverse-engineering.md`

---

## 1. System Overview

> Sources: `o2-tech-report.pdf` ch.1-2, `IP32.h`, `mooseaddr.h`

### UMA Architecture

The SGI O2 (codename "Moosehead") uses a **Unified Memory Architecture (UMA)**
where all subsystems share a single 256-bit wide SDRAM pool running at 66 MHz,
providing ~2.1 GB/s sustained bandwidth. There is no dedicated VRAM or texture
memory — the CPU, graphics, video, and I/O all access the same physical DRAM
through CRIME's central memory controller.

### ASIC Relationships

```
                    ┌──────────┐
                    │   CPU    │
                    │ R5000 /  │
                    │ R10000 / │
                    │ R12000   │
                    └────┬─────┘
                         │ SysAD (64-bit)
                    ┌────┴─────┐
                    │  CRIME   │     ┌──────┐
                    │ Memory   ├─────┤ VICE │
                    │ Control  │     │Video/│
                    │ Interrupt│     │Compr │
                    │ Render   │     └──────┘
                    │ Engine   │
                    └──┬───┬───┘
                       │   │
              256-bit  │   │ CRIME-GBE bus
              SDRAM    │   │
              bus      │   ▼
                       │  ┌──────┐
                       │  │ GBE  │──→ Monitor (VGA/FP)
                       │  │Graph │
                       │  │Back  │
                       │  │End   │
                       │  └──────┘
                       │
                  ┌────┴─────┐
                  │  MACE    │
                  │ I/O Ctrl │
                  ├──────────┤
                  │ PCI host │──→ AIC-7880 SCSI (×2), PCI slots (×3)
                  │ Ethernet │──→ 10/100 MAC110
                  │ Audio    │──→ AD1843 codec
                  │ Video IO │──→ Analog/D1 in/out
                  │ Serial   │──→ 16550 UART (×2)
                  │ PS/2     │──→ Keyboard, Mouse
                  │ RTC      │──→ DS17287
                  │ ISA ctrl │──→ Flash, NIC, LEDs
                  │ I2C      │
                  │ UST/MSC  │──→ Timers
                  └──────────┘
```

### CPU Options

| CPU | ISA | Notes |
|-----|-----|-------|
| R5000 | MIPS IV | Original, 150-250 MHz |
| RM5271 | MIPS IV | IDT variant |
| RM7000 | MIPS IV | PMC-Sierra, 350-600 MHz |
| R10000 | MIPS IV | 175-250 MHz, requires speculation WAR |
| R12000 | MIPS IV | 270-400 MHz |

All use 64-bit SysAD bus to CRIME. Single-processor only (MAXCPU=1).

### Key Differences from IP24 (Indy)

| Feature | IP24 (Indy) | IP32 (O2) |
|---------|-------------|-----------|
| Memory controller | MC (SGI custom) | CRIME |
| I/O controller | HPC3 | MACE |
| Interrupt controller | INT3 (in IOC2) | CRIME (32 sources) |
| Graphics | Newport (REX3) | GBE (tile-based FB) + CRIME RE |
| SCSI | WD33C93B (HPC3 DMA) | 2× AIC-7880 on PCI |
| Serial | Z85C30 SCC | 16550 UART (×2) |
| Ethernet | Seeq 80C03 | MACE MAC110 |
| Audio | HAL2 + MDAC | MACE + AD1843 |
| RTC | DS1386 | DS17287 |
| Memory arch | Separate VRAM | UMA (shared SDRAM) |
| Bus | GIO64 | PCI |

---

## 2. Physical Memory Map

> Sources: `mooseaddr.h`, `crime.h`:18, `mace.h`:11-24, `IP32.h`:45-53,
> `crime_gbe.h`:21, CRIME spec ch.2, MACE spec ch.2

| Physical Address | End | Size | Device |
|-----------------|-----|------|--------|
| `0x00000000` | `0x0FFFFFFF` | 256 MB | Main Memory SEG0 |
| `0x14000000` | `0x140002FF` | 768 B | CRIME registers |
| `0x15000000` | `0x15004FFF` | 20 KB | CRIME Rendering Engine |
| `0x16000000` | `0x160FFFFF` | 1 MB | GBE registers |
| `0x17000000` | `0x170FFFFF` | 1 MB | VICE registers |
| `0x18000000` | `0x19FFFFFF` | 32 MB | PCI Low I/O |
| `0x1A000000` | `0x1BFFFFFF` | 32 MB | PCI Low Memory |
| `0x1C000000` | `0x1DFFFFFF` | 32 MB | PCI Configuration |
| `0x1F000000` | `0x1F07FFFF` | 512 KB | MACE (reserved/future) |
| `0x1F080000` | `0x1F0FFFFF` | 512 KB | MACE PCI Interface |
| `0x1F100000` | `0x1F17FFFF` | 512 KB | MACE Video In 1 |
| `0x1F180000` | `0x1F1FFFFF` | 512 KB | MACE Video In 2 |
| `0x1F200000` | `0x1F27FFFF` | 512 KB | MACE Video Out |
| `0x1F280000` | `0x1F2FFFFF` | 512 KB | MACE Ethernet (MAC110) |
| `0x1F300000` | `0x1F37FFFF` | 512 KB | MACE Peripheral Controller |
| `0x1F380000` | `0x1F3FFFFF` | 512 KB | MACE ISA External I/O |
| `0x1FC00000` | `0x1FC7FFFF` | 512 KB | PROM (Flash) |
| `0x20000000` | `0x2FFFFFFF` | 256 MB | ECC-check memory alias |
| `0x30000000` | `0x3FFFFFFF` | 256 MB | No-ECC memory alias |
| `0x40000000` | `0x4FFFFFFF` | 256 MB | Linear Memory SEG1 |

### Memory Segments

- **SEG0** (`0x00000000`-`0x0FFFFFFF`): First 256 MB, mappable via kseg0/kseg1
- **SEG1** (`0x40000000`+): Extended memory beyond 256 MB (double-mapped
  alias of SEG0 at `0x40000000`-`0x4FFFFFFF`, then additional memory at
  `0x50000000`+). Cannot be accessed via kseg0/kseg1, requires TLB.
- Minimum memory: 32 MB (`MINMEMSIZE = 0x2000000`)
- Maximum memory: 1 GB (8 banks × 128 MB)

### KSEG Mapping

```
Physical 0x00000000 → kseg0 0x80000000 (cached)
Physical 0x00000000 → kseg1 0xA0000000 (uncached)
Physical 0x1FC00000 → kseg1 0xBFC00000 (PROM reset vector)
```

---

## 3. CRIME Register Map

> Sources: `crime.h`:18-226 (kernel & PROM — identical),
> CRIME spec ch.3 (register definitions), `IP32intr.c` (interrupt dispatch),
> `IP32init.c` (memory detection), `IP32err.c` (error registers),
> MAME `crime.cpp` (skeleton)

Base address: `0x14000000` (`CRM_BASEADDR`)

All CRIME registers are 64-bit aligned. The useful data width varies per
register (8 to 48 bits). Access via 64-bit loads/stores.

### 3.1 Core Registers

| Offset | Register | Width | R/W | Description |
|--------|----------|-------|-----|-------------|
| `0x000` | CRM_ID | 8 | R | ID and revision |
| `0x008` | CRM_CONTROL | 14 | R/W | CPU interface control |
| `0x010` | CRM_INTSTAT | 32 | R | Interrupt status |
| `0x018` | CRM_INTMASK | 32 | R/W | Interrupt enable mask |
| `0x020` | CRM_SOFTINT | 32 | R/W | Software interrupt |
| `0x028` | CRM_HARDINT | 32 | R | Hardware interrupt (read-only) |
| `0x030` | CRM_DOG | 21 | R/W | Watchdog timer |
| `0x038` | CRM_TIME | 48 | R/W | Free-running timer |
| `0x040` | CRM_CPU_ERROR_ADDR | 34 | R | CPU error address |
| `0x048` | CRM_CPU_ERROR_STAT | 3/10 | R/W | CPU error status |
| `0x050` | CRM_CPU_ERROR_ENA | 3/10 | R/W | CPU error enable (Petty Crime only) |
| `0x058` | CRM_VICE_ERROR_ADDR | 30 | R | VICE error address |

### 3.2 CRM_ID Register (`0x000`)

```
Bits [7:4]  ID value:  0xA = CRIME (rev 1.1+)
                       0xB = CRIME 1.5
Bits [3:0]  Revision:  0x0 = Petty Crime (pre-production)
                       0x11 = Rev 1.1
                       0x13 = Rev 1.3
                       0x14 = Rev 1.4
```

For emulation, return `0xA1` (CRIME rev 1.1) or `0xB1` (CRIME 1.5).

### 3.3 CRM_CONTROL Register (`0x008`)

```
Bit  13     Triton SysADC check (unused)
Bit  12     CRIME SysADC check (unused)
Bit  11     Hard reset (1 = trigger power-on reset)
Bit  10     Soft reset (unused)
Bit  9      Watchdog timer enable
Bit  8      Endianness (0 = little, 1 = big) — read-only, reflects hardware
Bits [7:5]  JUICE mode (coherent load filtering), default 001
Bits [4:0]  Reserved
```

IRIX kernel checks endianness bit. Must return 1 (big-endian) for MIPS.

### 3.4 Interrupt Registers

**CRM_INTSTAT** (`0x010`) — read-only, reflects current interrupt state:

| Bit | Source | Kernel Constant |
|-----|--------|-----------------|
| 31 | VICE | `VICE_CPU_INTR` |
| 30 | CPU SysCorErr | — |
| 29 | Software interrupt 2 | `SOFT_INTR(2)` |
| 28 | Software interrupt 1 | `SOFT_INTR(1)` |
| 27 | Software interrupt 0 | `SOFT_INTR(0)` |
| 26 | RE idle (level) | `RE_INTR(4)` |
| 25 | RE FIFO full (level) | `RE_INTR(3)` |
| 24 | RE FIFO empty (level) | `RE_INTR(2)` |
| 23 | RE idle (edge) | `RE_INTR(1)` |
| 22 | RE FIFO empty (edge) | `RE_INTR(0)` |
| 21 | Memory error | `MEMERR_INTR` |
| 20 | CRIME/CPU error | `CRMERR_INTR` |
| 19 | GBE interrupt 3 | `GBE_INTR(3)` |
| 18 | GBE interrupt 2 | `GBE_INTR(2)` |
| 17 | GBE interrupt 1 | `GBE_INTR(1)` |
| 16 | GBE interrupt 0 | `GBE_INTR(0)` |
| 15 | MACE PCI shared 2 | `MACE_INTR(15)` |
| 14 | MACE PCI shared 1 | `MACE_INTR(14)` |
| 13 | MACE PCI shared 0 | `MACE_INTR(13)` |
| 12 | MACE PCI slot 2 | `MACE_INTR(12)` |
| 11 | MACE PCI slot 1 | `MACE_INTR(11)` |
| 10 | MACE PCI slot 0 | `MACE_INTR(10)` |
| 9 | MACE PCI SCSI 1 | `MACE_INTR(9)` |
| 8 | MACE PCI SCSI 0 | `MACE_INTR(8)` |
| 7 | MACE PCI bridge | `MACE_INTR(7)` |
| 6 | MACE audio | `MACE_INTR(6)` |
| 5 | MACE peripheral misc | `MACE_INTR(5)` |
| 4 | MACE serial/parallel | `MACE_INTR(4)` |
| 3 | MACE Ethernet | `MACE_INTR(3)` |
| 2 | MACE Video Out | `MACE_INTR(2)` |
| 1 | MACE Video In 2 | `MACE_INTR(1)` |
| 0 | MACE Video In 1 | `MACE_INTR(0)` |

**CRM_INTMASK** (`0x018`) — R/W, enables corresponding INTSTAT bits.

**CRM_SOFTINT** (`0x020`) — R/W, software-settable interrupt bits.

**CRM_HARDINT** (`0x028`) — R, hardware interrupt state (mask `0xF0FFFFFF`).

**Interrupt routing to CPU:**
All 32 CRIME interrupt sources are OR'd together and routed to **CPU IP2**
(Cause register bit 10, `SR_IBIT3`). The kernel's `crime_intr()` handler
reads INTSTAT & INTMASK, then dispatches to individual handlers via
`crimevec_tbl[]`. The scheduling clock uses **CPU IP7** (R4000
Count/Compare timer) independently of CRIME.

### 3.5 CRM_DOG — Watchdog Timer (`0x030`)

```
Bit  20     Watchdog timeout flag (power-on reset)
Bit  19     Warm reset flag
Bits [14:0] Counter value
```

Counter increments every 64 CRIME clock cycles (64 × 15ns = 960ns per tick).
When bit 19 reaches 1, the watchdog fires a reset. Time to reset:
2^19 × 960ns ≈ 0.5 seconds.

Enable via `CRM_CONTROL` bit 9.

### 3.6 CRM_TIME — Free-Running Timer (`0x038`)

```
Bits [47:0]  48-bit free-running counter
```

- Clock: **66,666,500 Hz** (`MASTER_FREQ`)
- Period: **15 ns per tick** (`DNS_PER_TICK`)
- Used by PROM for delay calibration and by kernel for timestamps
- Wraps after ~130 years at 15ns/tick

### 3.7 CPU/VICE Error Registers

**CRM_CPU_ERROR_STAT** (`0x048`):

For CRIME rev 1.1+:
```
Bit 2   CPU illegal address
Bit 1   VICE write parity error
Bit 0   CPU write parity error
```

For Petty Crime (rev 0):
```
Bit 9   CPU invalid address read
Bit 8   VICE illegal instruction
Bit 7   VICE SysAD parity
Bit 6   VICE SysCmd parity
Bit 5   VICE invalid address
Bit 4   CPU illegal instruction
Bit 3   CPU SysAD parity
Bit 2   CPU SysCmd parity
Bit 1   CPU invalid address write
Bit 0   CPU invalid register address
```

### 3.8 Memory Controller Registers

| Offset | Register | Width | Description |
|--------|----------|-------|-------------|
| `0x200` | CRM_MEM_CONTROL | 2 | ECC enable/replacement |
| `0x208` | CRM_MEM_BANK_CTRL(0) | 9 | Bank 0 configuration |
| `0x210` | CRM_MEM_BANK_CTRL(1) | 9 | Bank 1 configuration |
| `0x218` | CRM_MEM_BANK_CTRL(2) | 9 | Bank 2 configuration |
| `0x220` | CRM_MEM_BANK_CTRL(3) | 9 | Bank 3 configuration |
| `0x228` | CRM_MEM_BANK_CTRL(4) | 9 | Bank 4 configuration |
| `0x230` | CRM_MEM_BANK_CTRL(5) | 9 | Bank 5 configuration |
| `0x238` | CRM_MEM_BANK_CTRL(6) | 9 | Bank 6 configuration |
| `0x240` | CRM_MEM_BANK_CTRL(7) | 9 | Bank 7 configuration |
| `0x248` | CRM_MEM_REFRESH_CNTR | 11 | DRAM refresh counter |
| `0x250` | CRM_MEM_ERROR_STAT | 28 | Memory error status |
| `0x258` | CRM_MEM_ERROR_ADDR | 30 | Memory error address |
| `0x260` | CRM_MEM_ERROR_ECC_SYN | 32 | ECC syndrome bits |
| `0x268` | CRM_MEM_ERROR_ECC_CHK | 32 | ECC generated check bits |
| `0x270` | CRM_MEM_ERROR_ECC_REPL | 32 | ECC replacement bits |

**CRM_MEM_CONTROL** (`0x200`):
```
Bit 1   Use ECC replacement register
Bit 0   ECC enable (1 = enabled)
```

**CRM_MEM_BANK_CTRL(n)** (`0x208 + n*8`):
```
Bit 8     SDRAM size: 0 = 16 Mbit (32 MB bank), 1 = 64 Mbit (128 MB bank)
Bits [7:5] Reserved
Bits [4:0] Bank address compare bits
```

Address matching:
- **16 Mbit SDRAM**: bits [4:0] compared with physical address [29:25]
  → Each bank = 32 MB, base = (bank_addr << 25)
- **64 Mbit SDRAM**: bits [4:2] compared with physical address [29:27]
  → Each bank = 128 MB, base = (bank_addr << 25)

The kernel detects memory by writing test patterns and checking which banks
respond (from `IP32init.c`). 8 banks maximum, each 32 MB or 128 MB.

**CRM_MEM_ERROR_STAT** (`0x250`):
```
Bits [27:25]  Invalid address during RMW/write/read
Bit  24       ECC error during RMW
Bit  23       Memory ECC read error
Bit  22       Multiple hard errors
Bit  21       Hard error
Bit  20       Soft error
Bit  18       CPU access
Bit  17       VICE access
Bit  16       GBE access
Bit  15       RE access
Bits [14:8]   RE source ID
Bit  7        MACE access
Bits [6:0]    MACE source ID
```

---

## 4. CRIME Rendering Engine

> Sources: `crimereg.h` (PROM, complete register typedefs),
> CRIME spec ch.7 (rendering engine), `crime_gfx.h` (kernel),
> `IP32.h`:327-342 (texture TLB constants)

Base address: `0x15000000`

The CRIME Rendering Engine (RE) provides hardware-accelerated 2D and basic 3D
rendering. It operates on tile-based framebuffers stored in main memory via
TLB translation.

### 4.1 RE Address Space

| Offset | Size | Access | Subsystem |
|--------|------|--------|-----------|
| `0x0000` | 4 KB | Kernel | Interface Buffer |
| `0x1000` | 4 KB | Kernel | TLB |
| `0x2000` | 4 KB | User | Pixel Pipeline |
| `0x3000` | 4 KB | User | MTE (Memory Transfer Engine) |
| `0x4000` | 4 KB | User | Status |

### 4.2 TLB Registers (`0x15001000`)

All TLB entries are 64-bit, mapping 64 KB tiles (or 4 KB pages for linear).

| Offset | Count | Description |
|--------|-------|-------------|
| `+0x000` | 64 | Framebuffer TLB A (`fbA[64]`) |
| `+0x200` | 64 | Framebuffer TLB B (`fbB[64]`) |
| `+0x400` | 64 | Framebuffer TLB C (`fbC[64]`) |
| `+0x600` | 28 | Texture TLB (`tex[28]`) |
| `+0x6E0` | 4 | Clip ID TLB (`cid[4]`) |
| `+0x700` | 16 | Linear TLB A (`linearA[16]`) — 4 KB pages |
| `+0x780` | 16 | Linear TLB B (`linearB[16]`) — 4 KB pages |

Each TLB entry: `{u_short taddr[4]}` — four 16-bit tile addresses packed
into one 64-bit doubleword. Each `taddr` is a physical page/tile number.

### 4.3 Interface Buffer (`0x15000000`)

64-entry FIFO for queuing rendering commands:

| Offset | Count | Description |
|--------|-------|-------------|
| `+0x000` | 64 | Data RAM — 64-bit entries |
| `+0x200` | 64 | Address RAM — 64-bit entries |
| `+0x400` | 1 | Control register |
| `+0x410` | 1 | Reset register |

Control register fields:
```
Bits [27:21]  FIFO full level
Bits [20:14]  FIFO empty level
Bits [13:7]   Stall level
Bits [6:0]    Stall count
```

### 4.4 Pixel Pipeline Registers (`0x15002000`)

| Offset | Register | Type | Description |
|--------|----------|------|-------------|
| `+0x000` | BufMode.src | Global | Source buffer format (bufType, pixType, depth) |
| `+0x008` | BufMode.dst | Global | Dest buffer format |
| `+0x010` | ClipMode | Global | Window clip enables (5 screen masks, CID) |
| `+0x018` | DrawMode | Global | Pipeline stage enables |
| `+0x020` | ScrMask[0] | Global | Screen mask 0 (xmin, ymin, xmax, ymax) |
| `+0x028` | ScrMask[1] | Global | Screen mask 1 |
| `+0x030` | ScrMask[2] | Global | Screen mask 2 |
| `+0x038` | ScrMask[3] | Global | Screen mask 3 |
| `+0x040` | ScrMask[4] | Global | Screen mask 4 |
| `+0x048` | Scissor | Global | GL scissor rectangle |
| `+0x050` | WinOffset.src | Global | Source window origin offset |
| `+0x058` | WinOffset.dst | Global | Dest window origin offset |
| `+0x060` | Primitive | Non-Global | Geometric primitive opcode |
| `+0x070` | Vertex.X[0-2] | Non-Global | X-format vertices (16.16 fixed) |
| `+0x080` | Vertex.GL[0-2] | Non-Global | GL-format vertices (13.6 fixed) |
| `+0x0A0` | PixelXfer.src | — | Pixel transfer source addr/step |
| `+0x0B8` | PixelXfer.dst | — | Pixel transfer dest addr/stride |
| `+0x0C0` | Stipple.mode | — | Line/poly stipple mode |
| `+0x0C8` | Stipple.pattern | — | 32-bit stipple pattern |
| `+0x0D0` | Shade.fgColor | Non-Global | Foreground / flat-shade color |
| `+0x0D8` | Shade.bgColor | Global | Background color (opaque stipple) |
| `+0x0E0` | Shade.r0,g0,b0,a0 | — | Color components (fixed-point) |
| `+0x100` | Shade.drdx..dady | — | Color gradients |
| `+0x110` | Texture.mode | Global | Texture enable, filter, wrap, func |
| `+0x118` | Texture.format | Global | Texture coordinate format |
| `+0x120` | Texture.sq0..dqdy | — | Texture coordinates & gradients |
| `+0x168` | Texture.borderColor | Global | Texture border color |
| `+0x170` | Fog.color | Global | Fog RGB color |
| `+0x178` | Fog.f0,dfdx,dfdy | — | Fog factor and gradients |
| `+0x190` | Antialias | — | Line antialiasing parameters |
| `+0x198` | AlphaTest | Global | Alpha test function and reference |
| `+0x1A0` | Blend.constColor | Global | Blend constant RGBA |
| `+0x1A8` | Blend.func | Global | Blend src/dst/op functions |
| `+0x1B0` | LogicOp | Global | Logic operation (4-bit op) |
| `+0x1B8` | ColorMask | Global | Per-channel color write mask |
| `+0x1C0` | Depth.mode | Global | Depth test function, tag clear |
| `+0x1C8` | Depth.z0 | — | Depth value (64-bit) |
| `+0x1D0` | Depth.dzdx | — | Depth X gradient |
| `+0x1D8` | Depth.dzdy | — | Depth Y gradient |
| `+0x1E0` | Stencil.mode | Global | Stencil test func/ops/ref/mask |
| `+0x1E8` | Stencil.mask | — | Stencil write mask |

"Global" registers: not updated until pipeline flush.
"Non-Global" registers: take effect immediately.

**DrawMode Register** enables individual pipeline stages:
```
Bit 23  enNoConflict      Bit 15  enShade
Bit 22  enGL              Bit 14  enTexture
Bit 21  enPixelXfer       Bit 13  enFog
Bit 20  enScissorTest     Bit 12  enCoverage
Bit 19  enLineStipple     Bit 11  enAntialiasLine
Bit 18  enPolyStipple     Bit 10  enAlphaTest
Bit 17  enOpaqStipple     Bit 9   enBlend
Bit 16  enShade           Bit 8   enLogicOp
Bit 7   enDither          Bit 3   enDepthTest
Bit 6   enColorMask       Bit 2   enDepthMask
Bits [5:2] enColorByteMask Bit 1  enStencilTest
```

### 4.5 MTE — Memory Transfer Engine (`0x15003000`)

Block copy/fill engine for memory operations:

| Offset | Register | Description |
|--------|----------|-------------|
| `+0x000` | mode | Op code, stipple, pixel depth, buf types, ECC |
| `+0x008` | byteMask | Per-byte write mask |
| `+0x010` | stippleMask | Stipple pattern for fill |
| `+0x018` | fgValue | Fill value |
| `+0x020` | src0 | Source start address |
| `+0x028` | src1 | Source end address |
| `+0x030` | dst0 | Dest start address |
| `+0x038` | dst1 | Dest end address |
| `+0x040` | srcYStep | Source Y stride |
| `+0x048` | dstYStep | Dest Y stride |

The kernel uses MTE for fast memory zeroing (ECC init) via `early_mte_zero()`.

### 4.6 Status Register (`0x15004000`)

```
Bit  28     RE idle
Bit  27     Setup idle
Bit  26     Pixel pipe idle
Bit  25     MTE idle
Bits [24:18] Interface buffer level (0-64)
Bits [17:12] Interface buffer read pointer
Bits [11:6]  Interface buffer write pointer
Bits [5:0]   Interface buffer start pointer
```

---

## 5. MACE Register Map

> Sources: `mace.h`:11-148 (kernel & PROM — identical),
> `mace_regs.h`:46-74 (PROM, subsystem offsets),
> MACE spec (complete register definitions),
> MAME `mace.cpp` (ISA + UST/MSC stubs)

Base address: `0x1F000000` (`MACE_BASE`)

### 5.1 MACE Subsystem Base Addresses

| Physical Address | Offset | Subsystem |
|-----------------|--------|-----------|
| `0x1F000000` | `+0x000000` | (Reserved / future) |
| `0x1F080000` | `+0x080000` | PCI Interface |
| `0x1F084000` | `+0x084000` | PCI Configuration Space |
| `0x1F100000` | `+0x100000` | Video Input 1 |
| `0x1F180000` | `+0x180000` | Video Input 2 |
| `0x1F200000` | `+0x200000` | Video Output |
| `0x1F280000` | `+0x280000` | Ethernet (MAC110) |
| `0x1F300000` | `+0x300000` | Audio |
| `0x1F310000` | `+0x310000` | ISA DMA Internal |
| `0x1F320000` | `+0x320000` | Keyboard (PS/2) |
| `0x1F320020` | `+0x320020` | Mouse (PS/2) |
| `0x1F330000` | `+0x330000` | I2C Controller |
| `0x1F340000` | `+0x340000` | UST/MSC Timers |
| `0x1F350000` | `+0x350000` | Compare 1 (alias) |
| `0x1F360000` | `+0x360000` | Compare 2 (alias) |
| `0x1F370000` | `+0x370000` | Compare 3 (alias) |
| `0x1F380000` | `+0x380000` | ISA Controller |
| `0x1F384000` | `+0x384000` | Parallel Port (EPP/ECP) |
| `0x1F388000` | `+0x388000` | Serial Port 1 (console) |
| `0x1F38C000` | `+0x38C000` | Serial Port 2 |
| `0x1F3A0000` | `+0x3A0000` | RTC (DS17287) |
| `0x1F3B0000` | `+0x3B0000` | Game Port |
| `0x1FC00000` | `+0xC00000` | PROM (Flash) |

### 5.2 ISA Controller (`0x1F310000`)

> Sources: `mace.h`:80-101, `IP32misc.c` (LED control), MAME `mace.cpp`

| Offset | Register | R/W | Description |
|--------|----------|-----|-------------|
| `+0x0000` | ISA_RINGBASE | R/W | ISA ring base address and reset |
| `+0x0008` | ISA_FLASH_NIC_REG | R/W | Flash/LED/NIC control |
| `+0x0010` | ISA_INT_STS_REG | R/W | ISA interrupt status |
| `+0x0018` | ISA_INT_MSK_REG | R/W | ISA interrupt mask |

**ISA_FLASH_NIC_REG** bits:
```
Bit 0   ISA_FLASH_WE      Flash write enable
Bit 1   ISA_PWD_CLEAR      Password clear jumper detected (read-only)
Bit 2   ISA_NIC_DEASSERT   DS2502 NIC control
Bit 3   ISA_NIC_DATA       DS2502 NIC data
Bit 4   ISA_LED_RED        Red LED (1 = illuminated)
Bit 5   ISA_LED_GREEN      Green LED (1 = illuminated)
Bit 6   ISA_DP_RAM_ENABLE  Dual-port RAM enable
```

**ISA_INT_STS_REG** / **ISA_INT_MSK_REG**:
```
Bit 8   ISA_INT_RTC_IRQ    RTC interrupt
(Other bits for serial, parallel, audio, keyboard, etc.)
```

### 5.3 Ethernet — MAC110 (`0x1F280000`)

> Sources: MACE spec ch.5 (Ethernet), `mace_regs_ether.h` (stub)

**MAC Control Register** (`+0x000`):
```
Bits [31:29]  Implementation revision (read-only, default 1)
Bits [28:22]  Inter-packet gap IPGR2
Bits [21:15]  Inter-packet gap IPGR1
Bits [14:8]   Inter-packet gap IPGT
Bit  7        Link failure enable
Bits [6:5]    Dest address filter mode:
              0 = station address only
              1 = station + broadcast + multicast filter
              2 = station + broadcast + all multicast
              3 = promiscuous
Bit  4        M10T/MII select (0 = MII, 1 = SIA)
Bit  3        100/10 Mbit (0 = 10M, 1 = 100M)
Bit  2        Loopback
Bit  1        Full duplex
Bit  0        Core reset (1 = active, default 1)
```

**Interrupt Status Register** (`+0x004`):
```
Bit  30       Multicast hash output (debug)
Bits [29:25]  RX sequence number
Bits [24:16]  TX ring read pointer
Bits [12:8]   RX mcl FIFO read pointer
Bit  7        RX DMA FIFO overflow (fatal)
Bit  6        RX cluster FIFO underflow
Bit  5        RX threshold interrupt
Bit  4        TX abort (fatal)
Bit  3        TX CRIME memory error (fatal)
Bit  2        TX link failure
Bit  1        TX packet user request
Bit  0        TX ring empty
```
Write 1 to clear interrupt bits.

**DMA Control Register** (`+0x008`):
```
Bit  15       RX DMA enable
Bits [14:12]  RX DMA starting offset (64-bit word index)
Bit  11       RX packet gathering enable
Bit  10       RX runt packets enable
Bit  9        RX interrupt enable
Bits [8:4]    RX interrupt threshold (FIFO watermark)
Bits [3:2]    TX ring size: 00=8K, 01=16K, 10=32K, 11=64K
Bit  1        TX DMA enable
Bit  0        TX interrupt enable (ring empty)
```

**Interrupt Delay Register** (`+0x00C`):
```
Bits [5:0]  Delay in 30.69µs increments (0-2ms range)
```

**TX Ring Buffer**: Circular buffer in system memory. Hardware reads from
ring read pointer, software advances write pointer. Empty when read==write.

**RX Message Cluster FIFO**: 16-entry FIFO of 4 KB buffer base addresses.
Upper 20 bits stored (4 KB aligned). Hardware pops, software pushes.
Separate read/write index registers.

### 5.4 Audio — AD1843 Codec (`0x1F300000`)

> Sources: MACE spec ch.6 (Audio), `mace_regs_audio.h` (stub)

MACE audio uses DMA ring buffers for AD1843 codec access:
- Stereo 16-bit ADC/DAC
- Codec registers accessed via serial interface through MACE
- DMA ring buffers in system memory for continuous audio streaming

### 5.5 PCI Host Bridge (`0x1F080000`)

> Sources: `mace.h`:30-62 (PCI registers and error flags),
> MACE spec ch.3 (PCI bridge), `IP32init.c` (PCI init, phantom read WAR)

| Offset | Register | R/W | Description |
|--------|----------|-----|-------------|
| `+0x000` | PCI_ERROR_ADDR | R | PCI error address |
| `+0x004` | PCI_ERROR_FLAGS | R/W | PCI error flags |
| `+0x008` | PCI_CONTROL | R/W | PCI control |
| `+0x00C` | PCI_REV_INFO / PCI_FLUSH | R/W | Revision (R) / Flush (W) |
| `+0xCF8` | PCI_CONFIG_ADDR | W | PCI config address (Type 0/1) |
| `+0xCFC` | PCI_CONFIG_DATA | R/W | PCI config data |

**PCI_ERROR_FLAGS** bits:
```
Bit 31  Master abort          Bit 23  Overrun
Bit 30  Target abort          Bit 21  Memory address valid
Bit 29  Data parity error     Bit 20  Config address valid
Bit 28  Retry error           Bit 19  Master abort addr valid
Bit 27  Illegal command       Bit 18  Target abort addr valid
Bit 26  System error          Bit 17  Data parity addr valid
Bit 25  Interrupt test        Bit 16  Retry addr valid
Bit 24  Parity error
```

**PCI Address Spaces:**
```
PCI Low I/O:     0x18000000  (CPU physical)
PCI Low Memory:  0x1A000000  (CPU physical)
PCI Configuration: 0x1C000000
PCI High Memory: 0x280000000 (36-bit, requires TLB)
PCI High I/O:    0x100000000 (36-bit, requires TLB)
PCI Native View: 0x40000000  (PCI bus perspective)
```

5 PCI masters: 2× SCSI + 3 expansion slots. Shared interrupt wiring:
```
SHARED0 = slot 0 int B, slot 1 int C, slot 2 int D
SHARED1 = slot 0 int C, slot 1 int D, slot 2 int B
SHARED2 = slot 0 int D, slot 1 int B, slot 2 int C
```

**Phantom read bug**: The kernel wraps all MACE PIO accesses through
special functions due to a known silicon bug.

### 5.6 PS/2 Keyboard & Mouse (`0x1F320000`)

> Sources: MACE spec ch.7 (Keyboard/Mouse), `mace_regs_keyboard.h` (stub),
> `mace_regs_kybdms.h` (stub)

Two identical PS/2 controller blocks at 32-byte spacing:

| Offset | Register | R/W | Description |
|--------|----------|-----|-------------|
| `+0x00` | TX Buffer | W | 8-bit transmit data |
| `+0x04` | RX Buffer | R | 8-bit received data |
| `+0x08` | Control | R/W | TX start, RX reset |
| `+0x0C` | Status | R | RX full, TX empty, TX busy |

Keyboard base: `0x1F320000`
Mouse base: `0x1F320020`

**Control Register**:
```
Bit 0   TX start (write 1 to begin transmission)
Bit 1   RX reset (write 1 to reset receiver)
```

**Status Register**:
```
Bit 0   RX full (1 = data available)
Bit 1   TX empty (1 = ready for data)
Bit 2   TX busy (1 = transmission in progress)
```

### 5.7 UST/MSC Timers (`0x1F340000`)

> Sources: `mace.h`:104-120 (timer register offsets and period),
> `mvpregs.h`:682-701 (UST/MSC register types),
> MAME `mace.cpp` (UST 960ns tick, MSC 1ms tick)

Universal System Time (UST) and Media Stream Counter (MSC) for multimedia
synchronization.

| Offset | Register | Description |
|--------|----------|-------------|
| `+0x00` | MACE_UST | Global UST (free-running) |
| `+0x08` | MACE_COMPARE1 | Compare register 1 (interrupt) |
| `+0x10` | MACE_COMPARE2 | Compare register 2 (interrupt) |
| `+0x18` | MACE_COMPARE3 | Compare register 3 (interrupt) |
| `+0x20` | MACE_AIN_MSC_UST | Audio in MSC/UST pair |
| `+0x28` | MACE_AOUT1_MSC_UST | Audio out 1 MSC/UST pair |
| `+0x30` | MACE_AOUT2_MSC_UST | Audio out 2 MSC/UST pair |
| `+0x38` | MACE_VIN1_MSC_UST | Video in 1 MSC/UST pair |
| `+0x40` | MACE_VIN2_MSC_UST | Video in 2 MSC/UST pair |
| `+0x48` | MACE_VOUT_MSC_UST | Video out MSC/UST pair |

- **UST period**: 960 ns (increments every 960 ns)
- **MSC period**: 1 ms (media stream counter, lower 32 bits of paired read)
- Atomic 64-bit read returns MSC in bits [31:0] and UST in bits [63:32]
- Compare registers generate MACE interrupts when UST matches

Compare registers also accessible at aliased addresses:
`0x1F350000` (compare 1), `0x1F360000` (compare 2), `0x1F370000` (compare 3).

### 5.8 I2C Controller (`0x1F330000`)

> Sources: `mvpregs.h`:520-596 (I2C register structs and bit defines),
> `mace_regs_iic.h` (PROM I2C definitions)

| Offset | Register | Description |
|--------|----------|-------------|
| `+0x00` | Config | Reset, fast mode, clock/data override |
| `+0x10` | Control | Bus direction, hold, status bits |
| `+0x18` | Data | 8-bit data register |

**Config Register**:
```
Bit 0   Reset (1 = reset controller)
Bit 1   Fast mode (400 kHz vs 100 kHz)
Bit 2   Data override (force SDA)
Bit 3   Clock override (force SCL)
Bit 4   Data input (read SDA state)
Bit 5   Clock input (read SCL state)
```

**Control Register**:
```
Bit 0   Force idle / not idle
Bit 1   Bus direction (0 = write, 1 = read)
Bit 2   Hold bus (keep bus after byte)
Bit 4   Transfer busy (1 = in progress)
Bit 5   NACK status (1 = NACK received)
Bit 7   Bus error (1 = error detected)
```

---

## 6. MACE Interrupt Map

> Sources: `mace.h`:132-148, `IP32.h`:18-24, `IP32intr.c` (full dispatch),
> `crime.h`:94-114 (CRIME interrupt bit definitions)

MACE sources map directly to CRIME INTSTAT bits [15:0]:

| CRIME Bit | MACE Source | Kernel Constant |
|-----------|-------------|-----------------|
| 0 | Video Input 1 | `MACE_VID_IN_1` |
| 1 | Video Input 2 | `MACE_VID_IN_2` |
| 2 | Video Output | `MACE_VID_OUT` |
| 3 | Ethernet (MAC110) | `MACE_ETHERNET` |
| 4 | Serial / Parallel | `MACE_PERIPH_SERIAL` / `MACE_PERIPH_PARALLEL` |
| 5 | Peripheral Misc (power, RTC, keyboard) | `MACE_PERIPH_MISC` |
| 6 | Audio | `MACE_PERIPH_AUDIO` |
| 7 | PCI Bridge | `MACE_PCI_BRIDGE` |
| 8 | PCI SCSI 0 | `MACE_PCI_SCSI0` |
| 9 | PCI SCSI 1 | `MACE_PCI_SCSI1` |
| 10 | PCI Slot 0 | `MACE_PCI_SLOT0` |
| 11 | PCI Slot 1 | `MACE_PCI_SLOT1` |
| 12 | PCI Slot 2 | `MACE_PCI_SLOT2` |
| 13 | PCI Shared 0 | `MACE_PCI_SHARED0` |
| 14 | PCI Shared 1 | `MACE_PCI_SHARED1` |
| 15 | PCI Shared 2 | `MACE_PCI_SHARED2` |

### Interrupt Routing Summary

```
Device IRQ → MACE aggregation → CRIME INTSTAT[15:0] ──┐
GBE interrupts ─────────────→ CRIME INTSTAT[19:16] ──┤
CRIME/MEM errors ────────────→ CRIME INTSTAT[21:20] ──┤
RE interrupts ───────────────→ CRIME INTSTAT[27:22] ──┼→ CPU IP2 (Cause bit 10)
Software interrupts ─────────→ CRIME INTSTAT[30:28] ──┤
VICE interrupt ──────────────→ CRIME INTSTAT[31] ─────┘

R4000 Count/Compare timer ───→ CPU IP7 (Cause bit 15) — scheduling clock
```

The IRIX kernel uses `setcrimevector()` to register handlers for each of
the 32 CRIME interrupt sources. All are dispatched through a single CPU
interrupt pin (IP2). The kernel prioritizes by SPL level and supports
both istack and ithread execution models.

**MACE ISA sub-interrupts** (RTC, serial, parallel, keyboard, power button)
are aggregated within MACE and appear as the `MACE_PERIPH_MISC` and
`MACE_PERIPH_SERIAL` sources. The ISA interrupt status/mask registers
(`ISA_INT_STS_REG` / `ISA_INT_MSK_REG`) provide finer-grained control.

---

## 7. GBE Register Map

> Sources: `crime_gbe.h`:21-188 (kernel & PROM — struct gbechip layout),
> GBE spec (complete register bitfields, video timing, tile format),
> `mvpregs.h`:420-513 (GBE video capture registers)

Base address: `0x16000000` (`GBECHIP_ADDR`)

GBE is a tile-based display controller. Instead of a contiguous framebuffer,
it reads pixel data from a list of tile numbers pointing to 64 KB tiles
scattered in main memory. This enables the UMA architecture — tiles are
allocated from the same SDRAM pool used by the CPU.

### 7.1 Control Registers

| Offset | Register | Width | Description |
|--------|----------|-------|-------------|
| `0x00000` | ctrlstat | 32 | Control/status, GPIO, sync polarity |
| `0x00004` | dotclock | 32 | Dot clock PLL parameters (M, N, P) |
| `0x00008` | i2c | 32 | DDC I2C interface (SDA, SCL) |
| `0x0000C` | sysclk | 32 | System clock PLL control |
| `0x00010` | i2cfp | 32 | Flat panel I2C interface |
| `0x00014` | id | 32 | Device ID (reads `0x00000666`) |

**ctrlstat** register:
```
Bits [29:28]  pclksel: 00=ext TTL, 01=ext diff, 11=internal PLL
Bit  27       csync_polarity (1 = active low)
Bit  26       half_phase (flat panel clock phase)
Bits [25:6]   io[0-9] — 10 GPIO pairs (data + output enable), reset = 0x3FF
Bit  5        reserved
Bit  4        sense_n — monitor sense input (read-only)
Bits [3:0]    chipid — chip revision
```

**dotclock** register:
```
Bits [7:0]    M — PLL multiplier
Bits [13:8]   N — input divider
Bits [15:14]  P — output post-scaler
Bit  22       oor — PLL out of range (status)
Bit  23       ool — PLL out of lock (status)
```

Dot clock frequency = (M / N) × reference / 2^P.

### 7.2 Video Timing Registers (`0x10000`-`0x1004C`)

All timing values use 12-bit on/off pairs for horizontal (X) and vertical (Y)
counters. `vt_freeze` (bit 31 of VT_0) stops the counters; clear to start.

| Offset | Register | Description |
|--------|----------|-------------|
| `0x10000` | vt_xy | Current dot position (X[11:0], Y[23:12]), freeze[31] |
| `0x10004` | vt_xymax | Max dot position (total frame size) |
| `0x10008` | vt_vsync | Vsync on/off Y positions |
| `0x1000C` | vt_hsync | Hsync on/off X positions |
| `0x10010` | vt_vblank | Vblank on/off Y positions |
| `0x10014` | vt_hblank | Hblank on/off X positions |
| `0x10018` | vt_flags | Sync polarity, DPMS control |
| `0x1001C` | vt_f2rf_lock | Stereo sync / framelock Y coords |
| `0x10020` | vt_intr01 | Interrupt 0/1 Y positions |
| `0x10024` | vt_intr23 | Interrupt 2/3 Y positions |
| `0x10028` | fp_hdrv | Flat panel H drive on/off |
| `0x1002C` | fp_vdrv | Flat panel V drive on/off |
| `0x10030` | fp_de | Flat panel data enable on/off |
| `0x10034` | vt_hpixen | Internal H pixel enable on/off |
| `0x10038` | vt_vpixen | Internal V pixel enable on/off |
| `0x1003C` | vt_hcmap | CMAP write enable (horizontal) |
| `0x10040` | vt_vcmap | CMAP write enable (vertical) |
| `0x10044` | did_start_xy | DID reset values (EOL/EOF) |
| `0x10048` | crs_start_xy | Cursor reset values (EOL/EOF) |
| `0x1004C` | vc_start_xy | Video capture reset values |

**vt_flags** (`0x10018`):
```
Bit 0  vt_vdrv_invert   Invert vsync output
Bit 1  vt_vdrv_low      Force vsync low (DPMS)
Bit 2  vt_hdrv_invert   Invert hsync output
Bit 3  vt_hdrv_low      Force hsync low (DPMS)
Bit 4  vt_sync_high     Force composite sync high
Bit 5  vt_sync_low      Force composite sync low
Bit 6  vt_f2rf_high     Force stereo sync high
```

GBE generates 4 programmable interrupts (→ CRIME bits 16-19) based on
`vt_intr0` through `vt_intr3` Y-position comparisons. Typically used for
VBLANK retrace interrupts.

### 7.3 Overlay Channel (`0x20000`)

| Offset | Register | Description |
|--------|----------|-------------|
| `0x20000` | ovr_width_tile | Overlay buffer width in tiles |
| `0x20004` | ovr_control | Tile list pointer (physical addr) + DMA enable |

### 7.4 Frame Channel (`0x30000`)

| Offset | Register | Description |
|--------|----------|-------------|
| `0x30000` | frm_size_tile | Framebuffer size in tiles (H/W) |
| `0x30004` | frm_size_pixel | Framebuffer size in pixels (H/W) |
| `0x30008` | frm_control | Tile list pointer + DMA enable |

### 7.5 DID Channel (`0x40000`)

| Offset | Register | Description |
|--------|----------|-------------|
| `0x40000` | did_control | DID table pointer + DMA enable |

### 7.6 Mode Registers (`0x48000`)

```
mode_regs[32]  — 32 XMAP-like mode registers
```

Mode register bits:
```
Bit 0   GBEWT_BUF_BOT     Buffer bottom select
Bit 1   GBEWT_BUF_TOP     Buffer top select
Bits [4:2] GBE_WID_TYPE   Visual type:
           1 = I12 (12-bit indexed)
           4 = RGB5 (5-5-5 RGB)
           5 = RGB8 (8-8-8 RGB)
```

### 7.7 CMAP — Color Palette (`0x50000`)

```
cmap[4608]  — 4608 × 32-bit CMAP entries
```

Overlay CMAP starts at offset `0x1100` within the array.

**cm_fifo** (`0x58000`): Number of empty slots in CMAP write FIFO.
Wait until `cm_fifo & 0x3F >= N` before writing N entries.

### 7.8 GMAP — Gamma Ramp (`0x60000`)

```
gmap[256]  — 256 × 32-bit gamma correction entries
```

### 7.9 Cursor (`0x70000`)

| Offset | Register | Description |
|--------|----------|-------------|
| `0x70000` | crs_pos | Cursor X/Y position |
| `0x70004` | crs_ctl | Cursor enable, crosshair enable |
| `0x70008` | crs_cmap[0] | Cursor color 1 |
| `0x7000C` | crs_cmap[1] | Cursor color 2 |
| `0x70010` | crs_cmap[2] | Cursor color 3 |
| `0x78000` | crs_glyph[64] | 32×32 cursor glyph (2 bpp) |

### 7.10 Video Capture (`0x80000`)

| Offset | Register | Description |
|--------|----------|-------------|
| `0x80000` | vc_lr | Capture window X coords (left/right) |
| `0x80004` | vc_tb | Capture window Y coords (top/bottom) |
| `0x80008` | vc_filters | Capture filter settings |
| `0x8000C` | vc_control | Capture control / DMA desc pointer |

### 7.11 Tile Format

GBE uses tiled memory for efficient 2D access patterns:

| Depth | Tile Size | Pixels |
|-------|-----------|--------|
| 8 bpp | 64 KB | 512 × 128 |
| 16 bpp | 64 KB | 256 × 128 |
| 32 bpp | 64 KB | 128 × 128 |

**Tile List Pointer Format** (in `frm_control`, `ovr_control`, `did_control`):
The control registers contain a physical address pointing to a tile list
in memory. The tile list is an array of 16-bit tile numbers. Each tile
number is multiplied by 64 KB to get the physical address of pixel data.

Framebuffer tile list: up to 80 entries (`fblist[80]`).
Overlay tile list: up to 32 entries (`ovlist[32]`).
DID tiles: triple-buffered, 3 tiles (`N_GBE_DID_TILES = 3`).

---

## 8. VICE — Video/Image Compression Engine

> Sources: VICE spec (MSP/BSP architecture, register map, DMA engine),
> `mvpregs.h`:41-101 (video channel control/status registers),
> `mvpregs.h`:204-413 (DMA descriptors, I/O channel register structs),
> `mooseaddr.h`:19 (`vice_regbase`)

Base address: `0x17000000`

VICE contains two embedded processors and DMA engines for video/image
processing. **Non-essential for initial boot** — can be fully stubbed.

### 8.1 Architecture

- **MSP** (Media Signal Processor): Modified MIPS core with 128-bit SIMD
  extensions, 4 KB instruction memory, 4 KB data memory
- **BSP** (Bitstream Processor): 16-bit MIPS-like core with hardware
  Huffman acceleration for JPEG/MPEG
- **DMA Engine**: Multi-channel with 2D stride support, TLB for tile
  addressing
- Not cache-coherent — operates as a stream processor

### 8.2 VICE Register Map

| Offset | Register | Description |
|--------|----------|-------------|
| `0x000` | VICE_ID | Chip ID & revision |
| `0x008` | VICE_CFG | General configuration |
| `0x010` | VICE_INT_RESET | Interrupt reset |
| `0x018` | VICE_INT | Interrupt status |
| `0x020` | VICE_INT_EN | Interrupt enable |
| `0x028` | BSP_SW_INT | BSP software interrupt |
| `0x030` | MSP_SW_INT | MSP software interrupt |
| `0x038` | MSP_D_RAM | Data RAM arbitration |
| `0x040` | MSP_CTL_STAT | MSP control & status |
| `0x048` | MSP_PC | MSP program counter |
| `0x050` | MSP_BadAddr | MSP bad address |
| `0x058` | MSP_WatchPoint | MSP watchpoint |
| `0x060` | MSP_EPC | MSP exception PC |
| `0x068` | MSP_CAUSE | MSP exception cause |
| `0x070` | MSP_ExcpFlag | MSP exception flags |
| `0x078` | VICEMSP_COUNT | MSP free-running counter |
| `0x080` | BSP_CTL_STAT | BSP control & status |
| `0x088` | BSP_WatchPoint | BSP watchpoint |
| `0x090` | BSP_IN_COUNT | BSP input bit counter |
| `0x098` | BSP_OUT_COUNT | BSP output bit counter |
| `0x0A0` | BSP_IN_BOX | BSP input mailbox |
| `0x0A8` | BSP_OUT_BOX | BSP output mailbox |
| `0x0B0` | HST_BSP_IN_BOX | Host snoop of BSP input |
| `0x0B8` | HST_BSP_OUT_BOX | Host snoop of BSP output |
| `0x0C0` | BSP_PC | BSP program counter |
| `0x0C8` | BSP_EPC | BSP exception PC |
| `0x0D0` | BSP_HALT_RESET | BSP halt & reset |
| `0x0D8` | BSP_CAUSE | BSP exception cause |
| `0x0E0` | BSP_FIFO_CTL_STAT | BSP FIFO control |
| `0x0E8` | BSP_AVALID_BITS | BSP A FIFO valid bits |
| `0x0F0` | BSP_FVALID_BITS | BSP F FIFO valid bits |
| `0x100`+ | DMA_CTL_CH1... | DMA channel registers |

### 8.3 Video I/O Channels (MACE)

Video Input 1 (`0x1F100000`) and Video Input 2 (`0x1F180000`):
```
+0x00  Control (DMA enable, interrupt enables)
+0x08  Status (interrupt flags, sync detect)
+0x10  Input config (format, precision, source, memory mode)
+0x18  Next DMA descriptor
+0x20  Field offset
+0x28  Line width
+0x30  H clip odd / +0x48 H clip even
+0x38  V clip odd / +0x50 V clip even
+0x40  Alpha odd  / +0x58 Alpha even
+0x80  DMA descriptor table (32 entries)
```

Video Output (`0x1F200000`):
```
+0x00  Control
+0x08  Status
+0x10  Output config (format, genlock, clamp, notch filter)
+0x18  Next DMA descriptor
+0x20  Field offset
+0x28  Field size
+0x30  H pad odd / +0x40 H pad even
+0x38  V pad odd / +0x48 V pad even
+0x50  Genlock delay
+0x58  VHW config (MACE revision, AB/CD port selects, sync sources)
+0x80  DMA descriptor table (32 entries)
```

Video formats: RGBA32 (8888), RGBA16 (1555), YUV422, YUV422_10, ABGR32.
Memory modes: Linear, Tiled, 128 (512×128 mipmap), 256 (512×256 mipmap).

---

## 9. PROM Structure

> Sources: `ip32prom-decompiler/doc/reverse-engineering.md` (full reverse
> engineering writeup with SHDR format, checksums, subsections),
> `IP32.h`:91-94 (PROM RAM addresses), PROM source in `prom-building/`

The IP32 PROM is a 512 KB flash image containing 5 sections, each prefixed
by a 72-byte SHDR (Section Header).

### 9.1 SHDR Format (72 bytes)

| Offset | Size | Field |
|--------|------|-------|
| `0x00` | 8 | Entry instructions (branch over SHDR, or NOP for data) |
| `0x08` | 4 | Magic: `0x53484452` ("SHDR") |
| `0x0C` | 4 | Section length (bytes, includes checksum) |
| `0x10` | 1 | Name string length |
| `0x11` | 1 | Version string length |
| `0x12` | 1 | Section type (bit 0: code, bit 1: has subsections) |
| `0x13` | 1 | Padding |
| `0x14` | 32 | Name string (null-terminated, zero-padded) |
| `0x34` | 8 | Version string (null-terminated, zero-padded) |
| `0x3C` | 4 | SHDR checksum (two's complement of SHDR words) |
| `0x40` | 4 | Metadata 1 (load address for type 3, else 0) |
| `0x44` | 4 | Metadata 2 (.text length for type 3, else 0) |

### 9.2 Sections

| Offset | Name | Type | Version | Description |
|--------|------|------|---------|-------------|
| `0x00000` | sloader | 1 (code) | 1.0 | Initial loader, runs from flash |
| `0x04000` | env | 0 (data) | 1.0 | Environment variables (NVRAM mirror) |
| `0x04400` | post1 | 1 (code) | 1.0 | POST, memory init, cache init |
| `0x09200` | firmware | 3 (code+subsections) | 4.18 | Main firmware |
| `0x69200` | version | 0 (data) | 4.18 | ELF binary with version info |

### 9.3 Firmware Subsections

The `firmware` section (type 3) contains an internal header chain linking
three subsections. Each subsection has an 8-byte header: `{load_addr, length}`.
The chain terminates with a sentinel `{base_addr, 0}`.

| Subsection | Load Address | Length | Content |
|------------|-------------|--------|---------|
| `.text` | `0x81000000` | `0x48E70` | Executable code |
| `.rodata` | `0x81048E70` | `0x0B290` | Strings, jump tables |
| `.data` | `0x81054100` | `0x0BEE0` | Initialized read-write data |
| sentinel | `0x81000000` | `0x00000` | End marker |

The PROM copies `firmware` from flash to RAM at `0x81000000` (kseg0, cached)
during boot.

### 9.4 Checksums

Two's complement checksum: sum all 32-bit words in the region; the stored
checksum value makes the total sum equal to zero.

SHDR checksum covers bytes `0x08`-`0x3F` (magic through padding).
Section checksum covers bytes after SHDR through end of section (last word).

---

## 10. Boot Sequence

> Sources: `ip32prom-decompiler/doc/reverse-engineering.md` (sloader → post1 → firmware),
> PROM source `prom-building/` (sloader, post1, firmware flow),
> `IP32init.c` (kernel-side memory/PCI init),
> `IP32.h`:91-94 and :141-148 (RAM addresses, firmware dispatch codes)

### 10.1 PROM Boot Flow

```
CPU reset → 0xBFC00000 (kseg1, uncached flash)
  └→ sloader
       ├─ Minimal hardware init
       ├─ Verify section checksums
       └→ post1
            ├─ Cache initialization (I-cache, D-cache, S-cache)
            ├─ Memory detection and initialization
            │    ├─ Probe CRIME bank control registers
            │    ├─ Size SDRAM banks (32 MB or 128 MB each)
            │    └─ Initialize ECC via MTE zero-fill
            ├─ CRIME timer init
            ├─ POST diagnostics
            └─ Copy firmware section to RAM at 0x81000000
                 └→ firmware (running from cached RAM)
                      ├─ Serial console init (16550 UART)
                      ├─ ARCS callback table setup
                      ├─ SPB (System Parameter Block) init
                      ├─ Component tree construction
                      ├─ PCI bus enumeration (find SCSI controllers)
                      ├─ GBE graphics init (textport display)
                      ├─ RTC read
                      └─ System Maintenance Menu
                           ├─ "1) Start System"
                           ├─ "2) Install System Software"
                           ├─ "3) Run Diagnostics"
                           ├─ "4) Recover System"
                           └─ "5) Enter Command Monitor"
```

### 10.2 Disk Boot

1. PROM reads SGI volume header from SCSI disk (partition 8/10)
2. Locates boot file entry (usually `sash` or `sashARCS`)
3. Loads ELF32 binary into memory
4. Jumps to entry point
5. Secondary loader (`sash`) loads IRIX kernel (`/unix`)
6. Kernel takes over: configures CRIME interrupts, memory, drivers

### 10.3 Key PROM RAM Addresses

```
PROM_RAMBASE     = 0x81000000   (firmware load address, kseg0)
PROM_STACK       = 0xA1400000   (kseg1, uncached)
PROM_TILE_BASE   = 0xA1100000   (GBE tile allocation)
SYMMON_STACK     = 0x80006000   (symmon debug stack)
RESTART_ADDR     = 0x80000400   (warm restart vector)
```

---

## 11. Serial Console

> Sources: `IP32.h`:114-117 (port base addresses, clock frequency, port count),
> `mace.h`:70-71 (ISA_SER1_BASE, ISA_SER2_BASE),
> `mace_regs.h`:71-72 (MACE_OFFSET_SER1/SER2),
> `ds17287.h`:18-23 (DS_REG_STRIDE = 256 for IP32)

### 11.1 Hardware

- **Chip**: 16550 UART (standard PC-compatible)
- **Location**: MACE ISA External, two ports
- **Port 1 (console)**: `0x1F390000` (ISA_SER1_BASE + 7)
- **Port 2**: `0x1F398000` (ISA_SER2_BASE + 7)
- **Clock**: 1,843,200 Hz (`SERIAL_CLOCK_FREQ`)
- **Register stride**: 256 bytes (IP32 `DS_REG_STRIDE = 256`)
- **Byte offset**: +7 within each 8-byte doubleword (big-endian alignment)

### 11.2 Register Access

The 16550 registers are at the standard offsets (0-7) but with 256-byte
spacing due to MACE's address decoding. The data byte sits at offset +7
within each 8-byte-aligned doubleword (big-endian byte lane).

Physical address of register N for serial port 1:
```
0x1F380000 + 0x10000 + 7 + (N × 256)
= 0x1F390007 + (N × 0x100)
```

In kseg1: `0xBF390007 + (N × 0x100)`

Standard 16550 registers: RBR/THR, IER, IIR/FCR, LCR, MCR, LSR, MSR, SCR.

Default baud: 9600. PROM configures based on `dbaud` NVRAM variable.

---

## 12. SCSI

> Sources: `mace.h`:142-143 (MACE_PCI_SCSI0/1 interrupt assignments),
> PROM source (PCI enumeration), `hinv` output from real O2 hardware

### 12.1 Hardware

- **Chip**: 2× Adaptec AIC-7880 (Wide Ultra SCSI)
- **Bus**: PCI (behind MACE PCI bridge)
- **SCSI 0**: MACE interrupt bit 8 (`MACE_PCI_SCSI0`)
- **SCSI 1**: MACE interrupt bit 9 (`MACE_PCI_SCSI1`)

### 12.2 Emulation

Use QEMU's existing `aic7xxx` PCI SCSI controller emulation. Place two
instances on the PCI bus at the appropriate slots. The PROM and IRIX kernel
use standard PCI configuration space discovery to find them.

---

## 13. RTC — DS17287

> Sources: `ds17287.h`:18-170 (register layout, stride, bit definitions),
> `IP32.h`:75-108 (RT_CLOCK_ADDR, NVRAM offsets),
> `IP32clock.c` (RTC init, BCD time, century, power-on alarm)

### 13.1 Hardware

- **Chip**: Dallas DS17287 (or DS1687-5) real-time clock with NVRAM
- **Location**: `ISA_RTC_BASE = 0x1F3A0000`
- **Access**: Byte at offset +7 for big-endian alignment

### 13.2 Register Layout

Standard DS17287 registers with IP32-specific stride of 256 bytes:

| Index | Register | Description |
|-------|----------|-------------|
| 0 | Seconds | BCD seconds (0-59) |
| 1 | Seconds Alarm | Alarm seconds |
| 2 | Minutes | BCD minutes (0-59) |
| 3 | Minutes Alarm | Alarm minutes |
| 4 | Hours | BCD hours (0-23) |
| 5 | Hours Alarm | Alarm hours |
| 6 | Day of Week | 1-7 |
| 7 | Date | BCD date (1-31) |
| 8 | Month | BCD month (1-12) |
| 9 | Year | BCD year (0-99) |
| 10 | Register A | UIP, oscillator, rate select |
| 11 | Register B | SET, interrupt enables, data mode, 12/24hr |
| 12 | Register C | Interrupt flags (read to clear) |
| 13 | Register D | VRT (battery status) |
| 14+ | NVRAM | 114 bytes general-purpose RAM |

### 13.3 Register A
```
Bit 7   UIP — Update In Progress
Bit 6   DV2 — Countdown channel reset
Bit 5   DV1 — Oscillator enable (1 = enabled)
Bit 4   DV0 — Bank select (0 = bank 0, 1 = bank 1)
Bits [3:0]  RS — Rate select for periodic interrupt
```

### 13.4 Register B
```
Bit 7   SET — Inhibit update (1 = freeze time registers)
Bit 6   PIE — Periodic interrupt enable
Bit 5   AIE — Alarm interrupt enable
Bit 4   UIE — Update-ended interrupt enable
Bit 3   SQWE — Square wave enable
Bit 2   DM — Data mode (0 = BCD, 1 = binary)
Bit 1   24/12 — Hour mode (1 = 24-hour)
Bit 0   DSE — Daylight saving enable
```

### 13.5 Bank 1 (Extended) Registers

When Register A bit 4 (DV0) = 1, accessing offsets 0x40-0x7F maps to
Bank 1 extended registers:

| Offset | Register | Description |
|--------|----------|-------------|
| 0x48 | Century | BCD century (19, 20) |
| 0x49 | Date Alarm | Date alarm register |
| 0x4A | Extended Control A | VRT2, increment, BME, wake flags |
| 0x4B | Extended Control B | ABE, E32K, crystal select |
| 0x50 | Extended RAM Addr LSB | Indirect RAM address low |
| 0x51 | Extended RAM Addr MSB | Indirect RAM address high |
| 0x53 | Extended RAM Data | Indirect RAM data |

### 13.6 NVRAM Usage

The PROM stores environment variables in NVRAM. The DS17287 has 114 bytes
of direct NVRAM plus an extended bank accessible via indirect addressing.

Key kernel NVRAM offsets (from `IP32.h`):
```
RTC_SAVE_UST  = 37   (saved UST value, 4 bytes)
RTC_SAVE_REG  = 41   (8-byte register save area)
RTC_RESET_CTR = 49   (firmware reset counter)
```

The MAC address is stored on a separate Dallas DS2502 1-wire device,
accessed via the `ISA_NIC_DEASSERT` / `ISA_NIC_DATA` bits in
`ISA_FLASH_NIC_REG`.

---

## 14. Emulation Priority

> Sources: Analysis based on boot sequence dependencies and PROM/kernel
> code flow from all sources above

| Priority | Component | Needed For | Complexity |
|----------|-----------|------------|------------|
| **P0** | CRIME core (ID, control, timer, interrupts) | PROM boot | Medium |
| **P0** | CRIME memory controller (bank regs) | Memory detection | Low |
| **P0** | MACE ISA controller (int status/mask, LEDs) | PROM boot | Low |
| **P0** | MACE serial (16550 UART) | Console I/O | Low (reuse QEMU 16550) |
| **P0** | MACE RTC (DS17287) | PROM time/NVRAM | Low (reuse QEMU DS) |
| **P1** | GBE (timing, CMAP, tile DMA, cursor) | Graphics display | High |
| **P1** | MACE PCI bridge (config space, error regs) | SCSI discovery | Medium |
| **P1** | PCI AIC-7880 SCSI (×2) | Disk/CD boot | Low (reuse QEMU aic7xxx) |
| **P1** | CRIME RE (TLB, basic rendering) | Xsgi / 3D | High |
| **P2** | MACE Ethernet (MAC110) | Networking | Medium |
| **P2** | MACE Audio (AD1843 DMA) | Sound | Medium |
| **P2** | MACE PS/2 (keyboard/mouse) | Input | Low |
| **P2** | MACE I2C | DDC monitor detection | Low |
| **P2** | MACE UST/MSC timers | Multimedia sync | Low |
| **P3** | VICE (MSP + BSP) | Video compression | Very High |
| **P3** | MACE Video I/O | Camera/video capture | High |

### P0 Implementation Notes

**CRIME**: Return valid ID register (0xA1 for rev 1.1). Implement the 48-bit
timer at 66.67 MHz. Implement the 32-bit interrupt status/mask/soft/hard
registers with proper routing to CPU IP2. Implement 8 memory bank control
registers for PROM memory detection.

**MACE ISA**: Implement interrupt status/mask registers and the flash/NIC/LED
control register. The PROM reads the LED register and password-clear jumper.

**Serial**: Reuse QEMU's existing 16550 UART emulation, configured with
256-byte register stride and 1,843,200 Hz clock. Map at MACE offset.

**RTC**: Reuse QEMU's existing DS1287/DS17287 emulation. Note the 256-byte
register stride and big-endian byte alignment (+7 offset within doublewords).

### P1 Implementation Notes

**GBE**: The most complex display subsystem. Must implement tile-based
framebuffer DMA (reading tile lists from memory, fetching 64 KB tiles,
compositing to display). Video timing registers drive interrupt generation
for VBLANK. CMAP provides 4608-entry palette. Start with a simple
"read tile list, render tiles to framebuffer" approach.

**PCI Bridge**: Standard Type 0/1 configuration space access via the
0xCF8/0xCFC register pair. Error registers can be stubbed. Route
PCI device interrupts to MACE interrupt bits 7-15.

**SCSI**: Drop in two QEMU `aic7xxx` PCI devices. The PROM and kernel
discover them via standard PCI enumeration.
