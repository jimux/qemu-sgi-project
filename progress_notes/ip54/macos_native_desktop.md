# Running the IP54 IRIX desktop natively on macOS (no Docker)

Goal: power-on → login → full Indigo Magic Desktop (speckled-grey IRIX root) → Atlantis,
running as an interactive Cocoa QEMU window **directly on macOS** (Apple-silicon), with the
DGL→host-GPU renderer also native. All prior IP54 desktop work ran headless inside the Linux
Docker container; this note covers the move to a native macOS run.

## What works

- **`build-mac` QEMU + slirp** — `qemu-sgi-repo/build-mac/qemu-system-mips64-unsigned`
  (Mach-O arm64, Cocoa + CoreAudio + slirp). Boots `sgi-ip54` to multi-user from
  `vm_instances/ip54-test/disk.qcow2` over an MTD drive, slirp NIC (`tftp` + telnet hostfwd).
- **Native launcher** — `run_mac_desktop.py` (uses `pyirix_qemu` directly; no MCP, no Docker).
  Fixed serial/monitor unix sockets so a helper can drive the same running guest. Post-login
  steps are best-effort so a successful boot always leaves the Cocoa window up.
- **SGI visual login (clogin) renders.** With `/etc/config/visuallogin=on`, `/var/X11/xdm/Xlogin`
  execs `/usr/Cadmin/bin/clogin` — the styled IRIS greeter with user icons (root / EZsetup /
  demos / guest), "Login name:" field, IRIS logo, Log In / Help. Renders bright & correct:

  ![SGI clogin visual login](screenshots/macos_clogin_visual_login.png)

- **Full `.dt` desktop config applied** to a separate golden (`disk.qcow2.golden.desktop`):
  `visuallogin=on`, `desktop=on`, and `Xsession.dt` shebang `#!/bin/bsh`→`#!/bin/sh` (the bsh
  arena-fault SIGSEGV trap). XFS verified clean after the live edits.
- **Classic 4Dwm desktop reached by keyboard login** on the pristine golden (`visuallogin=off`
  → plain xlogin; `root`+Enter+Enter → 4Dwm Toolchest + Console). Confirms the
  boot→login→desktop path end-to-end on native macOS:

  ![4Dwm desktop (dim — colormap issue)](screenshots/macos_4dwm_desktop_dim.png)

## Greeter / session architecture (from the on-disk scripts)

- X starts 8bpp PseudoColor: `/usr/bin/X11/X -bs -nobitscale -c -pseudomap 4sight
  -solidroot sgilightblue -cursorFG red -cursorBG white -gamma 1.7` (`Xservers`).
- `xdm-config`: `loginProgram=/var/X11/xdm/Xlogin`, `session=/var/X11/xdm/Xsession`.
- `Xlogin`: `if chkconfig visuallogin && [ -x /usr/Cadmin/bin/clogin ]; then exec clogin -f $1; fi`
  — else falls through to xdm's built-in plain "X Window System" greeter.
- `Xsession`: `if chkconfig desktop && [ -x $0.dt ... ]; then exec $0.dt; fi` — `desktop=on`
  + executable `Xsession.dt` → the full Indigo Magic Desktop session.

## Hard lessons (write these down)

1. **NEVER offline-`fs_inject` into this XFS.** Editing `/etc/config/*` + `Xsession.dt` via the
   qcow2→raw→`fs_inject`→qcow2 round-trip allocated new extents that left the AG free-space
   B+trees inconsistent → **`fsck` PANIC: Fatal error on root filesystem** on next boot. The
   surface `xfs_check` (superblock + root dir + named paths) still PASSED — it doesn't validate
   the free-space/inode btrees. **Always apply config changes LIVE in-guest** (the real IRIX XFS
   driver keeps everything consistent). Revert from `disk.qcow2.golden.preDesktop` if corrupted.
