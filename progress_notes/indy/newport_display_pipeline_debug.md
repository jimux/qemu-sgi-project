# Newport Display Pipeline Debugging

## Status: ALL FIXED — xdm Login Renders Correctly

The overlay compositing pipeline is now correct. Two bugs were fixed:
1. **Overlay transparency checked wrong bits** (cidaux[7:0] instead of [9:8])
2. **RGB mode missing BGR→RGB byte swap**

The xdm login dialog renders correctly (gray box, blue text, Login/Password
fields). Background still has horizontal line artifacts due to incomplete VRAM
content from the X server's root window fill — this is a pre-existing REX3
drawing engine issue, not a compositing problem.

---

## Timeline of Bugs Fixed

### Bug 1: RAMDAC Gamma LUTs Initialized to All Zeros

**File:** `qemu/hw/display/sgi_newport.c` — `sgi_newport_reset()`

Both display paths (`newport_update_display()` and `newport_dump_vram_ppm()`)
pass every pixel through the RAMDAC gamma LUTs as a final step:
```c
r = s->ramdac_lut_r[(rgb >> 16) & 0xff];
g = s->ramdac_lut_g[(rgb >> 8) & 0xff];
b = s->ramdac_lut_b[rgb & 0xff];
```

The LUTs were initialized to all zeros via `memset(..., 0)`, so every pixel
mapped to black. The PROM's `SetGammaIdentity()` programs identity LUTs via
DCB writes during POST, which is why the PROM gradient was visible — but only
after PROM init ran.

**Fix:** Initialize RAMDAC LUTs to identity mapping (`LUT[i] = i`).

### Bug 2: RAMDAC R/B Byte Order Swap

**File:** `qemu/hw/display/sgi_newport.c` — RAMDAC DCB write handler

The RAMDAC LUT write handler extracted R and B from the wrong bit positions:
```c
// BUG: R and B were swapped
s->ramdac_lut_r[idx] = (uint8_t)(val >> 8);   // Was extracting B
s->ramdac_lut_b[idx] = (uint8_t)(val >> 24);  // Was extracting R
```

IRIX `Bt445SetRGB()` packs as `(r << 24) | (g << 16) | (b << 8)`, so
bits 31:24 = R, bits 23:16 = G, bits 15:8 = B.

**Fix:** Match IRIX packing: `R = val >> 24`, `G = val >> 16`, `B = val >> 8`.

### Bug 3: VMState Incomplete

**File:** `qemu/hw/display/sgi_newport.c` — `vmstate_sgi_newport`

The VMState was missing VRAM arrays, VC2 SRAM/registers, RAMDAC LUTs, CMAP
palette, and numerous other fields. Snapshot save/restore would lose the
entire framebuffer and display configuration.

**Fix:** Added all missing fields with version bump to 2 and `post_load` hook
to rebuild decoded drawmode state.

### Bug 4: VC2 Data Width Mask (dw=3)

**File:** `qemu/hw/display/sgi_newport.c` — DCB val masking

The `dw_mask[3]` value was `0x00ffffff` instead of `0xffffffff`. This caused
VC2 writes using dw=3 (full 32-bit) to have their top 8 bits masked off.

**Fix:** Changed `dw_mask[3]` to `0xffffffff`.

### Bug 5: VC2 Data Extraction Position (dw=2)

The VC2 data extraction for `dw=2` (16-bit) was reading from the wrong half
of the 32-bit value. VC2 expects data in the lower 16 bits for dw=2.

**Fix:** Changed extraction to use lower 16 bits for dw=2.

### Bug 6: Global DCB Val Masking

DCB writes were globally applying `dw_mask` to all devices. Some devices
(notably XMAP and VC2) need the full unmasked value because the index is
encoded in the upper bits.

**Fix:** Changed to selective masking — only apply `dw_mask` to CMAP writes,
pass the full value to XMAP and VC2 handlers.

### Bug 7: Screen-to-Screen Copy Direction

**File:** `qemu/hw/display/sgi_newport.c` — `newport_draw_scr2scr()`

