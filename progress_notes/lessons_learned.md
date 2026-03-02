# Lessons Learned

Consolidated from all phases of the SGI QEMU emulation project. Each entry
describes the problem, why it was hard to diagnose, and the fix or pattern.

---

## 1. Hardware Gotchas

### Register Address Alignment (BE vs LE offsets)

SGI uses a 64-bit bus with 32-bit registers. The PROM accesses registers at
both big-endian (+4) and little-endian (+0) byte offsets within each 64-bit
word. A register at offset 0x00 might be accessed at 0x00 or 0x04 depending
on which PROM routine is running.

**Symptom:** Registers silently returned 0 because the address didn't match
any switch case. The PROM continued but with wrong state.

**Fix:** Normalize all register addresses with `addr &= ~7ULL` to strip the
byte-lane offset. Use 64-bit aligned offsets in `#define`s:

```c
#define MC_CPU_CTRL0    0x0000   /* Not 0x0004 */
#define MC_MEMCFG0      0x00c0   /* Not 0x00c4 */
addr &= ~7ULL;  /* In read/write handlers */
```

**Exception:** Newport REX3 uses `addr &= ~3ULL` (4-byte alignment) because
it has distinct registers at +0 and +4 within each 64-bit word.

### MAME Input Line Indexing (0-indexed from IP2)

MAME's `set_input_line(N, ...)` uses 0-indexed lines where **0 = IP2** (the
first external interrupt). This makes "line 2" = IP4, not IP2. Our initial
implementation mapped PIT timers to IP2/IP3 instead of IP4/IP5.

**Symptom:** The miniroot kernel hung in its idle loop. Timer interrupts were
delivered to `lcl0_intr` (INT3 Local0 handler) instead of `clock()`. The
interrupt was silently dropped because LOCAL0 status didn't have a timer bit.

**Fix:** Cross-referenced three sources:
1. MAME `ioc2.cpp:210-226` (line numbers, accounting for offset)
2. IRIX kernel `IP22.c c0vec_tbl[]` (SR_IBIT5 = IP4 → `clock()`)
3. MAME `ioc2.cpp:623-635` (`set_timer_int_clear` uses lines 2/3 = IP4/IP5)

**Lesson:** Never trust a single reference for interrupt routing. Always
cross-reference MAME, IRIX kernel source, and hardware docs.

### MEMCFG Register Format

Each 16-bit MEMCFG bank config encodes:
- Bits 0-7: Base address (physical >> 22, i.e., 4MB units)
- Bits 8-12: Size code ((size_MB/4) - 1)
- Bit 13 (0x2000): Valid
- Bit 14 (0x4000): 2 subbanks

The IRIX kernel source headers (`sys/IP22.h`) are the authoritative reference
for bit layouts.

### MAME Bank Index Mapping

MAME's `mc.cpp` bank indices map to MEMCFG register halves:

| Index | MEMCFG | Half | MAME Equivalent |
|-------|--------|------|-----------------|
| 0 | MEMCFG0 | upper (bits 29/24:16) | m_ram[0] |
| 1 | MEMCFG0 | lower (bits 13/8:0) | m_ram[1] |
| 2 | MEMCFG1 | upper | m_ram[2] |
| 3 | MEMCFG1 | lower | m_ram[3] |

---

## 2. QEMU Framework Gotchas

### GPIO `qemu_set_irq` is Last-Write-Wins

When multiple interrupt sources share a CPU IRQ line, calling
`qemu_set_irq(line, 0)` for one source clears the line even if another
source is still asserting. QEMU GPIO doesn't auto-OR.

**Fix:** Compute the OR of all sources before calling `qemu_set_irq`. Or
give each source its own GPIO output wired to separate CPU lines (as we did
for PIT timers → IP4/IP5).

### CPU Reset Ordering (Resettable Framework)

QEMU's `Resettable` mechanism re-resets the CPU after
`qemu_register_reset()` callbacks run, undoing PC overrides for direct
kernel boot.

**Fix:** Write a MIPS trampoline to the ROM area at the reset vector
(0xBFC00000). The CPU resets naturally and the trampoline jumps to the
kernel entry. This is the "Malta pattern."

### Physical Address Aliasing (kseg0/kseg1)

MIPS kseg0 (0x80000000) and kseg1 (0xA0000000) both map to physical 0.
When the kernel loads at virtual 0x88000000 (physical 0x08000000), data at
low physical addresses may be overwritten by the MC's RAM aliasing.

**Fix:** Keep all pre-kernel data (SPB, FirmwareVector, stubs) below physical
0x2000. Anything at 0x2000+ may be overwritten by kernel loading.

### SCSI Stack Integration (Vintage Controller in Modern Framework)

QEMU's SCSI subsystem expects modern semantics with scatter-gather lists
and asynchronous completion. The WD33C93 is a vintage controller with
single-byte DMA handshaking.

**Fix:** Required custom HPC3 DMA descriptor chaining, WD33C93-specific
status code mapping (device status in TARGET_LUN register, not SCSI_STATUS),
COMMAND_PHASE tracking, and TRANSFER_INFO command path for split SELECT +
TRANSFER operations.

