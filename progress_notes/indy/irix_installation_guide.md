# IRIX Installation Guide for QEMU SGI Emulation

## Overview

This documents the verified procedures for installing IRIX 5.3, 6.2, and 6.5
on the QEMU SGI emulator. All three versions have been successfully installed
and boot to a login prompt.

| Version | Machine | CPU | CD Images | Filesystem | Status |
|---------|---------|-----|-----------|------------|--------|
| 5.3 | indigo2 (IP22) | R4000 | 1 CD (Indigo2 IMPACT) | EFS | Boots to login |
| 6.2 | indigo2 (IP22) | R4000 | 1-2 CDs (2-disc set) | EFS or XFS | Boots to login |
| 6.5 | indy (IP24) | R4000 | 8 CDs (InstTools + F1 + F2 + Apps + Overlays) | XFS | Boots to login |

## Automated Installation

Use `pyirix/install/irix.py` for fully automated installation:

```bash
# Install IRIX 5.3
python3 -m pyirix.install.irix 5.3

# Install IRIX 6.2
python3 -m pyirix.install.irix 6.2

# Install IRIX 6.5
python3 -m pyirix.install.irix 6.5

# Custom disk path
python3 -m pyirix.install.irix 5.3 --disk /workspace/my_disk.qcow2

# Verify an existing installation boots
python3 -m pyirix.install.irix 6.2 --verify-only
```

The script handles disk creation, partitioning with fx, filesystem creation,
package installation, kernel build, and reboot verification. See
`pyirix/install/irix.py` for details.

---

## CD Images

### IRIX 5.3

| Image | Path |
|-------|------|
| All Indigo2 IMPACT | `software_library/IRIX 5.3 All Indigo2 IMPACT - 812-0119-010.efs.img` |

**Note:** This CD targets IMPACT graphics systems. Some packages are flagged
incompatible with our XL-graphics IP22 emulation. The installer requires
`rulesoverride on` to proceed (harmless for a serial-console system).

### IRIX 6.2

| Image | Path |
|-------|------|
| Part 1 of 2 | `software_library/irix_6.2_images/IRIX 6.2 (Part 1 of 2) - 812-0469-001.efs.img` |
| Part 2 of 2 | `software_library/irix_6.2_images/IRIX 6.2 (Part 2 of 2) - 812-0470-001.efs.img` |

Part 1 contains the full EOE (Execution Only Environment) and installs
cleanly with no conflicts. Part 2 has additional packages and is optional.

### IRIX 6.5

| Image | SCSI | Path |
|-------|------|------|
| Installation Tools | Target 4 (boot CD, permanent) | `software_library/irix_6.5.22_images/IRIX 6.5 Installation Tools June 1998.img` |
| Foundation 1 | Target 5 (data CD, swappable) | `software_library/irix_6.5.22_images/IRIX 6.5 Foundation 1.img` |
| Foundation 2 | Target 5 (swapped in) | `software_library/irix_6.5.22_images/IRIX 6.5 Foundation 2.img` |
| Applications | Target 5 (swapped in) | `software_library/irix_6.5.22_images/SGI IRIX 6.5 Applications 2004 April.img` |
| Overlays 1 of 3 | Target 5 (swapped in) | `software_library/irix_6.5.22_images/IRIX 6.5.22 Overlays 1 of 3.img` |
| Overlays 2 of 3 | Target 5 (swapped in) | `software_library/irix_6.5.22_images/IRIX 6.5.22 Overlays 2 of 3.img` |
| Overlays 3 of 3 | Target 5 (swapped in) | `software_library/irix_6.5.22_images/IRIX 6.5.22 Overlays 3 of 3.img` |

**IMPORTANT:** Do NOT attach more than 2 CD-ROM drives simultaneously.
The miniroot init script hangs indefinitely with 3+ CD-ROMs. Additional
CDs are swapped onto SCSI target 5 via QEMU monitor `change` command
between installation rounds.

---

## Common Setup

### Create Disk Image

All versions need a 2GB qcow2 disk (qcow2 required for snapshots):

```
harness_disk action=create path=/workspace/irix_disk.qcow2 size_mb=2048
```

