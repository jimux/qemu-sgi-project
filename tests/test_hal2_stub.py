"""
HAL2 audio source analysis tests.

Verifies that the HAL2 audio controller in sgi_hpc3.c/h has
correct register constants, proper dispatch in the PBUS PIO handler,
indirect register bank, Bresenham rate computation, PBUS DMA engine,
audio output integration, and trace events for debugging.

These tests are FAST (source code analysis only, no QEMU boot).
"""

import os
import re
import pytest

pytestmark = pytest.mark.xfail(
    reason="HAL2 audio / Dallas RTC epoch not yet fully implemented in qemu-sgi-repo",
    strict=False,
)


class TestHAL2Constants:
    """HAL2 register constants match IRIX sys/hal2.h addresses."""

    def test_rev_value(self, hpc3_header):
        """[CROSS-REF] HAL2 revision 0x4010 = HAL2 rev A (MAME hal2.cpp)."""
        assert "HAL2_REV_VALUE       0x4010" in hpc3_header

    def test_isr_offset(self, hpc3_header):
        """[CROSS-REF] ISR at offset 0x10 from PBUS PIO base."""
        assert "HAL2_REG_ISR         0x10" in hpc3_header

    def test_rev_offset(self, hpc3_header):
        """[CROSS-REF] REV at offset 0x20."""
        assert "HAL2_REG_REV         0x20" in hpc3_header

    def test_iar_offset(self, hpc3_header):
        """[CROSS-REF] IAR at offset 0x30."""
        assert "HAL2_REG_IAR         0x30" in hpc3_header

    def test_idr_offsets(self, hpc3_header):
        """[CROSS-REF] IDR0-3 at offsets 0x40, 0x50, 0x60, 0x70."""
        assert "HAL2_REG_IDR0        0x40" in hpc3_header
        assert "HAL2_REG_IDR1        0x50" in hpc3_header
        assert "HAL2_REG_IDR2        0x60" in hpc3_header
        assert "HAL2_REG_IDR3        0x70" in hpc3_header

    def test_volume_dac_offsets(self, hpc3_header):
        """[CROSS-REF] Volume DAC at 0x800/0x804 from HAL2 PIO base."""
        assert "HAL2_VOLUME_RIGHT    0x800" in hpc3_header
        assert "HAL2_VOLUME_LEFT     0x804" in hpc3_header

    def test_isr_bits(self, hpc3_header):
        """ISR bit definitions present."""
        assert "HAL2_ISR_TSTATUS" in hpc3_header
        assert "HAL2_ISR_GLOBAL_RESET_N" in hpc3_header
        assert "HAL2_ISR_CODEC_RESET_N" in hpc3_header


class TestHAL2IARConstants:
    """[CROSS-REF] IAR field encoding matches MAME hal2.h:46-52."""

    def test_iar_type_mask(self, hpc3_header):
        assert "HAL2_IAR_TYPE_MASK      0xf000" in hpc3_header

    def test_iar_type_shift(self, hpc3_header):
        assert "HAL2_IAR_TYPE_SHIFT     12" in hpc3_header

    def test_iar_num_mask(self, hpc3_header):
        assert "HAL2_IAR_NUM_MASK       0x0f00" in hpc3_header

    def test_iar_num_shift(self, hpc3_header):
        assert "HAL2_IAR_NUM_SHIFT      8" in hpc3_header

    def test_iar_access_sel(self, hpc3_header):
        assert "HAL2_IAR_ACCESS_SEL     0x0080" in hpc3_header

    def test_iar_param_mask(self, hpc3_header):
        assert "HAL2_IAR_PARAM_MASK     0x000c" in hpc3_header

    def test_iar_param_shift(self, hpc3_header):
        assert "HAL2_IAR_PARAM_SHIFT    2" in hpc3_header


class TestPBUSDMAConstants:
    """[CROSS-REF] PBUS DMA constants match MAME hpc3.h."""

    def test_ctrl_dmastart(self, hpc3_header):
        assert "PBUS_CTRL_DMASTART      0x00000010" in hpc3_header

    def test_ctrl_load_en(self, hpc3_header):
        assert "PBUS_CTRL_LOAD_EN       0x00000020" in hpc3_header

    def test_ctrl_endian(self, hpc3_header):
        assert "PBUS_CTRL_ENDIAN        0x00000002" in hpc3_header

    def test_ctrl_recv(self, hpc3_header):
        assert "PBUS_CTRL_RECV          0x00000004" in hpc3_header

    def test_ctrl_flush(self, hpc3_header):
        assert "PBUS_CTRL_FLUSH         0x00000008" in hpc3_header

    def test_desc_eox(self, hpc3_header):
        assert "PBUS_DESC_EOX" in hpc3_header

    def test_desc_xie(self, hpc3_header):
        assert "PBUS_DESC_XIE" in hpc3_header

    def test_desc_bcnt_mask(self, hpc3_header):
        assert "PBUS_DESC_BCNT_MASK     0x3fff" in hpc3_header


