#!/usr/bin/env python3
"""
GE7 Microcode Analyzer

Parses MAME log output to extract and analyze GE7 microcode
for reverse engineering the instruction set.

Usage:
    python ge7_microcode_analyzer.py <logfile> [options]

Options:
    --output-dir DIR    Output directory for analysis files
    --ge-id N           Analyze specific GE unit (0-7), default: all
    --dump-bin          Export raw microcode as binary
    --dump-hex          Export annotated hex dump
    --analyze           Perform pattern analysis
    --all               Enable all outputs
"""

import argparse
import re
import struct
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class MicrocodeSession:
    """Represents one microcode load sequence"""
    trigger_value: int
    ram_contents: dict[int, dict[int, int]] = field(default_factory=dict)  # ge_id -> {address -> value}
    write_order: dict[int, list[tuple[int, int]]] = field(default_factory=dict)  # ge_id -> [(address, value)]

    def __post_init__(self):
        # Initialize for all 8 GE units
        for i in range(8):
            if i not in self.ram_contents:
                self.ram_contents[i] = {}
            if i not in self.write_order:
                self.write_order[i] = []


@dataclass
class GE7Event:
    """A single GE7 log event"""
    event_type: str  # 'write', 'read', 'pc_set', 'exec', 'unstall'
    ge_id: int
    address: Optional[int] = None
    value: Optional[int] = None
    count: Optional[int] = None
    pc: Optional[int] = None
    instruction: Optional[int] = None


@dataclass
class HQ2Event:
    """A single HQ2 log event"""
    event_type: str  # 'loaducode', 'gepc', 'unstall', 'fin1', 'fin2', 'fin3', etc.
    value: int


