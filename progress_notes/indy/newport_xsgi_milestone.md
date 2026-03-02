# Newport Graphics: X Server Working (Xsgi + 4Dwm)

## Date: 2026-02-11

## Milestone

The IRIX Xsgi X server now starts and accepts client connections.
4Dwm window manager, xclock, and xterm all launch successfully.
This is a prerequisite for the full IRIX desktop experience.

## What Was Implemented

### Newport Drawing Engine Improvements (Phase 1-4)

1. **Screen-to-screen copy direction fix** — Source reads from `(start_x + window)`,
   destination writes to `(start_x + move, start_y + move)`. MAME ref: lines 3530-3533.

2. **DOSETUP (DM0 bit 5)** — `newport_do_setup()` computes octant from start/end
   coordinates before every command. Called from `newport_do_rex3_command()`.

3. **Pixel word read** — Complete rewrite of `newport_do_pixel_read()` to pack
   multiple pixels based on hostdepth (4/8/12/32bpp) with rwdouble support.
   New `newport_read_one_pixel()` helper with position advancement.

4. **LENGTH32 (DM0 bit 15)** — Clamps span/block X range to 32 pixels.

5. **LR_ABORT (DM0 bit 19)** — Aborts draw when direction is right-to-left.

6. **SKIPFIRST/SKIPLAST (DM0 bits 10,11)** — Skip first/last pixel in line drawing.

7. **Shade mode + get_rgb_color** — Extracts clamped R/G/B from curr_color
   accumulators. iterate_shade() advances colors by slope values per pixel.

8. **Color accumulators** — `curr_color_red/green/blue/alpha` fields track
   current shading state, reset at scanline boundaries.

### Critical VRINT Fix

**Root cause:** The IRIX ng1 kernel driver's VRINT interrupt handler does NOT
read REX3 STATUS to acknowledge the interrupt. It only reads INT3 local1_stat,
toggles the mask bit, and expects the hardware to deassert the GIO interrupt
line on its own when VBLANK ends.

