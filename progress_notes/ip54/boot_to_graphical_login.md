# IP54 Boot → Graphical Login (xdm/xlogin) — ACHIEVED

Date: 2026-06-09 (Milestone 2 of the boot-to-desktop effort)

## Result

**Zero-touch cold boot of `ip54-test`/`ip54-desktop` reaches the xlogin
dialog**: power-on → IRIX 6.5.5 multi-user (initdefault:2) → S98xdm →
xdm → Xsgi (spawned by xdm, stock Xservers flags + `-gamma 1.7`) →
"X Window System" Login/Password dialog. Typing via `newport_sendkey`
lands in the Login field (screendump `framebuffers/m2_xlogin_typed.png`
shows "guest" typed with cursor).

Serial console (pvuart getty) remains usable alongside xdm.

## How (all offline disk injection — no guest-side steps)

Files injected with `fs_inject` (now XFS-capable; see below) into the
instance qcow2 while the VM is off:

| Guest path | Content / source |
|---|---|
| /etc/config/visuallogin | `off` (xlogin, not clogin; NEVER write this file from inside the guest — guest-side write panics XFS) |
| /etc/config/windowsystem | `on` (gates both the X wrapper script and init.d/xdm) |
| /etc/config/netif.options | `if1name=pvnet0`, `if1addr=10.0.2.15` |
| /etc/config/static-route.options | `$ROUTE $QUIET add default 10.0.2.2` |
| /var/X11/xdm/xdm-config | desktop_config/ version — `DisplayManager.grabServer: False` (mandatory: without HW kbd interrupts XGrabServer hangs shmiq) |
| /var/X11/xdm/Xsetup_0 | `xset r off` |
| /var/X11/xdm/Xservers | stock line + `-gamma 1.7` |
| /etc/rc2.d/S98xdm | plain copy of init.d/xdm (pyirix.xfs cannot create symlinks; rc2 execs it fine) |

Payloads live in `ip54_tftp_staging/desktop_config/inject/`.

## Critical discovery: IRIX XFS V1 rejects inline regular files

First boot attempt failed mysteriously: xdm ran but looped with empty
`xdm error (pid N):` messages and a black screen. Manual run of the
Xservers command printed **`X: windowsystem not enabled`** —
`/usr/bin/X11/X` is a 1039-byte wrapper script that checks
`chkconfig windowsystem`. Reading the flag file made the kernel log
**`corrupt inode (local format for regular file)`**.

Root cause: `pyirix.xfs` stored small files inline in the inode
(XFS_DINODE_FMT_LOCAL). Modern XFS allows that; **IRIX 6.5 XFS V1 does
not for regular files** (only dir short-form and symlinks). Files under
~150 bytes (inode literal area) became unreadable to the guest; larger
ones (S98xdm 498B, xdm-config 541B) got extents and worked — which made
the failure pattern maximally confusing.

Fix: `pyirix/xfs/inode.py write_file_data()` now ALWAYS allocates
extents, even for 1-byte files. Verify after any injection:
`di_format == 2` for every written file (a quick read_inode loop), plus
`pyirix.xfs check`. tests/test_xfs_write.py passes with the change.

## Tooling change

`sgi_mcp/sgi_fs.py fs_inject()` now detects XFS-only images and
delegates to `pyirix.xfs.operations` (create or overwrite). Caveats:
flattens qcow2 backing chains (convert-raw round trip); VM must be shut
down; never trust multi-extent OFFLINE READS until the pyirix read bug
is fixed (see keyboard_mouse_input.md).

## Login completion — tested 2026-06-10 (partial)

xlogin keyboard flow confirmed: Enter in the Login field ADVANCES to
Password (screendump m2_login_after.png shows "root" + cursor in
Password); a second Enter submits. **The login itself works**: the
session started — Xsession.dt ran, soundscheme launched ("Soundscheme:
Ignoring invalid device DefaultOut" on console). Then:

    ALERT: Process [Xsession.dt] 288 generated trap, but has signal 11
    held or ignored
        epc 0x0 ra 0x0 badvaddr 0x0
    Process has been killed to prevent infinite loop
    PANIC: KERNEL FAULT

Xsession.dt jumped to PC 0 (NULL ra too) with SIGSEGV held → killed →
kernel fault. This is the post-activity fragility (see below) in its
clearest form, and it is now the ONLY blocker between zero-touch boot
and a full desktop session.

## FULL MILESTONE ACHIEVED — 2026-06-10 evening

Power-on → multi-user → xdm → xlogin → typed `root` + Enter + Enter →
**desktop session with the full Toolchest** (Desktop/Selected/Internet/
Find/System/Help under 4Dwm) — zero manual steps, zero traps, zero
panics, clean `init 0`. Screendump: `framebuffers/m2_login_final.png`.

Unblocked by injecting `/etc/config/desktop` = `off` offline (extent
format!) so xdm's Xsession takes the classic `/bin/sh` path instead of
exec'ing the trapping `#!/bin/bsh` Xsession.dt. Golden refreshed with
this state (2026-06-10 20:33).

## Remaining gaps (no longer blocking the milestone)

1. Xsession.dt (`chkconfig desktop on` path) still traps — see
   xsession_null_jump.md; the classic session sidesteps it.
2. **Post-X userland fragility**: after an X server has run, some
   binaries/sh forks segfault (xset, rc0 K-scripts at shutdown →
   Memory fault cascade → shutdown PANIC). Boot-time rc2 is clean.
   Suspect gfx-path (pvrex3/pvfb/shmiq) corrupting something. This is
   the top remaining stability bug (task: shutdown panic).
3. 3-consecutive-boot stability run not yet done.
4. Toolchest green-vs-gray scheme colors (cosmetic, deferred).

## State

- `ip54-desktop` instance = M2 testbed (full copy, has all injections).
- `ip54-test` disk + golden refreshed with the same config
  (2026-06-09 23:24, both 1.03GB standalone qcow2).
- xdm-era goldens: `disk.qcow2.golden.prev` kept one generation back.
