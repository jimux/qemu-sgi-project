"""
SGI HPC3 subsystem source assertions.

Verifies INT3 interrupt controller, PIT timers, SCSI DMA bits,
RTC register layout, keyboard controller, and board type constants.

These tests are FAST (source code analysis only, no QEMU boot).
"""

import re


class TestINT3RegisterOffsets:
    """INT3 register offsets for Guinness (IP24) and Full House (IP22)."""

    def test_guinness_local0_stat(self, hpc3_header):
        """Guinness LOCAL0_STAT at 0x59880."""
        assert re.search(
            r"#define\s+HPC3_INT3_LOCAL0_STAT\s+0x59880",
            hpc3_header
        )

    def test_guinness_local0_mask(self, hpc3_header):
        """Guinness LOCAL0_MASK at 0x59884."""
        assert re.search(
            r"#define\s+HPC3_INT3_LOCAL0_MASK\s+0x59884",
            hpc3_header
        )

    def test_guinness_map_mask0(self, hpc3_header):
        """Guinness MAP_MASK0 at 0x59894."""
        assert re.search(
            r"#define\s+HPC3_INT3_MAP_MASK0\s+0x59894",
            hpc3_header
        )

    def test_fullhouse_local0_stat(self, hpc3_header):
        """Full House LOCAL0_STAT at 0x59000."""
        assert re.search(
            r"#define\s+HPC3_FH_INT3_LOCAL0_STAT\s+0x59000",
            hpc3_header
        )

    def test_fullhouse_map_mask0(self, hpc3_header):
        """Full House MAP_MASK0 at 0x59014."""
        assert re.search(
            r"#define\s+HPC3_FH_INT3_MAP_MASK0\s+0x59014",
            hpc3_header
        )


class TestINT3InterruptBits:
    """INT3 interrupt source bit definitions."""

    def test_local0_scsi0(self, hpc3_header):
        """INT3_LOCAL0_SCSI0 is 0x02."""
        assert re.search(
            r"#define\s+INT3_LOCAL0_SCSI0\s+0x02",
            hpc3_header
        )

    def test_local0_scsi1(self, hpc3_header):
        """INT3_LOCAL0_SCSI1 is 0x04."""
        assert re.search(
            r"#define\s+INT3_LOCAL0_SCSI1\s+0x04",
            hpc3_header
        )

    def test_local0_ethernet(self, hpc3_header):
        """INT3_LOCAL0_ETHERNET is 0x08."""
        assert re.search(
            r"#define\s+INT3_LOCAL0_ETHERNET\s+0x08",
            hpc3_header
        )

    def test_local0_graphics(self, hpc3_header):
        """INT3_LOCAL0_GRAPHICS is 0x40."""
        assert re.search(
            r"#define\s+INT3_LOCAL0_GRAPHICS\s+0x40",
            hpc3_header
        )

    def test_local0_mappable0(self, hpc3_header):
        """INT3_LOCAL0_MAPPABLE0 is 0x80."""
        assert re.search(
            r"#define\s+INT3_LOCAL0_MAPPABLE0\s+0x80",
            hpc3_header
        )

    def test_local1_power(self, hpc3_header):
        """INT3_LOCAL1_POWER is 0x02."""
        assert re.search(
            r"#define\s+INT3_LOCAL1_POWER\s+0x02",
            hpc3_header
        )


class TestINT3IRQLogic:
    """INT3 interrupt routing logic."""

    def test_update_irq_exists(self, hpc3_source):
        """sgi_hpc3_update_irq function must exist."""
        assert "sgi_hpc3_update_irq" in hpc3_source

    def test_scsi_irq_routes_local0(self, hpc3_source):
        """SCSI interrupt sets LOCAL0_SCSI0 bit."""
        assert "INT3_LOCAL0_SCSI0" in hpc3_source


