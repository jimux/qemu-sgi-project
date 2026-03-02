"""
CP0 Count/Compare timer C source code assertions.

Verifies that the MIPS CP0 timer implementation in QEMU correctly
routes timer interrupts on IP7, uses virtual time, and re-arms
on Compare writes.

Lesson learned: IRIX IP22 uses CP0 Count/Compare (IP7) for the
scheduling clock, NOT the 8254 PIT timer. The timer was always
working correctly, but we spent time investigating it.

These tests are FAST (source code analysis only, no QEMU boot).
"""

import re
import pytest


class TestTimerIRQRouting:
    """Timer must fire on the correct IRQ line."""

    def test_intctl_ipti_default_is_7(self, cpu_source):
        """CP0_IntCtl must default to 0xe0000000 (IPTI bits [31:29] = 7).

        This means the timer fires on irq[7], which is the standard
        MIPS R4000 timer interrupt pin.
        """
        assert "CP0_IntCtl = 0xe0000000" in cpu_source, (
            "CP0_IntCtl default is not 0xe0000000 (IPTI=7)"
        )

    def test_timer_fires_on_ipti_irq(self, cp0_timer_source):
        """cpu_mips_timer_expire must raise irq based on CP0_IntCtl IPTI field."""
        # The expression: env->irq[(env->CP0_IntCtl >> CP0IntCtl_IPTI) & 0x7]
        assert re.search(
            r"qemu_irq_raise\(env->irq\[\(env->CP0_IntCtl >> CP0IntCtl_IPTI\) & 0x7\]\)",
            cp0_timer_source
        ), (
            "Timer expire does not route interrupt via CP0_IntCtl IPTI field"
        )


class TestCompareWriteBehavior:
    """Compare register write must arm timer and ack interrupt."""

    def test_compare_write_arms_timer(self, cp0_timer_source):
        """cpu_mips_store_compare must call cpu_mips_timer_update when DC bit not set."""
        # Find store_compare function
        match = re.search(
            r"void cpu_mips_store_compare.*?\{(.*?)\n\}",
            cp0_timer_source, re.DOTALL
        )
        assert match, "cpu_mips_store_compare function not found"
        body = match.group(1)

        assert "cpu_mips_timer_update" in body, (
            "store_compare does not call cpu_mips_timer_update"
        )

    def test_compare_write_lowers_irq(self, cp0_timer_source):
        """cpu_mips_store_compare must call qemu_irq_lower to ack the interrupt."""
        match = re.search(
            r"void cpu_mips_store_compare.*?\{(.*?)\n\}",
            cp0_timer_source, re.DOTALL
        )
        assert match, "cpu_mips_store_compare function not found"
        body = match.group(1)

        assert "qemu_irq_lower" in body, (
            "store_compare does not lower (ack) the timer interrupt"
        )


class TestCountFromVirtualTime:
    """Count register must be derived from QEMU virtual time."""

    def test_count_uses_virtual_clock(self, cp0_timer_source):
        """cpu_mips_get_count_val must use QEMU_CLOCK_VIRTUAL."""
        match = re.search(
            r"cpu_mips_get_count_val.*?\{(.*?)\n\}",
            cp0_timer_source, re.DOTALL
        )
        assert match, "cpu_mips_get_count_val function not found"
        body = match.group(1)

        assert "QEMU_CLOCK_VIRTUAL" in body, (
            "get_count_val does not use QEMU_CLOCK_VIRTUAL"
        )


class TestTimerInitialization:
    """Timer must be properly initialized."""

    def test_clock_init_creates_timer(self, cp0_timer_source):
        """cpu_mips_clock_init must create a QEMU timer."""
        match = re.search(
            r"void cpu_mips_clock_init.*?\{(.*?)\n\}",
            cp0_timer_source, re.DOTALL
        )
        assert match, "cpu_mips_clock_init function not found"
        body = match.group(1)

        assert "timer_new_ns" in body, (
            "clock_init does not create timer with timer_new_ns"
        )
        assert "QEMU_CLOCK_VIRTUAL" in body, (
            "clock_init timer does not use QEMU_CLOCK_VIRTUAL"
        )
        assert "mips_timer_cb" in body, (
            "clock_init timer does not use mips_timer_cb callback"
        )


# ---------------------------------------------------------------------------
# IRIX Timer Selection [CROSS-REF: IRIX kernel clock.c / ip22_sysid.c]
# ---------------------------------------------------------------------------