class GE7LogParser:
    """Parse MAME log and extract GE7 operations"""

    # Regex patterns for GE7 log messages
    RE_GE7_RAM_WRITE = re.compile(
        r'ge(\d+)\s+ram0\[([0-9a-fA-F]+)\]\s+write:\s+([0-9a-fA-F]+)\s+\(count=(\d+)\)'
    )
    RE_GE7_RAM_READ = re.compile(
        r'ge(\d+)\s+ram0\[([0-9a-fA-F]+)\]\s+read:\s+([0-9a-fA-F]+)'
    )
    RE_GE7_PC_SET = re.compile(
        r'ge(\d+)\s+pc\s+set\s+to\s+([0-9a-fA-F]+)'
    )
    RE_GE7_EXEC = re.compile(
        r'ge(\d+)\s+exec\s+pc=([0-9a-fA-F]+)\s+instr=([0-9a-fA-F]+)'
    )
    RE_GE7_UNSTALL = re.compile(
        r'ge(\d+)\s+unstalled\s+at\s+pc=([0-9a-fA-F]+)'
    )

    # Regex patterns for HQ2 log messages
    RE_HQ2_LOADUCODE = re.compile(
        r'hq2\s+ge7loaducode\s+triggered\s+with\s+([0-9a-fA-F]+)'
    )
    RE_HQ2_GEPC = re.compile(
        r'hq2\s+gepc\s+set\s+to\s+([0-9a-fA-F]+)'
    )
    RE_HQ2_UNSTALL = re.compile(
        r'hq2\s+unstall\s+set\s+to\s+([0-9a-fA-F]+)'
    )
    RE_HQ2_FIN1 = re.compile(
        r'hq2\s+fin1\s+set\s+to\s+([0-9a-fA-F]+)'
    )
    RE_HQ2_FIN2 = re.compile(
        r'hq2\s+fin2\s+set\s+to\s+([0-9a-fA-F]+)'
    )
    RE_HQ2_FIN3 = re.compile(
        r'hq2\s+fin3\s+set\s+to\s+([0-9a-fA-F]+)'
    )
    RE_HQ2_GEDMA = re.compile(
        r'hq2\s+gedma\s+set\s+to\s+([0-9a-fA-F]+)'
    )
    RE_HQ2_HQ_GEPC = re.compile(
        r'hq2\s+hq_gepc\s+set\s+to\s+([0-9a-fA-F]+)'
    )

    def __init__(self):
        self.sessions: list[MicrocodeSession] = []
        self.current_session: Optional[MicrocodeSession] = None
        self.ge7_events: list[GE7Event] = []
        self.hq2_events: list[HQ2Event] = []

    def parse_line(self, line: str) -> None:
        """Parse a single log line and extract relevant events"""
        # Check for HQ2 loaducode trigger (starts new session)
        match = self.RE_HQ2_LOADUCODE.search(line)
        if match:
            trigger_val = int(match.group(1), 16)
            if self.current_session:
                self.sessions.append(self.current_session)
            self.current_session = MicrocodeSession(trigger_value=trigger_val)
            self.hq2_events.append(HQ2Event('loaducode', trigger_val))
            return

        # Check for GE7 RAM write
        match = self.RE_GE7_RAM_WRITE.search(line)
        if match:
            ge_id = int(match.group(1))
            addr = int(match.group(2), 16)
            value = int(match.group(3), 16)
            count = int(match.group(4))

            event = GE7Event('write', ge_id, address=addr, value=value, count=count)
            self.ge7_events.append(event)

            if self.current_session:
                self.current_session.ram_contents[ge_id][addr] = value
                self.current_session.write_order[ge_id].append((addr, value))
            return

        # Check for GE7 RAM read
        match = self.RE_GE7_RAM_READ.search(line)
        if match:
            ge_id = int(match.group(1))
            addr = int(match.group(2), 16)
            value = int(match.group(3), 16)
            event = GE7Event('read', ge_id, address=addr, value=value)
            self.ge7_events.append(event)
            return

        # Check for GE7 PC set
        match = self.RE_GE7_PC_SET.search(line)
        if match:
            ge_id = int(match.group(1))
            pc = int(match.group(2), 16)
            event = GE7Event('pc_set', ge_id, pc=pc)
            self.ge7_events.append(event)
            return

        # Check for GE7 exec
        match = self.RE_GE7_EXEC.search(line)
        if match:
            ge_id = int(match.group(1))
            pc = int(match.group(2), 16)
            instr = int(match.group(3), 16)
            event = GE7Event('exec', ge_id, pc=pc, instruction=instr)
            self.ge7_events.append(event)
            return

        # Check for GE7 unstall
        match = self.RE_GE7_UNSTALL.search(line)
        if match:
            ge_id = int(match.group(1))
            pc = int(match.group(2), 16)
            event = GE7Event('unstall', ge_id, pc=pc)
            self.ge7_events.append(event)
            return

        # Check for other HQ2 events
        for pattern, event_type in [
            (self.RE_HQ2_GEPC, 'gepc'),
            (self.RE_HQ2_UNSTALL, 'unstall'),
            (self.RE_HQ2_FIN1, 'fin1'),
            (self.RE_HQ2_FIN2, 'fin2'),
            (self.RE_HQ2_FIN3, 'fin3'),
            (self.RE_HQ2_GEDMA, 'gedma'),
            (self.RE_HQ2_HQ_GEPC, 'hq_gepc'),
        ]:
            match = pattern.search(line)
            if match:
                value = int(match.group(1), 16)
                self.hq2_events.append(HQ2Event(event_type, value))
                return

    def parse_file(self, filepath: Path) -> None:
        """Parse an entire log file"""
        with open(filepath, 'r', errors='replace') as f:
            for line in f:
                self.parse_line(line)

        # Don't forget the last session
        if self.current_session:
            self.sessions.append(self.current_session)

    def get_merged_microcode(self, ge_id: int) -> dict[int, int]:
        """Get merged microcode for a GE unit across all sessions"""
        merged = {}
        for session in self.sessions:
            merged.update(session.ram_contents.get(ge_id, {}))
        return merged


