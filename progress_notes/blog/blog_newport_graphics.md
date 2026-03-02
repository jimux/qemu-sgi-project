# From Blank Screen to Indigo Magic: Building a Newport Graphics Engine

## The Arc

The Newport (XL) graphics subsystem is the most complex single device in the SGI Indy emulator. It's also the most visible. Like, literally. Over many very long sessions, it went from a stub that returned zeroes to a fully functional display system running the Indigo Magic Desktop with and mouse-driven interaction.

This is the story of eleven bugs, three architecture rewrites, and the discovery that MAME's reference implementation and the real hardware don't *always* agree. At least, enough that the implementation details worked for MAME, but broke for QEMU, a world where things work differently.

## The Hardware

Newport is SGI's mid-range 2D/3D graphics board for the Indy and Indigo 2. Its display pipeline has five major components:

```
REX3 (Raster Engine)     → 2D/3D drawing commands, VRAM access
VC2 (Video Controller)   → Scan timing, cursor, display ID tables
XMAP9 (CrossbarMap)      → Per-pixel visual mode selection (CI, RGB, overlay)
CMAP (Color Map)         → 256-entry palette for CI (Color Index) mode
RAMDAC (Bt445)           → Digital-to-analog conversion, gamma LUTs
```

All five communicate through the DCB (Display Control Bus), a shared bus accessed through REX3's DCB registers. Writing to REX3 at the right address with the right mode bits routes data to the appropriate sub-device. It's a bus-within-a-bus.

VRAM is dual-ported: the REX3 draws into it from one side, and the VC2/XMAP9 scan it out from the other. Each pixel has 38 bits: 24 bits of color data (RGB or CI index), 4 bits of overlay/popup planes, 4 bits of CID (Color ID for per-window visual mode), and auxiliary bits. The XMAP9 uses the CID to look up how each pixel should be interpreted, enabling mixed-depth windows on the same screen.

## First-up: VRAM and Block Fills

The first real graphics came from the PROM. During POST, the PROM fills the screen with a blue gradient. Light blue at the top fading to dark purple at the bottom. This requires:

1. **Block fill**: REX3 command 0x00000326: fill a rectangular region with a solid color
2. **Host data**: REX3 command 0x00000046: write pixel data from the CPU into VRAM (used for text glyphs)
3. **VRAM storage**: 1344×1024 pixels × 38 bits per pixel

The block fill was straightforward: compute the rectangle bounds from the REX3 coordinate registers and fill every pixel. But the first attempt produced a solid blue rectangle with no gradient. The PROM programs different colors for each horizontal band, using DOSETUP (bit 5 of drawmode0) to recompute draw parameters before each command. Without DOSETUP, the color stayed fixed.

Host data mode was trickier. The PROM renders text characters by writing packed 4-bit pixel data through REX3's HOSTRW register. Each 32-bit write contains 8 pixels at 4bpp, and the hardware unpacks them into VRAM. Getting the packing format right required reading MAME's `newport_fill_pixel()` carefully. The most significant nibble is the leftmost pixel.

After a lot of iteration, the PROM gradient appeared in the VNC window. A smooth blue gradient with white "System Maintenance Menu" text. Our first image.

## Next Challenge: The DCB Minefield

With the PROM rendering, the next target was the IRIX kernel's graphics initialization. The kernel's `ng1` Newport driver performs extensive hardware setup: programming the VC2 with scan timing tables, loading CMAP palettes, configuring XMAP9 mode entries, and setting RAMDAC gamma correction LUTs.

All of this goes through the DCB bus, and the DCB has a subtle addressing scheme. A single 32-bit write to the REX3 DCB register encodes:

- **Device select** (bits in REX3 DCB_MODE): Which sub-device to talk to (VC2, XMAP, CMAP, or RAMDAC)
- **Data width** (dw field): How many bits to transfer (8, 16, 24, or 32)
- **Config bits**: Device-specific configuration

The RAMDAC gamma LUTs were our first real display problem. After the kernel initialized them, the screen went black. Every pixel was being mapped through a gamma correction table that was... all zeros.

### Bug 1: RAMDAC Gamma LUT Initialization

The gamma LUTs are 256-entry tables for R, G, and B channels. Every pixel's color components pass through these tables as the last step before display. The PROM's `SetGammaIdentity()` programs them to identity mapping (LUT[i] = i), so colors come through unchanged. But our code initialized the LUTs to zero with `memset()`.

After the PROM programmed them, they worked. But if the kernel reset them (during a crash or driver re-init), they went back to zero. The fix: initialize the LUTs to identity mapping at reset time.

### Bug 2: RAMDAC R/B Byte Swap

The RAMDAC LUT write handler extracted the red and blue channels from the wrong bit positions. IRIX's `Bt445SetRGB()` packs color as `(R << 24) | (G << 16) | (B << 8)`, but I was extracting `R = val >> 8` and `B = val >> 24` swapped. The PROM gradient happened to look plausible with swapped R/B (blues and reds are both in the cool spectrum), but any warm colors would have been wrong.

