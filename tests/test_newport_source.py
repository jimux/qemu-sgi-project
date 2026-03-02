"""
SGI Newport (XL) graphics controller source assertions.

Verifies VRAM dimensions, REX3 register layout, version encoding,
status bits, DCB addresses, and reset defaults.

These tests are FAST (source code analysis only, no QEMU boot).
"""

import re


class TestNewportDimensions:
    """Screen dimensions and VRAM guard band constants."""

    def test_default_screen_width(self, newport_header):
        """Default screen width is 1280 pixels."""
        assert re.search(
            r"#define\s+NEWPORT_DEFAULT_SCREEN_W\s+1280",
            newport_header
        )

    def test_default_screen_height(self, newport_header):
        """Default screen height is 1024 pixels."""
        assert re.search(
            r"#define\s+NEWPORT_DEFAULT_SCREEN_H\s+1024",
            newport_header
        )

    def test_vram_guard_band(self, newport_header):
        """VRAM guard band is 64 pixels (matches MAME layout)."""
        assert re.search(
            r"#define\s+NEWPORT_VRAM_GUARD\s+64",
            newport_header
        )

    def test_configurable_screen_w(self, newport_header):
        """State struct has configurable screen_w field."""
        assert re.search(r"uint16_t\s+screen_w;", newport_header)

    def test_configurable_screen_h(self, newport_header):
        """State struct has configurable screen_h field."""
        assert re.search(r"uint16_t\s+screen_h;", newport_header)


class TestREX3RegisterLayout:
    """REX3 register offsets and region parameters."""

    def test_reg_size(self, newport_header):
        """REX3_REG_SIZE is 0x2000 (8KB register region)."""
        assert re.search(
            r"#define\s+REX3_REG_SIZE\s+0x2000",
            newport_header
        )

    def test_reg_offset(self, newport_header):
        """REX3_REG_OFFSET is 0x0f0000 within GIO slot."""
        assert re.search(
            r"#define\s+REX3_REG_OFFSET\s+0x0f0000",
            newport_header
        )

    def test_go_offset(self, newport_source):
        """REX3_GO_OFFSET is 0x0800 (trigger command execution)."""
        assert re.search(
            r"#define\s+REX3_GO_OFFSET\s+0x0800",
            newport_source
        )

    def test_drawmode1_offset(self, newport_header):
        """REX3_DRAWMODE1 is at offset 0x0000."""
        assert re.search(
            r"#define\s+REX3_DRAWMODE1\s+0x0000",
            newport_header
        )

    def test_drawmode0_offset(self, newport_header):
        """REX3_DRAWMODE0 is at offset 0x0004."""
        assert re.search(
            r"#define\s+REX3_DRAWMODE0\s+0x0004",
            newport_header
        )

    def test_status_offset(self, newport_header):
        """REX3_STATUS is at offset 0x1338."""
        assert re.search(
            r"#define\s+REX3_STATUS\s+0x1338",
            newport_header
        )

    def test_dcbmode_offset(self, newport_header):
        """REX3_DCBMODE is at offset 0x0238."""
        assert re.search(
            r"#define\s+REX3_DCBMODE\s+0x0238",
            newport_header
        )

    def test_dcbdata0_offset(self, newport_header):
        """REX3_DCBDATA0 is at offset 0x0240."""
        assert re.search(
            r"#define\s+REX3_DCBDATA0\s+0x0240",
            newport_header
        )


class TestNewportVersion:
    """Newport version identification."""

    def test_version_indy(self, newport_header):
        """REX3_VERSION_INDY is 3."""
        assert re.search(
            r"#define\s+REX3_VERSION_INDY\s+3",
            newport_header
        )