class TestPITTimer:
    """8254 PIT timer configuration."""

    def test_pit_clock_hz(self, hpc3_source):
        """PIT clock is 1 MHz."""
        assert re.search(
            r"#define\s+PIT_CLOCK_HZ\s+1000000",
            hpc3_source
        )

    def test_pit_ns_per_tick(self, hpc3_source):
        """PIT tick period is 1000 ns (1 MHz)."""
        assert re.search(
            r"#define\s+PIT_NS_PER_TICK\s+1000",
            hpc3_source
        )

    def test_map_timer0_bit(self, hpc3_source):
        """INT3_MAP_TIMER0 is 0x04."""
        assert re.search(
            r"#define\s+INT3_MAP_TIMER0\s+0x04",
            hpc3_source
        )

    def test_map_timer1_bit(self, hpc3_source):
        """INT3_MAP_TIMER1 is 0x08."""
        assert re.search(
            r"#define\s+INT3_MAP_TIMER1\s+0x08",
            hpc3_source
        )

    def test_pit_counter0_offset(self, hpc3_header):
        """Guinness PIT counter 0 at 0x598b0."""
        assert re.search(
            r"#define\s+HPC3_INT3_PIT_COUNTER0\s+0x598b0",
            hpc3_header
        )

    def test_pit_counter_control(self, hpc3_header):
        """Guinness PIT control at 0x598bc."""
        assert re.search(
            r"#define\s+HPC3_INT3_PIT_CONTROL\s+0x598bc",
            hpc3_header
        )

    def test_timer_callbacks_exist(self, hpc3_source):
        """Timer callback functions are defined."""
        assert "sgi_hpc3_pit_timer0_cb" in hpc3_source
        assert "sgi_hpc3_pit_timer1_cb" in hpc3_source


class TestSCSIDMA:
    """SCSI DMA control and descriptor bits."""

    def test_dmactrl_bits(self, hpc3_source):
        """DMA control register bit definitions."""
        assert re.search(r"#define\s+HPC3_DMACTRL_IRQ\s+0x01", hpc3_source)
        assert re.search(r"#define\s+HPC3_DMACTRL_ENDIAN\s+0x02", hpc3_source)
        assert re.search(r"#define\s+HPC3_DMACTRL_DIR\s+0x04", hpc3_source)
        assert re.search(r"#define\s+HPC3_DMACTRL_ENABLE\s+0x10", hpc3_source)

    def test_bc_descriptor_bits(self, hpc3_source):
        """Buffer descriptor XIE (bit 29), EOX (bit 31), COUNT_MASK (14-bit)."""
        assert re.search(r"#define\s+HPC3_BC_XIE\s+\(1U\s*<<\s*29\)", hpc3_source)
        assert re.search(r"#define\s+HPC3_BC_EOX\s+\(1U\s*<<\s*31\)", hpc3_source)
        assert re.search(r"#define\s+HPC3_BC_COUNT_MASK\s+0x3fff", hpc3_source)

    def test_scsi0_reg_offset(self, hpc3_header):
        """SCSI 0 registers at 0x40000."""
        assert re.search(
            r"#define\s+HPC3_SCSI0_REG\s+0x40000",
            hpc3_header
        )

    def test_scsi1_reg_offset(self, hpc3_header):
        """SCSI 1 registers at 0x48000."""
        assert re.search(
            r"#define\s+HPC3_SCSI1_REG\s+0x48000",
            hpc3_header
        )


