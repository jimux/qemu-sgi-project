"""IRIX installation automation."""
from pyirix_qemu.install.irix import (
    install_irix, install_addon, install_addon_live, IRIXShell,
    full_install_attempt, iterate_from_snapshot,
    boot_to_prom_menu, install_miniroot, wait_for_installer,
)