class TestNewportStatus:
    """Status register bit definitions."""

    def test_status_version_mask(self, newport_header):
        """REX3_STATUS_VERSION_MASK is 0x00000007 (3 bits)."""
        assert re.search(
            r"#define\s+REX3_STATUS_VERSION_MASK\s+0x00000007",
            newport_header
        )

    def test_status_gfxbusy_bit(self, newport_header):
        """GFXBUSY is bit 3."""
        assert re.search(
            r"#define\s+REX3_STATUS_GFXBUSY\s+\(1\s*<<\s*3\)",
            newport_header
        )

    def test_status_vrint_bit(self, newport_header):
        """VRINT is bit 5."""
        assert re.search(
            r"#define\s+REX3_STATUS_VRINT\s+\(1\s*<<\s*5\)",
            newport_header
        )


class TestDCBAddresses:
    """DCB slave addresses for sub-devices."""

    def test_dcb_addr_vc2(self, newport_header):
        """DCB_ADDR_VC2 is 0 (video timing controller)."""
        assert re.search(
            r"#define\s+DCB_ADDR_VC2\s+0\b",
            newport_header
        )

    def test_dcb_addr_cmap01(self, newport_header):
        """DCB_ADDR_CMAP01 is 1 (both CMAPs)."""
        assert re.search(
            r"#define\s+DCB_ADDR_CMAP01\s+1\b",
            newport_header
        )

    def test_dcb_addr_ramdac(self, newport_header):
        """DCB_ADDR_RAMDAC is 7."""
        assert re.search(
            r"#define\s+DCB_ADDR_RAMDAC\s+7\b",
            newport_header
        )


class TestNewportDefaults:
    """Reset default values."""

    def test_reset_write_mask(self, newport_source):
        """write_mask resets to 0x00ffffff."""
        assert "s->write_mask = 0x00ffffff" in newport_source

    def test_reset_global_mask(self, newport_source):
        """global_mask resets to 0xff (XL8 Indy default)."""
        assert "s->global_mask = 0xff" in newport_source

    def test_cmap_revision(self, newport_source):
        """CMAP revision is 0xa1."""
        assert "s->cmap_revision = 0xa1" in newport_source

    def test_xmap_revision(self, newport_source):
        """XMAP revision is 1."""
        assert "s->xmap_revision = 1" in newport_source


