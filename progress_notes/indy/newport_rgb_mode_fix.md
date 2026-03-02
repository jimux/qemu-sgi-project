# Newport RGB Mode Fix — 8bpp BGR Unpacking

## Problem

The xdm graphical login screen rendered the icon area with **only the red
channel** — a "Virtual Boy" effect where shapes and patterns were visible
but entirely in shades of red, with no green or blue.

## Root Cause

The icon area (lines 198-668, x=328-941) uses a **different DID** from the
rest of the screen:

| Region | DID | XMAP Mode | Type | CMAP Page |
|--------|-----|-----------|------|-----------|
| Background | 3 | 3 | CI 8bpp | Page 18 (0x1200) |
| Icon area | 6 | 6 | RGB-map1 8bpp | Gamma (0x1D00) |
| Border | 11 | 11 | CI 8bpp | Page 21 (0x1500) |

The icon area uses **XMAP mode 6 = RGB-map1 with 8bpp pixels**. This is an
RGB display mode where 8-bit pixel values are packed as 3-3-2 BGR (3 bits
red, 3 bits green, 2 bits blue).

The compositing code treated ALL non-CI pixel modes the same way: extracting
R from `pixel[7:0]`, G from `pixel[15:8]`, B from `pixel[23:16]`. For 8bpp
pixels, only the bottom byte has data — the upper bytes are zero. This gave:

- R = full pixel value (0-255) → valid red intensity
- G = 0 → no green
- B = 0 → no blue

## Fix

Added `newport_rgb_unpack()` function that handles packed BGR pixels for all
pixel sizes, matching MAME's `convert_{4,8,12}bpp_bgr_to_24bpp_rgb()`:

| pix_size | Bit Packing | Expansion |
|----------|-------------|-----------|
| 4bpp | 1-2-1 BGR (B[3], G[2:1], R[0]) | Each field scaled to 8-bit |
| 8bpp | 3-3-2 BGR (B[7:6], G[5:3], R[2:0]) | Weighted expansion: 0x92/0x49/0x24 |
| 12bpp | 4-4-4 BGR (B[11:8], G[7:4], R[3:0]) | Each nibble × 0x11 |
| 24bpp | Full bytes (B[23:16], G[15:8], R[7:0]) | Direct extraction (existing path) |

The BIT_SEL field (bit 0 of the XMAP mode entry) selects which portion of
the 32-bit VRAM word contains the pixel data (shift by 0 or N bits).

Applied to both compositing paths: the fb-dump/PPM path (for screendumps)
and the live display path (for VNC/SDL rendering).

## Discovery Process

1. **Enhanced VC2 DID diagnostic** to dump ALL unique DID line patterns
   (was limited to 8 lines). Revealed 3 distinct patterns: lines 0-197
   use DID 3 only, lines 198-668 add DID 6 for the icon region, lines
   669-1023 revert to DID 3 only.

2. **CMAP trace events** confirmed the X server writes CMAP data using
   full 32-bit word stores (dw=3), eliminating sub-word write corruption
   as a cause. All CMAP pages (0-31) are programmed during boot.

3. **MAME reference** showed that 8bpp RGB modes use 3-3-2 BGR packing,
   implemented in `convert_8bpp_bgr_to_24bpp_rgb()`. MAME only implements
   RGB Map0 (pix_mode=1); Map1 and Map2 return placeholder colors.

## Files Changed

- `qemu/hw/display/sgi_newport.c`: Added `newport_rgb_unpack()` helper,
  updated both compositing paths to use it for non-CI pixel modes.
  Enhanced VC2 diagnostic to show all unique DID line patterns.
  Added CMAP palette write and DCBDATA0 sub-word trace events.
- `qemu/hw/display/trace-events`: Added `sgi_newport_cmap_palette_write`
  and `sgi_newport_dcbdata0_subword` trace event definitions.

## Verification

- xdm login screen renders with correct colors: EZsetup icon shows
  yellow/brown, user icons are gray/purple, background is light pattern
- All 703 fast tests pass with 0 failures
- No regressions in existing Newport drawing/compositing behavior
