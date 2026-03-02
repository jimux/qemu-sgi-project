"""
WD33C93 SCSI controller and HPC3 SCSI DMA source code tests.

Verifies register definitions, status codes, command codes, DMA descriptor
format, IRQ routing, and behavioral patterns in the WD33C93 and HPC3 SCSI
subsystem source code.

These tests are FAST (source code analysis only, no QEMU boot).

Categories:
  - Standard: verifies known-correct constants
  - CROSS-REF: verified against MAME wd33c9x.cpp or WD33C93 datasheet
"""

import re
import pytest


# ---------------------------------------------------------------------------
# WD33C93 Register Definitions
# ---------------------------------------------------------------------------

class TestWD33C93Registers:
    """Verify WD33C93 register address definitions match the datasheet."""

    def test_register_count_32(self, wd33c93_header):
        """WD33C93_REGS must be 32 (0x00-0x1f address space)."""
        assert "#define WD33C93_REGS 32" in wd33c93_header

    def test_fifo_size_12(self, wd33c93_header):
        """WD33C93_FIFO_SIZE must be 12 bytes."""
        assert "#define WD33C93_FIFO_SIZE 12" in wd33c93_header

    def test_own_id_at_0x00(self, wd33c93_header):
        """WD_OWN_ID must be at register 0x00."""
        assert re.search(r"#define\s+WD_OWN_ID\s+0x00", wd33c93_header)

    def test_control_at_0x01(self, wd33c93_header):
        """WD_CONTROL must be at register 0x01."""
        assert re.search(r"#define\s+WD_CONTROL\s+0x01", wd33c93_header)

    def test_cdb_starts_at_0x03(self, wd33c93_header):
        """WD_CDB_1 must be at register 0x03."""
        assert re.search(r"#define\s+WD_CDB_1\s+0x03", wd33c93_header)

    def test_target_lun_at_0x0f(self, wd33c93_header):
        """WD_TARGET_LUN must be at register 0x0f."""
        assert re.search(r"#define\s+WD_TARGET_LUN\s+0x0f", wd33c93_header)

    def test_command_phase_at_0x10(self, wd33c93_header):
        """WD_COMMAND_PHASE must be at register 0x10."""
        assert re.search(r"#define\s+WD_COMMAND_PHASE\s+0x10", wd33c93_header)

    def test_transfer_count_msb_at_0x12(self, wd33c93_header):
        """WD_TRANSFER_COUNT_MSB must be at register 0x12."""
        assert re.search(r"#define\s+WD_TRANSFER_COUNT_MSB\s+0x12",
                         wd33c93_header)

    def test_transfer_count_mid_at_0x13(self, wd33c93_header):
        """WD_TRANSFER_COUNT (middle byte) must be at register 0x13."""
        assert re.search(r"#define\s+WD_TRANSFER_COUNT\s+0x13",
                         wd33c93_header)

    def test_transfer_count_lsb_at_0x14(self, wd33c93_header):
        """WD_TRANSFER_COUNT_LSB must be at register 0x14."""
        assert re.search(r"#define\s+WD_TRANSFER_COUNT_LSB\s+0x14",
                         wd33c93_header)

    def test_destination_id_at_0x15(self, wd33c93_header):
        """WD_DESTINATION_ID must be at register 0x15."""
        assert re.search(r"#define\s+WD_DESTINATION_ID\s+0x15",
                         wd33c93_header)

    def test_scsi_status_at_0x17(self, wd33c93_header):
        """WD_SCSI_STATUS must be at register 0x17."""
        assert re.search(r"#define\s+WD_SCSI_STATUS\s+0x17", wd33c93_header)

    def test_command_at_0x18(self, wd33c93_header):
        """WD_COMMAND must be at register 0x18."""
        assert re.search(r"#define\s+WD_COMMAND\s+0x18", wd33c93_header)

    def test_data_at_0x19(self, wd33c93_header):
        """WD_DATA must be at register 0x19."""
        assert re.search(r"#define\s+WD_DATA\s+0x19", wd33c93_header)

    def test_aux_status_at_0x1f(self, wd33c93_header):
        """WD_AUXILIARY_STATUS must be at register 0x1f."""
        assert re.search(r"#define\s+WD_AUXILIARY_STATUS\s+0x1f",
                         wd33c93_header)


# ---------------------------------------------------------------------------
# WD33C93 Status Codes
# ---------------------------------------------------------------------------

