# Community MIPS/IRIX/SGI Projects and Resources

A reference guide to external projects, tools, documentation, and communities
relevant to SGI Indy emulation in QEMU. Maintained to avoid reinventing wheels
and to know where to find reference material when stuck.

Last updated: 2026-02-10

---

## 1. SGI Full-System Emulators

### MAME SGI (Primary Reference)

- **URL:** https://github.com/mamedev/mame (`src/mame/sgi/`)
- **Status:** Active, working. Boots IRIX 5.3 and 6.5 with graphics on Indy and Indigo2.
- **What it does:** Full-system emulation of SGI Indy (IP24), Indigo2 (IP22), and
  Personal IRIS. Implements MC, HPC3, Newport (REX3/VC2/XMAP9), WD33C93 SCSI,
  HAL2 audio, Seeq ethernet, Z85C30 serial, DS1386 RTC/NVRAM, INT2/INT3, and
  GIO64 bus.
- **Key contributors:** Arbee, Mooglyguy (Ryan Holtz), FlyGoat (Jiaxun Yang).
- **What we learn:** MAME is the only working full-system SGI emulator and our
  primary hardware behavior reference. We already mirror its source tree in
  `mame/source/src/mame/sgi/`. See "Key Technical Lessons" below for specific
  findings from FlyGoat's PRs.
- **Setup guide:** https://sgi.neocities.org/ (MAME + IRIX 6.5.22 walkthrough)

### SimOS (Historical)

