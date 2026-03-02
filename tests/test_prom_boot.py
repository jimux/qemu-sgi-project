"""
PROM boot integration tests (SLOW).

These tests actually boot QEMU with the SGI Indy PROM and verify
serial output. They require:
  - A built qemu-system-mips64
  - An IP24 PROM image in PROM_library/bins/cpu/ip24/

Mark: slow (requires QEMU boot, ~30.5s for escape countdown alone)

Note: PROM boot takes ~30.5s minimum due to the "Press Escape" countdown,
even with no SCSI devices. With disk: ~90s. With disk + CD-ROM: ~120s.
-icount shift=0,sleep=off has no effect on PROM boot timing.
"""

import os
import pytest
from helpers.qemu_runner import SGIQemuRunner, find_prom, DEFAULT_QEMU_BIN

pytestmark = pytest.mark.slow

QEMU_BIN = DEFAULT_QEMU_BIN


def have_qemu():
    return os.path.exists(QEMU_BIN)


def have_prom():
    return find_prom() is not None


@pytest.fixture(scope="module")
def prom_boot_output():
    """Boot QEMU to PROM menu and capture serial output (once per module)."""
    if not have_qemu():
        pytest.skip("QEMU binary not built")
    if not have_prom():
        pytest.skip("No IP24 PROM image found")

    runner = SGIQemuRunner()
    runner.clean_traces()
    output = runner.boot_prom(timeout=45)
    return output


class TestPROMBoot:
    """PROM POST and System Maintenance Menu tests."""

    def test_prom_produces_output(self, prom_boot_output):
        """PROM should produce some serial output."""
        assert len(prom_boot_output) > 0, (
            "No serial output from PROM boot"
        )

    def test_prom_reaches_menu(self, prom_boot_output):
        """PROM should reach the System Maintenance Menu.

        The menu prompt includes 'Option?' or 'Enter' or similar.
        """
        output = prom_boot_output.lower()
        menu_indicators = [
            "system maintenance",
            "option?",
            "enter 1",
            "start system",
            "install system",
        ]
        found = any(indicator in output for indicator in menu_indicators)
        assert found, (
            "PROM did not reach System Maintenance Menu. "
            f"Output ({len(prom_boot_output)} bytes): "
            f"{prom_boot_output[:500]}"
        )

    def test_prom_no_checksum_error(self, prom_boot_output):
        """PROM should not report NVRAM checksum errors."""
        assert "checksum" not in prom_boot_output.lower() or \
               "error" not in prom_boot_output.lower(), (
            "PROM reported a checksum error — NVRAM may be corrupt"
        )

    def test_prom_serial_console(self, prom_boot_output):
        """PROM output should appear on serial (not just graphics).

        If the PROM only outputs to graphics, we get empty serial output.
        """
        # The PROM should print at least some identifiable text
        assert len(prom_boot_output.strip()) > 10, (
            "PROM serial output is too short — console may be on graphics"
        )