### NVRAM Settings

```
nvram_set machine=<machine> variable=console value=d      # Serial console
nvram_set machine=<machine> variable=autoload value=N     # Stop at PROM menu
```

Where `<machine>` is `indigo2` for IRIX 5.3/6.2 or `indy` for IRIX 6.5.

---

## IRIX 5.3 Installation (Indigo2)

### Phase 1: Boot and Partition

Start a session with the disk and CD:

```python
qemu_session_start(
    machine="indigo2",
    scsi_drives=[
        "/workspace/irix53_disk.qcow2",
        "software_library/IRIX 5.3 All Indigo2 IMPACT - 812-0119-010.efs.img:cdrom"
    ],
    autoload=False, boot_wait=120
)
```

At the System Maintenance Menu, enter Command Monitor (option 5) and boot sash:

```
>> boot -f dksc(0,4,8)sashARCS
```

From sash, list the CD's stand directory and run fx:

```
sash: ls dksc(0,4,7)stand
sash: dksc(0,4,7)stand/fx.ARCS -x
```

**Note:** IRIX 5.3's fx is in `stand/fx.ARCS` (no `.ARCS` suffix in the
volume header — it's only on the EFS filesystem in partition 7).

Accept defaults (dksc, controller 0, drive 1), then use `auto` to partition:

```
fx> a
about to destroy data on disk dksc(0,1,)! ok? yes
```

fx formats, exercises, and writes the label. Exit with `exi`.

### Phase 2: Install

Back at the System Maintenance Menu, select option 2 (Install System Software).
Accept the default Local CD-ROM source and press Enter through the prompts.

The miniroot copies to disk, boots the kernel, and asks to create filesystems:

```
Make new file system on /dev/dsk/dks0d1s0 [yes/no/sh/help]: yes
Are you sure? [y/n] (n): y
```

Repeat for `/dev/dsk/dks0d1s6` (usr partition). The installer launches.

### Phase 3: Handle Incompatible Packages

The default package selection has conflicts because the CD targets IMPACT
graphics. Override the compatibility check:

```
Inst> admin
Admin> set rulesoverride on
Admin> return
Inst> keep *
Inst> install default
Inst> go
```

Installation proceeds to 100%. Exit:

```
Inst> quit
```

It builds ELF inventory, runs rqs, and reconfigures the kernel (with harmless
gfxstubs multiply-defined warnings). At the restart prompt:

```
Ready to restart the system. Restart? { (y)es, (n)o, (sh)ell, (h)elp }: yes
```

### Phase 4: Boot from Disk

Stop the session. Set NVRAM for disk boot:

```
nvram_set machine=indigo2 variable=autoload value=Y
```

Start a new session with just the disk:

```python
qemu_session_start(
    machine="indigo2",
    scsi_drives=["/workspace/irix53_disk.qcow2"],
    autoload=True,
    extra_args="-icount shift=0,sleep=off",
    boot_wait=120
)
```

Expected: fsck runs (fixing minor issues from first boot), then login prompt.

```
IRIX IRIS 5.3 12201932 IP22 mips
```

### Disk Layout (IRIX 5.3)

```
part  type    blocks              Megabytes
  0: efs     4096 + 51200         2 + 25      ← root (/)
  1: raw    55296 + 81920        27 + 40      ← swap
  6: efs   137216 + 4057088      67 + 1981    ← /usr
  8: volhdr     0 + 4096          0 + 2       ← volume header
 10: volume    0 + 4194304        0 + 2048    ← entire disk
```

### Snapshots on irix53_disk.qcow2

| Snapshot | State | CDs Required |
|----------|-------|--------------|
| inst_prompt | At Inst> prompt | CD attached |
| install_complete | After install, at restart prompt | CD attached |
| irix53_booted | Running IRIX 5.3, root shell | Disk only |

---

## IRIX 6.2 Installation (Indigo2)

### Phase 1: Boot and Partition

Same as IRIX 5.3 but with the 6.2 CD:

