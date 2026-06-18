# IP54 screen-refresh fix + Indigo Magic parity status

**Date:** 2026-06-18

## ✅ Screen refresh fix — DONE

Symptom: on `machine=sgi-ip54`, the GTK window only repainted when the
guest received input. Idle screens (e.g. the X login dialog) froze on
their last-drawn pixel. Empirically: moving the host pointer with no
input events did NOT move the cursor in the guest framebuffer until
something else triggered a REX3 write.

Cause: `pvrex3_update_display()` in `qemu-sgi-repo/hw/display/sgi_pvrex3.c`
short-circuits when `display_dirty == false`, and `display_dirty` is
only set by REX3 register writes (which the guest only emits when the
X server has actual drawing work to do). When the X server settles
into a steady state, no writes happen, so no repaints reach the host.

Fix: in `pvrex3_vblank_timer` (which already fires at 60 Hz for VRINT
purposes), set `display_dirty = true` unconditionally. This mirrors
how a real SGI RAMDAC continuously scans out VRAM regardless of GPU
activity.

Verified: with the fix in place + a fresh boot of the IP54 gold disk,
sending `mouse_move` via QEMU monitor with ZERO keyboard input causes
the cursor in the GTK window to relocate, then hold position
across multiple seconds of idle. Before the fix the cursor stayed at
its last painted position until the next REX3 write event.

Pushed:
- `jimux/qemu-sgi@4b4ac503a7` — the pvfb tick fix
- `jimux/qemu-sgi-project@d39a736` — submodule bump

## ⚠️ Indigo Magic parity — partial / blocked

Goal: get clogin face picker + 4Dwm + Toolchest on IP54 to match the
working Indy gold.

What works (IP54 baseline):
- IP54 gold (`prebuilt_disks/ip54-6.5.5-gold.qcow2`, derived from
  `vm_instances/ip54-test/disk.qcow2.golden`) boots cleanly on
  `machine=sgi-ip54` to the stock xdm "X Window System" login dialog
  on a solid SGI light-blue background. visuallogin=off; xdm uses
  its built-in greeter.
- The Xlogin script HAS the clogin invocation path:

  ```sh
  # visuallogin: on: clogin; off: plain xdm login
  if /etc/chkconfig visuallogin ; then
      if [ -x /usr/Cadmin/bin/clogin ] ; then
          exec /usr/Cadmin/bin/clogin -f $1
      fi
  fi
  ```

  And `/usr/Cadmin/bin/clogin` exists on the disk (166 KB).

What doesn't work:

1. **Grafting Indy gold contents into the IP54 instance** — copying the
   Indy gold disk (which has working clogin + 4Dwm + Toolchest +
   visuallogin=on) into `vm_instances/ip54-test/` then doing live tftp
   injection of `/unix.new` (IP54 kernel) produced a disk that boots
   to a black screen on `sgi-ip54`. Kernel runs (PCs in userspace per
   IP54-DIAG) but Xsgi doesn't paint. Likely the userspace
   /etc/init.d/network / autoconfig / ec0-vs-pvnet config differs
   between Indy and IP54 in ways that break early-boot services on
   IP54.

2. **Flipping `chkconfig visuallogin on` on the IP54 disk** — boots,
   reaches multi-user (uptime climbs, telnet works briefly), but Xsgi
   never paints (100% black framebuffer); the shell soon segfaults
   on simple pipes (`ps -e | grep …` returns exit 139). xdm-errors
   showed multiple Xsgi PIDs spawning + crashing with
   "Fatal server error: Failed to establish all listening sockets" in
   an earlier iteration of this work.

Hypothesis: the legacy IP54 disk (built from `irix655-dev` +
`run_m1_kernel_rebuild.py`) lacks certain `desktop_eoe.sw.envm` /
`sysadmdesktop` files that clogin/Xsgi need to bind cleanly. We saw
during earlier work that the install_irix harness produced installs
with incomplete `desktop_eoe.*` extraction (the audit at
`progress_notes/install_harness_audit_2026-06-17.md` documented 17
required-file gaps including `/usr/lib/X11/iconlib`, faces, and
filetype catalogs). The IP54 disk is forked off that lineage and
inherits those gaps.

