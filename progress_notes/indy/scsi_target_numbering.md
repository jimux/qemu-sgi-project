# SCSI Target Numbering in QEMU SGI Indy

## The Problem

SCSI target numbering repeatedly causes confusion in boot paths, install
scripts, and disk mounting commands. This document explains the mapping.

## How SCSI IDs Work

The SGI Indy SCSI bus supports 8 devices (IDs 0-7). The SCSI host adapter
(WD33C93B) occupies one ID, and each attached device gets another.

### NVRAM `scsihostid` Setting

The NVRAM variable `scsihostid` sets the host adapter's SCSI ID. The
default in our NVRAM files is `scsihostid=0`, meaning the host adapter
claims **SCSI ID 0**.

### QEMU Drive Attachment

When drives are passed via `scsi_drives=["disk0.img", "disk1.img:cdrom"]`,
QEMU assigns SCSI target IDs **starting from 0, skipping the host ID**:

| scsi_drives index | SCSI Target ID | Device path | Notes |
|-------------------|---------------|-------------|-------|
| 0 (first drive)   | **1**         | `dks0d1s*`  | Skips ID 0 (host) |
| 1 (second drive)  | **2**         | `dks0d2s*`  | Next available |
| 2 (third drive)   | **3**         | `dks0d3s*`  | Next available |

The host adapter is at ID 0, so the first available device ID is 1.

### PROM Boot Paths

PROM boot paths use the **SCSI target ID**, not the array index:

```
scsi(0)disk(1)rdisk(0)partition(8)/unix    # First drive, partition 8
scsi(0)disk(2)rdisk(0)partition(7)          # Second drive, partition 7
```

The NVRAM `SystemPartition` and `OSLoadPartition` typically point to
`scsi(0)disk(1)rdisk(0)partition(8)` — the **first** SCSI drive.

### IRIX Device Paths

Inside IRIX, the device naming follows the same pattern:

```
/dev/dsk/dks0d1s0    # Controller 0, target 1, partition 0 (first drive)
/dev/dsk/dks0d2s7    # Controller 0, target 2, partition 7 (second drive)
/dev/rdsk/dks0d1s0   # Raw device version
```

## Common Pitfalls

### 1. Autoboot After Adding a Second Drive

When booting with two drives, the PROM's `SystemPartition` still says
`disk(1)` which correctly points to the first (OS) drive. But if drives
are reordered or the NVRAM is stale, the PROM may try to boot from the
wrong disk.

**Symptom:** `scsi(0)disk(1)rdisk(0)partition(0)/unix: no such file or directory`

This happens when the PROM tries to load the kernel from a non-OS disk.

### 2. Mounting the Second Drive (Addon Image)

To mount the second SCSI drive (e.g., an addon dist image) inside IRIX:

```bash
mount -r /dev/dsk/dks0d2s7 /mnt    # Target 2, partition 7 (EFS)
```

**Not** `dks0d1s7` (that's the OS drive) and **not** `dks0d0s7` (that's
the host adapter ID — no device there).

### 3. install_addon() in install_irix.py

The `install_addon()` function correctly uses `dks0d2s7` for the addon
image when it's the second SCSI drive. However, it assumes:
- The OS disk is always at target 1 (first `scsi_drives` entry)
- The addon image is always at target 2 (second entry)
- The NVRAM `SystemPartition` points to `disk(1)`

### 4. scsihostid != 0

If `scsihostid` were changed to 7 (a common SGI default on real hardware),
the first drive would be at SCSI target 0, changing all paths. Our NVRAM
files use `scsihostid=0` consistently, but this should be documented as
an assumption.

## Summary Table

| Concept | Value | Explanation |
|---------|-------|-------------|
| Host adapter SCSI ID | 0 | Set by `scsihostid=0` in NVRAM |
| First drive SCSI target | 1 | First ID after host |
| First drive PROM path | `disk(1)` | Uses SCSI target ID |
| First drive IRIX device | `dks0d1s*` | `dks0d{target}s{partition}` |
| Second drive SCSI target | 2 | Next available |
| Second drive IRIX device | `dks0d2s*` | |

## Potential Bug

On real SGI hardware, the default `scsihostid` is typically 0 for Indy.
However, some configurations use 7. If someone changes `scsihostid` in
NVRAM, all the hardcoded `dks0d1` and `disk(1)` references in scripts
would break. Consider making the install scripts query `scsihostid` and
compute target IDs dynamically rather than hardcoding them.
