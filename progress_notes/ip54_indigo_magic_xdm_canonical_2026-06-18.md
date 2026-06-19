# IP54 Indigo Magic — canonical xdm-config restores IRIS Motif dialog

**Date:** 2026-06-18 (later session)

## TL;DR

On top of the screen-refresh fix (`pvrex3_vblank_timer` setting
`display_dirty = true` unconditionally, already pushed), restoring the
canonical 2619-byte xdm-config from the `x_eoe` install media — which
the legacy IP54 baseline had as a 541-byte truncated stub left over
from the broken install_irix harness — promotes the IP54 boot from a
**blank blue X11 screen with just a cursor** to a **full IRIS-branded
Motif login dialog** (`Login name:` field, "Log In" / "Help" buttons,
purple IRIS logo). Screenshot: `progress_notes/ip54_iris_xdm_dialog.png`.

Clogin face picker still doesn't fire (Xlogin enters the `visuallogin
on` branch but xdm renders its own dialog instead), and the userspace
remains intermittently unstable when `visuallogin=on` (chkconfig and
even `ps -ef` segfault randomly). Those two remain open.

## What I changed

### On the disk (live, via tftp + telnet — never offline XFS write)

1. Replaced `/var/X11/xdm/xdm-config`:
   - Pre: 541 B (truncated stub from a prior install_irix `> redirect` bug)
   - Post: 2619 B (canonical, extracted via `pyirix.dist.archive.extract_one`
     from `IRIX_6.5_Foundation_1/x_eoe.sw`, entry `x_eoe.sw.eoe / xc/programs/xdm/config/default/xdm-config`)
   - Pre version preserved at `/var/X11/xdm/xdm-config.pre_canonical`.
2. `chkconfig visuallogin on` (was off)
3. `chkconfig desktop on` (was off)

### Pipeline used (new pyirix tooling)

```python
from pyirix.dist.idb import parse_idb
from pyirix.dist.archive import extract_one
idb = parse_idb(".../IRIX_6.5_Foundation_1/x_eoe.idb")
sw  = open(".../IRIX_6.5_Foundation_1/x_eoe.sw","rb").read()
data = extract_one(sw, [e for e in idb.files()
                        if e.install_path == "/var/X11/xdm/xdm-config"][0])
# 2619 B exactly
```

Then on the guest:

```
(echo binary; echo get xdm-config.canonical; echo quit) | tftp 10.0.2.2
cp /tmp/xdm-config.canonical /var/X11/xdm/xdm-config
chmod 644 /var/X11/xdm/xdm-config
/etc/chkconfig visuallogin on
/etc/chkconfig desktop on
sync; init 6
```

Driver: `run_ip54_xdm_fix.py` (committed alongside this note).

## What this proves

The audit-flagged "desktop_eoe pieces missing" theory was largely
wrong. The IP54 baseline disk **already has** 4Dwm, toolchest, fm,
clogin, the entire iconcatalog tree, every X11/xdm helper script
(`Xlogin`, `Xstartup`, `Xaccess`, `GiveConsole`, …) at byte-identical
sizes to the working Indy gold. The single file that was wrong on the
baseline was `xdm-config`: the install_irix harness had been writing a
truncated 541-byte stub instead of the canonical 2619-byte file. With
the canonical config in place, xdm reads its full DisplayManager.*
directives — including `loginProgram = /var/X11/xdm/Xlogin` and the
per-display session/startup paths — and renders the full IRIS Motif
dialog.

## What still doesn't work

### Clogin face picker doesn't fire

`/var/X11/xdm/Xlogin` reads:

```sh
if /etc/chkconfig visuallogin ; then
    if [ -x /usr/Cadmin/bin/clogin ] ; then
        exec /usr/Cadmin/bin/clogin -f $1
    fi
