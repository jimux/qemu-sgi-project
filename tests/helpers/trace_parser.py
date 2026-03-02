"""
Parsers for QEMU trace log files written during SGI Indy boot.

These trace logs are written by instrumentation in the QEMU source:
  - /tmp/cp0_timer_trace.log: CP0 Count/Compare timer fires and writes
  - /tmp/map_mask_raw.log: INT3 MAP_MASK0/MAP_MASK1 register writes
  - /tmp/scc_tx_timer_trace.log: SCC TX timer fires and serial writes
  - /tmp/scc_wr1_trace.log: SCC WR1 (interrupt enable) register writes
"""

import os
import re
from collections import namedtuple

TimerFire = namedtuple("TimerFire", [
    "sequence", "count", "compare", "irq", "status", "ie", "ip7_en"
])

CompareWrite = namedtuple("CompareWrite", [
    "sequence", "value", "count", "dc", "cause", "status"
])

MapMaskEntry = namedtuple("MapMaskEntry", [
    "addr", "base_addr", "value", "mask0_before", "mask1_before",
    "serial_write_count"
])

WR1Entry = namedtuple("WR1Entry", [
    "port", "value", "tx_int", "rx_int", "serial_write_count"
])


class CP0TimerTrace:
    """Parse /tmp/cp0_timer_trace.log for timer fire and compare write events."""

    # CP0_TIMER_FIRE: #1 Count=0x... Compare=0x... irq[7] Status=0x... (IE=1 IP7_en=1)
    FIRE_RE = re.compile(
        r"CP0_TIMER_FIRE: #(\d+) Count=0x([0-9a-f]+) Compare=0x([0-9a-f]+) "
        r"irq\[(\d+)\] Status=0x([0-9a-f]+) \(IE=(\d+) IP7_en=(\d+)\)"
    )

    # CP0_COMPARE_WRITE: #1 value=0x... Count=0x... DC=0 Cause=0x... Status=0x...
    WRITE_RE = re.compile(
        r"CP0_COMPARE_WRITE: #(\d+) value=0x([0-9a-f]+) Count=0x([0-9a-f]+) "
        r"DC=(\d+) Cause=0x([0-9a-f]+) Status=0x([0-9a-f]+)"
    )

    def __init__(self, path="/tmp/cp0_timer_trace.log"):
        self.fires = []
        self.writes = []
        self.path = path
        if os.path.exists(path):
            self._parse(path)

    def _parse(self, path):
        with open(path) as f:
            for line in f:
                m = self.FIRE_RE.search(line)
                if m:
                    self.fires.append(TimerFire(
                        sequence=int(m.group(1)),
                        count=int(m.group(2), 16),
                        compare=int(m.group(3), 16),
                        irq=int(m.group(4)),
                        status=int(m.group(5), 16),
                        ie=int(m.group(6)),
                        ip7_en=int(m.group(7)),
                    ))
                    continue
                m = self.WRITE_RE.search(line)
                if m:
                    self.writes.append(CompareWrite(
                        sequence=int(m.group(1)),
                        value=int(m.group(2), 16),
                        count=int(m.group(3), 16),
                        dc=int(m.group(4)),
                        cause=int(m.group(5), 16),
                        status=int(m.group(6), 16),
                    ))

    @property
    def fire_count(self):
        """Number of timer fire events recorded."""
        if not self.fires:
            return 0
        # The sequence number in the last fire is the total count
        return self.fires[-1].sequence

    @property
    def write_count(self):
        """Number of compare write events recorded."""
        if not self.writes:
            return 0
        return self.writes[-1].sequence

    def all_ie_enabled(self):
        """Check that all timer fires had IE (Interrupt Enable) set."""
        return all(f.ie == 1 for f in self.fires)

    def all_ip7_enabled(self):
        """Check that all timer fires had IP7 enabled in Status."""
        return all(f.ip7_en == 1 for f in self.fires)

    def all_on_irq7(self):
        """Check that all timer fires were on irq[7]."""
        return all(f.irq == 7 for f in self.fires)

    def average_interval_ticks(self):
        """Compute average interval between timer fires in Count ticks."""
        if len(self.fires) < 2:
            return 0
        intervals = []
        for i in range(1, len(self.fires)):
            # Handle 32-bit wrap
            delta = (self.fires[i].count - self.fires[i - 1].count) & 0xFFFFFFFF
            intervals.append(delta)
        return sum(intervals) / len(intervals)