class TestWD33C93StatusCodes:
    """Verify SCSI status code values match the WD33C93 datasheet."""

    def test_status_reset_0x00(self, wd33c93_header):
        """SCSI_STATUS_RESET must be 0x00."""
        assert re.search(r"#define\s+SCSI_STATUS_RESET\s+0x00",
                         wd33c93_header)

    def test_status_select_success_0x11(self, wd33c93_header):
        """SCSI_STATUS_SELECT_SUCCESS must be 0x11."""
        assert re.search(r"#define\s+SCSI_STATUS_SELECT_SUCCESS\s+0x11",
                         wd33c93_header)

    def test_status_xfer_success_0x16(self, wd33c93_header):
        """SCSI_STATUS_SELECT_TRANSFER_SUCCESS must be 0x16."""
        assert re.search(
            r"#define\s+SCSI_STATUS_SELECT_TRANSFER_SUCCESS\s+0x16",
            wd33c93_header)

    def test_status_timeout_0x42(self, wd33c93_header):
        """SCSI_STATUS_SELECTION_TIMEOUT must be 0x42."""
        assert re.search(r"#define\s+SCSI_STATUS_SELECTION_TIMEOUT\s+0x42",
                         wd33c93_header)

    def test_status_disconnect_0x85(self, wd33c93_header):
        """SCSI_STATUS_DISCONNECT must be 0x85."""
        assert re.search(r"#define\s+SCSI_STATUS_DISCONNECT\s+0x85",
                         wd33c93_header)


# ---------------------------------------------------------------------------
# WD33C93 Commands
# ---------------------------------------------------------------------------

class TestWD33C93Commands:
    """Verify command code values match the WD33C93 datasheet."""

    def test_cmd_reset_0x00(self, wd33c93_header):
        """CMD_RESET must be 0x00."""
        assert re.search(r"#define\s+CMD_RESET\s+0x00", wd33c93_header)

    def test_cmd_select_atn_0x06(self, wd33c93_header):
        """CMD_SELECT_ATN must be 0x06."""
        assert re.search(r"#define\s+CMD_SELECT_ATN\s+0x06", wd33c93_header)

    def test_cmd_select_atn_xfer_0x08(self, wd33c93_header):
        """CMD_SELECT_ATN_XFER must be 0x08."""
        assert re.search(r"#define\s+CMD_SELECT_ATN_XFER\s+0x08",
                         wd33c93_header)

    def test_cmd_transfer_info_0x20(self, wd33c93_header):
        """CMD_TRANSFER_INFO must be 0x20."""
        assert re.search(r"#define\s+CMD_TRANSFER_INFO\s+0x20",
                         wd33c93_header)

    def test_cmd_sbt_0x80(self, wd33c93_header):
        """CMD_SBT (Single Byte Transfer modifier) must be 0x80."""
        assert re.search(r"#define\s+CMD_SBT\s+0x80", wd33c93_header)


# ---------------------------------------------------------------------------
# WD33C93 ASR Bits
# ---------------------------------------------------------------------------

class TestWD33C93ASRBits:
    """Verify Auxiliary Status Register bit definitions."""

    def test_asr_dbr_0x01(self, wd33c93_header):
        """ASR_DBR (Data Buffer Ready) must be 0x01."""
        assert re.search(r"#define\s+ASR_DBR\s+0x01", wd33c93_header)

    def test_asr_cip_0x10(self, wd33c93_header):
        """ASR_CIP (Command In Progress) must be 0x10."""
        assert re.search(r"#define\s+ASR_CIP\s+0x10", wd33c93_header)

    def test_asr_int_0x80(self, wd33c93_header):
        """ASR_INT (Interrupt Pending) must be 0x80."""
        assert re.search(r"#define\s+ASR_INT\s+0x80", wd33c93_header)

    def test_asr_lci_0x40(self, wd33c93_header):
        """ASR_LCI (Last Command Ignored) must be 0x40."""
        assert re.search(r"#define\s+ASR_LCI\s+0x40", wd33c93_header)


# ---------------------------------------------------------------------------
# WD33C93 Command Phase Lifecycle [CROSS-REF: MAME wd33c9x.cpp]
# ---------------------------------------------------------------------------

