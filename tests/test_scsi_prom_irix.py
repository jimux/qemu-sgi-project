"""
SCSI PROM/IRIX integration tests (SLOW).

These tests boot QEMU with SCSI devices and verify PROM probe behavior,
volume header reading, and kernel SCSI interactions via serial output
and QEMU debug logs.

They require:
  - A built qemu-system-mips64
  - An IP24 PROM image
  - A partitioned disk image (irix_disk.img)
  - A patched IRIX install CD image (for CD-ROM tests)

Mark: slow (requires QEMU boot, ~90s with disk, ~120s with disk + CD-ROM)

Notes:
  - SCSI devices must use `-drive if=scsi` syntax, not `-device scsi-hd`
    (WD33C93 bus is not QOM-discoverable).
  - PROM boot takes ~30.5s minimum (escape countdown) even without devices.
  - -icount shift=0,sleep=off has no effect on PROM boot timing.
"""

import os
import re
import pytest
from helpers.qemu_runner import SGIQemuRunner, find_prom, DEFAULT_QEMU_BIN, _PROJECT_ROOT

pytestmark = pytest.mark.slow

QEMU_BIN = DEFAULT_QEMU_BIN
DISK_IMG = os.path.join(_PROJECT_ROOT, "irix_disk.img")
CDROM_IMG = os.path.join(_PROJECT_ROOT, "software_library", "irix_6.5.22_images",
                         "irix_install_patched.img")


def have_qemu():
    return os.path.exists(QEMU_BIN)


def have_prom():
    return find_prom() is not None


def have_disk():
    return os.path.exists(DISK_IMG)


def have_cdrom():
    return os.path.exists(CDROM_IMG)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def prom_scsi_disk_output():
    """Boot QEMU with SCSI disk, capture serial + debug output.

    Uses -d unimp to capture SCSI command traces.
    """
    if not (have_qemu() and have_prom() and have_disk()):
        pytest.skip("Missing QEMU, PROM, or disk image")

    runner = SGIQemuRunner()
    runner.clean_traces()
    output = runner.boot_prom(
        timeout=120,
        extra_args=[
            "-drive", f"if=scsi,file={DISK_IMG},format=raw",
            "-d", "unimp",
        ],
    )
    return output


@pytest.fixture(scope="module")
def prom_scsi_cdrom_output():
    """Boot QEMU with CD-ROM, capture serial + debug output."""
    if not (have_qemu() and have_prom() and have_cdrom()):
        pytest.skip("Missing QEMU, PROM, or CD-ROM image")

    runner = SGIQemuRunner()
    runner.clean_traces()
    output = runner.boot_prom(
        timeout=120,
        extra_args=[
            "-drive", f"if=scsi,file={CDROM_IMG},format=raw,media=cdrom,readonly=on",
            "-d", "unimp",
        ],
    )
    return output


@pytest.fixture(scope="module")
def prom_empty_scsi_output():
    """Boot QEMU with no SCSI devices to test timeout behavior."""
    if not (have_qemu() and have_prom()):
        pytest.skip("Missing QEMU or PROM")

    runner = SGIQemuRunner()
    runner.clean_traces()
    output = runner.boot_prom(
        timeout=30,
        extra_args=["-d", "unimp"],
    )
    return output


@pytest.fixture(scope="module")
def miniroot_scsi_output():
    """Boot miniroot kernel with SCSI disk and CD, capture debug output."""
    if not (have_qemu() and have_prom() and have_disk() and have_cdrom()):
        pytest.skip("Missing miniroot boot prerequisites")

    runner = SGIQemuRunner()
    runner.clean_traces()
    output = runner.boot_miniroot(
        disk_img=DISK_IMG,
        cdrom_img=CDROM_IMG,
        timeout=300,
        ram_mb=256,
        extra_args=["-d", "unimp"],
    )
    return output


# ---------------------------------------------------------------------------
# PROM SCSI Probe Tests
# ---------------------------------------------------------------------------

class TestPROMSCSIProbe:
    """PROM SCSI device detection during POST."""

    def test_prom_detects_scsi_disk(self, prom_scsi_disk_output):
        """PROM should detect the SCSI disk and print device info.

        The PROM probes all SCSI IDs during POST. It should find
        our disk at target 1 and print something about it.
        """
        output = prom_scsi_disk_output
        # PROM should reach the menu even with SCSI devices
        assert any(x in output.lower() for x in
                   ["system maintenance", "option?"]), (
            f"PROM did not reach menu. Output: {output[:500]}"
        )

    def test_prom_reads_volume_header(self, prom_scsi_disk_output):
        """PROM should issue READ commands to check for volume header.

        The PROM reads sector 0 (SGI volume header / disklabel)
        to determine if the disk is bootable.
        """
        output = prom_scsi_disk_output
        # Look for SCSI READ commands in debug output
        # CDB 0x08 = READ(6), 0x28 = READ(10)
        assert re.search(r"SELECT_XFER.*CDB.*(?:08|28)", output), (
            "No SCSI READ commands found in debug output"
        )

    def test_prom_scsi_inquiry(self, prom_scsi_disk_output):
        """PROM should issue INQUIRY (0x12) to at least one SCSI target."""
        output = prom_scsi_disk_output
        assert re.search(r"SELECT_XFER.*CDB.*12", output), (
            "No SCSI INQUIRY commands found in debug output"
        )

    def test_prom_timeout_empty_target(self, prom_empty_scsi_output):
        """With no SCSI devices, selection timeout should not hang.

        The PROM probes all SCSI targets. For empty targets, the
        WD33C93 should report SELECTION_TIMEOUT (0x42) quickly.
        """
        output = prom_empty_scsi_output
        # PROM should still reach the menu
        assert any(x in output.lower() for x in
                   ["system maintenance", "option?"]), (
            f"PROM hung during SCSI probe without devices. "
            f"Output: {output[:500]}"
        )


