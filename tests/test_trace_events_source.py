"""
SGI device trace event and NewView logger source assertions.

Verifies that QEMU trace event declarations exist in the trace-events files,
that source files include trace.h and call the trace functions, and that
the NewView binary logger is properly integrated.

These tests are FAST (source code analysis only, no QEMU boot).
"""

import re


# ============================================================
# MC trace events
# ============================================================

class TestMCTraceEvents:
    """MC memory controller trace event declarations."""

    def test_mc_read_event(self, misc_trace_events):
        """sgi_mc_read trace event is declared."""
        assert "sgi_mc_read(" in misc_trace_events

    def test_mc_write_event(self, misc_trace_events):
        """sgi_mc_write trace event is declared."""
        assert "sgi_mc_write(" in misc_trace_events

    def test_mc_rpss_event(self, misc_trace_events):
        """sgi_mc_rpss trace event is declared."""
        assert "sgi_mc_rpss(" in misc_trace_events

    def test_mc_memcfg_event(self, misc_trace_events):
        """sgi_mc_memcfg trace event is declared with bank/base/size params."""
        assert re.search(r"sgi_mc_memcfg\(.*bank.*base.*size", misc_trace_events)

    def test_mc_dma_event(self, misc_trace_events):
        """sgi_mc_dma trace event is declared."""
        assert "sgi_mc_dma(" in misc_trace_events


class TestMCTraceUsage:
    """MC source uses trace calls."""

    def test_mc_includes_trace_h(self, mc_source):
        """sgi_mc.c includes trace.h."""
        assert '#include "trace.h"' in mc_source

    def test_mc_calls_trace_read(self, mc_source):
        """sgi_mc.c calls trace_sgi_mc_read."""
        assert "trace_sgi_mc_read(" in mc_source

    def test_mc_calls_trace_write(self, mc_source):
        """sgi_mc.c calls trace_sgi_mc_write."""
        assert "trace_sgi_mc_write(" in mc_source

    def test_mc_calls_trace_rpss(self, mc_source):
        """sgi_mc.c calls trace_sgi_mc_rpss."""
        assert "trace_sgi_mc_rpss(" in mc_source

    def test_mc_calls_trace_memcfg(self, mc_source):
        """sgi_mc.c calls trace_sgi_mc_memcfg."""
        assert "trace_sgi_mc_memcfg(" in mc_source

    def test_mc_calls_trace_dma(self, mc_source):
        """sgi_mc.c calls trace_sgi_mc_dma."""
        assert "trace_sgi_mc_dma(" in mc_source


# ============================================================
# HPC3 trace events
# ============================================================

class TestHPC3TraceEvents:
    """HPC3 peripheral controller trace event declarations."""

    def test_hpc3_read_event(self, misc_trace_events):
        """sgi_hpc3_read trace event is declared."""
        assert "sgi_hpc3_read(" in misc_trace_events

    def test_hpc3_write_event(self, misc_trace_events):
        """sgi_hpc3_write trace event is declared."""
        assert "sgi_hpc3_write(" in misc_trace_events

    def test_hpc3_scsi_dma_event(self, misc_trace_events):
        """sgi_hpc3_scsi_dma trace event is declared."""
        assert "sgi_hpc3_scsi_dma(" in misc_trace_events

    def test_hpc3_scsi_dma_chain_event(self, misc_trace_events):
        """sgi_hpc3_scsi_dma_chain trace event is declared."""
        assert "sgi_hpc3_scsi_dma_chain(" in misc_trace_events

    def test_hpc3_scsi_irq_event(self, misc_trace_events):
        """sgi_hpc3_scsi_irq trace event is declared."""
        assert "sgi_hpc3_scsi_irq(" in misc_trace_events

    def test_hpc3_int3_event(self, misc_trace_events):
        """sgi_hpc3_int3 trace event is declared."""
        assert "sgi_hpc3_int3(" in misc_trace_events

    def test_hpc3_int3_map_event(self, misc_trace_events):
        """sgi_hpc3_int3_map trace event is declared."""
        assert "sgi_hpc3_int3_map(" in misc_trace_events

    def test_hpc3_pit_event(self, misc_trace_events):
        """sgi_hpc3_pit trace event is declared."""
        assert "sgi_hpc3_pit(" in misc_trace_events

    def test_hpc3_scc_tx_event(self, misc_trace_events):
        """sgi_hpc3_scc_tx trace event is declared."""
        assert "sgi_hpc3_scc_tx(" in misc_trace_events

    def test_hpc3_scc_rx_event(self, misc_trace_events):
        """sgi_hpc3_scc_rx trace event is declared."""
        assert "sgi_hpc3_scc_rx(" in misc_trace_events

    def test_hpc3_nvram_event(self, misc_trace_events):
        """sgi_hpc3_nvram trace event is declared."""
        assert "sgi_hpc3_nvram(" in misc_trace_events


