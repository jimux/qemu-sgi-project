"""
IRIX SCSI command validation tests.

Verifies that all SCSI commands IRIX uses during boot are properly handled
in QEMU's scsi-disk.c command dispatch, MODE_SENSE page tables, and our
WD33C93 controller implementation.

These tests are FAST (source code analysis only, no QEMU boot).

Categories:
  - Standard: verifies command dispatch coverage
  - CROSS-REF: verified against MAME wd33c9x.cpp or WD33C93 datasheet
  - INVESTIGATIVE: explores uncertain behavior
"""

import re
import pytest


# ---------------------------------------------------------------------------
# IRIX SCSI Command Coverage
# ---------------------------------------------------------------------------

class TestIRIXSCSICommandCoverage:
    """Verify scsi-disk.c dispatches all SCSI opcodes IRIX issues during boot.

    Each test confirms the opcode appears in the command dispatch table
    (scsi_disk_dma_reqops or scsi_disk_emulate_reqops).
    """

    def test_test_unit_ready_0x00(self, scsi_disk_source):
        """TEST_UNIT_READY must be in the emulate dispatch table."""
        assert re.search(
            r"\[TEST_UNIT_READY\]\s*=\s*&scsi_disk_emulate_reqops",
            scsi_disk_source)

    def test_request_sense_0x03(self, scsi_disk_source):
        """REQUEST_SENSE must be in the emulate dispatch table."""
        assert re.search(
            r"\[REQUEST_SENSE\]\s*=\s*&scsi_disk_emulate_reqops",
            scsi_disk_source)

    def test_read_6_0x08(self, scsi_disk_source):
        """READ(6) must be in the DMA dispatch table."""
        assert re.search(
            r"\[READ_6\]\s*=\s*&scsi_disk_dma_reqops",
            scsi_disk_source)

    def test_inquiry_0x12(self, scsi_disk_source):
        """INQUIRY must be in the emulate dispatch table."""
        assert re.search(
            r"\[INQUIRY\]\s*=\s*&scsi_disk_emulate_reqops",
            scsi_disk_source)

    def test_mode_select_6_0x15(self, scsi_disk_source):
        """MODE_SELECT(6) must be in the emulate dispatch table."""
        assert re.search(
            r"\[MODE_SELECT\]\s*=\s*&scsi_disk_emulate_reqops",
            scsi_disk_source)

    def test_mode_sense_6_0x1a(self, scsi_disk_source):
        """MODE_SENSE(6) must be in the emulate dispatch table."""
        assert re.search(
            r"\[MODE_SENSE\]\s*=\s*&scsi_disk_emulate_reqops",
            scsi_disk_source)

    def test_start_stop_0x1b(self, scsi_disk_source):
        """START_STOP_UNIT must be in the emulate dispatch table."""
        assert re.search(
            r"\[START_STOP\]\s*=\s*&scsi_disk_emulate_reqops",
            scsi_disk_source)

    def test_allow_medium_removal_0x1e(self, scsi_disk_source):
        """PREVENT_ALLOW_MEDIUM_REMOVAL must be in the emulate dispatch."""
        assert re.search(
            r"\[ALLOW_MEDIUM_REMOVAL\]\s*=\s*&scsi_disk_emulate_reqops",
            scsi_disk_source)

    def test_read_capacity_10_0x25(self, scsi_disk_source):
        """READ_CAPACITY(10) must be in the emulate dispatch table."""
        assert re.search(
            r"\[READ_CAPACITY_10\]\s*=\s*&scsi_disk_emulate_reqops",
            scsi_disk_source)

    def test_read_10_0x28(self, scsi_disk_source):
        """READ(10) must be in the DMA dispatch table."""
        assert re.search(
            r"\[READ_10\]\s*=\s*&scsi_disk_dma_reqops",
            scsi_disk_source)

    def test_write_10_0x2a(self, scsi_disk_source):
        """WRITE(10) must be in the DMA dispatch table."""
        assert re.search(
            r"\[WRITE_10\]\s*=\s*&scsi_disk_dma_reqops",
            scsi_disk_source)

    def test_synchronize_cache_0x35(self, scsi_disk_source):
        """SYNCHRONIZE_CACHE must be in the emulate dispatch table."""
        assert re.search(
            r"\[SYNCHRONIZE_CACHE\]\s*=\s*&scsi_disk_emulate_reqops",
            scsi_disk_source)

    def test_mode_sense_10_0x5a(self, scsi_disk_source):
        """MODE_SENSE(10) must be in the emulate dispatch table."""
        assert re.search(
            r"\[MODE_SENSE_10\]\s*=\s*&scsi_disk_emulate_reqops",
            scsi_disk_source)

    def test_reserve_0x16(self, scsi_disk_source):
        """[INVESTIGATIVE] RESERVE may not be in dispatch table.

        IRIX may issue RESERVE to lock a disk. Check if it's handled
        or if we need to add it.
        """
        has_reserve = re.search(
            r"\[RESERVE\]\s*=\s*&scsi_disk_", scsi_disk_source)
        if not has_reserve:
            pytest.xfail("RESERVE not in dispatch table — may need adding")
        assert has_reserve

    def test_release_0x17(self, scsi_disk_source):
        """[INVESTIGATIVE] RELEASE may not be in dispatch table.

        IRIX may issue RELEASE to unlock a disk. Check if it's handled.
        """
        has_release = re.search(
            r"\[RELEASE\]\s*=\s*&scsi_disk_", scsi_disk_source)
        if not has_release:
            pytest.xfail("RELEASE not in dispatch table — may need adding")
        assert has_release


