"""
WD33C93 state machine and HPC3 DMA chain lifecycle tests.

Verifies the complete command lifecycle: SELECT_ATN_XFER → transfer_data →
command_complete, including state transitions, register updates, IRQ/DRQ
signaling, and HPC3 DMA descriptor processing.

These tests are FAST (source code analysis only, no QEMU boot).

Categories:
  - Standard: verifies known-correct state transitions
  - CROSS-REF: verified against MAME wd33c9x.cpp
"""

import re
import pytest


# ---------------------------------------------------------------------------
# SELECT_ATN_XFER Lifecycle
# ---------------------------------------------------------------------------

class TestSelectXferLifecycle:
    """Verify SELECT_ATN_XFER code path via source analysis.

    SELECT_ATN_XFER is the primary command used by IRIX to issue SCSI
    commands. It selects a target and transfers a CDB in one operation.
    """

    def test_cip_bsy_set_on_select(self, wd33c93_source):
        """ASR_CIP | ASR_BSY must be set at start of select_xfer."""
        match = re.search(
            r"wd33c93_do_select_xfer.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match, "wd33c93_do_select_xfer not found"
        body = match.group(1)
        assert "ASR_CIP" in body
        assert "ASR_BSY" in body

    def test_target_from_dest_id(self, wd33c93_source):
        """Target ID must be extracted via DEST_ID_MASK."""
        match = re.search(
            r"wd33c93_do_select_xfer.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert "DEST_ID_MASK" in body

    def test_selection_timeout_no_device(self, wd33c93_source):
        """Missing target must return SCSI_STATUS_SELECTION_TIMEOUT."""
        match = re.search(
            r"wd33c93_do_select_xfer.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert "SCSI_STATUS_SELECTION_TIMEOUT" in body

    def test_cdb_built_from_registers(self, wd33c93_source):
        """CDB must be built from WD_CDB_1 through WD_CDB_N registers."""
        match = re.search(
            r"wd33c93_do_select_xfer.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert "WD_CDB_1" in body
        # CDB is copied in a loop
        assert re.search(r"cdb\[.*\]\s*=\s*s->regs\[WD_CDB_1", body)

    def test_transfer_count_loaded(self, wd33c93_source):
        """Transfer count must be loaded from TC MSB/ISB/LSB registers."""
        match = re.search(
            r"wd33c93_do_select_xfer.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert "wd33c93_get_transfer_count" in body

    def test_command_phase_set_0x10(self, wd33c93_source):
        """Command phase must be set to 0x10 after CDB setup."""
        match = re.search(
            r"wd33c93_do_select_xfer.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert "WD_COMMAND_PHASE] = 0x10" in body

    def test_scsi_req_new_called(self, wd33c93_source):
        """scsi_req_new must be called to create the SCSI request."""
        match = re.search(
            r"wd33c93_do_select_xfer.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert "scsi_req_new" in body

    def test_continue_for_data_commands(self, wd33c93_source):
        """scsi_req_continue must be called when datalen != 0."""
        match = re.search(
            r"wd33c93_do_select_xfer.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert "datalen != 0" in body or "datalen" in body
        assert "scsi_req_continue" in body


# ---------------------------------------------------------------------------
# Transfer Data Callback
# ---------------------------------------------------------------------------

class TestTransferDataCallback:
    """Verify wd33c93_transfer_data behavior.

    Called by the SCSI subsystem when data is available from the device.
    Must set up DMA state and signal data readiness.
    """

    def test_tc_zero_raises_unexpected_phase(self, wd33c93_source):
        """[CROSS-REF: IRIX wd93.c:2936] TC=0 must raise 'unexpected phase'
        interrupt for multi-pass DMA, not complete the command."""
        match = re.search(
            r"wd33c93_transfer_data.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match, "wd33c93_transfer_data not found"
        body = match.group(1)
        assert "transfer_count == 0" in body
        # Must raise IRQ with unexpected phase status, not complete
        assert "wd33c93_raise_irq" in body
        assert "SCSI_STATUS_UNEX_RDATA" in body or \
               "SCSI_STATUS_UNEX_SDATA" in body

    def test_tc_caps_transfer_len(self, wd33c93_source):
        """Transfer length must be capped to remaining TC."""
        match = re.search(
            r"wd33c93_transfer_data.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert re.search(r"len\s*>\s*s->transfer_count", body)

    def test_command_phase_set_0x30(self, wd33c93_source):
        """Command phase must be set to 0x30 during data transfer."""
        match = re.search(
            r"wd33c93_transfer_data.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert "WD_COMMAND_PHASE] = 0x30" in body

    def test_dbr_set(self, wd33c93_source):
        """ASR_DBR must be set when data is available."""
        match = re.search(
            r"wd33c93_transfer_data.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert "ASR_DBR" in body

    def test_drq_raised_in_dma_mode(self, wd33c93_source):
        """DRQ must be asserted when DMA mode is not POLLED."""
        match = re.search(
            r"wd33c93_transfer_data.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert "CONTROL_DM_POLLED" in body
        assert "wd33c93_set_drq" in body

    def test_drq_not_raised_in_polled(self, wd33c93_source):
        """DRQ must NOT be raised in POLLED mode.

        The check is: if DM != POLLED then raise DRQ.
        This means POLLED mode skips the DRQ raise.
        """
        match = re.search(
            r"wd33c93_transfer_data.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        # The condition should be "!= CONTROL_DM_POLLED" before raising DRQ
        assert re.search(
            r"CONTROL_DM_MASK.*!=.*CONTROL_DM_POLLED|"
            r"CONTROL_DM_POLLED.*wd33c93_set_drq",
            body, re.DOTALL)


# ---------------------------------------------------------------------------
# Command Complete Callback
# ---------------------------------------------------------------------------

class TestCommandCompleteCallback:
    """Verify wd33c93_command_complete behavior.

    Called when the SCSI device finishes processing a command.
    Must update registers, clear DMA state, and raise interrupt.
    """

    def test_status_in_target_lun_low_bits(self, wd33c93_source):
        """SCSI status must be masked to 5 bits and stored in TARGET_LUN."""
        match = re.search(
            r"wd33c93_command_complete.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match, "wd33c93_command_complete not found"
        body = match.group(1)
        assert "scsi_status & 0x1f" in body

    def test_tlv_preserved(self, wd33c93_source):
        """TARGET_LUN_TLV (bit 7) must be preserved when storing status."""
        match = re.search(
            r"wd33c93_command_complete.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert "TARGET_LUN_TLV" in body

    def test_wd_status_always_0x16(self, wd33c93_source):
        """WD33C93 status must always be SELECT_TRANSFER_SUCCESS.

        [CROSS-REF] MAME always pushes CSR_SELECT_XFER_DONE (0x16) to the
        IRQ FIFO on command completion — the actual SCSI device status goes
        in TARGET_LUN, not in SCSI_STATUS.
        """
        match = re.search(
            r"wd33c93_command_complete.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert "SCSI_STATUS_SELECT_TRANSFER_SUCCESS" in body

    def test_command_phase_set_0x60(self, wd33c93_source):
        """Command phase must be set to 0x60 on completion."""
        match = re.search(
            r"wd33c93_command_complete.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert "WD_COMMAND_PHASE] = 0x60" in body

    def test_drq_lowered(self, wd33c93_source):
        """DRQ must be deasserted on command completion."""
        match = re.search(
            r"wd33c93_command_complete.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert "wd33c93_set_drq(s, false)" in body

    def test_dbr_cleared(self, wd33c93_source):
        """ASR_DBR must be cleared on command completion."""
        match = re.search(
            r"wd33c93_command_complete.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert "~ASR_DBR" in body

    def test_irq_raised(self, wd33c93_source):
        """IRQ must be raised via wd33c93_complete_cmd."""
        match = re.search(
            r"wd33c93_command_complete.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert "wd33c93_complete_cmd" in body

    def test_current_req_released(self, wd33c93_source):
        """current_req must be unref'd and set to NULL."""
        match = re.search(
            r"wd33c93_command_complete.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert "scsi_req_unref" in body
        assert "current_req = NULL" in body


# ---------------------------------------------------------------------------
# HPC3 DMA Chain
# ---------------------------------------------------------------------------

class TestHPC3DMAChain:
    """Verify HPC3 SCSI DMA descriptor processing.

    The HPC3 reads linked lists of DMA descriptors from memory.
    Each descriptor has: CBP (4 bytes) + BC (4 bytes) + NBDP (4 bytes).
    """

    def test_descriptor_12_bytes(self, hpc3_source):
        """DMA descriptors are 12 bytes: CBP + BC + NBDP, read via 3 loads."""
        # Three sequential address_space_ldl_be calls for cbp, bc, nbdp
        matches = re.findall(r"address_space_ldl_be.*desc_addr", hpc3_source)
        assert len(matches) >= 3, (
            f"Expected 3+ descriptor reads, found {len(matches)}")

    def test_count_mask(self, hpc3_source):
        """BC must be masked with HPC3_BC_COUNT_MASK (14-bit)."""
        assert re.search(r"HPC3_BC_COUNT_MASK", hpc3_source)
        assert re.search(r"#define\s+HPC3_BC_COUNT_MASK\s+0x3fff",
                         hpc3_source)

    def test_xie_sets_local1_hpc_dma(self, hpc3_source):
        """XIE bit must trigger INT3_LOCAL1_HPC_DMA interrupt."""
        # XIE check followed by setting IRQ
        match = re.search(
            r"HPC3_BC_XIE.*?INT3_LOCAL1_HPC_DMA",
            hpc3_source, re.DOTALL)
        assert match, "XIE → INT3_LOCAL1_HPC_DMA path not found"

    def test_eox_stops_dma(self, hpc3_source):
        """EOX bit must stop the DMA engine (set scsi_dma_active = false)."""
        match = re.search(
            r"HPC3_BC_EOX.*?scsi_dma_active\[ch\]\s*=\s*false",
            hpc3_source, re.DOTALL)
        assert match, "EOX does not stop DMA"

    def test_eox_drain_loop(self, hpc3_source):
        """Zero-count terminal descriptors must be drained after EOX.

        When the DMA engine sees a descriptor chain ending in descriptors
        with count=0, it must process them to reach the EOX descriptor.
        """
        # Look for the drain loop that processes zero-count descriptors
        assert re.search(
            r"while.*scsi_dma_active.*scsi_dma_count.*==\s*0",
            hpc3_source, re.DOTALL)

    def test_dma_direction_flag(self, hpc3_source):
        """HPC3_DMACTRL_DIR must be checked for transfer direction."""
        assert "HPC3_DMACTRL_DIR" in hpc3_source
        assert re.search(r"scsi_dma_to_device", hpc3_source)

    def test_chunk_limited_by_async_len(self, hpc3_source):
        """DMA chunk size must be limited by async_len from WD33C93."""
        # The DMA handler should check wdc->async_len
        assert re.search(r"async_len", hpc3_source)

    def test_address_space_rw_called(self, hpc3_source):
        """address_space_read/write must be used for DMA data transfer."""
        assert "address_space_read" in hpc3_source
        assert "address_space_write" in hpc3_source


# ---------------------------------------------------------------------------
# Select without Transfer (SELECT/SELECT_ATN)
# ---------------------------------------------------------------------------

class TestSelectWithoutTransfer:
    """Verify SELECT and SELECT_ATN commands (without data transfer).

    These are used by IRIX when it needs to select a target but send the
    CDB separately via TRANSFER_INFO.
    """

    def test_select_sets_cip_bsy(self, wd33c93_source):
        """SELECT must set ASR_CIP | ASR_BSY."""
        match = re.search(
            r"wd33c93_do_select\b.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match, "wd33c93_do_select not found"
        body = match.group(1)
        assert "ASR_CIP" in body
        assert "ASR_BSY" in body

    def test_select_success_status(self, wd33c93_source):
        """Successful SELECT must return SCSI_STATUS_SELECT_SUCCESS."""
        match = re.search(
            r"wd33c93_do_select\b.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert "SCSI_STATUS_SELECT_SUCCESS" in body

    def test_select_timeout_on_no_device(self, wd33c93_source):
        """SELECT with no target must return SELECTION_TIMEOUT."""
        match = re.search(
            r"wd33c93_do_select\b.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert "SCSI_STATUS_SELECTION_TIMEOUT" in body


# ---------------------------------------------------------------------------
# TRANSFER_INFO after SELECT
# ---------------------------------------------------------------------------

class TestTransferInfoAfterSelect:
    """Verify TRANSFER_INFO builds and executes SCSI command after SELECT.

    When IRIX uses SELECT + TRANSFER_INFO (instead of SELECT_ATN_XFER),
    TRANSFER_INFO must build the CDB from registers and execute it.
    """

    def test_transfer_info_builds_cdb_after_select(self, wd33c93_source):
        """TRANSFER_INFO must build CDB when no current_req exists.

        This is the SELECT + TRANSFER_INFO path where SELECT was done
        but no SCSI request has been created yet.
        """
        # Extract the full TRANSFER_INFO block up to the next top-level case
        match = re.search(
            r"case CMD_TRANSFER_INFO:(.*?)(?:\n    case |\n    default:)",
            wd33c93_source, re.DOTALL)
        assert match, "CMD_TRANSFER_INFO case not found"
        body = match.group(1)
        # Must check for !current_req && current_dev
        assert "!s->current_req" in body
        assert "s->current_dev" in body
        # Must create a new request
        assert "scsi_req_new" in body

    def test_transfer_info_raises_drq_for_active_req(self, wd33c93_source):
        """TRANSFER_INFO must raise DRQ when data is available."""
        match = re.search(
            r"case CMD_TRANSFER_INFO:(.*?)(?:\n    case |\n    default:)",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert "wd33c93_set_drq" in body or "ASR_DBR" in body


# ---------------------------------------------------------------------------
# Request Cancelled Callback
# ---------------------------------------------------------------------------

class TestRequestCancelled:
    """Verify wd33c93_request_cancelled cleanup behavior."""

    def test_cancelled_clears_current_req(self, wd33c93_source):
        """Cancelled request must unref and NULL current_req."""
        match = re.search(
            r"wd33c93_request_cancelled.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match, "wd33c93_request_cancelled not found"
        body = match.group(1)
        assert "scsi_req_unref" in body
        assert "current_req = NULL" in body

    def test_cancelled_clears_async_state(self, wd33c93_source):
        """Cancelled request must clear async_len and async_buf."""
        match = re.search(
            r"wd33c93_request_cancelled.*?\{(.*?)\n\}",
            wd33c93_source, re.DOTALL)
        assert match
        body = match.group(1)
        assert "async_len = 0" in body
        assert "async_buf = NULL" in body


# ---------------------------------------------------------------------------
# HPC3 DMA Control Register Writes
# ---------------------------------------------------------------------------

class TestHPC3DMACtrlWrite:
    """Verify HPC3 SCSI DMA control register write semantics."""

    def test_wrmask_preserves_enable(self, hpc3_source):
        """WRMASK bit must preserve the current ENABLE state."""
        match = re.search(
            r"HPC3_DMACTRL_WRMASK\).*?\{(.*?)\}",
            hpc3_source, re.DOTALL)
        assert match, "WRMASK handling not found"
        body = match.group(1)
        assert "HPC3_DMACTRL_ENABLE" in body

    def test_enable_fetches_first_descriptor(self, hpc3_source):
        """Setting ENABLE must fetch the first DMA descriptor."""
        assert re.search(
            r"scsi_dma_active\[ch\].*?scsi_dma_fetch_chain",
            hpc3_source, re.DOTALL)

    def test_flush_disables_dma(self, hpc3_source):
        """FLUSH must clear ENABLE and stop DMA."""
        match = re.search(
            r"HPC3_DMACTRL_FLUSH\).*?\{(.*?)\}",
            hpc3_source, re.DOTALL)
        assert match, "FLUSH handling not found"
        body = match.group(1)
        assert "scsi_dma_active[ch] = false" in body