Source and destination offsets for block copies were miscalculated, causing
corrupted copies.

**Fix:** Corrected source/dest offset calculations.

### Bug 8: VRINT Level-Held vs Timed Pulse

**File:** `qemu/hw/display/sgi_newport.c` — VRINT interrupt handling

The VRINT (vertical retrace interrupt) was held at level until STATUS was read,
following MAME's model. But IRIX's ng1 kernel driver doesn't read STATUS to
clear the interrupt — it expects the hardware to deassert on its own when
VBLANK ends. Level-held VRINT caused INT3 local1_stat bit 7 to remain
permanently set, blocking Xsgi from opening `/dev/graphics`.

**Fix:** Changed to timed pulse model: assert IRQ on 60Hz VBLANK timer,
schedule deassert 500µs later via second timer. Use `vrint_active` flag to
prevent re-assertion during pulse.

### Bug 9: Overlay Compositing — FIXED (cidaux bit extraction + per-mode handling)

**File:** `qemu/hw/display/sgi_newport.c` — `newport_update_display()` and
`newport_dump_vram_ppm()`

**Root cause:** The overlay compositing branch checked `cidaux & 0xff` for
transparency. In the XL8 cidaux bit layout, bits [7:0] are CID plane data
(per-window visual mode tags written by the X server), NOT overlay data.
The overlay bits are at [9:8].

**Old code:**
```c
} else if (aux_pix_mode != 0
           && ((cidaux & 0xff) != 0           // WRONG: tests CID bits
               || aux_pix_mode == 1 || aux_pix_mode == 7)) {
    uint8_t aux_ci = cidaux & 0xff;           // WRONG: reads CID, not overlay
    uint16_t aux_msb = ((s->xmap_mode_table[...] >> 11) & 0x1f) << 8;
    rgb = s->cmap0_palette[(aux_msb | aux_ci) & 0x1fff];
```

**Why this caused black background:** Background pixels have CID=3 (written
by X server for per-window visual tagging), so `cidaux & 0xff = 3 != 0`.
These pixels entered the overlay branch and looked up
`CMAP[0x1b00 | 3] = CMAP[0x1b03]`. CMAP page 0x1b is uninitialized (all
zeros) → black pixels.

**Fix:** Rewrote the overlay branch to match MAME's per-mode handling with
correct bit extraction from cidaux[9:8]:

```c
} else if (aux_pix_mode != 0) {
    bool overlay_hit = false;
    switch (aux_pix_mode) {
    case 1: /* 2-Bit Underlay — always drawn */
        rgb = cmap[(aux_msb | ((cidaux >> 8) & 3)) & 0x1fff];
        overlay_hit = true;
        break;
    case 2: /* 2-Bit Overlay — zero is transparent */ {
        uint32_t ovl = (cidaux >> 8) & 3;
        if (ovl) { rgb = cmap[(aux_msb | ovl) & 0x1fff]; overlay_hit = true; }
        break;
    }
    case 6: /* 1-Bit Overlay */ {
        uint32_t shift = (mode_entry & 2) ? 9 : 8;
        uint32_t ovl = (cidaux >> shift) & 1;
        if (ovl) { rgb = cmap[(aux_msb | ovl) & 0x1fff]; overlay_hit = true; }
        break;
    }
    case 7: /* 1-Bit Overlay + 1-Bit Underlay */ {
        uint32_t ovl = (cidaux >> 8) & 1;
        if (ovl) rgb = cmap[(aux_msb | ovl) & 0x1fff];
        else     rgb = cmap[(aux_msb | ((cidaux >> 9) & 1)) & 0x1fff];
        overlay_hit = true;
        break;
    }
    }
    if (!overlay_hit) goto main_pixel;
}
```

Also added `aux_msb` and `mode_entry` as per-scanline cached variables
(extracted at DID-change time alongside `ci_msb`), matching MAME's approach.
Applied identically to both `newport_update_display()` and
`newport_dump_vram_ppm()`.

MAME reference: `newport.cpp` lines 1350-1393 (XL8 overlay handling).