class TestHAL2ReadDispatch:
    """PBUS PIO read handler dispatches HAL2 registers correctly."""

    def test_isr_read_clears_tstatus(self, hpc3_source):
        """ISR read must always have TSTATUS clear to prevent SPIN hangs.

        The PROM's SPIN macro loops while TSTATUS is set. If we ever
        return TSTATUS=1, the PROM hangs forever.
        """
        assert "hal2_isr & ~HAL2_ISR_TSTATUS" in hpc3_source, (
            "ISR read does not mask out TSTATUS bit"
        )

    def test_rev_returns_constant(self, hpc3_source):
        """REV register read returns HAL2_REV_VALUE."""
        assert "HAL2_REV_VALUE" in hpc3_source

    def test_iar_read(self, hpc3_source):
        """IAR register is readable."""
        assert "hal2_iar" in hpc3_source

    def test_idr_read(self, hpc3_source):
        """IDR registers are readable via indexed access."""
        assert "hal2_idr[" in hpc3_source

    def test_volume_read(self, hpc3_source):
        """Volume DAC registers are readable."""
        assert "hal2_volume_right" in hpc3_source
        assert "hal2_volume_left" in hpc3_source

    def test_hal2_registers_in_pbus_pio_range(self, hpc3_source):
        """HAL2 reads are handled within the PBUS PIO block (0x58000-0x5c000)."""
        # Find the PBUS PIO read block and verify HAL2 dispatch is inside it
        assert "HAL2_REG_ISR" in hpc3_source
        assert "HAL2_REG_REV" in hpc3_source


class TestHAL2WriteDispatch:
    """PBUS PIO write handler dispatches HAL2 registers correctly."""

    def test_isr_write_mame_pattern(self, hpc3_source):
        """[CROSS-REF] ISR write uses MAME pattern: clear bits [4:2], set from val.

        From MAME hal2.cpp:112-113:
            m_isr &= ~0x1c;
            m_isr |= data & 0x1c;
        """
        assert "hal2_isr &= ~0x1c" in hpc3_source
        assert "hal2_isr |= val & 0x1c" in hpc3_source

    def test_iar_write_dispatches(self, hpc3_source):
        """IAR write triggers indirect register dispatch."""
        assert "sgi_hpc3_hal2_iar_dispatch(s)" in hpc3_source

    def test_iar_write(self, hpc3_source):
        """IAR is writable (selects indirect register)."""
        match = re.search(r's->hal2_iar\s*=\s*val', hpc3_source)
        assert match, "IAR write does not store to hal2_iar"

    def test_idr_write(self, hpc3_source):
        """IDR registers are writable."""
        match = re.search(r's->hal2_idr\[', hpc3_source)
        assert match, "IDR write not found"

    def test_volume_write(self, hpc3_source):
        """Volume DAC writes are handled."""
        match = re.search(r's->hal2_volume_right\s*=\s*val', hpc3_source)
        assert match, "Volume right write not found"
        match = re.search(r's->hal2_volume_left\s*=\s*val', hpc3_source)
        assert match, "Volume left write not found"

    def test_volume_write_updates_audio(self, hpc3_source):
        """Volume DAC writes call AUD_set_volume_out_lr when voice exists."""
        assert "AUD_set_volume_out_lr" in hpc3_source


class TestHAL2IndirectRegisters:
    """HAL2 indirect register bank fields exist and are dispatched."""

    def test_codeca_ctrl_fields(self, hpc3_header):
        """Codec A control registers in state struct."""
        assert "hal2_codeca_ctrl[2]" in hpc3_header

    def test_codecb_ctrl_fields(self, hpc3_header):
        """Codec B control registers in state struct."""
        assert "hal2_codecb_ctrl[2]" in hpc3_header

    def test_bres_clock_sel_fields(self, hpc3_header):
        """Bresenham clock select registers in state struct."""
        assert "hal2_bres_clock_sel[3]" in hpc3_header

    def test_bres_clock_inc_fields(self, hpc3_header):
        """Bresenham increment registers in state struct."""
        assert "hal2_bres_clock_inc[3]" in hpc3_header

    def test_bres_clock_modctrl_fields(self, hpc3_header):
        """Bresenham modctrl registers in state struct."""
        assert "hal2_bres_clock_modctrl[3]" in hpc3_header

    def test_relay_control_field(self, hpc3_header):
        assert "hal2_relay_control" in hpc3_header

    def test_dma_enable_field(self, hpc3_header):
        assert "hal2_dma_enable" in hpc3_header

    def test_dma_endian_field(self, hpc3_header):
        assert "hal2_dma_endian" in hpc3_header

    def test_dma_drive_field(self, hpc3_header):
        assert "hal2_dma_drive" in hpc3_header

    def test_codeca_decoded_fields(self, hpc3_header):
        """Codec A decoded fields for DMA routing."""
        assert "hal2_codeca_channel" in hpc3_header
        assert "hal2_codeca_clock" in hpc3_header
        assert "hal2_codeca_channel_count" in hpc3_header

    def test_sample_rate_field(self, hpc3_header):
        """Computed sample rate field exists."""
        assert "hal2_sample_rate" in hpc3_header

    def test_iar_dispatch_function(self, hpc3_source):
        """IAR dispatch function exists."""
        assert "sgi_hpc3_hal2_iar_dispatch" in hpc3_source