class TestRTC:
    """DS1386 RTC register offsets."""

    def test_rtc_register_offsets(self, hpc3_source):
        """RTC registers start at offset 0 with standard DS1386 layout."""
        assert re.search(r"#define\s+RTC_HUNDREDTHS\s+0x00", hpc3_source)
        assert re.search(r"#define\s+RTC_SECONDS\s+0x01", hpc3_source)
        assert re.search(r"#define\s+RTC_MINUTES\s+0x02", hpc3_source)
        assert re.search(r"#define\s+RTC_HOURS\s+0x04", hpc3_source)
        assert re.search(r"#define\s+RTC_DATE\s+0x08", hpc3_source)
        assert re.search(r"#define\s+RTC_MONTH\s+0x09", hpc3_source)
        assert re.search(r"#define\s+RTC_YEAR\s+0x0a", hpc3_source)

    def test_dallas_yrref_constant(self, hpc3_source):
        """[CROSS-REF] DS1386 year base matches IRIX DALLAS_YRREF (1940).

        IRIX kernel (IP22.c) uses DALLAS_YRREF = 1940 as the year epoch.
        The RTC year register stores years since 1940, not since 1900.
        Without this, IRIX shows 1996 instead of 2026 (off by 30 years).
        """
        assert re.search(r"#define\s+DALLAS_YRREF\s+1940", hpc3_source)

    def test_rtc_year_uses_dallas_epoch(self, hpc3_source):
        """[CROSS-REF] RTC year encoding uses DALLAS_YRREF, not year % 100.

        tm_year is years since 1900. For IRIX, we must store
        (tm_year + 1900 - 1940) % 100 = years since 1940.
        """
        assert "tm.tm_year + 1900 - DALLAS_YRREF" in hpc3_source

    def test_rtc_year_write_uses_dallas_epoch(self, hpc3_source):
        """[CROSS-REF] RTC year write-back converts from DALLAS_YRREF to tm_year."""
        assert "DALLAS_YRREF - 1900" in hpc3_source

    def test_bcd_conversion_exists(self, hpc3_source):
        """BCD conversion functions are defined."""
        assert "bin_to_bcd" in hpc3_source
        assert "bcd_to_bin" in hpc3_source


class TestKeyboard:
    """8042-compatible keyboard controller responses."""

    def test_self_test_response(self, hpc3_source):
        """Self-test (0xAA) returns 0x55."""
        assert re.search(
            r"#define\s+KBD_RESP_SELF_TEST_OK\s+0x55",
            hpc3_source
        )

    def test_iface_test_response(self, hpc3_source):
        """Interface test (0xAB) returns 0x00."""
        assert re.search(
            r"#define\s+KBD_RESP_IFACE_OK\s+0x00",
            hpc3_source
        )

    def test_ps2_kbd_device_embedded(self, hpc3_header):
        """PS/2 keyboard device is embedded in HPC3 state."""
        assert "PS2KbdState" in hpc3_header
        assert "ps2kbd" in hpc3_header

    def test_ps2_mouse_device_embedded(self, hpc3_header):
        """PS/2 mouse device is embedded in HPC3 state."""
        assert "PS2MouseState" in hpc3_header
        assert "ps2mouse" in hpc3_header


class TestBoardType:
    """Board type constants and system ID masks."""

    def test_board_ip24_value(self, hpc3_header):
        """BOARD_IP24 (Indy/Guinness) is 0."""
        assert re.search(
            r"#define\s+BOARD_IP24\s+0\b",
            hpc3_header
        )

    def test_board_ip22_value(self, hpc3_header):
        """BOARD_IP22 (Indigo2/Full House) is 1."""
        assert re.search(
            r"#define\s+BOARD_IP22\s+1\b",
            hpc3_header
        )

    def test_sysid_masks(self, hpc3_header):
        """System ID bit field masks."""
        assert re.search(r"#define\s+SYSID_CHIP_REV_MASK\s+0xe0", hpc3_header)
        assert re.search(r"#define\s+SYSID_BOARD_REV_MASK\s+0x1e", hpc3_header)
        assert re.search(r"#define\s+SYSID_BOARD_ID_MASK\s+0x01", hpc3_header)

    def test_scc_rx_fifo_size(self, hpc3_header):
        """SCC RX FIFO is 16 bytes."""
        assert re.search(
            r"#define\s+SCC_RX_FIFO_SIZE\s+16",
            hpc3_header
        )


