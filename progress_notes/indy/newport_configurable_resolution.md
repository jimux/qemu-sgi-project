# Newport Configurable Resolution

## Summary

Added configurable width/height properties to the Newport graphics controller,
enabling native rendering at higher resolutions (e.g., 1920x1080, 2560x1440,
5120x2160) instead of the original 1280x1024.

## Usage

```
-global sgi-newport.width=1920 -global sgi-newport.height=1080
```

Default remains 1280x1024 if not specified.

## Implementation Details

### Step 1: Configurable Properties

Replaced compile-time constants (`NEWPORT_VRAM_W`, `NEWPORT_VRAM_H`,
`NEWPORT_SCREEN_W`, `NEWPORT_SCREEN_H`) with runtime state fields
(`screen_w`, `screen_h`, `vram_w`, `vram_h`). VRAM dimensions are computed
as `screen + 64` guard band (matching MAME's original layout).

New header defines:
- `NEWPORT_DEFAULT_SCREEN_W` (1280) / `NEWPORT_DEFAULT_SCREEN_H` (1024)
- `NEWPORT_VRAM_GUARD` (64)
- `NEWPORT_MAX_SCREEN_W` (7680) / `NEWPORT_MAX_SCREEN_H` (4320)

### Step 2: Dynamic VRAM Allocation

VRAM arrays (`vram_rgbci`, `vram_cidaux`) are allocated at realize time using
the configured dimensions. All 30+ references to the old constants were
replaced with the state fields.

### Step 3: VC2_SCANLINE_LEN Intercept

When IRIX reads VC2 register 0x06 (SCANLINE_LEN) and a non-default width is
configured, we return `(screen_w << 5)` instead of what the PROM wrote. This
makes the kernel and Xsgi believe the hardware has a wider active scanline.

### Step 4: DID Frame Table Extension

The PROM writes 1024 DID entries (one per scanline). For `screen_h > 1024`,
the display update and PPM dump functions clamp the DID frame table index to
the last PROM-written entry (line 1023). This ensures extended scanlines
inherit the same display mode (pixel format, color index MSB) as the last
standard line.

### Step 5: VMState

VMState bumped to version 6. Screen dimensions and VRAM buffer size are saved
in the snapshot. Uses `VMSTATE_VBUFFER_ALLOC_UINT32` for variable-length VRAM
buffers with a `pre_load` callback to free stale allocations.

## Files Modified

| File | Changes |
|------|---------|
| `qemu/include/hw/display/sgi_newport.h` | New constants, `screen_w/h`, `vram_w/h`, `vram_buf_size` fields |
| `qemu/hw/display/sgi_newport.c` | Properties, dynamic alloc, all dimension refs → state fields, VC2 intercept, DID clamping, VMState v6 |
| `tests/test_newport_source.py` | Updated dimension tests for new constant names |
| `tests/test_newport_drawing.py` | Updated VRAM bounds check pattern |

## Testing

- All 767 fast tests pass
- PROM boots cleanly at 1280x1024 (default) and 1920x1080
- Framebuffer correctly allocated at configured size (verified via PPM dump)
- PROM content renders in original 1280x1024 area; extended area is black
- DID clamping correctly handles scanlines beyond PROM-written range

## Known Limitations

- **PROM rex3Clear()** clears to its hardcoded width (1343). This means the
  PROM's background only fills the original area — cosmetic only, IRIX
  re-clears at boot.
- **VMState v6 incompatible with v5 snapshots.** Existing snapshots at
  1280x1024 must be re-created. This is a one-time cost.
- **gfx_info patching (Step 5 from plan) not implemented.** If IRIX reads
  `xpmax/ypmax` from the ARCS `ng1_info` struct in guest RAM instead of
  from hardware registers, the kernel won't see the new resolution. This
  requires testing with a full IRIX boot at non-default resolution. Can be
  added later if needed.

## What Could Go Wrong at Higher Resolutions

| Risk | Status |
|------|--------|
| PROM cosmetic: background only fills 1280 wide | Confirmed — harmless |
| Kernel reads gfx_info instead of hardware | Unknown — needs IRIX boot test |
| Xsgi internal resolution check | Unknown — needs IRIX boot test |
| REX3 drawing at large coordinates | Should work — 16-bit signed (±32K) |
| VRAM memory at 5K (54MB per plane) | Trivial for host |
| Performance at 5K (8x more pixels to fill) | Host-CPU-bound with icount |