fi
```

Both conditions evaluate true (chkconfig visuallogin returns 0,
`/usr/Cadmin/bin/clogin` is executable 166 KB), yet what xdm displays
is the standard Login-name dialog, not clogin's face picker. Either
clogin is launching and silently dying, OR the Xlogin invocation never
reaches the `exec` line because of the userspace instability described
below. With pipes and `ps` segfaulting we couldn't read xdm-errors
reliably; need an alternative diagnostic.

### Userspace is intermittently unstable with visuallogin=on

After visuallogin is flipped on, even simple commands segfault:

- `/etc/chkconfig desktop` — 1-in-5 `Memory fault (coredump)`
- `/etc/chkconfig visuallogin` — 1-in-3 segfault
- `ps -ef` — segfaults consistently (with or without pipe)
- `/usr/bin/X11/xdpyinfo` — segfaults
- Output redirection (`> /tmp/x`) sometimes survives, sometimes not

This is pre-existing (it was seen in the earlier `chkconfig
visuallogin on` attempt on the unmodified baseline) — not caused by
the xdm-config swap. It looks like a memory-corruption or
syscall-related kernel bug that surfaces when post-graphical-login
services start running. The IP54 kernel build needs investigation;
this is the wrong layer to fix it from userspace.

### Input doesn't reach the X dialog

Driving `mouse_button` + `sendkey` via the QEMU monitor moves the
cursor and clicks but the keystrokes don't reach the login field.
Known issue, separate from this work.

## Files / artifacts

- `progress_notes/ip54_iris_xdm_dialog.png` — boot screenshot, IRIS
  Motif dialog after the fix (proof of progress).
- `ip54_tftp_staging/xdm-config.canonical` — the canonical 2619-byte
  config, staged for live tftp injection. Reusable.
- `ip54_tftp_staging/desktop_eoe.tar.gz` — 1.26 MiB tar of the
  `desktop_eoe.sw.*` payload (198 files + 78 symlinks). NOT used in
  this iteration because the audit revealed those files were already
  on the baseline — kept for any future need.
- `build_desktop_eoe_tar.py` — re-runnable extractor (calls
  `pyirix.dist.archive.extract_one`).
- `run_ip54_xdm_fix.py` — end-to-end driver (boot → telnet → tftp →
  swap → chkconfig → init 6 → reboot → verify).
- `vm_instances/ip54-test/disk.qcow2.indigo_magic_dialog` — fork of
  the IP54 disk with the canonical xdm-config + visuallogin=on +
  desktop=on applied. Boots cleanly to the IRIS dialog.
- `vm_instances/ip54-test/disk.qcow2.post_xdm_canonical` — same change
  set, taken slightly earlier (after first reboot). Use either.

## What's left for full Indigo Magic parity

The remaining work is **on the IP54 kernel side**, not the userspace
disk:

1. Diagnose why `chkconfig` / `ps` / pipes intermittently segfault
   under post-Xsgi load. Candidate causes: a stack/heap protection
   issue when shells spawn many children, a TLB-miss-handler edge
   case at high process counts, or an issue specific to the
   pvfb/pvrex3 + Xsgi interaction. The IP54 kernel is built with
   `-G 8` and has driver-by-driver patches that don't exist on the
   Indy stock kernel — any of those is a candidate.
2. Decide whether to keep iterating on the IP54-patched kernel (Path
   A1: fix the IP54 kernel) or instead build a brand-new IP54 kernel
   from a clean tree using the new `pyirix.xfs.mkfs` /
   `pyirix_qemu.build_minimal_root` "build from scratch" workflow
   that's already proven to produce a bootable root (Path A2: rebuild).

For now, the **visual** parity with Indy gold is much closer than it
was: blank blue + cursor → full IRIS Motif login dialog with proper
purple IRIS branding. That is a real, user-visible win on top of the
screen-refresh fix.

## Reproducing

```bash
# from the qemu-sgi root, with vm_instances/ip54-test pointing at the
# updated disk.qcow2 (or restore: cp disk.qcow2.indigo_magic_dialog disk.qcow2)

env IP54_CAUSE_IP5_COUNT_PA=0x0829fee0 QEMU_DISPLAY=gtk \
  qemu-sgi-repo/build-linux/qemu-system-mips64 \
    -M sgi-ip54 \
    -bios PROM_library/bins/cpu/ip54/ip54.bin \
    -m 256M \
    -L qemu-sgi-repo/build-linux/pc-bios \
    -display gtk \
    -drive if=mtd,file=vm_instances/ip54-test/disk.qcow2,format=qcow2,cache=writeback,file.locking=off \
    -nic user,tftp=ip54_tftp_staging,hostfwd=tcp::2324-10.0.2.15:23 \
    -audiodev pa,id=aud0 -global sgi-pvaudio.audiodev=aud0
# Wait ~2.5 min for the IRIS dialog to appear on the GTK window.
```

To restore the canonical xdm-config on any IP54 disk built from the
broken install_irix lineage:

```python
# python3 run_ip54_xdm_fix.py
```