### Bug 3: VC2 Data Width Mask

The DCB data width field controls how many bits of a 32-bit write are meaningful. For VC2 writes, `dw=3` means "full 32-bit." Our `dw_mask[]` array had `dw_mask[3] = 0x00ffffff` instead of `0xffffffff`, silently masking the top 8 bits of every full-width VC2 write. This corrupted the VC2 SRAM contents. Scan timing tables had their MSB stripped, causing slightly wrong display timing. The visual effect was subtle (minor position shifts) but the bug was insidious.

## The Quagmire: The VRINT Discovery

Everything worked for PROM graphics and kernel textport mode. Then I tried to start the X server.

```
# /usr/bin/X11/Xsgi :0 &
```

Nothing. The Xsgi process started, but it hung during initialization. `xdpyinfo` hung trying to connect. No display output beyond the kernel textport.

Hours of debugging led to the interrupt subsystem. The Newport generates a VRINT (Vertical Retrace INTerrupt), a signal that fires once per frame (60Hz) when the display beam returns to the top of the screen. The X server's ng1 kernel driver needs this interrupt for frame synchronization.

Our initial implementation followed MAME's model: assert the GIO interrupt when VBLANK starts, and deassert it when the guest reads the REX3 STATUS register. This is a "read-to-clear" model. The interrupt stays asserted until the driver explicitly acknowledges it.

The IRIX ng1 driver doesn't work this way. It never reads STATUS to acknowledge the interrupt. Instead, it reads INT3 local1_stat (bit 7), toggles the interrupt mask, and *expects the hardware to deassert on its own when VBLANK ends*. With our level-held model, the interrupt stayed asserted forever. INT3 local1_stat bit 7 was permanently high. The ng1 driver saw a stuck interrupt, refused to complete initialization, and blocked `open("/dev/graphics")`, which blocked Xsgi from starting.

### Three Attempts at the Fix

**Attempt 1:** Enforce a minimum gap between VRINTs. Didn't help. The problem wasn't re-assertion speed, it was that the IRQ never lowered at all.

**Attempt 2:** Use `qemu_irq_pulse()` to raise then immediately lower. Didn't work. Both calls happen in the same QEMU tick, so the CPU never sees the interrupt.

**Attempt 3:** Timed pulse model. Assert the IRQ on the 60Hz VBLANK timer. Schedule a second timer to deassert 500µs later. Use a `vrint_active` flag to prevent re-assertion during the pulse.

```c
/* VBLANK timer fires at 60Hz */
static void newport_vblank_timer(void *opaque) {
    if (!s->vrint_active) {
        s->vrint_active = true;
        qemu_irq_raise(s->irq);
        /* Schedule deassert 500µs later */
        timer_mod(s->vrint_deassert_timer,
                  qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 500000);
    }
}

/* Deassert timer */
static void newport_vrint_deassert(void *opaque) {
    qemu_irq_lower(s->irq);
    s->vrint_active = false;
}
```

This models the actual hardware behavior: the GIO interrupt is active during the vertical blanking interval (~500µs at 60Hz), then goes low when active video resumes. The kernel driver depends on this auto-deassert behavior.

After this fix, Xsgi started immediately. `4Dwm`, `xclock`, and `xterm` all launched. I had a desktop!

## Wrong Abstraction: xdm and the grabServer Mystery

The X server worked when started manually. But IRIX boots with xdm (the X Display Manager), which manages the graphical login screen. xdm started Xsgi, connected to it, and then... hung. No login screen. No display output. `xdpyinfo` hung waiting for the server to respond.

The investigation went in circles before I found the one-line fix: `grabServer: False` in `/var/X11/xdm/xdm-config`.

When `grabServer` is True (the default), xdm calls `XGrabServer()` after connecting to the X server. Inside Xsgi, the XGrabServer processing interacts with the shmiq (shared memory input queue) subsystem, SGI's proprietary input device layer. Without actual keyboard/mouse hardware generating interrupts, the shmiq path blocked, freezing the X server and preventing all client connections.

The fix was one word: change `True` to `False`. On real SGI hardware with a physical keyboard generating interrupts, the grab works fine. In our emulator, the shmiq subsystem needs a keyboard interrupt to unblock the grab processing, and without it, the server hangs.

This was the wrong-level-of-abstraction debugging trap. I was stuck tracing REX3 commands, looking for missing drawing features, investigating kernel driver state machines, all at the graphics hardware level. The actual problem was in xdm's configuration, three levels of abstraction above the hardware.

## Down to Business: The Display Pipeline

With xdm configured to not grab the server, the login screen appeared... mostly. The dialog box rendered correctly (gray background, blue text, Login/Password fields). But the background behind it was black with diagonal line artifacts instead of the expected solid sgilightblue color.

### Bug: Overlay Compositing: The Wrong Bits