# ---------------------------------------------------------------------------
# MODE_SENSE Page Support
# ---------------------------------------------------------------------------

class TestModeSensePageSupport:
    """Verify MODE_SENSE pages needed by IRIX exist in mode_sense_valid[].

    The IRIX kernel probes various MODE_SENSE pages during device enumeration.
    If a page is missing from the valid table for TYPE_DISK, the device
    returns CHECK_CONDITION with INVALID_FIELD, causing IRIX to retry.
    """

    def test_vendor_specific_page_0x00(self, scsi_disk_source):
        """Page 0x00 (Vendor Specific) must be valid for TYPE_DISK."""
        assert re.search(
            r"MODE_PAGE_VENDOR_SPECIFIC.*TYPE_DISK",
            scsi_disk_source)

    def test_rw_error_page_0x01(self, scsi_disk_source):
        """Page 0x01 (R/W Error Recovery) must be valid for TYPE_DISK."""
        assert re.search(
            r"MODE_PAGE_R_W_ERROR.*TYPE_DISK",
            scsi_disk_source)

    def test_format_device_page_0x03(self, scsi_disk_source):
        """Page 0x03 (Format Device) must be valid for TYPE_DISK.

        The format device page uses a literal 0x03 in the valid table,
        not a MODE_PAGE_ constant.
        """
        # The table uses literal 0x03 with a comment
        assert re.search(r"\[0x03\s*/\*\s*FORMAT", scsi_disk_source)

    def test_hd_geometry_page_0x04(self, scsi_disk_source):
        """Page 0x04 (HD Geometry) must be valid for TYPE_DISK."""
        assert re.search(
            r"MODE_PAGE_HD_GEOMETRY.*TYPE_DISK",
            scsi_disk_source)

    def test_caching_page_0x08(self, scsi_disk_source):
        """Page 0x08 (Caching) must be valid for TYPE_DISK."""
        assert re.search(
            r"MODE_PAGE_CACHING.*TYPE_DISK",
            scsi_disk_source)

    def test_control_page_0x0a(self, scsi_disk_source):
        """Page 0x0a (Control) must be valid for TYPE_DISK."""
        assert re.search(
            r"MODE_PAGE_CONTROL.*TYPE_DISK",
            scsi_disk_source)

    def test_page_0x3f_all_pages_iteration(self, scsi_disk_source):
        """Page 0x3F must iterate pages 0x00-0x3E calling mode_sense_page().

        IRIX sends MODE_SENSE with page=0x3F to get all pages.
        """
        # The code should have a loop from 0 to 0x3e
        assert re.search(
            r"page\s*==\s*(0x3f|MODE_PAGE_ALLS)",
            scsi_disk_source)
        # And call mode_sense_page in a loop
        assert re.search(
            r"for.*page.*mode_sense_page",
            scsi_disk_source, re.DOTALL)

    def test_saved_values_pc3_returns_error(self, scsi_disk_source):
        """page_control=3 (Saved Values) must return error.

        QEMU doesn't support saved mode pages. This should return
        SAVING_PARAMS_NOT_SUPPORTED or CHECK_CONDITION.
        """
        # Look for page_control == 3 check
        assert re.search(r"page_control\s*==\s*3", scsi_disk_source)

    def test_check_condition_on_invalid_page(self, scsi_disk_source):
        """Invalid single page request must return CHECK_CONDITION.

        When mode_sense_page returns -1, the handler should goto
        illegal_request or return an error path.
        """
        # mode_sense_page returning -1 should lead to error
        assert re.search(
            r"mode_sense_page.*<\s*0|mode_sense_page.*==\s*-1|"
            r"buflen\s*<\s*0.*illegal_request",
            scsi_disk_source, re.DOTALL)

    def test_mode_sense_check_condition_logs(self, scsi_disk_source):
        """MODE_SENSE failure should log via scsi_check_condition.

        The scsi_check_condition helper logs with LOG_UNIMP including
        the command name and sense code, covering all CHECK_CONDITION
        responses including MODE_SENSE failures.
        """
        assert re.search(
            r"scsi_check_condition.*sense|"
            r"qemu_log_mask.*check_condition",
            scsi_disk_source)


