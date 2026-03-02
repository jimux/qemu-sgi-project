# pyirix_qemu

QEMU orchestration tools for SGI/IRIX emulation. Provides `QEMUSession` for
serial console automation, `qemu-img` wrappers, a fully automated IRIX
installation harness, and a disc image catalog for locating software libraries.

For general SGI/IRIX Python tools (EFS filesystem reader, distribution package
analysis) with no QEMU dependency, see the companion `pyirix` package.

---

## Dependencies

- Python 3.8+
- Standard library only
- **QEMU** built with `--target-list=mips64-softmmu` (SGI MIPS target); the
  `qemu-system-mips64` and `qemu-img` binaries must be available either on
  `PATH` or in a `qemu/build*` subdirectory relative to the project root
- **`pyirix`** package (EFS reader and distribution package analysis used by the install harness)

---

## Installation

```bash
pip install -e /path/to/workspace/pyirix_qemu
# or
export PYTHONPATH=/path/to/workspace:$PYTHONPATH
```

---

## Configuration

`pyirix_qemu.boot_harness` auto-detects the QEMU binary at import time:

```python
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # root of project checkout

# Checked in order: qemu/build-mac, qemu/build-linux, qemu/build
# Falls back to PATH lookup if none found
QEMU_BIN = _find_qemu_bin()
```

Override by setting the `QEMU_BIN` environment variable, or by patching
`pyirix_qemu.boot_harness.QEMU_BIN` before creating a session.

```python
MACHINE_PROM_MAP = {
    "indy":          ("ip24", "Indy_ip24prom.070-9101-011.bin"),
    "indigo2":       ("ip22", None),   # None = first .bin in the subdir
    "indigo2-r10k":  ("ip28", None),
    "indigo2-r8k":   ("ip26", None),
    "indigo":        ("ip20", None),
}
```

PROM images are resolved relative to `PROJECT_ROOT/PROM_library/bins/cpu/`.

---

## `pyirix_qemu.boot_harness` — QEMUSession

Serial console interaction engine for QEMU SGI machines.

### Class: `QEMUSession`

```python
class QEMUSession:
    def __init__(
        self,
        machine="indy",          # QEMU machine type
        ram_mb=64,               # RAM in megabytes (minimum 64)
        prom=None,               # PROM binary path (auto-resolved if None)
        scsi_drives=None,        # List of drive specs (see below)
        debug_flags=None,        # -d flags (e.g. "unimp,trace:sgi_hpc3_*")
        snapshot=None,           # Snapshot name to load (-loadvm)
        extra_args=None,         # Additional QEMU command-line arguments
        bail_patterns=None,      # Extra regex patterns that abort wait_for()
        repeat_threshold=3,      # Abort after N identical consecutive lines
        debug_log_path=None,     # Path for -D debug log
        serial_log_path=None,    # Path to write serial transcript
    ): ...
```

**Drive spec format** — append a suffix to the image path:
- `"disk.qcow2"` — read-write disk at SCSI ID 1 (IDs 1, 2, 3 in order)
- `"cd.img:cdrom"` — CD-ROM media at SCSI ID 4 (IDs 4, 5, 6, 7 in order)
- `"backup.qcow2:ro"` — read-only disk

### Context manager usage

```python
from pyirix_qemu.boot_harness import QEMUSession

with QEMUSession(
    machine="indy",
    ram_mb=128,
    scsi_drives=["disk.qcow2", "install.img:cdrom"],
    extra_args=["-icount", "shift=0,sleep=off"],
) as q:
    # Wait for PROM System Maintenance Menu
    result = q.wait_for(r"Option\?", timeout=5, max_wait=90)
    if result.matched:
        q.send("1\r")  # Boot from disk

    # Wait for login prompt
    result = q.wait_for(r"login:", timeout=30, max_wait=600)
    if result.matched:
        q.send("root\r")

    # Save a snapshot
    q.save_snapshot("irix65_booted")
```

### Methods

| Method | Description |
|--------|-------------|
| `wait_for(pattern, timeout=3, max_wait=120, bail_on=None)` | Wait for regex in serial output. Returns `WaitResult(matched, output, bail_reason)`. |
| `send(text)` | Send text to serial console; processes `\r`, `\n` escape sequences. |
| `send_monitor(cmd, timeout=10)` | Send HMP monitor command, return response text. |
| `change_media(scsi_unit, image_path)` | Swap CD-ROM media via monitor. |
| `save_snapshot(name)` | Save VM snapshot (requires qcow2 drive). |
| `collect(duration=3)` | Collect serial output for a fixed duration. |
| `close()` | Shut down QEMU and clean up temp files. |

### `WaitResult`

```python
result = q.wait_for(r"Inst>")
result.matched      # True if pattern was found
result.output       # All accumulated serial text
result.bail_reason  # Why wait stopped (None if matched)

# Also unpacks as a tuple
matched, output, bail = q.wait_for(r"Inst>")
```

### `DEFAULT_BAIL_PATTERNS`

`wait_for()` automatically aborts on these patterns (in addition to any
`bail_on` patterns you supply):

```python
DEFAULT_BAIL_PATTERNS = [
    r"PANIC", r"panic:", r"bus error", r"Bus Error",
    r"Unable to boot", r"not syncing", r"Kernel panic",
]
```

---

## `pyirix_qemu.disk_manager` — qemu-img Wrappers

Thin wrappers around `qemu-img` for disk creation, conversion, and snapshot
management.

### Functions

