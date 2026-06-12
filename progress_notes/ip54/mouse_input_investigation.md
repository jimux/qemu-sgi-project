# IP54 mouse-motion input — investigation (task 21)

Date: 2026-06-12. The full Indigo Magic `.dt` desktop renders and is stable
(task 15 fixed), but the **mouse pointer doesn't move** when motion is injected
via `newport_mouse`, blocking interaction testing (clicking the Toolchest,
launching apps). This note records the end-to-end trace.

## Method

Boot the `.dt` desktop, serial root login (separate from the framebuffer X
session), instrument each layer of the input path, inject motion via
`newport_mouse` (QEMU monitor `mouse_move`), and observe where the data stops.

## What WORKS (verified, ruled out)

1. **QEMU monitor → PS/2 device**: `info mice` shows `* Mouse #2: QEMU PS/2
   Mouse` active; `mouse_move` reaches it.
2. **8042 / IOC2 (`sgi_ioc2_kbd.c`)**: instrumented — guest configures the mouse
   correctly (`WRITE_CTRL cmd_byte=0x03`: MOUSE_INT=1, port enabled;
   `WRITE_MOUSE_P 0xf4`: enable reporting) and **reads correct PS/2 packets**
   after injection. The 8042 path is complete.
3. **pckm driver (`io/pckm.c`)**: a pure STREAMS byte-shuttle —
   reads 3-byte mouse messages (`{KM_MOUSE, 3, 1}`) and `putnext()`s the raw
   bytes upstream. No decode here.
4. **Kernel delivery to the device node**: with X stopped, `dd if=/dev/input/mouse
   bs=1` captured **correct raw PS/2 packets** during injection:
   - motion `28 46 ce` = byte0=0x28, X=+70, Y=−50 (matches injected dx=70/dy=50;
     PS/2 inverts Y)
   - click `09 00 00` / `08 00 00` = left-button down / up
   So **the hardware→kernel mouse path is perfect** — correct motion AND button
   data reach `/dev/input/mouse`.

## Where it breaks

`/dev/input/mouse` → `/hw/input/mouse` is an `idev` char device (major 0, minor
4; keyboard is minor 3). X reads pointer events through the IRIX input framework:
**device → `idev`/`shmiq` (decode raw bytes → events, post to the shared-memory
input queue `/dev/shmiq`) → Xsgi**. The decode from raw PS/2 to pointer-motion
events happens in this `shmiq`/idevDesc layer, which X drives.

- `strconf`/`autopush` aren't installed on this image, so the pushed-module list
  couldn't be dumped live.
- **`shmiq.c` is NOT in the IRIX 6.5.5 source tree** (only `sys/shmiq.h`); the
  module ships as closed `shmiq.o` (`/usr/cpu/sysgen/IP22boot/shmiq.o`,
  `/var/sysgen/master.d/shmiq`). `ev_kbdms.c` is the EVEREST/Origin serial-DUART
  kbd/mouse driver — NOT our PS/2 pckm path.
- The X server (`/usr/bin/X11/X -bs -nobitscale -c -pseudomap 4sight -solidroot
  sgilightblue -cursorFG red -cursorBG white -gamma 1.7`) holds `/dev/input/mouse`
  open ("Resource busy") — it DID attach the device — but never moves the pointer:
  the VC2 hardware cursor is enabled (`DC_CONTROL=0x009f` ENA=1/DISP=1) yet parked
  at `CURSOR_X=0,CURSOR_Y=0` and X writes **no** cursor-position register on motion
  (instrumented VC2 DCB writes: only `CURSOR_ENTRY` 0x01 during setup; never 0x02
  /0x03/0x04). So X is **not receiving pointer-motion events** from shmiq.

## Key correlated clue

Keyboard input is **intermittent** through the same framework — the xlogin
`Login:` field stays empty on some runs (keystrokes lost) yet logins succeed on
others (~50–66%). Both kbd and mouse flow device→idev/shmiq→Xsgi. The IP54 `pckm`
runs on a **50 Hz poll-dispatch shim** (no real 8042 interrupt — interrupt wiring
was Phase 11/in-progress), so this looks like an **idev/shmiq event-delivery
reliability problem** (pointer events never delivered; keyboard events delivered
unreliably), not a mouse-specific PS/2-decode bug.

## Conclusion

The mouse-motion failure is **above pckm**, in the kernel `idev`/`shmiq` →
`Xsgi` event layer — largely **closed-source** IRIX components. QEMU, the 8042,
and pckm are all verified correct. M1's "mouse clicks change focus" was likely
buttons-only / bare-X; pointer *motion* on the full `.dt` desktop may never have
worked.

## Tractable next directions (harder, partly closed-source)

1. **shmiq attach/notify path**: determine how X is woken when shmiq has pointer
   events. If the wakeup is interrupt-driven and IP54 relies on the 50 Hz poll
   shim, X may never be notified of queued pointer events. Tie this to the
   Phase-11 interrupt-wiring work (real 8042 IRQ instead of polling).
2. **QIO/idevDesc**: trace whether X's `QIOADDDEV`/device-info ioctls on
   `/dev/input/mouse` succeed. `pckm_wput` *eats* M_IOCTLs ("only should get
   TCSETA, which we just eat") — if X needs an ioctl reply (e.g. device descriptor)
   that pckm silently drops, the pointer never attaches for event translation.
3. **Compare kbd vs mouse shmiq registration** with a kernel probe in the
   idev/shmiq attach path (needs the closed module or a kernel-side hook).
4. **Real interrupt delivery**: finish wiring the IOC2/8042 IRQ to the kernel so
   pckm is interrupt-driven (not 50 Hz polled); test whether reliable IRQs fix
   both the intermittent keyboard and dead pointer.

The cursor-visible screendump (commit `d09fc6411f`) is ready and correct — it
will show the pointer the moment X actually moves it.