### Bug 10: RGB Mode BGR→RGB Byte Swap — FIXED

**File:** `qemu/hw/display/sgi_newport.c` — both compositing functions

Newport VRAM stores RGB data in BGR byte order. MAME swaps bytes during
compositing (`newport.cpp:1494-1499`). QEMU was passing the raw value
through, which would produce swapped colors for TrueColor visuals.

**Old:** `rgb = pixel & 0xffffff;`

**Fix:**
```c
uint8_t pr = pixel & 0xff;
uint8_t pg = (pixel >> 8) & 0xff;
uint8_t pb = (pixel >> 16) & 0xff;
rgb = (pr << 16) | (pg << 8) | pb;
```

**Impact:** Does not affect current xdm login (CI mode). Will affect future
TrueColor visuals (pix_mode 1/2/3, pix_size 3 for 24bpp).

### Bug 11: Block Fill Unconditional Y Advance — FIXED

**File:** `qemu/hw/display/sgi_newport.c` — `newport_draw_block()`

**Root cause:** The block fill outer loop unconditionally advanced Y (`sy += dy`)
after every inner loop iteration. In MAME, Y only advances inside the
"X reached end of row" conditional. This difference is critical for
rwpacked/colorhost modes where the X server feeds pixels one host word at
a time (drawing 8 pixels per command with `stoponx=0, stopony=0`).

**How the X server fills the root window:**
The IRIX Newport DDX driver uses colorhost+rwpacked block fills:
```
dm0=0x00000046  DRAW BLOCK DOSETUP COLORHOST
dm1=0x30007589  rwpacked=1 rwdouble=1 hostdepth=1(8bpp)
stoponx=0 stopony=0
```
Each host data write draws 8 packed CI pixels. The hardware draws 8 pixels,
advances X by 8, and returns. The X server writes the next host word, and
the hardware continues from the updated X position. When X reaches end_x,
Y advances and X resets to x_save.

**Old code (buggy):**
```c
do {
    sx = start_x;          // BUG 1: resets X progress each iteration
    do {
        // draw pixel
        sx += dx;
    } while (sx != prim_end_x && sx != end_x && stop_on_x);

    if (sx >= end_x) {     // X reached end of row
        sx = x_save;
    }
    sy += dy;              // BUG 2: unconditional Y advance
} while (sy != end_y && stop_on_y);
```

With stoponx=0 and rwpacked forcing stop_on_x=true for 8 pixels:
- Inner loop draws 8 pixels at (start_x, start_y), sx = start_x + 8
- Check: sx (start_x+8) >= end_x (1279)? NO → no reset
- sy += dy → sy = start_y + 1 (WRONG!)
- Outer loop exits (stopony=0)
- Coordinates saved: x=start_x+8, y=start_y+1

Result: each 8-pixel chunk shifted (+8, +1) → **diagonal line pattern**.
The 64,64,32 Y-spacing pattern was an aliasing artifact of the 8-pixel
X increment vs 1344 VRAM stride.

**MAME reference (lines 3478-3486):**
```cpp
if ((dx > 0 && start_x >= end_x) || ...) {
    start_x = m_rex3.m_x_save;
    start_y += dy;        // Y advance ONLY when row completes
}
```

**Fix:** Restructured block fill to match MAME exactly:
1. Moved `sy += dy` inside the "X reached end" conditional
2. Removed `sx = start_x` reset at top of outer loop — sx carries forward
   so partial rows (rwpacked, LENGTH32) continue where they left off

---

## Current State (2026-02-13, Post-Fix)

### What Works

- **PROM gradient:** Blue gradient renders correctly (light blue top to
  purple-blue bottom), 6.8KB PNG. No regression from any changes.

