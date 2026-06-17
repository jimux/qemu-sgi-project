# IP54 mouse-cursor diagnosis — 2026-06-13/14 (on the booting Indigo Magic Desktop)

## ✅✅✅ FIXED + VERIFIED WORKING (2026-06-14) ✅✅✅
Rebuilt /unix.new (lboot; CC_pvfb_RC=0, LBRC=0), booted sgi-ip54 desktop: VC2 CURSOR_X/Y went
from always-0 → **centered (640,512)** at X start → **tracked injected mouse motion** to the
screen edge (1279,1023). Screendump (_mousefix.png) shows the red-X cursor sprite VISIBLE and
moved. THE MOUSE CURSOR WORKS. Golden disk promoted with the fix. Cursor offset is correct.

## ★★★ ROOT CAUSE FOUND + FIX IMPLEMENTED (2026-06-14) ★★★
**The cursor freeze is the OUTPUT path: `pvfb_gf_PositionCursor` was a NO-OP.** The kernel
INPUT chain is flawless (proven: mouseread.c streams correct valuator events on motion). But
the kernel never writes the pvrex3 VC2 cursor registers (VC2 CURSOR_X/Y always 0). The kernel
shmiq/gfx cursor path calls the gfx board's `gf_PositionCursor` to move the HW cursor, and
pvfb's was empty → cursor frozen.
FIX (ip54_tftp_staging/pvfb.c): `pvfb_gf_PositionCursor` now writes VC2 via the REX3 DCB:
`PVREX3_REG(0x238)=0x3` (DCBMODE: slave=VC2, reg0, dw3 combined), then
`PVREX3_REG(0x240)=(2<<24)|(x<<8)` (CURSOR_X) and `=(3<<24)|(y<<8)` (CURSOR_Y). A rate-limited
`cmn_err` confirms the call path. Verified: a 32-bit DCBDATA0 store triggers newport_dcb_write→
vc2_reg[reg]=val in QEMU. Rebuild via run_m1_kernel_rebuild.py (lboot), then desktop-test:
cmn_err fires + cursor tracks mouse ⇒ FIXED. Cursor X/Y offset may need tuning. If cmn_err
never fires, shmiq uses a different cursor mechanism (investigate the QIOCSETCPOS/setscr path).

In-guest C TOOLCHAIN unlocked: TFTP to 10.0.2.2, inject N32 crt1.o/crtn.o into /usr/lib32
(from irix-655-source/f/root/usr/lib32/), `cc -n32`. MIPSpro=C89; be/ld32 backend flaky (use
local arrays, valloc, avoid pointer-align math). Probes: ip54_tftp_staging/{mouseprobe,
mouseread,cpos,ringtest,memcursor}.c.

----


**Status:** Desktop fully renders (4Dwm toolchest), **keyboard works** (types into xdm
login), all 5 PV devices work. The mouse cursor is the sole remaining gap. This note is
the full, layer-by-layer validated localization.

## What works (each independently verified)
1. **QEMU input delivery — 100% correct.**
   - `trace:ps2_write_mouse`: guest sends `0xF6` (SET_DEFAULT), `0xE8 0x03` (SET_RES 3),
     **`0xF4` (ENABLE_DATA_REPORTING)** — the mouse IS enabled (final state ENABLED).
   - `trace:ps2_mouse_send_packet`: injected `mouse_move` produces correct PS/2 packets
     (`x127 y-127`, `x73 y-23` for +200/+150; buttons `0x9`/`0x8`).
   - `sgi_ioc2_kbd` sets `KBD_STAT_MOUSE_OBF`(0x20)+`OBF`(0x01) for mouse data; pckm reads
     `(SR_MSFULL|SR_OBF)` → routes to `MOUSE_PORT` (bits match exactly: pckm.h SR_OBF=0x01,
     SR_MSFULL=0x20). Keyboard working proves the 8042 base addr is right.
2. **pckm driver** (open source: irix-655-source/m/irix/kern/io/pckm.c):
   - registers `/hw/input/keyboard` (kmports[0]) and `/hw/input/mouse` (kmports[1]).
   - `pckm_open` on the mouse runs `CMD_ENABLE/CMD_MSRES/0x03` — exactly the F4/E8/03 seen.
   - buffers 3-byte packets (kmports[1]={KM_MOUSE,3,1}) and forwards raw to the stream.
   - `gl_setporthandler` only sets the KEYBOARD `km_porthandler` (ignores port arg);
     `km_porthandler` is the text-console hook, NOT the mouse decoder. Red herring.
