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

## 2026-06-12 update — live gdb localization (NEW)

Using the Gen-2 debug toolkit (live gdb breakpoints on the running xdm greeter,
injecting input via `newport_mouse`/`newport_sendkey`), the break is now
localized **inside the kernel idev layer**, not below it.

**FIRST: a symbol-drift trap that invalidated earlier work.** The default symbol
JSON (`ip54_kernel_symbols_disk.json`) is **99% stale** vs the golden `/unix` —
every address wrong (splx 0x88011580→0x88010580, idevGenPtrEvent
0x88038530→0x880692a8, idev_rput 0x88039c54→0x8806a9cc). gdb breakpoints by name
therefore hit the WRONG functions. Regenerated correct symbols from golden /unix
via `kernel_syms.py gen` → `ip54_kernel_symbols_golden.json`; `guest_gdb.py` now
defaults to it. Use `kernel_syms.py drift` to check. (See memory
kernel_symbol_drift.)

**With CORRECT symbols, on the xdm greeter (X running, mouse+kbd open):**
- `idev_rput` (0x8806a9cc) **HITS on both keyboard and mouse injection** — input
  data DOES reach the idev STREAMS layer. (This is a strong on-demand control:
  breakpoints fire and data flows.)
- `idevGenPtrEvent` (0x880692a8) **does NOT fire on mouse motion** — idev receives
  the mouse bytes but never generates a pointer-motion event.

So the dead pointer is **idev failing to turn mouse data into pointer events**,
NOT a missing module / dead device path (idev_rput proves data arrives). The raw
bytes reach idev; the decode-to-ptr-event step doesn't happen. Likely causes:
the mouse's idev device descriptor has valuators disabled / ptr-mode unset, or the
PS/2-packet decode in idev_rput doesn't recognise this device. (idev internals are
closed — idevGen* are global but the decode/descriptor logic is not in source.)

**ROOT CAUSE (localized 2026-06-12): the pckbd decode module is NOT in the input
STREAMS stack.** Walked the call chain statically (kdisasm.py / kxref.py on golden
/unix) from the dead end up to the decoder:

```
idevGenPtrEvent ← idevSetPtr ← (static decoder) ← pckbd_rput      [pointer motion]
idevGenPtrEvent ← idevGenValEvents ← (static decoder) ← pckbd_rput [valuators]
                                       idevSetPtrMode ← pckbd_rput
```

`pckbd_rput` (golden 0x88268bd0) is the PS/2 input decoder that turns raw packets
into idev events. Live probe result (idev_rput as proven control):
- `idev_rput` HITS on key+mouse (data reaches the idev pass-through module).
- `idevGenPtrEvent`/`idevGenBtnEvent` MISS (no events generated).
- **`pckbd_rput` MISSES on BOTH key and mouse** — the decoder is never invoked.

So the active stream is `pckm (raw 8042) → idev (idev_rput pass-through) → X`, with
**pckbd missing**. Raw bytes flow up to X but are never decoded into pointer/button
events → dead cursor, unreliable keyboard. The kernel HAS the `autopush` table
(global 0x882c7bc8, 1408 bytes) but the input-device majors evidently don't have
pckbd configured (the note above: "strconf/autopush aren't installed").

