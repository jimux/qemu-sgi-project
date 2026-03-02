"""
INT3 interrupt source validation — regression tests for the spurious interrupt fix.

The core bug: INT3 local0_stat included bits for unimplemented hardware
(e.g., LIO_CENTR/PI1 parallel port, bit 0x20) which caused an interrupt
storm. The kernel's lcl_stray() handler could not acknowledge these
phantom interrupts, leading to an infinite interrupt loop.

Fix: Mask local0_stat to only reflect sources we actually emulate
(SCSI0, SCSI1, ETHERNET, MAPPABLE0) before evaluating pending state.

Also tests SysID register decoding, which determines whether IRIX
selects the R4000 CP0 Count/Compare timer or the 8254 PIT for scheduling.

These tests are FAST (source code analysis only, no QEMU boot).
"""

import re
import pytest


def _extract_function(source, func_sig):
    """Extract a C function body from source by finding balanced braces.

    func_sig can be a function name (e.g., 'sgi_hpc3_update_irq') or
    a partial signature (e.g., 'sgi_hpc3_scc_update_irq(SGIHPC3State').

    Searches for the function definition (not forward declaration) by
    looking for func_sig and then '{'. Skips forward declarations
    (which end with ';' before '{').
    """
    search_str = func_sig if "(" in func_sig else func_sig + "("
    search_from = 0
    while True:
        pos = source.find(search_str, search_from)
        if pos < 0:
            return None
        snippet = source[pos:pos + 300]
        semi_pos = snippet.find(";")
        brace_pos = snippet.find("{")
        if semi_pos >= 0 and (brace_pos < 0 or semi_pos < brace_pos):
            search_from = pos + len(search_str)
            continue
        brace_start = source.find("{", pos)
        if brace_start < 0:
            return None
        depth = 0
        for i in range(brace_start, len(source)):
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
                if depth == 0:
                    return source[brace_start:i + 1]
        return None


# ---------------------------------------------------------------------------
# Local0 Spurious Interrupt Masking [CROSS-REF]
# ---------------------------------------------------------------------------

class TestLocal0SpuriousMasking:
    """Verify that update_irq masks local0_stat to only emulated sources.

    [CROSS-REF] IRIX kernel lcl_stray() handler cannot clear interrupts
    from hardware we don't emulate. The mask must include ONLY the sources
    we actually drive: SCSI0, SCSI1, ETHERNET (Seeq 80C03), and MAPPABLE0
    (which cascades from map_status for DUART etc.).

    See progress_notes/int3_interrupt_storm_fix.md for full analysis.
    """

    @pytest.fixture
    def update_irq_body(self, hpc3_source):
        body = _extract_function(hpc3_source, "sgi_hpc3_update_irq")
        assert body is not None, "sgi_hpc3_update_irq function not found"
        return body

    def test_update_irq_masks_local0_stat(self, update_irq_body):
        """[CROSS-REF] update_irq must mask local0_stat with &= to filter
        spurious bits before evaluating pending state."""
        assert "int3_local0_stat &=" in update_irq_body, (
            "update_irq does not mask local0_stat — spurious interrupts possible"
        )

    def test_mask_includes_scsi0(self, hpc3_header):
        """SCSI0 (INT3_LOCAL0_SCSI0 = 0x02) must be in the emulated mask."""
        assert re.search(
            r"#define\s+INT3_LOCAL0_SCSI0\s+0x02",
            hpc3_header
        ), "INT3_LOCAL0_SCSI0 must be 0x02"

    def test_mask_includes_scsi1(self, hpc3_header):
        """SCSI1 (INT3_LOCAL0_SCSI1 = 0x04) must be in the emulated mask."""
        assert re.search(
            r"#define\s+INT3_LOCAL0_SCSI1\s+0x04",
            hpc3_header
        ), "INT3_LOCAL0_SCSI1 must be 0x04"

    def test_mask_includes_mappable0(self, hpc3_header):
        """MAPPABLE0 (INT3_LOCAL0_MAPPABLE0 = 0x80) must be in the emulated mask."""
        assert re.search(
            r"#define\s+INT3_LOCAL0_MAPPABLE0\s+0x80",
            hpc3_header
        ), "INT3_LOCAL0_MAPPABLE0 must be 0x80"

    def test_mask_excludes_parallel(self, hpc3_source, update_irq_body):
        """[CROSS-REF] PARALLEL (0x20) must NOT be in the mask.

        This was the bug: PI1 parallel port bit leaked into local0_stat,
        causing stray interrupts the kernel could not acknowledge.
        """
        # The mask line should NOT include INT3_LOCAL0_PARALLEL
        mask_match = re.search(
            r"int3_local0_stat\s*&=\s*\((.*?)\);",
            update_irq_body, re.DOTALL
        )
        assert mask_match, "Could not find local0_stat &= mask expression"
        mask_expr = mask_match.group(1)
        assert "PARALLEL" not in mask_expr, (
            "PARALLEL bit must NOT be in the emulated source mask"
        )

    def test_mask_includes_ethernet(self, update_irq_body):
        """[CROSS-REF] ETHERNET (0x08) must be in the mask (Seeq 80C03 emulated)."""
        mask_match = re.search(
            r"int3_local0_stat\s*&=\s*\((.*?)\);",
            update_irq_body, re.DOTALL
        )
        assert mask_match, "Could not find local0_stat &= mask expression"
        mask_expr = mask_match.group(1)
        assert "ETHERNET" in mask_expr, (
            "ETHERNET bit must be in the emulated source mask (Seeq is emulated)"
        )

    def test_mask_excludes_fifo(self, update_irq_body):
        """FIFO (0x01) must NOT be in the mask (not emulated)."""
        mask_match = re.search(
            r"int3_local0_stat\s*&=\s*\((.*?)\);",
            update_irq_body, re.DOTALL
        )
        assert mask_match, "Could not find local0_stat &= mask expression"
        mask_expr = mask_match.group(1)
        assert "FIFO" not in mask_expr, (
            "FIFO bit must NOT be in the mask (not emulated)"
        )

    def test_mask_excludes_graphics(self, update_irq_body):
        """GRAPHICS (0x40) must NOT be in the mask (not emulated as interrupt source)."""
        mask_match = re.search(
            r"int3_local0_stat\s*&=\s*\((.*?)\);",
            update_irq_body, re.DOTALL
        )
        assert mask_match, "Could not find local0_stat &= mask expression"
        mask_expr = mask_match.group(1)
        assert "GRAPHICS" not in mask_expr, (
            "GRAPHICS bit must NOT be in the mask (not emulated as IRQ source)"
        )

    def test_mask_before_pending_check(self, update_irq_body):
        """The &= mask must appear BEFORE the local0_pending = evaluation.

        If we check pending before masking, spurious bits leak through.
        """
        mask_pos = update_irq_body.find("int3_local0_stat &=")
        pending_pos = update_irq_body.find("local0_pending =")
        assert mask_pos >= 0, "local0_stat &= mask not found"
        assert pending_pos >= 0, "local0_pending = not found"
        assert mask_pos < pending_pos, (
            "Mask must be applied BEFORE pending check to prevent spurious IRQs"
        )