def _extract_function(source, func_sig):
    """Extract a C function body from source by finding balanced braces.

    func_sig can be a function name (e.g., 'sgi_hpc3_update_irq') or
    a partial signature (e.g., 'sgi_hpc3_scc_update_irq(SGIHPC3State').

    Searches for the function definition (not forward declaration) by
    looking for func_sig and then '{'. Skips forward declarations
    (which end with ';' before '{').
    """
    # If func_sig doesn't contain '(', append one to match definition
    search_str = func_sig if "(" in func_sig else func_sig + "("
    search_from = 0
    while True:
        pos = source.find(search_str, search_from)
        if pos < 0:
            return None
        # Check if this is a forward declaration (has ';' before '{')
        snippet = source[pos:pos + 300]
        semi_pos = snippet.find(";")
        brace_pos = snippet.find("{")
        if semi_pos >= 0 and (brace_pos < 0 or semi_pos < brace_pos):
            # This is a forward declaration or call site, skip it
            search_from = pos + len(search_str)
            continue
        # This is a function definition — find the opening brace
        brace_start = source.find("{", pos)
        if brace_start < 0:
            return None
        # Count braces to find matching close
        depth = 0
        for i in range(brace_start, len(source)):
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
                if depth == 0:
                    return source[brace_start:i + 1]
        return None


class TestINT3MappedCascade:
    """INT3 mapped interrupt cascade logic.

    [CROSS-REF] MAME ioc2.cpp:268-284: set_local_int() evaluates
    (map_status & map_mask0/1) to assert MAPPABLE0/MAPPABLE1 in
    local0/local1 status registers.

    Bug fix (phase 9): The cascade was not centralized — PIT timers
    incorrectly set map_status bits, and DUART bypassed the cascade
    to directly manipulate local0_stat.  The fix centralizes all
    cascade logic in sgi_hpc3_update_irq().
    """

    def test_cascade_evaluates_map_mask0(self, hpc3_source):
        """sgi_hpc3_update_irq must check (map_status & map_mask0).

        [CROSS-REF] MAME ioc2.cpp:268-284: if any bit in
        (map_status & map_mask0) is set, MAPPABLE0 is asserted.
        """
        body = _extract_function(hpc3_source, "sgi_hpc3_update_irq")
        assert body, "sgi_hpc3_update_irq function not found"
        assert re.search(
            r"int3_map_status\s*&\s*.*int3_map_mask0|"
            r"map_status\s*&\s*.*map_mask0",
            body
        ), "update_irq does not evaluate map_status & map_mask0"

    def test_cascade_evaluates_map_mask1(self, hpc3_source):
        """sgi_hpc3_update_irq must check (map_status & map_mask1).

        [CROSS-REF] MAME ioc2.cpp:268-284: same for map_mask1→MAPPABLE1.
        """
        body = _extract_function(hpc3_source, "sgi_hpc3_update_irq")
        assert body, "sgi_hpc3_update_irq function not found"
        assert re.search(
            r"int3_map_status\s*&\s*.*int3_map_mask1|"
            r"map_status\s*&\s*.*map_mask1",
            body
        ), "update_irq does not evaluate map_status & map_mask1"

    def test_cascade_sets_mappable0(self, hpc3_source):
        """Cascade must set INT3_LOCAL0_MAPPABLE0 in local0_stat."""
        body = _extract_function(hpc3_source, "sgi_hpc3_update_irq")
        assert body, "sgi_hpc3_update_irq function not found"
        assert re.search(
            r"int3_local0_stat\s*\|=\s*INT3_LOCAL0_MAPPABLE0",
            body
        ), "Cascade does not set MAPPABLE0 in local0_stat"

    def test_cascade_clears_mappable0(self, hpc3_source):
        """Cascade must clear INT3_LOCAL0_MAPPABLE0 when no mapped interrupts."""
        body = _extract_function(hpc3_source, "sgi_hpc3_update_irq")
        assert body, "sgi_hpc3_update_irq function not found"
        assert re.search(
            r"int3_local0_stat\s*&=\s*~INT3_LOCAL0_MAPPABLE0",
            body
        ), "Cascade does not clear MAPPABLE0 in local0_stat"

    def test_map_mask0_write_triggers_update(self, hpc3_source):
        """Writing MAP_MASK0 must call sgi_hpc3_update_irq().

        [CROSS-REF] MAME ioc2.cpp set_map_int_mask(): re-evaluates
        cascade when mask changes.
        """
        # Find all MAP_MASK0 case blocks — the write handler is the one
        # that assigns to int3_map_mask0 (not just reads it)
        matches = list(re.finditer(
            r"case HPC3_INT3_MAP_MASK0:(.*?)break;",
            hpc3_source, re.DOTALL
        ))
        write_handler = None
        for m in matches:
            if "int3_map_mask0 = " in m.group(1) or "int3_map_mask0 =" in m.group(1):
                write_handler = m.group(1)
                break
        assert write_handler, "MAP_MASK0 write handler not found"
        assert "sgi_hpc3_update_irq" in write_handler, (
            "MAP_MASK0 write handler does not call sgi_hpc3_update_irq"
        )

    def test_map_mask1_write_triggers_update(self, hpc3_source):
        """Writing MAP_MASK1 must call sgi_hpc3_update_irq()."""
        matches = list(re.finditer(
            r"case HPC3_INT3_MAP_MASK1:(.*?)break;",
            hpc3_source, re.DOTALL
        ))
        write_handler = None
        for m in matches:
            if "int3_map_mask1 = " in m.group(1) or "int3_map_mask1 =" in m.group(1):
                write_handler = m.group(1)
                break
        assert write_handler, "MAP_MASK1 write handler not found"
        assert "sgi_hpc3_update_irq" in write_handler, (
            "MAP_MASK1 write handler does not call sgi_hpc3_update_irq"
        )