3. **Cursor OUTPUT path is fine** (half-B ruled out): pvrex3 VC2 HW cursor ENABLED
   (DC_CONTROL=0x009f), bitmap loaded (CURSOR_ENTRY=0x0500). The no-op
   `pvfb_gf_PositionCursor` is NOT the bug (Xsgi moves the cursor via shmiq, not gf_).

## The break (validated by live gdb on the running desktop, golden syms)
- On mouse injection: `idev_rput` **HITS** (mouse data reaches the idev module) but
  `idevGenPtrEvent`/`idevGenValEvents`/`idevSetPtr` **MISS** → mouse data is never decoded
  into pointer/valuator events.
- `pckbd_rput` (tportpckbd graphics-console decoder) **MISSES on BOTH key and ptr** — it is
  not in the path at all. Keyboard still works → **keyboard decodes via a different path**
  (pckm built-in / Xsgi userland), NOT tportpckbd. The stale "push tportpckbd" recipe is
  therefore the wrong fix.
- Static disasm (golden /unix, ELF32 MSB MIPS): **`idev_rput` is a pure pass-through** —
  only calls `putnext`/`flushq`/`canput`/`freemsg`. It does NOT decode. So the decode that
  calls `idevGenPtrEvent` lives in a module ABOVE idev in the stream (or in Xsgi userland).
- Callers of `idevSetPtr`/`idevGenValEvents` are **static functions (no symbols)** — the
  decoder is anonymous; reached on the gfx-console path from pckbd_rput.