class MicrocodeAnalyzer:
    """Analyze microcode patterns"""

    # Known IEEE 754 float constants
    KNOWN_FLOATS = {
        0x3f800000: "1.0",
        0x40000000: "2.0",
        0x40400000: "3.0",
        0x40800000: "4.0",
        0x3f000000: "0.5",
        0x3e800000: "0.25",
        0x3fc00000: "1.5",
        0x00000000: "0.0",
        0xbf800000: "-1.0",
        0xc0000000: "-2.0",
        0x40a00000: "5.0",
        0x40c00000: "6.0",
        0x40e00000: "7.0",
        0x41000000: "8.0",
        0x41800000: "16.0",
        0x42000000: "32.0",
        0x42800000: "64.0",
        0x43000000: "128.0",
        0x43800000: "256.0",
        0x447a0000: "1000.0",
        0x3dcccccd: "0.1",
        0x3f4ccccd: "0.8",
        0x3f19999a: "0.6",
        0x3e4ccccd: "0.2",
        0x3e99999a: "0.3",
        0x3ecccccd: "0.4",
        0x3f333333: "0.7",
        0x3f666666: "0.9",
    }

    def __init__(self, microcode: dict[int, int]):
        self.microcode = microcode
        self.opcode_freq: dict[int, int] = defaultdict(int)
        self.byte_freq: dict[int, dict[int, int]] = {i: defaultdict(int) for i in range(4)}

    def analyze(self) -> dict:
        """Perform full analysis and return results"""
        results = {
            'total_words': len(self.microcode),
            'address_range': (min(self.microcode.keys()), max(self.microcode.keys())) if self.microcode else (0, 0),
            'unique_values': len(set(self.microcode.values())),
            'opcode_frequency': {},
            'byte_frequency': {i: {} for i in range(4)},
            'potential_floats': [],
            'zero_words': 0,
            'potential_branches': [],
            'instruction_patterns': [],
        }

        for addr, value in sorted(self.microcode.items()):
            # Extract bytes (big-endian assumed for MIPS)
            byte0 = (value >> 24) & 0xFF  # MSB - potential opcode
            byte1 = (value >> 16) & 0xFF
            byte2 = (value >> 8) & 0xFF
            byte3 = value & 0xFF

            self.opcode_freq[byte0] += 1
            self.byte_freq[0][byte0] += 1
            self.byte_freq[1][byte1] += 1
            self.byte_freq[2][byte2] += 1
            self.byte_freq[3][byte3] += 1

            if value == 0:
                results['zero_words'] += 1

            # Check for known floats
            if value in self.KNOWN_FLOATS:
                results['potential_floats'].append((addr, value, self.KNOWN_FLOATS[value]))

            # Check for potential branch targets (low byte might be address)
            if byte3 < 0x100 and byte3 in self.microcode:
                results['potential_branches'].append((addr, value, byte3))

        # Convert frequency dicts
        results['opcode_frequency'] = dict(sorted(
            self.opcode_freq.items(), key=lambda x: -x[1]
        ))
        for i in range(4):
            results['byte_frequency'][i] = dict(sorted(
                self.byte_freq[i].items(), key=lambda x: -x[1]
            ))

        # Identify instruction patterns (repeated sequences)
        results['instruction_patterns'] = self._find_patterns()

        return results

    def _find_patterns(self) -> list[dict]:
        """Find repeated instruction sequences"""
        patterns = []
        values = [self.microcode.get(i, 0) for i in range(256)]

        # Look for 2-word and 3-word patterns
        for pattern_len in [2, 3]:
            pattern_count: dict[tuple, list[int]] = defaultdict(list)
            for i in range(len(values) - pattern_len + 1):
                pattern = tuple(values[i:i + pattern_len])
                if any(v != 0 for v in pattern):  # Skip all-zero patterns
                    pattern_count[pattern].append(i)

            for pattern, addresses in pattern_count.items():
                if len(addresses) >= 2:
                    patterns.append({
                        'length': pattern_len,
                        'values': list(pattern),
                        'occurrences': len(addresses),
                        'addresses': addresses[:5],  # First 5 occurrences
                    })

        # Sort by occurrence count
        patterns.sort(key=lambda x: -x['occurrences'])
        return patterns[:20]  # Top 20 patterns

    def ieee754_to_float(self, value: int) -> Optional[float]:
        """Convert 32-bit int to IEEE 754 float"""
        try:
            return struct.unpack('>f', struct.pack('>I', value))[0]
        except (struct.error, OverflowError):
            return None