```python
qemu_session_start(
    machine="indigo2",
    scsi_drives=[
        "/workspace/irix62_disk.qcow2",
        "software_library/irix_6.2_images/IRIX 6.2 (Part 1 of 2) - 812-0469-001.efs.img:cdrom"
    ],
    autoload=False, boot_wait=120
)
```

Boot sash and run fx exactly as for IRIX 5.3:

```
>> boot -f dksc(0,4,8)sashARCS
sash: dksc(0,4,7)stand/fx.ARCS -x
fx> a → yes
fx> exi
```

### Phase 2: Install

Select option 2 from the PROM menu. The miniroot boots and asks to create
the root filesystem:

```
Make new file system on /dev/dsk/dks0d1s0 [yes/no/sh/help]: yes
Are you sure? [y/n] (n): y
Do you want an EFS or an XFS filesystem? [efs/xfs]: efs
```

**Note:** IRIX 6.2 offers both EFS and XFS. EFS is recommended for this era.
Unlike IRIX 5.3, IRIX 6.2 puts everything on one partition (no separate /usr).

The installer shows a README about upgrading from previous IRIX versions.
Skip the startup script (option 2 or answer "no" to each check).

### Phase 3: Install Packages

No conflicts — the 6.2 CD is compatible with IP22. Just run:

```
Inst> go
```

Installation completes. Exit:

```
Inst> quit
```

Kernel is built and system offers restart.

### Phase 4: Boot from Disk

Same as IRIX 5.3 — stop session, set autoload=Y, boot with just the disk.

```
IRIX IRIS 6.2 03131015 IP22
```

### Disk Layout (IRIX 6.2)

```
part  type    blocks                Megabytes
  0: efs     266240 + 3928064      130 + 1918   ← root (/)
  1: raw       4096 + 262144         2 + 128    ← swap
  8: volhdr       0 + 4096           0 + 2      ← volume header
 10: volume      0 + 4194304         0 + 2048   ← entire disk
```

### Snapshots on irix62_disk.qcow2

| Snapshot | State | CDs Required |
|----------|-------|--------------|
| install_complete | After install, at restart prompt | CD attached |
| irix62_booted | Running IRIX 6.2, root shell | Disk only |

---

## IRIX 6.5 Installation (Indy)

### Phase 1: Boot and Partition

Start a session with the disk and both CDs (Installation Tools + Foundation 1):

```python
qemu_session_start(
    machine="indy",
    scsi_drives=[
        "/workspace/irix65_disk.qcow2",
        "software_library/irix_6.5.22_images/IRIX 6.5 Installation Tools June 1998.img:cdrom",
        "software_library/irix_6.5.22_images/IRIX 6.5 Foundation 1.img:cdrom"
    ],
    autoload=False, boot_wait=120
)
```

**IMPORTANT:** Do NOT attach more than 2 CD-ROM drives. The miniroot init
script hangs indefinitely with 3+ CD-ROMs.

At the System Maintenance Menu, enter Command Monitor (option 5) and boot sash:

```
>> boot -f dksc(0,4,8)sashARCS
```

From sash, run fx:

```
sash: dksc(0,4,7)stand/fx.ARCS -x
```

Accept defaults (dksc, controller 0, drive 1), then use `auto` to partition:

```
fx> a
about to destroy data on disk dksc(0,1,)! ok? yes
```

fx formats, exercises, and writes the label. Exit with `exi`.

### Phase 2: Miniroot Boot & Filesystem Creation

Back at the System Maintenance Menu, select option 2 (Install System Software).
Accept the default Local CD-ROM source and press Enter through the prompts.

The miniroot copies to disk and the kernel boots. After the kernel starts
(look for "audio: AES receiver not responding."), the init script runs
SCSI probing. This leads to a miniroot status prompt:

```
c, f, r, or a
```

Send `c` to continue. The installer detects the fresh disk and asks to create
an XFS filesystem:

```
Make new file system on /dev/dsk/dks0d1s0 [yes/no/sh/help]: yes
Are you sure? [y/n] (n): y
About to remake (mkfs) file system on: /dev/dsk/dks0d1s0
Block size of filesystem 512, 1024, 2048, or 4096 bytes? 4096
```