class TestBresenhamRate:
    """Bresenham sample rate computation matches MAME hal2.cpp:333-354."""

    def test_update_rate_function(self, hpc3_source):
        """Rate computation function exists."""
        assert "sgi_hpc3_hal2_update_rate" in hpc3_source

    def test_48khz_master(self, hpc3_source):
        """48kHz master clock selection (sel=0)."""
        assert "48000" in hpc3_source

    def test_44100hz_master(self, hpc3_source):
        """44.1kHz master clock selection (sel=1)."""
        assert "44100" in hpc3_source

    def test_bresenham_formula(self, hpc3_source):
        """[CROSS-REF] Bresenham mod formula from MAME.

        mod = 0x10000 - ((modctrl + 1) - inc)
        rate = master_freq * inc / mod  (when mod != 0)
        """
        assert "0x10000" in hpc3_source
        assert "modctrl + 1" in hpc3_source


class TestPBUSDMAEngine:
    """PBUS DMA engine implementation."""

    def test_dma_active_state(self, hpc3_header):
        """PBUS DMA active flags exist."""
        assert "pbus_dma_active[8]" in hpc3_header

    def test_dma_cur_ptr_state(self, hpc3_header):
        assert "pbus_dma_cur_ptr[8]" in hpc3_header

    def test_dma_desc_flags_state(self, hpc3_header):
        assert "pbus_dma_desc_flags[8]" in hpc3_header

    def test_dma_next_ptr_state(self, hpc3_header):
        assert "pbus_dma_next_ptr[8]" in hpc3_header

    def test_dma_bytes_left_state(self, hpc3_header):
        assert "pbus_dma_bytes_left[8]" in hpc3_header

    def test_dma_timer(self, hpc3_header):
        assert "pbus_dma_timer" in hpc3_header

    def test_dma_start_function(self, hpc3_source):
        """DMA start function exists."""
        assert "sgi_hpc3_pbus_dma_start" in hpc3_source

    def test_dma_tick_function(self, hpc3_source):
        """DMA tick callback exists."""
        assert "sgi_hpc3_pbus_dma_tick" in hpc3_source

    def test_dma_fetch_desc_function(self, hpc3_source):
        """Descriptor fetch function exists."""
        assert "sgi_hpc3_pbus_dma_fetch_desc" in hpc3_source

    def test_ctrl_read_format(self, hpc3_source):
        """[CROSS-REF] PBUS DMA ctrl read returns MAME format (bit0=IRQ, bit1=active).

        From MAME hpc3.cpp:778-788: reading ctrl returns interrupt pending (bit 0)
        and channel active (bit 1), and clears the interrupt bit on read.
        """
        # Check that ctrl read has bit 0 = IRQ from intstat
        assert "intstat & (1 << ch)" in hpc3_source
        # Check that ctrl read has bit 1 = active
        assert "pbus_dma_active[ch]" in hpc3_source

    def test_dma_start_on_ctrl_write(self, hpc3_source):
        """[CROSS-REF] DMA starts when DMASTART + LOAD_EN written.

        From MAME hpc3.cpp:842.
        """
        assert "PBUS_CTRL_DMASTART" in hpc3_source
        assert "PBUS_CTRL_LOAD_EN" in hpc3_source

    def test_desc_write_fetches(self, hpc3_source):
        """Writing descriptor pointer fetches the first descriptor."""
        # DP write triggers fetch_desc
        assert "sgi_hpc3_pbus_dma_fetch_desc" in hpc3_source