- **xdm login screen:** Complete and correct rendering:
  - Solid sgilightblue (#7d9ec0 after gamma) background — no artifacts
  - Gray dialog box with "Welcome to IRIS / IRIX 6.5"
  - Login: and Password: fields with text cursor
  - Blue italic text for titles
  - 8.8KB PNG (vs 17KB before — smaller due to uniform background)

- **Overlay compositing:** aux_pix_mode=2 correctly checks cidaux[9:8],
  transparent pixels fall through to CI/RGB path.

- **Block fills:** rwpacked/colorhost mode with stoponx=0/stopony=0 now
  correctly fills entire rectangles without diagonal artifacts.

### Subsequent Milestones

- **Keyboard/mouse input** — implemented via PS/2 8042 in IOC2. See
  [`keyboard_mouse_input.md`](keyboard_mouse_input.md).
- **Newport RGB mode fix** — 8bpp BGR unpacking for mixed-depth visuals.
  See [`newport_rgb_mode_fix.md`](newport_rgb_mode_fix.md).

---

## Framebuffer Captures

| File | Description | Size | Key Observation |
|------|-------------|------|-----------------|
| `20260213_151902_exp0_prom_menu.png` | PROM System Maintenance Menu | 6,812B | Blue gradient, no text (text via cursor) |
| `20260213_151951_exp0_irix_login_prompt.png` | IRIX xdm login screen (pre-fix) | 17,095B | Dialog renders! Background black + line artifacts |
| `20260213_160213_prom_menu_after_compositing_fix.png` | PROM menu after fix | 6,812B | No regression — gradient still correct |
| `20260213_160448_xdm_check_1.png` | xdm login after overlay fix | 17,095B | Dialog correct. Background black + diagonal lines |
| `20260213_170945_block_fill_fix.png` | xdm login after block fill fix | 8,822B | **FIXED** — solid sgilightblue background |
| `20260213_171212_prom_after_blockfill_fix.png` | PROM gradient regression check | 6,812B | No regression — gradient still correct |

---

## Reference: cidaux Bit Layout (XL8)

From MAME `newport.cpp` store_shift table:
```
Bits [3:0]  = CID buffer 0 (4 bits)
Bits [3:2]  = Popup buffer 0 (2 bits, overlapping CID)
Bits [7:4]  = CID buffer 1
Bits [7:6]  = Popup buffer 1
Bits [9:8]  = Overlay buffer 0 (2 bits)
```

Popup check mask: `cidaux & 0xcc` = bits {7,6,3,2}
Overlay read: `(cidaux >> 8) & 3` (NOT `cidaux & 0xff`)

## Reference: XMAP9 Mode Entry Fields

```
Bits [0]     BUF_SEL (front/back buffer select)
Bits [1]     OVL_BUF_SEL (overlay buffer select)
Bits [2]     GAMMA_BYPASS
Bits [7:3]   MSB_CMAP (CI palette page select)
Bits [9:8]   PIX_MODE (0=CI, 1=RGB Map0, 2=Map1, 3=Map2)
Bits [11:10] PIX_SIZE (0=4bpp, 1=8bpp, 2=12bpp, 3=24bpp)
Bits [18:16] AUX_PIX_MODE (0=none, 1=underlay, 2=overlay, 6/7=mixed)
Bits [23:19] AUX_MSB_CMAP (overlay palette page select)
```

Mode 3 entry 0xda0490 decoded:
- BUF_SEL=0, OVL_BUF_SEL=0, GAMMA_BYPASS=0
- MSB_CMAP = (0x90 >> 3) & 0x1f = 0x12 → ci_msb = 0x12 << 8 = 0x1200
- PIX_MODE = 0 (CI)
- PIX_SIZE = 1 (8bpp)
- AUX_PIX_MODE = 2 (overlay)
- AUX_MSB_CMAP = (0xda >> 3) & 0x1f = 0x1b → aux_msb = 0x1b00

## Files

| File | Role |
|------|------|
| `qemu/hw/display/sgi_newport.c` | Display pipeline implementation |
| `qemu/include/hw/display/sgi_newport.h` | State structures |
| `mame/source/src/devices/bus/gio64/newport.cpp` | MAME reference |
| `gathered_documentation/newport_graphics/NEWPORT_REFERENCE.md` | Comprehensive docs |
| `gathered_documentation/newport_graphics/NEWPORT_XMAP9.md` | XMAP9 mode details |