**Note:** IRIX 6.5 always uses XFS — no EFS/XFS choice is offered. Unlike
IRIX 5.3, there is no separate /usr partition. For disks <4GB, block size
512 is more space-efficient, but 4096 is the safe default per SGI's
installation manual.

A README is displayed — press Q to dismiss. At the startup script prompt,
enter `2` (Ignore and go to Inst).

### Phase 3: Install Packages (Per-CD with Reconciliation)

The Installation Tools CD (SCSI target 4) contains the installer and miniroot
but NOT the OS packages. The OS is spread across 7 CDs. Per-CD `go` is
required because QEMU's SCSI media change causes inst's `verify_volume()`
to hang during a running `go` (see Known Issues below).

**CD order:**
Foundation 1, Foundation 2, Applications, Overlays 1-3, InstTools

**Note on scanning:** Ian Mapleson's guide recommends scanning all CDs with
`open` before installing. This doesn't work with QEMU because all CDs share
the same mount point (`/CDROM/dist`) — inst registers the path, not cached
file content, so only the last-mounted CD's files are accessible. Instead,
we install per-CD and use a reconciliation pass to catch cross-CD deps.

#### Per-CD install

For each CD, mount it, set as active distribution, select packages, install:

```
Inst> sh
# umount /CDROM 2>/dev/null; true
# mount -r /dev/dsk/dks0d5s7 /CDROM    (or dks0d4s7 for InstTools)
# exit
Inst> from /CDROM/dist
Inst> keep *
Inst> install standard
Inst> install prereqs
Inst> go
```

Between CDs on target 5, swap via QEMU monitor:

```
(qemu) change scsi0-cd5 /path/to/next_cd.img
```

Wait 2-3 seconds for SCSI UNIT ATTENTION to settle before mounting.

Each `go` only installs packages from the currently mounted CD. After each
round, installed packages are on disk, so subsequent CDs see them as
satisfied prerequisites.

#### Reconciliation pass

After all CDs are installed, iterate through all CDs again with the same
`from` / `keep *` / `install standard` / `install prereqs` / `go` sequence.
This picks up packages that were skipped on the first pass because their
prerequisites (from later CDs) weren't yet installed (e.g., Foundation 1's
`x_eoe.sw.xdps` needing Foundation 2's `dps_eoe.sw.dpsfonts`).

**Key behaviors:**
- `install standard` skips already-installed packages, so each `go` only
  installs new packages from the current CD.
- Foundation 1 may show conflicts on first install (resolved automatically
  with `conflicts 1a`).
