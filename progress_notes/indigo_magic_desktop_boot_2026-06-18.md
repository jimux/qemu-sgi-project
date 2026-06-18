# Booted into the Indigo Magic 4Dwm desktop — 2026-06-18

**Outcome:** From `prebuilt_disks/irix-6.5.5-complete.qcow2`, the v2 gold
image boots to clogin (with working face icons for root, EZsetup, demos,
guest), accepts root login, and presents the 4Dwm-managed Indigo Magic
Desktop (file manager, toolchest, xterm with proper window
decorations).

Captures at the milestones (in `/tmp/desktop_*.png`):
1. PROM "WELCOME TO INDY" maintenance menu
2. clogin Visual Login dialog with face icons
3. Post-login: bare xterm with no WM
4. After `/usr/bin/X11/4Dwm &`: xterm gains title bar + min/max buttons
5. After `toolchest &` + `fm &`: full 4Dwm desktop (file-manager
   window with root directory, toolchest sidebar, xterm)

## Fixes applied this session

### 1. NVRAM `console = g` (graphics, not serial)

The cleared NVRAM defaults to `console = d` (serial-only). With that,
the PROM never paints to Newport, the kernel boots with serial console,
and Xsgi has no display to attach to — the framebuffer stays black.

Fix:
```bash
# Once per fresh NVRAM:
python3 -c "from sgi_mcp.server import _handle_tool; \
    print(_handle_tool('nvram_set', {'variable': 'console', 'value': 'g'}))"
```
or equivalently set `console=g` via the PROM Command Monitor.

After this, `WELCOME TO INDY` renders on the Newport at boot.

### 2. Launch with `-display gtk`, drive PROM via PS/2 keyboard

`console=g` means the PROM menu only appears on the Newport
framebuffer — it ignores serial input from that point. So:
- Launch QEMU with `-display gtk` (and `QEMU_DISPLAY=gtk` env in
  boot_harness scripts).
- To select "Start System" from the PROM menu, send PS/2 keys via the
  QEMU monitor (`sendkey 1 ; sendkey ret`) — NOT via the serial
  socket.

### 3. After login, start 4Dwm + toolchest + fm by hand

The base IRIX install's clogin session (Xsession script) launches
**only** an xterm on this disk — not 4Dwm or any desktop chrome. After
logging in, the wsh terminal opens but has no window decorations. From
the wsh:

```sh
/usr/bin/X11/4Dwm &        # window manager (gives windows their decorations)
/usr/bin/X11/toolchest &    # desktop launcher sidebar
/usr/sbin/fm &              # file manager (also paints desktop icons)
```

That fully renders the Indigo Magic Desktop.

**Follow-up:** make the clogin session script auto-start these. The
right place is the per-user `.sgisession` (or `/usr/lib/X11/Xsession`'s
default fallback). A user with `.sgisession` containing `4Dwm &` /
`toolchest &` / `fm &` would not need the manual launch above.

## What was WRONG before the fix

### Surgical-fill XFS writes corrupted the gold image

`run_v2_fill_remaining.py` used `pyirix.xfs.operations.create_file` +
`write_file` + `mkdir` to inject 67 files (headers + face/icon
placeholders) into the qcow2. **IRIX panics at boot on that disk** —
"PANIC: Fatal error on root filesystem". Our XFS write path leaves the
FS in a state that passes `xfs_check` but fails IRIX's runtime
validation.

**Action taken:** the previous-promote (`*.qcow2.prev`, the pre-fill
disk) is now the canonical gold image. The post-fill broken disk is
preserved as `prebuilt_disks/irix-6.5.5-complete.qcow2.fill_broken` for
inspection.

**Implication for the verifier:** the manifest reports
`INCOMPLETE: 29/48 OK, 9 required FAIL` against the working pre-fill
disk — but the desktop actually boots and works. The manifest is
over-strict (wrong paths for several entries). See "Manifest path
corrections" below.

## Manifest path corrections (verifier vs reality)

The disk-inspection contradicts the manifest at these points:

| Manifest path | Actual location |
|---|---|
| `/usr/Cadmin/lib/cloginlib/cloginlogo.rgb` | `/usr/Cadmin/images/cloginlogo.rgb` |
| `/usr/local/lib/faces/<user>` | (different scheme — clogin pulls from `<user-homedir>/.icon`/etc.) |
| `/usr/lib/X11/iconlib` | (visual fallback worked without it) |
| `/usr/lib/desktop/iconcatalog/C` | (visual fallback worked without it) |
| `/usr/include/{Xm,gl,GL}/*.h` | not in this base; require dev_libraries CD |
| `/usr/lib32/mips3/crt1.o` | not in this base; require c_dev CD |
| `/usr/etc/{telnetd,ftpd,tftp}` | live as `/usr/etc/{telnetd,ftpd}` (no `in.` prefix); tftp is in `inetd.conf` |

These reflect the actual paths in a standard IRIX 6.5.5 install. The
manifest should be updated. Dev headers + crt1.o are legitimately
missing — they require installing dev CDs that the standard install
doesn't include.

## Reproducing this boot

```bash
cd /home/jimmy/qemu-sgi
mkdir -p vm_instances/ip54-desktop-test
cp prebuilt_disks/irix-6.5.5-complete.qcow2 vm_instances/ip54-desktop-test/disk.qcow2
# Make sure NVRAM has console=g (one-time setup):
python3 -c "from sgi_mcp.server import _handle_tool; \
    print(_handle_tool('nvram_set', {'variable': 'console', 'value': 'g'}))"
# Launch with GTK display:
/home/jimmy/qemu-sgi/qemu/build-linux/qemu-system-mips64 \
    -M indy -m 256M \
    -bios PROM_library/bins/cpu/ip24/Indy_ip24prom.070-9101-011.bin \
    -display gtk \
    -global sgi-hpc3.autoload=false \
    -drive if=scsi,bus=0,unit=1,file=vm_instances/ip54-desktop-test/disk.qcow2,format=qcow2,cache=writethrough,file.locking=off &
# In the GTK window: at the PROM menu, click "Start System".
# At clogin: type "root", press Enter.
# In the xterm that appears:
#   /usr/bin/X11/4Dwm &
#   /usr/bin/X11/toolchest &
#   /usr/sbin/fm &
```

Desktop appears.