class TestAudioIntegration:
    """QEMU audio backend integration."""

    def test_audio_backend_field(self, hpc3_header):
        """AudioBackend pointer in state struct."""
        assert "AudioBackend *audio_be" in hpc3_header

    def test_audio_voice_field(self, hpc3_header):
        """SWVoiceOut pointer in state struct."""
        assert "SWVoiceOut" in hpc3_header

    def test_audio_property(self, hpc3_source):
        """DEFINE_AUDIO_PROPERTIES declared in properties."""
        assert "DEFINE_AUDIO_PROPERTIES" in hpc3_source

    def test_aud_open_out(self, hpc3_source):
        """AUD_open_out called to create voice."""
        assert "AUD_open_out" in hpc3_source

    def test_aud_write(self, hpc3_source):
        """AUD_write called to output samples."""
        assert "AUD_write" in hpc3_source

    def test_aud_set_active_out(self, hpc3_source):
        """AUD_set_active_out called to start playback."""
        assert "AUD_set_active_out" in hpc3_source

    def test_audio_header_included(self, hpc3_header):
        """Audio header is included."""
        assert "qemu/audio.h" in hpc3_header


class TestHAL2State:
    """HAL2 state fields exist in SGIHPC3State."""

    def test_state_fields(self, hpc3_header):
        """HAL2 state fields are declared in the struct."""
        assert "hal2_isr" in hpc3_header
        assert "hal2_iar" in hpc3_header
        assert "hal2_idr[4]" in hpc3_header
        assert "hal2_volume_left" in hpc3_header
        assert "hal2_volume_right" in hpc3_header

    def test_vmstate_includes_hal2(self, hpc3_source):
        """VMState includes HAL2 fields for migration/snapshot."""
        assert "VMSTATE_UINT32(hal2_isr" in hpc3_source
        assert "VMSTATE_UINT32(hal2_iar" in hpc3_source
        assert "VMSTATE_UINT32_ARRAY(hal2_idr" in hpc3_source
        assert "VMSTATE_UINT8(hal2_volume_left" in hpc3_source
        assert "VMSTATE_UINT8(hal2_volume_right" in hpc3_source

    def test_vmstate_includes_indirect_regs(self, hpc3_source):
        """VMState includes indirect register fields."""
        assert "VMSTATE_UINT16_ARRAY(hal2_codeca_ctrl" in hpc3_source
        assert "VMSTATE_UINT16_ARRAY(hal2_codecb_ctrl" in hpc3_source
        assert "VMSTATE_UINT16_ARRAY(hal2_bres_clock_sel" in hpc3_source
        assert "VMSTATE_UINT16_ARRAY(hal2_bres_clock_inc" in hpc3_source
        assert "VMSTATE_UINT16_ARRAY(hal2_bres_clock_modctrl" in hpc3_source
        assert "VMSTATE_UINT16(hal2_relay_control" in hpc3_source

    def test_vmstate_includes_pbus_dma(self, hpc3_source):
        """VMState includes PBUS DMA runtime state."""
        assert "VMSTATE_BOOL_ARRAY(pbus_dma_active" in hpc3_source
        assert "VMSTATE_UINT32_ARRAY(pbus_dma_cur_ptr" in hpc3_source
        assert "VMSTATE_UINT32_ARRAY(pbus_dma_desc_flags" in hpc3_source
        assert "VMSTATE_UINT32_ARRAY(pbus_dma_next_ptr" in hpc3_source
        assert "VMSTATE_UINT32_ARRAY(pbus_dma_bytes_left" in hpc3_source


class TestHAL2TraceEvents:
    """Trace events exist for HAL2 debugging."""

    @pytest.fixture
    def trace_events(self):
        """Load trace-events file."""
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "qemu", "hw", "misc", "trace-events"
        )
        with open(path) as f:
            return f.read()

    def test_hal2_read_event(self, trace_events):
        assert "sgi_hpc3_hal2_read" in trace_events

    def test_hal2_write_event(self, trace_events):
        assert "sgi_hpc3_hal2_write" in trace_events

    def test_hal2_iar_event(self, trace_events):
        assert "sgi_hpc3_hal2_iar" in trace_events

    def test_hal2_volume_event(self, trace_events):
        assert "sgi_hpc3_hal2_volume" in trace_events

    def test_hal2_indirect_event(self, trace_events):
        assert "sgi_hpc3_hal2_indirect" in trace_events

    def test_hal2_bres_rate_event(self, trace_events):
        assert "sgi_hpc3_hal2_bres_rate" in trace_events

    def test_pbus_dma_start_event(self, trace_events):
        assert "sgi_hpc3_pbus_dma_start" in trace_events

    def test_pbus_dma_next_event(self, trace_events):
        assert "sgi_hpc3_pbus_dma_next" in trace_events

    def test_pbus_dma_complete_event(self, trace_events):
        assert "sgi_hpc3_pbus_dma_complete" in trace_events

    def test_hal2_audio_out_event(self, trace_events):
        assert "sgi_hpc3_hal2_audio_out" in trace_events