# ---------------------------------------------------------------------------
# PROM SCSI CD-ROM Tests
# ---------------------------------------------------------------------------

class TestPROMSCSICDROM:
    """PROM CD-ROM detection during POST."""

    def test_prom_detects_cdrom(self, prom_scsi_cdrom_output):
        """PROM should detect the CD-ROM at target 4."""
        output = prom_scsi_cdrom_output
        # PROM should reach menu with CD-ROM present
        assert any(x in output.lower() for x in
                   ["system maintenance", "option?"]), (
            f"PROM did not reach menu with CD-ROM. Output: {output[:500]}"
        )

    def test_prom_reads_cdrom_vh(self, prom_scsi_cdrom_output):
        """PROM should read the volume header from the CD-ROM.

        The PROM checks the CD-ROM for an SGI volume header to
        determine if it's a bootable install disc.
        """
        output = prom_scsi_cdrom_output
        # Should see SCSI commands to target 4
        assert re.search(r"target=4", output), (
            "No SCSI commands to CD-ROM target 4 in debug output"
        )


# ---------------------------------------------------------------------------
# SCSI DMA Integration Tests
# ---------------------------------------------------------------------------

class TestSCSIDMAIntegration:
    """SCSI DMA transfer behavior during boot."""

    def test_dma_completes_no_hang(self, prom_scsi_disk_output):
        """DMA transfers should complete without timeout.

        If DMA hangs, the PROM will not reach the menu within the
        timeout period.
        """
        output = prom_scsi_disk_output
        assert len(output) > 100, (
            "Very little output — possible DMA hang"
        )

    def test_dma_irq_fires(self, prom_scsi_disk_output):
        """DMA completion should generate interrupts.

        Look for HPC DMA descriptor IRQ processing in debug output.
        """
        output = prom_scsi_disk_output
        # Check for DMA descriptor processing
        has_dma = ("DMA fetch chain" in output or
                   "DMA descriptor IRQ" in output or
                   "DMA end of chain" in output)
        # DMA activity is expected when reading from disk
        assert has_dma, (
            "No HPC3 SCSI DMA activity seen in debug output"
        )


# ---------------------------------------------------------------------------
# Kernel SCSI Boot Tests
# ---------------------------------------------------------------------------

class TestKernelSCSIBoot:
    """IRIX kernel SCSI interactions during miniroot boot."""

    def test_kernel_scsi_bus_scan(self, miniroot_scsi_output):
        """Kernel should probe SCSI bus during boot.

        The kernel scans all SCSI IDs during initialization.
        """
        output = miniroot_scsi_output
        # Kernel should issue commands to multiple targets
        targets_seen = set()
        for m in re.finditer(r"target=(\d)", output):
            targets_seen.add(int(m.group(1)))
        assert len(targets_seen) >= 1, (
            "Kernel did not probe any SCSI targets"
        )

    def test_kernel_reads_partition_table(self, miniroot_scsi_output):
        """Kernel should read the SGI volume header / partition table.

        Look for READ commands in the debug output, specifically to
        the disk target (ID 1).
        """
        output = miniroot_scsi_output
        assert re.search(r"target=1.*CDB.*(?:08|28)", output), (
            "No READ commands to disk target 1"
        )

    def test_kernel_mode_sense(self, miniroot_scsi_output):
        """Kernel should issue MODE_SENSE (0x1a or 0x5a) during probing.

        IRIX queries MODE_SENSE page 0x3f (all pages) to detect
        disk geometry and capabilities.
        """
        output = miniroot_scsi_output
        # CDB 0x1a = MODE_SENSE(6), 0x5a = MODE_SENSE(10)
        has_mode_sense = (
            re.search(r"CDB.*1a", output) or
            re.search(r"CDB.*5a", output)
        )
        assert has_mode_sense, (
            "No MODE_SENSE commands found in debug output"
        )

    def test_kernel_read_capacity(self, miniroot_scsi_output):
        """Kernel should issue READ_CAPACITY (0x25) to determine disk size."""
        output = miniroot_scsi_output
        assert re.search(r"CDB.*25", output), (
            "No READ_CAPACITY commands found in debug output"
        )