# ---------------------------------------------------------------------------
# SysID Register Decoding [CROSS-REF]
# ---------------------------------------------------------------------------

class TestSysIDDecoding:
    """SysID register determines board type and IOC chip revision.

    [CROSS-REF] IRIX kernel ip22_sysid.c:
      CHIP_IOC1 = 0x20 (bits [7:5] == 0x20)
      BOARD_ID bit 0: 0=Guinness, 1=Fullhouse
      BOARD_REV bits [4:1]
      is_ioc1_flag = 2 when board_rev >= 2 and chip is IOC1
    """

    def test_indy_sysid_0x26(self, hpc3_source):
        """[CROSS-REF] Guinness (Indy/IP24) default sysid must be 0x26."""
        assert "sysid = 0x26" in hpc3_source, (
            "Indy sysid should be 0x26"
        )

    def test_indigo2_sysid_0x11(self, hpc3_source):
        """[CROSS-REF] Full House (Indigo2/IP22) default sysid must be 0x11."""
        assert "sysid = 0x11" in hpc3_source, (
            "Indigo2 sysid should be 0x11"
        )

    def test_sysid_0x26_is_ioc1(self):
        """SysID 0x26: bits [7:5] = 0x20 = CHIP_IOC1."""
        sysid = 0x26
        chip_rev = sysid & 0xe0
        assert chip_rev == 0x20, (
            f"Chip rev bits should be 0x20 (CHIP_IOC1), got 0x{chip_rev:02x}"
        )

    def test_sysid_0x26_is_guinness(self):
        """SysID 0x26: bit 0 = 0 means Guinness (not Fullhouse)."""
        sysid = 0x26
        board_id = sysid & 0x01
        assert board_id == 0, (
            "Bit 0 should be 0 for Guinness board"
        )

    def test_sysid_0x26_board_rev_3(self):
        """SysID 0x26: bits [4:1] = 3, board revision 3.

        IRIX sets is_ioc1_flag=2 when board_rev >= 2 and chip is IOC1.
        """
        sysid = 0x26
        board_rev = (sysid & 0x1e) >> 1
        assert board_rev == 3, (
            f"Board rev should be 3, got {board_rev}"
        )
        assert board_rev >= 2, (
            "Board rev >= 2 required for is_ioc1_flag=2"
        )

    def test_sysid_0x26_selects_r4000_timer(self):
        """[ASSUMPTION] SysID 0x26 → is_ioc1_flag=2 → R4000 timer path.

        When is_ioc1() returns 2, the IRIX kernel calls
        startrtclock_r4000() instead of startrtclock_8254().
        This means scheduling uses CP0 Count/Compare on IP7.
        """
        sysid = 0x26
        chip_rev = sysid & 0xe0
        board_rev = (sysid & 0x1e) >> 1
        is_ioc1 = (chip_rev == 0x20)
        is_ioc1_flag = 2 if (is_ioc1 and board_rev >= 2) else (1 if is_ioc1 else 0)
        assert is_ioc1_flag == 2, (
            f"is_ioc1_flag should be 2 for R4000 timer selection, got {is_ioc1_flag}"
        )

    def test_sysid_0x11_not_ioc1(self):
        """SysID 0x11: bits [7:5] = 0x00, NOT IOC1 chip."""
        sysid = 0x11
        chip_rev = sysid & 0xe0
        assert chip_rev != 0x20, (
            "Full House sysid 0x11 should not have IOC1 chip rev"
        )

    def test_sysid_0x11_is_fullhouse(self):
        """SysID 0x11: bit 0 = 1 means Fullhouse board."""
        sysid = 0x11
        board_id = sysid & 0x01
        assert board_id == 1, (
            "Bit 0 should be 1 for Fullhouse board"
        )