class TestPITTimerBypassINT3:
    """PIT timers must bypass INT3 cascade entirely.

    [CROSS-REF] MAME ioc2.cpp:210-226: timer0/timer1 interrupts go
    directly to CPU IRQ lines (IP4/IP5), not through INT3 Local0/Local1.

    Bug fix (phase 9): PIT timer callbacks incorrectly set
    int3_map_status |= INT3_MAP_TIMER0/TIMER1, which caused spurious
    MAPPABLE0 cascades when the kernel enabled map_mask0 bits.
    """

    def test_timer0_cb_no_map_status_write(self, hpc3_source):
        """Timer0 callback must NOT write int3_map_status.

        [CROSS-REF] MAME ioc2.cpp:210-226: timers bypass INT3.
        Reading map_status for logging is fine; writing (|= or =) is not.
        """
        body = _extract_function(hpc3_source, "sgi_hpc3_pit_timer0_cb")
        assert body, "Timer0 callback function not found"
        assert not re.search(r"int3_map_status\s*\|=", body), (
            "Timer0 callback must NOT set int3_map_status — "
            "PIT timers bypass INT3 cascade (MAME ioc2.cpp:210-226)"
        )

    def test_timer1_cb_no_map_status_write(self, hpc3_source):
        """Timer1 callback must NOT write int3_map_status."""
        body = _extract_function(hpc3_source, "sgi_hpc3_pit_timer1_cb")
        assert body, "Timer1 callback function not found"
        assert not re.search(r"int3_map_status\s*\|=", body), (
            "Timer1 callback must NOT set int3_map_status — "
            "PIT timers bypass INT3 cascade (MAME ioc2.cpp:210-226)"
        )

    def test_timer_irq_uses_dedicated_lines(self, hpc3_source):
        """update_irq must route timers via timer_irq[], not cpu_irq[]."""
        body = _extract_function(hpc3_source, "sgi_hpc3_update_irq")
        assert body, "sgi_hpc3_update_irq function not found"
        assert "timer_irq[0]" in body, (
            "update_irq does not use timer_irq[0] for Timer0"
        )
        assert "timer_irq[1]" in body, (
            "update_irq does not use timer_irq[1] for Timer1"
        )

    def test_timer_clear_no_manual_mappable(self, hpc3_source):
        """TIMER_CLEAR handler must not manually set/clear MAPPABLE bits.

        It should only clear timer_pending[] and call update_irq(),
        which handles MAPPABLE via the centralized cascade.
        """
        # Find the TIMER_CLEAR write handler (the one with timer_pending)
        matches = list(re.finditer(
            r"case HPC3_INT3_TIMER_CLEAR:(.*?)break;",
            hpc3_source, re.DOTALL
        ))
        write_handler = None
        for m in matches:
            if "timer_pending" in m.group(1):
                write_handler = m.group(1)
                break
        assert write_handler, "TIMER_CLEAR write handler not found"
        # Check for MAPPABLE as a C assignment (|= or &=~), not in comments
        assert not re.search(r"MAPPABLE\w*\s*[|&]?=", write_handler), (
            "TIMER_CLEAR handler should not directly manipulate MAPPABLE bits"
        )
        assert not re.search(r"[|&]=\s*.*MAPPABLE", write_handler), (
            "TIMER_CLEAR handler should not directly manipulate MAPPABLE bits"
        )
        assert "sgi_hpc3_update_irq" in write_handler, (
            "TIMER_CLEAR handler must call sgi_hpc3_update_irq()"
        )


