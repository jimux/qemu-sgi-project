# install_addon() Issues and Lessons Learned

## Overview

The `install_addon()` function in `tools/install_irix.py` automates
installing additional software onto an existing IRIX disk. Several issues
were discovered during MIPSpro dev tools installation attempts.

## Issue 1: NVRAM Autoboot Path vs SCSI Target Assignment

**Symptom:** After copying the base disk and booting with an addon image
as the second SCSI drive, the PROM fails with:
```
scsi(0)disk(1)rdisk(0)partition(0)/unix: no such file or directory.
Autoboot failed.
```

**Root cause:** The NVRAM `SystemPartition` and `OSLoadPartition` point to
`scsi(0)disk(1)rdisk(0)partition(8)`. When `scsihostid=0`, the first SCSI
drive is target 1. This is correct — but the function must ensure the OS
disk is always passed as the **first** entry in `scsi_drives`, and the
addon as the **second**.

**Fix needed:** `install_addon()` should verify NVRAM boot paths match
the drive arrangement, or use `autoload=False` and manually navigate
PROM menus to boot from the correct disk.

## Issue 2: Snapshot RAM Size Mismatch

**Symptom:** Restoring a snapshot fails with:
```
qemu-system-mips64: Size mismatch: sgi.ram: 0x10000000 != 0x4000000
```

**Root cause:** The `install_complete` snapshot on `irix65_disk.qcow2` was
created with `ram_mb=256`, but `install_addon()` defaults to 64MB. QEMU
requires the RAM size to match exactly when restoring snapshots.

**Fix needed:** `install_addon()` should either:
1. Query the snapshot's RAM size before restoring
2. Accept `ram_mb` as a parameter
3. Default to 256MB (the size used by `harness_install`)

## Issue 3: qcow2 Copy + Snapshot Restore Corruption

**Symptom:** After `cp base.qcow2 copy.qcow2`, restoring a snapshot on
the copy causes kernel PANIC:
```
Fatal error on root filesystem
SCSI sense=5/33/0 (Illegal LBA)
```

**Root cause:** qcow2 files contain internal snapshot state with block
mappings. A raw `cp` copies the file but the snapshot metadata may
reference internal offsets that don't survive correctly. For qcow2 files
with snapshots, use `qemu-img create -b base.qcow2 -F qcow2 copy.qcow2`
(overlay) or `qemu-img convert` instead.

**Current workaround:** Boot fresh (no snapshot restore) with `autoload=True`,
which boots from the installed kernel without needing snapshot state.

## Issue 4: Overlay/Base Product Prerequisite Deadlock

**Symptom:** When a combined dist image contains both base and overlay
versions of the same product (e.g., `dev.sw.lib` base + `dev.sw.lib`
overlay), the IRIX `inst` installer refuses to install either:
- The overlay requires the base to be installed first
- `inst` sees the overlay as newer and won't install the "older" base
- Neither can be installed, creating a deadlock

**Root cause:** IRIX's overlay model assumes base products are installed
from a base OS CD, then overlays are applied from overlay CDs. When both
are on the same dist, `inst` can't resolve the ordering.

**Fix approaches:**
1. **Two-pass install:** First install only base products, then install
   overlays in a second `inst` session
2. **Separate images:** Build separate base and overlay images
3. **Avoid mixing:** Only include overlay products if the base is already
   installed on the target disk

## Issue 5: FamilyResolver Greedy Selection

**Symptom:** `cmd_build_dist()` with FamilyResolver selects the MIPSpro
All-Compiler CD (812-0925-001, May 1999) instead of the newer DTM
7.3.1.3m (812-0980-003) for compiler products.

**Root cause:** FamilyResolver uses greedy set cover — it picks the image
covering the **most** requested products first. The All-Compiler CD
covers more product names than the DTM, even though the DTM has newer,
compatible versions.

**Result:** The All-Compiler CD's `compiler_eoe.sw.lib` (timestamp
1275539210, ~2010) is incompatible with the installed `eoe.sw.base`
(timestamp 1289434520, ~2010, IRIX 6.5.22). The version mismatch causes:
```
compiler_eoe.sw.lib (1275539210) is incompatible with eoe.sw.base (1289434520)
```

**Fix needed:** FamilyResolver should prefer images with version codes
matching or exceeding the target system's version, rather than just
maximizing product coverage.

## Issue 6: inst Cascading Dependency Chains

**Symptom:** Selecting `c_dev.sw.c` for install triggers a dependency
chain that eventually requires products from CDs not on the dist:
```
c_dev.sw.c → compiler_dev.sw.base → dev.sw.lib (overlay) → dev.sw.lib (base, missing)
```

**Root cause:** Compiler products have deep dependency trees. A single
missing base product blocks the entire chain. The combined dist must
include ALL dependencies, not just the directly-requested products.

**Lesson:** When building combined dist images, trace the full dependency
tree of each product and ensure all prerequisites are included.

## Recommended Approach for Dev Tools Installation

Based on these lessons, the most reliable approach is:

1. **Use pre-extracted alldev directories** rather than FamilyResolver-
   selected EFS images. The curated directories (e.g.,
   `alldev/MipsPro-7.4.3m/`) contain known-compatible product sets.

2. **Include foundation products** (devf_13, developmentlibraries) that
   provide base prerequisites like `dev.sw.lib`, `complib.sw.*`.

3. **Order matters:** Place base/foundation products first in the combine
   order, compiler overlays last. `collect_dist_files()` uses "last wins"
   for duplicate filenames.

4. **Boot fresh, don't restore snapshots** on copied disks. Use
   `autoload=True` instead of snapshot restore.

5. **Use 256MB RAM** to match the snapshot state in the base disk, even
   if not restoring snapshots (avoids potential issues).

## Files Referenced

| File | Relevant function/line |
|------|----------------------|
| `tools/install_irix.py` | `install_addon()` ~line 1601 |
| `tools/irix_pkg_analyzer.py` | `cmd_build_dist()` ~line 2323 |
| `tools/combine_dist.py` | `extract_dist_from_image()`, `collect_dist_files()` |
