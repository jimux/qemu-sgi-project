"""
Virtuix (IP55) vs authentic-Indy machine separation assertions.

The QEMU source was refactored to cleanly split the AUTHENTIC Indy/IP22
machine (sgi_indy.c + sgi_mc.c / sgi_hpc3.c / sgi_newport.c) from the
virtualization-native IP55 "virtuix" machine, which now lives in its own
set of source files with renamed QOM types:

  - hw/mips/sgi_virtuix.c        (virtuix machine)
  - hw/misc/sgi_mc_virtuix.c     (TYPE_SGI_MC_VIRTUIX, + 64-bit host RT counter)
  - hw/misc/sgi_hpc3_virtuix.c   (TYPE_SGI_HPC3_VIRTUIX)
  - hw/display/sgi_newport_virtuix.c (TYPE_SGI_NEWPORT_VIRTUIX)

These tests verify the IP55-specific behavior lives on the virtuix side
and that authentic Indy did NOT inherit the IP55 extensions (66 MHz clock,
2 GiB RAM, 64-bit MC real-time counter, SMP).

These tests are FAST (source code analysis only, no QEMU boot).
"""

import re


class TestVirtuixCPUClock:
    """Virtuix CPU clock is the IP55 66.67 MHz default, overridable via env."""

    def test_virtuix_clock_66mhz(self, virtuix_machine_source):
        """Virtuix defaults cpuclk to 66666666 Hz (66.67 MHz)."""
        assert "clock_set_hz(cpuclk, e && *e ? strtoull(e, NULL, 0) : 66666666)" \
            in virtuix_machine_source, \
            "Virtuix CPU clock default is not 66666666 Hz"

    def test_virtuix_clock_env_override(self, virtuix_machine_source):
        """Virtuix CPU clock honors the IP55_CPU_HZ environment override."""
        assert 'getenv("IP55_CPU_HZ")' in virtuix_machine_source, \
            "Virtuix CPU clock does not honor IP55_CPU_HZ"

    def test_indy_clock_stays_100mhz(self, indy_machine_source):
        """Authentic Indy did NOT inherit the 66 MHz IP55 clock."""
        assert "clock_set_hz(cpuclk, 100000000)" in indy_machine_source, \
            "Authentic Indy CPU clock is not 100 MHz"
        assert "66666666" not in indy_machine_source, \
            "Authentic Indy must not carry the IP55 66 MHz clock"
        assert "IP55_CPU_HZ" not in indy_machine_source, \
            "Authentic Indy must not honor IP55_CPU_HZ"


class TestVirtuixRAMMax:
    """Virtuix supports up to 2 GiB RAM; authentic Indy is capped at 256 MiB."""

    def test_virtuix_ram_max_2gib(self, virtuix_machine_source):
        """Virtuix SGI_RAM_MAX is 2048 * MiB (2 GiB)."""
        assert re.search(
            r"#define\s+SGI_RAM_MAX\s+\(2048\s*\*\s*MiB\)",
            virtuix_machine_source
        ), "Virtuix SGI_RAM_MAX is not 2048 * MiB"

    def test_indy_ram_max_256mib(self, indy_machine_source):
        """Authentic Indy SGI_RAM_MAX is 256 * MiB (not the IP55 2 GiB)."""
        assert re.search(
            r"#define\s+SGI_RAM_MAX\s+\(256\s*\*\s*MiB\)",
            indy_machine_source
        ), "Authentic Indy SGI_RAM_MAX is not 256 * MiB"


