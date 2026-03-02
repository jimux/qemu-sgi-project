# SGI Graphics Architectures: Comparative Analysis

This document analyzes the graphics architectures across SGI's workstation and
visualization product lines, identifies platform-specific features, and evaluates
the feasibility of a universal virtual graphics interface that can run software
from any SGI platform.

## Contents

1. [The IRIX Graphics Abstraction Layer](#the-irix-graphics-abstraction-layer)
2. [Architecture Profiles](#architecture-profiles)
3. [Architecture Comparison Table](#architecture-comparison-table)
4. [GL Pipeline Paths](#gl-pipeline-paths)
5. [Platform-Specific Features](#platform-specific-features)
6. [The Universal Interception Problem](#the-universal-interception-problem)
7. [Recommendation: Intercept at libGLcore.so](#recommendation-intercept-at-libglcoreso)
8. [Source References](#source-references)

---

## The IRIX Graphics Abstraction Layer

Every SGI graphics board -- from the entry-level Newport to the InfiniteReality
visualization system -- implements the same kernel-level interface. This is
IRIX's equivalent of Linux DRI/DRM.

### gfx_fncs: The Universal Vtable

Defined in `sys/gfx.h:281-332`, the `struct gfx_fncs` vtable contains 27
function pointers that every graphics driver must implement:

```c
struct gfx_fncs {
    /* Board lifecycle */
    int (*gf_Info)(struct gfx_data *, void *, unsigned int, int *);
    int (*gf_Attach)(struct gfx_gfx *, caddr_t);
    int (*gf_Detach)(struct gfx_gfx *);
    int (*gf_Initialize)(struct gfx_gfx *);
    int (*gf_Download)(struct gfx_gfx *, struct gfx_download_args *);
    int (*gf_Start)(struct gfx_gfx *);
    int (*gf_PositionCursor)(struct gfx_data *, int, int);
    int (*gf_Exit)(struct gfx_gfx *);

    /* Rendering Resource Manager (RRM) */
    int (*gf_CreateDDRN)(struct gfx_data *, struct rrm_rnode *);
    int (*gf_DestroyDDRN)(struct gfx_data *, struct gfx_gfx *, struct rrm_rnode *);
    int (*gf_ValidateClip)(struct gfx_gfx *, struct rrm_rnode *,
                           struct rrm_rnode *, struct RRM_ValidateClip *);
    int (*gf_SetNullClip)(struct RRM_ValidateClip *);
    int (*gf_MapGfx)(struct gfx_gfx *, __psunsigned_t, int);
    int (*gf_UnMapGfx)(struct gfx_gfx *);
    int (*gf_InvalTLB)(struct gfx_gfx *);
    int (*gf_PcxSwap)(struct gfx_data *, struct rrm_rnode *,
                      struct rrm_rnode *, struct rrm_rnode *);
    int (*gf_PcxSwitch)(struct gfx_data *, struct rrm_rnode *,
                        struct rrm_rnode *);
    int (*gf_SchedSwapBuf)(struct gfx_data *, struct rrm_rnode *, int, int);
    int (*gf_UnSchedSwapBuf)(struct gfx_gfx *, struct rrm_rnode *, int);
    int (*gf_SchedRetraceEvent)(struct gfx_data *, struct rrm_rnode *);
    int (*gf_SetDisplayMode)(struct gfx_gfx *, int, unsigned int);
    int (*gf_SchedMGRSwapBuf)(struct gfx_gfx *, int, int, int, int);
    int (*gf_Suspend)(struct gfx_data *, struct gfx_gfx *, int);
    int (*gf_Resume)(struct gfx_data *, struct gfx_gfx *, int);
    int (*gf_ReleaseGfxSema)(struct gfx_gfx *);

    /* Board-specific escape hatch */
    int (*gf_Private)(struct gfx_gfx *, struct rrm_rnode *,
                      unsigned int, void *, int *);

    /* Frame Scheduler linkage */
    int (*gf_FrsInstall)(struct gfx_data *, void *intrgroup);
    int (*gf_FrsUninstall)(struct gfx_data *);
};
```

### Three-Layer Architecture

```
User space:  libGL.so -> libGLcore.so -> mapped HW registers / FIFO
                                              |
Kernel:      /dev/graphics -> gfx.o (gfx_fncs vtable) -> board driver
                                              |
Hardware:    Newport / IMPACT / Odyssey / CRIME / InfiniteReality
```

- **User-space mapping:** Each board's `gf_MapGfx` callback maps hardware
  registers or command FIFOs into the calling process's address space. GL
  libraries write directly to these mapped regions for maximum throughput.

- **Kernel mediation:** The kernel handles context switching (`gf_PcxSwap`),
  buffer swaps at retrace (`gf_SchedSwapBuf`), and the graphics semaphore
  (`gfxsema`) that serializes pipe access.

- **Board-specific ioctls:** Generic ioctls use codes 100-113 (`GFX_BASE`).
  Board-specific ioctls start at 10000 (`GFX_PRIVATE_BASE`). The `gf_Private`
  function dispatches these.

### Board Registration

Each board calls `GfxRegisterBoard()` during kernel init, passing its `gfx_fncs`
vtable, per-board `gfx_data`, and a `gfx_info` structure:

```c
struct gfx_info {
    char name[16];           /* "NEWPORT", "MGRAS", "ODYSSEY", etc. */
    char label[16];          /* User label */
    unsigned short xpmax;    /* Screen width */
    unsigned short ypmax;    /* Screen height */
    unsigned int length;     /* Size of extended info struct */
};
```

Hardware inventory types from `sys/invent.h`:

```c
#define INV_GR2     11    /* Express (Indigo) */
#define INV_RE      12    /* RealityEngine (Onyx) */
#define INV_NEWPORT 14    /* Newport (Indy, Indigo2 XL) */
#define INV_MGRAS   15    /* MardiGras/IMPACT (Indigo2, Octane) */
#define INV_CRIME   17    /* CRM (O2) */
/* Odyssey/VPro and Kona added in later IRIX versions */
```

---

## Architecture Profiles

### 1. Newport (NG1) -- Indy, Indigo2 XL

**Era:** 1993
**Bus:** GIO64 (0x1f000000)
**Driver:** ng1*.o
**MAME:** Complete implementation (`devices/bus/gio64/newport.cpp`)

**Hardware components:**
- **REX3** -- Raster Engine. Handles 2D drawing: spans, lines, block fills,
  screen-to-screen copies, host data transfers. No 3D geometry capability.
- **VC2** -- Video Controller. CRTC timing, hardware cursor, Display ID (DID)
  tables for per-pixel visual selection.
- **XMAP9** -- Display Mode Generator (5 units). Converts framebuffer pixels
  to display format using mode tables (0x20 entries each). Selects between
  PseudoColor/TrueColor/DirectColor visuals.
- **CMAP** -- Color Map. 8K palette entries shared across visuals.
- **RAMDAC** -- Analog output (Bt445 or equivalent).

**Key characteristics:**
- 2D-only hardware. All OpenGL geometry runs in software on the CPU via
  `libGLcore.so`. REX3 can rasterize Gouraud-shaded triangles (~50 Mpixel/s)
  but triangle setup is done in software.
- 8-bit, 12-bit, or 24-bit color depending on installed VRAM.
- DOSETUP pipeline optimization reduces per-span REX3 register writes.
- VRINT is a timed pulse (not level-held) -- see `progress_notes/indy/newport_xsgi_milestone.md`.

**Variants:**
- Newport (8-bit, Indy)
- Newport 24-bit (Indy)
- XL (24-bit, Indigo2)
- XL24 (`INV_NEWPORT_XL | INV_NEWPORT_24`)

### 2. GR2 / Express -- Indigo, Personal IRIS, Crimson

**Era:** 1991-1995
**Bus:** GIO64 (0x1f000000)
**Driver:** gr2*.o
**MAME:** Partial (GR1 in `sgi_gr1.cpp`; RE2 in `sgi_re2.cpp`)

**Hardware components:**
- **HQ2** -- Host Queue Processor. Command dispatch, GE7 control, DMA engine.
  Includes microcode RAM (32KB).
- **GE7** -- Geometry Engine (7th generation). Microcode-driven floating-point
  processor, 32 MFLOPS per unit. 1-8 units depending on configuration. Handles
  vertex transformation, lighting, clipping.
- **RE3** -- Raster Engine. Shaded/flat span rendering, anti-aliased lines.
  Includes 27-bit buffered and 24-bit unbuffered register sets.
- **VC1** -- Video Controller. CRTC timing, cursor.
- **XMAP5** -- Display Mode Generator (5 units). 32-entry mode tables, 4096-entry
  color LUTs.
- **BT457** -- RAMDACs (3 units for R/G/B).

**Memory map:**
```
0x00000-0x1ffff  shram (32KB shared data RAM)
0x40000-0x5ffff  fifo (32KB command FIFO)
0x60000-0x67fff  hqucode (32KB HQ2 microcode RAM)
0x68000-0x69fff  ge[8] (GE7 unit RAMs, 256 words each)
0x6a000-0x6a0ff  HQ2 registers
0x6c040-0x6c0ff  VC1 registers
0x6c100-0x6c19f  XMAP5[5] + broadcast
0x6c200-0x6c27f  RE3 27-bit registers (buffered)
0x6c280-0x6c3ff  RE3 24-bit registers (unbuffered)
```

**Key characteristics:**
- First SGI architecture with dedicated geometry engines.
- GE7 microcode instruction set is undocumented -- a major blocker for
  emulation. MAME loads/stores microcode but cannot execute it.
- `gf_Download` is used to upload GE7 microcode at boot time.

**Variants** (from `invent.h`):
- XS (8-bit, 1 GE): `INV_GR2_XS`
- XS24 (24-bit, 1 GE): `INV_GR2_XS24`
- XS24Z (24-bit + Z, 1 GE): `INV_GR2_XS24Z`
- XZ (24-bit + Z, 2 GE): `INV_GR2_XZ`
- Elan (24-bit + Z, 4 GE): `INV_GR2_ELAN`
- GR5 (4 GE variant): `INV_GR2_GR5`

### 3. IMPACT / MardiGras (MGRAS) -- Indigo2 IMPACT, Octane

**Era:** 1995-1999
**Bus:** GIO64 (Indigo2) / XIO (Octane)
**Driver:** mgras*.o
**MAME:** None

**Hardware components:**
- **HQ3** -- Host Queue Processor (successor to HQ2). Command FIFO dispatch,
  DMA engine. Exposes three address regions: kernel-space (0x000000),
  diagnostic-space (0x060000), user-space (0x070000).
- **GE11** -- Geometry Engine (11th generation). Hardware vertex transformation
  and lighting. 1-2 units.
- **RE4** -- Raster Engine. Hardware rasterization. 1-2 units.
- **TRAM** -- Texture RAM option. 0, 1, or 2 TRAM modules.
- **VC3** -- Video Controller. SRAM-based timing tables.

**Key characteristics:**
- User-space code writes GL command tokens directly into memory-mapped HQ3 FIFO.
  No system call overhead for most GL operations.
- Context switching requires draining the FIFO and saving/restoring complete
  hardware state -- the `gf_PcxSwap` implementation is heavy (see
  `TRACE_SWAP_H1` through `TRACE_SWAP_H8` in `mgras_internals.h`).
- HQ3 has a FIFO high-water mark (`gfxbackedup` flag) to prevent overflow.
  The `GET_GFXSEMA` macro spins while `gfxbackedup` is set.

**Variants** (from `invent.h`):
- Solid IMPACT (HQ3, 1 GE, 1 RE, 0 TRAM): `INV_MGRAS_HQ3 | INV_MGRAS_1GE | INV_MGRAS_1RE`
- High IMPACT (HQ3, 1 GE, 1 RE, 1 TRAM): `... | INV_MGRAS_1TR`
- Maximum IMPACT (HQ3, 2 GE, 2 RE, 2 TRAM): `... | INV_MGRAS_2GE | INV_MGRAS_2RE | INV_MGRAS_2TR`
- SSE/MXE (HQ4 variants, Octane): `INV_MGRAS_HQ4`

**Sub-architectures:**
```c
#define INV_MGRAS_HQ3   0x00000000   /* IMPACT */
#define INV_MGRAS_HQ4   0x01000000   /* Gamera (Octane refresh) */
#define INV_MGRAS_MOT   0x02000000   /* Mothra */
```

### 4. CRM / CRIME -- O2

**Era:** 1996
**Bus:** Unified Memory Architecture (integrated, base 0x14000000)
**Driver:** crime*.o / a3_dd.o / rad_dd.o
**MAME:** Partial (`crime.cpp`)

**Hardware components:**
- **CRIME** -- Central controller. Memory controller, DMA engine, interrupt
  controller, 66.67 MHz system timer. Base address 0x14000000.
- **GBE** -- Graphics Backend Engine. Framebuffer controller, display timing,
  overlay/underlay compositing.
- **MRE** -- Media Rendering Engine. Rasterization and texture mapping ASIC.
  Reads textures directly from main memory.
- **ICE** -- Image Compression Engine. Pixel packing/unpacking.
- **VICE** -- Video Interface Controller Extension. Hardware video compositing,
  keying, multi-layer display.
- **MACE** -- Peripheral controller (serial, audio, ethernet, etc.).

**Key characteristics:**
- Only SGI platform with true Unified Memory Architecture. Textures, frame-
  buffer, and Z-buffer all live in system RAM. No dedicated graphics memory.
- Geometry is done in software on the CPU (like Newport). The MRE handles
  rasterization and texturing.
- CRIME's interrupt controller routes 6 rendering engine interrupts
  (`CRM_INT_RE[0-5]`) and 4 GBE interrupts (`CRM_INT_GBE[0-3]`).
- The `crime_frameinfo_t` structure provides UST (unadjusted system time),
  field counts, and swap status for video synchronization.
- Horizontal line interrupt scheduling (`crime_hli_*`) allows precise timing
  relative to video retrace.

**O2-specific kernel interface** (from `crime.h`):
```c
/* Frame timing info */
typedef struct {
    stamp_t ust;           /* unadjusted system time */
    __int32_t field;       /* field count */
    __int32_t line;        /* current line */
    __int32_t swap_pending;/* swap not yet executed */
    __int32_t swap_done;   /* swap completed */
} crime_frameinfo_t;

/* Video info */
typedef struct {
    __int32_t boardrev;    /* board revision */
    __int32_t crimerev;    /* CRIME chip revision */
    __int32_t gberev;      /* GBE chip revision */
    __int32_t w, h;        /* display dimensions */
    __int32_t flags;       /* configuration flags */
} crime_vinfo_t;
```

### 5. Odyssey / VPro -- Octane2, Fuel, Tezro

**Era:** 1999-2004
**Bus:** XIO (Octane2) / proprietary (Fuel/Tezro)
**Driver:** odsy*.o
**MAME:** None

**Hardware components:**
- **Buzz ASIC** -- Single-chip graphics pipeline running at 251 MHz. Contains
  the complete geometry engine, rasterizer, and texture unit on one die with
  on-chip SRAM. Processes `__BUZZpackets` command packets.
- **PB&J** -- Pixel Blaster & Jammer ASIC. Display output, video I/O.
- **SDRAM** -- Shared graphics memory (32-128MB depending on model). Used for
  both framebuffer and texture storage.

**Key characteristics:**
- Single-chip pipeline design (vs. multi-board IMPACT and InfiniteReality).
- 48-bit RGBA color (12 bits per component).
- Hardware context limit: 127 contexts (`NR_ODSY_DDRNS = 127`) plus 1 board
  manager context (`ODSY_BRDMGR_HW_CONTEXT_ID = 0`).
- Up to 2 boards in a system (`ODSY_MAXBOARDS = 2`).
- User-space writes go to a mapped "write region" whose size is determined by
  `ODSY_HW_WR_RGN_OFFSET_ADDR_BITS = 15` (32K slots).
- The driver uses `buzz_config_flags` to track master/slave board relationships
  in dual-board configurations.
- Retrace synchronization uses a dedicated `odsy_retrace` structure with
  swap groups, sync values, and swap time tracking.
- DDC/I2C monitor detection (`ddc_i2c.h`).
- Flat panel support (`fpanel.h`).

**Variants:**
- V6 (32MB SDRAM)
- V8 (128MB SDRAM)
- V10 (32MB, 2x geometry perf)
- V12 (128MB, 2x geometry perf)

**Note:** SGI later sold VPro-branded boards (V3, VR3, V7, VR7) based on
Nvidia Quadro GPUs. These share nothing with Odyssey and only work on SGI's
x86 Visual Workstations, not MIPS/IRIX systems.

### 6. InfiniteReality / Kona -- Onyx, Onyx2, Onyx 3000

**Era:** 1996-2005
**Bus:** POWERpath-2 (Onyx) / NUMAlink (Onyx2/3000)
**Driver:** kona*.o
**MAME:** None

**Hardware components:**
- **HIP** -- Host Interface Processor (HIP1 with F-chip, HIP2 with XG-chip).
  Command dispatch, DMA engine. The XG-chip on HIP2 has a 2MB register space.
- **GE board** -- Geometry Engine board with 4 parallel geometry processors.
  Downloadable microcode via `gf_Download`.
- **RM board** -- Raster Manager. 1, 2, or 4 per pipeline. Each RM has
  dedicated texture RAM (up to 256MB on RM10, 1GB on RM11).
- **DG board** -- Display Generator. Video output, gamma correction, genlock.

**User-space memory map** (from `kona.h`):
```
0x0000-0x3fff  GFIFO (16KB) -- graphics command FIFO
0x4000-0x7fff  HIP registers (16KB) -- read-only except diagnostics
0x8000-0xbfff  rdata (16KB) -- return data memory
0xc000-0xffff  F-chip registers (HIP1, diagnostics only)
0xc000-0x20ffff XG-chip registers (HIP2, diagnostics only, 2MB)
```

**Key characteristics:**
- 48-bit RGBA color (12 bits per component).
- Sort-middle architecture: geometry is distributed across GE processors,
  then screen-space fragments are routed to the appropriate RM.
- Massively scalable: up to 16 pipelines, each with up to 4 RMs.
- Total raster memory up to 10GB across all RMs.
- 151 ASICs per maximum pipeline configuration (260M+ transistors).

**Platform-specific ioctls** (from `kona.h:153-176`):
```c
#define KONA_DMAREAD            (10000 + 0)   /* DMA read from pipe */
#define KONA_DMAWRITE           (10000 + 1)   /* DMA write to pipe */
#define KONA_START_SELECTFEED   (10000 + 2)   /* GL selection/feedback */
#define KONA_FINISH_SELECTFEED  (10000 + 3)
#define KONA_SELECT_CURSOR      (10000 + 5)   /* Cursor mode */
#define KONA_SET_CHANNEL_RECT   (10000 + 9)   /* Multi-display regions */
#define KONA_MEM_ALLOC          (10000 + 10)  /* ARM/GE/TEX pool alloc */
#define KONA_MEM_FREE           (10000 + 11)
#define KONA_NCLOPS_CONFIGURE   (10000 + 20)  /* Hyperpipe setup */
#define KONA_NCLOPS_BIND        (10000 + 22)  /* Bind to hyperpipe */
```

**Board name variants** (compile-time selection):
```c
#ifdef IP19   /* Onyx R4400 */
#define KONA_BOARDNAME  "KONA"
#ifdef IP21   /* Onyx R8000 */
#define KONA_BOARDNAME  "KONAT"
#ifdef IP25   /* Challenge XL */
#define KONA_BOARDNAME  "KONAS"
#ifdef IP27   /* Origin 2000 / Onyx2 */
#define KONA_BOARDNAME  "KONAL"
```

**Strided DMA support** (unique to InfiniteReality):
```c
typedef struct kona_dmavec {
    caddr_t base;    /* user virtual address */
    uint    len;     /* total DMA length in bytes */
    uint    llen;    /* bytes per line (for 2D textures) */
    uint    stride;  /* padding between lines */
} kona_dmavec_t;
```

**Pipe death tracking** -- the driver maintains detailed failure reasons
(`KD_CONTEXT_DEACT_TIMEOUT`, `KD_GFIFO_CLOGGED`, `KD_PARITY_ERROR`, etc.)
for diagnostics. 24 distinct death reason codes are defined.

---

## Architecture Comparison Table

| | **Newport** | **GR2/Express** | **IMPACT/MGRAS** | **CRM (O2)** | **Odyssey/VPro** | **InfiniteReality** |
|---|---|---|---|---|---|---|
| **Platforms** | Indy, Indigo2 XL | Indigo, IRIS, Crimson | Indigo2, Octane | O2 | Octane2, Fuel, Tezro | Onyx, Onyx2, Onyx 3000 |
| **Bus** | GIO64 | GIO64 | GIO64 / XIO | UMA | XIO | POWERpath-2 / NUMAlink |
| **Geometry** | Software (CPU) | HW: 1-8 GE7 | HW: 1-2 GE11 | Software (CPU) | HW: Buzz ASIC | HW: GE board (4 procs) |
| **Rasterization** | REX3 (2D accel) | RE3 | RE4 (1-2 units) | MRE ASIC | Buzz ASIC | RM board (1-4 units) |
| **Texturing** | None | None | Optional (0-2 TRAM) | MRE + main RAM | HW (shared SDRAM) | HW (dedicated, up to 1GB) |
| **Color depth** | 8/24-bit | 8/24-bit | 24-bit+ | 24-bit | 48-bit RGBA | 48-bit RGBA |
| **Z-buffer** | System RAM | Optional HW | Dedicated | System RAM | Dedicated | Dedicated |
| **Framebuffer** | On-board VRAM | On-board VRAM | Dedicated FB RAM | System RAM (UMA) | Shared SDRAM (32-128MB) | Up to 10GB raster |
| **Context HW limit** | N/A (trivial) | N/A | Per-FIFO | N/A | 127 | Per-pipe |
| **Multi-board** | No | No | No (2 max for Octane) | No | 2 max | 16 pipelines |
| **Microcode DL** | No | Yes (GE7) | No | No | No | Yes (GE) |
| **Video I/O** | IndyCam only | No | Compression/Channel | VICE (integrated) | Optional | Yes |
| **User mapping** | REX3 registers | HQ2 FIFO + shram | HQ3 FIFO (3 regions) | CRM registers | Buzz write region | 16KB GFIFO + rdata |
| **OpenGL** | 1.1 (software) | 1.0 (HW geometry) | 1.1-1.2 | 1.1 | 1.2 | 1.2 |
| **Emulation status** | MAME + QEMU | MAME partial | None | MAME partial | None | None |

---

## GL Pipeline Paths

The path from an OpenGL call to pixels differs fundamentally across platforms.

### Newport (Software Path)

```
Application
    |
libGL.so
    |
libGLcore.so  <-- Software transform, lighting, clipping, triangle setup
    |               All done on the CPU. ~2000 lines of MIPS FP code per
    |               glVertex3f in the worst case (with lighting enabled).
    v
REX3 MMIO writes  <-- Only pixel fill is hardware-accelerated.
    |                   Gouraud shading at ~50 Mpixel/s.
    v
Newport VRAM
```

### GR2 / Express (Microcode Path)

```
Application
    |
libGL.so
    |
HQ2 FIFO writes (mapped into user space)
    |
    v
HQ2 dispatches to GE7 units
    |
GE7 microcode execution  <-- Proprietary ISA, field-upgradable.
    |                          Parallel across 1-8 units.
    v
RE3 rasterization
    |
    v
GR2 VRAM
```

### IMPACT / MGRAS (HQ3 FIFO Path)

```
Application
    |
libGL.so
    |
HQ3 FIFO writes (user-space region at offset 0x070000)
    |
    v
HQ3 dispatches to GE11 units
    |
GE11 hardware transform+lighting
    |
    v
RE4 rasterization + TRAM texturing
    |
    v
IMPACT framebuffer
```

### Odyssey / VPro (Buzz Packet Path)

```
Application
    |
libGL.so
    |
Buzz write region (mapped, __BUZZpackets format)
    |
    v
Buzz ASIC (251 MHz single-chip pipeline)
    |-- Geometry engine
    |-- Rasterizer
    |-- Texture unit
    |
    v
SDRAM framebuffer
```

### InfiniteReality / Kona (GFIFO Path)

```
Application
    |
libGL.so
    |
GFIFO writes (16KB mapped region)
    |
    v
HIP (F-chip or XG-chip) dispatches across GEs
    |
GE board (4 parallel geometry processors)
    |
    v
Sort-middle distribution to RMs
    |
RM board(s) rasterization + texturing
    |
    v
DG board -> display
```

### O2 / CRIME (UMA Path)

```
Application
    |
libGL.so
    |
libGLcore.so  <-- Software geometry (like Newport)
    |
CRM register writes
    |
    v
ICE (pixel packing) -> MRE (rasterization + texturing)
    |                       |
    |                       v
    |                   Textures read directly from main RAM
    v
GBE -> framebuffer in main RAM
```

---

## Platform-Specific Features

### Features Unique to One Architecture

| Feature | Platform | Description |
|---------|----------|-------------|
| Hyperpipe multi-pipe sync | InfiniteReality | `KONA_NCLOPS_*` ioctls. Synchronizes rendering across up to 32 pipes via NUMAlink. Required for CAVE/powerwall displays. |
| Channel rectangles | InfiniteReality | `KONA_SET_CHANNEL_RECT`. Per-region display priority for multi-display configurations. |
| Strided DMA | InfiniteReality | `kona_dmavec` with `llen`/`stride` fields for efficient 2D texture uploads. |
| Pipe death diagnostics | InfiniteReality | 24 distinct failure reason codes (`KD_*`). Self-diagnosing pipe health via `KONA_HEALTH` ioctl. |
| GE/ARM/TEX memory pools | InfiniteReality | `KONA_MEM_ALLOC/FREE`. Separate allocation pools for geometry engine, raster manager, and texture memory. |
| Unified Memory Architecture | O2/CRIME | Textures, framebuffer, and Z-buffer in main RAM. Only SGI platform with true UMA. |
| VICE video compositor | O2/CRIME | Hardware keying and multi-layer compositing. Integrated into CRIME interrupt controller. |
| Horizontal line interrupt | O2/CRIME | `crime_hli_*` scheduling relative to specific scanlines. |
| UST frame timing | O2/CRIME | `crime_frameinfo_t` with unadjusted system time, field/line counters, swap status. |
| Buzz single-chip pipeline | Odyssey/VPro | Complete GL pipeline on one ASIC (251 MHz). No multi-board pipeline. |
| 127-context hardware limit | Odyssey/VPro | Buzz context ID is 7 bits. `NR_ODSY_DDRNS = 127`. |
| Dual-board master/slave | Odyssey/VPro | `buzz_config_flags` tracks master/slave relationship. 2 boards maximum. |
| GE7 downloadable microcode | GR2/Express | Field-upgradable geometry engine via `gf_Download`. Undocumented ISA. |
| DOSETUP optimization | Newport | REX3-specific pipeline shortcut reducing per-span register writes. |
| HQ3 three-region mapping | IMPACT/MGRAS | Kernel, diagnostic, and user-space regions at distinct offsets within the board address range. |
| Video texture transfer | IMPACT/MGRAS | `MgrasVidTextureXferStart/Done` for live video-as-texture. |

### Features Shared Across Some Platforms

| Feature | Has It | Does Not Have It |
|---------|--------|-----------------|
| Hardware geometry engine | GR2, IMPACT, Odyssey, IR | Newport, O2 |
| Hardware texturing | IMPACT (opt), O2 (MRE), Odyssey, IR | Newport, GR2 |
| Hardware Z-buffer | GR2 (opt), IMPACT, Odyssey, IR | Newport, O2 (uses system RAM) |
| 48-bit RGBA color | Odyssey, IR | Newport, GR2, IMPACT, O2 |
| Microcode download | GR2, IR | Newport, IMPACT, O2, Odyssey |
| Context save/restore in HW | IMPACT, Odyssey, IR | Newport (trivial), O2, GR2 |
| VRINT / retrace interrupt | All | -- |
| Hardware cursor | All | -- |
| Double/triple buffering | All | -- |
| Swap groups (multi-window sync) | IMPACT, Odyssey, IR | Newport, GR2, O2 |
| Frame Scheduler integration | All (via `gf_FrsInstall`) | -- |

### IrisGL vs OpenGL

Older IrisGL (pre-OpenGL) applications use an entirely different API. Support
is conditional in the kernel:

```c
/* IrisGL is NOT supported on newer platforms */
#ifndef SUPPORT_NATIVE_IRISGL  /* defined out for IP27, IP30, IP32 */
```

This means IrisGL apps can only run on Indigo/Indigo2/Indy (IP20/IP22/IP24)
and the original Onyx (IP19). All newer platforms require OpenGL.

---

## The Universal Interception Problem

### What Must Be Intercepted

To run software from any SGI platform, we need to handle three categories:

**1. Standard OpenGL calls (catchable)**

All IRIX applications link against `libGL.so`, which dispatches to a
board-specific `libGLcore.so`. The OpenGL API is identical across all
platforms -- `glVertex3f`, `glBegin`, `glLoadMatrix`, etc. are the same
whether the application was compiled for Indy, Octane, or Onyx.

**2. X11 / 2D operations (catchable)**

The X protocol is board-independent. `Xsgi` uses the `gfx_fncs` interface
internally, but X clients don't see board-specific details. Newport already
handles this correctly in QEMU.

**3. Board-specific hardware access (not generically catchable)**

Software that writes directly to board-specific registers or FIFOs cannot
be intercepted without emulating that specific board's hardware. This includes:

| Access Pattern | Example | Prevalence |
|---------------|---------|------------|
| Direct FIFO writes | HQ3 command tokens, Buzz packets, GFIFO words | libGLcore.so only |
| Board-specific ioctls | `KONA_NCLOPS_*`, MGRAS diagnostics | System tools, X DDX |
| Mapped register access | REX3 direct writes, CRM register access | libGLcore.so, diag tools |
| Microcode upload | GE7 code, InfiniteReality GE code | Boot-time only |

### Hardware Register Incompatibility

The mapped register interfaces are completely incompatible across platforms:

- **Newport:** REX3 registers -- span-level 2D drawing commands at fixed offsets
- **IMPACT:** HQ3 FIFO tokens -- proprietary 64-bit command format for GE11/RE4
- **Odyssey:** Buzz packets -- `__BUZZpackets` proprietary binary format
- **InfiniteReality:** GFIFO words -- proprietary command format for HIP/GE
- **O2/CRIME:** CRM registers -- UMA DMA descriptors at 0x14000000+

Any emulation of these register interfaces requires a complete hardware model
of the specific board. There is no common format.

### The Key Insight

The incompatible register formats only matter within `libGLcore.so`. This is
the one shared library that translates OpenGL API calls into board-specific
register writes. **All application code above `libGLcore.so` uses the standard
OpenGL API.** Therefore:

```
        Application code            <-- board-independent (OpenGL API)
              |
          libGL.so                  <-- board-independent (dispatch)
              |
        libGLcore.so                <-- board-SPECIFIC (register writes)
              |                         THIS is what we replace/patch
        Mapped HW registers         <-- board-specific (incompatible)
```

By replacing or patching `libGLcore.so`, we intercept all GL calls at the
one point where they're still in a universal format (the OpenGL C API) and
redirect them to our GL accelerator's simple MMIO interface.

---

## Recommendation: Intercept at libGLcore.so

### The Strategy

The GL Accelerator approach from `progress_notes/ip54_platform_design.md`
is the correct architecture. Here's why it works as a universal catch-all:

**1. Keep Newport for 2D/X11.**

Newport already works with Xsgi, 4Dwm, xterm, xclock, and all 2D X11
applications. The 2D path is board-independent at the X protocol level.

**2. Use the GL Accelerator for all 3D.**

By intercepting at `libGLcore.so`, we catch every OpenGL call regardless of
which SGI platform the application was originally compiled for. An Octane
application calling `glVertex3f` links against the same `libGL.so` and uses
the same calling convention as an Indy application. The `analysis_tools/
patch_libglcore.py` tool replaces the board-specific `libGLcore.so` functions
with MMIO trampolines to our GL accelerator at 0x1f400000.

**3. Stub board-specific ioctls.**

For the small number of applications that use board-specific ioctls
(`KONA_NCLOPS_*`, MGRAS diagnostics, CRIME video), return `ENODEV` or
appropriate "not available" responses. These are overwhelmingly system
utilities and diagnostic tools, not end-user applications.

### What This Covers

| Software Category | Example | Works? | Mechanism |
|-------------------|---------|--------|-----------|
| 2D X11 apps | xterm, xedit, toolchest | Yes | Newport |
| Window managers | 4Dwm, mwm | Yes | Newport |
| Standard OpenGL apps | ideas, glxgears, flight | Yes | GL Accelerator via patched libGLcore |
| IRIS Performer apps | Town, Roam | Yes | OpenGL path (Performer uses GL) |
| OpenInventor apps | SceneViewer | Yes | OpenGL path |
| MIPSpro Visual Workshop | CaseVision, cvd | Yes | X11 + standard GL |
| System admin tools | hinv, gr_osview | Yes | hinv uses /dev/graphics ioctls (generic) |
| Board diagnostics | ide, mgras_diag | No | Board-specific register access |
| Hyperpipe visualization | CAVE apps | No | Requires multi-pipe hardware |
| IrisGL legacy apps | flight (original) | No | IrisGL not supported on IP54 |
| Video compositing | MediaRecorder | No | Requires VICE/CRIME or IMPACT video |

### Coverage Estimate

Based on a typical IRIX 6.5 software library:
- **~95% of graphical applications** use standard OpenGL and X11 only
- **~3%** use board-specific ioctls for diagnostics or system monitoring
- **~2%** use platform-specific features (hyperpipe, video compositing,
  IrisGL legacy)

### What Cannot Be Caught

| Category | Why | Workaround |
|----------|-----|------------|
| Direct FIFO writes (HQ3/Buzz/GFIFO) | Proprietary binary formats, no common encoding | Not needed -- only libGLcore.so does this |
| Hyperpipe multi-pipe rendering | Requires physical multi-pipe hardware and NUMAlink | None -- fundamental architecture dependency |
| Hardware video compositing | VICE (O2) and IMPACT video are unique ASICs | Software compositing could be done in the GL accelerator |
| IrisGL `sproc` share groups | `gfxsema`/`gfxlock` atomics for IrisGL threading | Not supported on IP54 (matches IP30/IP32 behavior) |
| GE7/GE microcode execution | Undocumented instruction sets | Not needed -- GL accelerator replaces geometry engine |
| Direct framebuffer pixel access | Applications that `mmap` the framebuffer directly | Could be supported via Newport VRAM mapping |

### Extension Path

The GL accelerator can be extended incrementally:

**Phase 1:** Core geometry pipeline (`glVertex`, `glColor`, `glBegin/End`,
matrix operations). Covers simple demos and test programs.

**Phase 2:** Lighting and materials (`glLight`, `glMaterial`, `glNormal`).
Covers most scientific visualization.

**Phase 3:** Texturing (`glTexImage2D`, `glTexCoord`, `glTexParameter`).
Requires shared-memory path for bulk texture upload. Covers games and
multimedia.

**Phase 4:** Display lists (`glNewList`, `glCallList`). Required for IRIS
Performer and complex scene graphs. Can be implemented as captured command
sequences replayed through the accelerator.

**Phase 5:** Selection and feedback (`glRenderMode`, `glSelectBuffer`).
Required for interactive picking in 3D applications.

**Phase 6:** Advanced features (fog, stencil, accumulation buffer, evaluators).
Covers the long tail of OpenGL 1.x functionality.

Each phase corresponds to patching additional `libGLcore.so` entry points via
`patch_libglcore.py` and implementing the corresponding register handlers in
`sgi_glaccel.c`.

---

## Source References

### IRIX Kernel Headers

| File | Content |
|------|---------|
| `irix-655-source/f/root/usr/include/sys/gfx.h` | `gfx_fncs` vtable, `gfx_data`, `gfx_gfx`, generic ioctls |
| `irix-655-source/f/root/usr/include/sys/rrm.h` | RRM commands, `rrm_rnode`, resource masks, pane cache |
| `irix-655-source/f/root/usr/include/sys/invent.h` | `INV_NEWPORT`, `INV_MGRAS`, `INV_GR2`, `INV_RE`, `INV_CRIME` |
| `irix-655-source/f/root/usr/include/sys/kona.h` | InfiniteReality ioctls, GFIFO/HIP/rdata map, pipe death codes |
| `irix-655-source/f/root/usr/include/sys/crime.h` | CRIME registers, interrupts, `crime_frameinfo_t`, `crime_vinfo_t` |

### IRIX Kernel Driver Stubs

| File | Board |
|------|-------|
| `irix-655-source/f/irix/kern/stubs/ng1stubs.c` | Newport |
| `irix-655-source/f/irix/kern/stubs/gr2stubs.c` | GR2/Express |
| `irix-655-source/f/irix/kern/stubs/mgrasstubs.c` | IMPACT/MGRAS |
| `irix-655-source/f/irix/kern/stubs/crimestubs.c` | O2/CRIME |

### IRIX PROM Graphics Init

| File | Board |
|------|-------|
| `irix-657m-source/stand/arcs/lib/libsk/graphics/NEWPORT/ng1_init.c` | Newport |
| `irix-657m-source/stand/arcs/lib/libsk/graphics/MGRAS/mgras_internals.h` | IMPACT |
| `irix-657m-source/stand/arcs/lib/libsk/graphics/ODYSSEY/odsy_internals.h` | Odyssey/VPro |

### MAME Implementations

| File | Board | Status |
|------|-------|--------|
| `mame/source/src/devices/bus/gio64/newport.{h,cpp}` | Newport | Complete |
| `mame/source/src/mame/sgi/sgi_gr1.{h,cpp}` | GR1 | Partial |
| `mame/source/src/mame/sgi/sgi_re2.{h,cpp}` | RE2 (GR1 raster) | Partial |
| `mame/source/src/mame/sgi/crime.{h,cpp}` | CRIME (O2) | Memory controller only |

### Other References

| File | Content |
|------|---------|
| `gathered_documentation/GL_ACCELERATOR.md` | GL accelerator register map |
| `gathered_documentation/GR2_SKELETON.md` | GR2 memory map and components |
| `gathered_documentation/graphics/NEWPORT_ARCHITECTURE.md` | Newport internals |
| `analysis_tools/patch_libglcore.py` | libGLcore.so binary patcher |
| `netbsd_source/sys/arch/sgimips/gio/newport.c` | NetBSD Newport driver |
| `progress_notes/ip54_platform_design.md` | IP54 platform + GL accelerator design |
