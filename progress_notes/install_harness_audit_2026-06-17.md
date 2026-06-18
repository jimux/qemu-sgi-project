# Install completeness audit — current golden, 2026-06-17

Run against `vm_instances/ip54-test/disk.qcow2.golden` with the new
`pyirix_qemu/install_v2/completeness/manifest_6_5_5.yaml` manifest and the
`HostBackend` (reads the XFS image directly, no boot needed).

Tool:

```
python3 -m pyirix_qemu.install_v2.completeness.check \
    --backend host \
    --disk vm_instances/ip54-test/disk.qcow2.golden \
    --manifest pyirix_qemu/install_v2/completeness/manifest_6_5_5.yaml
```

## Result

```
INCOMPLETE: 24/48 OK, 7 optional missing, 17 required FAIL
```

The current "golden" install is materially incomplete. The 17 hard failures
explain symptoms we've been patching one-off-style for months.

### Hard failures (manifest entries we contractually require)

Grouped by which IRIX package set they belong to. Once we have the new
install harness running, the corresponding subsystem `inst` selectors are
the fix.

**`desktop_eoe.*` mostly absent — Indigo Magic Desktop**
- `/usr/Cadmin/lib/cloginlib/cloginlogo.rgb` — clogin's bundled logo
- `/usr/local/lib/faces/root` — root's default face (this is the icon-list
  blocker we kept hitting)
- `/usr/sbin/4Dwm` — **the window manager itself**
- `/usr/sbin/toolchest` — desktop launcher
- `/usr/lib/desktop/FTRlib` — file-type rules
- `/usr/lib/X11/iconlib` — default X11 icon library (empty dir)
- `/usr/lib/desktop/iconcatalog/C` — desktop icon catalog (empty)

**`dev.*` / `*_dev` absent — development headers + runtime**
- `/usr/include/Xm/Xm.h` — Motif dev headers (lib present, no headers)
- `/usr/include/gl/gl.h` — IRIS GL dev headers
- `/usr/include/GL/gl.h` — OpenGL dev headers
- `/usr/include/stdio.h` — basic C stdio dev header
- `/usr/lib32/mips3/crt1.o` — C runtime startup object

**`netman_eoe` partial — network daemons missing**
- `/usr/etc/in.telnetd` — telnet daemon (we use telnet from outside)
- `/usr/etc/in.ftpd` — ftp daemon
- `/usr/etc/tftp` — tftp client

**Empty-dir sanity checks**
- `/usr/local/lib/faces` — 0 entries (need ≥1)
- `/usr/lib/X11/iconlib` — 0 entries (need ≥10)
- `/usr/lib/desktop/iconcatalog/C` — 0 entries (need ≥5)

### What IS present (24/48)

- `clogin` itself, `fm`, `Xsgi`, `xdm`, `inetd`, `lboot`, `ifconfig`
- MIPSpro (`cc`, `be`, `fec`) — kernel rebuilds work
- `/var/sysgen/master.d`, `/var/sysgen/system/irix.sm`, sysgen tree
- `libX11.so`, `libXt.so`, `libXm.so` (Motif runtime), `libGL.so`,
  `libgl.so` (IRIS GL)
- `/usr/lib/desktop/iconcatalog` (the dir exists, but its `C/` subdir is
  empty — file-type catalogs got skipped)

## Why this matters

This is the audit the user asked for when they said "we've had way too
many one-off installs of this and that — we really need to do a complete
fresh install." The list above is the contract for what the new
`install_v2` harness has to deliver. When a fresh install passes this
manifest, we have an honest baseline.

The 17 required failures map directly to `inst` selector groups the new
profile (`profiles/irix_6_5_5_dev.yaml`) declares:

```
install desktop_eoe.*    # 4Dwm, toolchest, FTRlib, iconlib, faces
install dev.*            # crt1.o, stdio.h
install c_dev x_dev gl_dev motif_dev   # all the missing headers
install nfs.* netman.*   # in.telnetd, in.ftpd, tftp
```

## Re-running this audit

Against any disk, any time:

```
python3 -m pyirix_qemu.install_v2.completeness.check \
    --backend host --disk <disk.qcow2> \
    --manifest pyirix_qemu/install_v2/completeness/manifest_6_5_5.yaml
```

Exit 0 = manifest passes; exit 1 = at least one required entry failed.
JSON output via `--json`.

## Next

- Wire profiles + conflict policies (install_v2/profiles/, policies/).
- Build inst_session.py — clean expect wrapper for the `inst` shell.
- Run a fresh install through the new harness, gate on this manifest.
- Save the result as `prebuilt_disks/irix-6.5.5-complete.qcow2`.
- Re-fork ip54-test from it, confirm clogin shows faces by default.
