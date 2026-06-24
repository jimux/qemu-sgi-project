"""
SGI Indy machine configuration assertions.

Verifies that sgi_indy.c correctly wires the CPU clock, IRQ lines,
CP0 timer, and serial chardev for the IP24 Indy machine.

These tests are FAST (source code analysis only, no QEMU boot).
"""

import re
import pytest


class TestCPUClock:
    """CPU clock must be 100 MHz."""

    def test_cpu_clock_100mhz(self, indy_machine_source):
        """CPU reference clock should be set to 100 MHz.

        The R4600 in the Indy runs at 100-200 MHz. The CP0 Count
        register increments at half the pipeline clock, so a 100 MHz
        clock gives 50 MHz Count rate (~20ns per tick).
        """
        assert "clock_set_hz(cpuclk, 100000000)" in indy_machine_source, (
            "CPU clock is not set to 100 MHz"
        )


class TestCPUTimerInit:
    """CP0 timer must be initialized."""

    def test_cpu_timer_initialized(self, indy_machine_source):
        """cpu_mips_clock_init(cpu) must be called to create the CP0 timer."""
        assert "cpu_mips_clock_init(cpu)" in indy_machine_source, (
            "cpu_mips_clock_init not called — CP0 timer won't work"
        )

    def test_irq_init_called(self, indy_machine_source):
        """cpu_mips_irq_init_cpu(cpu) must be called to set up IRQ lines."""
        assert "cpu_mips_irq_init_cpu(cpu)" in indy_machine_source, (
            "cpu_mips_irq_init_cpu not called — IRQ lines won't work"
        )


class TestIRQWiring:
    """HPC3 interrupts must be wired to correct CPU IRQ pins."""

    def test_local0_wired_to_ip2(self, indy_machine_source):
        """Local0 (SCSI, ethernet) must be wired to CPU IP2 (env.irq[2])."""
        # Look for: cpu-irq index 0 -> irq[2]
        assert re.search(
            r'qdev_connect_gpio_out_named\(hpc3_dev,\s*"cpu-irq",\s*0,\s*cpu->env\.irq\[2\]\)',
            indy_machine_source
        ), "Local0 not wired to CPU irq[2] (IP2)"

    def test_local1_wired_to_ip3(self, indy_machine_source):
        """Local1 (panel, DMA) must be wired to CPU IP3 (env.irq[3])."""
        assert re.search(
            r'qdev_connect_gpio_out_named\(hpc3_dev,\s*"cpu-irq",\s*1,\s*cpu->env\.irq\[3\]\)',
            indy_machine_source
        ), "Local1 not wired to CPU irq[3] (IP3)"

    def test_timer0_wired_to_ip4(self, indy_machine_source):
        """PIT Timer 0 (sched clock) must be wired to CPU IP4 (env.irq[4])."""
        assert re.search(
            r'qdev_connect_gpio_out_named\(hpc3_dev,\s*"timer-irq",\s*0,\s*cpu->env\.irq\[4\]\)',
            indy_machine_source
        ), "Timer0 not wired to CPU irq[4] (IP4)"

    def test_timer1_wired_to_ip5(self, indy_machine_source):
        """PIT Timer 1 (prof clock) must be wired to CPU IP5 (env.irq[5])."""
        assert re.search(
            r'qdev_connect_gpio_out_named\(hpc3_dev,\s*"timer-irq",\s*1,\s*cpu->env\.irq\[5\]\)',
            indy_machine_source
        ), "Timer1 not wired to CPU irq[5] (IP5)"


class TestSerialConnection:
    """Serial console chardev must be connected."""

    def test_hpc3_serial_connected(self, indy_machine_source):
        """HPC3 chardev property must be connected to serial_hd(0)."""
        assert re.search(
            r'qdev_prop_set_chr\(hpc3_dev,\s*"chardev",\s*serial_hd\(0\)\)',
            indy_machine_source
        ), "HPC3 serial not connected to serial_hd(0)"


class TestNVRAMConfiguration:
    """NVRAM file must be configured per-machine."""

    def test_indy_nvram_filename(self, indy_machine_source):
        """IP24 (Indy) should use 'sgi_indy_nvram.bin' for NVRAM."""
        assert re.search(
            r'case SGI_IP24:\s*return "sgi_indy_nvram.bin"', indy_machine_source
        ), "IP24 NVRAM filename not set to sgi_indy_nvram.bin"


class TestBoardTypeSelection:
    """Board type (Guinness vs Full House) must be set correctly."""

    def test_ip24_is_guinness(self, indy_machine_source):
        """IP24 (Indy) uses Guinness (is_fullhouse = false)."""
        # IP24 is not in the is_fullhouse condition
        assert re.search(
            r"is_fullhouse\s*=\s*\(model\s*==\s*SGI_IP22\s*\|\|",
            indy_machine_source
        ), "is_fullhouse must be computed from model"
        # IP24 should get BOARD_IP24
        assert "is_fullhouse ? BOARD_IP22 : BOARD_IP24" in indy_machine_source

    def test_ip22_is_fullhouse(self, indy_machine_source):
        """IP22 (Indigo2) is Full House."""
        assert "model == SGI_IP22" in indy_machine_source

    def test_ip28_is_fullhouse(self, indy_machine_source):
        """IP28 (Indigo2 Impact) is Full House."""
        assert "model == SGI_IP28" in indy_machine_source

    def test_fullhouse_has_eisa(self, indy_machine_source):
        """Full House machines set has-eisa property on MC."""
        assert re.search(
            r'qdev_prop_set_bit\(mc_dev,\s*"has-eisa",\s*true\)',
            indy_machine_source
        ), "Full House must set has-eisa=true on MC"

    def test_scsi_bus_attached(self, indy_machine_source):
        """SCSI bus handles command line drives."""
        assert "scsi_bus_legacy_handle_cmdline" in indy_machine_source