class TestSCCDUARTCascade:
    """SCC DUART interrupt routing through mapped cascade.

    The Z85C30 SCC INT output connects to INT3's DUART mapped interrupt
    (LIO_DUART_BIT in map_status). The cascade from map_status through
    map_mask0 to MAPPABLE0 is handled centrally by sgi_hpc3_update_irq().

    Bug fix (phase 9): sgi_hpc3_scc_update_irq was directly manipulating
    local0_stat instead of delegating to the centralized cascade.
    """

    def test_scc_update_sets_map_status(self, hpc3_source):
        """scc_update_irq must set LIO_DUART_BIT in map_status."""
        body = _extract_function(hpc3_source, "sgi_hpc3_scc_update_irq(SGIHPC3State")
        assert body, "sgi_hpc3_scc_update_irq function not found"
        assert "LIO_DUART_BIT" in body, (
            "scc_update_irq must use LIO_DUART_BIT for map_status"
        )

    def test_scc_update_calls_update_irq(self, hpc3_source):
        """scc_update_irq must delegate cascade to sgi_hpc3_update_irq()."""
        body = _extract_function(hpc3_source, "sgi_hpc3_scc_update_irq(SGIHPC3State")
        assert body, "sgi_hpc3_scc_update_irq function not found"
        assert "sgi_hpc3_update_irq" in body, (
            "scc_update_irq must call sgi_hpc3_update_irq for cascade"
        )

    def test_scc_update_no_direct_local0(self, hpc3_source):
        """scc_update_irq must NOT directly modify local0_stat.

        The cascade from map_status → MAPPABLE0 → local0_stat is
        handled centrally by sgi_hpc3_update_irq().
        Read-only references (e.g. in trace/debug log messages) are OK.
        """
        body = _extract_function(hpc3_source, "sgi_hpc3_scc_update_irq(SGIHPC3State")
        assert body, "sgi_hpc3_scc_update_irq function not found"
        # Check for assignment (|= or &=) to local0_stat, not mere reads
        assert not re.search(r"int3_local0_stat\s*[|&]?=", body), (
            "scc_update_irq should NOT directly modify local0_stat — "
            "cascade is handled by sgi_hpc3_update_irq()"
        )