# ---------------------------------------------------------------------------
# WD33C93 Command Coverage [CROSS-REF: MAME wd33c9x.cpp]
# ---------------------------------------------------------------------------

class TestWD33C93CommandCoverage:
    """Cross-ref our WD33C93 commands against MAME's wd33c9x.cpp.

    IRIX uses these WD33C93 commands during boot. Each test verifies the
    command code is handled in the execute_cmd switch statement.
    """

    def test_cmd_reset_0x00(self, wd33c93_source):
        """CMD_RESET (0x00) must be implemented."""
        assert re.search(r"case CMD_RESET:", wd33c93_source)

    def test_cmd_abort_0x01(self, wd33c93_source):
        """CMD_ABORT (0x01) must be implemented."""
        assert re.search(r"case CMD_ABORT:", wd33c93_source)

    def test_cmd_disconnect_0x04(self, wd33c93_source):
        """CMD_DISCONNECT (0x04) must be implemented."""
        assert re.search(r"case CMD_DISCONNECT:", wd33c93_source)

    def test_cmd_select_atn_0x06(self, wd33c93_source):
        """CMD_SELECT_ATN (0x06) must be implemented."""
        assert re.search(r"case CMD_SELECT_ATN:", wd33c93_source)

    def test_cmd_select_0x07(self, wd33c93_source):
        """CMD_SELECT (0x07) must be implemented."""
        assert re.search(r"case CMD_SELECT:", wd33c93_source)

    def test_cmd_select_atn_xfer_0x08(self, wd33c93_source):
        """CMD_SELECT_ATN_XFER (0x08) must be implemented."""
        assert re.search(r"case CMD_SELECT_ATN_XFER:", wd33c93_source)

    def test_cmd_select_xfer_0x09(self, wd33c93_source):
        """CMD_SELECT_XFER (0x09) must be implemented."""
        assert re.search(r"case CMD_SELECT_XFER:", wd33c93_source)

    def test_cmd_transfer_info_0x20(self, wd33c93_source):
        """CMD_TRANSFER_INFO (0x20) must be implemented."""
        assert re.search(r"case CMD_TRANSFER_INFO:", wd33c93_source)

    def test_unimplemented_returns_invalid_cmd(self, wd33c93_source):
        """Unknown command code must return SCSI_STATUS_INVALID_COMMAND."""
        # The default case should set INVALID_COMMAND
        assert re.search(
            r"default:.*SCSI_STATUS_INVALID_COMMAND",
            wd33c93_source, re.DOTALL)

    def test_assert_atn_0x02(self, wd33c93_header):
        """[INVESTIGATIVE] CMD_ASSERT_ATN (0x02) defined but may not be needed.

        IRIX may issue ASSERT_ATN during error recovery. Check if it's
        handled or if the default INVALID_COMMAND path suffices.
        """
        # Verify the constant is defined
        assert re.search(r"#define\s+CMD_ASSERT_ATN\s+0x02",
                         wd33c93_header)
        # But it may not have a case handler — that's informative

    def test_negate_ack_0x03(self, wd33c93_header):
        """[INVESTIGATIVE] CMD_NEGATE_ACK (0x03) defined but may not be needed.

        Check if IRIX needs this during message-in phase handling.
        """
        assert re.search(r"#define\s+CMD_NEGATE_ACK\s+0x03",
                         wd33c93_header)


