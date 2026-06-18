"""Regression guards for the IP54 boot-to-graphical-login configuration.

[ASSUMPTION] These assert the disk state that progress_notes/ip54/
boot_to_graphical_login.md establishes as required for zero-touch boot
to the xlogin dialog. If a test fails after intentional reconfiguration,
update the test; if it fails unexpectedly, the instance regressed.

All checks are OFFLINE reads of the instance qcow2 — safe to run while
no VM is using the disk, skipped if the instance is absent.
"""
import os
import pytest

DISK = "/workspace/vm_instances/ip54-test/disk.qcow2"
if not os.path.exists(DISK):
    DISK = os.path.join(os.path.dirname(__file__), "..",
                        "vm_instances", "ip54-test", "disk.qcow2")

pytestmark = pytest.mark.skipif(
    not os.path.exists(DISK), reason="ip54-test instance not present")


@pytest.fixture(scope="module")
def xfs():
    from pyirix.xfs.image import open_disk_image, find_xfs_partition
    from pyirix.xfs.superblock import read_superblock
    with open_disk_image(DISK) as f:
        po, _ = find_xfs_partition(f)
        sb = read_superblock(f, po)
        yield f, po, sb


def _cat(xfs, path):
    from pyirix.xfs.operations import resolve_path
    from pyirix.xfs.inode import read_inode, read_file_data
    f, po, sb = xfs
    ino = resolve_path(f, po, sb, path)
    assert ino is not None, f"missing: {path}"
    inode = read_inode(f, po, sb, ino)
    return inode, read_file_data(f, po, sb, inode)


def _fmt(xfs, path):
    inode, _ = _cat(xfs, path)
    return inode["di_format"]


class TestChkconfigFlags:
    def test_visuallogin_off(self, xfs):
        _, data = _cat(xfs, "/etc/config/visuallogin")
        assert data.strip() == b"off"

    def test_windowsystem_on(self, xfs):
        _, data = _cat(xfs, "/etc/config/windowsystem")
        assert data.strip() == b"on"


class TestNoInlineRegularFiles:
    """IRIX XFS V1 rejects FMT_LOCAL data forks on regular files
    ("corrupt inode (local format for regular file)"). Every file the
    boot path reads must be extent-format (di_format == 2)."""

    PATHS = [
        "/etc/config/visuallogin",
        "/etc/config/windowsystem",
        "/etc/config/netif.options",
        "/etc/config/static-route.options",
        "/var/X11/xdm/xdm-config",
        "/var/X11/xdm/Xsetup_0",
        "/var/X11/xdm/Xservers",
        "/etc/rc2.d/S98xdm",
    ]

    @pytest.mark.parametrize("path", PATHS)
    def test_extent_format(self, xfs, path):
        assert _fmt(xfs, path) == 2, f"{path} is inline (FMT_LOCAL)"


class TestXdmConfig:
    def test_grab_server_false(self, xfs):
        _, data = _cat(xfs, "/var/X11/xdm/xdm-config")
        assert b"grabServer:\tFalse" in data or b"grabServer: False" in data

    def test_xservers_has_gamma(self, xfs):
        _, data = _cat(xfs, "/var/X11/xdm/Xservers")
        assert b"-gamma 1.7" in data

    def test_s98xdm_present_and_executable(self, xfs):
        inode, data = _cat(xfs, "/etc/rc2.d/S98xdm")
        assert data.startswith(b"#!/sbin/sh")
        assert inode["di_mode"] & 0o111, "S98xdm not executable"


class TestBootSafety:
    def test_initdefault_multiuser(self, xfs):
        _, data = _cat(xfs, "/etc/inittab")
        assert b"is:2:initdefault:" in data

    def test_no_live_autoconfig(self, xfs):
        """S23autoconfig must stay disabled: init-time lboot would
        rebuild the kernel from irix.sm (sduart) and destroy pvuart_cn."""
        from pyirix.xfs.operations import resolve_path
        f, po, sb = xfs
        assert resolve_path(f, po, sb, "/etc/rc2.d/S23autoconfig") is None

    def test_netif_options_pvnet0(self, xfs):
        _, data = _cat(xfs, "/etc/config/netif.options")
        assert b"if1name=pvnet0" in data
        assert b"if1addr=10.0.2.15" in data
