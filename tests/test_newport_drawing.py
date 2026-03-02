"""
SGI Newport drawing pipeline source assertions.

Verifies logic operations, draw commands, drawmode decoding, rwpacked tables,
plane selection, and window offset handling.

These tests are FAST (source code analysis only, no QEMU boot).

Categories:
  - CROSS-REF: verified against MAME newport.cpp
  - ASSUMPTION: documents simplifications or workarounds
"""

import re


# ---------------------------------------------------------------------------
# Newport Logic Operations [CROSS-REF: MAME newport.cpp:1039-1062]
# ---------------------------------------------------------------------------

class TestNewportLogicOps:
    """All 16 ROP logic operations must be implemented in newport_logic_pixel.

    MAME ref: logic_pixel() at newport.cpp:1039-1062.
    """

    def test_all_16_rops_implemented(self, newport_source):
        """Switch must have cases 0x0 through 0xf."""
        # Find the logic_pixel function body
        fn = re.search(
            r"newport_logic_pixel\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_logic_pixel function not found"
        body = fn.group(1)
        for rop in range(16):
            assert re.search(
                rf"case\s+0x{rop:x}:", body
            ), f"ROP case 0x{rop:x} missing from logic_pixel switch"

    def test_rop_0x3_is_src_copy(self, newport_source):
        """Case 0x3 must assign src (the most common ROP)."""
        assert re.search(
            r"case\s+0x3:\s*result\s*=\s*src\s*;",
            newport_source
        )

    def test_rop_0x6_is_xor(self, newport_source):
        """Case 0x6 must compute src ^ dst."""
        assert re.search(
            r"case\s+0x6:\s*result\s*=\s*src\s*\^\s*dst\s*;",
            newport_source
        )

    def test_rop_0x5_is_dst_noop(self, newport_source):
        """Case 0x5 must be a no-op (result = dst)."""
        assert re.search(
            r"case\s+0x5:\s*result\s*=\s*dst\s*;",
            newport_source
        )

    def test_mask_applied_after_rop(self, newport_source):
        """Mask must be applied: (buf & ~mask) | (result & mask)."""
        fn = re.search(
            r"newport_logic_pixel\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_logic_pixel function not found"
        body = fn.group(1)
        # Check for mask = write_mask & global_mask
        assert "write_mask" in body and "global_mask" in body
        # Check for the masked write pattern
        assert re.search(r"~mask.*result\s*&\s*mask", body)


# ---------------------------------------------------------------------------
# Newport Draw Commands [CROSS-REF: MAME newport.cpp:3418+]
# ---------------------------------------------------------------------------

class TestNewportDrawCommands:
    """Draw command functions must exist and be dispatched from the command handler."""

    def test_draw_block_exists(self, newport_source):
        """newport_draw_block function must exist."""
        assert re.search(
            r"static\s+void\s+newport_draw_block\s*\(",
            newport_source
        )

    def test_draw_span_exists(self, newport_source):
        """newport_draw_span function must exist."""
        assert re.search(
            r"static\s+void\s+newport_draw_span\s*\(",
            newport_source
        )

    def test_draw_iline_exists(self, newport_source):
        """newport_draw_iline function must exist."""
        assert re.search(
            r"static\s+void\s+newport_draw_iline\s*\(",
            newport_source
        )

    def test_draw_fline_exists(self, newport_source):
        """newport_draw_fline function must exist."""
        assert re.search(
            r"static\s+void\s+newport_draw_fline\s*\(",
            newport_source
        )

    def test_draw_scr2scr_exists(self, newport_source):
        """newport_draw_scr2scr function must exist."""
        assert re.search(
            r"static\s+void\s+newport_draw_scr2scr\s*\(",
            newport_source
        )

    def test_unimplemented_adrmode_logged(self, newport_source):
        """Unimplemented address modes must produce LOG_UNIMP."""
        # Find the rex3 command handler
        fn = re.search(
            r"newport_do_rex3_command\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_do_rex3_command not found"
        body = fn.group(1)
        assert "LOG_UNIMP" in body


# ---------------------------------------------------------------------------
# Newport DRAWMODE1 Decode [CROSS-REF: MAME newport.cpp:3786-3807]
# ---------------------------------------------------------------------------

class TestNewportDrawmode1Decode:
    """DRAWMODE1 bit field extraction must match MAME layout."""

    def test_dm1_planes_bits_0_2(self, newport_source):
        """dm1_planes = val & 7 (bits 2:0)."""
        assert re.search(
            r"dm1_planes\s*=\s*val\s*&\s*7",
            newport_source
        )

    def test_dm1_drawdepth_bits_3_4(self, newport_source):
        """dm1_drawdepth = (val >> 3) & 3 (bits 4:3)."""
        assert re.search(
            r"dm1_drawdepth\s*=\s*\(val\s*>>\s*3\)\s*&\s*3",
            newport_source
        )

    def test_dm1_rwpacked_bit_7(self, newport_source):
        """dm1_rwpacked = (val >> 7) & 1 (bit 7)."""
        assert re.search(
            r"dm1_rwpacked\s*=\s*\(val\s*>>\s*7\)\s*&\s*1",
            newport_source
        )

    def test_dm1_hostdepth_bits_8_9(self, newport_source):
        """dm1_hostdepth = (val >> 8) & 3 (bits 9:8)."""
        assert re.search(
            r"dm1_hostdepth\s*=\s*\(val\s*>>\s*8\)\s*&\s*3",
            newport_source
        )

    def test_dm1_logicop_bits_28_31(self, newport_source):
        """dm1_logicop = (val >> 28) & 0xf (bits 31:28)."""
        assert re.search(
            r"dm1_logicop\s*=\s*\(val\s*>>\s*28\)\s*&\s*0xf",
            newport_source
        )

    def test_dm1_rgbmode_bit_15(self, newport_source):
        """dm1_rgbmode = (val >> 15) & 1 (bit 15)."""
        assert re.search(
            r"dm1_rgbmode\s*=\s*\(val\s*>>\s*15\)\s*&\s*1",
            newport_source
        )


# ---------------------------------------------------------------------------
# Newport DRAWMODE0 Decode
# ---------------------------------------------------------------------------

class TestNewportDrawmode0Decode:
    """DRAWMODE0 bit field extraction macros and decoded fields."""

    def test_dm0_adrmode_bits_2_4(self, newport_source):
        """DM0_ADRMODE extracts bits 4:2 — (dm0 >> 2) & 7."""
        assert re.search(
            r"#define\s+DM0_ADRMODE\(dm0\)\s+\(\(\(dm0\)\s*>>\s*2\)\s*&\s*7\)",
            newport_source
        )

    def test_dm0_colorhost_bit_6(self, newport_source):
        """dm0_colorhost decoded from bit 6."""
        assert re.search(
            r"dm0_colorhost\s*=\s*\(val\s*>>\s*6\)\s*&\s*1",
            newport_source
        )

    def test_dm0_stoponx_bit_8(self, newport_source):
        """dm0_stoponx decoded from bit 8."""
        assert re.search(
            r"dm0_stoponx\s*=\s*\(val\s*>>\s*8\)\s*&\s*1",
            newport_source
        )

    def test_dm0_stopony_bit_9(self, newport_source):
        """dm0_stopony decoded from bit 9."""
        assert re.search(
            r"dm0_stopony\s*=\s*\(val\s*>>\s*9\)\s*&\s*1",
            newport_source
        )

    def test_dm0_zpattern_bit_12(self, newport_source):
        """dm0_zpattern decoded from bit 12."""
        assert re.search(
            r"dm0_zpattern\s*=\s*\(val\s*>>\s*12\)\s*&\s*1",
            newport_source
        )

    def test_dm0_lspattern_bit_13(self, newport_source):
        """dm0_lspattern decoded from bit 13."""
        assert re.search(
            r"dm0_lspattern\s*=\s*\(val\s*>>\s*13\)\s*&\s*1",
            newport_source
        )


# ---------------------------------------------------------------------------
# Newport RWPacked Table [CROSS-REF: MAME newport.cpp:3358-3365]
# ---------------------------------------------------------------------------

class TestNewportRWPacked:
    """rwpacked pixel limit table must match MAME layout."""

    def test_rwpacked_max_len_table(self, newport_source):
        """rwpacked_max_len[2][4] array must exist."""
        assert re.search(
            r"rwpacked_max_len\[2\]\[4\]",
            newport_source
        )

    def test_rwpacked_4bpp_limit_4(self, newport_source):
        """rwdouble=0, 4bpp: limit is 4 pixels per word."""
        # First row of table: { 4, 4, 2, 1 }
        assert re.search(
            r"rwpacked_max_len.*=.*\{\s*\{\s*4,\s*4,\s*2,\s*1\s*\}",
            newport_source, re.DOTALL
        )

    def test_rwpacked_8bpp_limit_4(self, newport_source):
        """rwdouble=0, 8bpp: limit is 4 pixels per word."""
        # Same row, second element — already covered by pattern above
        match = re.search(
            r"rwpacked_max_len.*=.*\{\s*\{\s*4,\s*4",
            newport_source, re.DOTALL
        )
        assert match

    def test_rwpacked_double_8bpp_limit_8(self, newport_source):
        """rwdouble=1, 8bpp: limit is 8 pixels per double-word."""
        # Second row: { 8, 8, 4, 2 }
        assert re.search(
            r"\{\s*8,\s*8,\s*4,\s*2\s*\}",
            newport_source
        )


# ---------------------------------------------------------------------------
# Newport Plane Selection [ASSUMPTION]
# ---------------------------------------------------------------------------

class TestNewportPlaneSelect:
    """Plane enable field controls which VRAM buffer is written.

    Our implementation directs planes 4,5 to vram_cidaux and others to
    vram_rgbci. Plane 0 = no write (early return).
    """

    def test_planes_0_no_write(self, newport_source):
        """dm1_planes == 0 must cause early return (no write)."""
        fn = re.search(
            r"newport_logic_pixel\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_logic_pixel function not found"
        body = fn.group(1)
        # case 0 should be followed by return
        assert re.search(r"case\s+0:.*?return;", body, re.DOTALL)

    def test_planes_4_5_use_cidaux(self, newport_source):
        """dm1_planes 4 or 5 must write to vram_cidaux."""
        fn = re.search(
            r"newport_logic_pixel\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_logic_pixel function not found"
        body = fn.group(1)
        # Check that planes 4/5 → cidaux
        assert re.search(r"dm1_planes\s*==\s*4.*vram_cidaux", body, re.DOTALL)

    def test_planes_1_2_use_rgbci(self, newport_source):
        """dm1_planes 1 or 2 must write to vram_rgbci."""
        fn = re.search(
            r"newport_logic_pixel\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_logic_pixel function not found"
        body = fn.group(1)
        # Default path uses vram_rgbci
        assert "vram_rgbci" in body

    def test_vram_bounds_check(self, newport_source):
        """Address must be bounds-checked against VRAM dimensions."""
        fn = re.search(
            r"newport_logic_pixel\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_logic_pixel function not found"
        body = fn.group(1)
        assert re.search(r"s->vram_w\s*\*\s*s->vram_h", body)


# ---------------------------------------------------------------------------
# Newport Window Offset [ASSUMPTION]
# ---------------------------------------------------------------------------

class TestNewportWindowOffset:
    """Window offset arithmetic in output_pixel.

    MAME ref: output_pixel() at newport.cpp:2345-2363.
    wx = x + win_x_off - 0x1000, wy = y + win_y_off - 0x1000.
    """

    def test_window_offset_minus_0x1000(self, newport_source):
        """Window offset must subtract 0x1000 after adding xy_window."""
        fn = re.search(
            r"newport_output_pixel\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_output_pixel function not found"
        body = fn.group(1)
        assert "0x1000" in body

    def test_go_register_offset_0x800(self, newport_source):
        """Go register region starts at offset 0x800."""
        assert re.search(
            r"is_go\s*=.*addr\s*>=\s*REX3_GO_OFFSET",
            newport_source
        )


# ---------------------------------------------------------------------------
# Newport Host Color Tables [CROSS-REF: MAME newport.cpp]
# ---------------------------------------------------------------------------

class TestNewportHostColorTables:
    """Host depth and color mask tables must match MAME values."""

    def test_host_depth_bpp_table(self, newport_source):
        """host_depth_bpp[4] = { 8, 8, 16, 32 }."""
        assert re.search(
            r"host_depth_bpp\[4\]\s*=\s*\{\s*8,\s*8,\s*16,\s*32\s*\}",
            newport_source
        )

    def test_host_color_masks_table(self, newport_source):
        """host_color_masks[4] = { 0xf, 0xff, 0xfff, 0xffffffff }."""
        assert re.search(
            r"host_color_masks\[4\]\s*=\s*\{\s*0xf,\s*0xff,\s*0xfff,\s*0xffffffff\s*\}",
            newport_source
        )


# ---------------------------------------------------------------------------
# Newport Drawmode0 Opcode/Adrmode Constants
# ---------------------------------------------------------------------------

class TestNewportOpcodeConstants:
    """DM0 opcode and address mode constants must be defined."""

    def test_dm0_op_noop_is_0(self, newport_source):
        """DM0_OP_NOOP = 0."""
        assert re.search(r"#define\s+DM0_OP_NOOP\s+0", newport_source)

    def test_dm0_op_read_is_1(self, newport_source):
        """DM0_OP_READ = 1."""
        assert re.search(r"#define\s+DM0_OP_READ\s+1", newport_source)

    def test_dm0_op_draw_is_2(self, newport_source):
        """DM0_OP_DRAW = 2."""
        assert re.search(r"#define\s+DM0_OP_DRAW\s+2", newport_source)

    def test_dm0_op_scr2scr_is_3(self, newport_source):
        """DM0_OP_SCR2SCR = 3."""
        assert re.search(r"#define\s+DM0_OP_SCR2SCR\s+3", newport_source)

    def test_dm0_adr_span_is_0(self, newport_source):
        """DM0_ADR_SPAN = 0."""
        assert re.search(r"#define\s+DM0_ADR_SPAN\s+0", newport_source)

    def test_dm0_adr_block_is_1(self, newport_source):
        """DM0_ADR_BLOCK = 1."""
        assert re.search(r"#define\s+DM0_ADR_BLOCK\s+1", newport_source)

    def test_dm0_adr_iline_is_2(self, newport_source):
        """DM0_ADR_ILINE = 2."""
        assert re.search(r"#define\s+DM0_ADR_ILINE\s+2", newport_source)

    def test_dm0_adr_fline_is_3(self, newport_source):
        """DM0_ADR_FLINE = 3."""
        assert re.search(r"#define\s+DM0_ADR_FLINE\s+3", newport_source)


# ---------------------------------------------------------------------------
# Newport Sign-Magnitude Conversion [CROSS-REF: MAME convert_to_sm()]
# ---------------------------------------------------------------------------

class TestNewportSignMagnitude:
    """Two's complement to sign-magnitude conversion for slope registers."""

    def test_twos_to_sm_function_exists(self, newport_source):
        """newport_twos_to_sm function must exist."""
        assert re.search(
            r"static\s+uint32_t\s+newport_twos_to_sm\s*\(",
            newport_source
        )

    def test_slope_registers_use_sm_conversion(self, newport_source):
        """Slope register writes must use newport_twos_to_sm."""
        # Verify that SLOPERED uses it
        assert re.search(
            r"REX3_SLOPERED.*newport_twos_to_sm",
            newport_source, re.DOTALL
        )


# ---------------------------------------------------------------------------
# Newport Default Color Handling [CROSS-REF: MAME newport.cpp:3276-3305]
# ---------------------------------------------------------------------------

class TestNewportDefaultColor:
    """get_default_color selects color_i or color_vram based on fastclear."""

    def test_default_color_function_exists(self, newport_source):
        """newport_get_default_color function must exist."""
        assert re.search(
            r"static\s+uint32_t\s+newport_get_default_color\s*\(",
            newport_source
        )

    def test_fastclear_uses_color_vram(self, newport_source):
        """When dm1_fastclear is set, use color_vram."""
        fn = re.search(
            r"newport_get_default_color\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_get_default_color not found"
        body = fn.group(1)
        assert "dm1_fastclear" in body
        assert "color_vram" in body

    def test_normal_uses_color_i(self, newport_source):
        """When not fastclear, use color_i."""
        fn = re.search(
            r"newport_get_default_color\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_get_default_color not found"
        body = fn.group(1)
        assert "color_i" in body


# ---------------------------------------------------------------------------
# Newport Reset Defaults [CROSS-REF]
# ---------------------------------------------------------------------------

class TestNewportResetDefaults:
    """Reset state values that affect drawing behavior."""

    def test_write_mask_default_0x00ffffff(self, newport_source):
        """write_mask must default to 0x00ffffff on reset."""
        fn = re.search(
            r"sgi_newport_reset\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "sgi_newport_reset not found"
        body = fn.group(1)
        assert "write_mask = 0x00ffffff" in body

    def test_global_mask_default_0xff(self, newport_source):
        """global_mask must default to 0xff (XL8 Indy)."""
        fn = re.search(
            r"sgi_newport_reset\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "sgi_newport_reset not found"
        body = fn.group(1)
        assert "global_mask = 0xff" in body

    def test_status_resets_to_version_indy(self, newport_source):
        """status must reset to REX3_VERSION_INDY."""
        fn = re.search(
            r"sgi_newport_reset\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "sgi_newport_reset not found"
        body = fn.group(1)
        assert "REX3_VERSION_INDY" in body


# ---------------------------------------------------------------------------
# Newport DOSETUP (DM0 bit 5) [CROSS-REF: MAME newport.cpp:2724-2740]
# ---------------------------------------------------------------------------

class TestNewportDoSetup:
    """do_setup() computes octant from start/end coordinates when DM0 bit 5 set."""

    def test_do_setup_function_exists(self, newport_source):
        """newport_do_setup function must exist."""
        assert re.search(
            r"static\s+void\s+newport_do_setup\s*\(",
            newport_source
        )

    def test_do_setup_called_when_bit5_set(self, newport_source):
        """do_setup must be called in command dispatch when DOSETUP bit is set."""
        fn = re.search(
            r"newport_do_rex3_command\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_do_rex3_command not found"
        body = fn.group(1)
        assert "DM0_DOSETUP" in body
        assert "newport_do_setup" in body

    def test_do_setup_writes_octant(self, newport_source):
        """do_setup must write octant bits into bres_octant_inc1[26:24]."""
        fn = re.search(
            r"newport_do_setup\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_do_setup not found"
        body = fn.group(1)
        assert "bres_octant_inc1" in body
        assert "octant" in body

    def test_dosetup_define_bit5(self, newport_source):
        """DM0_DOSETUP must be defined as (1 << 5)."""
        assert re.search(r"#define\s+DM0_DOSETUP\s+\(1\s*<<\s*5\)", newport_source)


# ---------------------------------------------------------------------------
# Newport SCR2SCR Direction Fix [CROSS-REF: MAME newport.cpp:3530-3533]
# ---------------------------------------------------------------------------

class TestNewportScr2scrDirection:
    """scr2scr must read source at (start_x, start_y) and write to
    (start_x + move, start_y + move). The move offset applies to the
    destination (output_pixel call), not the source read."""

    def test_scr2scr_reads_from_start_coords(self, newport_source):
        """Source read uses start_x with window offset, not move."""
        fn = re.search(
            r"newport_draw_scr2scr\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_draw_scr2scr not found"
        body = fn.group(1)
        # Source should read from start_x + window, NOT start_x + move + window
        assert re.search(r"src_wx\s*=\s*start_x\s*\+", body)

    def test_scr2scr_writes_to_move_offset(self, newport_source):
        """Destination write via output_pixel includes move offset."""
        fn = re.search(
            r"newport_draw_scr2scr\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_draw_scr2scr not found"
        body = fn.group(1)
        assert re.search(r"output_pixel\(s,\s*start_x\s*\+\s*move_x", body)


# ---------------------------------------------------------------------------
# Newport Pixel Word Read [CROSS-REF: MAME newport.cpp:3138-3210]
# ---------------------------------------------------------------------------

class TestNewportPixelWordRead:
    """Pixel word read must pack multiple pixels based on hostdepth."""

    def test_read_one_pixel_function_exists(self, newport_source):
        """newport_read_one_pixel helper must exist."""
        assert re.search(
            r"static\s+uint32_t\s+newport_read_one_pixel\s*\(",
            newport_source
        )

    def test_read_one_pixel_advances_position(self, newport_source):
        """read_one_pixel must advance x_start_int and wrap at x_end."""
        fn = re.search(
            r"newport_read_one_pixel\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_read_one_pixel not found"
        body = fn.group(1)
        assert "x_start_int++" in body
        assert "x_save_int" in body

    def test_pixel_read_packs_by_hostdepth(self, newport_source):
        """do_pixel_read must switch on dm1_hostdepth for packing."""
        fn = re.search(
            r"newport_do_pixel_read\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_do_pixel_read not found"
        body = fn.group(1)
        assert "dm1_hostdepth" in body
        assert "newport_read_one_pixel" in body


# ---------------------------------------------------------------------------
# Newport LENGTH32 (DM0 bit 15) [CROSS-REF: MAME newport.cpp:3355, 3427]
# ---------------------------------------------------------------------------

class TestNewportLength32:
    """LENGTH32 clamps span/block X range to 32 pixels."""

    def test_length32_define(self, newport_source):
        """DM0_LENGTH32 must be defined as (1 << 15)."""
        assert re.search(r"#define\s+DM0_LENGTH32\s+\(1\s*<<\s*15\)", newport_source)

    def test_length32_in_span(self, newport_source):
        """newport_draw_span must check DM0_LENGTH32."""
        fn = re.search(
            r"newport_draw_span\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_draw_span not found"
        body = fn.group(1)
        assert "DM0_LENGTH32" in body

    def test_length32_in_block(self, newport_source):
        """newport_draw_block must check DM0_LENGTH32."""
        fn = re.search(
            r"newport_draw_block\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_draw_block not found"
        body = fn.group(1)
        assert "DM0_LENGTH32" in body


# ---------------------------------------------------------------------------
# Newport SKIPFIRST/SKIPLAST (DM0 bits 10, 11)
# ---------------------------------------------------------------------------

class TestNewportSkipFirstLast:
    """Line drawing must honor skip_first and skip_last bits."""

    def test_skipfirst_define(self, newport_source):
        """DM0_SKIPFIRST must be defined as (1 << 10)."""
        assert re.search(r"#define\s+DM0_SKIPFIRST\s+\(1\s*<<\s*10\)", newport_source)

    def test_skiplast_define(self, newport_source):
        """DM0_SKIPLAST must be defined as (1 << 11)."""
        assert re.search(r"#define\s+DM0_SKIPLAST\s+\(1\s*<<\s*11\)", newport_source)

    def test_skipfirst_in_iline(self, newport_source):
        """newport_draw_iline must decode skip_first."""
        fn = re.search(
            r"newport_draw_iline\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_draw_iline not found"
        body = fn.group(1)
        assert "skip_first" in body

    def test_skiplast_in_fline(self, newport_source):
        """newport_draw_fline must decode skip_last."""
        fn = re.search(
            r"newport_draw_fline\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_draw_fline not found"
        body = fn.group(1)
        assert "skip_last" in body


# ---------------------------------------------------------------------------
# Newport RGB Color from Slope Accumulators [CROSS-REF: MAME get_rgb_color]
# ---------------------------------------------------------------------------

class TestNewportGetRGBColor:
    """get_rgb_color extracts clamped R/G/B from curr_color registers."""

    def test_get_rgb_color_function_exists(self, newport_source):
        """newport_get_rgb_color function must exist."""
        assert re.search(
            r"static\s+uint32_t\s+newport_get_rgb_color\s*\(",
            newport_source
        )

    def test_rgb_color_extracts_9bit_values(self, newport_source):
        """get_rgb_color must extract 9-bit values from bits [19:11]."""
        fn = re.search(
            r"newport_get_rgb_color\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_get_rgb_color not found"
        body = fn.group(1)
        assert ">> 11" in body
        assert "0x1ff" in body

    def test_rgb_color_clamps_negative(self, newport_source):
        """get_rgb_color must clamp values >= 0x180 to 0."""
        fn = re.search(
            r"newport_get_rgb_color\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_get_rgb_color not found"
        body = fn.group(1)
        assert "0x180" in body


# ---------------------------------------------------------------------------
# Newport iterate_shade [CROSS-REF: MAME newport.cpp:3212-3251]
# ---------------------------------------------------------------------------

class TestNewportIterateShade:
    """iterate_shade advances color accumulators by slope values per pixel."""

    def test_iterate_shade_function_exists(self, newport_source):
        """newport_iterate_shade function must exist."""
        assert re.search(
            r"static\s+void\s+newport_iterate_shade\s*\(",
            newport_source
        )

    def test_iterate_shade_updates_curr_colors(self, newport_source):
        """iterate_shade must update curr_color_red/green/blue/alpha."""
        fn = re.search(
            r"newport_iterate_shade\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_iterate_shade not found"
        body = fn.group(1)
        assert "curr_color_red" in body
        assert "curr_color_green" in body
        assert "curr_color_blue" in body
        assert "curr_color_alpha" in body

    def test_iterate_shade_uses_slope(self, newport_source):
        """iterate_shade must read slope_red/green/blue/alpha."""
        fn = re.search(
            r"newport_iterate_shade\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_iterate_shade not found"
        body = fn.group(1)
        assert "slope_red" in body
        assert "slope_green" in body
        assert "slope_blue" in body


# ---------------------------------------------------------------------------
# Newport LR_ABORT (DM0 bit 19) [CROSS-REF: MAME newport.cpp:3377]
# ---------------------------------------------------------------------------

class TestNewportLRAbort:
    """LR_ABORT aborts draw when direction is right-to-left."""

    def test_lr_abort_define(self, newport_source):
        """DM0_LR_ABORT must be defined as (1 << 19)."""
        assert re.search(r"#define\s+DM0_LR_ABORT\s+\(1\s*<<\s*19\)", newport_source)

    def test_lr_abort_in_span(self, newport_source):
        """newport_draw_span must check lr_abort."""
        fn = re.search(
            r"newport_draw_span\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_draw_span not found"
        body = fn.group(1)
        assert "lr_abort" in body

    def test_lr_abort_in_block(self, newport_source):
        """newport_draw_block must check lr_abort."""
        fn = re.search(
            r"newport_draw_block\(.*?\{(.*?)^\}",
            newport_source, re.DOTALL | re.MULTILINE
        )
        assert fn, "newport_draw_block not found"
        body = fn.group(1)
        assert "lr_abort" in body


# ---------------------------------------------------------------------------
# Newport curr_color State [CROSS-REF: MAME newport.h]
# ---------------------------------------------------------------------------

class TestNewportCurrColorState:
    """curr_color accumulator fields must exist in state struct."""

    def test_curr_color_red_in_header(self, newport_header):
        """curr_color_red field must be in SGINewportState."""
        assert "curr_color_red" in newport_header

    def test_curr_color_green_in_header(self, newport_header):
        """curr_color_green field must be in SGINewportState."""
        assert "curr_color_green" in newport_header

    def test_curr_color_blue_in_header(self, newport_header):
        """curr_color_blue field must be in SGINewportState."""
        assert "curr_color_blue" in newport_header

    def test_curr_color_alpha_in_header(self, newport_header):
        """curr_color_alpha field must be in SGINewportState."""
        assert "curr_color_alpha" in newport_header

    def test_colorred_write_sets_curr(self, newport_source):
        """Writing COLORRED must also set curr_color_red."""
        assert re.search(
            r"color_red\s*=.*\n\s*s->curr_color_red\s*=\s*s->color_red",
            newport_source
        )