class TestINT3IRQOutput:
    """INT3 IRQ output routing to CPU.

    INT3 Local0 → IP2 (cpu_irq[0]), Local1 → IP3 (cpu_irq[1]).
    Only MASKED interrupts (status & mask) assert CPU IRQ lines.
    """

    def test_local0_masked_pending(self, hpc3_source):
        """CPU IP2 assertion must check (local0_stat & local0_mask)."""
        body = _extract_function(hpc3_source, "sgi_hpc3_update_irq")
        assert body, "sgi_hpc3_update_irq function not found"
        assert re.search(
            r"int3_local0_stat\s*&\s*.*int3_local0_mask|"
            r"local0_stat\s*&\s*.*local0_mask",
            body
        ), "IP2 assertion does not check (local0_stat & local0_mask)"

    def test_local1_masked_pending(self, hpc3_source):
        """CPU IP3 assertion must check (local1_stat & local1_mask)."""
        body = _extract_function(hpc3_source, "sgi_hpc3_update_irq")
        assert body, "sgi_hpc3_update_irq function not found"
        assert re.search(
            r"int3_local1_stat\s*&\s*.*int3_local1_mask|"
            r"local1_stat\s*&\s*.*local1_mask",
            body
        ), "IP3 assertion does not check (local1_stat & local1_mask)"

    def test_uses_cpu_irq_0_for_local0(self, hpc3_source):
        """Local0 pending → cpu_irq[0] (IP2)."""
        body = _extract_function(hpc3_source, "sgi_hpc3_update_irq")
        assert body, "sgi_hpc3_update_irq function not found"
        assert "cpu_irq[0]" in body, (
            "update_irq must route Local0 to cpu_irq[0]"
        )

    def test_uses_cpu_irq_1_for_local1(self, hpc3_source):
        """Local1 pending → cpu_irq[1] (IP3)."""
        body = _extract_function(hpc3_source, "sgi_hpc3_update_irq")
        assert body, "sgi_hpc3_update_irq function not found"
        assert "cpu_irq[1]" in body, (
            "update_irq must route Local1 to cpu_irq[1]"
        )


class TestINT3TimerIRQWiring:
    """Timer IRQ wiring in machine init.

    [CROSS-REF] sgi_indy.c: timer-irq GPIO outputs 0/1 connect to
    cpu->env.irq[4/5] (IP4/IP5).
    """

    def test_timer_irq_gpio_exists(self, hpc3_source):
        """HPC3 must have timer-irq GPIO output."""
        assert re.search(
            r'qdev_init_gpio_out_named.*"timer-irq"',
            hpc3_source
        ), "HPC3 does not define timer-irq GPIO output"

    def test_timer_irq_wired_to_ip4_ip5(self, indy_machine_source):
        """sgi_indy.c must wire timer-irq to irq[4] and irq[5]."""
        assert "irq[4]" in indy_machine_source, (
            "Timer IRQ not wired to irq[4] (IP4)"
        )
        assert "irq[5]" in indy_machine_source, (
            "Timer IRQ not wired to irq[5] (IP5)"
        )


class TestPS2InputWiring:
    """PS/2 keyboard and mouse device initialization and IRQ wiring."""

    def test_ps2_kbd_device_initialized(self, hpc3_source):
        """PS/2 keyboard device is initialized as child object."""
        assert re.search(
            r'object_initialize_child\(.*"ps2kbd"',
            hpc3_source
        ), "PS/2 keyboard not initialized with object_initialize_child"

    def test_ps2_mouse_device_initialized(self, hpc3_source):
        """PS/2 mouse device is initialized as child object."""
        assert re.search(
            r'object_initialize_child\(.*"ps2mouse"',
            hpc3_source
        ), "PS/2 mouse not initialized with object_initialize_child"

    def test_ps2_devices_realized(self, hpc3_source):
        """Both PS/2 devices are realized during HPC3 realize."""
        assert re.search(
            r'sysbus_realize\(SYS_BUS_DEVICE\(&s->ps2kbd\)',
            hpc3_source
        ), "PS/2 keyboard not realized"
        assert re.search(
            r'sysbus_realize\(SYS_BUS_DEVICE\(&s->ps2mouse\)',
            hpc3_source
        ), "PS/2 mouse not realized"

    def test_ps2_kbd_irq_connected(self, hpc3_source):
        """PS/2 keyboard IRQ output is connected to HPC3."""
        assert re.search(
            r'qdev_connect_gpio_out\(DEVICE\(&s->ps2kbd\)',
            hpc3_source
        ), "PS/2 keyboard IRQ not connected"

    def test_ps2_mouse_irq_connected(self, hpc3_source):
        """PS/2 mouse IRQ output is connected to HPC3."""
        assert re.search(
            r'qdev_connect_gpio_out\(DEVICE\(&s->ps2mouse\)',
            hpc3_source
        ), "PS/2 mouse IRQ not connected"

    def test_8042_self_test_returns_0x55(self, hpc3_source):
        """8042 self-test command (0xAA) returns 0x55."""
        assert "0x55" in hpc3_source, "0x55 self-test response not found"
        assert re.search(
            r'KBD_CMD_SELF_TEST|0xAA',
            hpc3_source
        ), "Self-test command handler not found"


