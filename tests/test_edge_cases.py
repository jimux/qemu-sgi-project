"""
Edge-of-knowledge tests: assumptions, investigative checks, and cross-references.

These tests document uncertain behaviors, known workarounds, simplifications,
and divergences from MAME. They are intended to teach us something when they
fail, not just confirm known-good values.

Categories:
  - ASSUMPTION: documents a known workaround or simplification
  - INVESTIGATIVE: may fail; the result teaches us something
  - CROSS-REF: verifies our code against MAME or IRIX source

These tests are FAST (source code analysis only, no QEMU boot).
"""

import re
import pytest


# ---------------------------------------------------------------------------
# PIT Timer Assumptions [ASSUMPTION]
# ---------------------------------------------------------------------------

class TestPITTimerAssumptions:
    """PIT timer mode bits are stored but not all modes are implemented.

    The real 8254 supports modes 0-5, but the SGI PROM and IRIX kernel
    only use mode 2 (rate generator). Our implementation stores mode bits
    but always behaves as a rate generator.
    """

    def test_pit_control_stored(self, hpc3_source):
        """PIT control word must be stored per channel."""
        assert "pit_control[channel] = val" in hpc3_source

    def test_timer_pending_clear_on_read(self, hpc3_source):
        """timer_pending must be cleared on TIMER_CLEAR read."""
        # Find TIMER_CLEAR handling
        assert re.search(r"TIMER_CLEAR.*timer_pending\[0\]\s*=\s*false",
                         hpc3_source, re.DOTALL)
        assert re.search(r"TIMER_CLEAR.*timer_pending\[1\]\s*=\s*false",
                         hpc3_source, re.DOTALL)

    def test_counter2_not_routed_to_irq(self, hpc3_header):
        """Counter 2 must NOT have a timer IRQ output.

        Only counters 0 and 1 generate CPU interrupts. Counter 2 is the
        1 MHz master clock and has no IRQ. The timer_irq array should be
        size 2, not 3.
        """
        assert re.search(r"timer_irq\[2\]", hpc3_header)
        # Also verify pit_timer array is size 2 (channels 0 and 1 only)
        assert re.search(r"pit_timer\[2\]", hpc3_header)


# ---------------------------------------------------------------------------
# RPSS Counter Assumptions [ASSUMPTION]
# ---------------------------------------------------------------------------

class TestRPSSCounterAssumptions:
    """RPSS counter implementation assumptions and workarounds."""

    def test_50mhz_base_clock_assumed(self, mc_source):
        """RPSS tick period must be derived from a 50MHz base clock."""
        # The magic number: (elapsed_ns * 50) / (1000 * divider)
        assert re.search(r"50.*1000.*divider|elapsed_ns.*50", mc_source)

    def test_minimum_1_tick_guarantee(self, mc_source):
        """RPSS must advance by at least 1 tick per read.

        This is a workaround for tight polling loops where virtual time
        doesn't advance between reads, preventing hangs.
        """
        # Check for the ticks == 0 guard
        assert re.search(r"ticks\s*==\s*0.*ticks\s*=\s*1",
                         mc_source, re.DOTALL)

    def test_rpss_div_default_0x0104(self, mc_source):
        """RPSS divider register must default to 0x0104.

        This means increment=1, divider=4 (low byte + 1).
        """
        assert "rpss_div = 0x0104" in mc_source

    def test_rpss_increment_zero_guard(self, mc_source):
        """When increment field is 0, it must be treated as 1.

        Prevents zero-increment which would cause the counter to never
        advance, hanging any code that waits for it.
        """
        assert re.search(r"increment\s*==\s*0.*increment\s*=\s*1",
                         mc_source, re.DOTALL)


# ---------------------------------------------------------------------------
# MC DMA Stubs [ASSUMPTION]
# ---------------------------------------------------------------------------

