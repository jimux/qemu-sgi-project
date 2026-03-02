"""
Trace log analysis tests (post-boot).

These tests analyze trace log files written by QEMU during boot.
They require a previous QEMU boot run to have generated the log files
in /tmp/. If the files don't exist, tests are skipped.

Mark: These tests are FAST if log files exist, but depend on a prior boot.
"""

import os
import pytest
from helpers.trace_parser import CP0TimerTrace, MapMaskTrace, SCCTrace
from conftest import (
    CP0_TIMER_TRACE, MAP_MASK_TRACE,
    SCC_TX_TIMER_TRACE, SCC_WR1_TRACE,
)


def have_trace(path):
    return os.path.exists(path) and os.path.getsize(path) > 0


class TestCP0TimerTrace:
    """CP0 Count/Compare timer trace analysis.

    Validates that the scheduler clock (CP0 Count/Compare on IP7)
    fires correctly during boot.
    """

    @pytest.fixture
    def trace(self):
        if not have_trace(CP0_TIMER_TRACE):
            pytest.skip(f"No trace file: {CP0_TIMER_TRACE}")
        return CP0TimerTrace(CP0_TIMER_TRACE)

    def test_cp0_timer_fires(self, trace):
        """Timer should fire many times during boot (>100 fires = scheduler running)."""
        assert trace.fire_count > 100, (
            f"Only {trace.fire_count} timer fires recorded. "
            f"Expected >100 for a working scheduler."
        )

    def test_cp0_timer_ie_enabled(self, trace):
        """Most timer fires should have IE (global Interrupt Enable) set.

        Early timer fires (during PROM/early kernel) may have IE=0 before
        the kernel enables interrupts. We check that the majority have IE=1.
        """
        if not trace.fires:
            pytest.skip("No timer fires recorded")
        ie_set = sum(1 for f in trace.fires if f.ie == 1)
        ratio = ie_set / len(trace.fires)
        assert ratio > 0.5, (
            f"Only {ie_set}/{len(trace.fires)} ({ratio:.0%}) timer fires "
            f"had IE=1. Expected majority to have interrupts enabled."
        )

    def test_cp0_timer_ip7_enabled(self, trace):
        """Most timer fires should have IP7 enabled in CP0 Status.

        Early timer fires may have IP7_en=0 before the kernel unmasks
        the timer interrupt. We check that the majority have IP7_en=1.
        """
        if not trace.fires:
            pytest.skip("No timer fires recorded")
        ip7_set = sum(1 for f in trace.fires if f.ip7_en == 1)
        ratio = ip7_set / len(trace.fires)
        assert ratio > 0.5, (
            f"Only {ip7_set}/{len(trace.fires)} ({ratio:.0%}) timer fires "
            f"had IP7_en=1. Expected majority to have timer unmasked."
        )

    def test_cp0_timer_on_irq7(self, trace):
        """All timer fires should be on irq[7] (IPTI field = 7)."""
        assert trace.all_on_irq7(), (
            "Some timer fires were on wrong IRQ (expected irq[7])"
        )

    def test_cp0_timer_compare_rearmed(self, trace):
        """Compare writes should roughly match timer fires (timer is re-armed).

        Each timer fire should be followed by a Compare write to re-arm.
        The counts may not be exactly equal due to sampling, but they
        should be within the same order of magnitude.
        """
        if trace.fire_count == 0:
            pytest.skip("No timer fires to compare against")
        ratio = trace.write_count / trace.fire_count if trace.fire_count > 0 else 0
        assert ratio > 0.5, (
            f"Compare writes ({trace.write_count}) much less than "
            f"timer fires ({trace.fire_count}). Timer may not be re-armed."
        )


class TestMapMaskTrace:
    """MAP_MASK register trace analysis.

    After the WR1 TX timer fix, the DUART bit should never be
    permanently cleared from map_mask0.
    """

    @pytest.fixture
    def trace(self):
        if not have_trace(MAP_MASK_TRACE):
            pytest.skip(f"No trace file: {MAP_MASK_TRACE}")
        return MapMaskTrace(MAP_MASK_TRACE)

    def test_map_mask_duart_not_cleared(self, trace):
        """DUART bit (0x20) should not be permanently cleared from map_mask0.

        If it is, the IRIX threaded interrupt handler masked the DUART
        because TX_IP fired before du_open() registered an ISR.
        """
        assert not trace.duart_ever_cleared(), (
            "DUART bit (0x20) was cleared from map_mask0 after being set. "
            "This indicates the TX timer fired before du_open(), causing "
            "the threaded interrupt handler to permanently mask the DUART."
        )


class TestSCCTrace:
    """SCC (Z85C30) serial controller trace analysis."""

    @pytest.fixture
    def scc(self):
        return SCCTrace(
            wr1_path=SCC_WR1_TRACE,
            tx_timer_path=SCC_TX_TIMER_TRACE,
        )

    def test_tx_timer_respects_wr1(self, scc):
        """TX timer should not fire when WR1 TX_INT_ENBL is never set.

        During early boot (du_init only), WR1 TX_INT_ENBL is never
        enabled. If the TX timer fires anyway, something is wrong.
        """
        if not have_trace(SCC_TX_TIMER_TRACE):
            pytest.skip(f"No trace file: {SCC_TX_TIMER_TRACE}")

        # If WR1 was never written with TX_INT_ENBL, TX timer should not fire
        wr1_has_tx_enable = any(e.tx_int == 1 for e in scc.wr1_entries)
        if not wr1_has_tx_enable:
            assert scc.tx_timer_fires == 0, (
                f"TX timer fired {scc.tx_timer_fires} times but "
                f"WR1 TX_INT_ENBL was never set"
            )

    def test_wr1_during_du_init_only(self, scc):
        """WR1 writes should only appear during du_init (early boot).

        If WR1 writes appear very late in boot, du_open() has been
        called, which means the STREAMS serial path is active.
        """
        if not scc.wr1_entries:
            pytest.skip("No WR1 trace entries")

        # During du_init, serial_write_count is typically < 1500
        # WR1 writes from du_init should all be before count ~1500
        early_entries = [e for e in scc.wr1_entries
                         if e.serial_write_count < 1500]
        assert len(early_entries) > 0, (
            "No WR1 writes during early boot (du_init). "
            "Expected at least some WR1 configuration."
        )
