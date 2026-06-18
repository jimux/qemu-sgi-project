# IP54 gold image up and running with Indigo Magic Desktop

**Date:** 2026-06-18
**Result:** `prebuilt_disks/ip54-6.5.5-gold.qcow2` (1.1 GB) +
`prebuilt_disks/ip54-6.5.5-gold.nvram.bin` (8 KB). Boots on
`machine=sgi-ip54` to the IRIX X Window System login dialog on a solid
SGI light-blue background; logging in as `root` (no password)
auto-launches **4Dwm + toolchest** — the Indigo Magic Desktop.

## Launch recipe

```bash
QEMU_DISPLAY=gtk ./run_ip54_desktop.sh
```

(or pass an alternate disk: `./run_ip54_desktop.sh path/to/other.qcow2`)

The script wraps:

```bash
IP54_CAUSE_IP5_COUNT_PA=0x0829fee0 \
qemu-sgi-repo/build-linux/qemu-system-mips64 \
    -M sgi-ip54 \
    -bios PROM_library/bins/cpu/ip54/ip54.bin -m 256M \
    -L qemu-sgi-repo/build-linux/pc-bios -display gtk \
    -serial mon:stdio \
    -drive if=mtd,file=prebuilt_disks/ip54-6.5.5-gold.qcow2,\
        format=qcow2,cache=writeback,file.locking=off \
    -nic user,tftp=ip54_tftp_staging,hostfwd=tcp::2324-10.0.2.15:23
```

### Required env var: `IP54_CAUSE_IP5_COUNT_PA`

QEMU's `sgi-pvclock` device watches a specific physical address in the
guest's kernel memory. That address is the `cause_ip5_count` global
symbol's location, which **drifts every time the kernel is rebuilt**.
The gold image's `/unix.new` has it at `0x88829fee0` (virtual) → phys
`0x0829fee0`.

For any disk you build via `run_m1_kernel_rebuild.py`, recompute:

```bash
SYM=$(mips-linux-gnu-nm /path/to/unix.new | grep -w cause_ip5_count | awk '{print $1}')
PA=$(printf "0x%x\n" $((0x$SYM & 0x1FFFFFFF)))
export IP54_CAUSE_IP5_COUNT_PA=$PA
```

## What's on the disk

| Component | State |
|---|---|
| `/unix.new` (IP54 kernel with PV drivers) | 6,134,744 B — built via run_m1_kernel_rebuild.py |
| `/usr/bin/X11/Xsgi` | Present (X server) |
| `/usr/bin/X11/xdm` | Present (display manager) |
| `/usr/bin/X11/4Dwm` | Present (window manager) |
| `/usr/bin/X11/toolchest` | Present (desktop launcher) |
| `/var/X11/xdm/xdm-config` | 541 B — hand-crafted minimal config with all critical directives (`servers`, `setup`, `startup`, `session`, `loginProgram`) |
| `/etc/config/visuallogin` | **off** (gives stock xdm Xlogin dialog, NOT clogin face picker) |
| `/etc/config/xdm` | on |
| `/etc/config/windowsystem` | on |
| `/etc/config/desktop` | off — but Xsession still launches 4Dwm + toolchest because its fall-through path doesn't gate on this |

### About `visuallogin=off`

The gold image has visuallogin OFF, meaning xdm uses its built-in
"X Window System" dialog rather than IRIX's clogin face picker. Why
keep this off:

Earlier in this session I tried flipping it to `on` + restarting xdm
to get clogin. xdm spawned correctly but no clogin face dialog
appeared (stuck on solid blue with cursor only) and subsequent
shell commands segfaulted while reading `/var/X11/xdm/xdm-errors`.
After `init 6`, the next boot hung silently. Reverting to the
stock golden (visuallogin=off) restored clean behavior.

clogin integration on IP54 is left as a follow-up — likely needs
a setup script that the install harness's `apply_xdm_fixes` flow
(now corrected in qemu-sgi-project@390f524) would have provided
naturally if the disk had been built through the install_irix
pipeline. The current IP54 gold predates that fix.

The xdm Xlogin dialog is fully functional — type `root`, press
Enter twice (no password), and the Indigo Magic Desktop comes up.

## Indigo Magic Desktop after login

The post-login session script launches:
- **4Dwm** — Motif-look window manager (window decorations)
- **toolchest** — the SGI desktop launcher menu (upper-left)
- (file manager `fm` is not auto-spawned on this disk; can be
  started from Toolchest → Tools or from a terminal)

## How this disk came to be

- Base: `vm_instances/ip54-test/disk.qcow2.golden` (1.1 GB, Jun 17),
  forked from `irix655-dev` and updated with the IP54 PV kernel via
  `run_m1_kernel_rebuild.py`.
- Promoted unchanged to `prebuilt_disks/ip54-6.5.5-gold.qcow2`
  alongside its NVRAM (`.nvram.bin`).

The Indy gold (`prebuilt_disks/irix-6.5.5-complete-fixed.qcow2`) has
the **clogin face picker** AND `desktop_eoe` extras, but doesn't have
the IP54 PV kernel. The two golds serve different machines:
- Indy gold → `machine=indy` (HPC3 SCSI, Newport graphics)
- IP54 gold → `machine=sgi-ip54` (PV drivers, pvfb framebuffer)

## Verified boot sequence

1. PROM loads kernel from disk via MTD interface.
2. IP54 kernel inits PV devices (pvuart_cn, pvdisk, pvfb, etc.).
3. init runs through runlevel 2 normally; reaches multi-user.
4. xdm starts via `/etc/init.d/xdm` (chkconfig xdm=on, windowsystem=on).
5. xdm reads `/var/X11/xdm/xdm-config`, finds:
   - `DisplayManager.servers: /var/X11/xdm/Xservers`
   - `DisplayManager._0.session: /var/X11/xdm/Xsession`
6. Xservers runs the X server with `-solidroot sgilightblue` →
   solid blue background painted on the pvfb framebuffer.
7. xdm's built-in Xlogin dialog renders.
8. After login, Xsession runs — launches 4Dwm + toolchest →
   Indigo Magic Desktop.

## Files added in this session

- `run_ip54_desktop.sh` — single-command launch
- `prebuilt_disks/ip54-6.5.5-gold.qcow2` — the gold disk (gitignored)
- `prebuilt_disks/ip54-6.5.5-gold.nvram.bin` — saved NVRAM (gitignored)
- `progress_notes/ip54_gold_image_up_2026-06-18.md` — this writeup