class TestDefaultPROMNames:
    """Default PROM filenames per platform."""

    def test_ip24_prom_name(self, indy_machine_source):
        """IP24 defaults to ip24prom.bin."""
        assert re.search(r'case SGI_IP24:\s*return "ip24prom.bin"', indy_machine_source)

    def test_ip22_prom_name(self, indy_machine_source):
        """IP22 defaults to ip22prom.bin."""
        assert re.search(r'case SGI_IP22:\s*return "ip22prom.bin"', indy_machine_source)

    def test_ip28_prom_name(self, indy_machine_source):
        """IP28 defaults to ip28prom.bin."""
        assert re.search(r'case SGI_IP28:\s*return "ip28prom.bin"', indy_machine_source)


# ---------------------------------------------------------------------------
# IRIX IRQ Mapping [CROSS-REF: IRIX kernel intr.c / ml/IP22.c]
# ---------------------------------------------------------------------------

class TestIRIXIRQMapping:
    """Verify hardware wiring matches IRIX's expected interrupt routing.

    IRIX uses 1-based interrupt numbering. The c0vec_tbl[] array maps
    IRIX IP numbers to handler functions. Our QEMU wiring must match
    so that IRIX dispatches interrupts to the correct handlers.

    Mapping:
      cpu-irq[0] → env.irq[2] → IRIX "hardint 3" → lcl0_intr
      cpu-irq[1] → env.irq[3] → IRIX "hardint 4" → lcl1_intr
      timer-irq[0] → env.irq[4] → IRIX "hardint 5" → clock
      timer-irq[1] → env.irq[5] → IRIX "hardint 6" → ackkgclock
      CP0 timer → env.irq[7] → IRIX "hardint 8" → r4kcount_intr
    """

    def test_local0_is_ip2_irix_ip3(self, indy_machine_source):
        """Local0 → cpu-irq[0] → irq[2] → IRIX IP3 = lcl0_intr.

        IRIX IP3 (hardint 3) dispatches to lcl0_intr() which handles
        SCSI, ethernet, and other LOCAL0 sources.
        """
        assert re.search(
            r'qdev_connect_gpio_out_named\(hpc3_dev,\s*"cpu-irq",\s*0,\s*cpu->env\.irq\[2\]\)',
            indy_machine_source
        ), "Local0 must be wired to irq[2] (IRIX IP3 / lcl0_intr)"
        # Verify IRIX mapping: irq[2] = hardware IP2 = IRIX IP3
        hw_ip = 2
        irix_ip = hw_ip + 1
        assert irix_ip == 3

    def test_local1_is_ip3_irix_ip4(self, indy_machine_source):
        """Local1 → cpu-irq[1] → irq[3] → IRIX IP4 = lcl1_intr.

        IRIX IP4 (hardint 4) dispatches to lcl1_intr() which handles
        power button, DMA, and other LOCAL1 sources.
        """
        assert re.search(
            r'qdev_connect_gpio_out_named\(hpc3_dev,\s*"cpu-irq",\s*1,\s*cpu->env\.irq\[3\]\)',
            indy_machine_source
        ), "Local1 must be wired to irq[3] (IRIX IP4 / lcl1_intr)"
        hw_ip = 3
        irix_ip = hw_ip + 1
        assert irix_ip == 4

    def test_timer0_is_ip4_irix_ip5_clock(self, indy_machine_source):
        """Timer0 → timer-irq[0] → irq[4] → IRIX IP5 = clock().

        IRIX IP5 (hardint 5) dispatches to clock() for the scheduling
        tick (on non-IOC1 boards; IOC1 boards use R4000 timer instead).
        """
        assert re.search(
            r'qdev_connect_gpio_out_named\(hpc3_dev,\s*"timer-irq",\s*0,\s*cpu->env\.irq\[4\]\)',
            indy_machine_source
        ), "Timer0 must be wired to irq[4] (IRIX IP5 / clock)"
        hw_ip = 4
        irix_ip = hw_ip + 1
        assert irix_ip == 5

    def test_timer1_is_ip5_irix_ip6_ackkgclock(self, indy_machine_source):
        """Timer1 → timer-irq[1] → irq[5] → IRIX IP6 = ackkgclock().

        IRIX IP6 (hardint 6) dispatches to ackkgclock() for profiling.
        """
        assert re.search(
            r'qdev_connect_gpio_out_named\(hpc3_dev,\s*"timer-irq",\s*1,\s*cpu->env\.irq\[5\]\)',
            indy_machine_source
        ), "Timer1 must be wired to irq[5] (IRIX IP6 / ackkgclock)"
        hw_ip = 5
        irix_ip = hw_ip + 1
        assert irix_ip == 6

    def test_cp0_timer_is_ip7_irix_ip8_r4kcount(self, cpu_source):
        """CP0 timer → IPTI=7 → irq[7] → IRIX IP8 = r4kcount_intr().

        IRIX IP8 (hardint 8) dispatches to r4kcount_intr() which is
        the R4000 Count/Compare scheduling clock on IOC1 boards (Indy).
        """
        assert "CP0_IntCtl = 0xe0000000" in cpu_source, (
            "CP0_IntCtl default must be 0xe0000000 (IPTI=7)"
        )
        intctl = 0xe0000000
        ipti = (intctl >> 29) & 7
        assert ipti == 7, f"IPTI should be 7 for timer on IP7"
        irix_ip = ipti + 1
        assert irix_ip == 8, f"IRIX IP should be 8, got {irix_ip}"