class TestWD33C93CommandPhaseLifecycle:
    """Verify command phase values match MAME's CP_* constants.

    MAME references:
      CP_BYTES_0 = 0x10 (CDB being sent)
      CP_TRANSFER_COUNT = 0x30 (data transfer)
      CP_COMMAND_COMPLETE = 0x60 (command done)
    """

    def test_phase_cdb_0x10(self, wd33c93_source):
        """Command phase must be set to 0x10 during CDB send (select_xfer)."""
        assert "WD_COMMAND_PHASE] = 0x10" in wd33c93_source

    def test_phase_data_0x30(self, wd33c93_source):
        """Command phase must be set to 0x30 during data transfer."""
        assert "WD_COMMAND_PHASE] = 0x30" in wd33c93_source

    def test_phase_complete_0x60(self, wd33c93_source):
        """Command phase must be set to 0x60 on command completion."""
        assert "WD_COMMAND_PHASE] = 0x60" in wd33c93_source


# ---------------------------------------------------------------------------
# WD33C93 SCSI Status in TARGET_LUN [CROSS-REF: MAME wd33c9x.cpp:1136]
# ---------------------------------------------------------------------------

class TestWD33C93StatusInTargetLUN:
    """Verify that SCSI device status is stored in TARGET_LUN register.

    MAME stores the SCSI status in TARGET_LUN low bits, preserving TLV (bit 7).
    This is how IRIX reads back CHECK_CONDITION after a command.
    """

    def test_scsi_status_stored_in_target_lun(self, wd33c93_source):
        """SCSI status must be masked to 5 bits and stored in TARGET_LUN."""
        assert "scsi_status & 0x1f" in wd33c93_source

    def test_tlv_bit_preserved(self, wd33c93_source):
        """TARGET_LUN_TLV (bit 7) must be preserved when storing status."""
        assert "TARGET_LUN_TLV" in wd33c93_source
        # The pattern is: (regs[TARGET_LUN] & TARGET_LUN_TLV) | (status & 0x1f)
        assert re.search(
            r"WD_TARGET_LUN\].*TARGET_LUN_TLV",
            wd33c93_source)


# ---------------------------------------------------------------------------
# WD33C93 Transfer Count
# ---------------------------------------------------------------------------

