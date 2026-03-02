# MIPSpro 7.4.4m + Development Tools Installation on IRIX 6.5.5

## Summary

Successfully installed MIPSpro 7.4.4m compilers, development libraries, and ProDev tools on an IRIX 6.5.5 system running in QEMU (Indy/IP24 emulation).

## Combo Image Build

Used `tools/combine_dist.py --suite devtools-655 build` to create a single EFS disk image combining four source directories:

| Source CD | Content | Files |
|-----------|---------|-------|
| `alldev/MIPSPro7.4.4` | Base MIPSpro 7.4 + 7.4.2m/7.4.4m overlays | 289 |
| `alldev/developmentlibraries` | dev.sw.lib, irix_dev, x_dev, gl_dev, etc. | 105 |
| `alldev/prodev` | CaseVision, WorkShop, SpeedShop, dbx | 30 |
| `6.5.5_overlays-2_812-0819-005` | 6.5.5-compatible overlay versions of dev packages | 295 |

Output: `software_library/prepackaged_combo_discs/devtools_for_655_with_base.img` (1888MB, 719 files)

## Key Finding: Dev Library Version Incompatibility

The development libraries CD (`alldev/developmentlibraries`) ships packages from the IRIX 6.5 foundation era (version 1274627333). These are flagged as "incompatible" by `inst` when installed alongside IRIX 6.5.5's `eoe.sw.base` (version 1275719131).

The solution is to include **6.5.5 Overlays CD 2** (NOT CD 1). CD 2 contains overlay versions of all dev packages (`dev_655m`, `irix_dev_655m`, `x_dev_655m`, `gl_dev_655m`, etc.) that are version-matched to 6.5.5. When both the foundation base and 6.5.5 overlays are loaded, `inst` automatically selects the compatible overlay versions.

**Important:** CD 1 (`6.5.5_install-tools-overlays-1`) only has `eoe_655f/m` and boot tools. The dev overlays are exclusively on CD 2 (`6.5.5_overlays-2`).

## Key Finding: SCSI CD-ROM vs Read-Only Disk

Large EFS images (>700MB) must be attached as **read-only SCSI disks**, not CD-ROMs:

```
# CRASHES — `:cdrom` triggers IRIX kernel SCSI probe bug with large images
scsi_drives=["disk.qcow2", "combo.img:cdrom"]

# WORKS — `:ro` attaches as read-only disk at SCSI ID 2
extra_args="-drive if=scsi,bus=0,unit=2,file=combo.img,format=raw,readonly=on,cache=writethrough"
```

The IRIX kernel's CD-ROM probe issues a READ(10) that exceeds the expected transfer size for the device, causing `wd93 SCSI Bus=0 ID=4: Too much data requested` followed by a SCSI bus reset and QEMU process crash. Using a regular SCSI disk avoids this code path entirely.

After adding `:ro` suffix support to the MCP server, the simpler syntax works:
```
scsi_drives=["disk.qcow2", "combo.img:ro"]
```

## Key Finding: Snapshot Topology Must Match

QEMU snapshots encode the full device topology. A snapshot saved with N SCSI drives cannot be restored with a different number of drives — the kernel's saved state references devices by their SCSI target ID, and missing devices cause `Illegal logical block address` panics.

**Workaround:** Boot fresh (`autoload=true`) instead of restoring snapshots when the drive configuration has changed. The installation persists on the qcow2 disk regardless.

## inst Procedure

```
mount -r /dev/dsk/dks0d2s7 /mnt    # Mount combo image at SCSI ID 2
inst
from /mnt/MIPSPro7.4.4             # Base compilers (flat layout)
open /mnt/6.5.5_overlays-2_812-0819-005  # Version-compatible dev overlays
open /mnt/developmentlibraries      # Foundation dev libs (base for overlays)
open /mnt/prodev                    # ProDev tools

install default
keep *_eoe*                         # Don't touch runtime libraries
keep inst_dev*                      # Don't touch inst itself
install c_fe.sw.c c++_fe.sw.c++     # Ensure front-ends selected

conflicts                           # Should show ~9 conflicts:
                                    # - NFS overlay missing base (skip: Na)
                                    # - ftn77 missing prereqs (skip: Na)
                                    # - ftn77_fe cascade (skip: Na)
go
```

## Installed Components

| Package | Version | Description |
|---------|---------|-------------|
| `c_fe` | 7.4.4m | C front-end (parser) |
| `c++_fe` | 7.4.4m | C++ front-end |
| `compiler_dev` | 7.4.4m | Backend tools, assembler, linker |
| `c_dev` | 7.4.4m | C development environment |
| `c++_dev` | 7.4.4m | C++ development environment |
| `dev.sw.lib` | 6.5.5m | Standard libraries (libc.a, etc.) |
| `irix_dev.sw.headers` | 6.5.5m | System headers (/usr/include/*) |
| `x_dev` | 6.5.5m | X11 development headers/libs |
| `gl_dev` | 6.5.5m | OpenGL development |
| `motif_dev` | 6.5.5m | Motif widget development |
| `WorkShop` | — | Visual debugger/profiler |
| `SpeedShop` | — | Performance analysis |
| `dbx` | — | Command-line debugger |

## Verification

```
cc -version          # → MIPSpro Compilers: Version 7.4.4m
ls /usr/include/stdio.h  # → exists
versions c_fe c++_fe compiler_dev dev irix_dev  # → all present
```

## VM Instance

Snapshot `irix655_devtools` saved in instance `irix65-desktop` — fresh boot with root shell, all dev tools installed.