class TestIRIXTimerSelection:
    """IRIX kernel timer selection depends on CP0_IntCtl IPTI field.

    [CROSS-REF] The R4000 Count/Compare timer fires on the IP specified
    by IPTI bits [31:29] of CP0_IntCtl. For standard MIPS R4000, this
    is IP7 (IPTI=7, default 0xe0000000).
    """

    def test_intctl_default_0xe0000000(self, cpu_source):
        """[CROSS-REF] CP0_IntCtl defaults to 0xe0000000 (IPTI=7)."""
        assert "CP0_IntCtl = 0xe0000000" in cpu_source

    def test_ipti_7_routes_to_ip7(self):
        """IPTI=7 means timer fires on hardware IP7 (IRIX IP8 / SR_IBIT8)."""
        intctl = 0xe0000000
        ipti = (intctl >> 29) & 7
        assert ipti == 7, f"IPTI should be 7, got {ipti}"

    def test_timer_expire_uses_intctl_ipti(self, cp0_timer_source):
        """Timer expire must use CP0_IntCtl >> CP0IntCtl_IPTI to select IRQ."""
        assert "CP0IntCtl_IPTI" in cp0_timer_source, (
            "Timer code does not reference CP0IntCtl_IPTI"
        )
        # Both raise and lower must use the same IPTI-based routing
        assert cp0_timer_source.count("CP0IntCtl_IPTI") >= 2, (
            "CP0IntCtl_IPTI must be used in both raise and lower paths"
        )


# ---------------------------------------------------------------------------
# IRIX Interrupt Numbering [pure arithmetic — no fixtures needed]
# ---------------------------------------------------------------------------

class TestIRIXInterruptNumbering:
    """IRIX uses 1-based interrupt numbering (IP1 through IP8).

    The MIPS architecture uses 0-based hardware IP numbers (IP0-IP7).
    Status Register bits 15:8 are the interrupt mask (IM) field.
    Cause Register bits 15:8 are the interrupt pending (IP) field.

    IRIX SR_IBIT1 = 0x0100 corresponds to hardware IP0 (bit 8).
    IRIX SR_IBIT8 = 0x8000 corresponds to hardware IP7 (bit 15).
    """

    def test_irix_ibit_is_1_based(self):
        """SR_IBIT1 = 0x0100 is bit 8 of Status (hardware IP0)."""
        sr_ibit1 = 0x0100
        bit_position = sr_ibit1.bit_length() - 1
        assert bit_position == 8, f"SR_IBIT1 should be bit 8, got {bit_position}"
        hw_ip = bit_position - 8
        assert hw_ip == 0, f"SR_IBIT1 should map to hardware IP0, got IP{hw_ip}"

    def test_irix_ip4_is_ibit5(self):
        """SR_IBIT5 = 0x1000 = bit 12 = hardware IP4 (PIT Timer 0)."""
        sr_ibit5 = 0x1000
        bit_position = sr_ibit5.bit_length() - 1
        assert bit_position == 12
        hw_ip = bit_position - 8
        assert hw_ip == 4, f"SR_IBIT5 should be hardware IP4, got IP{hw_ip}"

    def test_irix_ip7_is_ibit8(self):
        """SR_IBIT8 = 0x8000 = bit 15 = hardware IP7 (CP0 timer)."""
        sr_ibit8 = 0x8000
        bit_position = sr_ibit8.bit_length() - 1
        assert bit_position == 15
        hw_ip = bit_position - 8
        assert hw_ip == 7, f"SR_IBIT8 should be hardware IP7, got IP{hw_ip}"

    def test_splhi_masks_ip4_and_below(self):
        """SR_IMASK5 = 0xe000 enables only IP5, IP6, IP7 (IRIX IP6-IP8).

        splhi() sets SR_IMASK5 to mask out the scheduling clock (IP5/IBIT5)
        and everything below, while keeping bus error (IP7) and CP0 timer (IP8).
        """
        sr_imask5 = 0xe000
        enabled_bits = []
        for i in range(8):
            if sr_imask5 & (1 << (i + 8)):
                enabled_bits.append(i)
        assert enabled_bits == [5, 6, 7], (
            f"SR_IMASK5 should enable IP5,IP6,IP7 only, got {enabled_bits}"
        )