class TestKbdIRQGating:
    """8042 command byte must gate PS/2 interrupt delivery to INT3.

    [CROSS-REF] Real 8042: IRQ1 asserted only when OBF AND
    KBD_MODE_KBD_INT set. IRIX pckm_reinit_lock() clears these
    bits during polled init, so ungated IRQs cause a mutex deadlock.
    """

    def test_kbd_irq_checks_cmd_byte(self, hpc3_source):
        """PS/2 kbd IRQ path must check KBD_MODE_KBD_INT."""
        body = _extract_function(hpc3_source, "sgi_hpc3_kbd_update_map_irq")
        if body is None:
            body = _extract_function(hpc3_source, "sgi_hpc3_ps2_kbd_irq")
        assert body, "Keyboard IRQ function not found"
        assert "kbd_cmd_byte" in body, (
            "PS/2 kbd IRQ does not check kbd_cmd_byte — IRQs must "
            "be gated by command byte to prevent pckm_mutex deadlock"
        )

    def test_mouse_irq_checks_cmd_byte(self, hpc3_source):
        """PS/2 mouse IRQ path must check KBD_MODE_MOUSE_INT."""
        body = _extract_function(hpc3_source, "sgi_hpc3_kbd_update_map_irq")
        if body is None:
            body = _extract_function(hpc3_source, "sgi_hpc3_ps2_mouse_irq")
        assert body, "Mouse IRQ function not found"
        assert "KBD_MODE_MOUSE_INT" in body or "kbd_cmd_byte" in body, (
            "PS/2 mouse IRQ does not check command byte"
        )

    def test_cmd_byte_write_triggers_irq_update(self, hpc3_source):
        """Writing command byte (0x60) must re-evaluate mapped IRQ."""
        matches = list(re.finditer(
            r"case KBD_CMD_WRITE_CTRL:(.*?)break;",
            hpc3_source, re.DOTALL
        ))
        write_ctrl = None
        for m in matches:
            if "kbd_cmd_byte" in m.group(1):
                write_ctrl = m.group(1)
                break
        assert write_ctrl, "KBD_CMD_WRITE_CTRL handler not found"
        assert re.search(
            r"sgi_hpc3_kbd_update_map_irq|sgi_hpc3_update_irq",
            write_ctrl
        ), (
            "KBD_CMD_WRITE_CTRL does not call IRQ update — enabling "
            "interrupts via command byte must fire pending data"
        )

    def test_irq_not_unconditional(self, hpc3_source):
        """INT3_MAP_KBDMS must not be set from raw IRQ level alone."""
        body = _extract_function(hpc3_source, "sgi_hpc3_kbd_update_map_irq")
        if body is None:
            body = _extract_function(hpc3_source, "sgi_hpc3_ps2_kbd_irq")
        assert body, "IRQ handler not found"
        assert "kbd_cmd_byte" in body, (
            "IRQ handler sets INT3_MAP_KBDMS unconditionally — "
            "must check command byte interrupt enable bits"
        )

    def test_reset_default_has_kbd_int(self, hpc3_source):
        """Reset command byte includes KBD_MODE_KBD_INT."""
        assert re.search(
            r"kbd_cmd_byte\s*=.*KBD_MODE_KBD_INT",
            hpc3_source, re.DOTALL
        ), "Reset default must include KBD_MODE_KBD_INT"