# ---------------------------------------------------------------------------
# WD33C93 Status Codes (IRIX-relevant subset)
# ---------------------------------------------------------------------------

class TestWD33C93StatusCodesIRIX:
    """Verify all status codes IRIX checks are defined.

    IRIX reads SCSI_STATUS after every command completion and branches
    based on the status code. Missing codes cause undefined behavior.
    """

    def test_reset_status_0x00(self, wd33c93_header):
        """SCSI_STATUS_RESET (0x00) used after CMD_RESET."""
        assert re.search(
            r"#define\s+SCSI_STATUS_RESET\s+0x00", wd33c93_header)

    def test_select_transfer_success_0x16(self, wd33c93_header):
        """SCSI_STATUS_SELECT_TRANSFER_SUCCESS (0x16) for normal completion."""
        assert re.search(
            r"#define\s+SCSI_STATUS_SELECT_TRANSFER_SUCCESS\s+0x16",
            wd33c93_header)

    def test_selection_timeout_0x42(self, wd33c93_header):
        """SCSI_STATUS_SELECTION_TIMEOUT (0x42) for absent targets."""
        assert re.search(
            r"#define\s+SCSI_STATUS_SELECTION_TIMEOUT\s+0x42",
            wd33c93_header)

    def test_invalid_command_0x40(self, wd33c93_header):
        """SCSI_STATUS_INVALID_COMMAND (0x40) for unknown commands."""
        assert re.search(
            r"#define\s+SCSI_STATUS_INVALID_COMMAND\s+0x40",
            wd33c93_header)

    def test_disconnect_0x85(self, wd33c93_header):
        """SCSI_STATUS_DISCONNECT (0x85) for bus disconnect."""
        assert re.search(
            r"#define\s+SCSI_STATUS_DISCONNECT\s+0x85",
            wd33c93_header)


# ---------------------------------------------------------------------------
# SCSI Opcode Constants [CROSS-REF: scsi/constants.h]
# ---------------------------------------------------------------------------

class TestSCSIOpcodeConstants:
    """Verify SCSI opcode values match the SPC/SBC specifications."""

    def test_test_unit_ready_value(self, scsi_constants_header):
        """TEST_UNIT_READY must be 0x00."""
        assert re.search(
            r"#define\s+TEST_UNIT_READY\s+0x00", scsi_constants_header)

    def test_request_sense_value(self, scsi_constants_header):
        """REQUEST_SENSE must be 0x03."""
        assert re.search(
            r"#define\s+REQUEST_SENSE\s+0x03", scsi_constants_header)

    def test_inquiry_value(self, scsi_constants_header):
        """INQUIRY must be 0x12."""
        assert re.search(
            r"#define\s+INQUIRY\s+0x12", scsi_constants_header)

    def test_mode_sense_value(self, scsi_constants_header):
        """MODE_SENSE must be 0x1a."""
        assert re.search(
            r"#define\s+MODE_SENSE\s+0x1a", scsi_constants_header)

    def test_mode_sense_10_value(self, scsi_constants_header):
        """MODE_SENSE_10 must be 0x5a."""
        assert re.search(
            r"#define\s+MODE_SENSE_10\s+0x5a", scsi_constants_header)

    def test_read_capacity_10_value(self, scsi_constants_header):
        """READ_CAPACITY_10 must be 0x25."""
        assert re.search(
            r"#define\s+READ_CAPACITY_10\s+0x25", scsi_constants_header)

    def test_start_stop_value(self, scsi_constants_header):
        """START_STOP must be 0x1b."""
        assert re.search(
            r"#define\s+START_STOP\s+0x1b", scsi_constants_header)