- InstTools and some CDs may have nothing new to install ("No matches for
  standard") — this is normal.
- Overlays supersede base packages from Foundation 1/2.

After all CDs are installed, exit:

```
Inst> quit
```

It builds ELF inventory, runs rqs, and reconfigures the kernel. At the
restart prompt:

```
Ready to restart the system. Restart? { (y)es, (n)o, (sh)ell, (h)elp }: yes
```

### Phase 4: Boot from Disk

Stop the session. Set NVRAM for disk boot:

```
nvram_set machine=indy variable=autoload value=Y
```

Start a new session with just the disk:

```python
qemu_session_start(
    machine="indy",
    scsi_drives=["/workspace/irix65_disk.qcow2"],
    autoload=True,
    extra_args="-icount shift=0,sleep=off",
    boot_wait=120
)
```

Expected: fsck runs, then login prompt.

```
IRIX IRIS 6.5 05190003 IP22
```

### Disk Layout (IRIX 6.5)

```
part  type    blocks                Megabytes
  0: xfs     266240 + 3928064      130 + 1918   ← root (/)
  1: raw       4096 + 262144         2 + 128    ← swap
  8: volhdr       0 + 4096           0 + 2      ← volume header
 10: volume      0 + 4194304         0 + 2048   ← entire disk
```

### Snapshots on irix65_disk.qcow2

| Snapshot | State | CDs Required |
|----------|-------|--------------|
| install_complete | All 7 CDs installed, kernel built | InstTools + last data CD |
| irix65_booted | Running IRIX 6.5, root shell | Disk only |

### Why Per-CD Install with Reconciliation

The traditional SGI installation procedure (per Ian Mapleson's guide) loads
ALL CD distributions into inst upfront via `open`, then runs a single `go`
that reads from whichever CD it needs, prompting for disc swaps as needed.

This doesn't work in QEMU for two reasons:

1. **`open /CDROM/dist` doesn't accumulate across CD swaps.** All CDs
   share the same mount point path, and inst registers the path — not
   cached file content. Only the last-mounted CD's files are accessible.

2. **Mid-install CD swaps hang inst.** QEMU's monitor `change` command
   triggers SCSI UNIT ATTENTION. inst's `verify_volume()` hangs
   permanently after the UA during a running `go`.

Our approach: install per-CD, then reconcile:
1. **Per-CD `go`** — each `go` only reads from the currently mounted CD,
   avoiding mid-install swaps. Cross-CD prerequisites are auto-resolved
   by deselecting packages whose deps aren't yet installed.
2. **Reconciliation pass** — after all CDs are installed, iterate through
   all CDs again. Packages that were deselected on the first pass (due to
   missing cross-CD prereqs) are now installable since the prereqs from
   later CDs are on disk.

---

## Performance Notes

- **`-icount shift=0,sleep=off`** is critical for kernel boot speed.
  Without it, kernel scheduling is throttled to wall-clock time.
  Has NO effect on PROM boot (PROM polls, never uses WAIT).
- **PROM boot: ~30s** with 1 disk. ~90s with disk + CD.
- **Miniroot init (6.5): ~5-10 minutes** after kernel boot (SCSI probing).
- **Installation: ~2-5 minutes** with icount.
- **Disk boot to login: ~45s** with icount.

## Critical Implementation Detail: cache=writethrough

All QEMU `-drive` arguments use `cache=writethrough` to ensure writes are
flushed synchronously to the host disk image file. Without this, QEMU's
default `cache=writeback` can lose data when a session is killed (SIGKILL),
because buffered writes in the host page cache are discarded.

This was the root cause of a bug where volume header data (sash binary) was
lost after IRIX 5.3 installation — the directory entry was written but the
actual file data was still in the page cache when the session was killed.

## Known Issues

1. **3+ CD-ROMs cause miniroot init hang (6.5 only).** With 3 CD-ROM drives
   attached, the miniroot init script hangs. Use 2 CDs maximum.

2. **SCSI alert on CD-ROM mount:** `[Alert] Illegal request` — non-fatal.

3. **Snapshot serial restore:** May not produce serial output (SCC state issue).

4. **IRIX 5.3 IMPACT CD incompatibility:** Packages flagged as incompatible
   with XL graphics. Use `rulesoverride on` in inst admin menu.

5. **"miniroot install failed" message (6.5):** Use 'f' to fix state or 'r'
   to reload miniroot.

6. **Mid-install CD swap hangs inst (6.5).** QEMU's `change` command triggers
   SCSI UNIT ATTENTION. inst's `verify_volume()` hangs permanently after the
   first UA during a running `go`. Workaround: CD-by-CD installation (see
   Phase 3 above).

7. **IRIX 6.5 miniroot hangs on indigo2.** The 6.5 miniroot kernel boots on
   indigo2 (`IRIX Release 6.5 IP22`) but stalls after "audio: AES receiver
   not responding." — never reaches the filesystem prompt or `Inst>`. The
   indigo2 kernel probes for GFE (GIO Fiber Ethernet) which isn't emulated.
   IRIX 5.3 and 6.2 work on indigo2 because their kernels probe fewer
   devices. Use `indy` for IRIX 6.5.

## Installed Disk Images

| Disk | IRIX | Machine | CDs Installed | Key Snapshot | Time |
|------|------|---------|---------------|--------------|------|
| `irix53_disk.qcow2` | 5.3 | indigo2 | 1 (IMPACT) | `irix53_booted` | ~130s |
| `irix62_disk.qcow2` | 6.2 | indigo2 | 1 (Part 1) | `irix62_booted` | ~113s |
| `irix65_disk.qcow2` | 6.5 | indy | 8 (full set) | `irix65_booted` | ~205s |