class ReportGenerator:
    """Generate analysis reports"""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_hex_dump(self, ge_id: int, microcode: dict[int, int], analyzer: MicrocodeAnalyzer) -> str:
        """Generate annotated hex dump"""
        lines = []
        lines.append(f"GE7 Unit {ge_id} Microcode ({len(microcode)} words)")
        lines.append("=" * 60)
        lines.append("")
        lines.append("Addr  Value     [Op ] [B1 ] [B2 ] [B3 ]  Annotation")
        lines.append("-" * 60)

        for addr in range(256):
            value = microcode.get(addr, 0)
            byte0 = (value >> 24) & 0xFF
            byte1 = (value >> 16) & 0xFF
            byte2 = (value >> 8) & 0xFF
            byte3 = value & 0xFF

            annotation = ""
            if value in analyzer.KNOWN_FLOATS:
                annotation = f"float {analyzer.KNOWN_FLOATS[value]}"
            elif value == 0:
                annotation = "zero/NOP?"
            else:
                # Try to interpret as float
                f = analyzer.ieee754_to_float(value)
                if f is not None and 0.0001 < abs(f) < 100000 and f == f:  # reasonable range, not NaN
                    annotation = f"float? {f:.6g}"

            lines.append(f"0x{addr:02x}: {value:08x}  [{byte0:02x} ] [{byte1:02x} ] [{byte2:02x} ] [{byte3:02x} ]  {annotation}")

        return "\n".join(lines)

    def generate_analysis_report(self, ge_id: int, analysis: dict) -> str:
        """Generate pattern analysis report"""
        lines = []
        lines.append(f"GE7 Unit {ge_id} Microcode Analysis")
        lines.append("=" * 60)
        lines.append("")

        lines.append("Summary Statistics")
        lines.append("-" * 40)
        lines.append(f"Total words:      {analysis['total_words']}")
        lines.append(f"Address range:    0x{analysis['address_range'][0]:02x} - 0x{analysis['address_range'][1]:02x}")
        lines.append(f"Unique values:    {analysis['unique_values']}")
        lines.append(f"Zero words:       {analysis['zero_words']}")
        lines.append("")

        lines.append("Opcode Frequency Analysis (Byte 31-24)")
        lines.append("-" * 40)
        lines.append("Byte    Count  Possible Meaning")
        for opcode, count in list(analysis['opcode_frequency'].items())[:20]:
            meaning = self._guess_opcode_meaning(opcode)
            lines.append(f"0x{opcode:02x}    {count:5d}  {meaning}")
        lines.append("")

        if analysis['potential_floats']:
            lines.append("Detected Float Constants")
            lines.append("-" * 40)
            for addr, value, name in analysis['potential_floats']:
                lines.append(f"0x{addr:02x}: {value:08x} = {name}")
            lines.append("")

        if analysis['potential_branches']:
            lines.append("Potential Branch Instructions")
            lines.append("-" * 40)
            for addr, value, target in analysis['potential_branches'][:15]:
                lines.append(f"0x{addr:02x}: {value:08x} -> target 0x{target:02x}?")
            lines.append("")

        if analysis['instruction_patterns']:
            lines.append("Repeated Instruction Patterns")
            lines.append("-" * 40)
            for pattern in analysis['instruction_patterns'][:10]:
                values_str = " ".join(f"{v:08x}" for v in pattern['values'])
                addrs_str = ", ".join(f"0x{a:02x}" for a in pattern['addresses'])
                lines.append(f"{pattern['length']}-word pattern ({pattern['occurrences']}x): {values_str}")
                lines.append(f"  at: {addrs_str}")
            lines.append("")

        return "\n".join(lines)

    def generate_opcode_csv(self, ge_id: int, analysis: dict) -> str:
        """Generate CSV of opcode frequencies"""
        lines = ["opcode_hex,opcode_dec,count,percentage"]
        total = sum(analysis['opcode_frequency'].values())
        for opcode, count in analysis['opcode_frequency'].items():
            pct = (count / total * 100) if total > 0 else 0
            lines.append(f"0x{opcode:02x},{opcode},{count},{pct:.2f}")
        return "\n".join(lines)

    def write_binary(self, ge_id: int, microcode: dict[int, int]) -> Path:
        """Write raw microcode as binary file"""
        filepath = self.output_dir / f"ge7_microcode_ge{ge_id}.bin"
        with open(filepath, 'wb') as f:
            for addr in range(256):
                value = microcode.get(addr, 0)
                f.write(struct.pack('>I', value))  # Big-endian
        return filepath

    def write_hex_dump(self, ge_id: int, content: str) -> Path:
        """Write hex dump to file"""
        filepath = self.output_dir / f"ge7_hexdump_ge{ge_id}.txt"
        with open(filepath, 'w') as f:
            f.write(content)
        return filepath

    def write_analysis(self, ge_id: int, content: str) -> Path:
        """Write analysis report to file"""
        filepath = self.output_dir / f"ge7_analysis_ge{ge_id}.txt"
        with open(filepath, 'w') as f:
            f.write(content)
        return filepath

    def write_csv(self, ge_id: int, content: str) -> Path:
        """Write CSV to file"""
        filepath = self.output_dir / f"ge7_opcodes_ge{ge_id}.csv"
        with open(filepath, 'w') as f:
            f.write(content)
        return filepath

    def _guess_opcode_meaning(self, opcode: int) -> str:
        """Make educated guesses about opcode meaning"""
        # Common patterns seen in graphics processors
        if opcode == 0x00:
            return "NOP or data"
        elif 0x3e <= opcode <= 0x43:
            return "FPU constant (IEEE 754 exponent range)"
        elif opcode >= 0xc0:
            return "Control flow? (high bit set)"
        elif opcode >= 0x80:
            return "ALU operation? (bit 7 set)"
        elif 0x20 <= opcode <= 0x3f:
            return "Load/store?"
        elif 0x10 <= opcode <= 0x1f:
            return "Register operation?"
        else:
            return ""