class MapMaskTrace:
    """Parse /tmp/map_mask_raw.log for MAP_MASK register writes."""

    # MAP_MASK raw: addr=0x... base=0x... val=0x... ...
    ENTRY_RE = re.compile(
        r"MAP_MASK raw: addr=0x([0-9a-f]+) base=0x([0-9a-f]+) "
        r"val=0x([0-9a-f]+).*?"
        r"mask0=0x([0-9a-f]+).*?mask1=0x([0-9a-f]+).*?"
        r"wc=(\d+)"
    )

    # Also match simpler MAP_MASK0 = 0x... lines
    MASK0_RE = re.compile(
        r"MAP_MASK0 = 0x([0-9a-f]+) \(DUART=0x20 bit=(\d+)\)"
    )

    DUART_BIT = 0x20

    def __init__(self, path="/tmp/map_mask_raw.log"):
        self.entries = []
        self.mask0_values = []
        self.path = path
        if os.path.exists(path):
            self._parse(path)

    def _parse(self, path):
        with open(path) as f:
            for line in f:
                m = self.ENTRY_RE.search(line)
                if m:
                    self.entries.append(MapMaskEntry(
                        addr=int(m.group(1), 16),
                        base_addr=int(m.group(2), 16),
                        value=int(m.group(3), 16),
                        mask0_before=int(m.group(4), 16),
                        mask1_before=int(m.group(5), 16),
                        serial_write_count=int(m.group(6)),
                    ))
                m = self.MASK0_RE.search(line)
                if m:
                    self.mask0_values.append(int(m.group(1), 16))

    def duart_ever_cleared(self):
        """Check if DUART bit (0x20) was ever cleared from map_mask0.

        Returns True if any MAP_MASK0 write had the DUART bit cleared
        AFTER it was previously set, indicating the threaded interrupt
        handler masked it.
        """
        was_set = False
        for val in self.mask0_values:
            if val & self.DUART_BIT:
                was_set = True
            elif was_set:
                # DUART was set, now it's cleared
                return True
        return False

    def final_mask0_value(self):
        """Get the last written MAP_MASK0 value."""
        if self.mask0_values:
            return self.mask0_values[-1]
        return None


class SCCTrace:
    """Parse SCC-related trace logs."""

    WR1_RE = re.compile(
        r"WR1 write port=(\d+) val=0x([0-9a-f]+) "
        r"\(TX_INT=(\d+) RX_INT=(\d+)\) serial_write_count=(\d+)"
    )

    TX_TIMER_RE = re.compile(
        r"TX_TIMER ch=(\d+) SET TX_IP"
    )

    SERIAL_WRITE_RE = re.compile(
        r"SERIAL_WRITE port=(\d+).*?wc=(\d+)"
    )

    def __init__(self,
                 wr1_path="/tmp/scc_wr1_trace.log",
                 tx_timer_path="/tmp/scc_tx_timer_trace.log"):
        self.wr1_entries = []
        self.tx_timer_fires = 0
        self.max_serial_write_count = 0
        self.wr1_path = wr1_path
        self.tx_timer_path = tx_timer_path

        if os.path.exists(wr1_path):
            self._parse_wr1(wr1_path)
        if os.path.exists(tx_timer_path):
            self._parse_tx_timer(tx_timer_path)

    def _parse_wr1(self, path):
        with open(path) as f:
            for line in f:
                m = self.WR1_RE.search(line)
                if m:
                    self.wr1_entries.append(WR1Entry(
                        port=int(m.group(1)),
                        value=int(m.group(2), 16),
                        tx_int=int(m.group(3)),
                        rx_int=int(m.group(4)),
                        serial_write_count=int(m.group(5)),
                    ))

    def _parse_tx_timer(self, path):
        with open(path) as f:
            for line in f:
                if self.TX_TIMER_RE.search(line):
                    self.tx_timer_fires += 1
                m = self.SERIAL_WRITE_RE.search(line)
                if m:
                    wc = int(m.group(2))
                    if wc > self.max_serial_write_count:
                        self.max_serial_write_count = wc