**This reframes the bug from closed shmiq/Xsgi to a kernel STREAMS-config issue.**
Fix direction (next): get pckbd pushed onto /dev/input/keyboard + /dev/input/mouse
— via the autopush table (read it to confirm what's configured for those majors),
`strconf`/`autopush(1M)` in the guest, or wherever the idev device descriptor
selects the decode module. Then re-probe pckbd_rput / idevGenPtrEvent to confirm
events flow.

Drivers: `run_a_idevtrace3.py` (live gdb probe battery); `kdisasm.py` / `kxref.py`
(static disasm + caller xref by symbol); `kernel_syms.py` (correct symbols — the
disk JSON was 99% stale and silently poisoned earlier probes).

### DEFINITIVE root cause (boot-trace, 2026-06-13)

`pckbd_rput` is registered in `ng1_htp_fncs` (Newport board's keyboard fncs table)
and installed via the graphics-keyboard init chain: `gfx_earlyinit`/`ng1_earlyinit`
→ `htp_register_board`; `tp_init` → `keyboard_init`. Boot-trace (`run_a_gfxkbdtrace.py`:
-gdb -S, hbreak each with auto-continue, `pckminit` as a MUST-fire control):

```
boot reached LOGIN;  pckminit(control)=HIT
gfx_earlyinit, ng1_earlyinit, ng1_init, tp_init, keyboard_init,
htp_register_board, initkbdtype = ALL never fired
```

**The entire graphics-console keyboard init subsystem is skipped on IP54** (it's
tied to the real gfx/newport board device-init, which IP54 doesn't probe — pvfb/
pvrex3 replaces newport, PROM removes ng1_init). So the keyboard board is never
registered and `tportpckbd`/`pckbd_rput` is never installed → PS/2 bytes flow
through `idev_rput` un-decoded → no events → dead cursor. pckm (low-level) inits
independently via `pckminit` (device switch), which is why raw data still reaches
`/dev/input/mouse`.

**Fix (next):** make the gfx-keyboard registration run on IP54 — a PROM io_init[]
append or pvfb/pvrex3 hook that calls `htp_register_board` (+ the keyboard stream
setup) for the PS/2 path. CAUTION: `tp_init`/`keyboard_init`/`ng1_init` touch DUART
(`du_keyboard_port`)/GIO hardware that faults on IP54 — stub the HW-probe parts and
wire only pckm→tportpckbd→idev. Re-probe `pckbd_rput`→`idevGenPtrEvent` to confirm.

### 2026-06-16 update — decode modules are in fmodsw (macOS-native session)

Static analysis of golden `/unix` (kdisasm.py/kxref.py + a pointer scan) refined the fix:

- `pckbd_rput` (0x88268bd0) / `pckbd_wput` (0x88268b70) are the rput/wput of a complete STREAMS
  module: streamtab @0x882bebb8 (rd qinit 0x882beb78, wr qinit 0x882beb98), module_info @0x882beb58
  with **name "keyboard"** (string @0x8829b108). A sibling **"mouse"** module sits right after.
- The pckbd streamtab is referenced at **`fmodsw+0xc0` (0x882a3d30)** — i.e. the decode modules
  are **built into the kernel's STREAMS module switch and are pushable** (not gfx-board-private).
- `keyboard_init` (0x8825efe0) only probes existence (`du_init` [faults on IP54], `early_pckminit`,
  `pckm_kbdexists`); `initkbdtype` (0x8825f02c) only detects keyboard type. Neither wires the stream.
- So the decode is missing purely because the **"keyboard"/"mouse" modules are never PUSHED onto
  `/dev/input/{keyboard,mouse}`** on IP54 (the gfx/idev init that arranges the push is skipped).

`/sbin/autopush` IS present on the golden. Candidate **no-kernel-rebuild fix**: `autopush` the
decode modules onto the idev input major (mouse minor 4, keyboard minor 3), then X re-opens the
devices and the modules auto-push. Experiment harness: `mac_autopush_test.py`. RISK: idev may
manage its own stream and not honour SAD autopush — if so, fall back to a kernel idev-registration
hook (call the device-register-with-decode-module path from a pvfb/PROM IP54 hook).

### 2026-06-16 (cont) — autopush ruled out; decode wiring is idev-internal + HW-entangled

- **autopush is NOT viable.** `/sbin/autopush` is present, but `/hw/input/{keyboard,mouse}` are
  hwgraph devices created by `pckm.c` via `hwgraph_char_device_add` (`ls -lL` shows **major 0**,
  mouse minor 4 / kbd minor 3). `autopush -f` fails: **"Major device is not a STREAMS driver"** —
  these aren't classic-cdevsw STREAMS devices, so SAD autopush can't push modules onto them. idev
  manages its own stream stack; the decode-module selection is internal to idev's device descriptor.
- **`htp_register_board` (0x88067c9c) is pure bookkeeping** — stores info/fncs/width/height into
  globals (data @0x882d45c8 + gp slots), no HW access, no decode wiring. Safe to call but
  insufficient by itself.
- **`ng1_earlyinit` (0x88262130)** is the chain that actually wires the textport keyboard, but it
  hardcodes Indy/Indigo2 8042 addresses (`0x1fbd9880`/`0x1fbd9000`, stored to gp -0x2bf8/-0x2bf4)
  and does IOC1/fullhouse HW probing — entangled with real-board addresses, not reusable as-is for
  IP54's ioc2-kbd 8042 @ 0x1FBD9840.

**Net:** the fix is a focused kernel sub-project — RE exactly how the textport-keyboard path
installs the pckbd decode into the idev stream (idev internals are closed), then replicate ONLY
that wiring for the IP54 8042 + pvfb (pointing at 0x1FBD9840, no Indy HW probe), then lboot-rebuild
`/unix.new`. The QEMU side (8042 + ps2 mouse) is fully correct and instrumented (SGI_KBD_DEBUG).

### 2026-06-16 (cont) — kernel-fix plan (campaign committed)

Implementable primitives confirmed in golden /unix:
- `qattach` (0x880bf17c, GLOBAL) — the kernel STREAMS module-push primitive (what I_PUSH uses).
- idev event API all GLOBAL (idevGenPtrEvent/idevSetPtr/idevGenBtnEvent/idevGenValEvents...).
- decode modules "keyboard"/"mouse" (pckbd_rput 0x88268bd0 / pckbd_wput 0x88268b70) in fmodsw+0xc0.
- `ap_hadd`/the autopush[] table are sad-internal STATIC — NOT callable to register autopush from
  a driver; and SAD ioctl autopush can't target the hwgraph (major-0) input device anyway.

Ranked fix candidates (each needs an lboot `/unix.new` rebuild on irix655-dev):
1. **pckm_wput M_IOCTL NAK (lowest risk, try first).** Stock `pckm_wput` does `case M_IOCTL:
   freemsg(bp); break;` — silently eats EVERY ioctl with no reply (violates STREAMS). If X's
   pointer-attach / device-descriptor ioctl reaches pckm and is dropped with no M_IOCACK/NAK, X's
   pointer never attaches. Fix: reply `M_IOCNAK` (or handle) for unrecognized ioctls. Small, safe.
   (pckm.c is stock — compile a patched pckm.o into the IP54 kernel.)
2. **qattach-push the decode module.** In pckm_open (mouse port), `qattach` the "mouse" decode
   module onto the stream so raw PS/2 is decoded → idevGenPtrEvent. Topology-sensitive (need to
   confirm pckbd's q_ptr/idev-device expectation); validate with live gdb on idev first.
3. **In-driver decode.** Have pckm decode the 3-byte packet and call idevGen* directly — needs the
   idev device handle (only available above pckm in the stream); least clean.

NEXT: (a) get live stream-topology ground truth (adapt guest_gdb.py off /workspace paths + a
gdb-multiarch on macOS, OR run in Docker) — walk the /dev/input/mouse module stack + break on
qattach/idev_wput during X start to see what's pushed and where the ioctl dies; (b) build+test
fix #1 via lboot. QEMU side is done (SGI_KBD_DEBUG trace in sgi_ioc2_kbd.c).

### 2026-06-16 (cont) — ROOT CAUSE refined via golden disasm (decode handler at idev_device+0xc)

Deeper static RE of golden /unix nailed the mechanism:
- `pckbd_rput`/`pckbd_wput` are THIN SHIMS over idev: pckbd_rput forwards to `idev_rput` (handles
  only the 0xde kbd-id M_CTL); pckbd_wput intercepts the SHMQ ioctl then calls `idev_wput`.
- `idev_wput` (0x8806ae24) ALREADY handles the SHMQ (0x53484d51) registration ioctl itself —
  stores the 2 params into the idev device (q_ptr+8/+0xa), acks "QMHS". So SHMQ plumbing is intact
  on IP54 (idev IS in the stream — idev_rput AND idev_wput are reachable).
- `idev_rput` (0x8806a9cc) M_DATA path calls a PER-DEVICE DECODE HANDLER: `t1 = *(q_ptr+0xc);
  jalr t1` with (device, b_rptr, len). THIS is what emits idevGenPtrEvent. If q_ptr+0xc is unset/
  generic, raw bytes are received but never decoded → dead pointer. This is IP54's exact symptom.
- `pckbd` qopen (0x882689a8) does `kmem_alloc(0xf0)` + bzero + init — it CREATES & sets up the
  idev pointer device (incl. the decode handler). So pushing the pckbd module ("keyboard"/"mouse"
  in fmodsw) yields a working pointer device; the plain "idev" module pushed on IP54 does not set
  up the pointer decode.

REFINED FIX: get the pckbd "mouse"/"keyboard" module pushed onto /dev/input/* instead of (or
below) the plain "idev" module. Open question (who selects idev vs pckbd at push time — X via
device descriptor, or kernel) still needs ground truth. Cleanest implementable: make `pckm` (the
driver, ours to patch) install the pckbd-style pointer-device decode on its input streams (port
pckbd qopen's device setup into pckm_open, or qattach the "mouse"/"keyboard" module there), then
lboot-rebuild /unix.new.

GDB note: tracing idev_wput live — pointers READ from guest mem are 32-bit (0x88xxxxxx); must
sign-extend (| 0xffffffff00000000) AND sign-extend $a1 before deref, else gdb hits unmapped
xkphys. Trace harness: run_a_ioctltrace.py (in container; idev_wput @ 0x8806ae24). Still
debugging the bp-command expression (b_datap reads 0 → revisit $a1 capture at fn entry).

### 2026-06-16 (cont) — LIVE TRACE: X sends NO config ioctls (device must be pre-configured)

Live gdb trace of idev_wput (0x8806ae40, after db_type->$v0) through X greeter startup
(run_a_ioctltrace.py, in container): 78 hits, ALL M_DATA (type 0) + some high-pri; ZERO M_IOCTL
(0x83), ZERO SHMQ. So X does NOT configure the pointer via ioctls on this stream — it only writes
M_DATA (LED/mouse-reinit bytes that pass through idev_wput->pckm_wput). 

=> The pointer VALUATOR/decode descriptor must be PRE-SET at device-creation by the pushed module.
pckbd's qopen (kmem_alloc 0xf0 + init) sets it up; the plain idev-style module (streamtab near
`keyboardinfo`, rput=idev_rput) pushed on IP54 does NOT register valuators. So IP54's input device
advertises no pointer capability -> X never enables pointer mode -> idev_rput's per-device decode
handler (device+0xc) stays generic -> motion received, no idevGenPtrEvent. FULL CHAIN EXPLAINED.

FIX (next, needs lboot): pre-register the input device as an idev POINTER with valuators. Cleanest
in-our-code options for pckm.c: (a) in pckm_open, qattach the pckbd "mouse"/"keyboard" module so
its qopen builds the valuator device; (b) call the idev valuator-registration (idevSetValDesc etc.,
all GLOBAL) for the device. Needs 1-3 lboot iterations to get the exact sequence right; best run
with fresh context. Reference working-board setup if possible (irix655-dev/Indy X) to copy the
exact module/valuator registration. Base to patch: irix-655-source/m/irix/kern/io/pckm.c (817 lines,
f==m identical). Build per run_m1_kernel_rebuild.py (compile on indy, install /var/sysgen/boot, lboot).

### 2026-06-16 (cont) — FULL input-subsystem map (two idev modules) + where to look next

Two idev STREAMS modules fully mapped in golden /unix:
- Module A "keyboard" (pckbd): streamtab 0x882bebb8; rput=pckbd_rput (wraps idev_rput + 0xde kbd-id
  M_CTL); wput=pckbd_wput (SHMQ + idev_wput); qopen 0x882689a8 = kmem_alloc(0xf0)+bzero, sets
  device+0xc=0x882682fc (DECODE handler), device+0x10=0x88268674, q_ptr=device, sends 0xde kbd-id
  query upstream. (KEYBOARD device.)
- Module B (idev-based): qinit 0x882bec68 rput=idev_rput, qopen=0x882691b4, qclose=0x882693f8;
  wput qinit 0x882bec88 wput=idev_wput. qopen 0x882691b4 = kmem_alloc(0xd8)+bzero, sets up 2
  VALUATORS from a template (data @0x882c-0x1438 / 0x882b+0xa88), zeroes ptr state (0x3c-0x46).
  This IS a pointer/mouse device setup. Module B is what's pushed on IP54 (idev_rput hits,
  pckbd_rput misses).

So Module B *does* build a 2-valuator pointer device — yet idevGenPtrEvent still never fires on
IP54. The remaining gap is subtle (decode handler device+0xc not finished, or ptr-mode/enable not
set, or the wrong template). RESOLVING IT NEEDS LIVE DEBUG (next session, fresh context):
  1. Boot ip54-test greeter in Docker w/ -gdb. Break at Module B qopen 0x882691b4 when X opens
     /dev/input/mouse; dump the returned device (0xd8) — check device+0xc (decode handler set?),
     the valuator array, ptr-enable flags.
  2. Break at idev_rput 0x8806aa90 (M_DATA decode-handler call) on mouse motion; read device+0xc
     and whether jalr target decodes. (Use sign-extended ptr reads: addr | 0xffffffff00000000.)
  3. Compare against a WORKING reference: boot irix655-dev (Indy, machine=indy, newport gfx) to X
     and trace the SAME on its working mouse — diff the device setup. That diff IS the fix.
  4. Implement in pckm.c (irix-655-source/m/.../io/pckm.c) or the idev path; lboot-rebuild
     /unix.new (run_m1_kernel_rebuild.py pattern); re-test.
Decode handlers 0x882682fc/0x88268674 live in the closed newportFrameInfo region (gfx-input code).
