# Indigo Magic Desktop blocker: xdm-config overwritten by install harness

**Date:** 2026-06-18
**Cause:** `pyirix_qemu/install/irix.py:apply_xdm_fixes()` overwrote
`/var/X11/xdm/xdm-config` with a 128-byte 3-line stub, wiping out the
2619-byte canonical config that `x_eoe.sw` had just installed.
**Fix:** Append (`>>`) the 3 clogin overrides instead of replacing (`>`).
**Status:** Confirmed end-to-end. After applying the proper xdm-config
to a running guest and restarting xdm, the full Indigo Magic Desktop
came up — solid SGI light-blue root, toolchest sidebar, file-manager
Icon Catalog, 4Dwm window decorations. Captured at `/tmp/post_fix_desktop.png`.

## How we found it

Diagnosing "logged in but no 4Dwm or background" required two new
pyirix modules:

- **`pyirix.dist.idb`** — parser for IRIX's text manifest format.
  Every IRIX product ships `<product>.idb` listing every file it owns
  with install_path / mode / size / archive offset / subsystem.
- **`pyirix.dist.audit`** — diffs an installed disk vs the `.idb`s of
  the products it claims to have installed. Surfaces three failure
  modes: missing files, size mismatches, type mismatches. Polymorphic
  on `/var/inst/<product>` layout — accepts either file (modern
  descriptor-blob) or directory (older versioned-subtree).
- **`pyirix.dist.archive`** — extracts files from `.sw` archives. The
  archive format is per-file `[u16-BE pathlen][path][cmpsize bytes]`,
  with payloads stored as UNIX `compress`-format LZW (magic
  `\x1f\x9d`), NOT deflate as I'd initially guessed. We use
  `gunzip -c` via subprocess to decompress.

Running the audit on the gold image surfaced 17 required failures —
notably `/var/X11/xdm/xdm-config: expected=2619, actual=128`. That
file is the entire xdm bootstrap config — when truncated, xdm has no
idea where to find `Xservers`, `Xstartup`, or `Xsession`. Without
`Xservers`, X11 starts without `-solidroot sgilightblue` → B&W
checkerboard root window. Without `_N.session: /var/X11/xdm/Xsession`,
xdm falls back to its compiled-in default which spawns a bare xterm
→ no 4Dwm, no toolchest.

Extracting the proper xdm-config from `x_eoe.sw` (via the new archive
module) revealed it ships **all** the directives — `servers`,
`startup`, `session` per-display — that were missing from our disk.

A `grep` of the install harness for `DisplayManager._0.loginProgram`
matched immediately: `apply_xdm_fixes` wrote three echo lines with
`>` (truncate). The author's comment claimed "xdm-config is delivered
empty by x_eoe.sw" — incorrect. The proper file is shipped and was
being overwritten.

## The fix

```diff
- q.send(f"echo 'DisplayManager._0.loginProgram:    /var/X11/xdm/Xlogin' > {xdm_cfg}\r")
+ q.send(f"echo 'DisplayManager._0.loginProgram:    /var/X11/xdm/Xlogin' >> {xdm_cfg}\r")
```

(`>` → `>>` on the first line; the next two were already `>>`.)

The three clogin-integration directives now APPEND to the existing
2619-byte xdm-config, producing a final 2830-byte file with:
- All standard xdm settings preserved (`servers`, `errorLogFile`,
  `pidFile`, per-display `startup`/`session`, etc.)
- Three clogin overrides on top:
  - `_0.loginProgram: /var/X11/xdm/Xlogin` (clogin user-picker)
  - `grabServer: False` (emulation friendliness)
  - `_0.authorize: false` (XAUTHORITY path fix)

## Validating the fix on the existing gold

```bash
# Telnet into the running guest:
python3 -c "
from pyirix_qemu.irix_telnet import IRIXTelnet
t = IRIXTelnet(host='localhost', port=2326)
t.login(user='root'); t.run('exec /bin/sh')
# Fetch fixed config via tftp from the host:
t.run('cd /tmp ; ifconfig ec0 10.0.2.15 netmask 255.255.255.0 up')
t.run('tftp 10.0.2.2')                # binary; get xdm-config-fixed /tmp/x; quit
t.run('cp /tmp/xdm-config-fixed /var/X11/xdm/xdm-config')
t.run('sync ; sync ; sync')
# Restart xdm to pick up new config:
t.run('/etc/init.d/xdm stop')
t.run('/etc/init.d/xdm start')
"
```

After xdm restart, the framebuffer shows the proper clogin with
indigo background. After login, 4Dwm + toolchest + fm spawn
automatically via `/var/X11/xdm/Xsession`. The full Indigo Magic
Desktop renders.

## Saved gold image

- `prebuilt_disks/irix-6.5.5-complete-fixed.qcow2` (2.1 GB) — the
  test instance with the fixed xdm-config baked in. Boots straight
  to a working Indigo Magic Desktop session.
- `prebuilt_disks/irix-6.5.5-complete.qcow2` — the original
  install_irix output (broken xdm-config still). Will be replaced on
  next install_irix run since the harness now writes the corrected
  flow.

## Other findings worth keeping

The audit also surfaced **543 files** with size mismatches across the
desktop packages (4Dwm, desktop_eoe, desktop_base, sysadmdesktop,
x_eoe). Most are slight (+60 bytes here, -20 bytes there); a few are
material (one file +825KB). The binaries we exercised (`4Dwm`,
`toolchest`, `clogin`, `Xsgi`) all run despite the deltas, so most
of those are non-fatal. Some are due to install_level=standard
deliberately skipping optional subsystems (Xoptfonts, Xunicodefonts,
pex — ~430 files). The rest are a separate "size drift during
extraction" issue that doesn't appear to break runtime — left as a
follow-up to investigate via .sw-archive checksum comparison.

The 23 missing files gated by `mach()` filters (e.g.
`mach(GFXBOARD=KONA SUBGR=IP19)`) are correctly skipped — those are
hardware-specific variants for boards our emulated indy doesn't have.

## New pyirix module — usage

```bash
# Parse one .idb:
python3 -m pyirix.dist.idb 4Dwm.idb

# Audit an installed disk vs a dist:
python3 -m pyirix.dist.audit \
    --disk prebuilt_disks/irix-6.5.5-complete.qcow2 \
    --dist-image software_library/.../combined.img \
    --product x_eoe

# Extract one product's files from a .sw to a host dir:
python3 -m pyirix.dist.archive \
    --idb 4Dwm.idb --sw 4Dwm.sw --out /tmp/4Dwm-extracted
```
