"""
Virtual time and icount source code assertions.

Verifies that QEMU's timing infrastructure correctly handles:
- MIPS WAIT instruction (halts CPU, raises HLT)
- CPU wakeup on pending interrupts
- PIT timers using virtual clock
- CP0 Count using virtual clock
- icount sleep=off instant time advancement
- icount sleep=on warp timer scheduling

These tests are FAST (source code analysis only, no QEMU boot).
"""

import re
import pytest


class TestWAITInstruction:
    """WAIT must halt the CPU and raise EXCP_HLT."""

    def test_wait_sets_halted(self, exception_source):
        """helper_wait must set cs->halted = 1.

        Without this, QEMU won't enter the idle path and virtual time
        won't advance past the WAIT instruction.
        """
        match = re.search(
            r"void helper_wait.*?\{(.*?)\n\}",
            exception_source, re.DOTALL
        )
        assert match, "helper_wait function not found in exception.c"
        body = match.group(1)
        assert "cs->halted = 1" in body, (
            "helper_wait does not set cs->halted = 1"
        )

    def test_wait_raises_hlt(self, exception_source):
        """helper_wait must raise EXCP_HLT to exit the CPU loop.

        This exception causes QEMU to check for pending timers and
        advance virtual time when the CPU is idle.
        """
        match = re.search(
            r"void helper_wait.*?\{(.*?)\n\}",
            exception_source, re.DOTALL
        )
        assert match, "helper_wait function not found in exception.c"
        body = match.group(1)
        assert "EXCP_HLT" in body, (
            "helper_wait does not raise EXCP_HLT"
        )


class TestCPUHasWork:
    """mips_cpu_has_work must check interrupt state to wake from WAIT."""

    def test_cpu_has_work_checks_interrupts(self, cpu_source):
        """mips_cpu_has_work must check CPU_INTERRUPT_HARD.

        This is how QEMU determines if a halted CPU should wake up
        when an external interrupt (timer, PIT, etc.) fires.
        """
        match = re.search(
            r"mips_cpu_has_work.*?\{(.*?)\n\}",
            cpu_source, re.DOTALL
        )
        assert match, "mips_cpu_has_work function not found"
        body = match.group(1)
        assert "CPU_INTERRUPT_HARD" in body, (
            "mips_cpu_has_work does not check CPU_INTERRUPT_HARD"
        )

    def test_cpu_has_work_checks_enabled(self, cpu_source):
        """mips_cpu_has_work must check hw_interrupts_enabled.

        The CPU should only wake from WAIT when interrupts are actually
        enabled in CP0_Status (IE=1, EXL=0, ERL=0).
        """
        match = re.search(
            r"mips_cpu_has_work.*?\{(.*?)\n\}",
            cpu_source, re.DOTALL
        )
        assert match, "mips_cpu_has_work function not found"
        body = match.group(1)
        assert "hw_interrupts_enabled" in body, (
            "mips_cpu_has_work does not check hw_interrupts_enabled"
        )


class TestPITVirtualClock:
    """PIT timers must use QEMU_CLOCK_VIRTUAL for deterministic timing."""

    def test_pit_uses_virtual_clock(self, hpc3_source):
        """PIT timer creation must use QEMU_CLOCK_VIRTUAL.

        Using QEMU_CLOCK_VIRTUAL means PIT timing is tied to simulated
        time, not wall-clock time. This is essential for icount mode
        to work correctly.
        """
        # Find PIT timer creation
        assert re.search(
            r"pit_timer\[0\].*=.*timer_new_ns\(QEMU_CLOCK_VIRTUAL",
            hpc3_source
        ), "PIT timer 0 does not use QEMU_CLOCK_VIRTUAL"
        assert re.search(
            r"pit_timer\[1\].*=.*timer_new_ns\(QEMU_CLOCK_VIRTUAL",
            hpc3_source
        ), "PIT timer 1 does not use QEMU_CLOCK_VIRTUAL"

    def test_pit_clock_is_1mhz(self, hpc3_source):
        """PIT clock must be 1 MHz (PIT_NS_PER_TICK = 1000).

        The 8254-compatible PIT in the IOC2/INT3 runs at 1 MHz.
        At 1000 ns per tick, a count of 10000 = 10 ms period.
        """
        assert "PIT_NS_PER_TICK" in hpc3_source, (
            "PIT_NS_PER_TICK constant not found"
        )
        assert re.search(
            r"#define\s+PIT_NS_PER_TICK\s+1000\b",
            hpc3_source
        ), "PIT_NS_PER_TICK is not 1000 (1 MHz)"