---

## 3. MAME-to-QEMU Translation

### Different Abstraction Levels

MAME uses machine-level callbacks (`set_input_line`, `read()/write()`
handlers, flat memory maps). QEMU uses QOM with GPIO interconnects, named
outputs, memory regions, and device state structs.

**Lesson:** MAME code is an excellent behavioral reference but can't be
directly ported. Translate the _logic_ while adapting to QEMU's
_architecture_.

### IOC2 Folded into HPC3

MAME has a separate `ioc2.cpp` for the interrupt controller. Our
implementation folds IOC2 into HPC3 because they share the same MMIO space
(0x1FB80000). The combined `sgi_hpc3.c` is ~2600 lines and mixes peripheral
control with interrupt routing.

**Trade-off:** Avoids inter-device GPIO complexity but makes the single file
unwieldy and harder to reason about for interrupt bugs.

### Timer Fidelity

MAME's 8254 PIT is a full device model with all counter modes. Our
implementation is minimal: count, latch, reload, callback. Only modes 2
(rate generator) and 3 (square wave) are exercised by PROM and kernel.

**Lesson:** Start with a minimal timer implementation. Add complexity only
when a specific code path exercises an unimplemented mode.

---

## 4. Debugging Techniques

### Silent Failures from Stub Registers

Stubs that return 0 for unimplemented registers often "work" initially but
produce subtly wrong state that manifests much later.

**Example:** O2 GBE graphics stub returns 0 for all reads → kernel computes
`nscreens = 1531` (should be 0 or 1) → massive `kmem_alloc` returns
unmapped address → fault far from the actual bug.
(**Status: HYPOTHESIS** — nscreens=1531 may come from GBE stub returning 0)

**Lesson:** Check MAME for default/reset values. Add `LOG_UNIMP` traces for
every unimplemented register access.

### Stripped Kernel Symbols

The IRIX kernel ELF has symbols stripped. All debugging requires manual
disassembly, GP-relative offset tracking, and cross-referencing with IRIX
source fragments from `software_library/irix-657m-source/`.

### Interrupt-Level Bugs Are Hard to Reproduce

The timer IRQ routing bug only manifested when the kernel entered its idle
loop. During POST and initialization, the PROM and kernel poll timers
directly. The system booted through many stages before suddenly hanging.

**Lesson:** Test interrupt delivery independently of polling. Use QEMU's
`-d int` flag to trace interrupts.

### Debug Logging Volume

`qemu_log_mask(LOG_UNIMP)` has zero overhead when `-d unimp` is not passed.
File-based `fopen/fprintf` tracing has enormous overhead — 80,000+ timer
fire log entries added minutes to boot time. Always use `qemu_log_mask()`
and remove file-based tracing before committing.

### Serial Interaction Buffering

The `qemu_serial_interact` MCP tool's expect phase must match against _all_
received data including boot_wait data, not just new data after boot_wait
completes. Otherwise patterns visible in the transcript time out.

---

## 5. Architecture Decisions

### Two Boot Paths (PROM vs Direct Kernel)

Direct kernel boot (`-kernel`) and PROM boot are completely different code
paths. The ARCS hypercall device is only used by `-kernel` mode. PROM boot
is the primary path for IRIX installation. Bugs can exist in one path but
not the other.

### Multiple IRIX Versions

IRIX 6.2.1 (used with `-kernel`) and IRIX 6.5.22 (used with CD-ROM boot)
have different SCSI drivers, interrupt handling, and init sequences. Be
aware of which version you're testing against.

### PROM Version Differences

The two IP24 PROMs (070-9101-007 and 070-9101-011) have slightly different
behavior. -011 probes at SEG1 (0x20000000); -007 probes at SEG0 (0x08000000)
and exercises the per-bank MEMCFG path more thoroughly.

---

## 6. Patterns That Work

1. **Start with MAME, verify with IRIX source:** Use MAME as the behavioral
   reference, then validate register semantics against IRIX kernel headers.

2. **`addr &= ~7ULL` normalization:** Handles BE/LE register access
   transparently. Apply in every read/write handler.

3. **Unimplemented device regions for memory probing:** Create regions that
   return 0 on reads (not bus errors) so the PROM's write-read-compare
   pattern correctly detects "no memory here."

4. **Malta trampoline pattern:** For `-kernel` boot, write MIPS code to the
   PROM ROM area that jumps to the kernel entry. Don't try to override PC.

5. **DMA descriptor chaining with EOX drain:** After the main transfer loop,
   always drain remaining zero-count terminal descriptors to clear DMA state.

6. **Persistent NVRAM:** File-backed NVRAM with auto-checksum enables testing
   PROM behavior with different configurations without rebuilding.

7. **Per-bank dynamic RAM mapping (MAME-style):** Four independent banks
   with full remap on every MEMCFG write. Priority-based overlay lets probe
   regions show through when banks are unmapped.
