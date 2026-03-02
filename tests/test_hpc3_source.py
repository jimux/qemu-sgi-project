"""
HPC3 C source code assertions.

Verifies that critical code patterns in sgi_hpc3.c are correct,
based on lessons learned from debugging the miniroot kernel hang.

These tests are FAST (source code analysis only, no QEMU boot).
"""

import re
import pytest


class TestTXTimerGating:
    """TX timer must only fire when WR1 TX_INT_ENBL is set.

    Lesson learned: Firing TX_IP before du_open() registers an ISR
    causes the IRIX threaded interrupt handler to permanently mask
    the DUART in map_mask0, killing all serial interrupts.
    """

    def test_tx_timer_uses_wr1_check(self, hpc3_source):
        """serial_write must check scc_wr1[port] & 0x02 before scheduling TX timer.

        The condition should be: if (s->scc_wr1[port] & 0x02)
        NOT: if (serial_write_count > N)
        """
        # Find the serial_write function and check for the WR1 gating
        assert "s->scc_wr1[port] & 0x02" in hpc3_source, (
            "serial_write does not check WR1 TX_INT_ENBL (bit 1) "
            "before scheduling TX timer"
        )

    def test_tx_timer_in_serial_write(self, hpc3_source):
        """The WR1 check should gate the timer_mod call in serial_write."""
        # Find the serial_write function body
        match = re.search(
            r"static void sgi_hpc3_serial_write.*?\{(.*?)\n\}",
            hpc3_source, re.DOTALL
        )
        assert match, "sgi_hpc3_serial_write function not found"
        body = match.group(1)

        # The WR1 check should appear before timer_mod
        wr1_pos = body.find("scc_wr1[port] & 0x02")
        timer_pos = body.find("timer_mod(s->scc_tx_timer[port]")
        assert wr1_pos >= 0, "WR1 check not found in serial_write"
        assert timer_pos >= 0, "timer_mod not found in serial_write"
        assert wr1_pos < timer_pos, (
            "WR1 check must appear before timer_mod in serial_write"
        )

    def test_tx_timer_callback_exists(self, hpc3_source):
        """sgi_hpc3_scc_tx_timer_cb function must exist and set TX_IP."""
        assert "sgi_hpc3_scc_tx_timer_cb" in hpc3_source, (
            "TX timer callback function not found"
        )
        # It should set TX_IP in RR3
        match = re.search(
            r"sgi_hpc3_scc_tx_timer_cb.*?\{(.*?)\n\}",
            hpc3_source, re.DOTALL
        )
        assert match, "Could not parse TX timer callback body"
        body = match.group(1)
        assert "scc_rr3 |=" in body, (
            "TX timer callback does not set TX_IP in scc_rr3"
        )


class TestNVRAMDefaults:
    """NVRAM default values must be correct for serial console."""

    def test_nvram_defaults_set_console_d(self, hpc3_source):
        """sgi_hpc3_nvram_init_defaults must set console to 'd' (serial)."""
        assert "table[NVOFF_CONSOLE] = 'd'" in hpc3_source, (
            "NVRAM defaults do not set console to 'd'"
        )

    def test_nvram_defaults_set_dbaud(self, hpc3_source):
        """NVRAM defaults must include '9600' baud rate."""
        assert '"9600"' in hpc3_source, (
            "NVRAM defaults do not include '9600' baud rate"
        )


class TestSCCTracking:
    """SCC WR1 register tracking."""

    def test_scc_wr1_tracking(self, hpc3_source):
        """scc_wr1[port] must be updated on SCC WR1 writes."""
        # Both ports should be tracked
        assert "scc_wr1[0]" in hpc3_source, "scc_wr1[0] not tracked"
        assert "scc_wr1[1]" in hpc3_source, "scc_wr1[1] not tracked"

    def test_map_mask_registers_exist(self, hpc3_source):
        """MAP_MASK0 and MAP_MASK1 register handling must exist."""
        assert "HPC3_INT3_MAP_MASK0" in hpc3_source, (
            "MAP_MASK0 register handling not found"
        )
        assert "HPC3_INT3_MAP_MASK1" in hpc3_source, (
            "MAP_MASK1 register handling not found"
        )
        # Values must be stored
        assert "int3_map_mask0 = val" in hpc3_source or \
               "int3_map_mask0 =" in hpc3_source, (
            "MAP_MASK0 value not stored"
        )

    def test_lio_duart_bit_defined(self, hpc3_source):
        """LIO_DUART_BIT should be defined as 0x20."""
        assert re.search(r"#define\s+LIO_DUART_BIT\s+0x20", hpc3_source), (
            "LIO_DUART_BIT not defined as 0x20"
        )


class TestSerialWritePolledPath:
    """Serial write must have a polled output path."""

    def test_serial_write_polled_path(self, hpc3_source):
        """serial_write must write to chardev unconditionally (polled path).

        The polled path (qemu_chr_fe_write_all) must NOT require
        interrupts. The PROM and early kernel use polled serial output.
        """
        match = re.search(
            r"static void sgi_hpc3_serial_write.*?\{(.*?)\n\}",
            hpc3_source, re.DOTALL
        )
        assert match, "sgi_hpc3_serial_write not found"
        body = match.group(1)

        assert "qemu_chr_fe_write_all" in body, (
            "serial_write does not call qemu_chr_fe_write_all "
            "(polled output path missing)"
        )

    def test_tx_timer_cb_checks_wr1(self, hpc3_source):
        """TX timer callback must check WR1 TX_INT_ENBL before asserting TX_IP.

        On real Z85C30, TX_IP in RR3 is gated by WR1 TX_INT_ENBL.
        If TX_INT_ENBL has been cleared before the timer fires (e.g.,
        du_init finishes SCC config), no spurious interrupt should occur.
        """
        match = re.search(
            r"static void sgi_hpc3_scc_tx_timer_cb.*?\{(.*?)\n\}",
            hpc3_source, re.DOTALL
        )
        assert match, "sgi_hpc3_scc_tx_timer_cb not found"
        body = match.group(1)

        assert "scc_wr1" in body, (
            "TX timer callback does not check WR1 TX_INT_ENBL — "
            "TX_IP must be gated by WR1 bit 1 to prevent spurious "
            "interrupts during du_init"
        )
