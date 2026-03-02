# IP54 Platform Design

A custom SGI platform family built for QEMU, designed to run IRIX 6.5 with
capabilities far beyond any real SGI hardware: arbitrary CPU count, up to 1TB
of RAM, multiple graphics architecture options, 10 Gbps networking, and
pixel-perfect rendering at any resolution up to 8K. All unnecessary real-device
emulation is replaced with purpose-built paravirtual hardware.

## Contents

1. [Design Goals](#design-goals)
2. [Motivation](#motivation)
3. [Platform Overview](#platform-overview)
4. [Hardware Architecture](#hardware-architecture)
5. [SMP Controller](#smp-controller)
6. [Secondary CPU Bootstrap Protocol](#secondary-cpu-bootstrap-protocol)
7. [High Memory Architecture](#high-memory-architecture)
8. [QEMU Implementation](#qemu-implementation)
9. [PROM Changes](#prom-changes)
10. [IRIX Kernel Changes](#irix-kernel-changes)
11. [Paravirtual 10GbE Networking](#paravirtual-10gbe-networking)
12. [Graphics Architecture](#graphics-architecture)
13. [Arbitrary Display Resolution](#arbitrary-display-resolution)
14. [Platform Variants](#platform-variants)
15. [Phased Implementation Plan](#phased-implementation-plan)
16. [Verification Criteria](#verification-criteria)
17. [Key Source References](#key-source-references)

---

## Design Goals

Five capabilities define IP54, each exceeding what any real SGI system offered:

### 1. Arbitrary SMP

Use multiple host CPU cores to run multiple guest CPUs. The target is
"as many as IRIX can handle" — the Everest platform (Challenge/Onyx, IP19)
ran R4400 CPUs at MAXCPU=128, and Origin 2000 (IP27) reached 256. IP54
should support at least 16 CPUs initially, expandable to 128.

**IRIX MAXCPU by real platform:**

| Platform | MAXCPU | CPUs/Node | Notes |
|----------|--------|-----------|-------|
| IP22/IP24 (Indy) | 1 | - | Uniprocessor only |
| IP30 (Octane) | 2 | - | "max is 4, but only 2 support now" |
| IP19 (Challenge R4400) | 128 | 4/board | Everest bus, up to 52 real CPUs |
| IP25 (Power Challenge R10K) | 128 | 4/board | Everest bus |
| IP27 (Origin 2000) | 128 | 2/node | 64 nodes, SN0 interconnect |
| IP27 XXL (Origin 3000) | 256 | 2/node | 128 nodes |

**Host parallelism caveat:** QEMU's MIPS64 target does not currently support
MTTCG (multi-threaded TCG). All vCPUs execute round-robin on one host thread.
This was disabled due to a 9% failure rate in SMP stability tests (commit
a092a95547, March 2020). The bug was never fixed — just disabled. If/when
it's resolved upstream, IP54 would gain real host-parallel execution with
no changes needed on our side. Until then, SMP is **functionally correct**
(the kernel schedules across N CPUs, processes migrate, locks work) but
not performance-parallel on the host.

### 2. High RAM

Assign 128GB of RAM or more — no arbitrary ceiling. IRIX was an HPC operating
system that ran on machines with hundreds of gigabytes; it should be able to
handle whatever we give it.

**Physical address space by CPU:**

| CPU | PABITS | Max Physical | In QEMU |
|-----|--------|-------------|---------|
| R4000/R4400 | 36 | 64 GB | Yes |
| R10000 | 40 | 1 TB | Yes |
| I6400/I6500 | 48 | 256 TB | Yes |

The R4400 (current IP24 CPU) is limited to 64GB physical. Switching to
R10000 raises the ceiling to 1TB and is architecturally appropriate — R10000
was the CPU in every SMP SGI system (Octane, Challenge, Origin). The current
MC memory controller supports only 256MB; IP54 replaces it with a paravirtual
memory controller that maps all configured RAM directly.

**IRIX memory support:** The IRIX 6.5 kernel comment states "64 bit kernels
support up to 16 GB of memory" — but Origin systems shipped with 256-512GB.
The practical limit depends on the memory descriptor tables and TLB
configuration. Testing will determine the actual ceiling.

### 3. Multiple Graphics Architectures

Run demos and applications designed for different SGI graphics hardware:
O2 (CRIME), Octane (Impact), InfiniteReality, etc. Two approaches:

- **Unified platform (preferred):** IP54 can attach different graphics devices
  at the GIO slot. The PROM detects which one is present and initializes it.
  IRIX loads the corresponding graphics driver. Different graphics = different
  `-global` or `-device` option, same machine type.

- **Platform variants:** If bus architecture differences are irreconcilable
  (e.g., XIO vs GIO vs UMA), define IP55, IP56, etc. with different
  backplane assumptions. Each shares the same CPU, SMP, memory, and network
  subsystems but differs in how graphics are connected.

The goal is to run `ideas`, `flight`, and other classic SGI demos with
hardware-accelerated 3D via host GPU passthrough.

### 4. 10 Gigabit Ethernet

High-speed networking for file transfers, NFS, and general I/O. The Seeq
80C03 (10 Mbps) remains available for compatibility but a paravirtual 10GbE
NIC is the primary network interface. Simple ring-buffer protocol, no
virtio complexity.

### 5. Virtual Devices Everywhere

Eliminate emulation of real hardware wherever possible. Real-device emulation
is justified only when:
- Existing firmware or drivers require it (MC, HPC3, IOC2 for PROM compat)
- No virtual alternative exists (Newport for 2D — though even this gets
  resolution extensions)
- The real device is the only way to run specific software

Everything else should be purpose-built paravirtual hardware: simpler to
implement, faster to emulate, and unconstrained by real-world limitations.

| Component | Real Device | Virtual Replacement | Rationale |
|-----------|------------|-------------------|-----------|
| Memory controller | MC (256MB max) | PV-MEM (1TB) | Real MC is the RAM bottleneck |
| Network | Seeq 80C03 (10Mbps) | PV-NET (10Gbps) | 1000x faster |
| 3D Graphics | Newport (2D only) | GL Accelerator | Host GPU passthrough |
| SMP/IPI | None on IP24 | SMP Controller | Clean MMIO protocol |
| Storage | WD33C93 SCSI | Keep for now | IRIX SCSI stack is complex |
| Serial/RTC/INT3 | Real devices | Keep for now | PROM requires them |

---

## Motivation

The Indy (IP24) is strictly uniprocessor: `MAXCPU=1`, no MP kernel
infrastructure, no IPI mechanism. Its networking is limited to the 10 Mbps
Seeq 80C03, and Newport provides only 2D software-rendered graphics.

Emulating a real SMP SGI platform (e.g., Octane/IP30, Origin/IP27) would
require entirely different chipsets: HEART crossbar, XBOW interconnect,
HUB ASIC, BRIDGE PCI. Instead, we design a **custom platform family** that
reuses our proven IP24 base hardware and adds paravirtual devices for
everything else:

| Feature | IP24 (current) | IP54 (target) |
|---------|---------------|---------------|
| CPU | 1x R4400 | Nx R10000 (N = 1..128) |
| IPI | None | SMP controller |
| RAM | 256 MB (MC limit) | Up to 1 TB (R10000 PABITS=40) |
| Networking | 10 Mbps Seeq | + 10 Gbps PV-NET |
| 2D Graphics | Newport 1280x1024 | Newport at any resolution |
| 3D Graphics | Software OpenGL | GL Accelerator (host GPU) |
| Base HW | MC, HPC3, IOC2 | Retained for PROM compat |

We have the tools to build this: the IP54 PROM already cross-compiles
(`prom-building/`), the IRIX 6.5.7m kernel source includes the IP30 SMP
and IP19 Everest implementations as templates, and we control the full QEMU
hardware definition.

---

## Platform Overview

IP54 is "SGI Indy reimagined as a scalable workstation." It keeps the HPC3
peripheral controller, IOC2 interrupt controller, and Newport graphics for
PROM and driver compatibility, but replaces or augments everything else with
paravirtual hardware. The MC memory controller is extended (or replaced) to
support the full R10000 physical address space.

```
                    +----------+  +----------+          +----------+
                    |  CPU 0   |  |  CPU 1   |  . . .   |  CPU N   |
                    | R10000   |  | R10000   |          | R10000   |
                    +----+-----+  +----+-----+          +----+-----+
                         |             |                      |
              CP0 IP2-IP7|             |CP0 IP2-IP7          |
                         |             |                      |
                    +----+-------------+----------------------+
                    |         SMP Controller                   |
                    |    IPI, boot mailbox, CPU discovery       |  0x1f480000
                    +----------+-------------------------------+
                               |
        +----------+-----------+-----------+----------+----------+
        |          |           |           |          |          |
   +----+---+ +---+---+ +-----+----+ +----+---+ +---+----+     |
   | PV-MEM | | HPC3  | |   IOC2   | |Newport | |  PROM  |     |
   | Ctrl   | |SCSI   | |INT3      | |Graphics| | 512KB  |     |
   |(≤1TB)  | |Serial | |          | |(any res)| |        |     |
   +--------+ |Enet   | +----------+ +--------+ +--------+     |
              |Audio  |                                         |
              +-------+                                         |
                                                                |
        +-------------------------------------------------------+
        |
   +----+-----------------------------+  +---------------------------+
   |  PV-NET (Paravirtual NIC)        |  |  GL Accelerator           |
   |  GIO64 EXP1: 0x1f600000         |  |  GIO64 EXP0: 0x1f400000  |
   |  Ring-buffer TX/RX DMA           |  |  MMIO GL command stream   |
   |  10 Gbps                         |  |  Host GPU passthrough     |
   +----------------------------------+  +---------------------------+
```

---

## Hardware Architecture

### Memory Map

All existing IP24 I/O ranges are preserved. New devices use unused address
space. System memory extends across the full R10000 physical address range:

```
0x00000000-0x07ffffff  Reserved (aliases/unmapped)
0x08000000-0x17ffffff  Low System Memory (256MB, MC compat)     [existing]
0x1f000000-0x1f3fffff  GIO64 Graphics slot (Newport)            [existing]
0x1f400000-0x1f47ffff  GIO64 Expansion 0 (GL Accelerator)       [NEW]
0x1f480000-0x1f4800ff  SMP Controller                            [NEW]
0x1f490000-0x1f4900ff  PV-MEM Controller                         [NEW]
0x1f600000-0x1f9fffff  GIO64 Expansion 1 (PV-NET)               [NEW]
0x1fa00000-0x1fa1ffff  Memory Controller (MC, legacy compat)     [existing]
0x1fb80000-0x1fbfffff  HPC3 Peripheral Controller                [existing]
0x1fc00000-0x1fc7ffff  PROM (512KB)                              [existing]
0x20000000-0x2fffffff  High System Memory segment 1 (256MB)      [existing]
0x30000000-0xFFFFFFFF  Extended Memory (PV-MEM, up to ~3.2GB)    [NEW]
  (with R10000 40-bit PABITS, physical addresses continue above 4GB)
```

Note: The SMP controller at 0x1f480000 is within the GIO64 EXP0 range
(0x1f400000-0x1f5fffff). This is fine — the GL accelerator uses 0x1f400000-
0x1f47ffff (256KB), the SMP controller uses 0x1f480000-0x1f4800ff (256B),
and the PV-MEM controller uses 0x1f490000-0x1f4900ff (256B). All are mapped
as separate MMIO regions in QEMU.

### CPU Interrupt Routing

```
IP0: Software interrupt 0 (per-CPU, unused)
IP1: Software interrupt 1 (per-CPU, unused)
IP2: Local0 cascade (HPC3/IOC2) -- CPU0 only
IP3: Local1 cascade (HPC3/IOC2) -- CPU0 only
IP4: IPI (SMP controller) -- per-CPU          <-- CHANGED from PIT Timer0
IP5: PIT Timer1 -- CPU0 only (PROM use only)  <-- keep for PROM compat
IP6: Bus error (MC) -- CPU0 only
IP7: Count/Compare timer -- per-CPU (scheduling clock)
```

Device interrupts (Local0/Local1) route to CPU0 only. The kernel uses
`sendintr()` to dispatch work to other CPUs via IPI. This matches how IP30
works: HEART interrupts go to a specific CPU and the kernel redistributes
via IPI and the `actionlist` mechanism.

**Rationale for IP4:** The PIT Timer0 interrupt is wired to IP4 in the current
implementation, but IRIX never programs it — the kernel uses Count/Compare on
IP7 for scheduling (see `startrtclock_r4000()`). Repurposing IP4 for IPI has
no impact on existing PROM or kernel functionality.

### CPU Selection: R10000 Family

IP54 uses the MIPS R10000 family (R10K/R12K/R14K/R16K) instead of R4400.
Key advantages over R4400:

| Feature | R4400 | R10000 family |
|---------|-------|---------------|
| PABITS (physical addr) | 36 (64GB) | 40 (1TB) |
| SEGBITS (virtual addr) | 40 | 44 |
| Instruction set | MIPS III | MIPS IV |
| L2 cache | None | 1MB+ (configurable) |
| SMP heritage | Challenge only | Octane, Origin, Challenge |

**The specific R-series model is cosmetic in QEMU.** Since we don't do
cycle-accurate emulation, the differences between R10000, R12000, R14000,
and R16000 are irrelevant — they all implement MIPS IV, and clock speed /
pipeline depth don't affect emulated execution. The only visible difference
is the PRId register (CP0 $15) that IRIX reads to identify the CPU:

| CPU | PRId | Clock (real) | ISA | QEMU Status |
|-----|------|-------------|-----|-------------|
| R10000 | 0x0900 | 175-250 MHz | MIPS IV | Defined |
| R12000 | 0x0E00 | 270-400 MHz | MIPS IV | Not defined (trivial to add) |
| R14000 | 0x0F00 | 500-600 MHz | MIPS IV | Not defined (trivial to add) |
| R16000 | 0x2800 | 700 MHz | MIPS IV | Not defined (trivial to add) |

Adding R14000 or R16000 to QEMU is a ~30-line copy of the R10000 definition
with different PRId and name. IP54 should default to R14000 or R16000 (the
"latest" in the family) and allow selection via `-cpu R10000` etc. if needed.

The PROM and kernel must be compiled for MIPS IV. The MIPSpro compiler
handles this via `-mips4 -r10000` (the `-r10000` flag applies to the entire
R10K family).

---

## SMP Controller

### Register Map (0x1f480000)

All registers are 32-bit, accessed at 64-bit aligned offsets following the SGI
convention of `addr &= ~7ULL` normalization in read/write handlers.

The SMP controller is designed for arbitrary CPU counts. CPU_COUNT returns the
actual number configured. IPI_SET/BOOT_GO use bitmasks; for >32 CPUs, these
become arrays of 32-bit words (offsets 0x10+4*N). Initial implementation
supports up to 128 CPUs (matching IRIX Everest MAXCPU).

```
Offset  Name          Access  Description
------  ----          ------  -----------
0x00    CPU_COUNT     RO      Number of CPUs present (1..128)
0x08    CPU_ID        RO      Current CPU's physical ID (per-CPU, returns
                              current_cpu->cpu_index)
0x10    IPI_SET       WO      Write bitmask to send IPI to target CPUs.
                              Bit N = send IPI to CPU N. Raises CP0 IP4
                              on each target. For >32 CPUs, write to
                              0x10+4*W to target CPUs 32*W..32*W+31.
0x18    IPI_CLEAR     WO      Write 1 to clear IPI for current CPU.
                              Lowers CP0 IP4 for the writing CPU.
0x20    IPI_STATUS    RO      IPI pending bitmask for current CPU.
                              Returns which CPUs have sent pending IPIs.
0x28    BOOT_ADDR     RW      Entry point address for secondary CPU
                              bootstrap. PROM writes a small stub here
                              that polls MPCONF.
0x30    BOOT_GO       WO      Write CPU bitmask to release from reset hold.
                              Bit N = release CPU N. Each bit triggers
                              cpu_resume() for the corresponding halted CPU.
                              The CPU begins execution at BOOT_ADDR.
0x38    BOOT_STATUS   RO      Bitmask of CPUs that have started execution.
                              Bit N = 1 means CPU N has exited reset hold.
```

### Register Semantics

**CPU_ID** returns `current_cpu->cpu_index` so each CPU reading the same
address gets its own physical ID. This avoids needing per-CPU MMIO regions.

**IPI_SET** is a write-only "fire and forget" register. Writing a bitmask
immediately raises CP0 IP4 on each target CPU. The target must read IPI_STATUS
and write IPI_CLEAR in its interrupt handler.

**BOOT_GO** is the mechanism that releases secondary CPUs from the halted
state set at machine init. QEMU creates secondary CPUs with
`start_powered_off = true`. Writing to BOOT_GO calls `cpu_resume()` on each
target, setting its PC to BOOT_ADDR. This happens once during PROM init.

---

## Secondary CPU Bootstrap Protocol

Modeled directly on the IP30 MPCONF mechanism from
`irix/kern/sys/RACER/racermp.h`:

### MPCONF Structure

Located at physical address 0x600 (`K0BASE + 0x600 = 0x80000600`), one 128-byte
entry per CPU:

```c
/* From racermp.h */
#define MPCONF_MAGIC    0xBADDEED2
#define MPCONF_ADDR     (K0BASE + 0x600)    /* 0x80000600 */
#define MPCONF_SIZE     128

typedef struct mpconf_blk {
    uint          mpconf_magic;       /* +0x00: Must be 0xBADDEED2 */
    int           pr_id;              /* +0x04: CP0 PRId register */
    int           phys_id;            /* +0x08: Physical CPU ID */
    int           virt_id;            /* +0x0c: Virtual CPU ID */
    int           scache_size;        /* +0x10: Secondary cache size */
    short         fanloads;           /* +0x14: unused for IP54 */
    ushort        unused2;            /* +0x16: padding */
    volatile void *launch;            /* +0x18: Bootstrap entry point */
    volatile void *rendezvous;        /* +0x20: Completion rendezvous */
    void          *unused3;           /* +0x28: reserved */
    void          *unused4;           /* +0x30: reserved */
    void          *unused5;           /* +0x38: reserved */
    volatile void *stack;             /* +0x40: Stack pointer */
    volatile void *lnch_parm;         /* +0x48: Parameter for launch */
    volatile void *rndv_parm;         /* +0x50: Parameter for rendezvous */
    int           idle_flag;          /* +0x58: Idle loop flag */
    int           unused6;            /* +0x5c: padding */
    __uint64_t    padto128[4];        /* +0x60: Pad to 128 bytes */
} mpconf_t;
```

### Bootstrap Sequence

The sequence below mirrors IP30's boot exactly (see `slave.s` and `IP30.c`):

**Phase 1: PROM initialization**

1. PROM reads `CPU_COUNT` register at 0x1f480000 to discover N CPUs
2. PROM populates `mpconf_t` array at 0x80000600:
   - Sets `mpconf_magic = 0xBADDEED2` for each CPU
   - Sets `pr_id` from CP0 PRId (CPU 0's value, all CPUs are identical)
   - Sets `phys_id = i`, `virt_id = i`
   - Sets `scache_size = 0` (R4400 has no L2 in our config)
   - Sets `launch = NULL` initially
3. PROM writes bootstrap stub address to `BOOT_ADDR` register
4. PROM writes secondary CPU bitmask to `BOOT_GO` to release from reset
5. Secondary CPUs begin executing at `BOOT_ADDR`, which is a small stub
   in PROM that:
   - Reads `CPU_ID` register to determine which CPU this is
   - Indexes into MPCONF array at `MPCONF_ADDR + phys_id * MPCONF_SIZE`
   - Sets `slave_loop_ready[phys_id] = 1`
   - Enters tight polling loop watching `launch` field (like `slave.s`)
6. PROM adds N CPU + N FPU components to ARCS component tree

**Phase 2: Kernel boot (master CPU)**

7. Kernel starts on CPU 0, initializes all hardware (MC, HPC3, IOC2)
8. Kernel calls `allowboot()` which iterates enabled CPUs:

```c
/* From IP30.c:571-651, adapted for IP54 */
void allowboot(void)
{
    for (i = 0; i < MAXCPU; i++) {
        if (!cpu_enabled(i) || i == master_procid)
            continue;
        if (!slave_loop_ready[i]) {
            cpu_disable(i);
            continue;
        }
        mpconf = (mpconf_t *)(MPCONF_ADDR + i * MPCONF_SIZE);
        mpconf->lnch_parm = mpconf;
        mpconf->rendezvous = NULL;
        mpconf->stack = pdaindr[i].pda->p_bootlastframe;
        mpconf->launch = (void *)bootstrap;  /* triggers slave */
    }
    /* Wait for all slaves to call cboot() */
    while (slave_cpus) {
        if (cb_wait != -1)
            dobootduty();
        DELAY(1);
    }
}
```

**Phase 3: Secondary CPU bootstrap**

9. Secondary CPU sees non-NULL `launch`, loads stack pointer, clears `launch`,
   jumps to `bootstrap()` with `lnch_parm` as argument
10. `bootstrap()` (assembly) sets up TLB, wires PDA, jumps to `cboot()`
11. `cboot()` acquires boot lock, signals master via `cb_wait`, waits for
    master to allocate resources, then enters scheduler

### Slave Loop (Assembly)

Adapted from `irix/kern/ml/RACER/slave.s`:

```asm
# slave_loop -- Secondary CPU waits for launch address
# a0 = pointer to this CPU's mpconf_t
LEAF(slave_loop)
    .set noreorder
    lw    t0, MP_VIRTID(a0)
    LA    t1, slave_loop_ready
    addu  t0, t1
    li    t1, 1
    sb    t1, 0(t0)              # slave_loop_ready[virt_id] = 1
1:
    li    t0, 0xffffff           # delay counter (avoid bus hogging)
2:  bne   t0, zero, 2b
    subu  t0, 1                  # BDSLOT
    PTR_L t0, MP_LAUNCHOFF(a0)   # read launch pointer
    beq   t0, zero, 1b
    nop                          # BDSLOT
    PTR_L sp, MP_STACKADDR(a0)   # load stack
    PTR_S zero, MP_LAUNCHOFF(a0) # clear launch (acknowledge)
    PTR_L a0, MP_LPARM(a0)       # load launch parameter
    jal   t0                     # jump to bootstrap()
    nop                          # BDSLOT
    END(slave_loop)
```

---

## High Memory Architecture

### Problem

The IP24 MC memory controller supports a maximum of 256MB across 4 banks.
Its MEMCFG register format uses an 8-bit base field (`physical >> 22`)
and a 5-bit size field (`(MB/4)-1`), limiting individual banks to 128MB
and total addressable memory to the low 32-bit physical space.

For 128GB+ RAM, we need to bypass MC's memory mapping entirely.

### PV-MEM Controller (0x1f490000)

A paravirtual memory controller that tells the PROM and kernel how much
RAM is available and where it is mapped. The actual memory backing is
configured by QEMU's `-m` flag — PV-MEM just provides the discovery
mechanism.

```
Offset  Name            Access  Description
------  ----            ------  -----------
0x00    MEM_ID          RO      Magic: 0x50564D45 ("PVME")
0x08    MEM_TOTAL_LO    RO      Total RAM in bytes (low 32 bits)
0x10    MEM_TOTAL_HI    RO      Total RAM in bytes (high 32 bits)
0x18    MEM_SEGMENT_COUNT RO    Number of physical memory segments
0x20    MEM_SEG_SEL     WO      Select segment index for queries
0x28    MEM_SEG_BASE_LO RO      Selected segment base (low 32)
0x30    MEM_SEG_BASE_HI RO      Selected segment base (high 32)
0x38    MEM_SEG_SIZE_LO RO      Selected segment size in bytes (low 32)
0x40    MEM_SEG_SIZE_HI RO      Selected segment size in bytes (high 32)
0x48    MEM_PAGE_SIZE   RO      Page size in bytes (16384 for R10000)
```

### Memory Layout Strategy

QEMU maps RAM as contiguous physical memory, split into segments to avoid
the I/O hole:

```
Segment 0:  0x08000000 - 0x17ffffff   (256 MB, legacy-compatible)
Segment 1:  0x20000000 - 0x2fffffff   (256 MB, legacy high mem)
Segment 2:  0x40000000 - end          (remainder, up to PABITS limit)
```

For R10000 (PABITS=40), physical addresses extend to 0xFF_FFFFFFFF (1TB).
The PROM populates ARCS memory descriptors from PV-MEM segment info. The
kernel discovers memory the same way it always does — via the ARCS memory
descriptor table.

### MC Compatibility

The MC is retained for PROM compatibility (RPSS counter, SysID, error
registers, GIO config). Its MEMCFG registers still work but only describe
the first 256MB. The PROM uses PV-MEM for actual memory discovery and
falls back to MC MEMCFG only if PV-MEM is absent (for backward compat
with IP24 mode).

### IRIX Kernel Considerations

The IRIX 6.5 kernel source comments state "64 bit kernels support up to
16 GB of memory." However, Origin 2000 systems shipped with 256-512GB,
so the kernel clearly supports more on platforms that provide it. The key
is the memory descriptor table format and the node/NASID addressing scheme.

For IP54, we use a flat (non-NUMA) memory model — all RAM is equidistant
from all CPUs. This matches the Challenge/Everest model rather than Origin's
NUMA. The kernel platform file (`IP54.c`) populates `pfdat` from ARCS
memory descriptors just like IP22 and IP30 do.

**Testing will determine the practical ceiling.** We should test at:
- 512MB (baseline)
- 4GB (32-bit boundary)
- 16GB (documented kernel limit)
- 64GB (R4400 physical limit)
- 128GB+ (user target, R10000 only)

---

## QEMU Implementation

### New File: `qemu/hw/misc/sgi_smp.c` (~400 lines)

```c
#include "qemu/osdep.h"
#include "hw/sysbus.h"
#include "hw/irq.h"
#include "qom/object.h"
#include "exec/cpu-common.h"

#define TYPE_SGI_SMP "sgi-smp"
OBJECT_DECLARE_SIMPLE_TYPE(SGISMPState, SGI_SMP)

#define SGI_SMP_MAXCPU    128   /* Matches IRIX Everest MAXCPU */
#define SGI_SMP_MMIO_SIZE 0x100

/* Register offsets (64-bit aligned) */
#define REG_CPU_COUNT     0x00
#define REG_CPU_ID        0x08
#define REG_IPI_SET       0x10
#define REG_IPI_CLEAR     0x18
#define REG_IPI_STATUS    0x20
#define REG_BOOT_ADDR     0x28
#define REG_BOOT_GO       0x30
#define REG_BOOT_STATUS   0x38

struct SGISMPState {
    SysBusDevice parent_obj;

    MemoryRegion mmio;
    uint32_t num_cpus;
    uint32_t boot_addr;
    uint32_t boot_status;

    /* Per-CPU state */
    bool     ipi_pending[SGI_SMP_MAXCPU];
    bool     started[SGI_SMP_MAXCPU];
    qemu_irq ipi_irq[SGI_SMP_MAXCPU];  /* -> each CPU's CP0 IP4 */

    /* CPU references for BOOT_GO */
    CPUState *cpus[SGI_SMP_MAXCPU];
};
```

**Key methods:**

- **`sgi_smp_read()`**: Returns CPU_COUNT, CPU_ID (from `current_cpu->cpu_index`),
  IPI_STATUS, BOOT_STATUS. Normalizes address with `addr &= ~7ULL`.

- **`sgi_smp_write()`**: Handles IPI_SET (calls `qemu_irq_raise()` on targets),
  IPI_CLEAR (calls `qemu_irq_lower()` on current CPU), BOOT_ADDR (stores entry
  point), BOOT_GO (calls `cpu_resume()` on halted secondary CPUs).

- **Properties**: `num-cpus` (uint32, default 2).

### New Header: `qemu/include/hw/misc/sgi_smp.h`

```c
#ifndef SGI_SMP_H
#define SGI_SMP_H

#include "hw/sysbus.h"

#define TYPE_SGI_SMP "sgi-smp"

#define SGI_SMP_MAXCPU    128
#define SGI_SMP_BASE_ADDR 0x1f480000

/* Register offsets */
#define SGI_SMP_CPU_COUNT     0x00
#define SGI_SMP_CPU_ID        0x08
#define SGI_SMP_IPI_SET       0x10
#define SGI_SMP_IPI_CLEAR     0x18
#define SGI_SMP_IPI_STATUS    0x20
#define SGI_SMP_BOOT_ADDR     0x28
#define SGI_SMP_BOOT_GO       0x30
#define SGI_SMP_BOOT_STATUS   0x38

#endif /* SGI_SMP_H */
```

### Modified: `qemu/hw/mips/sgi_indy.c`

Changes to support multi-CPU creation:

```c
/* In sgi_ip54_init() */

/* Create N CPUs -- R10000 is the default cpu_type */
int ncpus = machine->smp.cpus;  /* 1..128 */
MIPSCPU **cpus = g_new0(MIPSCPU *, ncpus);
for (int i = 0; i < ncpus; i++) {
    cpus[i] = mips_cpu_create_with_clock(machine->cpu_type, cpuclk,
                                          TARGET_BIG_ENDIAN);
    cpu_mips_irq_init_cpu(cpus[i]);
    cpu_mips_clock_init(cpus[i]);

    if (i == 0) {
        qemu_register_reset(main_cpu_reset, cpus[i]);
    } else {
        /* Secondary CPUs start halted */
        CPUState *cs = CPU(cpus[i]);
        cs->start_powered_off = true;
        qemu_register_reset(secondary_cpu_reset, cpus[i]);
    }
}

/* Wire HPC3/IOC2 interrupts to CPU0 only */
CPUMIPSState *env0 = &cpus[0]->env;
/* ... existing interrupt wiring using env0->irq[2..6] ... */

/* Create SMP controller */
DeviceState *smp = qdev_new(TYPE_SGI_SMP);
qdev_prop_set_uint32(smp, "num-cpus", ncpus);
sysbus_realize_and_unref(SYS_BUS_DEVICE(smp), &error_fatal);
sysbus_mmio_map(SYS_BUS_DEVICE(smp), 0, SGI_SMP_BASE_ADDR);

/* Wire IPI outputs to each CPU's IP4 */
SGISMPState *smp_state = SGI_SMP(smp);
for (int i = 0; i < ncpus; i++) {
    smp_state->cpus[i] = CPU(cpus[i]);
    sysbus_connect_irq(SYS_BUS_DEVICE(smp), i, cpus[i]->env.irq[4]);
}

/* Disconnect PIT Timer0 from IP4 (was: pit_irq[0] -> env->irq[4]) */
/* PIT Timer0 now unused; PIT Timer1 stays on CPU0 IP5 for PROM compat */
```

**Machine class changes:**

```c
static void indy_machine_class_init(ObjectClass *oc, void *data)
{
    MachineClass *mc = MACHINE_CLASS(oc);
    /* ... existing fields ... */
    mc->max_cpus = SGI_SMP_MAXCPU;  /* Allow up to 128 CPUs */
    mc->default_cpus = 1;            /* Default to 1 for compatibility */
    mc->default_cpu_type = MIPS_CPU_TYPE_NAME("R10000");
}
```

### Build Integration

**`qemu/hw/misc/Kconfig`** -- add:
```
config SGI_SMP
    bool
```

**`qemu/hw/misc/meson.build`** -- add:
```
system_ss.add(when: 'CONFIG_SGI_SMP', if_true: files('sgi_smp.c'))
```

**`qemu/hw/mips/Kconfig`** -- add SGI_SMP dependency to SGI_INDY:
```
config SGI_INDY
    bool
    # ... existing deps ...
    select SGI_SMP
```

---

## PROM Changes

### Modified: `prom-building/src/fw/ip54_stubs.c`

**1. Read CPU count from hardware:**

```c
#define SMP_CTRL_BASE   0xBF480000UL  /* kseg1 address */
#define SMP_CPU_COUNT   0x00
#define SMP_CPU_ID      0x08
#define SMP_BOOT_ADDR   0x28
#define SMP_BOOT_GO     0x30

static int get_cpu_count(void)
{
    volatile uint32_t *smp = (volatile uint32_t *)SMP_CTRL_BASE;
    return smp[SMP_CPU_COUNT / 4];
}
```

**2. Multi-CPU component tree:**

```c
/* In init_component_tree() -- replace single CPU/FPU with loop */
int ncpus = get_cpu_count();
for (int i = 0; i < ncpus; i++) {
    /* CPU */
    tmpl.Class = ProcessorClass;
    tmpl.Type = CPU;
    tmpl.Key = i;
    tmpl.AffinityMask = 1 << i;
    tmpl.Identifier = id_cpu;  /* "MIPS-R10000" */
    cpu_c = AddChild(root, &tmpl, NULL);

    /* FPU */
    tmpl.Type = FPU;
    tmpl.Key = i;
    tmpl.Identifier = id_fpu;
    AddChild(cpu_c, &tmpl, NULL);

    /* Caches (per-CPU) */
    /* ... PrimaryICache, PrimaryDCache as before ... */
}
```

**3. MPCONF population:**

```c
void init_mpconf(void)
{
    int ncpus = get_cpu_count();
    mpconf_t *mpconf;
    uint32_t prid;

    /* Read CPU 0's PRId */
    __asm__ volatile("mfc0 %0, $15" : "=r"(prid));

    for (int i = 0; i < ncpus; i++) {
        mpconf = (mpconf_t *)(0x80000600 + i * 128);
        mpconf->mpconf_magic = 0xBADDEED2;
        mpconf->pr_id = prid;
        mpconf->phys_id = i;
        mpconf->virt_id = i;
        mpconf->scache_size = 0;
        mpconf->launch = NULL;
        mpconf->rendezvous = NULL;
        mpconf->stack = NULL;
        mpconf->lnch_parm = NULL;
        mpconf->rndv_parm = NULL;
        mpconf->idle_flag = 0;
    }
}
```

**4. Secondary CPU release:**

```c
/* Bootstrap stub for secondary CPUs -- placed in PROM space */
extern void secondary_entry(void);  /* defined in assembly */

void release_secondary_cpus(void)
{
    int ncpus = get_cpu_count();
    volatile uint32_t *smp = (volatile uint32_t *)(SMP_CTRL_BASE);

    if (ncpus <= 1)
        return;

    /* Set bootstrap entry point */
    smp[SMP_BOOT_ADDR / 4] = (uint32_t)secondary_entry;

    /* Release all secondary CPUs (bits 1..N) */
    uint32_t mask = ((1 << ncpus) - 1) & ~1;  /* all except CPU 0 */
    smp[SMP_BOOT_GO / 4] = mask;

    stub_puts("[IP54] Released secondary CPUs, mask=");
    stub_puthex(mask);
    stub_puts("\n");
}
```

**5. Secondary entry point (new assembly file):**

```asm
/* prom-building/src/fw/secondary_boot.S */
#include <sys/asm.h>
#include <sys/regdef.h>

#define SMP_CTRL_BASE   0xBF480000
#define SMP_CPU_ID      0x08
#define MPCONF_ADDR     0x80000600
#define MPCONF_SIZE     128
#define MP_LAUNCHOFF    0x18
#define MP_STACKADDR    0x40
#define MP_LPARM        0x48

LEAF(secondary_entry)
    .set noreorder

    /* Read our CPU ID from SMP controller */
    li    t0, SMP_CTRL_BASE
    lw    t1, SMP_CPU_ID(t0)       /* t1 = our physical CPU ID */

    /* Calculate MPCONF address for this CPU */
    li    t2, MPCONF_ADDR
    li    t3, MPCONF_SIZE
    multu t1, t3
    mflo  t3
    addu  a0, t2, t3               /* a0 = &MPCONF[cpu_id] */

    /* Signal ready (write to slave_loop_ready array) */
    LA    t4, slave_loop_ready
    addu  t4, t4, t1
    li    t5, 1
    sb    t5, 0(t4)

    /* Poll MPCONF launch address */
1:  li    t0, 0xffffff
2:  bne   t0, zero, 2b
    subu  t0, 1                    /* BDSLOT: delay loop */

    PTR_L t0, MP_LAUNCHOFF(a0)
    beq   t0, zero, 1b
    nop                            /* BDSLOT */

    /* Launch: load stack, clear launch, jump */
    PTR_L sp, MP_STACKADDR(a0)
    PTR_S zero, MP_LAUNCHOFF(a0)
    PTR_L a0, MP_LPARM(a0)
    jal   t0
    nop                            /* BDSLOT */

    /* NOTREACHED */
    .set reorder
    END(secondary_entry)
```

**6. System ID change:**

```c
static SYSTEMID sys_id = {
    { 'S', 'G', 'I', '\0', '\0', '\0', '\0', '\0' },
    { 'I', 'P', '5', '4', '\0', '\0', '\0', '\0' }
};
```

**7. Memory discovery from PV-MEM:**

```c
void init_memory_descriptors(void)
{
    volatile uint32_t *pvmem = (volatile uint32_t *)0xBF490000UL;

    /* Check for PV-MEM controller */
    if (pvmem[0] == 0x50564D45) {  /* "PVME" */
        /* Use PV-MEM for large memory discovery */
        uint64_t total = ((uint64_t)pvmem[0x10/4] << 32) | pvmem[0x08/4];
        int nsegs = pvmem[0x18/4];
        for (int i = 0; i < nsegs; i++) {
            pvmem[0x20/4] = i;  /* select segment */
            uint64_t base = ((uint64_t)pvmem[0x30/4] << 32) | pvmem[0x28/4];
            uint64_t size = ((uint64_t)pvmem[0x40/4] << 32) | pvmem[0x38/4];
            add_memory_descriptor(base, size, FreeMemory);
        }
    } else {
        /* Fall back to MC MEMCFG probing (IP24 compat) */
        probe_memcfg();
    }
}
```

**8. Call order in `_init_saio()`:**

```c
void _init_saio(void)
{
    init_spb();
    init_fd_table();
    init_mpconf();            /* NEW: populate MPCONF array */
    init_component_tree();    /* MODIFIED: N CPUs in tree */
    init_memory_descriptors();/* MODIFIED: PV-MEM for large memory */
    release_secondary_cpus(); /* NEW: wake secondaries into slave_loop */
}
```

---

## IRIX Kernel Changes

This is the most complex part. The kernel needs a new platform file that reuses
IP22 hardware initialization but adds the SMP mechanisms from IP30.

### New File: `irix/kern/ml/IP54.c` (~800 lines)

Modeled on `irix/kern/ml/RACER/IP30.c`, using IP22 hardware setup:

```c
/* Key functions (abbreviated) */

/* Platform initialization -- reuse IP22 hardware setup */
void
mlreset(int slave)
{
    if (!slave) {
        /* Master CPU init: MC, HPC3, IOC2 -- same as IP22 */
        mc_init();
        hpc3_init();
        ioc2_init();

        /* Detect CPUs */
        maxcpus = smp_get_cpu_count();
        for (int i = 0; i < maxcpus; i++)
            processor_enabled[i] = 1;

        /* Install IPI interrupt handler */
        install_cpuintr();
    } else {
        /* Slave CPU init: per-CPU hardware setup */
        install_cpuintr();  /* IPI handler on this CPU too */
    }
}

/* Check if CPU exists in hardware */
int
cpu_exists(int id)
{
    volatile uint32_t *smp = (volatile uint32_t *)SMP_CTRL_KSEG1;
    int count = smp[SMP_CPU_COUNT / 4];
    return (id >= 0 && id < count);
}

int
cpu_enabled(cpuid_t id)
{
    return processor_enabled[id];
}

/* Send IPI to another CPU */
int
sendintr(cpuid_t destid, unchar status)
{
    volatile uint32_t *smp = (volatile uint32_t *)SMP_CTRL_KSEG1;
    ASSERT(status == DOACTION);
    smp[SMP_IPI_SET / 4] = (1 << destid);
    return 0;
}

/* IPI interrupt handler (installed on IP4) */
static void
smp_ipi_handler(eframe_t *ep, void *arg)
{
    volatile uint32_t *smp = (volatile uint32_t *)SMP_CTRL_KSEG1;

    /* Clear our IPI */
    smp[SMP_IPI_CLEAR / 4] = 1;

    /* Process pending actions (borrowed from IP30) */
    docallout_check();
    doacvec();  /* process actionlist entries */
}

/* Install IPI handler on current CPU's IP4 */
static void
install_cpuintr(void)
{
    if (setcrimevector(4, 0, smp_ipi_handler, NULL))
        cmn_err(CE_PANIC, "Cannot install IPI handler");
}

/* Release slave CPUs -- called from main() after system is ready */
void
allowboot(void)
{
    /* Identical pattern to IP30.c:571-711 */
    for (i = 0; i < MAXCPU; i++) {
        if (!cpu_enabled(i) || i == master_procid)
            continue;
        if (!slave_loop_ready[i]) {
            cpu_disable(i);
            continue;
        }
        mpconf = (mpconf_t *)(MPCONF_ADDR + i * MPCONF_SIZE);
        mpconf->lnch_parm = mpconf;
        mpconf->stack = pdaindr[i].pda->p_bootlastframe;
        mpconf->launch = (void *)bootstrap;
    }
    /* Wait for slaves to complete cboot() */
    timeout = BOOTTIMEOUT * slave_cpus;
    while (--timeout && slave_cpus) {
        if (cb_wait != -1) {
            dobootduty();
            slave_cpus--;
        }
        DELAY(1);
    }
}

/* Secondary CPU C entry point */
void
cboot(void)
{
    /* Identical pattern to IP30.c:721-818 */
    int id = getcpuid();
    wirepda(pdaindr[id].pda);
    mlreset(1);                   /* slave init */
    private.p_cpuid = id;
    /* ... TLB setup, exception vectors, etc ... */
    /* Signal master that we're ready */
    cb_wait = id;
    /* Wait for master to allocate resources */
    while (cb_wait != -1)
        ;
    /* Mark ourselves as enabled */
    private.p_flags |= PDAF_ENABLED;
    atomicAddInt(&numcpus, 1);
    /* Enter scheduler -- never returns */
    spl0();
    idle();
}
```

### New Header: `irix/kern/sys/IP54.h` (~200 lines)

```c
#ifndef __SYS_IP54_H__
#define __SYS_IP54_H__

/* IP54: Custom SMP platform — R10000, up to 128 CPUs, 1TB RAM */

#define MAXCPU          128     /* Matches Everest; can reduce for testing */
#define MPCONF_MAGIC    0xBADDEED2
#define MPCONF_ADDR     (K0BASE + 0x600)
#define MPCONF_SIZE     128

/* SMP Controller registers (kseg1 addresses) */
#define SMP_CTRL_BASE       0x1f480000
#define SMP_CTRL_KSEG1      (SMP_CTRL_BASE | 0xA0000000)

#define SMP_CPU_COUNT       0x00
#define SMP_CPU_ID          0x08
#define SMP_IPI_SET         0x10
#define SMP_IPI_CLEAR       0x18
#define SMP_IPI_STATUS      0x20
#define SMP_BOOT_ADDR       0x28
#define SMP_BOOT_GO         0x30
#define SMP_BOOT_STATUS     0x38

/* Reuse IP22/IP24 hardware definitions for MC, HPC3, IOC2 */
#include <sys/IP22.h>

/* IPI is on CP0 IP4 (Cause bit 12, SR_IBIT5) */
#define SMP_IPI_INTR_LEVEL  4

/* MPCONF structure (identical to racermp.h) */
#include <sys/RACER/racermp.h>

#endif /* __SYS_IP54_H__ */
```

### New Assembly: `irix/kern/ml/IP54asm.s` (~100 lines)

```asm
/* bootstrap -- Secondary CPU entry point called from MPCONF launch */
#include <sys/asm.h>
#include <sys/regdef.h>
#include <sys/sbd.h>
#include <sys/cpu.h>

LEAF(bootstrap)
    .set noreorder

    /* a0 = pointer to mpconf_t (set by slave_loop) */

    /* Set up Status register: BEV=0, KSU=kernel, IE=0 */
    li    t0, SR_CU0|SR_CU1|SR_FR|SR_KX
    mtc0  t0, C0_SR

    /* Clear TLB */
    jal   tlbclear
    nop

    /* Wire PDA for this CPU */
    lw    a0, MP_VIRTID(a0)      /* CPU ID */
    jal   wirepda_slave
    nop

    /* Jump to C entry */
    jal   cboot
    nop

    /* NOTREACHED */
    .set reorder
    END(bootstrap)
```

### Kernel Build Process

The kernel must be built on the running IRIX 6.5 system (requires `smake` +
MIPSpro toolchain):

1. Transfer `IP54.c`, `IP54.h`, `IP54asm.s` to IRIX filesystem via telnet/scp
2. Extract pre-built kernel objects from `irix-bld.cpio` if available
3. Modify kernel build config:
   - Set `CPUBOARD = IP54`
   - Define `MP` for multiprocessor
   - Set `MAXCPU = 4`
   - Link `IP54.c` + `IP54asm.s` instead of `IP22.c`
4. Build with `smake`: recompile platform files, relink `/unix`
5. Copy new kernel to QEMU disk image
6. Boot with new kernel

### Important Constraints

**MIPS64 MTTCG:** QEMU's multi-threaded TCG is **not** supported for MIPS64:

```c
/* qemu/target/mips/cpu.c:602 */
.mttcg_supported = TARGET_LONG_BITS == 32,
```

This was disabled in commit a092a95547 (March 2020) due to a 9% failure rate
in SMP stability tests that was never debugged. All vCPUs run round-robin on
one host thread. SMP is **functionally correct** but not host-parallel.

**What this means practically:**
- The IRIX kernel will see and schedule across all configured CPUs
- Processes can be bound to specific CPUs, locks work, `mpstat` shows activity
- MP-only software (e.g., `make -j N`) runs correctly
- But all N vCPUs share one host thread, so N CPUs ≠ N× throughput
- If MTTCG is fixed upstream, IP54 gains real parallelism with no changes

**icount + SMP:** `-icount` disables MTTCG entirely (even on MIPS32). Since we
use icount for kernel boot, this is fine — round-robin is the only option
anyway.

**Device interrupts:** Only CPU0 receives HPC3/IOC2 interrupts. The kernel
dispatches work to other CPUs via `sendintr()` / IPI / `actionlist`. This
matches SGI SMP convention (IP30, IP19, IP27 all work this way).

**R10K family for SMP:** The R10000 family was the CPU in all SGI SMP systems.
Using R4400 for SMP would be ahistorical — IRIX's MP kernel code paths were
written for R10000+. MIPS IV is a superset of MIPS III (R4400), so all
existing R4400 PROM code is forward-compatible. The specific R-series model
(R10K/R12K/R14K/R16K) is cosmetic in QEMU — same ISA, different PRId.

---

## Paravirtual 10GbE Networking

### Motivation

The Seeq 80C03 is a 10 Mbps controller. Transferring files to/from the guest
is painfully slow. A paravirtual NIC delivers 10 Gbps speeds with lower
emulation overhead by using a simple ring-buffer protocol — no virtio
complexity, no endianness friction.

PV-NET coexists with Seeq: IRIX can have multiple ethernet interfaces
(`ec0` = Seeq, `et0` = PV-NET). The Seeq remains for compatibility; PV-NET
is the primary high-speed interface.

### PV-NET Registers (0x1f600000)

GIO64 EXP1 slot, MMIO register interface:

```
Offset  Name            Access  Description
------  ----            ------  -----------
0x00    NET_ID          RO      Magic ID: 0x50564E45 ("PVNE")
0x08    NET_VERSION     RO      Version: 0x00010000 (1.0)
0x10    NET_STATUS      RO      Bit 0: link up, bit 1: TX ready
0x18    NET_INTR        RW      Interrupt control/status
                                  Bit 0: RX interrupt enable
                                  Bit 1: TX interrupt enable
                                  Bit 4: RX interrupt pending (W1C)
                                  Bit 5: TX interrupt pending (W1C)
0x20    NET_MAC_HI      RW      MAC address bytes 0-3
0x28    NET_MAC_LO      RW      MAC address bytes 4-5 (upper 16 bits)
0x30    NET_MTU         RO      Maximum transfer unit (9000 = jumbo)
0x38    NET_SPEED       RO      Link speed in Mbps (10000)
0x40    TX_RING_BASE    RW      TX ring physical base address
0x48    TX_RING_SIZE    RW      TX ring entry count (power of 2)
0x50    TX_PROD         RW      TX producer index (driver writes)
0x58    TX_CONS         RO      TX consumer index (device writes)
0x60    TX_KICK         WO      Write 1 to notify device of new TX entries
0x80    RX_RING_BASE    RW      RX ring physical base address
0x88    RX_RING_SIZE    RW      RX ring entry count (power of 2)
0x90    RX_PROD         RO      RX producer index (device writes)
0x98    RX_CONS         RW      RX consumer index (driver writes)
0xa0    RX_KICK         WO      Write 1 to refill RX ring after consuming
```

### Ring Buffer Entry Format

Each ring entry is 16 bytes, big-endian:

```c
struct pvnet_desc {
    uint32_t addr;       /* Physical address of data buffer */
    uint32_t len;        /* TX: packet length. RX: buffer size */
    uint32_t flags;      /* Bit 31: OWN (1=device owns, 0=driver owns)
                            Bit 0: EOP (end of packet)
                            Bit 1: SOP (start of packet)
                            Bit 4: CSUM_OK (RX: checksum valid) */
    uint32_t vlan_tag;   /* Reserved for VLAN (future) */
};
```

### Interrupt Delivery

PV-NET uses **INT3 local0 bit 0** (GIO slot 1 interrupt), the same mechanism
used by real GIO expansion cards. IOC2 routes GIO slot interrupts through the
INT3 cascade to CPU0's IP2. No new interrupt path needed.

### Why Not Virtio?

Virtio-mmio would require:
1. An IRIX virtio-mmio driver (virtqueues, feature negotiation, endian issues)
2. Virtio-net on top (control virtqueue, GSO, checksums)
3. Big-endian guest + little-endian virtio spec = constant byte-swapping

A custom simple protocol is ~500 lines on the IRIX side vs ~2000+ for virtio,
and avoids endianness friction since we control both sides. This is a prime
example of the "virtual devices everywhere" principle — the Seeq 80C03 is
retained for backward compatibility, but PV-NET eliminates 1000× of
complexity (DMA descriptor format, bank-selected registers, address filtering)
while delivering 1000× the throughput.

### QEMU Implementation: `qemu/hw/misc/sgi_pvnet.c` (~500 lines)

```c
#define TYPE_SGI_PVNET "sgi-pvnet"

struct SGIPVNETState {
    SysBusDevice parent_obj;
    MemoryRegion mmio;
    NICState *nic;
    NICConf conf;
    qemu_irq irq;          /* -> INT3 local0 */

    /* Registers */
    uint32_t mac_hi, mac_lo;
    uint32_t intr;
    uint32_t tx_ring_base, tx_ring_size, tx_prod, tx_cons;
    uint32_t rx_ring_base, rx_ring_size, rx_prod, rx_cons;
};
```

**TX path:**
1. Driver writes descriptor(s) to TX ring, advances TX_PROD
2. Driver writes TX_KICK
3. QEMU reads descriptors from guest memory (`address_space_read`)
4. Assembles packet from buffer pointers
5. Calls `qemu_send_packet()` (same API as Seeq)
6. Advances TX_CONS, optionally raises TX interrupt

**RX path:**
1. `pvnet_receive()` callback called by QEMU network layer
2. Writes packet data to guest buffer at `RX_RING[rx_prod].addr`
3. Updates descriptor flags (SOP, EOP, length)
4. Advances RX_PROD
5. Raises RX interrupt if enabled

### IRIX Driver: `if_pvnet.c` (Loadable Kernel Module)

Follows the `struct etherifops` pattern from `irix/kern/bsd/misc/ether.h`:

```c
/* Module version and flags */
char *if_pvnetmversion = M_VERSION;
int if_pvnetdevflag = D_MP;

/* Operations table */
static struct etherifops pvnet_ops = {
    pvnet_init,
    pvnet_reset,
    pvnet_watchdog,
    pvnet_transmit,
    pvnet_ioctl,
};

/* Initialization */
static int
pvnet_init(struct etherif *eif, int flags)
{
    volatile uint32_t *regs = (volatile uint32_t *)PVNET_BASE;

    /* Verify device presence */
    if (regs[0] != 0x50564E45)  /* "PVNE" */
        return ENODEV;

    /* Allocate TX/RX rings */
    pvnet_sc->tx_ring = kmem_zalloc(TX_RING_SIZE * sizeof(pvnet_desc_t), KM_NOSLEEP);
    pvnet_sc->rx_ring = kmem_zalloc(RX_RING_SIZE * sizeof(pvnet_desc_t), KM_NOSLEEP);

    /* Program ring base addresses */
    regs[TX_RING_BASE / 4] = kvtophys(pvnet_sc->tx_ring);
    regs[RX_RING_BASE / 4] = kvtophys(pvnet_sc->rx_ring);
    regs[TX_RING_SIZE / 4] = TX_RING_ENTRIES;
    regs[RX_RING_SIZE / 4] = RX_RING_ENTRIES;

    /* Allocate RX buffers */
    for (int i = 0; i < RX_RING_ENTRIES; i++) {
        struct mbuf *m = m_get(M_DONTWAIT, MT_DATA);
        pvnet_sc->rx_ring[i].addr = kvtophys(mtod(m, caddr_t));
        pvnet_sc->rx_ring[i].len = MCLBYTES;
        pvnet_sc->rx_ring[i].flags = PVNET_OWN;  /* device owns */
    }

    /* Enable interrupts */
    regs[NET_INTR / 4] = 0x03;  /* RX + TX interrupt enable */

    return 0;
}

/* Transmit */
static int
pvnet_transmit(struct etherif *eif, struct etheraddr *edst,
               struct etheraddr *esrc, u_short type, struct mbuf *m)
{
    /* Fill TX descriptor */
    pvnet_desc_t *desc = &pvnet_sc->tx_ring[pvnet_sc->tx_prod];
    desc->addr = kvtophys(mtod(m, caddr_t));
    desc->len = m->m_len;
    desc->flags = PVNET_SOP | PVNET_EOP;

    /* Advance producer and kick */
    pvnet_sc->tx_prod = (pvnet_sc->tx_prod + 1) % TX_RING_ENTRIES;
    regs[TX_PROD / 4] = pvnet_sc->tx_prod;
    regs[TX_KICK / 4] = 1;

    return 0;
}

/* Registration */
void
if_pvnetinit(void)
{
    ether_attach(&pvnet_eif, "et", 0, &pvnet_sc, &pvnet_ops, &pvnet_addr,
                 INV_ETHER_EE, 0);
    pvnet_eif.eif_arpcom.ac_if.if_baudrate.ifs_value = 1250000000;
    pvnet_eif.eif_arpcom.ac_if.if_baudrate.ifs_log2 = 3;  /* 10 Gbps */
}
```

**Compile and load on running IRIX:**

```bash
cc -G 0 -jalr -non_shared -c if_pvnet.c
ld -r -d -G 0 if_pvnet.o -o if_pvnet.o.loadable
ml load -t enet -s pvnet -m if_pvnet.o.loadable
ifconfig et0 inet 10.0.2.15 netmask 255.255.255.0 up
```

---

## Graphics Architecture

### Design Philosophy

The goal is to run graphics demos and applications from across the SGI product
line: O2 (CRIME graphics), Octane (Impact/MarvelU), InfiniteReality, etc.
Rather than faithfully emulating each graphics ASIC (which took SGI thousands
of engineer-years), we use the **GL Accelerator** — a paravirtual device that
intercepts OpenGL calls and forwards them to the host GPU.

This is another instance of the "virtual devices everywhere" principle. Real
SGI graphics hardware had wildly different architectures:
- **Newport (Indy):** 2D framebuffer, GIO64 bus, software GL
- **CRIME (O2):** Unified memory architecture, shared framebuffer
- **Impact (Octane):** Geometry engine + rasterizer, XIO bus
- **InfiniteReality:** Multi-pipe, texture memory, display generator

Emulating any of these at the register level is a multi-year effort. The GL
Accelerator sidesteps this entirely: IRIX applications issue GL calls, which
get redirected to MMIO writes, which QEMU forwards to the host GPU. The result
is hardware-accelerated 3D at native speed, regardless of which SGI graphics
subsystem the application was written for.

**Application compatibility:** Most SGI demos use standard OpenGL 1.x, not
hardware-specific APIs. `ideas`, `flight`, `atlantis` — these all go through
`libGL.so` → `libGLcore.so`. By patching `libGLcore.so` (which we already
have tooling for), we intercept at the GL level, above any hardware differences.

For applications that use hardware-specific interfaces (e.g., direct CRIME
register access, Impact DMA), those would need per-architecture stubs. But
the vast majority of SGI graphics software uses the GL API.

### Newport (2D Baseline)

Newport remains the 2D graphics engine for window management, text rendering,
and the X11 display. It has been extended with configurable resolution (see
[Arbitrary Display Resolution](#arbitrary-display-resolution)). The GL
Accelerator composites its 3D output on top of Newport's 2D framebuffer.

### GL Accelerator

The GL Accelerator is a GIO64 device at **EXP0 slot (0x1f400000)** that accepts
OpenGL 1.x commands via MMIO writes and renders to an off-screen framebuffer
using the host GPU.

The complete register map is documented in `gathered_documentation/GL_ACCELERATOR.md`.
Key register groups:

| Range | Purpose |
|-------|---------|
| 0x0000-0x00ff | Control (ID, version, caps, enable flags) |
| 0x0200-0x02ff | Matrix operations (mode, push/pop, load, multiply) |
| 0x0300-0x03ff | Vertex operations (X/Y/Z/W, emit) |
| 0x0400-0x04ff | Color operations (R/G/B/A) |
| 0x0500-0x05ff | Normal operations (X/Y/Z) |
| 0x0600-0x06ff | Primitive operations (begin/end) |
| 0x0700-0x0eff | Lighting (8 lights, ambient/diffuse/specular/position) |
| 0x0f00-0x0fff | Viewport and depth control |
| 0x1000-0x10ff | Framebuffer and Z-buffer control |
| 0x1f00-0x1fff | Debug and statistics registers |

Detection: read `0x1f400000` -- returns `0x474C4143` ("GLAC") if present.

### Host-Side Rendering Pipeline

```
IRIX process                    QEMU device                Host GPU
---------------                 -----------                ---------
glBegin(GL_TRIANGLES)  ---->   REG_PRIM_BEGIN=4  ---->   glBegin(GL_TRIANGLES)
glColor3f(1,0,0)       ---->   REG_COLOR_R/G/B   ---->   glColor3f(1,0,0)
glVertex3f(0,1,0)      ---->   REG_VERTEX + EMIT ---->   glVertex3f(0,1,0)
glEnd()                ---->   REG_PRIM_END      ---->   glEnd()
```

The QEMU device maintains an OpenGL context on the host:
- `dpy_gl_ctx_create()` -- create host GL context
- `dpy_gl_scanout_texture()` -- present rendered texture to display
- `dpy_gl_update()` -- flip framebuffer

Rendered output is composited with Newport's 2D framebuffer for the final
display.

### libGLcore.so Binary Patching

The existing `analysis_tools/patch_libglcore.py` tool handles this. It:

1. Analyzes IRIX's `libGLcore.so` ELF to find GL entry points
2. Generates MIPS trampolines that redirect calls to MMIO writes:

```asm
# glVertex3f trampoline (from patch_libglcore.py):
lui   $t0, 0x1f40         # GL accelerator base (kseg1: 0xBF400000)
swc1  $f12, 0x0300($t0)   # Write X (first float arg)
swc1  $f14, 0x0304($t0)   # Write Y (second float arg)
swc1  $f16, 0x0308($t0)   # Write Z (third float arg)
lui   $t1, 0x3f80          # 1.0f = 0x3f800000
sw    $t1, 0x030c($t0)    # Write W = 1.0
sw    $zero, 0x0310($t0)  # Emit vertex
jr    $ra
nop
```

3. Patches function entry points in-place in the `.so`

Currently supports: `glVertex3f`, `glColor3f`, `glBegin`, `glEnd`.
Extensible to ~200 GL 1.x functions.

### QEMU Implementation: `qemu/hw/display/sgi_glaccel.c` (~1200 lines)

```c
#define TYPE_SGI_GLACCEL "sgi-glaccel"

struct SGIGLAccelState {
    SysBusDevice parent_obj;
    MemoryRegion mmio;

    /* GL state machine */
    uint32_t enable_flags;
    int matrix_mode;            /* 0=modelview, 1=projection, 2=texture */
    float modelview[32][16];    /* 32-level stack */
    float projection[2][16];    /* 2-level stack */
    int mv_depth, proj_depth;

    /* Current vertex attributes */
    float cur_color[4];
    float cur_normal[3];
    float cur_vertex[4];

    /* Primitive assembly */
    int prim_type;              /* GL_TRIANGLES, etc. */
    bool in_primitive;
    /* ... vertex buffer for primitive assembly ... */

    /* Framebuffer */
    uint32_t fb_base, fb_width, fb_height, fb_stride;
    uint32_t zb_base;

    /* Host GL context (for Phase 7) */
    QemuGLContext gl_ctx;

    /* Statistics */
    uint32_t stat_vertices, stat_triangles, stat_pixels;
};
```

**Phase 6 (Software):** Implement the GL state machine in C -- matrix math,
vertex transformation, triangle rasterization to an off-screen buffer. No host
GPU dependency. Output to a QEMU `DisplaySurface` composited with Newport.

**Phase 7 (Host GPU):** Replace software rasterization with host OpenGL calls.
Requires QEMU built with `-display gtk,gl=on` or `-display sdl,gl=on`. Falls
back to software (Mesa llvmpipe) if no GPU available.

### Compositing GL + Newport

Simplest approach for initial implementation:
- GL accelerator renders to a separate RGBA buffer
- Newport renders to its VRAM as usual
- The display update callback composites GL output on top of Newport, using
  GL window coordinates (set by `glViewport` in the guest) to position the
  overlay
- Future refinement: per-pixel alpha compositing using GL depth/stencil

### Limitations

- **OpenGL 1.x only** -- IRIX 6.5 uses OpenGL 1.2. Modern host GPUs support
  the full fixed-function pipeline via compatibility profiles.
- **Display lists** -- IRIX apps heavily use display lists. These could be
  captured as sequences of MMIO writes or handled with a bulk command buffer.
- **Texture support** -- Requires a shared-memory path for bulk texture upload
  (too much data for individual MMIO writes).
- **Requires QEMU GL display** for Phase 7 (won't work with VNC-only).

---

## Arbitrary Display Resolution

### Goal

Pixel-for-pixel native rendering at any resolution, including 5K ultra-wide
(5120x2160, 5120x1440). Not scaling or stretching a 1280x1024 framebuffer —
IRIX renders directly into a larger VRAM at the configured dimensions.

### Current Status (Newport in QEMU)

The Newport drawing engine is fully parameterized via `screen_w`, `screen_h`,
`vram_w`, `vram_h` state fields (previously hardcoded constants). VRAM is
dynamically allocated. Usage:

```
-global sgi-newport.width=1920 -global sgi-newport.height=1080
```

What works today:
- REX3 drawing engine operates at any configured resolution
- VRAM bounds checks, pixel addressing, display update all use runtime dims
- VC2_SCANLINE_LEN reads are intercepted to report configured width
- DID frame table is clamped for scanlines beyond the PROM-written 1024
- PROM boots and renders correctly (within its own 1280x1024 area)

What remains unproven:
- Whether the IRIX kernel reads resolution from VC2 hardware or from the
  PROM's `gfx_info.xpmax/ypmax` in guest RAM
- Whether Xsgi has internal resolution limits

### IP54 PROM Advantage

The real IP24 PROM hardcodes `1280+63` in `rex3Clear()` for all four plane
clears (CID, PUP, OLAY, RGB). Since we control the IP54 PROM source, we
eliminate all three resolution bottlenecks:

1. **`rex3Clear()` uses actual dimensions.** Read `VC2_SCANLINE_LEN` back
   from hardware and clear to `(width + 63, height)` instead of hardcoded
   `(1343, 1024)`. The full framebuffer is properly cleared at any resolution.

2. **`gfx_info.xpmax` / `ypmax` set from hardware.** The PROM's `ng1_init`
   stores screen dimensions in the ARCS component tree. IP54 reads the
   configured width from VC2_SCANLINE_LEN and populates `gfx_info` accordingly.
   The kernel receives the correct resolution without guest RAM patching.

3. **DID frame table covers all scanlines.** The PROM writes one DID entry
   per scanline. IP54 writes `screen_h` entries instead of hardcoding 1024,
   so the full display has proper mode/pixel-format coverage.

The CRIME (O2) PROM already demonstrates the right pattern — `crm_init.c`
clears using `gfx_info.xpmax - 1` dynamically. IP54 follows the same
approach for Newport.

### Resolution-Aware Init Sequence

In the IP54 PROM's Newport initialization:

```c
/* Read configured width from VC2 (QEMU intercepts this) */
uint16_t width = vc2GetReg(VC2_SCANLINE_LEN) >> 5;
uint16_t height = /* from timing table or fixed query */;

/* Store in gfx_info for kernel consumption */
info->gfx_info.xpmax = width;
info->gfx_info.ypmax = height;

/* Build DID frame table for full height */
for (int y = 0; y < height; y++) {
    vc2_sram[did_entry_ptr + y] = did_line_ptr;  /* same mode all lines */
}

/* Clear all planes to full configured dimensions */
rex3SetAndGo(rex3, xyendi, ((width + 63) << 16) | height);
```

### Target Resolutions

| Resolution | Aspect | VRAM/plane | Use Case |
|-----------|--------|-----------|----------|
| 1280x1024 | 5:4 | 5.6 MB | Default (original Newport) |
| 1600x1200 | 4:3 | 8.2 MB | Modest upgrade |
| 1920x1080 | 16:9 | 8.9 MB | Standard HD |
| 2560x1440 | 16:9 | 15.8 MB | QHD |
| 3440x1440 | 21:9 | 21.2 MB | Ultra-wide QHD |
| 3840x2160 | 16:9 | 35.5 MB | 4K |
| 5120x1440 | 32:9 | 31.6 MB | Super ultra-wide |
| 5120x2160 | 21:9 | 47.4 MB | 5K ultra-wide |

VRAM per plane = `(width + 64) * (height + 64) * 4` bytes. Two planes
(RGBCI + CIDAUX), so double for total VRAM. All sizes trivial for the host.

### Performance Considerations

REX3 is CPU-emulated. Fill rate scales linearly with pixel count:
- 1280x1024 = 1.3M pixels (baseline)
- 5120x2160 = 11.1M pixels (8.4x baseline)

With `-icount shift=0,sleep=off`, this is host-CPU-bound, not wall-clock
throttled. Large window operations (full-screen clear, window drag) will be
proportionally slower but not blocking. Text rendering and small widget draws
are unaffected.

### GL Accelerator Interaction

When the GL Accelerator is active, its rendered output is composited with
Newport's VRAM. The compositing layer must use the same configured
`screen_w`/`screen_h` for correct alignment. The GL viewport coordinates
map naturally to the larger framebuffer.

---

## Platform Variants

If different graphics architectures prove irreconcilable on a single bus
architecture, the IP54 family can expand into variants that share the same
CPU, SMP, memory, and network subsystems but differ in their backplane and
graphics attachment:

| Variant | Backplane | Graphics | Use Case |
|---------|-----------|----------|----------|
| IP54 | GIO64 (Indy-based) | Newport + GL Accel | Default, proven base |
| IP55 | UMA (O2-based) | CRIME/GBE + GL Accel | O2-specific software |
| IP56 | XIO (Octane-based) | Impact + GL Accel | Octane-specific software |

### Shared Components (All Variants)

- **CPU:** R10000 family (R10K/R12K/R14K/R16K — ISA-identical, see below)
- **SMP Controller:** Same MMIO protocol, same IPI mechanism
- **PV-MEM:** Same high-memory controller
- **PV-NET:** Same 10GbE ring-buffer NIC
- **PROM:** Variant-specific init, shared ARCS framework
- **IRIX Kernel:** `IP54.c`/`IP55.c`/`IP56.c` platform files, shared SMP code

### When Variants Are Needed

Most SGI software uses standard IRIX APIs (GL, X11, STREAMS) that are
bus-agnostic. Variants are only needed when:
- Software directly programs graphics hardware registers (rare)
- Software checks `hinv` or `uname` for a specific platform and refuses
  to run otherwise (sometimes fixable by spoofing the platform string)
- The graphics driver architecture is fundamentally different (CRIME uses
  shared main memory for framebuffer; Impact has dedicated geometry engines)

**Preferred approach:** Start with IP54 (GIO64). Add variants only when
specific software requires them. The GL Accelerator handles the common
case of standard OpenGL applications.

---

## Phased Implementation Plan

### Phase 1: QEMU SMP Controller

**Goal:** Bare-metal test with 2 CPUs communicating via IPI.

**Files:**
- New: `qemu/hw/misc/sgi_smp.c`, `qemu/include/hw/misc/sgi_smp.h`
- Modified: `qemu/hw/mips/sgi_indy.c` (multi-CPU loop, SMP controller wiring)
- Modified: `qemu/hw/misc/Kconfig`, `qemu/hw/misc/meson.build`
- Modified: `qemu/hw/mips/Kconfig` (add SGI_SMP dependency)

**Test:** Write a bare-metal program (PROM-loaded) that boots 2 CPUs, exchanges
IPI, prints "Hello from CPU 0" and "Hello from CPU 1" to serial.

### Phase 2: PROM Multi-CPU Support

**Goal:** PROM `hinv` shows multiple CPUs.

**Files:**
- Modified: `prom-building/src/fw/ip54_stubs.c` (MPCONF, multi-CPU tree,
  secondary release)
- New: `prom-building/src/fw/secondary_boot.S` (secondary entry point)

**Test:** Boot IP54 PROM, run `hinv`, verify output shows N CPUs with caches.

### Phase 3: IRIX Kernel Platform

**Goal:** IRIX boots with SMP kernel, `hinv` shows N CPUs.

**Files:**
- New: `IP54.c`, `IP54.h`, `IP54asm.s` (on IRIX filesystem)
- Modified: kernel build config

**Test:** Boot SMP kernel, `hinv` shows 2+ CPUs, `mpstat` shows utilization.

### Phase 4: SMP Integration

**Goal:** Full IRIX desktop with SMP.

**Test:** Run `make -j2`, both CPUs show load in `mpstat`.

### Phase 5: High Memory (PV-MEM)

**Goal:** Boot IRIX with >256MB RAM.

**Files:**
- New: `qemu/hw/misc/sgi_pvmem.c`, `qemu/include/hw/misc/sgi_pvmem.h`
- Modified: `qemu/hw/mips/sgi_indy.c` (PV-MEM creation, extended RAM regions)
- Modified: `prom-building/src/fw/ip54_stubs.c` (PV-MEM memory discovery)

**Test:** Boot with `-m 4G`, verify `hinv` shows 4GB RAM, allocate large arrays.

### Phase 6: Paravirtual 10GbE Networking

**Goal:** 10 Gbps NIC alongside Seeq.

**Files:**
- New: `qemu/hw/misc/sgi_pvnet.c`, `qemu/include/hw/misc/sgi_pvnet.h`
- New: `if_pvnet.c` (IRIX loadable driver, built on-guest)
- Modified: `qemu/hw/mips/sgi_indy.c` (PV-NET creation at GIO EXP1)

**Test:** `ifconfig et0` shows 10 Gbps, `ping` works, file transfer at >1 Gbps.

### Phase 7: GL Accelerator (Software Rendering)

**Goal:** Simple GL programs render via MMIO commands.

**Files:**
- New: `qemu/hw/display/sgi_glaccel.c`, `qemu/include/hw/display/sgi_glaccel.h`
- Modified: `qemu/hw/mips/sgi_indy.c` (GL accel at GIO EXP0)
- Modified: `qemu/hw/display/Kconfig`, `qemu/hw/display/meson.build`

**Test:** Patched `libGLcore.so` redirects GL calls, simple colored triangle
renders correctly.

### Phase 8: GL Host GPU Passthrough

**Goal:** GL rendering at native speed using host GPU.

**Files:**
- Modified: `qemu/hw/display/sgi_glaccel.c` (add `dpy_gl_*` integration)
- Modified: `qemu/hw/display/sgi_newport.c` (compositing)

**Test:** `ideas` or equivalent GL demo renders at >30 FPS. Extend
`patch_libglcore.py` for additional GL functions as needed.

### Phase 9: Stress Testing

**Goal:** Push limits of all subsystems.

**Tests:**
- Boot with `-smp 16` and `-m 16G`, run `mpstat` and `top`
- Boot with `-smp 128` (MAXCPU) and verify stability
- Boot with `-m 128G` (R10000 PABITS allows up to 1TB)
- Run `make -j16` on a large codebase
- Transfer large files via PV-NET, measure throughput
- Run multiple GL demos simultaneously

---

## Verification Criteria

| Phase | Criterion |
|-------|-----------|
| 1 | Bare-metal program boots 2 CPUs, exchanges IPI, prints from both |
| 2 | IP54 PROM `hinv` shows "CPU 0: MIPS R10000 ... CPU 1: MIPS R10000 ..." |
| 3 | IRIX `hinv` shows 2+ CPUs, `mpstat` shows both active |
| 4 | Run `make -j2` on IRIX, both CPUs show load |
| 5 | Boot with `-m 4G`, `hinv` shows 4GB, large allocations succeed |
| 6 | `ifconfig et0` shows 10000 Mbps, `ping` works, `ftp` >1 Gbps |
| 7 | GL triangle renders correctly via patched libGLcore -> MMIO -> software |
| 8 | GL demo renders at >30 FPS using host GPU |
| 9 | `-smp 16 -m 16G` stable; `-smp 128` boots; `-m 128G` boots |

---

## Key Source References

### SMP

| File | Purpose |
|------|---------|
| `qemu/hw/mips/sgi_indy.c` | Current machine definition (modify for multi-CPU) |
| `qemu/hw/mips/malta.c:1023-1044` | Multi-CPU creation loop without CPS |
| `qemu/hw/mips/mips_int.c` | Per-CPU IRQ allocation |
| `qemu/hw/intc/loongson_ipi.c` | IPI device with per-CPU GPIO outputs |
| `qemu/hw/mips/loongson3_virt.c:203-249` | Multi-CPU + IPI boot ROM |
| `qemu/target/mips/cpu.c:602` | `mttcg_supported = TARGET_LONG_BITS == 32` |
| `irix/kern/ml/RACER/IP30.c:571-711` | IP30 `allowboot()`, `cboot()` |
| `irix/kern/ml/RACER/slave.s` | IP30 secondary CPU assembly bootstrap |
| `irix/kern/sys/RACER/racermp.h` | `mpconf_t` structure (128 bytes, magic 0xBADDEED2) |
| `irix/kern/ml/IP22.c` | IP22 platform init (reuse for hardware setup) |
| `prom-building/src/fw/ip54_stubs.c` | Current IP54 PROM (modify for multi-CPU) |

### CPU and Memory

| File | Purpose |
|------|---------|
| `qemu/target/mips/cpu-defs.c.inc:617-649` | R10000 CPU definition (PRId, PABITS=40, SEGBITS=44) |
| `qemu/target/mips/cpu.c:602` | MTTCG disabled for MIPS64 (`TARGET_LONG_BITS == 32`) |
| `irix/kern/sys/EVEREST/everest.h:43-46` | Everest MAXCPU=128, CPU_PER_BOARD |
| `irix/kern/sys/SN/SN0/arch.h:29-34` | Origin 2000 MAXCPU=128/256, 2 CPUs/node |
| `irix/kern/sys/RACER/IP30.h:179` | IP30 MAXCPU=2 |
| `irix/kern/sys/sbd.h:219-223` | R10000 TLB PFN mask (28-bit, 40-bit phys) |
| `irix/kern/sys/immu.h:1674` | "64 bit kernels support up to 16 GB of memory" |
| `irix/kern/sys/mc.h:453-466` | MC memory segment limits (256MB low + 512MB high) |

### Networking

| File | Purpose |
|------|---------|
| `qemu/hw/misc/sgi_hpc3.c` | Current Seeq ethernet (NICState/NICConf pattern) |
| `irix/kern/bsd/mips/if_ec2.c` | IRIX Seeq driver (`etherifops` pattern) |
| `irix/kern/bsd/misc/ether.h` | `struct etherif`, `struct etherifops` definitions |
| `irix/kern/bsd/net/if.h` | `struct ifnet`, bandwidth macros |

### Graphics

| File | Purpose |
|------|---------|
| `gathered_documentation/GL_ACCELERATOR.md` | Complete register map spec |
| `analysis_tools/patch_libglcore.py` | libGLcore.so binary patcher (MIPS trampolines) |
| `qemu/hw/display/sgi_newport.c` | Newport with configurable `width`/`height` properties |
| `qemu/include/hw/display/sgi_newport.h` | `screen_w/h`, `vram_w/h` state fields |
| `qemu/hw/display/virtio-gpu-gl.c` | QEMU GL passthrough example (virglrenderer) |
| `qemu/include/ui/console.h` | QEMU `dpy_gl_*` API |

### Resolution

| File | Purpose |
|------|---------|
| `irix-657m-source/stand/arcs/lib/libsk/graphics/NEWPORT/ng1_init.c` | IRIX `rex3Clear()` — hardcodes 1280+63 (line 1017) |
| `prom-building/src/libsk/graphics/crm_init.c` | O2 `initFramebuffer()` — uses `xpmax` dynamically (line 576) |
| `progress_notes/indy/newport_configurable_resolution.md` | Implementation notes for Newport configurable resolution |
