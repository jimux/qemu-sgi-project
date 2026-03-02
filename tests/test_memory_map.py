"""
SGI Indy memory map constant assertions.

Verifies that sgi_indy.c defines correct memory map addresses, GIO slot
behavior, and memory probe regions matching the IP22/IP24 hardware.

These tests are FAST (source code analysis only, no QEMU boot).
"""

import re


class TestMemoryMapConstants:
    """Memory map #define values must match the IP22/IP24 hardware spec."""

    def test_ram_low_base(self, indy_machine_source):
        """Low system memory starts at 0x08000000."""
        assert "#define SGI_RAM_LOW_BASE" in indy_machine_source
        assert "0x08000000" in indy_machine_source

    def test_ram_high_base(self, indy_machine_source):
        """High system memory starts at 0x20000000."""
        assert "#define SGI_RAM_HIGH_BASE" in indy_machine_source
        assert "0x20000000" in indy_machine_source

    def test_gio_gfx_base(self, indy_machine_source):
        """GIO graphics slot at 0x1f000000."""
        assert re.search(
            r"#define\s+SGI_GIO_GFX_BASE\s+0x1f000000",
            indy_machine_source
        ), "SGI_GIO_GFX_BASE must be 0x1f000000"

    def test_gio_exp0_base(self, indy_machine_source):
        """GIO expansion slot 0 at 0x1f400000."""
        assert re.search(
            r"#define\s+SGI_GIO_EXP0_BASE\s+0x1f400000",
            indy_machine_source
        ), "SGI_GIO_EXP0_BASE must be 0x1f400000"

    def test_gio_exp1_base(self, indy_machine_source):
        """GIO expansion slot 1 at 0x1f600000."""
        assert re.search(
            r"#define\s+SGI_GIO_EXP1_BASE\s+0x1f600000",
            indy_machine_source
        ), "SGI_GIO_EXP1_BASE must be 0x1f600000"

    def test_mc_base(self, indy_machine_source):
        """Memory controller at 0x1fa00000."""
        assert re.search(
            r"#define\s+SGI_MC_BASE\s+0x1fa00000",
            indy_machine_source
        ), "SGI_MC_BASE must be 0x1fa00000"

    def test_hpc3_base(self, indy_machine_source):
        """HPC3 peripheral controller at 0x1fb80000."""
        assert re.search(
            r"#define\s+SGI_HPC3_BASE\s+0x1fb80000",
            indy_machine_source
        ), "SGI_HPC3_BASE must be 0x1fb80000"

    def test_prom_base(self, indy_machine_source):
        """PROM at 0x1fc00000."""
        assert re.search(
            r"#define\s+SGI_PROM_BASE\s+0x1fc00000",
            indy_machine_source
        ), "SGI_PROM_BASE must be 0x1fc00000"

    def test_prom_size_512k(self, indy_machine_source):
        """PROM size is 512KB."""
        assert re.search(
            r"#define\s+SGI_PROM_SIZE\s+\(512\s*\*\s*KiB\)",
            indy_machine_source
        ), "SGI_PROM_SIZE must be 512 * KiB"

    def test_ram_max_256mb(self, indy_machine_source):
        """Maximum RAM is 256MB, enforced with error_report."""
        assert re.search(
            r"#define\s+SGI_RAM_MAX\s+\(256\s*\*\s*MiB\)",
            indy_machine_source
        ), "SGI_RAM_MAX must be 256 * MiB"
        assert "RAM size more than 256MB is not supported" in indy_machine_source


class TestGIOSlots:
    """GIO slot stubs must signal 'no device present'."""

    def test_empty_slot_returns_ff(self, indy_machine_source):
        """Empty GIO slot reads return 0xffffffffffffffff (all bits set)."""
        assert "0xffffffffffffffffULL" in indy_machine_source, (
            "gio_empty_slot_read must return all-ones"
        )

    def test_newport_at_gfx_offset(self, indy_machine_source):
        """Newport is mapped at SGI_GIO_GFX_BASE + REX3_REG_OFFSET."""
        assert "SGI_GIO_GFX_BASE + REX3_REG_OFFSET" in indy_machine_source, (
            "Newport must be mapped at GIO GFX base + REX3 offset"
        )


class TestMemoryProbeRegions:
    """Unimplemented device regions for PROM memory probing."""

    def test_low_mem_probe_region(self, indy_machine_source):
        """Low memory probe at 0x08000000 covering 256MB."""
        assert re.search(
            r'create_unimplemented_device\("low-mem-probe",\s*SGI_RAM_LOW_BASE',
            indy_machine_source
        ), "Low memory probe region must be at SGI_RAM_LOW_BASE"

    def test_high_mem_probe_region(self, indy_machine_source):
        """High memory probe at 0x20000000 covering 256MB."""
        assert re.search(
            r'create_unimplemented_device\("high-mem-probe",\s*SGI_RAM_HIGH_BASE',
            indy_machine_source
        ), "High memory probe region must be at SGI_RAM_HIGH_BASE"

    def test_zero_mem_probe_region(self, indy_machine_source):
        """Zero-page probe at 0x00000000 covering 512KB."""
        assert re.search(
            r'create_unimplemented_device\("zero-mem-probe",\s*0x00000000,\s*512\s*\*\s*KiB\)',
            indy_machine_source
        ), "Zero memory probe region must be at 0x0 with 512KB size"