class TestHPC3TraceUsage:
    """HPC3 source uses trace calls."""

    def test_hpc3_includes_trace_h(self, hpc3_source):
        """sgi_hpc3.c includes trace.h."""
        assert '#include "trace.h"' in hpc3_source

    def test_hpc3_calls_trace_read(self, hpc3_source):
        """sgi_hpc3.c calls trace_sgi_hpc3_read."""
        assert "trace_sgi_hpc3_read(" in hpc3_source

    def test_hpc3_calls_trace_write(self, hpc3_source):
        """sgi_hpc3.c calls trace_sgi_hpc3_write."""
        assert "trace_sgi_hpc3_write(" in hpc3_source

    def test_hpc3_calls_trace_scsi_dma(self, hpc3_source):
        """sgi_hpc3.c calls trace_sgi_hpc3_scsi_dma."""
        assert "trace_sgi_hpc3_scsi_dma(" in hpc3_source

    def test_hpc3_calls_trace_scsi_dma_chain(self, hpc3_source):
        """sgi_hpc3.c calls trace_sgi_hpc3_scsi_dma_chain."""
        assert "trace_sgi_hpc3_scsi_dma_chain(" in hpc3_source

    def test_hpc3_calls_trace_int3(self, hpc3_source):
        """sgi_hpc3.c calls trace_sgi_hpc3_int3."""
        assert "trace_sgi_hpc3_int3(" in hpc3_source

    def test_hpc3_calls_trace_pit(self, hpc3_source):
        """sgi_hpc3.c calls trace_sgi_hpc3_pit."""
        assert "trace_sgi_hpc3_pit(" in hpc3_source

    def test_hpc3_calls_trace_scc_tx(self, hpc3_source):
        """sgi_hpc3.c calls trace_sgi_hpc3_scc_tx."""
        assert "trace_sgi_hpc3_scc_tx(" in hpc3_source

    def test_hpc3_calls_trace_scc_rx(self, hpc3_source):
        """sgi_hpc3.c calls trace_sgi_hpc3_scc_rx."""
        assert "trace_sgi_hpc3_scc_rx(" in hpc3_source

    def test_hpc3_calls_trace_nvram(self, hpc3_source):
        """sgi_hpc3.c calls trace_sgi_hpc3_nvram."""
        assert "trace_sgi_hpc3_nvram(" in hpc3_source


# ============================================================
# Newport trace events
# ============================================================

class TestNewportTraceEvents:
    """Newport graphics trace event declarations."""

    def test_rex3_read_event(self, display_trace_events):
        """sgi_newport_rex3_read trace event is declared."""
        assert "sgi_newport_rex3_read(" in display_trace_events

    def test_rex3_write_event(self, display_trace_events):
        """sgi_newport_rex3_write trace event is declared."""
        assert "sgi_newport_rex3_write(" in display_trace_events

    def test_rex3_cmd_event(self, display_trace_events):
        """sgi_newport_rex3_cmd trace event is declared with drawmode params."""
        assert re.search(r"sgi_newport_rex3_cmd\(.*drawmode", display_trace_events)

    def test_dcb_write_event(self, display_trace_events):
        """sgi_newport_dcb_write trace event is declared."""
        assert "sgi_newport_dcb_write(" in display_trace_events

    def test_vc2_event(self, display_trace_events):
        """sgi_newport_vc2 trace event is declared."""
        assert "sgi_newport_vc2(" in display_trace_events

    def test_cmap_event(self, display_trace_events):
        """sgi_newport_cmap trace event is declared."""
        assert "sgi_newport_cmap(" in display_trace_events

    def test_xmap_event(self, display_trace_events):
        """sgi_newport_xmap trace event is declared."""
        assert "sgi_newport_xmap(" in display_trace_events

    def test_draw_block_event(self, display_trace_events):
        """sgi_newport_draw_block trace event is declared."""
        assert "sgi_newport_draw_block(" in display_trace_events

    def test_draw_line_event(self, display_trace_events):
        """sgi_newport_draw_line trace event is declared."""
        assert "sgi_newport_draw_line(" in display_trace_events


