"""
Miniroot kernel boot integration tests (SLOW).

These tests boot the IRIX 6.5 miniroot kernel via the PROM and verify
serial output. They require:
  - A built qemu-system-mips64
  - An IP24 PROM image
  - A partitioned disk image (irix_disk.img)
  - A patched IRIX install CD image

Mark: slow (requires QEMU boot, ~5 minutes)
"""

import os
import pytest
from helpers.qemu_runner import SGIQemuRunner, find_prom, DEFAULT_QEMU_BIN, _PROJECT_ROOT
from helpers.trace_parser import CP0TimerTrace

pytestmark = pytest.mark.slow
QEMU_BIN = DEFAULT_QEMU_BIN
DISK_IMG = os.path.join(_PROJECT_ROOT, "irix_disk.img")
CDROM_IMG = os.path.join(_PROJECT_ROOT, "software_library", "irix_6.5.22_images",
                         "IRIX 6.5 Installation Tools June 1998.img")


def have_miniroot_prereqs():
    return (os.path.exists(QEMU_BIN) and
            find_prom() is not None and
            os.path.exists(DISK_IMG) and
            os.path.exists(CDROM_IMG))


@pytest.fixture(scope="module")
def miniroot_boot_output():
    """Boot miniroot kernel and capture serial output (once per module)."""
    if not have_miniroot_prereqs():
        pytest.skip("Missing miniroot boot prerequisites")

    runner = SGIQemuRunner()
    runner.clean_traces()
    output = runner.boot_miniroot(
        disk_img=DISK_IMG,
        cdrom_img=CDROM_IMG,
        timeout=300,
        ram_mb=256,
    )
    return output


class TestMinirootKernelBoot:
    """IRIX 6.5 miniroot kernel boot tests."""

    def test_kernel_prints_banner(self, miniroot_boot_output):
        """Kernel should print the IRIX release banner."""
        assert "IRIX Release 6.5" in miniroot_boot_output, (
            "Kernel did not print IRIX 6.5 banner. "
            f"Output: {miniroot_boot_output[:500]}"
        )

    def test_kernel_mounts_root(self, miniroot_boot_output):
        """Kernel should print 'root on' indicating root mount."""
        assert "root on" in miniroot_boot_output, (
            "Kernel did not reach 'root on' message"
        )

    def test_kernel_scsi_error_nonfatal(self, miniroot_boot_output):
        """The SCSI 'Illegal field in CDB' error should be non-fatal.

        This error appears during device probing and the kernel
        should continue past it.
        """
        if "Illegal field in CDB" not in miniroot_boot_output:
            # Error may not appear in all configurations — that's fine
            return

        # The error should not be the last line of output
        lines = miniroot_boot_output.strip().split("\n")
        error_line = None
        for i, line in enumerate(lines):
            if "Illegal field in CDB" in line:
                error_line = i
        if error_line is not None:
            assert error_line < len(lines) - 1, (
                "SCSI error is the last line of output — kernel may have hung"
            )

    def test_kernel_no_panic(self, miniroot_boot_output):
        """Kernel should not panic during boot."""
        output_lower = miniroot_boot_output.lower()
        panic_indicators = ["kernel fault", "panic", "exception"]
        for indicator in panic_indicators:
            if indicator in output_lower:
                # Find the line for context
                for line in miniroot_boot_output.split("\n"):
                    if indicator in line.lower():
                        pytest.fail(
                            f"Kernel panic detected: {line.strip()}"
                        )


class TestMinirootTimerActivity:
    """Timer activity during miniroot boot."""

    def test_timer_fires_during_boot(self, miniroot_boot_output):
        """CP0 timer should fire many times during boot (scheduler running).

        This is the key proof that the scheduler is alive: 1000+ timer
        fires means the kernel's clock() function is being called
        regularly.
        """
        trace_path = "/tmp/cp0_timer_trace.log"
        if not os.path.exists(trace_path):
            pytest.skip("No CP0 timer trace from this boot")

        trace = CP0TimerTrace(trace_path)
        assert trace.fire_count > 1000, (
            f"Only {trace.fire_count} timer fires. Expected >1000 for "
            f"a running scheduler during miniroot boot."
        )


class TestMinirootDeviceCreation:
    """Miniroot device creation test.

    The kernel now boots past the idle loop (INT3 cascade fix, phase 9)
    and reaches "Creating miniroot devices". This is the current
    regression target.
    """

    def test_kernel_creates_devices(self, miniroot_boot_output):
        """Kernel should reach 'Creating miniroot devices'.

        After phase 9 INT3 fix, the kernel runs init and begins
        device enumeration. This test verifies we get at least
        this far.
        """
        assert "Creating miniroot devices" in miniroot_boot_output, (
            "Kernel did not reach 'Creating miniroot devices'. "
            f"Output tail: {miniroot_boot_output[-500:]}"
        )

    @pytest.mark.xfail(reason="Installer menu not yet reached — device creation may need longer timeout or SCSI fix")
    def test_miniroot_installer_menu(self, miniroot_boot_output):
        """Miniroot should reach the installer menu.

        The IRIX miniroot installer prints a menu with options like
        'Inst>' or similar. This test is marked xfail because the
        system currently hangs during device creation.
        """
        installer_indicators = [
            "inst>",
            "install system software",
            "maintenance",
            "admin>",
            "option",
        ]
        output_lower = miniroot_boot_output.lower()
        found = any(indicator in output_lower
                    for indicator in installer_indicators)
        assert found, (
            "Miniroot installer menu not reached. "
            f"Output tail: {miniroot_boot_output[-500:]}"
        )