def main():
    parser = argparse.ArgumentParser(
        description="GE7 Microcode Analyzer - Parse and analyze GE7 microcode from MAME logs"
    )
    parser.add_argument('logfile', type=Path, help="MAME log file to parse")
    parser.add_argument('--output-dir', type=Path, default=Path('.'),
                        help="Output directory for analysis files")
    parser.add_argument('--ge-id', type=int, choices=range(8), default=None,
                        help="Analyze specific GE unit (0-7), default: all with data")
    parser.add_argument('--dump-bin', action='store_true',
                        help="Export raw microcode as binary")
    parser.add_argument('--dump-hex', action='store_true',
                        help="Export annotated hex dump")
    parser.add_argument('--analyze', action='store_true',
                        help="Perform pattern analysis")
    parser.add_argument('--all', action='store_true',
                        help="Enable all outputs")
    parser.add_argument('--verbose', '-v', action='store_true',
                        help="Verbose output")

    args = parser.parse_args()

    if args.all:
        args.dump_bin = True
        args.dump_hex = True
        args.analyze = True

    if not any([args.dump_bin, args.dump_hex, args.analyze]):
        # Default to showing analysis on stdout
        args.analyze = True

    if not args.logfile.exists():
        print(f"Error: Log file not found: {args.logfile}", file=sys.stderr)
        sys.exit(1)

    # Parse the log file
    print(f"Parsing log file: {args.logfile}")
    log_parser = GE7LogParser()
    log_parser.parse_file(args.logfile)

    print(f"Found {len(log_parser.sessions)} microcode load session(s)")
    print(f"Found {len(log_parser.ge7_events)} GE7 events")
    print(f"Found {len(log_parser.hq2_events)} HQ2 events")

    if not log_parser.sessions and not log_parser.ge7_events:
        print("No GE7 microcode data found in log file.")
        print("Make sure VERBOSE includes LOG_GE7 | LOG_HQ2 in gr2.cpp")
        sys.exit(0)

    # Determine which GE units to analyze
    ge_units = [args.ge_id] if args.ge_id is not None else range(8)

    report_gen = ReportGenerator(args.output_dir)
    files_written = []

    for ge_id in ge_units:
        microcode = log_parser.get_merged_microcode(ge_id)

        if not microcode:
            if args.verbose:
                print(f"GE{ge_id}: No microcode data")
            continue

        print(f"\nGE{ge_id}: {len(microcode)} words loaded")

        analyzer = MicrocodeAnalyzer(microcode)
        analysis = analyzer.analyze()

        if args.dump_bin:
            path = report_gen.write_binary(ge_id, microcode)
            files_written.append(path)
            print(f"  Written: {path}")

        if args.dump_hex:
            content = report_gen.generate_hex_dump(ge_id, microcode, analyzer)
            path = report_gen.write_hex_dump(ge_id, content)
            files_written.append(path)
            print(f"  Written: {path}")

        if args.analyze:
            content = report_gen.generate_analysis_report(ge_id, analysis)
            path = report_gen.write_analysis(ge_id, content)
            files_written.append(path)
            print(f"  Written: {path}")

            csv_content = report_gen.generate_opcode_csv(ge_id, analysis)
            csv_path = report_gen.write_csv(ge_id, csv_content)
            files_written.append(csv_path)
            print(f"  Written: {csv_path}")

            # Also print summary to stdout
            print(f"\n  Summary for GE{ge_id}:")
            print(f"    Total words:   {analysis['total_words']}")
            print(f"    Unique values: {analysis['unique_values']}")
            print(f"    Float constants: {len(analysis['potential_floats'])}")
            print(f"    Top opcodes: ", end="")
            top_ops = list(analysis['opcode_frequency'].items())[:5]
            print(", ".join(f"0x{op:02x}({cnt})" for op, cnt in top_ops))

    if files_written:
        print(f"\nTotal files written: {len(files_written)}")


if __name__ == '__main__':
    main()