class TestCP0CountVirtualClock:
    """CP0 Count must be derived from virtual time."""

    def test_cp0_count_uses_virtual_clock(self, cp0_timer_source):
        """[CROSS-REF] cpu_mips_get_count_val uses QEMU_CLOCK_VIRTUAL.

        Already verified in test_cp0_timer_source.py, but included
        here for completeness of the virtual time test suite.
        """
        match = re.search(
            r"cpu_mips_get_count_val.*?\{(.*?)\n\}",
            cp0_timer_source, re.DOTALL
        )
        assert match, "cpu_mips_get_count_val function not found"
        body = match.group(1)
        assert "QEMU_CLOCK_VIRTUAL" in body, (
            "get_count_val does not use QEMU_CLOCK_VIRTUAL"
        )


class TestIcountSleepOff:
    """icount sleep=off must advance virtual time instantly during idle."""

    def test_icount_sleep_off_instant_warp(self, icount_source):
        """When icount_sleep is false, bias is updated immediately.

        The !icount_sleep path adds the deadline directly to
        qemu_icount_bias, which makes virtual time jump forward
        instantly when the CPU is idle (WAIT). This is the key
        mechanism that makes IRIX boot fast under icount mode.
        """
        # The code pattern: if (!icount_sleep) { ... qemu_icount_bias ... }
        # Find the block where !icount_sleep leads to bias update
        assert re.search(
            r"if\s*\(!icount_sleep\)",
            icount_source
        ), "!icount_sleep check not found in icount-common.c"

        # The immediate bias update should be in the !icount_sleep branch
        assert "qemu_icount_bias" in icount_source, (
            "qemu_icount_bias not referenced in icount-common.c"
        )

    def test_icount_sleep_on_schedules_warp_timer(self, icount_source):
        """When icount_sleep is true, a warp timer is scheduled.

        The icount_sleep=true (default) path uses timer_mod_anticipate
        to schedule a warp timer that fires in real time. This means
        virtual time only advances at wall-clock speed during idle,
        which is why IRIX boot is slow without sleep=off.
        """
        assert "timer_mod_anticipate" in icount_source, (
            "timer_mod_anticipate not found — warp timer not scheduled"
        )
        assert "icount_warp_timer" in icount_source, (
            "icount_warp_timer not found in icount-common.c"
        )


class TestCPUClock:
    """SGI Indy CPU clock must be 100 MHz."""

    def test_cpu_clock_100mhz(self, indy_machine_source):
        """clock_set_hz(cpuclk, 100000000) sets 100 MHz CPU clock.

        The R4600 in the Indy runs at 100-133 MHz. QEMU defaults
        to 100 MHz. With CCRes=2, CP0 Count increments at 50 MHz.
        """
        assert re.search(
            r"clock_set_hz\(\s*cpuclk\s*,\s*100000000\s*\)",
            indy_machine_source
        ), "CPU clock is not set to 100 MHz"


class TestCCResDivider:
    """CCRes must divide the CPU clock for CP0 Count."""

    def test_ccres_divider(self, cpu_source):
        """clock_set_mul_div uses cpu_model->CCRes to divide the clock.

        CCRes=2 means Count increments at half the CPU clock rate.
        For a 100 MHz CPU, Count runs at 50 MHz (20 ns per tick).
        """
        assert re.search(
            r"clock_set_mul_div\(\s*cpu->count_div\s*,\s*env->cpu_model->CCRes\s*,\s*1\s*\)",
            cpu_source
        ), "clock_set_mul_div not using CCRes for count divider"