```python
from pyirix_qemu.disk_manager import (
    create_disk,        # Create a new disk image
    convert_disk,       # Convert between formats
    disk_info,          # Get image metadata
    list_snapshots,     # List snapshots in a qcow2
    delete_snapshot,    # Delete a snapshot
    create_backed_disk, # Create a qcow2 overlay
)

# Create a 4 GB qcow2 disk
create_disk("/tmp/irix.qcow2", size_mb=4096, fmt="qcow2")

# Convert raw → qcow2
convert_disk("/tmp/irix.img", dst="/tmp/irix.qcow2", fmt="qcow2")

# Inspect
info = disk_info("/tmp/irix.qcow2")
print(info["virtual-size"], info["actual-size"])

# List snapshots
for snap in list_snapshots("/tmp/irix.qcow2"):
    print(snap["tag"], snap["date"])

# Overlay for disposable testing (writes go to overlay, backing stays clean)
create_backed_disk("/tmp/irix.qcow2", "/tmp/test_overlay.qcow2")
```

### CLI

```bash
python -m pyirix_qemu.disk_manager create --path irix.qcow2 --size 4096
python -m pyirix_qemu.disk_manager convert irix.img --format qcow2
python -m pyirix_qemu.disk_manager info irix.qcow2
python -m pyirix_qemu.disk_manager snapshots irix.qcow2
python -m pyirix_qemu.disk_manager delete-snapshot irix.qcow2 old_snap
python -m pyirix_qemu.disk_manager overlay irix.qcow2 --overlay /tmp/test.qcow2
```

---

## `pyirix_qemu.install` — IRIX Installation Harness

Fully automated IRIX installation: disk creation, partitioning with `fx`,
EFS/XFS filesystem creation, package selection, and reboot verification.
Disc images are resolved dynamically by scanning a software library directory.

### High-level entry points

```python
from pyirix_qemu.install.irix import install_irix, install_addon, IRIXShell

# Full installation (disk creation through package install)
install_irix(
    version="6.5",
    disk="/workspace/irix.qcow2",
    machine="indy",
    ram_mb=128,
)

# Add software to an existing install
install_addon(
    disk="/workspace/irix.qcow2",
    dist_image="/path/to/mipspro.img",
    packages=["c_dev", "c++_dev", "compiler_eoe"],
)

# Run commands in a live IRIX session
shell = IRIXShell(method="session", session_id="my-session")
shell.run("uname -a")
shell.run("cc -version 2>&1")
```

### Install attempt helpers

```python
from pyirix_qemu.install.irix import (
    full_install_attempt,   # Boot → PROM menu → miniroot → Inst>
    iterate_from_snapshot,  # Resume from snapshot → Inst>
    boot_to_prom_menu,      # Wait for System Maintenance Menu
    install_miniroot,       # Navigate PROM to boot miniroot kernel
    wait_for_installer,     # Wait for Inst> after miniroot boots
)

# Full attempt from cold boot
result = full_install_attempt(
    disk_path="/workspace/irix.qcow2",
    version="6.5",
    machine="indy",
    ram_mb=128,
)
print(result["success"], result["duration"], result["bail_reason"])

# Resume from a previously saved snapshot (much faster)
result = iterate_from_snapshot(
    snapshot_name="at_prom_menu",
    disk_path="/workspace/irix.qcow2",
    version="6.5",
)
```

### CLI

```bash
# Install IRIX 6.5
python -m pyirix_qemu.install.irix 6.5 --disk irix.qcow2

# Install IRIX 6.5.5 on a custom disk
python -m pyirix_qemu.install.irix 6.5.5 --disk /tmp/irix655.qcow2

# Verify an existing install (no reinstall)
python -m pyirix_qemu.install.irix 6.5 --verify-only
```

---

## End-to-End Example

```python
from pyirix_qemu.disk_manager import create_disk
from pyirix_qemu.boot_harness import QEMUSession
from pyirix_qemu.install.irix import install_irix

DISK = "/workspace/irix65.qcow2"
INSTALL_CD = "/path/to/IRIX_6.5_Install_Tools.img"
FOUNDATION1 = "/path/to/IRIX_6.5_Foundation_1.img"

# Step 1: Create disk image
create_disk(DISK, size_mb=4096)

# Step 2: Automated installation
install_irix(version="6.5", disk=DISK, machine="indy", ram_mb=128)

# Step 3: Boot and save a snapshot
with QEMUSession(
    machine="indy", ram_mb=128,
    scsi_drives=[DISK],
    extra_args=["-icount", "shift=0,sleep=off"],
) as q:
    q.wait_for(r"login:", timeout=30, max_wait=300)
    q.send("root\r")
    q.wait_for(r"#", timeout=10, max_wait=60)
    q.save_snapshot("irix65_booted")
    print("Snapshot saved!")

# Step 4: Resume from snapshot (fast path)
with QEMUSession(
    machine="indy", ram_mb=128,
    scsi_drives=[DISK],
    snapshot="irix65_booted",
    extra_args=["-icount", "shift=0,sleep=off"],
) as q:
    q.collect(duration=3)  # Drain initial output
    q.send("uname -a\r")
    result = q.wait_for(r"#", timeout=5, max_wait=30)
    print(result.output)
```

---

## Module Summary

| Module | Purpose |
|--------|---------|
| `pyirix_qemu.boot_harness` | `QEMUSession` serial console engine; PROM/QEMU binary discovery |
| `pyirix_qemu.disk_manager` | `qemu-img` wrappers: create, convert, info, snapshots, overlays |
| `pyirix_qemu.install.irix` | Fully automated IRIX installation harness; `IRIXShell` for live sessions |
| `pyirix_qemu.catalog.images` | In-memory disc image scanner and classifier |
| `pyirix_qemu.catalog.library` | SQLite-backed library index with search and staging |
| `pyirix_qemu.deadcode` | Archived deprecated code (reference only; do not import) |
