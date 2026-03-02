"""
SGI Memory Controller (MC) source assertions.

Verifies register offsets, address normalization, MEMCFG format,
system ID masks, and default values from sgi_mc.c and sgi_mc.h.

These tests are FAST (source code analysis only, no QEMU boot).
"""

import re


class TestMCRegisterOffsets:
    """MC register offsets must match IRIX sys/mc.h."""

    def test_cpu_ctrl0(self, mc_header):
        """MC_CPU_CTRL0 at offset 0x0000."""
        assert re.search(
            r"#define\s+MC_CPU_CTRL0\s+0x0000",
            mc_header
        )

    def test_memcfg0(self, mc_header):
        """MC_MEMCFG0 at offset 0x00c0."""
        assert re.search(
            r"#define\s+MC_MEMCFG0\s+0x00c0",
            mc_header
        )

    def test_memcfg1(self, mc_header):
        """MC_MEMCFG1 at offset 0x00c8."""
        assert re.search(
            r"#define\s+MC_MEMCFG1\s+0x00c8",
            mc_header
        )

    def test_sysid(self, mc_header):
        """MC_SYSID at offset 0x0018."""
        assert re.search(
            r"#define\s+MC_SYSID\s+0x0018",
            mc_header
        )

    def test_rpss_div(self, mc_header):
        """MC_RPSS_DIV at offset 0x0028."""
        assert re.search(
            r"#define\s+MC_RPSS_DIV\s+0x0028",
            mc_header
        )

    def test_rpss_ctr(self, mc_header):
        """MC_RPSS_CTR at offset 0x1000 (separate page)."""
        assert re.search(
            r"#define\s+MC_RPSS_CTR\s+0x1000",
            mc_header
        )

    def test_reg_size(self, mc_header):
        """MC_REG_SIZE is 0x20000 (covers semaphores)."""
        assert re.search(
            r"#define\s+MC_REG_SIZE\s+0x20000",
            mc_header
        )


class TestMCAddressNormalization:
    """MC read/write handlers must normalize addresses with addr &= ~7ULL."""

    def test_read_normalizes_addr(self, mc_source):
        """sgi_mc_read normalizes address to 64-bit boundary."""
        # The function should have addr &= ~7ULL early
        read_fn = re.search(
            r"sgi_mc_read\(.*?\{(.*?)^}",
            mc_source, re.DOTALL | re.MULTILINE
        )
        assert read_fn, "sgi_mc_read function not found"
        assert "addr &= ~7ULL" in read_fn.group(1), (
            "sgi_mc_read must normalize addr with &= ~7ULL"
        )

    def test_write_normalizes_addr(self, mc_source):
        """sgi_mc_write normalizes address to 64-bit boundary."""
        write_fn = re.search(
            r"sgi_mc_write\(.*?\{(.*?)^}",
            mc_source, re.DOTALL | re.MULTILINE
        )
        assert write_fn, "sgi_mc_write function not found"
        assert "addr &= ~7ULL" in write_fn.group(1), (
            "sgi_mc_write must normalize addr with &= ~7ULL"
        )


class TestMEMCFGFormat:
    """MEMCFG register bit field definitions."""

    def test_memcfg_vld_bit(self, mc_source):
        """MEMCFG_VLD is 0x2000 (bit 13)."""
        assert re.search(
            r"#define\s+MEMCFG_VLD\s+0x2000",
            mc_source
        )

    def test_memcfg_bnk_bit(self, mc_source):
        """MEMCFG_BNK is 0x4000 (bit 14, 2 subbanks)."""
        assert re.search(
            r"#define\s+MEMCFG_BNK\s+0x4000",
            mc_source
        )

    def test_memcfg_addr_mask(self, mc_source):
        """MEMCFG_ADDR_MASK is 0x00ff (8-bit base address)."""
        assert re.search(
            r"#define\s+MEMCFG_ADDR_MASK\s+0x00ff",
            mc_source
        )

    def test_memcfg_size_mask(self, mc_source):
        """MEMCFG_SIZE_MASK is 0x1f00."""
        assert re.search(
            r"#define\s+MEMCFG_SIZE_MASK\s+0x1f00",
            mc_source
        )

    def test_memcfg_size_codes(self, mc_source):
        """Size codes: 4MB=0x0000, 128MB=0x1f00."""
        assert re.search(r"#define\s+MEMCFG_4MB\s+0x0000", mc_source)
        assert re.search(r"#define\s+MEMCFG_128MB\s+0x1f00", mc_source)


class TestMCSysID:
    """System ID register masks."""

    def test_sysid_rev_mask(self, mc_source):
        """MC_SYSID_REV_MASK is 0x0f (revision in lower 4 bits)."""
        assert re.search(
            r"#define\s+MC_SYSID_REV_MASK\s+0x0f",
            mc_source
        )

    def test_sysid_eisa_mask(self, mc_source):
        """MC_SYSID_EISA_MASK is 0x10 (EISA present bit)."""
        assert re.search(
            r"#define\s+MC_SYSID_EISA_MASK\s+0x10",
            mc_source
        )


class TestMCDefaults:
    """MC reset default register values."""

    def test_default_rpss_div(self, mc_source):
        """Default RPSS divider is 0x0104."""
        assert "s->rpss_div = 0x0104" in mc_source, (
            "Default rpss_div must be 0x0104"
        )

    def test_seg0_alias_size(self, mc_source):
        """SEG0 low alias is 512KB."""
        assert re.search(
            r"#define\s+SEG0_ALIAS_SIZE\s+\(512\s*\*\s*1024\)",
            mc_source
        ), "SEG0_ALIAS_SIZE must be 512 * 1024"


class TestMCDMA:
    """MC DMA register behavior."""

    def test_dma_run_bit6(self, mc_source):
        """DMA_RUN bit 6 (0x40) indicates running."""
        assert "s->dma_run = 0x40" in mc_source, (
            "DMA start must set dma_run to 0x40 (bit 6)"
        )