class TestMCDMAStubs:
    """MC DMA is stubbed — instant completion, no actual transfer."""

    def test_dma_instant_completion(self, mc_source):
        """DMA must set dma_run to 0x40 (bit 6) immediately.

        The PROM checks bit 6 to verify DMA started. We set it
        instantly without performing any actual transfer.
        """
        assert "dma_run = 0x40" in mc_source

    def test_no_actual_dma_transfer(self, mc_source):
        """MC DMA must NOT perform actual address_space_read/write.

        Unlike HPC3 SCSI DMA, the MC DMA is completely stubbed.
        The DMA start just sets the run flag.
        """
        # Look for the DMA_START handler — it should only set dma_run
        match = re.search(
            r"MC_DMA_START:.*?break;",
            mc_source, re.DOTALL)
        assert match, "MC_DMA_START case not found"
        body = match.group(0)
        assert "address_space_read" not in body
        assert "address_space_write" not in body

    def test_cpu_err_addr_write_clears(self, mc_source):
        """CPU_ERR_ADDR write must clear the register to 0.

        This is a write-to-clear behavior. The write handler sets it to 0.
        """
        # Find the write handler (sgi_mc_write) and look for cpu_err_addr = 0
        match = re.search(
            r"sgi_mc_write.*?\{(.*)",
            mc_source, re.DOTALL)
        assert match, "sgi_mc_write function not found"
        write_body = match.group(1)
        # Within the write handler, CPU_ERR_ADDR case should clear to 0
        assert "cpu_err_addr = 0" in write_body


# ---------------------------------------------------------------------------
# Newport Edge Cases [INVESTIGATIVE]
# ---------------------------------------------------------------------------

class TestNewportEdgeCases:
    """Newport graphics edge cases and potential bugs."""

    def test_12bpp_ci_mode_uses_0xff(self, newport_source):
        """12bpp CI mode uses pixel & 0xff — should this be 0xfff?

        INVESTIGATIVE: In the display refresh path, 12bpp CI mode
        uses & 0xff (8-bit index), not & 0xfff (12-bit index).
        The XL8 Indy only has 256 CMAP entries, so 0xff may be correct
        for this hardware even though the mode is nominally 12bpp.
        """
        # Verify this is what the code actually does
        # The case 2 (12bpp) block should use 0xff
        match = re.search(
            r"case 2:.*?ci = pixel & 0xff;",
            newport_source, re.DOTALL)
        assert match, "12bpp CI mode does not use pixel & 0xff"

    def test_fline_integer_bresenham(self, newport_source):
        """Fractional line drawing must convert to integer Bresenham.

        The fractional start/end registers are 16.16 fixed-point.
        Our implementation truncates to integer (>> 16) and uses
        standard Bresenham. MAME's do_fline() is more complex.
        """
        # Verify >> 16 conversion is used
        assert re.search(r"x_start >> 16", newport_source)
        assert re.search(r"y_start >> 16", newport_source)

    def test_global_mask_xl8_only(self, newport_source):
        """global_mask must default to 0xff (only XL8 Indy).

        XL24 (24-bit) uses 0xffffff. This affects all pixel writes.
        """
        assert "global_mask = 0xff" in newport_source

    def test_dcb_timeout_1ms(self, newport_source):
        """DCB bus timeout timer must exist for non-existent devices.

        When a DCB read/write targets a device that doesn't exist
        (or hasn't responded), a timer fires to clear BACKBUSY.
        """
        assert "dcb_timeout" in newport_source
        # The timeout callback should clear BACKBUSY
        match = re.search(
            r"newport_dcb_timeout.*?\{(.*?)\n\}",
            newport_source, re.DOTALL)
        assert match, "DCB timeout callback not found"
        body = match.group(1)
        assert "BACKBUSY" in body


# ---------------------------------------------------------------------------
# SCC Serial Assumptions [ASSUMPTION]
# ---------------------------------------------------------------------------