class TestNewportAddressNormalization:
    """Newport aligns to 32-bit (not 64-bit like MC)."""

    def test_addr_aligned_32bit(self, newport_source):
        """Newport normalizes address with addr &= ~3ULL."""
        # Must appear in both read and write functions
        assert "addr &= ~3ULL" in newport_source, (
            "Newport must align addresses to 32-bit (not 64-bit)"
        )
        # And must NOT use ~7ULL (that's MC's pattern)
        read_fn = re.search(
            r"sgi_newport_read\(.*?\{(.*?)^}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert read_fn, "sgi_newport_read function not found"
        assert "~7ULL" not in read_fn.group(1), (
            "Newport must NOT use ~7ULL normalization (that's MC)"
        )


class TestXMAPCRSRegisters:
    """[CROSS-REF] XMAP9 CRS register numbering matches MAME."""

    def test_xmap_crs5_mode_table_write(self, newport_source):
        """CRS 5 is mode table write (not CRS 3)."""
        assert re.search(
            r"case 5:.*Mode table write",
            newport_source
        ), "Mode table write must be at CRS 5 (MAME ref)"

    def test_xmap_crs3_cursor_cmap(self, newport_source):
        """CRS 3 is cursor CMAP MSB."""
        assert re.search(
            r"case 3:.*[Cc]ursor CMAP",
            newport_source
        ), "CRS 3 must be cursor CMAP MSB (MAME ref)"

    def test_xmap_crs4_popup_cmap(self, newport_source):
        """CRS 4 is popup CMAP MSB."""
        assert re.search(
            r"case 4:.*[Pp]opup CMAP",
            newport_source
        ), "CRS 4 must be popup CMAP MSB (MAME ref)"

    def test_xmap_crs7_mode_table_idx(self, newport_source):
        """CRS 7 is mode table address."""
        assert re.search(
            r"case 7:.*[Mm]ode table address",
            newport_source
        ), "CRS 7 must be mode table address (MAME ref)"

    def test_xmap_state_fields(self, newport_header):
        """XMAP state has cursor_cmap, popup_cmap, mode_table_idx."""
        assert "xmap_cursor_cmap" in newport_header
        assert "xmap_popup_cmap" in newport_header
        assert "xmap_mode_table_idx" in newport_header


class TestCMAPPaletteSize:
    """[CROSS-REF] CMAP palette must be 8192 entries."""

    def test_cmap_8192_entries(self, newport_header):
        """CMAP palette array has 8192 entries (13-bit index)."""
        assert re.search(
            r"cmap0_palette\[8192\]",
            newport_header
        ), "CMAP must have 8192 entries (MAME: palette_entries() = 0x2000)"

    def test_cmap_write_bound_8192(self, newport_source):
        """CMAP write bounds check uses 8192."""
        assert "cmap_palette_idx < 8192" in newport_source


class TestVRINTTimer:
    """[CROSS-REF] Vertical retrace interrupt from Newport."""

    def test_vblank_timer_exists(self, newport_header):
        """Newport state has vblank_timer."""
        assert "vblank_timer" in newport_header

    def test_irq_output_exists(self, newport_header):
        """Newport state has qemu_irq irq output."""
        assert "qemu_irq irq" in newport_header

    def test_vblank_callback(self, newport_source):
        """VBLANK timer callback sets STATUS_VRINT."""
        assert "STATUS_VRINT" in newport_source
        assert "newport_vblank_timer" in newport_source

    def test_status_read_lowers_irq(self, newport_source):
        """Reading status register lowers IRQ (MAME behavior)."""
        assert "qemu_irq_lower" in newport_source


class TestCIMSBDisplayPipeline:
    """[CROSS-REF] ci_msb computation in display pipeline."""

    def test_ci_msb_shift5(self, newport_source):
        """CI mode uses (mode_entry & 0xf8) << 5 for 13-bit index."""
        assert "(mode_entry & 0xf8) << 5" in newport_source

    def test_rgb_map0_msb(self, newport_source):
        """RGB Map0 uses ci_msb = 0x1d00."""
        assert "ci_msb = 0x1d00" in newport_source

    def test_rgb_map1_msb(self, newport_source):
        """RGB Map1 uses ci_msb = 0x1e00."""
        assert "ci_msb = 0x1e00" in newport_source

    def test_rgb_map2_msb(self, newport_source):
        """RGB Map2 uses ci_msb = 0x1f00."""
        assert "ci_msb = 0x1f00" in newport_source


class TestPixelClipping:
    """[CROSS-REF] Screenmask pixel clipping."""

    def test_clip_function_exists(self, newport_source):
        """pixel_clip_pass function exists."""
        assert "newport_pixel_clip_pass" in newport_source

    def test_clip_called_in_output(self, newport_source):
        """output_pixel checks clip_mode before writing."""
        assert re.search(
            r"clip_mode.*newport_pixel_clip_pass",
            newport_source, re.DOTALL
        )


class TestRAMDACGamma:
    """RAMDAC gamma correction in display pipeline."""

    def test_ramdac_lut_applied(self, newport_source):
        """RAMDAC LUT is applied in display update."""
        assert "ramdac_lut_r" in newport_source
        assert "ramdac_lut_g" in newport_source
        assert "ramdac_lut_b" in newport_source


class TestDIDPerScanline:
    """[CROSS-REF] DID per-scanline mode changes."""

    def test_did_next_entry(self, newport_source):
        """Display pipeline reads next DID entry per line."""
        assert "next_did_entry" in newport_source

    def test_did_x_position_check(self, newport_source):
        """Display pipeline checks X position against DID entry."""
        assert re.search(
            r"next_did_entry >> 5",
            newport_source
        ), "DID entry bits [15:5] = X position of mode change"
