"""
SCSI and boot performance benchmarks.

Measures wall-clock timing for various boot configurations, SCSI probe
durations, RAM size impact, and disk format overhead.

All tests are SLOW (require QEMU boot) and marked with @benchmark.
Benchmark results are emitted as structured JSON via emit_benchmark().

Run with: pytest tests/test_scsi_benchmarks.py -v -s -m benchmark
(needs -s to see benchmark output on stdout)

Key findings (see progress_notes/benchmark_results.md):
  - PROM boot baseline is ~30.5s (escape countdown), not instantaneous
  - 32MB RAM causes PROM to hang; 64MB is the minimum
  - -icount shift=0,sleep=off has no effect on PROM boot timing
  - SCSI devices must use -drive if=scsi syntax (not -device scsi-hd)
"""

import os
import re
import time
import tempfile
import subprocess

import pytest

from helpers.qemu_runner import SGIQemuRunner, find_prom, DEFAULT_QEMU_BIN, _PROJECT_ROOT
from helpers.benchmark_reporter import emit_benchmark

pytestmark = [pytest.mark.slow, pytest.mark.benchmark]

QEMU_BIN = DEFAULT_QEMU_BIN
DISK_IMG = os.path.join(_PROJECT_ROOT, "irix_disk.img")
CDROM_IMG = os.path.join(_PROJECT_ROOT, "software_library", "irix_6.5.22_images",
                         "IRIX 6.5 Installation Tools June 1998.img")


def have_qemu():
    return os.path.exists(QEMU_BIN)


def have_prom():
    return find_prom() is not None


def have_disk():
    return os.path.exists(DISK_IMG)


def have_cdrom():
    return os.path.exists(CDROM_IMG)


def skip_if_missing():
    """Skip if QEMU or PROM unavailable."""
    if not have_qemu():
        pytest.skip("QEMU binary not built")
    if not have_prom():
        pytest.skip("No IP24 PROM image found")


def timed_boot(runner, timeout=30, ram_mb=64, extra_args=None):
    """Boot PROM and return (output, elapsed_seconds)."""
    start = time.monotonic()
    output = runner.boot_prom(
        timeout=timeout, ram_mb=ram_mb, extra_args=extra_args)
    elapsed = time.monotonic() - start
    return output, elapsed


def reached_menu(output):
    """Check if PROM reached the System Maintenance Menu."""
    lower = output.lower()
    return any(s in lower for s in [
        "system maintenance", "option?", "enter 1", "start system"])


def count_select_xfer(output):
    """Count SELECT_XFER commands in QEMU debug output."""
    return len(re.findall(r"SELECT_XFER", output))


# ---------------------------------------------------------------------------
# PROM Boot Timing
# ---------------------------------------------------------------------------

class TestPROMBootTiming:
    """Measure wall-clock time to reach System Maintenance Menu."""

    def test_prom_boot_default(self):
        """Boot timing with default configuration (no icount)."""
        skip_if_missing()
        runner = SGIQemuRunner()
        runner.clean_traces()
        output, elapsed = timed_boot(runner, timeout=60)
        menu = reached_menu(output)
        emit_benchmark("prom_boot_default", {
            "elapsed_seconds": round(elapsed, 2),
            "reached_menu": menu,
            "output_bytes": len(output),
        })
        assert len(output) > 0, "No output from PROM boot"

    def test_prom_boot_icount_sleep_off(self):
        """Boot timing with -icount shift=0,sleep=off."""
        skip_if_missing()
        runner = SGIQemuRunner()
        runner.clean_traces()
        output, elapsed = timed_boot(
            runner, timeout=60,
            extra_args=["-icount", "shift=0,sleep=off"])
        menu = reached_menu(output)
        emit_benchmark("prom_boot_icount_sleep_off", {
            "elapsed_seconds": round(elapsed, 2),
            "reached_menu": menu,
            "output_bytes": len(output),
        })
        assert len(output) > 0, "No output with icount sleep=off"


# ---------------------------------------------------------------------------
# SCSI Probe Timing
# ---------------------------------------------------------------------------

class TestSCSIProbeTiming:
    """Measure SCSI bus probe duration with different device configs."""

    def test_scsi_probe_empty_bus(self):
        """PROM boot with no SCSI devices — probe timeout overhead."""
        skip_if_missing()
        runner = SGIQemuRunner()
        runner.clean_traces()
        output, elapsed = timed_boot(
            runner, timeout=120,
            extra_args=["-d", "unimp"])
        scsi_cmds = count_select_xfer(output)
        menu = reached_menu(output)
        emit_benchmark("scsi_probe_empty_bus", {
            "elapsed_seconds": round(elapsed, 2),
            "reached_menu": menu,
            "scsi_commands": scsi_cmds,
        })

    def test_scsi_probe_with_disk(self):
        """PROM boot with SCSI disk at target 1."""
        skip_if_missing()
        if not have_disk():
            pytest.skip("No SCSI disk image")
        runner = SGIQemuRunner()
        runner.clean_traces()
        output, elapsed = timed_boot(
            runner, timeout=120,
            extra_args=[
                "-drive", f"if=scsi,file={DISK_IMG},format=raw",
                "-d", "unimp",
            ])
        scsi_cmds = count_select_xfer(output)
        menu = reached_menu(output)
        emit_benchmark("scsi_probe_with_disk", {
            "elapsed_seconds": round(elapsed, 2),
            "reached_menu": menu,
            "scsi_commands": scsi_cmds,
        })

    def test_scsi_probe_disk_and_cdrom(self):
        """PROM boot with disk + CD-ROM."""
        skip_if_missing()
        if not have_disk():
            pytest.skip("No SCSI disk image")
        if not have_cdrom():
            pytest.skip("No CD-ROM image")
        runner = SGIQemuRunner()
        runner.clean_traces()
        output, elapsed = timed_boot(
            runner, timeout=120,
            extra_args=[
                "-drive", f"if=scsi,file={DISK_IMG},format=raw",
                "-drive", f"if=scsi,file={CDROM_IMG},format=raw,media=cdrom,readonly=on",
                "-d", "unimp",
            ])
        scsi_cmds = count_select_xfer(output)
        menu = reached_menu(output)
        emit_benchmark("scsi_probe_disk_and_cdrom", {
            "elapsed_seconds": round(elapsed, 2),
            "reached_menu": menu,
            "scsi_commands": scsi_cmds,
        })