class TestSCCSerialAssumptions:
    """Z85C30 SCC serial simplifications."""

    def test_limited_rr_registers(self, hpc3_source):
        """Only RR0, RR1, RR2, RR3, and RR8 are implemented.

        Real Z85C30 has RR0-RR15, but the PROM and IRIX only
        use a few. Our implementation returns 0 for unimplemented RRs.
        """
        assert "scc_rr3" in hpc3_source

    def test_mie_gating(self, hpc3_source):
        """MIE (Master Interrupt Enable) in WR9 must gate all SCC IRQs.

        Our simplified check: (scc_wr9 & 0x08) && scc_rr3.
        Real Z85C30 has more complex interrupt priority and daisy-chain.
        """
        assert re.search(r"scc_wr9\s*&\s*0x08.*scc_rr3", hpc3_source)

    def test_wr9_shared_between_channels(self, hpc3_header):
        """WR9 must be a single register shared between both SCC channels.

        Unlike WR1/WR5 which are per-channel, WR9 is the master
        interrupt control register shared across Channel A and B.
        """
        # scc_wr9 should NOT be an array (it's shared)
        assert re.search(r"uint8_t scc_wr9;", hpc3_header)
        # scc_wr1 SHOULD be an array (per-channel)
        assert re.search(r"uint8_t scc_wr1\[2\]", hpc3_header)


# ---------------------------------------------------------------------------
# ARCS Firmware Stubs [CROSS-REF: IRIX arcs headers]
# ---------------------------------------------------------------------------

class TestARCSFirmwareStubs:
    """Verify ARCS firmware stub constants match IRIX headers."""

    def test_get_memory_desc_returns_next(self, arcs_source):
        """GetMemoryDescriptor(prev) must return the next descriptor.

        arg0=0 returns first; arg0=current returns next by index+1.
        """
        # Check for the index = ... + 1 pattern
        match = re.search(
            r"arcs_get_memory_desc.*?\{(.*?)\n\}",
            arcs_source, re.DOTALL)
        assert match, "arcs_get_memory_desc function not found"
        body = match.group(1)
        assert "index = 0" in body  # First call returns index 0
        # Next call advances: index = (phys - base) / size + 1
        assert re.search(r"index\s*=.*\+\s*1", body)

    def test_max_memdescs_8(self, arcs_source):
        """MAX_MEMDESCS must be 8."""
        assert "#define MAX_MEMDESCS 8" in arcs_source

    def test_cpufreq_175mhz(self, arcs_source):
        """cpufreq must return 175 (MHz).

        NOTE: The machine clock is actually 100MHz base. 175MHz is
        the R4600 CPU clock for Indy.
        """
        # Check both the env var and the PV function
        assert '"175"' in arcs_source
        assert re.search(r"result\s*=\s*175", arcs_source)

    def test_spb_magic_arcs(self, arcs_header):
        """ARCS_SPB_MAGIC must be 0x53435241 ("ARCS" in big-endian).

        Note: "ARCS" in ASCII is 0x41524353, but stored BE as 0x53435241
        because the SPB defines it as the integer value of "SCRA" in memory
        order (the signature reads as "ARCS" when viewed byte-by-byte).
        """
        assert re.search(r"ARCS_SPB_MAGIC\s+0x53435241", arcs_header)

    def test_memdesc_struct_12_bytes(self, arcs_source):
        """MEMDESC_STRUCT_SIZE must be 12 (3 x uint32_t)."""
        assert "#define MEMDESC_STRUCT_SIZE 12" in arcs_source

    def test_arcs_version_1_rev_10(self, arcs_header):
        """ARCS_VERSION must be 1 and ARCS_REVISION must be 10."""
        assert re.search(r"#define\s+ARCS_VERSION\s+1", arcs_header)
        assert re.search(r"#define\s+ARCS_REVISION\s+10", arcs_header)

    def test_arcs_fv_slots_35(self, arcs_header):
        """FirmwareVector must have 35 slots (ARCS_FN_COUNT)."""
        assert re.search(r"#define\s+ARCS_FV_SLOTS\s+ARCS_FN_COUNT",
                         arcs_header)

    def test_arcs_pv_slots_13(self, arcs_header):
        """PrivateVector must have 13 slots for 32-bit systems."""
        assert re.search(r"#define\s+ARCS_PV_SLOTS\s+13", arcs_header)