- **URL:** https://en.wikipedia.org/wiki/SimOS
- **Papers:** [TOMACS96](https://pages.cs.wisc.edu/~stjones/proj/vm_reading/TOMACS96-simos.pdf),
  [Stanford CSL-TR-94-631](http://i.stanford.edu/pub/cstr/reports/csl/tr/94/631/CSL-TR-94-631.pdf)
- **Status:** Historical, no longer usable. Stanford, ~1994-2000.
- **What it does:** Full-system MIPS R3000/R4000 simulator. Ran IRIX 5.3 on
  simulated SGI hardware. Used Embra for dynamic binary translation achieving
  3-9x slowdown vs native (fastest reported at the time).
- **What we learn:** Binary translation was key to making full-system MIPS
  simulation fast enough to be useful. SimOS required a custom IRIX kernel.
  Mendel Rosenblum co-founded VMware based on this work, adapting the binary
  translation technique from MIPS to x86. Architecturally interesting but
  not directly reusable.

### Our QEMU Project (This Repository)

- **Status:** In progress. The only QEMU-based full-system SGI emulation effort.
- **What works:** PROM POST, System Maintenance Menu, SCSI boot, miniroot kernel
  boot through init. See CLAUDE.md for current status.

---

## 2. MIPS/IRIX Userland Emulation

### qemu-irix

- **URL:** https://github.com/n64decomp/qemu-irix
- **Upstream:** https://github.com/irixxxx/qemu-irix (original by irixxxx)
- **Status:** Maintained. QEMU 2.11 fork, Linux-only.
- **What it does:** IRIX and Solaris userland emulation in QEMU (not full-system).
  Supports `irix-linux-user`, `irixn32-linux-user`, `irix64-linux-user` targets.
  Born from the N64 decompilation community needing SGI's IDO compilers to run
  on modern Linux.
- **Key features:**
  - IRIX PRDA (thread-local storage at 0x20000) emulation via `QEMU_IRIXPRDA`
    environment variable. Checks every memory access, causing significant
    performance impact.
  - Extended `QEMU_LD_PREFIX` with colon-separated multiple paths.
  - binfmt integration via helper scripts.
- **What we learn:** PRDA at 0x20000 is a critical IRIX threading mechanism.
  The performance cost of checking every memory access for PRDA is a useful
  data point. This fork is already cloned in our `qemu-irix/` directory as
  a potential reference for IRIX syscall behavior.

---

## 3. PROM & Firmware Analysis Tools

### ip32prom-decompiler

- **URL:** https://github.com/mattst88/ip32prom-decompiler
- **Status:** Complete. Already cloned in our `ip32prom-decompiler/` directory.
- **What it does:** Disassembles SGI O2 (IP32) PROM to assembly with bit-identical
  reassembly. Written in Rust using Capstone for MIPS disassembly.
- **What we learn:** Demonstrates that SGI PROMs can be fully disassembled and
  reassembled. The O2 PROM is structurally similar to IP24 PROMs. Good reference
  for understanding PROM structure.

### Our MCP Server (sgi)

- **Location:** `sgi_mcp/` in this repository
- **What it does:** Full SGI emulation toolkit: QEMU build/run/debug automation,
  IRIX installation harness, PROM analysis (disassembly, function detection,
  call graphs, boot sequence tracing, hardware access tracking), Ghidra
  integration for decompilation, filesystem tools, and IRIX kernel inspection.
  See CLAUDE.md for full tool list.

---

## 4. MIPS Decompilation Tools

### m2c (Machine code to C)

- **URL:** https://github.com/matt-kempster/m2c
- **Status:** Active, 479+ stars. Supports MIPS (IDO, GCC) and PowerPC (MWCC).
- **What it does:** MIPS/PowerPC assembly to C decompiler focused on "matching"
  decompilation -- producing C source that compiles to byte-identical binary
  output with a specific compiler. Supports IDO 5.3 target (`--target mips-ido-c`).
- **Ecosystem:** Used with splat (binary splitter), asm-differ (output comparison),
  and decomp-permuter (automated matching search). Core tool in N64 decompilation
  projects (Ocarina of Time, Majora's Mask, Paper Mario).
- **What we learn:** IDO 5.3 compiler output has specific patterns that a
  specialized decompiler can target. Since IRIX kernel and userland were compiled
  with IDO, m2c's pattern knowledge could theoretically help analyze IRIX binaries.
  Our Ghidra integration serves a similar purpose for PROM analysis.

### N64Recomp

- **URL:** https://github.com/N64Recomp/N64Recomp
- **Status:** Active, widely used for N64 game ports.
- **What it does:** Static recompiler: takes MIPS N64 binaries and emits C code
  that can be compiled natively. Each MIPS function becomes a C function. The
  translation is intentionally literal to keep complexity low.
- **Inspiration:** The IDO static recompilation project, which recompiles SGI's
  IDO compiler itself on modern systems. N64Recomp applies the same technique
  to arbitrary N64 binaries.
- **What we learn:** Demonstrates that MIPS-to-C static recompilation is
  practical for real-world binaries. The literal translation approach (one C
  function per MIPS function) is simpler than trying to recover high-level
  constructs. Tested primarily with old MIPS compilers (gcc 2.7.2, IDO).

### epanos

- **URL:** https://github.com/drvink/epanos
- **Status:** Proof of concept, 2013-2014. 72 stars.
- **What it does:** "Very dumb MIPS to C static translator" using IDA Pro.
  Successfully decompiled SGI's ElectroPaint screensaver from IRIX MIPS binary
  to Windows native code (result: [electroportis](https://github.com/drvink/electroportis)).
- **What we learn:**
  - IRIX `malloc` may zero memory -- crashes occurred when a replacement `malloc`
    didn't zero. This is a potential gotcha for any IRIX binary analysis.
  - Even a "very dumb" literal translator can produce working code from IRIX
    binaries, suggesting the MIPS/IDO calling conventions are regular enough for
    mechanical translation.
  - Some functions (like "reshape") required manual rewrite from disassembly.

### Ghidra / IDA Pro

- **Ghidra:** https://ghidra-sre.org/ (installed at `/home/dev/ghidra/`)
- **IDA Pro:** Commercial, widely used for SGI binary analysis in the community.
- **Our integration:** The MCP server provides `ghidra_analyze`, `ghidra_decompile`,
  `ghidra_functions`, `ghidra_xrefs` for automated PROM decompilation. Projects
  cached in `/workspace/ghidra_projects/`.

---

## 5. Hardware Documentation Sources

### Already In Our Repository

We have the following SGI ASIC specification PDFs in `gathered_documentation/IndyDocs/`:

| Document | File | Content |
|----------|------|---------|
| REX3 | `rex3.pdf` | Raster Engine spec (contains octant encoding error -- see Lessons) |
| VC2 | `vc2.pdf` | Video Controller spec |
| RB2 | `rb2.pdf` | Rendering Backend spec |
| RO1 | `ro1.pdf` | Raster Operations spec |
| XMAP9 | `xmap9.pdf` | Colormap spec |
| MC | `mc.pdf` | Memory Controller spec |
| HPC3 | `hpc3.pdf` | High-Performance I/O Controller spec |
| IOC | `ioc.pdf` | I/O Controller (INT2/INT3/IOC2) spec |
| GIO64 | `gio64.pdf` | GIO64 bus spec |
| VDMA | `vdma.pdf` | Virtual DMA spec |
| VINO | `vino/` | Video timing diagrams (6 files) |

**Source:** Originally from ftp://ftp.linux-mips.org/pub/linux/mips/doc/ (SGI internal
engineering documents released for Linux porting). Also mirrored at bukosek.si.

### External Documentation Sources

**Bukosek Hardware Collection** (https://bukosek.si/hardware/collection/sgi-indy.html)
- Hosts the same Newport ASIC PDFs we already have (REX3, VC2, RB2, RO1, XMAP9).
- Also has specs for other SGI systems we don't need yet:
  - **O2:** CRIME, MACE, GBE, VICE ASIC specs
  - **Octane:** HEART ASIC, technical report
  - **Origin:** Hardware quick-reference booklet

**linux-mips.org** (ftp://ftp.linux-mips.org/pub/linux/mips/doc/)
- Original source of our ASIC PDFs (MC.ps, REX3.pdf, VC2.pdf, etc.)
- Also hosts IOC2 chip spec, HPC3 spec, GIO64 spec.
- Some content also available at sigxcpu.org.

**ARC Specification v1.2** (https://www.netbsd.org/docs/Hardware/Machines/ARC/riscspec.pdf)
- The firmware specification our ARCS implementation is based on.
- Defines the callback table, system parameter block, memory descriptors,
  and boot protocol that IRIX expects from the PROM.

**OSDev Wiki** (https://wiki.osdev.org/)
- SGI MIPS system initialization page with boot sequence details.
- General MIPS architecture reference.

---

## 6. Community Hubs

| Community | URL | Description |
|-----------|-----|-------------|
| IRIX Network Forums | https://forums.irixnet.org/ | Primary IRIX community. Hosts IRIX-32 project discussion. |
| IRIX Network Wiki | https://wiki.irixnet.org/ | Technical wiki with MAME setup, GCC on IRIX, MIPSPro info. |
| Silicon Graphics User Group | https://forums.sgi.sh/ | SGI enthusiast & developer forum. 1,800+ members. |
| Higher Intellect Wiki | https://wiki.preterhuman.net/SGI | Vintage computing wiki with SGI company history, product catalog. |
| sgi.neocities.org | https://sgi.neocities.org/ | Step-by-step MAME + IRIX 6.5.22 setup guide. |
| irix7.com | https://irix7.com/ | SGI TechPubs archive (original SGI documentation). |

---

## 7. IRIX Kernel Reverse Engineering

### IRIX-32 Kernel Project

- **URL:** https://forums.irixnet.org/thread-3941.html
- **Coverage:** [OSnews](https://www.osnews.com/story/136178/irix-community-proposes-to-reverse-engineer-the-last-32-bit-irix-kernel/),
  [Tedium](https://tedium.co/2023/05/27/sgi-irix-revival-efforts/),
  [Hackaday](https://hackaday.com/2023/05/31/can-hobbyists-bring-sgis-irix-os-back-to-life/),
  [The Register](https://www.theregister.com/2023/05/31/bugs_in_ex_sgi_xfs/)
- **Status:** Early stages. Announced mid-2023 with $6,500 crowdfunding goal.
- **What it does:** Community proposal to reverse-engineer the IRIX 5.3 kernel
  (last 32-bit version, ~1/3 the complexity of IRIX 6.5.30). Goal is to produce
  a technical manual, eventually an open-source derivative kernel. Using Ghidra
  for disassembly. Clean-room approach intended.
- **Key figure:** Kazuo Kuroi (maintains IRIX Network).
- **Legal concerns:** HP Enterprise owns SGI's IP. The project argues patents have
  expired, but The Register noted the argument "sounds less than totally
  watertight."
- **What we learn:** We have IRIX 6.5.7m source in our repository
  (`software_library/irix-657m-source/`), which gives us more direct access to
  kernel internals than the IRIX-32 project has. Their work on IRIX 5.3 kernel
  structure could still be useful if they produce documentation.

---

## 8. Key Technical Lessons

Actionable findings from these projects that are directly relevant to our
QEMU SGI emulation work.

### From MAME (FlyGoat's PRs)

**PR [#11117](https://github.com/mamedev/mame/pull/11117) -- SGI Indy fixes:**

1. **REX3 octant encoding differs from spec pseudocode.** The actual hardware
   encoding is `[XMAJOR][XDEC][YDEC]`, documented in "6. Revision History" of
   the REX3 manual, not in the main specification text. IRIX calculates octant
   in software to save the 1-cycle hardware setup cost.

   **Our status:** Our `sgi_newport.c` already uses the correct hardware encoding:
   ```c
   uint8_t octant = (s->bres_octant_inc1 >> 24) & 7;
   int16_t dx = (octant & 2) ? -1 : 1;  // bit 1 = XDEC
   int16_t dy = (octant & 1) ? -1 : 1;  // bit 0 = YDEC
   ```
   This matches the `XMAJOR & XDEC & YDEC` encoding. No changes needed.

2. **Iterator setup is a dedicated operation.** REX3 only calculates quadrant/octant
   when DoSetup is set in DRAWMODE0 or on host write to SETUP register. Linux's
   `newport_con` driver relies on reusing the quadrant from a previous draw call.

   **Our status:** We handle `REX3_SETUP` writes. Worth verifying DoSetup gating
   if we pursue Linux boot.

3. **LL instruction must sign-extend.** COP0 LL (Load Linked) must sign-extend
   32-bit loads to 64 bits in MIPS III mode. Corrupts pointer values otherwise.

   **Our status:** This is in upstream QEMU's MIPS CPU, should be correct.

**PR [#11128](https://github.com/mamedev/mame/pull/11128) -- PRID and clock fixes:**

4. **R4600 rev 2.0 PRID.** Almost all shipped systems have R4600 rev 2.0 (major
   rev field bumped to 2 so software can detect revised silicon). MAME sets
   PRID accordingly.

   **Our status:** Worth checking our PRID value.

5. **GIO clock frequency sources the RPSS counter tick.** The system clock
   (50MHz for most IP22/IP24, 66MHz for `indy_4613`) drives the GIO bus and
   RPSS counter.

6. **Different boards use different system clock divisors** to reach different
   CPU frequencies from the system clock.

**PR [#10546](https://github.com/mamedev/mame/pull/10546) -- Iterator setup split:**

7. **Block & span reuse the same octant encoding as lines.** Also, Scr2Scr
   (screen-to-screen copy) needs to handle dx/dy direction and DoSetup.

### From qemu-irix

8. **PRDA (Process Data Area) at 0x20000** is critical for IRIX multithreading.
   Every thread gets a private mapping at this address. Emulating it requires
   checking every memory access, which is expensive. This matters if we ever
   need to understand IRIX's threading behavior during kernel boot.

### From epanos/electroportis

9. **IRIX malloc may zero memory.** The default IRIX allocator appears to zero
   returned memory (or at least frequently returns zeroed memory). Code compiled
   for IRIX may depend on this behavior implicitly. A replacement allocator
   that doesn't zero can cause crashes.

### From m2c / N64 Decomp Ecosystem

10. **IDO compiler output has recognizable patterns.** The IDO 5.3/7.1 compilers
    produce MIPS code with specific register allocation, stack frame layout, and
    instruction scheduling patterns. This is useful when reading disassembled
    IRIX kernel or PROM code -- knowing the compiler's habits helps identify
    function boundaries, local variables, and control flow.

### From SimOS

11. **Binary translation was the key performance innovation** for full-system
    MIPS simulation. SimOS/Embra achieved 3-9x native speed using dynamic
    binary translation. QEMU already uses this technique (TCG), so we benefit
    from the same approach without needing to implement it ourselves.

### Debug Infrastructure Adopted from MAME

12. **MAME's per-subsystem log categories** (`logmacro.h`) provide filterable debug
    output: `LOG_SCSI_DMA`, `LOG_REX3`, `LOG_INT3`, `LOG_MEMCFG`, etc. across HPC3,
    MC, IOC2, Newport, and HAL2. We adopted the same granularity via QEMU's trace
    event system (`trace-events` files), which provides runtime `--trace pattern`
    filtering with zero overhead when disabled. Events are named `sgi_mc_*`,
    `sgi_hpc3_*`, and `sgi_newport_*`.

13. **MAME's NewView binary logger** in `newport.cpp` records every REX3 register
    access (read/write/frame-boundary) to a file for replay and analysis. We
    implemented an equivalent via the `sgi-newport.newview-log` QEMU property,
    using the same 20-byte record format (offset, data_hi, data_lo, mask_hi, mask_lo).

---

## 9. Reference Codebases

### NetBSD sgimips

- **Key files:**
  - `arcemu.c` -- ARCS firmware emulation layer
  - `arcbios_calls.S` -- ARCS BIOS call stubs
  - `arcbios.h` -- Machine-independent ARCS header
- **What we learn:** Clean ARCS interaction code. A NetBSD developer noted:
  "I had to stumble through enough that I'd really not like to see others
  have to do the same." Their ARCS implementation documents hard-won
  knowledge about firmware callback behavior.
- **Our copy:** `netbsd_source/` in this repository.

### Linux sgimips (linux-mips)

- **What we learn:** Hardware initialization sequences for IP22/IP24/IP28.
  Includes MC, HPC3, INT3, Newport, WD33C93 drivers. The Linux `newport_con`
  driver's reliance on REX3 DoSetup behavior was the trigger for FlyGoat's
  MAME fix (PR #10546).

### MAME SGI

- **Our copy:** `mame/source/src/mame/sgi/` in this repository.
- **Key files:** `indy.cpp` (machine), `mc.cpp`, `hpc3.cpp`, `newport.cpp`,
  `hal2.cpp`, `ioc2.cpp`, `wd33c9x.cpp`.
- **What we learn:** Complete device emulation reference. The most battle-tested
  implementation of SGI hardware behavior.

### IRIX Source

- **Our copy:** `software_library/irix-657m-source/` in this repository.
- **What we learn:** Kernel source for IRIX 6.5.7m including ARCS firmware
  interface, device drivers, interrupt handling, memory management. This is
  our most authoritative reference for how IRIX expects hardware to behave.
