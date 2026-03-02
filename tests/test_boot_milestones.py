"""Tests for boot milestone detection.

Tests the boot_milestones module which detects structured boot
progress milestones from serial and debug output.
"""
import time
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from sgi_mcp.boot_milestones import (
    detect_milestones, BOOT_MILESTONES, BootReport, MilestoneResult,
)


class TestMilestoneDefinitions:
    """Test milestone definition structure."""

    def test_milestones_have_required_fields(self):
        for m in BOOT_MILESTONES:
            assert m.name, "Milestone must have a name"
            assert m.pattern, "Milestone must have a pattern"
            assert m.phase in ("prom", "loader", "kernel", "miniroot", "error")

    def test_non_error_milestones_count(self):
        """Should have at least 8 non-error milestones."""
        non_error = [m for m in BOOT_MILESTONES if not m.is_error]
        assert len(non_error) >= 8

    def test_error_milestones_exist(self):
        errors = [m for m in BOOT_MILESTONES if m.is_error]
        assert len(errors) >= 2


class TestDetectMilestones:
    """Test milestone detection from serial output."""

    def test_empty_output(self):
        report = detect_milestones("", start_time=time.time())
        assert report.milestones_reached == 0
        assert report.stop_reason == "timeout"

    def test_prom_post_detection(self):
        serial = "Running power-on diagnostics\n"
        report = detect_milestones(serial, start_time=time.time())
        reached = [m for m in report.milestones if m.reached and m.name == "PROM POST start"]
        assert len(reached) == 1

    def test_memory_detected(self):
        serial = (
            "Running power-on diagnostics\n"
            "Memory size: 64 megabytes\n"
        )
        report = detect_milestones(serial, start_time=time.time())
        reached_names = {m.name for m in report.milestones if m.reached}
        assert "PROM POST start" in reached_names
        assert "Memory detected" in reached_names
        assert report.milestones_reached >= 2

    def test_full_prom_boot(self):
        serial = (
            "Running power-on diagnostics\n"
            "Memory size: 64 megabytes\n"
            "scsi(0)disk(1)rdisk(0)\n"
            "Press Esc to enter Command Monitor\n"
            "System Maintenance Menu\n"
        )
        report = detect_milestones(serial, start_time=time.time())
        reached_names = {m.name for m in report.milestones if m.reached}
        assert "PROM POST start" in reached_names
        assert "Memory detected" in reached_names
        assert "SCSI probe" in reached_names
        assert "Escape countdown" in reached_names
        assert "System Maintenance Menu" in reached_names
        assert report.milestones_reached >= 5

    def test_kernel_boot(self):
        serial = (
            "Running power-on diagnostics\n"
            "Memory size: 64 megabytes\n"
            "System Maintenance Menu\n"
            "Obtaining /unix from partition 1\n"
            "IRIX Release 6.5\n"
            "INIT: started\n"
        )
        report = detect_milestones(serial, start_time=time.time())
        reached_names = {m.name for m in report.milestones if m.reached}
        assert "sashARCS loaded" in reached_names
        assert "Kernel banner" in reached_names
        assert "Init running" in reached_names


class TestStopReason:
    """Test stop reason determination."""

    def test_timeout_when_no_milestones(self):
        report = detect_milestones("", start_time=time.time())
        assert report.stop_reason == "timeout"

    def test_panic_detected(self):
        serial = (
            "Running power-on diagnostics\n"
            "PANIC: something bad\n"
        )
        report = detect_milestones(serial, start_time=time.time())
        assert report.stop_reason == "panic"

    def test_scsi_error_loop(self):
        """When we reach Creating devices but not Installer, with SCSI errors."""
        serial = (
            "Running power-on diagnostics\n"
            "System Maintenance Menu\n"
            "Creating miniroot devices, please wait...\n"
            "check_condition\n"
        )
        report = detect_milestones(serial, start_time=time.time())
        assert report.stop_reason == "scsi_error_loop"

    def test_success_at_installer(self):
        serial = (
            "Running power-on diagnostics\n"
            "System Maintenance Menu\n"
            "Creating miniroot devices, please wait...\n"
            "Inst> \n"
        )
        report = detect_milestones(serial, start_time=time.time())
        assert report.stop_reason == "success"


class TestDebugLogIntegration:
    """Test SCSI error detection from debug log."""

    def test_scsi_errors_from_debug_log(self):
        serial = "Creating miniroot devices, please wait...\n"
        debug_log = (
            "scsi-disk: check_condition cmd=MODE_SENSE (0x1a) sense=5/36/0\n"
            "scsi-disk: check_condition cmd=MODE_SENSE (0x1a) sense=5/36/0\n"
            "scsi-disk: MODE_SENSE unsupported page 0x3f (page_control=0, dbd=0, dev_type=0)\n"
        )
        report = detect_milestones(serial, start_time=time.time(),
                                   debug_log=debug_log)
        assert report.scsi_error_summary is not None
        assert "check_condition" in report.scsi_error_summary
        # SCSI error milestone should be marked as reached
        scsi_err = [m for m in report.milestones if m.name == "SCSI error"]
        assert any(m.reached for m in scsi_err)

    def test_no_debug_log(self):
        serial = "Running power-on diagnostics\n"
        report = detect_milestones(serial, start_time=time.time())
        assert report.scsi_error_summary is None


class TestBootReportFormat:
    """Test the boot report formatter."""

    def test_format_basic_report(self):
        from sgi_mcp.formatters import format_boot_report

        serial = (
            "Running power-on diagnostics\n"
            "Memory size: 64 megabytes\n"
            "System Maintenance Menu\n"
        )
        report = detect_milestones(serial, start_time=time.time())
        output = format_boot_report(report)
        assert "Boot Progress Report" in output
        assert "Milestone Timeline" in output
        assert "PROM POST start" in output
        assert "reached" in output

    def test_format_report_with_errors(self):
        from sgi_mcp.formatters import format_boot_report

        serial = "Creating miniroot devices, please wait...\n"
        debug_log = "scsi-disk: check_condition cmd=MODE_SENSE (0x1a) sense=5/36/0\n"
        report = detect_milestones(serial, start_time=time.time(),
                                   debug_log=debug_log)
        output = format_boot_report(report)
        assert "SCSI Errors During Boot" in output
