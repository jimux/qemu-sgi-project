# The "black root window" bug — XMAP9 mode table kernel invariant

Date: 2026-06-12. Fixed in `hw/display/sgi_pvrex3.c` (reset function).

## Symptom

Ever since Xsgi first ran on IP54/pvrex3, the root window rendered
BLACK (xlogin on black, desktop sessions on black, no weave). Toolchest
showed literal red/green. Everyone chased drawing bugs and scheme
loading — the drawing was fine all along.

## Root cause

Xsgi paints the root correctly (a fullscreen colorhost block — 327,684
HOSTRW GO writes — VRAM was 1.3M/1.46M non-zero pixels!). The pixels
were CI values whose **XMAP9 mode lookup failed at scanout**:

- VC2 DID entries from Xsgi reference XMAP mode-table indices like 0xb.
- Our device reset initialized ONLY `mode_table[0] = 0x000490`
  (CI8, CMAP page 18) — entries 1..31 were 0x000000.
- Mode 0 (value 0) → ci_msb = 0 → CMAP page 0 (all black entries) →
  every window whose DID referenced a non-zero mode index rendered
  black, regardless of VRAM content.

The kicker (verified with a DCB write trace over a FULL boot): **Xsgi
never writes the XMAP mode table itself** — just 2 XMAP writes total
(cursor/popup cmap MSBs). On real hardware the PROM/ng1 kernel driver
establishes the invariant "all 32 mode registers = 8-bit CI"
(`ng1_init.c: for(i=0;i<32;i++) xmap9SetModeReg(rex3, i, XM9_PIXSIZE_8, …)`)
and the X server relies on it.

## Fix

Initialize ALL 32 mode-table entries to 0x000490 at device reset
(matching the PROM loop). One-liner; instantly: xlogin appears on the
proper `-solidroot sgilightblue` background.

## Debugging method that worked

1. trace draw ops (block/span/scr2scr with coords + dm0/dm1) — proved
   ops arrive and complete.
2. trace ALL reg writes gated via monitor `trace-event ... on` around
   the repro — proved no MMIO is lost and the op plan is coherent
   (xsetroot clips around the xlogin window — what looked like "random
   bands" was a correct plan minus three large transfers).
3. The screendump's own Newport-state diagnostics (`VRAM non-zero`,
   `mode_table`, `DID line` dumps) contained the answer: painted VRAM +
   mode_table[11] == 0.
4. `--trace sgi_pvrex3_dcb_write` over a full boot for the protocol
   question (who writes XMAP and how).

## Still open (separate bug): large PutImage data loss

xsetroot granite: seed rects ≤42 rows arrive via PIO HOSTRW (painted),
seed rects ≥214 rows NEVER reach the device (no MMIO anywhere, no
unimp hits, no faults). Suspect: the DDX's bulk path uses the IP22 MC
**write-gather window** (or a DMA ioctl) — a second mapping requested
via gf_MapGfx flags that our pvfb.c ignores (it always maps the 8KB
REX3 regs; check `gf_MapGfx flags=` values in SYSLOG). Symptom:
xsetroot tile fill covers only horizontal bands; 4Dwm/desktop
backgrounds will be partial until fixed.