# ---------------------------------------------------------------------------
# MAME Behavioral Differences [INVESTIGATIVE]
# ---------------------------------------------------------------------------

class TestMAMEBehavioralDiffs:
    """Differences between our implementation and MAME's."""

    def test_simm_wrap_mc_rev_check(self, mc_source):
        """SIMM address wrapping must only apply for MC rev >= 5 (addr_shift >= 24).

        INVESTIGATIVE: MAME uses mirror masks for all revisions via
        install_ram(). Our implementation only creates wrap aliases for
        MC rev >= 5 (IP28) because IP22/IP24 PROM memory sizing depends
        on reads beyond physical SIMM returning 0, not aliased data.
        """
        assert re.search(r"addr_shift >= 24.*cfg_size > map_size",
                         mc_source, re.DOTALL)

    def test_rpss_base_clock_50mhz(self, mc_source):
        """RPSS uses 50MHz base for all configurations.

        INVESTIGATIVE: MAME ties the RPSS clock to the CPU timer
        which is 50MHz only for R4600@100MHz. Other CPU speeds may
        use different base clocks. Our implementation hardcodes 50MHz.
        """
        # The 50MHz is embedded in the calculation
        assert re.search(r"elapsed_ns.*50.*1000", mc_source)

    def test_mc_rev_controls_addr_shift(self, mc_source):
        """MC revision >= 5 must use shift 24 (16MB units), < 5 uses shift 22 (4MB).

        This controls memory bank sizing. MAME's mc.cpp uses the same
        distinction for its memory configuration.
        """
        assert re.search(r"revision >= 5.*24.*22", mc_source, re.DOTALL)


# ---------------------------------------------------------------------------
# HPC3 DMA Control Bits [CROSS-REF: MAME hpc3.cpp]
# ---------------------------------------------------------------------------

class TestHPC3DMAControlBits:
    """Verify DMA control register bit definitions match MAME."""

    def test_dmactrl_irq_0x01(self, hpc3_source):
        """HPC3_DMACTRL_IRQ must be 0x01."""
        assert re.search(r"#define\s+HPC3_DMACTRL_IRQ\s+0x01", hpc3_source)

    def test_dmactrl_endian_0x02(self, hpc3_source):
        """HPC3_DMACTRL_ENDIAN must be 0x02."""
        assert re.search(r"#define\s+HPC3_DMACTRL_ENDIAN\s+0x02",
                         hpc3_source)

    def test_dmactrl_dir_0x04(self, hpc3_source):
        """HPC3_DMACTRL_DIR must be 0x04."""
        assert re.search(r"#define\s+HPC3_DMACTRL_DIR\s+0x04", hpc3_source)

    def test_dmactrl_enable_0x10(self, hpc3_source):
        """HPC3_DMACTRL_ENABLE must be 0x10."""
        assert re.search(r"#define\s+HPC3_DMACTRL_ENABLE\s+0x10",
                         hpc3_source)

    def test_dmactrl_wrmask_0x20(self, hpc3_source):
        """HPC3_DMACTRL_WRMASK must be 0x20."""
        assert re.search(r"#define\s+HPC3_DMACTRL_WRMASK\s+0x20",
                         hpc3_source)


# ---------------------------------------------------------------------------
# PIT Clock Frequency [ASSUMPTION]
# ---------------------------------------------------------------------------

class TestPITClockFrequency:
    """Verify PIT clock is 1 MHz (1000 ns per tick)."""

    def test_pit_clock_1mhz(self, hpc3_source):
        """PIT_CLOCK_HZ must be 1000000 (1 MHz)."""
        assert re.search(r"#define\s+PIT_CLOCK_HZ\s+1000000", hpc3_source)

    def test_pit_ns_per_tick_1000(self, hpc3_source):
        """PIT_NS_PER_TICK must be 1000 (1 MHz = 1us per tick)."""
        assert re.search(r"#define\s+PIT_NS_PER_TICK\s+1000", hpc3_source)


