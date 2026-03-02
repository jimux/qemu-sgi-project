"""
SGI Indy build configuration assertions.

Verifies Kconfig dependencies, selected devices, and meson.build
entries for the SGI_INDY machine.

These tests are FAST (file analysis only, no QEMU boot).
"""

import re


class TestSGIIndyKconfig:
    """Kconfig entries for the SGI_INDY machine."""

    def test_depends_mips64_tcg(self, mips_kconfig):
        """SGI_INDY depends on MIPS64 && TCG."""
        # Find the SGI_INDY config block and check its depends line
        block = re.search(
            r"config SGI_INDY\n(.*?)(?=\nconfig\b|\Z)",
            mips_kconfig, re.DOTALL
        )
        assert block, "config SGI_INDY block not found in Kconfig"
        assert "depends on MIPS64 && TCG" in block.group(1)

    def test_selects_sgi_mc(self, mips_kconfig):
        """SGI_INDY selects SGI_MC (memory controller)."""
        block = re.search(
            r"config SGI_INDY\n(.*?)(?=\nconfig\b|\Z)",
            mips_kconfig, re.DOTALL
        )
        assert block
        assert "select SGI_MC" in block.group(1)

    def test_selects_sgi_hpc3(self, mips_kconfig):
        """SGI_INDY selects SGI_HPC3 (peripheral controller)."""
        block = re.search(
            r"config SGI_INDY\n(.*?)(?=\nconfig\b|\Z)",
            mips_kconfig, re.DOTALL
        )
        assert block
        assert "select SGI_HPC3" in block.group(1)

    def test_selects_sgi_newport(self, mips_kconfig):
        """SGI_INDY selects SGI_NEWPORT (graphics)."""
        block = re.search(
            r"config SGI_INDY\n(.*?)(?=\nconfig\b|\Z)",
            mips_kconfig, re.DOTALL
        )
        assert block
        assert "select SGI_NEWPORT" in block.group(1)

    def test_selects_wd33c93(self, mips_kconfig):
        """SGI_INDY selects WD33C93 (SCSI controller)."""
        block = re.search(
            r"config SGI_INDY\n(.*?)(?=\nconfig\b|\Z)",
            mips_kconfig, re.DOTALL
        )
        assert block
        assert "select WD33C93" in block.group(1)

    def test_selects_sgi_arcs(self, mips_kconfig):
        """SGI_INDY selects SGI_ARCS (firmware stubs)."""
        block = re.search(
            r"config SGI_INDY\n(.*?)(?=\nconfig\b|\Z)",
            mips_kconfig, re.DOTALL
        )
        assert block
        assert "select SGI_ARCS" in block.group(1)

    def test_default_enabled(self, mips_kconfig):
        """SGI_INDY is default y."""
        block = re.search(
            r"config SGI_INDY\n(.*?)(?=\nconfig\b|\Z)",
            mips_kconfig, re.DOTALL
        )
        assert block
        assert "default y" in block.group(1)


class TestBuildFiles:
    """Meson build file entries."""

    def test_sgi_indy_in_meson_build(self, mips_meson_build):
        """sgi_indy.c is compiled under CONFIG_SGI_INDY."""
        assert re.search(
            r"CONFIG_SGI_INDY.*sgi_indy\.c",
            mips_meson_build
        ), "sgi_indy.c must be compiled when CONFIG_SGI_INDY is set"