The Newport display pipeline composites multiple layers per pixel. Each pixel has a CID (Color ID) that selects an XMAP9 mode entry, which determines how to interpret the pixel data: as Color Index (palette lookup), RGB, overlay, or underlay. The compositing logic must check the correct bits for each mode.

Our overlay transparency check was testing `cidaux & 0xff`, the CID plane bits. The overlay data is at bits [9:8], not [7:0]. Background pixels had CID=3 (written by the X server for per-window visual tagging), so `cidaux & 0xff = 3 != 0`. These pixels entered the overlay branch and looked up CMAP page 0x1b (from the aux_msb field) at index 3, which was uninitialized (zero = black).

The fix was essentially just better matching MAME's per-mode overlay handling.:

```c
switch (aux_pix_mode) {
case 2: /* 2-Bit Overlay. Zero is transparent */
    uint32_t ovl = (cidaux >> 8) & 3;   /* Bits [9:8], not [7:0] */
    if (ovl) { rgb = cmap[...]; overlay_hit = true; }
    break;
/* ... other modes ... */
}
```

### Bug: Block Fill Y Advance

The diagonal line artifacts in the background were from a separate bug in the block fill engine. When the X server fills the root window, it uses colorhost+rwpacked mode, writing 8 packed 8-bit pixels per 32-bit host data word, with `stoponx=0` and `stopony=0`. The hardware draws 8 pixels, stops, and waits for the next word.

Our block fill outer loop unconditionally advanced Y after every inner loop iteration. In MAME, Y only advances when X reaches the end of a row. With stoponx=0 and rwpacked forcing a stop after 8 pixels, each batch of 8 pixels shifted diagonally: (+8 pixels in X, +1 pixel in Y). The 64-64-32 Y-spacing artifact was aliasing of the 8-pixel X increment against the 1344-pixel VRAM stride.

The fix: Once again, MAME's implementation to the rescue. Recreating the block fill to match it better: Y advances only inside the "X reached end of row" conditional, and X carries forward between iterations so partial rows (from rwpacked or LENGTH32 modes) continue where they left off.

After both fixes, the xdm login screen rendered perfectly: solid sgilightblue background, gray dialog box, blue italic text.

## Wrapping It Up: Keyboard and Mouse

A login screen is useless without input. The SGI Indy has a PS/2 keyboard and mouse port connected through an 8042 controller embedded in the IOC2 ASIC. The entire input stack was already implemented from the PROM bring-up phase, I just hadn't tested it with the X server.

The PS/2 path: MCP tool → QEMU HMP command → PS/2 event → 8042 controller → IOC2 interrupt → INT3 mappable interrupt → LOCAL0 cascade → CPU IP2 → pckm_intr() → shmiq → X server.

The keyboard worked immediately. We could type `root` at the login prompt, press Enter, and log in. Mouse input required relative motion coordinates and button state, delivered through a similar path.

One surprise: with `-icount shift=0,sleep=off`, the X server's auto-repeat timer fires thousands of times per virtual second. But that was a timing issue that ran deeper and required an entirely separate effort that impacted much of the system.

## The Desktop

After all of these fixes:

```
setenv DISPLAY :0
/usr/bin/X11/4Dwm &
/usr/bin/X11/xclock &
/usr/bin/X11/xterm &
```

The Indigo Magic Desktop. 4Dwm window manager with the distinctive SGI window decorations. xclock ticking (at wildly accelerated virtual-time speed, until the kernel real-time patch fixed that). xterm accepting commands. Mouse clicks selecting windows, dragging title bars, resizing.

## Reflections

### MAME Is a Guide, Not Gospel

MAME's Newport implementation was invaluable. Without it, I wouldn't have understood the DCB bus addressing, the XMAP9 mode entry format, or the cidaux bit layout. But MAME's interrupt model (read-to-clear VRINT) is wrong for IRIX on QEMU. It works in MAME because MAME's screen device naturally cycles VBLANK at real-time speed, and the software MAME runs doesn't depend on the exact interrupt timing model. IRIX's kernel driver does.

The lesson: use MAME for data formats and register layouts, but verify interrupt and timing behavior against the actual OS driver code.

### Compositing Is Fragile

Getting even one bit wrong in the display compositing logic (like testing bits [7:0] instead of [9:8]) turns the entire screen black. There's no graceful degradation. The XMAP9's mode entry system is powerful (it enables mixed-depth windows on the same screen, which was revolutionary in 1994) but unforgiving: every bit position, every field extraction, every conditional branch must be exactly right.

### The Diagonal Line Artifact Pattern

The rwpacked block fill bug produced a distinctive visual artifact: horizontal lines at regularly-spaced Y intervals, caused by the X server's background fill wrapping at the wrong rate. This pattern of regular geometric artifacts in an area that should be solid is almost always a coordinate tracking bug in the drawing engine, specifically a Y-advance that fires at the wrong time. Worth remembering for future graphics debugging.