## In-guest topology
- `/hw/input/{keyboard,mouse}` both exist; `/dev/{mouse,keybd}` symlink to them.
- Xsgi: `/usr/bin/X11/Xsgi -bs -nobitscale -c -pseudomap 4sight -solidroot sgilightblue`.
- `/usr/lib/X11/input/{PC,SGI,compose,config}` — only keymaps; config/ has just a README.
- No `/var/X11/Xdevicetab`; no X server log files found.
- **`fuser /dev/mouse` returned EMPTY** — no process holds the mouse open (suggestive but
  fuser may not resolve hwgraph vertices; re-checking with /hw/input/* paths).

## ARCHITECTURE CORRECTION (2026-06-13, late) — it's shmiq-based, Model 2
Live gdb settled the model: **the keyboard does NOT generate kernel idev events**
(`idevGenBtnEvent`/`idevGenBtnEvents` MISS on keypress; `idev_rput`/key HITS as control).
So the kernel idev/tportpckbd path (idevGenPtrEvent etc.) is NOT how input reaches X —
chasing it was a dead end. The real path (from Xsgi binary strings/symbols):
- Xsgi opens **`/dev/shmiq`** (`shmiqInit`), then **`shmiqLink`s** each `/dev/input/*`
  device into the shmiq multiplexor (I_LINK). Strings: "Must open shmiq before linking
  devices", "Failed to open shmiq device", "Error Starting SHMIQ I/O".
- The kernel **shmiq** module receives linked-device data on its lower-read queue,
  decodes it per device type, and writes events into a shared-memory ring that Xsgi
  mmaps. Xsgi fns: `OpenInputDevice`, `shmiqLink`, `shmiqInit`, `sgiMapValuators`,
  `InitValuatorClassDeviceStruct`, `simpleSetValuators`.
- So the decode is in the **kernel shmiq module** (+ Xsgi's link/descriptor setup), NOT
  idev and NOT Xsgi-raw-read. Both are closed binaries (shmiq in /unix; Xsgi).
- Keyboard works → its device is linked + decoded by shmiq. Mouse fails → either NOT
  linked (Xsgi `shmiqLink` skipped/failed for the mouse) or shmiq's pointer-decode for
  the linked mouse isn't firing / is misconfigured (wrong device type/descriptor).

## NEXT (correct targets)
- Probe the kernel **shmiq lower-read-put** (not idevGenPtrEvent) on key vs ptr to see if
  mouse data reaches shmiq's decode. (streamtab walk TBD — shmiqinfo struct layout.)
- Decompile Xsgi **`OpenInputDevice`** + **`shmiqLink`** (have xsgi.bin + xsgi_symbols.json
  + callgraph + working Ghidra) to see how the mouse is opened/linked and where it diverges
  from the keyboard (device type, valuator class, descriptor).
- par/truss: truss ABSENT on this IRIX; try `par` to watch Xsgi link/read the mouse.

## Current leading hypothesis
Mouse data reaches the kernel mouse-idev stream but the **pointer-decode stage that emits
idevGenPtrEvent is absent from the mouse stream** (while the keyboard's decode runs). Either
(a) Xsgi opens the mouse, fails an init ioctl (IDEVINITDEVICE/IDEVSETPTRMODE), and stops
reading it, or (b) the mouse stream is missing the decoder module/shmiq link that the
keyboard has. The decode + Xsgi config are CLOSED binaries (idev, shmiq, Xsgi).

## Xsgi RE — the input device open/link path (decompiled 2026-06-13)
Decompiled with `_ghidra_decomp_gp.sh 0x105498ec "<fns>" out.json` (ET_EXEC, $gp set).
Flow (from `InitInput` → `AddOtherInputDevices`):
- **`AddOtherInputDevices`** = `sgiCheckDevices()` then `sgiIterateDevices(simpleCreateDevice)`.
- **`sgiCheckDevices`**: iterates a device table (`DAT_105620a4`, count `DAT_105620a0`);
  for each: `stat(path)`, check char-special, `open(path,0xc06)`, **`ioctl(fd, I_PUSH(0x5302),
  device[1])`** (push module by name to probe). If the push fails → device DISCARDED
  (sgiCloseDevice + free the table entry). So a device whose module can't be pushed is dropped.
- **`sgiOpenDevice(dev)`**: open via helper(0x101004f8); if `dev[8]` (a NAME) set →
  `I_STR(0xc0286920 = _IOWR('i',32) , nameopt+name)` (named init); `ddxFindDeviceOptions(dev[4],
  "device_init", …)`; **`I_STR(IDEVINITDEVICE 0x80046933 = _IOW('i',51,uint))`** (ic_len=0!);
  then **`shmiqLink(fd, dev, &LAB_10059330, dev+0x10)`**; close.
- **`shmiqLink`**: `ioctl(shmiqfds, I_LINK(0x530c), devfd)` → link idx; if <0 → "I_LINK ioctl
  failed, device not added". Then `I_STR(QIOCGETINDX = _IOWR('Q',8,4))` to read the stream
  index; if !=0 → "QIOCGETINDX ioctl failed, device not added". If idx ≥ 0x20 → unlink +
  "stream index too high". Else store linkInfo, close devfd, return idx.
- **`ddxFindDeviceOptions`**: opens `ddxDeviceConfigDir/<devname>` and scans `{…}` option
  blocks. The config dir's only file is an EMPTY `mouse.ptrmap` (same on the reference eoe),
  so device options are effectively absent — not the driver.
- **`simpleCreateDevice`**: builds the X device (valuator/button/keyboard class). Type
  (pointer vs keyboard) decides valuator setup; dispatched via a control proc (LAB_100e6f88).

Device-config files: `/usr/lib/X11/input/{PC,SGI,compose,config}` are keymaps; `config/` only
has README + empty mouse.ptrmap (reference eoe identical) → NOT the fix.

Empirical (run_a_xsgilog2.py): a freshly-launched Xsgi wrote NO link/enumeration warnings to
stderr — *suggesting the mouse links OK and the gap is downstream* (shmiq pointer-decode of the
linked mouse, or simpleCreateDevice typing) — but the manual-Xsgi capture was unreliable
(csh redirect); re-verifying foreground (run_a_xsgifg.py).

## *** BREAKTHROUGH: the precise failure point (InitInput, decompiled 2026-06-13) ***
`InitInput` (0x10101898) does, in order: `shmiqInit()`, `InitCorePointer()`, `sgiIEInit()`,
`sgiCheckDevices()`, `sgiIterateDevices(simpleCreateDevice)`, then:
```
ptr = xdevLookupDevice(corePtrName);          // corePtrName = "mouse"
if (ptr == 0)  ErrorF("WARNING: Couldn't find core pointer ...");   // NON-FATAL
else { xdevAddDeviceToCorePtr(ptr); coreSetCursorPosition(screen, w/2, h/2, 0); }
kbd = xdevLookupDevice(coreKbdName);          // coreKbdName = "keyboard"
if (kbd == 0)  FatalError("Couldn't find core keyboard device");    // FATAL
else { xdevSetKeyboardDevice(kbd); RegisterKeyboardDevice(kbd); EnableDevice(kbd); }
```
**This is the smoking gun.** The core-pointer lookup is a NON-FATAL warning while the
core-keyboard lookup is FATAL. Symptoms match exactly: keyboard works (else X would
FatalError and die), mouse dead, X stays up. So **Xsgi is failing `xdevLookupDevice("mouse")`**
→ the core pointer is never bound to a physical device → `coreSetCursorPosition`/cursor
centering never runs → cursor frozen and motion ignored.
Values confirmed from the binary: `corePtrName`@0x10547e58 → "mouse"; `coreKbdName` → "keyboard".

The physical input devices are created by `simpleCreateDevice` (called via
`sgiIterateDevices` over the devices that survived `sgiCheckDevices`' I_PUSH probe). A device
is looked up by its name (`dev+0x1c`). So the bug is one of:
1. The "mouse" device is DISCARDED by sgiCheckDevices (I_PUSH probe of dev[1] module fails on
   the mouse) → never created → lookup fails. (BUT idev_rput fires on ptr injection, implying
   idev IS on the mouse stream — needs reconciling; the probe push is a separate transient fd.)
2. The "mouse" device is created with a NAME != "mouse" (name mismatch) → lookup fails.
3. The device is created but as a non-pointer (no valuators) so it's not a valid core pointer.
Confirm which via `xsetpointer -l` against the running Xsgi (run_a_listptr.py): if "mouse" is
absent from the device list, case 1/2; if present but no valuators, case 3.

Likely fixes once confirmed:
- If the mouse device isn't created/named "mouse": fix the device-table population (the
  /dev/input enumerator that sets dev name/module) or provide the missing input config so the
  mouse enumerates with name="mouse" + the right STREAMS module + pointer type.
- The named device-init in sgiOpenDevice (`I_STR(_IOWR('i',32), nameopt+dev[8])`) sets the
  device type by NAME; if dev[8] (name) is unset for the mouse, IDEVINITDEVICE (ic_len=0)
  leaves it untyped — so ensuring the mouse entry has the proper name is likely central.

## *** TOOLCHAIN UNLOCKED + KERNEL MOUSE DEVICE CONFIRMED PERFECT (2026-06-13) ***
Got an in-guest C toolchain working: TFTP-deliver source (10.0.2.2 = ip54_tftp_staging),
inject the missing **N32 crt** (`/usr/lib32/crt1.o` + `crtn.o` from irix-655-source/f/root/
usr/lib32/ — the kernel-build disk lacked the lib32 crt; libc.so/libc.so.1 ARE present),
`cc -n32`. MIPSpro is C89 (decls before statements). Now arbitrary in-guest probes work.

`mouseprobe.c` (open /dev/input/<dev>, I_PUSH(<dev>), IDEVGETDEVICEDESC), xdm stopped:
```
/dev/input/keyboard: I_PUSH("keyboard")=0  GETDESC=0 devType='KEYBOARD' nBtn=240 nVal=0 flags=5
/dev/input/mouse:    I_PUSH("mouse")=0     GETDESC=0 devType='MOUSE'    nBtn=3   nVal=2 flags=0
```
**The kernel mouse device is PERFECT**: the "mouse" STREAMS module exists + pushes (I_PUSH=0),
and the descriptor is a correct pointer (devType=MOUSE, 3 buttons, **2 valuators**). So the
device-creation branch (C1) is DISPROVEN — Xsgi's sgiCheckDevices keeps it, simpleCreateDevice
makes it a valuator/pointer device, and xdevLookupDevice("mouse") will succeed. NOT the bug.
=> The bug is **event delivery** (C2): mouse MOTION must not be producing idev events. Next
probe `mouseread.c`: enable buttons+valuators, read the device while injecting motion.

## *** KERNEL MOUSE PATH PROVEN FLAWLESS — bug is 100% Xsgi/shmiq (2026-06-13) ***
`mouseread.c` (open mouse, I_PUSH("mouse"), IDEVINITDEVICE, IDEVENABLEBUTTONS/VALUATORS,
non-blocking read loop) WHILE injecting motion (xdm stopped): the device streams 12-byte
idev valuator events whose values EXACTLY match the injected deltas (dx=40→0x28, dy=30→0x1e),
accumulating with continued motion. INIT=0, ENBTN=0, ENVAL=0 all succeed. So the FULL kernel
chain 8042→pckm→"mouse" idev module→valuator events WORKS. The cursor freeze is **entirely in
the Xsgi→shmiq userland path** (I_LINK device under /dev/shmiq → kernel shmiq writes the ring →
Xsgi reads ring → DIX moves cursor). Next: replicate shmiqInit + I_LINK(mouse) + mmap ring in a
probe to find where pointer events fail to reach the ring (or where Xsgi mishandles them).

## Where it stands after the full Xsgi RE (honest state, 2026-06-13)
Two candidates remain, NOT yet disambiguated:
- **(C1) The "mouse" X input device is never created** → `xdevLookupDevice("mouse")` fails
  (non-fatal) → core pointer unbacked. Would require `sgiCheckDevices`' `I_PUSH("mouse")`
  probe or naming to fail. BUT: both "keyboard" and "mouse" appear symmetrically in a kernel
  table (slots 0x882965f4 / 0x882966e4 in /unix.new), so a missing "mouse" STREAMS module is
  not obviously the cause; and a freshly-run foreground Xsgi printed NO "Couldn't find core
  pointer" warning (though that capture was unreliable — X server is silent on the console).
- **(C2) The "mouse" device IS created + linked to shmiq, but pointer events never flow**
  (kernel shmiq doesn't decode the linked mouse stream into ring events, or the device is
  typed without valuators). Then the cursor is frozen with no warning.

Disambiguation needs ONE of (all heavier, next session):
- A working in-guest C toolchain to: open /dev/input/mouse, `I_PUSH("mouse")` (check errno),
  `IDEVGETDEVICEDESC` (check nValuators), and read events. ip54-test lacks crt1.o (cc -S only);
  may need to build crt on the disk or use irix655-dev's full toolchain.
- Reliable Xsgi-stderr capture (X server logs nothing to console; xdm-errors empty) — perhaps
  set `DisplayManager._0.errorLogFile` in xdm-config, or wrap Xsgi.
- Kernel gdb on shmiq: check shmiq link count after X start (1 = kbd only → mouse not linked;
  2 = both linked → C2) and whether mouse data reaches shmiq's lower-read-put.
- Userland gdb on Xsgi at `sgiCheckDevices`/`xdevLookupDevice` (hard per prior notes).

Note: `/var/X11/xdm/DefaultDeviceList` (1450 b) exists — worth reading next (may list input
devices / pointer config). No `xinput`/`xsetpointer` installed (only xset/xsetmon/xsetroot/
xdpyinfo/listres in /usr/bin/X11); xdpyinfo connects fine as root on DISPLAY=:0 (no auth needed).

## Candidate fixes (by tractability)
1. **Find the failing Xsgi mouse-init step** (trace ioctls on the mouse fd; gdb/xtruss on
   Xsgi) → fix the config/descriptor. Most surgical if it's a config gap.
2. **Kernel shim in pckm.c** (ours to patch): on a complete MOUSE_PORT 3-byte packet,
   directly enqueue a pointer event into shmiq (needs the shmiq enqueue API + event format,
   partially RE'd in B2). Bypasses the broken decode entirely. Most robust if config can't
   be fixed, but substantial.
3. RE the static idev pointer-decoder + why it's not pushed on the mouse stream.

## Tools written this session (reusable)
- `run_mouse_diag.py` / `run_mouse_diag2.py` — VC2 cursor-state probe (newport_inspect).
- `run_mouse_enable_trace.py` / `run_mouse_packet_trace.py` — ps2 enable/packet traces.
- `run_a_mouseAB.py` / `run_a_mousedecode.py` — live gdb idev/pckbd breakpoint battery.
- `run_a_mouseinspect2.py` / `run_a_xinputlog.py` / `run_a_mouseopen.py` — in-guest topology
  (csh-safe: use `|&`, `set VAR=`, NO Bourne `2>&1`).