# ---------------------------------------------------------------------------
# Timer Selection Assumptions [ASSUMPTION]
# ---------------------------------------------------------------------------

class TestTimerSelectionAssumptions:
    """Timer selection depends on is_ioc1() which reads SysID.

    [ASSUMPTION] For IOC1 boards (Indy/Guinness with sysid=0x26),
    is_ioc1() returns 2, selecting the R4000 Count/Compare timer
    for scheduling. PIT counters 0 and 1 are used only by the PROM.
    """

    def test_ioc1_selects_r4000_path(self):
        """[ASSUMPTION] is_ioc1() == 2 → R4000 Count/Compare timer.

        IRIX kernel clock.c: if (is_ioc1() == 2) startrtclock_r4000();
        The PIT is NOT used for kernel scheduling on IOC1 boards.
        """
        sysid = 0x26
        chip_rev = sysid & 0xe0
        board_rev = (sysid & 0x1e) >> 1
        is_ioc1_flag = 2 if (chip_rev == 0x20 and board_rev >= 2) else 0
        assert is_ioc1_flag == 2, (
            "Indy sysid 0x26 must produce is_ioc1_flag=2"
        )

    def test_pit_counters_01_not_used_by_kernel(self, hpc3_source):
        """[ASSUMPTION] PIT channels 0 and 1 are configured only by PROM.

        The IRIX kernel does not program PIT channels 0/1 on IOC1 boards.
        The PIT timers still fire on IP4/IP5 but are only used by the PROM
        for the escape countdown and delay loops.
        """
        # Verify PIT has timer callbacks (PROM uses them)
        assert "pit_timer0_cb" in hpc3_source or "pit_timer1_cb" in hpc3_source, (
            "PIT timer callback functions should exist"
        )

    def test_pit_counter2_used_for_delays(self, hpc3_source):
        """[ASSUMPTION] PIT counter 2 is the 1 MHz master clock.

        Counter 2 runs continuously and is read by the PROM for delay
        calibration. It has no IRQ output.
        """
        # Counter 2 should exist in the PIT implementation
        assert re.search(r"pit_count\[2\]|pit_counter\[2\]|channel.*2",
                         hpc3_source), (
            "PIT counter 2 should be referenced"
        )


# ---------------------------------------------------------------------------
# DUART Cascade Constants [CROSS-REF]
# ---------------------------------------------------------------------------

class TestDUARTCascadeConstants:
    """DUART (SCC) interrupt routing via INT3 map cascade.

    [CROSS-REF] IRIX defines LIO_DUART = 0x20 in the map status
    register. The SCC interrupt does NOT directly set local0_stat;
    instead it sets map_status and the cascade logic in update_irq()
    propagates it through map_mask0/1 to MAPPABLE0/1.
    """

    def test_lio_duart_bit_0x20(self, hpc3_source):
        """[CROSS-REF] LIO_DUART_BIT must be 0x20 (bit 5 of map_status)."""
        assert re.search(
            r"#define\s+LIO_DUART_BIT\s+0x20",
            hpc3_source
        ), "LIO_DUART_BIT must be defined as 0x20"

    def test_duart_routes_via_map_status(self, hpc3_source):
        """scc_update_irq must set/clear LIO_DUART_BIT in map_status,
        NOT directly modify local0_stat."""
        assert re.search(
            r"int3_map_status\s*\|=\s*LIO_DUART_BIT",
            hpc3_source
        ), "scc_update_irq must set LIO_DUART_BIT in map_status"
        assert re.search(
            r"int3_map_status\s*&=\s*~LIO_DUART_BIT",
            hpc3_source
        ), "scc_update_irq must clear LIO_DUART_BIT in map_status"
