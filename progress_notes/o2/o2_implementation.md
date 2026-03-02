# O2 (IP32/IP54) Implementation

Consolidated notes on O2 PROM boot, disk boot chain, kernel crash analysis,
and serial fix.

---

## 1. PROM Boot Status

The IP54 PROM boots through `firmware()` → `finit2()` → `finit3()` →
`fw_dispatcher()` → `_main()`. Serial output confirms complete boot:

```
L[C][E]SsRrVabcdefghijv2[IP54] post2
F[IP54] initGraphics
[IP54] _init_saio
[IP54] init_spb
[IP54] initConsole
f3[IP54] post3
GgD 0  \r\n<NULL>
```

### Bugs Fixed

**BSS zeroing kseg0/kseg1 mismatch** (`csu.s`): `PROM_STACK` is defined as
kseg1 (`0xA1400000`) but `firstBss` is at kseg0 (`0x80100720`). Subtracting
kseg0 from kseg1 wraps to ~557MB. Fix: convert `PROM_STACK` to kseg0 before
computing BSS range.

**Flash access crash** (`env.c`): `init_env()` → `findFlashSegment()` reads
from PROM ROM, then `reconstruct_envstrs()` tries to write to flash → Address
Error. Fix: enabled `#define LABnoFlash` to skip all flash operations.

**CPU frequency cache crash** (`env.c`): `cpu_get_freq_str(0)` calls
`cpuclkper100ticks()` which uses CACHE instructions that crash with
unconfigured caches. Fix: hardcoded `syssetenv("cpufreq","180")`.

**ARCS Read() returning ENODEV** (`ip54_stubs.c`): `Read()` stub returned
ENODEV with count=0. `getchar()` → `Read(0,&c,1,&cnt)` always got EOF.
Menu parsers fell through instantly, producing the `0  \r\n<NULL>` output
loop. Fix: implemented `Read()` to directly poll MACE serial hardware (LSR
at 0xBF390507, RBR at 0xBF390007).

---

## 2. Disk Boot Chain

The PROM can load and execute ELF32 binaries from disk via
`boot dksc(0,1,0)<filename>`:

```
PROM → System Maintenance Menu → Command Monitor
  → Open disk device, read SGI Volume Header
  → Find file in volume directory by name
  → Detect format (ELF32 or ECOFF)
  → Load PT_LOAD segments to physical memory via kseg1
  → Jump to entry point
```

### Components Implemented
- FirmwareVector: all 31 ARCS callbacks populated in SPB
- Component tree: CPU, caches, memory, SCSI adapter hierarchy
- Memory descriptors: ExceptionBlock, SPBPage, FirmwarePermanent, Free
- Virtual boot disk (QEMU MMIO at 0x17000000)
- PROM disk I/O: Open/Read/Seek/Close for `dksc()` device paths
- ELF32 loader + ECOFF section headers

### Limitation: ECOFF Relocation
IRIX `sash` (ECOFF OMAGIC) has ~6000 relocation entries requiring runtime
patching. Not yet implemented. ELF32 binaries load and run correctly.

---

## 3. Kernel Crash Analysis

The IRIX 6.3 kernel (`unix.IP32`) loads and jumps to entry point but crashes
during early init:

```
PANIC: KERNEL FAULT
PC: 0x800d0548 ep: 0xffffcd18
EXC code:16, 'Read Address Error'
Bad addr: 0xffb00000
```

### Root Cause Chain

A function initializing a kernel graphics subsystem calls a probe function
that computes `nscreens`. The variable is initialized to 0 in the binary
but becomes 1531 (0x5FB) during execution. This leads to
`kmem_alloc(18380)` which returns `0xffb00000` (an invalid/unmapped
address), and the subsequent store crashes.

### Key Variables

| Variable | GP Offset | Expected | Actual |
|----------|-----------|----------|--------|
| nscreens | GP-29928 | 0 or 1 | 0x5FB (1531) |
| alloc result | GP-18632 | valid ptr | 0xffb00000 |

### Open Hypotheses

**nscreens=1531 from GBE stub** (**HYPOTHESIS**): The GBE graphics stub
returns 0 for all reads. Some graphics probe reads GBE/CRIME registers and
computes nscreens from them. Zero returns may be misinterpreted as a large
count.

**Kernel PDA not set up**: The allocator accesses PDA via
`lw v1, -24360(zero)` (virtual 0xFFFFA0E8, kseg3) which requires a wired
TLB entry. If not properly installed, the allocator reads garbage.

**Page allocator empty**: If memory descriptors are wrong or heap init
failed, the allocator returns an error value that the caller doesn't check.

---

## 4. Socket Chardev Fix

### Problem
`qemu_run_sgi` (stdio) sees all serial output, but `qemu_serial_interact`
(Unix socket) receives 0 bytes. The PROM sends output within microseconds
of starting. Socket chardev with `server=on,wait=off` discards data sent
before a client connects.

### Fix (Two Parts)

**Part A: serial_mm for MACE serial** (`sgi_o2.c`): Replaced custom MACE
serial with QEMU's `serial_mm_init()` (regshift=8, 256-byte register
spacing). The serial_mm memory region is a subregion of MACE iomem at
`MACE_SER1_OFFSET` (0x390000). Subregions take priority.

**Part B: wait=on** (`server.py`): Changed socket chardev from `wait=off` to
`wait=on` in all three MCP tools. With `wait=on`, QEMU blocks at chardev
init until client connects, then starts VM. All boot output is captured.

---

## 5. Key Addresses

### MACE Serial Port 0

| Register | Physical | kseg1 | Purpose |
|----------|----------|-------|---------|
| RBR/THR | 0x1F390007 | 0xBF390007 | Data read/write |
| LSR | 0x1F390507 | 0xBF390507 | Line status (bit 0 = DR) |

Register spacing: 256 bytes (regshift=8). Byte offset +7 within each
8-byte doubleword (big-endian).

### System

| Device | Base Address |
|--------|-------------|
| CRIME | 0x14000000 |
| CRIME RE | 0x15000000 |
| GBE | 0x16000000 |
| Bootdisk | 0x17000000 |
| MACE serial | 0x1F390000 |