## What's saved

- `vm_instances/ip54-test/disk.qcow2.golden` — original IP54 gold,
  visuallogin=off, X Window System dialog (baseline that boots cleanly)
- `vm_instances/ip54-test/disk.qcow2.before_indy_graft` — same content
  as `.golden`, preserved as safety copy before today's graft attempts
- `prebuilt_disks/ip54-6.5.5-gold.qcow2` — copy of the baseline IP54
  gold, pristine
- `prebuilt_disks/irix-6.5.5-complete-fixed.qcow2` — Indy gold,
  unchanged (boots clogin face picker on `machine=indy`)

## Two viable paths forward

**A. Rebuild IP54 with a complete desktop_eoe.** Take a fresh install
that goes through `install_irix` with our `apply_xdm_fixes` correction
(committed 390f524). After install, follow the install_v2 verify +
addon pipeline to fill the desktop_eoe.* gaps. Then run
`run_m1_kernel_rebuild.py` on top to install /unix.new. This is the
"do it right from scratch" path — ~1-2 hours.

**B. Targeted file injection from Indy gold to IP54.** On the IP54
disk, replace specifically these files (extracted from Indy gold via
the `pyirix.dist.archive` extractor):
  - `/var/X11/xdm/Xsetup_0`
  - `/var/X11/xdm/Xsession`, `Xsession.dt`
  - `/usr/lib/desktop/iconcatalog/*` (file-type icons)
  - `/usr/lib/X11/iconlib/*` (X11 default icons)
  - Possibly `/usr/Cadmin/lib/cloginlib/*` (clogin's per-user
    photo/account assets)
Then `chkconfig visuallogin on`. This is the surgical path — ~30 min
if the right file set turns out to be small.

The screen-refresh portion of this session's goal is fully done and
pushed. The clogin/4Dwm parity is documented as a follow-up that
needs one of the two paths above.

## Reproducing the refresh-fix verification

```bash
# Boot the IP54 gold disk with the rebuilt QEMU:
env IP54_CAUSE_IP5_COUNT_PA=0x0829fee0 \
    /home/jimmy/qemu-sgi/qemu-sgi-repo/build-linux/qemu-system-mips64 \
    -M sgi-ip54 \
    -bios /home/jimmy/qemu-sgi/PROM_library/bins/cpu/ip54/ip54.bin \
    -m 256M \
    -L /home/jimmy/qemu-sgi/qemu-sgi-repo/build-linux/pc-bios \
    -display gtk \
    -chardev socket,id=ser0,path=/tmp/qemu_ip54/serial.sock,server=on,wait=off \
    -serial chardev:ser0 \
    -monitor unix:/tmp/qemu_ip54/monitor.sock,server,nowait \
    -drive if=mtd,file=prebuilt_disks/ip54-6.5.5-gold.qcow2,format=qcow2,cache=writeback,file.locking=off \
    -nic user,tftp=ip54_tftp_staging,hostfwd=tcp::2324-10.0.2.15:23 \
    -audiodev pa,id=aud0 -global sgi-pvaudio.audiodev=aud0 &
sleep 90

# Wait until X login dialog is visible, then via the monitor socket:
python3 -c "
import socket, time
def mon(c):
  s=socket.socket(socket.AF_UNIX);s.connect('/tmp/qemu_ip54/monitor.sock')
  s.sendall(c.encode()+b'\n');time.sleep(0.3);s.close()
# Observe the cursor moving WITHOUT any keyboard input:
mon('mouse_move 200 200')
mon('screendump /tmp/before.ppm')
time.sleep(2)
mon('screendump /tmp/after.ppm')
# diff: cursor will be at different position in after.ppm vs before
"
```
