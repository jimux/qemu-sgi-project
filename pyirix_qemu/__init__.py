"""pyirix_qemu — QEMU orchestration tools for SGI/IRIX emulation.

Provides QEMUSession (serial console interaction engine), qemu-img wrappers,
and the automated IRIX installation harness. Requires QEMU with SGI/MIPS support
and the pyirix package for disc image cataloguing.
"""
from pyirix_qemu.boot_harness import QEMUSession, PROJECT_ROOT
from pyirix_qemu.disk_manager import create_disk, convert_disk, disk_info