class TestNewportTraceUsage:
    """Newport source uses trace calls."""

    def test_newport_includes_trace_h(self, newport_source):
        """sgi_newport.c includes trace.h."""
        assert '#include "trace.h"' in newport_source

    def test_newport_calls_trace_read(self, newport_source):
        """sgi_newport.c calls trace_sgi_newport_rex3_read."""
        assert "trace_sgi_newport_rex3_read(" in newport_source

    def test_newport_calls_trace_write(self, newport_source):
        """sgi_newport.c calls trace_sgi_newport_rex3_write."""
        assert "trace_sgi_newport_rex3_write(" in newport_source

    def test_newport_calls_trace_cmd(self, newport_source):
        """sgi_newport.c calls trace_sgi_newport_rex3_cmd."""
        assert "trace_sgi_newport_rex3_cmd(" in newport_source

    def test_newport_calls_trace_dcb_write(self, newport_source):
        """sgi_newport.c calls trace_sgi_newport_dcb_write."""
        assert "trace_sgi_newport_dcb_write(" in newport_source

    def test_newport_calls_trace_draw_block(self, newport_source):
        """sgi_newport.c calls trace_sgi_newport_draw_block."""
        assert "trace_sgi_newport_draw_block(" in newport_source

    def test_newport_calls_trace_draw_line(self, newport_source):
        """sgi_newport.c calls trace_sgi_newport_draw_line."""
        assert "trace_sgi_newport_draw_line(" in newport_source

    def test_newport_calls_trace_vc2(self, newport_source):
        """sgi_newport.c calls trace_sgi_newport_vc2."""
        assert "trace_sgi_newport_vc2(" in newport_source

    def test_newport_calls_trace_cmap(self, newport_source):
        """sgi_newport.c calls trace_sgi_newport_cmap."""
        assert "trace_sgi_newport_cmap(" in newport_source

    def test_newport_calls_trace_xmap(self, newport_source):
        """sgi_newport.c calls trace_sgi_newport_xmap."""
        assert "trace_sgi_newport_xmap(" in newport_source


# ============================================================
# NewView binary logger
# ============================================================

class TestNewViewLogger:
    """NewView binary register access logger."""

    def test_newview_path_field(self, newport_header):
        """SGINewportState has newview_log_path field."""
        assert "newview_log_path" in newport_header

    def test_newview_file_field(self, newport_header):
        """SGINewportState has newview_log_file field."""
        assert "newview_log_file" in newport_header

    def test_newview_property(self, newport_source):
        """Newport has newview-log property via DEFINE_PROP_STRING."""
        assert re.search(
            r'DEFINE_PROP_STRING\(\s*"newview-log"',
            newport_source
        )

    def test_newview_log_function(self, newport_source):
        """newport_newview_log helper function exists."""
        assert "newport_newview_log(" in newport_source

    def test_newview_fopen(self, newport_source):
        """NewView file is opened in realize."""
        assert re.search(r'fopen\(s->newview_log_path', newport_source)

    def test_newview_fclose(self, newport_source):
        """NewView file is closed in finalize."""
        assert re.search(r'fclose\(s->newview_log_file', newport_source)

    def test_newview_write_calls(self, newport_source):
        """NewView log is called in read/write paths."""
        calls = [m.start() for m in re.finditer(r'newport_newview_log\(', newport_source)]
        # At least 3: read path, write path, frame marker
        assert len(calls) >= 3, f"Expected >= 3 newview_log calls, found {len(calls)}"

    def test_newview_frame_marker(self, newport_source):
        """Frame boundary marker uses 0x80000000."""
        assert re.search(r'newport_newview_log\(s,\s*0x80000000', newport_source)

    def test_newview_read_bit30(self, newport_source):
        """Read path sets bit 30 (0x40000000) in offset."""
        assert re.search(r'0x40000000', newport_source)

    def test_newview_record_size(self, newport_source):
        """NewView record is 5 uint32_t = 20 bytes."""
        assert re.search(r'uint32_t\s+record\[5\]', newport_source)

    def test_newview_instance_finalize(self, newport_source):
        """TypeInfo uses instance_finalize for cleanup."""
        assert "instance_finalize" in newport_source


# ============================================================
# Event naming convention
# ============================================================

class TestTraceEventNaming:
    """Trace events follow sgi_ prefix convention for runtime filtering."""

    def test_mc_events_prefixed(self, misc_trace_events):
        """All MC events start with sgi_mc_."""
        mc_lines = [l for l in misc_trace_events.splitlines()
                    if l.startswith("sgi_mc_")]
        assert len(mc_lines) >= 5, f"Expected >= 5 MC events, found {len(mc_lines)}"

    def test_hpc3_events_prefixed(self, misc_trace_events):
        """All HPC3 events start with sgi_hpc3_."""
        hpc3_lines = [l for l in misc_trace_events.splitlines()
                      if l.startswith("sgi_hpc3_")]
        assert len(hpc3_lines) >= 10, f"Expected >= 10 HPC3 events, found {len(hpc3_lines)}"

    def test_newport_events_prefixed(self, display_trace_events):
        """All Newport events start with sgi_newport_."""
        newport_lines = [l for l in display_trace_events.splitlines()
                         if l.startswith("sgi_newport_")]
        assert len(newport_lines) >= 9, f"Expected >= 9 Newport events, found {len(newport_lines)}"
