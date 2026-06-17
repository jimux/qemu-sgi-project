# HEART shim / RAM overlap — investigated, EXONERATED

Date: 2026-06-12. The long-standing "post-X fragility" / desktop
instability (task 15).

## VERDICT (2026-06-12): NOT the cause.

A 120 MB boot (RAM ends at 0x0F800000, entirely below the HEART shim
at 0x0FF00000 → zero overlap possible) STILL panicked — `bcopy+0xb0`
Data Bus Error on a corrupted pointer (0xc04c8000). So the RAM/HEART
overlap is not the corruption source. Also, the PROM descriptor hole
below had NO measurable effect: "Available memory" was unchanged
(254148 KB) with and without it — the IRIX kernel builds its free list
by probing the memory controller, not from the ARCS FirmwarePermanent
descriptors, so the reservation is a harmless no-op. (It can be left in
or reverted; it does nothing.)

The real bug is pointer corruption ~30 s after the .dt graphical login,
manifesting differently by RAM size (256 MB → delayed `PC=0` after the
corrupt pointer lands in still-valid RAM; 120 MB → immediate bus error
because the corrupt pointer hits unmapped PA). Signatures seen:
`zone_shake+0x64`, `mrlock_resort_queue+0x278`, `PC=0`, `bcopy+0xb0`.
Prime remaining suspect: the heavily-exercised gfx path (fm/bgicons
icon rendering → Xsgi → custom pvrex3/pvfb kernel drivers / gf_MapGfx)
corrupting kernel memory. It predates the Xsgi PIO patch (the earliest
zone_shake panics were before it). See task 15.

The original (wrong) theory is preserved below for the record.

---

# (original theory — overlay collision)

## Symptom

The full Indigo Magic `.dt` session (fm + bgicons + iconcatalog +
soundscheme) panicked on roughly half of boots, with THREE distinct
signatures across runs:
- `zone_shake+0x64`  — Bad addr 0x4 / 0x0 (zone allocator reclaim)
- `mrlock_resort_queue+0x278` — badvaddr 0xff800000, ra=emulate_branch,
  s4=c0vec_tbl (multi-reader lock queue walk in interrupt context)
- `PANIC PC=0x0` after "X connection broken / XIO fatal IO error"

The classic session (`desktop=off`, just 4Dwm+toolchest) was stable.
Every boot prints "fsck: Warning - Low free memory, swapping likely".

## Root cause

The HEART compatibility shim (`hw/mips/sgi_ip54pv.c`,
`memory_region_add_subregion_overlap` at PA 0x0FF00000, size 0x70000)
is overlaid on top of main RAM. RAM_BASE is 0x08000000, so 0x0FF00000
is **127 MB into RAM** — inside the populated range for 128/256 MB
configs.

The shim passes *non-register* offsets through to RAM, but ~16 register
offsets are intercepted: COUNT (0x20000), the ISR/IMR/IMSR/CAUSE/
SET_ISR/CLR_ISR cluster (0x10000-0x10040), COMPARE (0x30000), MODE,
STATUS, PRID, SYNC, TRIGGER. These are silent holes in "RAM":
- a write of a kernel pointer to e.g. 0x0FF20000 hits the COUNT
  register and vanishes;
- the read-back returns a 66 MHz timer tick → a garbage pointer;
- a write to 0x0FF10028 (CLR_ISR) also clobbers live interrupt state.

The PROM's `init_memory_descriptors()` handed pages 0xC000..0x18000
(256 MB) to the kernel as `FreeMemory` with a comment asserting the
HEART region was "RAM-transparent ... so no holes needed" — wrong. The
kernel allocated into pages 0xFF00..0xFF70 only under memory pressure,
which is exactly what the heavy `.dt` desktop creates. Whatever kernel
structure happened to land on a register offset produced the
(random-looking, load-dependent) panic. The classic session used less
memory and rarely reached the collision region; 64 MB RAM tops out at
0x0C000000, *below* the shim, and never collided at all — which is why
"64/128/256 MB all boot to multi-user" but only the big-memory desktop
crashed.

## Fix

`prom-building/src/fw/ip54_stubs.c init_memory_descriptors()`: split
the ">64MB" FreeMemory block to exclude pages 0xFF00..0xFF70, marking
them `FirmwarePermanent`. The kernel never allocates there; the shim's
register holes no longer alias live kernel data. Costs 448 KB.

## Lesson

An MMIO region overlaid on RAM is transparent only for the addresses
it forwards. Every offset it claims as a register is an invisible hole;
if the guest's firmware memory map still advertises that span as
ordinary RAM, the OS will eventually allocate into it and corrupt
itself in a way that looks like random, activity-correlated flakiness.
Either keep MMIO out of the RAM range, or punch the exact MMIO pages
out of the firmware free-memory map (done here).