2. **The root *console* login shell is csh, and `exec /bin/sh` does NOT switch it** over the
   serial console (telnet's `login` execs `/bin/sh`, which is why telnet could use Bourne syntax).
   Drive serial config with **csh-native** commands:
   - No `2>&1` (csh: "Ambiguous output redirect" — aborts the command). Use `>&` if needed.
   - No `!` anywhere (csh history-expands it even in single quotes — "Unmatched '" / "Variable
     syntax"). To fix a `#!/bin/bsh` shebang, `sed '1s/bsh/sh/'` — match only `bsh`, never type `!`.
   - No `$?`. Use a trailing `echo MARKER` to detect completion.
3. **`chkconfig` writes persist** across a clean halt (`init 0` + monitor `quit` flushes the
   writeback qcow2); they were lost earlier only because the `2>&1` made csh skip the command.

## Open blockers (native path)

- **Mouse input is dead (the key usability blocker) — root cause known.** Monitor `mouse_move`
  (and Cocoa mouse) produce **zero** new `pvfb PositionCursor()` events — at the greeter *and* on a
  fully logged-in 4Dwm desktop. This is the documented **task-21** issue
  (`mouse_input_investigation.md`): QEMU, the 8042 (`sgi_ioc2_kbd.c`), and `pckm` are all verified
  correct — `dd if=/dev/input/mouse` captures correct raw PS/2 packets, and live gdb shows
  `idev_rput` HITS but `pckbd_rput`/`idevGenPtrEvent` MISS. Root cause: the PS/2 **decoder**
  `pckbd_rput` (raw packets → idev pointer/button events) is installed only via the graphics-console
  keyboard init chain (`gfx_earlyinit`/`ng1_earlyinit`→`htp_register_board`; `tp_init`→
  `keyboard_init`), which IP54 **skips** (pvfb/pvrex3 replaces Newport; PROM removes `ng1_init`).
  So raw bytes flow `pckm → idev_rput → X` un-decoded → dead cursor + intermittent keyboard.
  My contribution this round: env-gated `SGI_KBD_DEBUG` tracing in `sgi_ioc2_kbd.c` that re-proves
  the guest *does* enable + configure the mouse (`0xD4`→`0xF4`/`0xE8 0x03`) and QEMU correctly
  enables + queues motion packets — confirming the break is entirely above pckm, in the kernel
  idev STREAMS config. **Fix (kernel, non-trivial):** register the PS/2 kbd/mouse decode into the
  idev stack on IP54 (call `htp_register_board` + keyboard stream setup, stubbing the DUART/GIO
  HW-probe that faults on IP54), then rebuild `/unix.new` via lboot. This blocks a mouse-driven
  clogin login and full interactive desktop use; keyboard login to the classic 4Dwm desktop works.
- **Colormap/gamma dimness.** The plain xdm greeter and the 4Dwm desktop render **near-black**,
  while clogin (which installs its own colormap) renders bright. Likely pvrex3 isn't applying the
  RAMDAC `-gamma 1.7` ramp / default-colormap path on 8bpp PseudoColor. Secondary to the mouse.
- **clogin greeter non-determinism.** On some boots clogin exits and xdm falls back to the dim
  built-in plain greeter. Tied to the same colormap issue / clogin lifetime.

## Files

- `run_mac_desktop.py` — native launcher (boot, login, renderer, Atlantis; `--atlantis`,
  `--foreground`). Dev convenience: `QEMU_COCOA_BG=1` opens the window backgrounded/no-focus
  (gated in `ui/cocoa.m`; NOT a delivery feature).
- `vm_instances/ip54-test/disk.qcow2.golden` — pristine (visuallogin/desktop off).
  `…golden.preDesktop` — pre-config backup. `…golden.desktop` — visuallogin=on + desktop=on +
  Xsession.dt fixed (XFS-clean).
- `mac_cfg_diag2.py` (live csh-native config apply), `mac_*_capture.py` / `mac_mouse_*.py`
  (boot + capture/probe harnesses).