class TestWD33C93TransferCount:
    """Verify transfer count is handled as a 24-bit (3-byte) value."""

    def test_24bit_transfer_count(self, wd33c93_source):
        """Transfer count must be assembled from MSB|MID|LSB (3 bytes)."""
        # The get function should shift MSB by 16, MID by 8, OR LSB
        assert re.search(r"WD_TRANSFER_COUNT_MSB\].*<< 16", wd33c93_source)
        assert re.search(r"WD_TRANSFER_COUNT\].*<< 8", wd33c93_source)
        assert "WD_TRANSFER_COUNT_LSB]" in wd33c93_source

    def test_tc_zero_raises_unexpected_phase(self, wd33c93_source):
        """[CROSS-REF: IRIX wd93.c:2936] When TC=0, WD33C93 must raise
        'unexpected phase' interrupt instead of completing the command.
        IRIX driver relies on this for multi-pass DMA (>256KB transfers)."""
        match = re.search(
            r"wd33c93_transfer_data.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match, "wd33c93_transfer_data function not found"
        body = match.group(1)
        assert "transfer_count == 0" in body
        # Must NOT cancel request on TC=0
        # Find the TC==0 block and verify no scsi_req_cancel
        tc_block = re.search(
            r"if \(s->transfer_count == 0\).*?return;",
            body, re.DOTALL)
        assert tc_block, "TC==0 block not found in transfer_data"
        assert "scsi_req_cancel" not in tc_block.group(0), \
            "TC=0 path must not cancel SCSI request"
        # Must use unexpected phase status codes
        assert "SCSI_STATUS_UNEX_RDATA" in tc_block.group(0) or \
               "SCSI_STATUS_UNEX_SDATA" in tc_block.group(0), \
            "TC=0 path must use unexpected phase status codes"


# ---------------------------------------------------------------------------
# WD33C93 Address Register Behavior
# ---------------------------------------------------------------------------

class TestWD33C93AddressRegister:
    """Verify indirect addressing and auto-increment behavior."""

    def test_addr_5bit_mask(self, wd33c93_source):
        """Address register must be masked to 5 bits (0x1f)."""
        assert "val & 0x1f" in wd33c93_source

    def test_auto_increment(self, wd33c93_source):
        """Address register must auto-increment after data access."""
        # Check for (addr_reg + 1) & 0x1f pattern
        assert re.search(r"addr_reg.*\+.*1.*&.*0x1f", wd33c93_source)

    def test_no_autoinc_aux_status(self, wd33c93_source):
        """AUX_STATUS reads must NOT auto-increment."""
        match = re.search(
            r"case WD_AUXILIARY_STATUS:.*?break;",
            wd33c93_source, re.DOTALL)
        assert match, "AUX_STATUS case not found in data_read"
        body = match.group(0)
        assert "auto_inc = false" in body

    def test_no_autoinc_command(self, wd33c93_source):
        """COMMAND register reads must NOT auto-increment."""
        # Find the COMMAND case in data_read
        match = re.search(
            r"case WD_COMMAND:\s*\n(.*?)break;",
            wd33c93_source, re.DOTALL)
        assert match, "COMMAND case not found in data_read"
        body = match.group(1)
        assert "auto_inc = false" in body


# ---------------------------------------------------------------------------
# WD33C93 IRQ Behavior
# ---------------------------------------------------------------------------

class TestWD33C93IRQBehavior:
    """Verify interrupt handling patterns."""

    def test_status_read_clears_irq(self, wd33c93_source):
        """Reading SCSI_STATUS must clear the ASR_INT interrupt."""
        # Find SCSI_STATUS case in data_read
        match = re.search(
            r"case WD_SCSI_STATUS:.*?break;",
            wd33c93_source, re.DOTALL)
        assert match, "SCSI_STATUS case not found in data_read"
        body = match.group(0)
        assert "lower_irq" in body

    def test_new_cmd_clears_pending_irq(self, wd33c93_source):
        """Issuing a new command must clear any pending stale IRQ."""
        # execute_cmd should check ASR_INT and lower it
        match = re.search(
            r"wd33c93_execute_cmd.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match, "wd33c93_execute_cmd function not found"
        body = match.group(1)
        assert "ASR_INT" in body
        assert "lower_irq" in body


# ---------------------------------------------------------------------------
# HPC3 SCSI DMA Descriptor Format [CROSS-REF: MAME hpc3.cpp]
# ---------------------------------------------------------------------------

class TestHPC3SCSIDMADescriptor:
    """Verify HPC3 SCSI DMA descriptor format matches MAME."""

    def test_descriptor_big_endian(self, hpc3_source):
        """DMA descriptors must be read in big-endian format."""
        assert "address_space_ldl_be" in hpc3_source

    def test_xie_routes_local1_hpc_dma(self, hpc3_source):
        """XIE bit must route interrupt to INT3_LOCAL1_HPC_DMA."""
        assert "INT3_LOCAL1_HPC_DMA" in hpc3_source
        # Verify the XIE → LOCAL1 connection
        assert "HPC3_BC_XIE" in hpc3_source

    def test_eox_disables_dma(self, hpc3_source):
        """EOX bit must disable the DMA engine."""
        assert "HPC3_BC_EOX" in hpc3_source
        # EOX should set scsi_dma_active to false
        match = re.search(r"HPC3_BC_EOX.*?scsi_dma_active\[ch\]\s*=\s*false",
                          hpc3_source, re.DOTALL)
        assert match, "EOX does not set scsi_dma_active to false"

    def test_count_mask_14bit(self, hpc3_source):
        """HPC3_BC_COUNT_MASK must be 0x3fff (14-bit byte count)."""
        assert re.search(r"#define\s+HPC3_BC_COUNT_MASK\s+0x3fff",
                         hpc3_source)


# ---------------------------------------------------------------------------
# HPC3 SCSI CTRL Write Behavior
# ---------------------------------------------------------------------------

class TestHPC3SCSICtrlWrite:
    """Verify SCSI DMA control register write semantics."""

    def test_wrmask_preserves_enable(self, hpc3_source):
        """WRMASK bit must preserve the current ENABLE state."""
        # Look for the WRMASK handling pattern
        match = re.search(
            r"HPC3_DMACTRL_WRMASK\).*?\{(.*?)\}",
            hpc3_source, re.DOTALL)
        assert match, "WRMASK handling block not found"
        body = match.group(1)
        assert "HPC3_DMACTRL_ENABLE" in body

    def test_enable_fetches_chain(self, hpc3_source):
        """Setting ENABLE must fetch the first DMA descriptor."""
        assert "scsi_dma_fetch_chain" in hpc3_source
        # The fetch should happen when dma_active becomes true
        match = re.search(
            r"scsi_dma_active\[ch\]\).*?scsi_dma_fetch_chain",
            hpc3_source, re.DOTALL)
        assert match, "ENABLE does not trigger descriptor fetch"

    def test_flush_disables_dma(self, hpc3_source):
        """FLUSH bit must clear ENABLE and count."""
        match = re.search(
            r"HPC3_DMACTRL_FLUSH\).*?\{(.*?)\}",
            hpc3_source, re.DOTALL)
        assert match, "FLUSH handling block not found"
        body = match.group(1)
        assert "scsi_dma_active[ch] = false" in body


# ---------------------------------------------------------------------------
# HPC3 SCSI IRQ Routing
# ---------------------------------------------------------------------------

class TestHPC3SCSIIRQRouting:
    """Verify SCSI interrupt routing through INT3 to CPU."""

    def test_scsi0_irq_to_local0_scsi0(self, hpc3_header):
        """SCSI0 IRQ must map to INT3_LOCAL0_SCSI0 (0x02)."""
        assert re.search(r"#define\s+INT3_LOCAL0_SCSI0\s+0x02", hpc3_header)

    def test_scsi0_irq_to_cpu_ip2(self, hpc3_source):
        """Local0 interrupts must route to CPU env.irq (cpu_irq[0])."""
        assert "cpu_irq[0]" in hpc3_source

    def test_dma_irq_to_local1(self, hpc3_header):
        """HPC DMA complete must map to INT3_LOCAL1_HPC_DMA (0x10)."""
        assert re.search(r"#define\s+INT3_LOCAL1_HPC_DMA\s+0x10", hpc3_header)


# ---------------------------------------------------------------------------
# SCSI Bus Configuration [CROSS-REF: WD33C93 datasheet]
# ---------------------------------------------------------------------------

class TestSCSIBusConfig:
    """Verify SCSI bus configuration constants."""

    def test_tcq_disabled(self, wd33c93_source):
        """Tagged Command Queuing must be disabled (.tcq = false)."""
        assert ".tcq = false" in wd33c93_source

    def test_max_target_7(self, wd33c93_source):
        """Maximum target ID must be 7."""
        assert ".max_target = 7" in wd33c93_source

    def test_max_lun_7(self, wd33c93_source):
        """Maximum LUN must be 7."""
        assert ".max_lun = 7" in wd33c93_source


# ---------------------------------------------------------------------------
# HPC3 SCSI Dual Channel
# ---------------------------------------------------------------------------

class TestHPC3SCSIDualChannel:
    """Verify dual SCSI channel support (SCSI0 always, SCSI1 Indigo2 only)."""

    def test_scsi0_always_present(self, hpc3_source):
        """SCSI channel 0 must always be created."""
        # scsi[0] should be created unconditionally
        assert "scsi[0]" in hpc3_source

    def test_scsi1_ip22_only(self, hpc3_source):
        """SCSI channel 1 must only be created for BOARD_IP22 (Indigo2)."""
        assert re.search(r"board_type\s*==\s*BOARD_IP22.*scsi\[1\]",
                         hpc3_source, re.DOTALL)

    def test_scsi0_reg_offset(self, hpc3_header):
        """HPC3_SCSI0_REG must be at offset 0x40000."""
        assert re.search(r"#define\s+HPC3_SCSI0_REG\s+0x40000", hpc3_header)

    def test_scsi1_reg_offset(self, hpc3_header):
        """HPC3_SCSI1_REG must be at offset 0x48000."""
        assert re.search(r"#define\s+HPC3_SCSI1_REG\s+0x48000", hpc3_header)


# ---------------------------------------------------------------------------
# WD33C93 CDB Length Detection
# ---------------------------------------------------------------------------

class TestWD33C93CDBLength:
    """Verify CDB length is determined correctly from command group."""

    def test_group0_6byte_cdb(self, wd33c93_source):
        """SCSI command group 0 must use 6-byte CDB."""
        # Look for group 0 → 6 in CDB length detection
        assert re.search(r"case 0:.*?cdb_len.*?=.*?6", wd33c93_source,
                         re.DOTALL)

    def test_group1_10byte_cdb(self, wd33c93_source):
        """SCSI command groups 1 and 2 must use 10-byte CDB."""
        assert re.search(r"case 1:.*?case 2:.*?10", wd33c93_source,
                         re.DOTALL)

    def test_group5_12byte_cdb(self, wd33c93_source):
        """SCSI command group 5 must use 12-byte CDB."""
        assert re.search(r"case 5:.*?12", wd33c93_source, re.DOTALL)

    def test_max_cdb_12(self, wd33c93_source):
        """CDB length must be capped at 12 bytes."""
        assert re.search(r"cdb_len.*>.*12.*cdb_len.*=.*12",
                         wd33c93_source, re.DOTALL)


# ---------------------------------------------------------------------------
# WD33C93 EAF (Extended Advanced Features)
# ---------------------------------------------------------------------------

class TestWD33C93EAF:
    """Verify EAF flag handling in OWN_ID register."""

    def test_eaf_bit_defined(self, wd33c93_header):
        """OWN_ID_EAF must be defined as 0x08."""
        assert re.search(r"#define\s+OWN_ID_EAF\s+0x08", wd33c93_header)

    def test_eaf_reset_status(self, wd33c93_source):
        """Reset with EAF set must return SCSI_STATUS_RESET_EAF (0x01)."""
        assert "SCSI_STATUS_RESET_EAF" in wd33c93_source
        # Check that it's conditionally set based on EAF
        assert re.search(r"OWN_ID_EAF.*RESET_EAF", wd33c93_source,
                         re.DOTALL)

    def test_eaf_controls_cdb_length(self, wd33c93_source):
        """EAF flag in OWN_ID must control CDB length source."""
        assert re.search(r"OWN_ID.*EAF.*cdb_len", wd33c93_source, re.DOTALL)


# ---------------------------------------------------------------------------
# WD33C93 Control Register DMA Mode
# ---------------------------------------------------------------------------

class TestWD33C93ControlDMAMode:
    """Verify DMA mode select bits in the Control register."""

    def test_dm_polled_0x00(self, wd33c93_header):
        """CONTROL_DM_POLLED must be 0x00."""
        assert re.search(r"#define\s+CONTROL_DM_POLLED\s+0x00",
                         wd33c93_header)

    def test_dm_mask_0xe0(self, wd33c93_header):
        """CONTROL_DM_MASK must be 0xe0 (top 3 bits)."""
        assert re.search(r"#define\s+CONTROL_DM_MASK\s+0xe0", wd33c93_header)

    def test_drq_only_in_dma_mode(self, wd33c93_source):
        """DRQ must only be raised when DMA mode is not POLLED."""
        assert re.search(r"CONTROL_DM.*POLLED", wd33c93_source)


# ---------------------------------------------------------------------------
# WD33C93 FIFO and State Management
# ---------------------------------------------------------------------------

class TestWD33C93StateManagement:
    """Verify FIFO and state management patterns."""

    def test_fifo_created_with_correct_size(self, wd33c93_source):
        """FIFO must be created with WD33C93_FIFO_SIZE."""
        assert "fifo8_create(&s->fifo, WD33C93_FIFO_SIZE)" in wd33c93_source

    def test_fifo_reset_on_device_reset(self, wd33c93_source):
        """Device reset must reset the FIFO."""
        match = re.search(
            r"wd33c93_reset.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match, "wd33c93_reset function not found"
        body = match.group(1)
        assert "fifo8_reset" in body

    def test_request_cancelled_clears_state(self, wd33c93_source):
        """Request cancellation must clear current_req and async state."""
        match = re.search(
            r"wd33c93_request_cancelled.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match, "wd33c93_request_cancelled not found"
        body = match.group(1)
        assert "current_req = NULL" in body
        assert "async_len = 0" in body


# ---------------------------------------------------------------------------
# WD33C93 VMState
# ---------------------------------------------------------------------------

class TestWD33C93VMState:
    """Verify migration state includes critical fields."""

    def test_vmstate_includes_addr_reg(self, wd33c93_source):
        """VMState must include addr_reg."""
        assert "VMSTATE_UINT8(addr_reg" in wd33c93_source

    def test_vmstate_includes_regs(self, wd33c93_source):
        """VMState must include register array."""
        assert "VMSTATE_UINT8_ARRAY(regs" in wd33c93_source

    def test_vmstate_includes_transfer_count(self, wd33c93_source):
        """VMState must include transfer_count."""
        assert "VMSTATE_UINT32(transfer_count" in wd33c93_source

    def test_vmstate_includes_fifo(self, wd33c93_source):
        """VMState must include FIFO state."""
        assert "VMSTATE_FIFO8(fifo" in wd33c93_source

    def test_vmstate_includes_pending_len(self, wd33c93_source):
        """VMState must include pending_len for multi-pass DMA migration."""
        assert "VMSTATE_UINT32(pending_len" in wd33c93_source


# ---------------------------------------------------------------------------
# WD33C93 Multi-Pass DMA Support [CROSS-REF: IRIX wd93.c]
# ---------------------------------------------------------------------------

class TestWD33C93MultiPassDMA:
    """Verify multi-pass DMA support for large SCSI transfers (>256KB).

    IRIX allocates 64 HPC3 DMA descriptors per SCSI channel (NSCSI_DMA_PGS=64
    in irix/kern/sys/IP22.h). Each covers one 4KB page, so a single DMA map
    can transfer at most 256KB. For larger transfers, the IRIX WD93 driver
    uses a multi-pass mechanism triggered by "unexpected phase" interrupts
    (ST_UNEX_RDATA=0x48, ST_UNEX_SDATA=0x49).

    References:
      - IRIX wd93.c:2936 — ST_UNEX_SDATA/RDATA handler
      - IRIX IP22.h:457 — NSCSI_DMA_PGS=64
      - WD33C93B datasheet — status 0x48/0x49
    """

    def test_unex_phase_status_codes_defined(self, wd33c93_header):
        """WD33C93 must define UNEX_RDATA (0x48) and UNEX_SDATA (0x49)."""
        assert re.search(
            r"#define\s+SCSI_STATUS_UNEX_RDATA\s+0x48", wd33c93_header)
        assert re.search(
            r"#define\s+SCSI_STATUS_UNEX_SDATA\s+0x49", wd33c93_header)

    def test_wd33c93_has_pending_data_fields(self, wd33c93_header):
        """WD33C93State must have pending_len and pending_buf for multi-pass."""
        assert "pending_len" in wd33c93_header
        assert "pending_buf" in wd33c93_header

    def test_tc_zero_does_not_cancel_request(self, wd33c93_source):
        """[CROSS-REF: IRIX wd93.c:2936] When TC=0, WD33C93 must raise
        'unexpected phase' interrupt instead of canceling the SCSI request.
        IRIX driver relies on this for multi-pass DMA (>256KB transfers)."""
        # Find the TC==0 block in transfer_data
        match = re.search(
            r"wd33c93_transfer_data.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match, "wd33c93_transfer_data function not found"
        body = match.group(1)
        tc_block = re.search(
            r"if \(s->transfer_count == 0\).*?return;",
            body, re.DOTALL)
        assert tc_block, "TC==0 block not found in transfer_data"
        assert "scsi_req_cancel" not in tc_block.group(0), \
            "TC=0 path must NOT cancel SCSI request"

    def test_tc_zero_saves_pending_data(self, wd33c93_source):
        """TC=0 path must save pending_len and pending_buf for resume."""
        match = re.search(
            r"if \(s->transfer_count == 0\).*?return;",
            wd33c93_source, re.DOTALL)
        assert match, "TC==0 block not found"
        block = match.group(0)
        assert "pending_len" in block, "TC=0 must save pending_len"
        assert "pending_buf" in block, "TC=0 must save pending_buf"

    def test_transfer_info_resumes_pending_data(self, wd33c93_source):
        """[CROSS-REF: IRIX wd93.c:2936-2956] TRANSFER_INFO must have a path
        to resume a transfer after 'unexpected phase' interrupt. Driver
        reprograms TC, issues TRANSFER_INFO, expects DRQ to resume DMA."""
        # Find CMD_TRANSFER_INFO handler
        match = re.search(
            r"case CMD_TRANSFER_INFO:.*?break;",
            wd33c93_source, re.DOTALL)
        assert match, "CMD_TRANSFER_INFO handler not found"
        body = match.group(0)
        assert "pending_len" in body, \
            "TRANSFER_INFO must check pending_len"
        assert "wd33c93_get_transfer_count" in body, \
            "TRANSFER_INFO must read new TC from registers"
        assert "ASR_DBR" in body, \
            "TRANSFER_INFO resume must set DBR"

    def test_pending_state_cleared_on_reset(self, wd33c93_source):
        """pending_len must be cleared on reset to prevent stale state."""
        match = re.search(
            r"wd33c93_do_reset.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match, "wd33c93_do_reset function not found"
        body = match.group(1)
        assert "pending_len = 0" in body

    def test_pending_state_cleared_on_abort(self, wd33c93_source):
        """pending_len must be cleared on abort to prevent stale state."""
        match = re.search(
            r"wd33c93_do_abort.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match, "wd33c93_do_abort function not found"
        body = match.group(1)
        assert "pending_len = 0" in body

    def test_pending_state_cleared_on_complete(self, wd33c93_source):
        """pending_len must be cleared on command completion."""
        match = re.search(
            r"wd33c93_command_complete.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match, "wd33c93_command_complete function not found"
        body = match.group(1)
        assert "pending_len = 0" in body

    def test_pending_state_cleared_on_cancel(self, wd33c93_source):
        """pending_len must be cleared on request cancellation."""
        match = re.search(
            r"wd33c93_request_cancelled.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match, "wd33c93_request_cancelled function not found"
        body = match.group(1)
        assert "pending_len = 0" in body

    def test_bsy_and_cip_cleared_on_unexpected_phase(self, wd33c93_source):
        """[CROSS-REF: MAME wd33c9x.cpp:970] On 'unexpected phase' interrupt,
        both CIP and BSY must be cleared — matching MAME's FINISHED state.
        IRIX wd93.c:2424 busy-waits for both to clear before reading status."""
        match = re.search(
            r"if \(s->transfer_count == 0\).*?return;",
            wd33c93_source, re.DOTALL)
        assert match, "TC==0 block not found"
        block = match.group(0)
        # Must NOT call complete_cmd (different status handling)
        assert "wd33c93_complete_cmd" not in block, \
            "TC=0 path must NOT call complete_cmd"
        # Both CIP and BSY should be cleared
        assert "ASR_CIP" in block, "TC=0 path must clear ASR_CIP"
        assert "ASR_BSY" in block, "TC=0 path must clear ASR_BSY"

    def test_command_phase_set_to_0x46_on_unexpected_phase(self, wd33c93_source):
        """[CROSS-REF: MAME wd33c9x.cpp:1294] On unexpected phase, the
        command phase register must be set to COMMAND_PHASE_TRANSFER_COUNT
        (0x46), which IRIX reads as PH_DATA. Without this, IRIX's
        handle_intr won't recognize the multi-pass DMA condition."""
        match = re.search(
            r"if \(s->transfer_count == 0\).*?return;",
            wd33c93_source, re.DOTALL)
        assert match, "TC==0 block not found"
        block = match.group(0)
        assert "0x46" in block, \
            "TC=0 path must set command phase to 0x46 (PH_DATA)"

    def test_unex_rdata_used_for_writes(self, wd33c93_source, hpc3_source):
        """[CROSS-REF: IRIX wd93.c:2930-2935] Status codes are named from the
        WD33C93 chip's perspective: UNEX_RDATA (0x48) = chip Receiving = host
        Writing (DATA OUT). IRIX confirms: ST_UNEX_RDATA with !SCDMA_IN (write).
        Both wd33c93.c (XFER_TO_DEV) and sgi_hpc3.c (dma_to_device) must use
        SCSI_STATUS_UNEX_RDATA for the write direction."""
        # WD33C93: XFER_TO_DEV (write) -> UNEX_RDATA
        assert re.search(
            r"SCSI_XFER_TO_DEV\).*?SCSI_STATUS_UNEX_RDATA",
            wd33c93_source, re.DOTALL), \
            "wd33c93.c: XFER_TO_DEV must map to UNEX_RDATA (0x48)"
        # HPC3: dma_to_device (write) -> UNEX_RDATA
        assert re.search(
            r"scsi_dma_to_device\[ch\].*?SCSI_STATUS_UNEX_RDATA",
            hpc3_source, re.DOTALL), \
            "sgi_hpc3.c: dma_to_device must map to UNEX_RDATA (0x48)"

    def test_unex_sdata_used_for_reads(self, wd33c93_source, hpc3_source):
        """[CROSS-REF: IRIX wd93.c:2930-2935] UNEX_SDATA (0x49) = chip Sending
        = host Reading (DATA IN). IRIX: ST_UNEX_SDATA with SCDMA_IN (read).
        Both files must use SCSI_STATUS_UNEX_SDATA for the read direction."""
        # WD33C93: XFER_FROM_DEV (read) -> UNEX_SDATA (via ternary else)
        assert re.search(
            r"SCSI_STATUS_UNEX_SDATA.*DATA IN|DATA IN.*SCSI_STATUS_UNEX_SDATA",
            wd33c93_source), \
            "wd33c93.c: DATA IN (read) must map to UNEX_SDATA (0x49)"
        # HPC3: !dma_to_device (read) -> UNEX_SDATA
        assert re.search(
            r"SCSI_STATUS_UNEX_SDATA.*read|read.*SCSI_STATUS_UNEX_SDATA",
            hpc3_source), \
            "sgi_hpc3.c: read direction must map to UNEX_SDATA (0x49)"