Our original implementation kept the IRQ asserted until STATUS was read
(matching MAME's `read-to-clear` model). This caused INT3 local1_stat bit 7
to remain permanently set (0x80), making the kernel think the retrace
interrupt was stuck. The ng1 driver would never complete initialization,
blocking `open("/dev/graphics")` and preventing Xsgi from starting.

**Fix:** Model VRINT as a timed pulse:
- VBLANK timer (60Hz) asserts the IRQ and sets `vrint_active=true`
- A deassert timer fires 500µs later, lowering the IRQ
- The `vrint_active` flag prevents re-assertion during the pulse
- STATUS read still clears the VRINT status bit (for PROM compatibility)

This matches real hardware where the retrace signal is only active during
the vertical blanking interval (~40 scanlines ≈ 2.5ms at 60Hz).

## Trace Analysis

IRIX boot with graphics console generates ~176,000 Newport commands.
The dominant drawmodes are:

| Count | DM0 | Description |
|-------|-----|-------------|
| 163,840 | 0x00000046 | Host data block, 4bpp rwpacked (textport font) |
| 7,598 | 0x00000326 | Block fill with DOSETUP (rectangle clear) |
| 4,913 | 0x00009106 | Host data block with LENGTH32 (text rendering) |
| 200 | 0x00000000 | NOOP (sync) |
| 4 | 0x00000045 | Pixel read block |

Zero `unimp` warnings from Newport during IRIX boot.

## How to Start the Desktop

xdm starts automatically at boot and displays the graphical login screen.
The `grabServer: False` fix in `/var/X11/xdm/xdm-config` is applied
automatically by `tools/install_irix.py` during installation. See
[`xdm_graphical_login_fix.md`](xdm_graphical_login_fix.md).

Use `newport_sendkey` and `newport_mouse` MCP tools for keyboard/mouse
input. For example, to log in as root:

```python
newport_sendkey(session_id="...", text="root\n")
```

To start desktop apps manually via serial console:

```bash
setenv DISPLAY :0
/usr/bin/X11/4Dwm &
/usr/bin/X11/xclock &
/usr/bin/X11/xterm &
```

## Known Limitations

- Missing endian swap (DM1 bit 11) for host color data
- No multicast hash filtering for Seeq ethernet

## Files Modified

- `qemu/hw/display/sgi_newport.c` — Drawing engine, VRINT timing, command dispatch
- `qemu/include/hw/display/sgi_newport.h` — New state fields (curr_color, vrint)
- `tests/test_newport_drawing.py` — 30 new source analysis tests (125 total)

## Lessons Learned and Mistakes Made

### 1. MAME's model is not always how real hardware works

**Mistake:** Assumed MAME's read-to-clear VRINT model was ground truth. MAME
clears the GIO interrupt line when the REX3 STATUS register is read. We
faithfully replicated this. But the IRIX kernel's ng1 driver never reads
STATUS to acknowledge the interrupt — it only reads INT3 local1_stat, toggles
the mask, and expects the hardware to deassert on its own.

**Lesson:** MAME emulates hardware "well enough" for its purposes. Its
interrupt model works because MAME's screen device naturally cycles VBLANK
at real-time speed. In QEMU with icount, the virtual timer fires faster
than the kernel can process, and the level-held IRQ causes a permanently
stuck interrupt. Always check how the actual OS driver handles interrupts,
not just how the emulator models them.

### 2. Don't assume the X server is the problem

**Mistake:** When `xdpyinfo` hung, the initial assumption was that the Xsgi
X server itself was broken — stuck in some hardware initialization loop.
Spent significant time investigating the X server process, trying to trace
its system calls, and looking for kernel driver issues.

**Reality:** Xsgi was perfectly fine. It had started, created its sockets,
entered its event loop, and was waiting for connections. The actual problem
was that **xdm** (the display manager, a separate process) was blocking
during its initialization — likely on input device (keyboard/shmiq) setup.
When we killed xdm and started Xsgi directly with `-ac`, everything worked
immediately.

**Lesson:** When debugging a multi-process system, identify exactly WHICH
process is stuck before diving into root cause analysis. `ps -elf` shows
the PID tree — check parent/child relationships. The first xdpyinfo test
connected via xdm's X server, but xdm's child process was also blocked,
preventing X client connections from being processed.

### 3. Trace the right thing from the start

**Mistake:** Early debugging focused on Newport REX3 command traces (drawing
operations), looking for missing features that might block Xsgi. This was
a dead end — the drawing engine was fine, and the X server never even got
to the point of issuing draw commands.

**What worked:** Tracing INT3 interrupt state (`trace:sgi_hpc3_int3*`) and
looking at `local1_stat` and `local1_mask` over time. The pattern was
unmistakable: `stat=0x80` (retrace bit) stuck permanently, `mask` toggling
between `0xa2` and `0x22` as the kernel's interrupt handler ran but couldn't
clear the interrupt source.

**Lesson:** For "process hangs" issues, trace the interrupt/IRQ subsystem
first, not the device's functional registers. The hang was in the interrupt
acknowledge path, not in any drawing or register operation.

### 4. Level-sensitive vs edge-sensitive interrupt semantics matter enormously

**Mistake:** Treated the Newport VRINT as a level-sensitive interrupt —
asserted until explicitly cleared by reading STATUS. This matches MAME's
model and seemed reasonable.

**Reality:** Real Newport hardware generates a VBLANK pulse — the GIO
interrupt line goes high during vertical blanking (~500µs) then goes low
when active video resumes. The ng1 kernel driver depends on this: it
enables the mask, waits for the pulse, handles it, and knows the interrupt
will clear itself. A level-held interrupt that requires an explicit STATUS
read to clear is incompatible with this driver model.

**The fix required three attempts:**
1. First attempt: `vrint_next_allowed_ns` — enforce minimum gap between
   VRINTs. Didn't work because the problem wasn't re-assertion speed, it
   was that the IRQ never lowered at all.
2. Second attempt: `qemu_irq_pulse()` — raise then immediately lower.
   Didn't work because both calls happen in the same QEMU tick, so the
   CPU never sees the interrupt.
3. Third attempt: timed pulse with `vrint_deassert_timer` — assert on
   VBLANK, deassert 500µs later via a second timer. This works because
   it models the actual hardware behavior and gives the CPU time to
   enter the interrupt handler.

**Lesson:** When an interrupt-driven driver hangs, the interrupt polarity
and timing model is almost always the issue. Look at how the real hardware
signals the interrupt (pulse vs level), how the driver acknowledges it
(read status register vs mask toggle vs auto-clear), and whether the
emulator's model matches.

### 5. The kernel has the real graphics driver, not stubs

**Initial worry:** That the IRIX kernel might have been built with
`gfxstubs.a` instead of the real `ng1.a` Newport driver, since the kernel
was built via autoconfig during IRIX installation. If the graphics hardware
wasn't properly detected at install time, stubs would be linked.

**Verification:** `strings /unix | grep ng1_` found `ng1_i2c: Unknown
command %d` — a string from the real ng1 driver, not present in stubs.
Also found `rrm` error strings. The kernel has the full graphics driver.

**Lesson:** Check kernel symbols/strings early when debugging kernel driver
issues. If the stubs were linked, the entire investigation would have been
moot — no amount of hardware emulation fixes would help.

### 6. QEMU MMIO is always mediated — no "direct mapping" bypass

**Early theory:** Perhaps Xsgi mmaps Newport registers into userspace and
reads STATUS directly, bypassing our MMIO handler. If so, the
`qemu_irq_lower()` in the STATUS read path would never be called.

**Reality:** QEMU always mediates MMIO through the registered handler
functions. Even guest userspace loads/stores to memory-mapped device
regions go through the `MemoryRegionOps` callbacks. There is no bypass.
This theory was wrong.

**Lesson:** Don't waste time on impossible scenarios in QEMU's architecture.
MMIO is always handled. Focus on the actual handler logic and timer
interactions.
