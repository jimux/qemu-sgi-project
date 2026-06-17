# Xsession.dt NULL jump — RESOLVED (task 11)

**RESOLVED 2026-06-11.** Root cause: Xsession.dt is `#!/bin/bsh`, and the
classic V7-style Bourne shell grows its memory arena by FAULTING and
catching SIGSEGV (irix-655/m/eoe/cmd/sh/fault.c). xdm spawns sessions
with SIGSEGV blocked; IRIX exec preserves held signals
(kern/os/exec.c:862), so the kernel kills bsh on its first arena-growth
fault ("signal 11 held or ignored"; the epc 0x0 is a kill-report
artifact). Fix: shebang swap to `#!/bin/sh` (the script is POSIX-clean;
verified by diff-free behavior under sh). Deployed via offline
fs_inject; two consecutive .dt-session boots ran with zero traps,
soundscheme started, /.desktop-IRIS session state written. The
byte-authentic alternative (kernel clears held SIGSEGV on exec) remains
optional Phase E in the Indigo Magic plan.

Original diagnosis log follows.

Date: 2026-06-10. Last blocker to a full xdm desktop session.

## Symptom

Every xdm spawn of the session traps:

    ALERT: Process [Xsession.dt] N generated trap, but has signal 11
    held or ignored
        epc 0x0 ra 0x0 badvaddr 0x0
    Process has been killed to prevent infinite loop

Deterministic (3 consecutive PIDs per boot, multiple boots). Since the
CP0_Cause race fix, the kernel SURVIVES these traps (xdm retries, then
returns to xlogin) — pre-fix each trap escalated to a kernel panic.

## Ruled out (one diagnostic boot, run_t11_bshdiag.py)

- **Shell binary**: Xsession.dt is `#!/bin/bsh`, but `/bin/bsh -c` and
  `/bin/csh -c` run fine from serial. All relevant binaries are N32
  (file(1)): bsh, sh, sbin/sh static; xset, xterm dynamic. No O32 angle.
- **Script content**: `sh /var/X11/xdm/Xsession.dt` from an interactive
  serial shell (DISPLAY=:0 against bare Xsgi) RAN THE SESSION — 4Dwm
  came up (`4Dwm -launch -xrm *SG_UseBackgrounds: True` in ps, root
  weave on screendump t11_xsession_sh.png).
- **xset**: now exits 0 (its old Memory faults were the Cause race).

## Remaining hypothesis space

The trigger is **xdm's spawn context**, not the script or shell:
- "signal 11 held or ignored" is itself the anomaly to chase — a
  normal session child should NOT have SIGSEGV blocked. Either xdm
  blocks it around fork and the IP54 kernel loses the unblock (sigmask
  syscall stub? sigprocmask path?), or the kernel's exec fails to
  reset the mask. With SIGSEGV blocked, ANY fault in the child becomes
  the kill+ALERT, and `epc 0x0` may be an artifact of the kernel's
  forced-kill reporting rather than a literal NULL jump.
- xdm session setup: setuid/setgid, setpgrp, controlling-tty-less
  exec — each touches IP54-stubbed kernel paths (ip54_stubs.c).

## Next steps

1. Read xdm's session-spawn code (X11R6 xdm session.c in IRIX source
   tree if present) — what sigmask does it set before exec?
2. In-guest discriminator: `bsh /var/X11/xdm/Xsession.dt` from serial
   (shell vs context), and a tiny C wrapper that blocks SIGSEGV then
   execs the script — if that reproduces the trap, the bug is the
   kernel's handling of blocked-fatal-signals (likely fixable in the
   IP54 kernel stubs or acceptable to patch xdm's resources).
3. Workaround available NOW: `chkconfig desktop off` (or inject
   $HOME/.disableDesktop) → Xsession (plain /bin/sh, no .dt) runs the
   classic session — proven flow on Indy. Gets a WORKING xdm login
   session while .dt is debugged.

## Filesystem incident (same day)

During the serial Xsession.dt run, the kernel logged "Bad magic # 0x0
in XFS inode buffer ... blockno 5270224" — a zeroed inode cluster
~2.5GB in, likely torn metadata from the panic-era hard kills
(writeback caching amplifies). Offline pyirix check misses it (doesn't
walk all inode clusters). Repaired via in-IRIX xfs_repair from
irix655-dev with the ip54 disk as unit 2 (run_t11_xfsrepair.py).
NOTE: irix655-dev's qcow2 also needed `qemu-img check -r all`
(container-level refcount corruption from a SIGKILL) before it would
open read/write.