# ---------------------------------------------------------------------------
# RAM Size Impact
# ---------------------------------------------------------------------------

class TestRAMSizeImpact:
    """Measure memory probing overhead across RAM sizes."""

    @pytest.mark.parametrize("ram_mb", [32, 64, 128, 256])
    def test_prom_boot_ram_size(self, ram_mb):
        """PROM boot timing with varying RAM size.

        Note: 32MB is expected to timeout (PROM hangs with insufficient RAM).
        64MB is the minimum working configuration.
        """
        skip_if_missing()
        runner = SGIQemuRunner()
        runner.clean_traces()
        output, elapsed = timed_boot(
            runner, timeout=120, ram_mb=ram_mb)
        menu = reached_menu(output)
        emit_benchmark(f"prom_boot_ram_{ram_mb}mb", {
            "elapsed_seconds": round(elapsed, 2),
            "reached_menu": menu,
            "ram_mb": ram_mb,
            "output_bytes": len(output),
        })


# ---------------------------------------------------------------------------
# SCSI Command Throughput
# ---------------------------------------------------------------------------

class TestSCSICommandThroughput:
    """Measure SCSI command rate during PROM boot."""

    def test_prom_scsi_cmds_per_second(self):
        """Count SELECT_XFER commands during default boot."""
        skip_if_missing()
        if not have_disk():
            pytest.skip("No SCSI disk image")
        runner = SGIQemuRunner()
        runner.clean_traces()
        output, elapsed = timed_boot(
            runner, timeout=120,
            extra_args=[
                "-drive", f"if=scsi,file={DISK_IMG},format=raw",
                "-d", "unimp",
            ])
        scsi_cmds = count_select_xfer(output)
        cmds_per_sec = scsi_cmds / elapsed if elapsed > 0 else 0
        emit_benchmark("prom_scsi_cmds_default", {
            "elapsed_seconds": round(elapsed, 2),
            "scsi_commands": scsi_cmds,
            "cmds_per_second": round(cmds_per_sec, 1),
        })

    def test_prom_scsi_cmds_icount(self):
        """Count SELECT_XFER commands with icount sleep=off."""
        skip_if_missing()
        if not have_disk():
            pytest.skip("No SCSI disk image")
        runner = SGIQemuRunner()
        runner.clean_traces()
        output, elapsed = timed_boot(
            runner, timeout=120,
            extra_args=[
                "-drive", f"if=scsi,file={DISK_IMG},format=raw",
                "-d", "unimp",
                "-icount", "shift=0,sleep=off",
            ])
        scsi_cmds = count_select_xfer(output)
        cmds_per_sec = scsi_cmds / elapsed if elapsed > 0 else 0
        emit_benchmark("prom_scsi_cmds_icount", {
            "elapsed_seconds": round(elapsed, 2),
            "scsi_commands": scsi_cmds,
            "cmds_per_second": round(cmds_per_sec, 1),
        })


# ---------------------------------------------------------------------------
# Disk Format Impact
# ---------------------------------------------------------------------------

class TestDiskFormatImpact:
    """Compare boot timing between raw and qcow2 disk formats."""

    def test_prom_boot_raw_disk(self):
        """Boot timing with raw format disk image."""
        skip_if_missing()
        if not have_disk():
            pytest.skip("No SCSI disk image")
        runner = SGIQemuRunner()
        runner.clean_traces()
        output, elapsed = timed_boot(
            runner, timeout=120,
            extra_args=[
                "-drive", f"if=scsi,file={DISK_IMG},format=raw",
            ])
        menu = reached_menu(output)
        emit_benchmark("prom_boot_raw_disk", {
            "elapsed_seconds": round(elapsed, 2),
            "reached_menu": menu,
            "disk_format": "raw",
        })

    def test_prom_boot_qcow2_disk(self):
        """Boot timing with qcow2 format disk image."""
        skip_if_missing()
        if not have_disk():
            pytest.skip("No SCSI disk image")
        # Check if a qcow2 version exists
        qcow2_img = DISK_IMG.replace(".img", ".qcow2")
        if not os.path.exists(qcow2_img):
            pytest.skip("No qcow2 disk image (run qemu_disk_convert)")
        runner = SGIQemuRunner()
        runner.clean_traces()
        output, elapsed = timed_boot(
            runner, timeout=120,
            extra_args=[
                "-drive", f"if=scsi,file={qcow2_img},format=qcow2",
            ])
        menu = reached_menu(output)
        emit_benchmark("prom_boot_qcow2_disk", {
            "elapsed_seconds": round(elapsed, 2),
            "reached_menu": menu,
            "disk_format": "qcow2",
        })