class TestVirtuixMCRealtimeCounter:
    """The 64-bit host real-time counter lives ONLY on the virtuix MC."""

    def test_virtuix_mc_header_has_ctr64(self, mc_virtuix_header):
        """Virtuix MC header defines MC_REALTIME_CTR64_LO/HI."""
        assert re.search(
            r"#define\s+MC_REALTIME_CTR64_LO\s+0x0054", mc_virtuix_header
        ), "Virtuix MC header missing MC_REALTIME_CTR64_LO"
        assert re.search(
            r"#define\s+MC_REALTIME_CTR64_HI\s+0x0058", mc_virtuix_header
        ), "Virtuix MC header missing MC_REALTIME_CTR64_HI"

    def test_virtuix_mc_source_handles_ctr64(self, mc_virtuix_source):
        """Virtuix MC read path services both 64-bit RT counter halves."""
        assert "case MC_REALTIME_CTR64_LO:" in mc_virtuix_source
        assert "case MC_REALTIME_CTR64_HI:" in mc_virtuix_source

    def test_indy_mc_header_no_ctr64_define(self, mc_header):
        """Authentic Indy MC header must NOT #define the 64-bit RT counter."""
        assert not re.search(r"#define\s+MC_REALTIME_CTR64_LO", mc_header), \
            "Authentic Indy MC must not define MC_REALTIME_CTR64_LO"
        assert not re.search(r"#define\s+MC_REALTIME_CTR64_HI", mc_header), \
            "Authentic Indy MC must not define MC_REALTIME_CTR64_HI"
        # The 32-bit MC_REALTIME_CTR is retained on indy.
        assert re.search(r"#define\s+MC_REALTIME_CTR\s+0x0050", mc_header), \
            "Authentic Indy MC should keep the 32-bit MC_REALTIME_CTR"

    def test_indy_mc_source_no_ctr64(self, mc_source):
        """Authentic Indy MC source must not reference the 64-bit RT counter."""
        assert "MC_REALTIME_CTR64_LO" not in mc_source
        assert "MC_REALTIME_CTR64_HI" not in mc_source


class TestVirtuixDeviceInstantiation:
    """Virtuix machine instantiates the renamed virtuix device types + SMP."""

    def test_instantiates_virtuix_mc(self, virtuix_machine_source):
        """Virtuix machine creates TYPE_SGI_MC_VIRTUIX."""
        assert "qdev_new(TYPE_SGI_MC_VIRTUIX)" in virtuix_machine_source

    def test_instantiates_virtuix_hpc3(self, virtuix_machine_source):
        """Virtuix machine creates TYPE_SGI_HPC3_VIRTUIX."""
        assert "qdev_new(TYPE_SGI_HPC3_VIRTUIX)" in virtuix_machine_source

    def test_instantiates_virtuix_newport(self, virtuix_machine_source):
        """Virtuix machine creates TYPE_SGI_NEWPORT_VIRTUIX."""
        assert "qdev_new(TYPE_SGI_NEWPORT_VIRTUIX)" in virtuix_machine_source

    def test_instantiates_smp(self, virtuix_machine_source):
        """Virtuix machine creates the paravirtual SMP/IPI device."""
        assert "qdev_new(TYPE_SGI_SMP)" in virtuix_machine_source

    def test_max_cpus_32(self, virtuix_machine_source):
        """Virtuix supports up to 32 CPUs (IP55 SMP)."""
        assert re.search(r"max_cpus\s*=\s*32", virtuix_machine_source), \
            "Virtuix machine does not set max_cpus = 32"

    def test_indy_is_single_cpu(self, indy_machine_source):
        """Authentic Indy did not inherit the virtuix SMP device or types."""
        assert "TYPE_SGI_SMP" not in indy_machine_source, \
            "Authentic Indy must not reference the SMP device"
        assert "TYPE_SGI_MC_VIRTUIX" not in indy_machine_source
        assert "TYPE_SGI_HPC3_VIRTUIX" not in indy_machine_source
        assert "TYPE_SGI_NEWPORT_VIRTUIX" not in indy_machine_source


class TestVirtuixNewportPlaneFix:
    """Virtuix Newport keeps the popup/overlay plane-shift fix."""

    def test_plane_shift_present(self, newport_virtuix_source):
        """Popup planes shift src << 2; overlay planes shift src << 8.

        Without these shifts the popup write (value in [1:0], write_mask 0xcc)
        gets masked to zero and the 4Dwm menu is invisible.
        """
        fn = re.search(
            r"newport_logic_pixel\(.*?\{(.*?)^\}",
            newport_virtuix_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_logic_pixel function not found in virtuix Newport"
        body = fn.group(1)
        assert re.search(r"dm1_planes\s*==\s*5", body), \
            "popup (plane 5) branch missing"
        assert "src <<= 2" in body, "popup plane shift (src <<= 2) missing"
        assert "src <<= 8" in body, "overlay plane shift (src <<= 8) missing"
