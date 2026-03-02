"""Tests for SCSI log parser.

Tests the scsi_parser module which parses QEMU -d unimp output
for SCSI command traces from wd33c93.c and scsi-disk.c.
"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from sgi_mcp.scsi_parser import (
    parse_scsi_log, opcode_name, sense_description,
    SCSICommand, SCSILogSummary,
    SCSI_OPCODE_NAMES, SENSE_DESCRIPTIONS,
)


class TestOpcodeNames:
    """Test SCSI opcode name lookup."""

    def test_known_opcodes(self):
        assert opcode_name(0x00) == "TEST_UNIT_READY"
        assert opcode_name(0x12) == "INQUIRY"
        assert opcode_name(0x1a) == "MODE_SENSE"
        assert opcode_name(0x28) == "READ_10"
        assert opcode_name(0x25) == "READ_CAPACITY"

    def test_unknown_opcode(self):
        name = opcode_name(0xff)
        assert "UNKNOWN" in name
        assert "0xff" in name

    def test_opcode_table_has_common_commands(self):
        """Verify all commonly-used SCSI commands are in the table."""
        expected = {0x00, 0x03, 0x12, 0x1a, 0x25, 0x28, 0x2a, 0x08, 0x0a}
        assert expected.issubset(set(SCSI_OPCODE_NAMES.keys()))


class TestSenseDescriptions:
    """Test sense code description lookup."""

    def test_invalid_field_in_cdb(self):
        # ASC 0x24 = 36 decimal — this is how QEMU logs it
        desc = sense_description(5, 0x24, 0)
        assert "INVALID FIELD IN CDB" in desc

    def test_unknown_sense(self):
        desc = sense_description(15, 255, 255)
        assert "KEY=15" in desc

    def test_sense_with_only_key_match(self):
        """When only the key matches, still provides useful info."""
        desc = sense_description(5, 0x99, 0)
        assert "INVALID COMMAND" in desc or "KEY=5" in desc or "asc=" in desc

    def test_no_sense(self):
        desc = sense_description(0, 0, 0)
        assert "NO SENSE" in desc


class TestParseSelectXfer:
    """Test parsing of SELECT_XFER log lines."""

    def test_basic_select_xfer(self):
        log = "wd33c93: SELECT_XFER target=1 CDB[6]={12 00 00 00 24 00 }tc=36\n"
        summary = parse_scsi_log(log)
        assert summary.total_commands == 1
        assert len(summary.commands) == 1
        cmd = summary.commands[0]
        assert cmd.target_id == 1
        assert cmd.opcode == 0x12
        assert cmd.opcode_name == "INQUIRY"
        assert cmd.transfer_count == 36
        assert cmd.status == "ok"

    def test_enhanced_select_xfer_with_cmd_name(self):
        """Test the enhanced format with cmd= prefix."""
        log = "wd33c93: SELECT_XFER target=1 cmd=INQUIRY(0x12) CDB[6]={12 00 00 00 24 00 }tc=36\n"
        summary = parse_scsi_log(log)
        assert summary.total_commands == 1
        cmd = summary.commands[0]
        assert cmd.opcode == 0x12
        assert cmd.opcode_name == "INQUIRY"

    def test_select_xfer_with_response(self):
        log = (
            "wd33c93: SELECT_XFER target=1 CDB[6]={12 00 00 00 24 00 }tc=36\n"
            "wd33c93: SCSI response datalen=36\n"
        )
        summary = parse_scsi_log(log)
        assert summary.total_commands == 1
        cmd = summary.commands[0]
        assert cmd.data_len == 36
        assert cmd.status == "ok"


class TestParseCheckCondition:
    """Test parsing of check_condition errors."""

    def test_check_condition(self):
        log = (
            "wd33c93: SELECT_XFER target=1 CDB[6]={1a 00 3f 00 fc 00 }tc=252\n"
            "wd33c93: SCSI response datalen=0\n"
            "scsi-disk: check_condition cmd=MODE_SENSE (0x1a) sense=5/36/0\n"
        )
        summary = parse_scsi_log(log)
        assert summary.total_commands == 1
        assert summary.failed_commands == 1
        assert summary.successful_commands == 0
        cmd = summary.commands[0]
        assert cmd.status == "check_condition"
        assert cmd.sense_key == 5
        assert cmd.sense_asc == 36  # 0x24 in decimal
        assert cmd.sense_ascq == 0
        assert "INVALID FIELD" in cmd.sense_desc

    def test_mode_sense_unsupported_page(self):
        log = (
            "wd33c93: SELECT_XFER target=1 CDB[6]={1a 00 3f 00 fc 00 }tc=252\n"
            "scsi-disk: MODE_SENSE unsupported page 0x3f (page_control=0, dbd=0, dev_type=0)\n"
            "scsi-disk: check_condition cmd=MODE_SENSE (0x1a) sense=5/36/0\n"
        )
        summary = parse_scsi_log(log)
        assert summary.total_commands == 1
        cmd = summary.commands[0]
        assert "unsupported page 0x3f" in cmd.error_detail
        assert 0x3f in summary.mode_sense_pages
        assert summary.mode_sense_pages[0x3f] == "failed"

    def test_standalone_check_condition(self):
        """check_condition without preceding SELECT_XFER."""
        log = "scsi-disk: check_condition cmd=MODE_SENSE (0x1a) sense=5/36/0\n"
        summary = parse_scsi_log(log)
        assert summary.total_commands == 1
        assert summary.failed_commands == 1


class TestParseCmdFailed:
    """Test parsing of CMD FAILED lines."""

    def test_cmd_failed(self):
        log = (
            "wd33c93: SELECT_XFER target=1 CDB[6]={1a 00 3f 00 fc 00 }tc=252\n"
            "wd33c93: SCSI CMD FAILED target=1 status=2 CDB[6]={1a 00 3f 00 fc 00 }\n"
        )
        summary = parse_scsi_log(log)
        assert summary.total_commands == 1
        cmd = summary.commands[0]
        assert cmd.status == "check_condition"


class TestMultipleCommands:
    """Test parsing of multiple SCSI commands."""

    def test_mixed_success_and_failure(self):
        log = (
            "wd33c93: SELECT_XFER target=1 CDB[6]={12 00 00 00 24 00 }tc=36\n"
            "wd33c93: SCSI response datalen=36\n"
            "wd33c93: SELECT_XFER target=1 CDB[6]={25 00 00 00 00 00 }tc=8\n"
            "wd33c93: SCSI response datalen=8\n"
            "wd33c93: SELECT_XFER target=1 CDB[6]={1a 00 3f 00 fc 00 }tc=252\n"
            "scsi-disk: check_condition cmd=MODE_SENSE (0x1a) sense=5/36/0\n"
        )
        summary = parse_scsi_log(log)
        assert summary.total_commands == 3
        assert summary.successful_commands == 2
        assert summary.failed_commands == 1
        assert summary.command_counts["INQUIRY"] == 1
        assert summary.command_counts["READ_CAPACITY"] == 1
        assert summary.command_counts["MODE_SENSE"] == 1

    def test_target_activity(self):
        log = (
            "wd33c93: SELECT_XFER target=1 CDB[6]={12 00 00 00 24 00 }tc=36\n"
            "wd33c93: SCSI response datalen=36\n"
            "wd33c93: SELECT_XFER target=4 CDB[6]={12 00 00 00 24 00 }tc=36\n"
            "wd33c93: SCSI response datalen=36\n"
        )
        summary = parse_scsi_log(log)
        assert summary.target_activity[1] == 1
        assert summary.target_activity[4] == 1

    def test_interleaved_non_scsi_lines(self):
        """Non-SCSI lines between SCSI events should be tolerated."""
        log = (
            "wd33c93: SELECT_XFER target=1 CDB[6]={12 00 00 00 24 00 }tc=36\n"
            "sgi_mc: unimplemented read at 0x00c0\n"
            "sgi_hpc3: write reg 0x1234 = 0x5678\n"
            "wd33c93: SCSI response datalen=36\n"
        )
        summary = parse_scsi_log(log)
        assert summary.total_commands == 1
        cmd = summary.commands[0]
        assert cmd.data_len == 36


class TestFilters:
    """Test parsing with filters applied."""

    def test_errors_only(self):
        log = (
            "wd33c93: SELECT_XFER target=1 CDB[6]={12 00 00 00 24 00 }tc=36\n"
            "wd33c93: SCSI response datalen=36\n"
            "wd33c93: SELECT_XFER target=1 CDB[6]={1a 00 3f 00 fc 00 }tc=252\n"
            "scsi-disk: check_condition cmd=MODE_SENSE (0x1a) sense=5/36/0\n"
        )
        summary = parse_scsi_log(log, errors_only=True)
        # Counts reflect all commands
        assert summary.total_commands == 2
        # But commands list only has errors
        assert len(summary.commands) == 1
        assert summary.commands[0].opcode_name == "MODE_SENSE"

    def test_target_filter(self):
        log = (
            "wd33c93: SELECT_XFER target=1 CDB[6]={12 00 00 00 24 00 }tc=36\n"
            "wd33c93: SCSI response datalen=36\n"
            "wd33c93: SELECT_XFER target=4 CDB[6]={12 00 00 00 24 00 }tc=36\n"
            "wd33c93: SCSI response datalen=36\n"
        )
        summary = parse_scsi_log(log, target_filter=4)
        assert summary.total_commands == 2
        assert len(summary.commands) == 1
        assert summary.commands[0].target_id == 4

    def test_opcode_filter_by_name(self):
        log = (
            "wd33c93: SELECT_XFER target=1 CDB[6]={12 00 00 00 24 00 }tc=36\n"
            "wd33c93: SCSI response datalen=36\n"
            "wd33c93: SELECT_XFER target=1 CDB[6]={1a 00 01 00 fc 00 }tc=252\n"
            "wd33c93: SCSI response datalen=12\n"
        )
        summary = parse_scsi_log(log, opcode_filter="MODE_SENSE")
        assert len(summary.commands) == 1
        assert summary.commands[0].opcode_name == "MODE_SENSE"

    def test_opcode_filter_by_hex(self):
        log = (
            "wd33c93: SELECT_XFER target=1 CDB[6]={12 00 00 00 24 00 }tc=36\n"
            "wd33c93: SCSI response datalen=36\n"
            "wd33c93: SELECT_XFER target=1 CDB[6]={1a 00 01 00 fc 00 }tc=252\n"
            "wd33c93: SCSI response datalen=12\n"
        )
        summary = parse_scsi_log(log, opcode_filter="0x12")
        assert len(summary.commands) == 1
        assert summary.commands[0].opcode_name == "INQUIRY"


class TestEmptyAndEdgeCases:
    """Test edge cases and empty input."""

    def test_empty_log(self):
        summary = parse_scsi_log("")
        assert summary.total_commands == 0
        assert summary.commands == []

    def test_no_scsi_lines(self):
        log = (
            "sgi_mc: unimplemented read at 0x00c0\n"
            "sgi_hpc3: write reg 0x1234 = 0x5678\n"
        )
        summary = parse_scsi_log(log)
        assert summary.total_commands == 0

    def test_max_commands_limit(self):
        lines = []
        for i in range(10):
            lines.append(f"wd33c93: SELECT_XFER target=1 CDB[6]={{12 00 00 00 24 00 }}tc=36")
            lines.append("wd33c93: SCSI response datalen=36")
        log = "\n".join(lines) + "\n"
        summary = parse_scsi_log(log, max_commands=3)
        assert summary.total_commands == 10
        assert len(summary.commands) == 3

    def test_error_counts(self):
        log = (
            "wd33c93: SELECT_XFER target=1 CDB[6]={1a 00 3f 00 fc 00 }tc=252\n"
            "scsi-disk: check_condition cmd=MODE_SENSE (0x1a) sense=5/36/0\n"
            "wd33c93: SELECT_XFER target=1 CDB[6]={1a 00 3f 00 fc 00 }tc=252\n"
            "scsi-disk: check_condition cmd=MODE_SENSE (0x1a) sense=5/36/0\n"
        )
        summary = parse_scsi_log(log)
        assert summary.failed_commands == 2
        # Error counts should aggregate
        assert len(summary.error_counts) == 1
        desc = list(summary.error_counts.keys())[0]
        assert summary.error_counts[desc] == 2


class TestFormatScsiLogSummary:
    """Test the SCSI log summary formatter."""

    def test_format_basic(self):
        from sgi_mcp.formatters import format_scsi_log_summary

        log = (
            "wd33c93: SELECT_XFER target=1 CDB[6]={12 00 00 00 24 00 }tc=36\n"
            "wd33c93: SCSI response datalen=36\n"
            "wd33c93: SELECT_XFER target=1 CDB[6]={1a 00 3f 00 fc 00 }tc=252\n"
            "scsi-disk: check_condition cmd=MODE_SENSE (0x1a) sense=5/36/0\n"
        )
        summary = parse_scsi_log(log)
        output = format_scsi_log_summary(summary)
        assert "SCSI Command Trace" in output
        assert "Total commands" in output
        assert "INQUIRY" in output
        assert "MODE_SENSE" in output
        assert "INVALID FIELD" in output
