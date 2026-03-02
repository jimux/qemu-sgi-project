# SGI PROM Comparative Analysis - Output Formatters
"""
Output formatting utilities for JSON and Markdown.
"""

import json
from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING
from dataclasses import asdict, is_dataclass

if TYPE_CHECKING:
    from .analysis import (
        QemuLogSummary, ExpectedAccess, RegisterValueAnalysis, ExecutionComparison
    )
    from .scsi_parser import SCSILogSummary
    from .boot_milestones import BootReport


def to_dict(obj: Any) -> Any:
    """Convert object to dictionary, handling dataclasses and bytes."""
    if is_dataclass(obj):
        return {k: to_dict(v) for k, v in asdict(obj).items()}
    elif isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [to_dict(item) for item in obj]
    elif isinstance(obj, bytes):
        return obj.hex()
    elif hasattr(obj, '__dict__'):
        return {k: to_dict(v) for k, v in obj.__dict__.items() if not k.startswith('_')}
    else:
        return obj


def format_json(data: Any, indent: int = 2) -> str:
    """Format data as JSON string."""
    return json.dumps(to_dict(data), indent=indent, default=str)


def format_markdown_table(
    rows: List[Dict],
    columns: Optional[List[str]] = None,
    headers: Optional[Dict[str, str]] = None
) -> str:
    """
    Format data as a Markdown table.

    Args:
        rows: List of dictionaries
        columns: Column keys to include (None = all)
        headers: Display names for columns (key -> header)

    Returns:
        Markdown table string
    """
    if not rows:
        return "*(no data)*"

    if columns is None:
        # Get all unique keys
        columns = []
        for row in rows:
            for key in row.keys():
                if key not in columns:
                    columns.append(key)

    if headers is None:
        headers = {col: col for col in columns}

    # Build header row
    header_row = "| " + " | ".join(headers.get(col, col) for col in columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"

    # Build data rows
    data_rows = []
    for row in rows:
        cells = []
        for col in columns:
            value = row.get(col, "")
            # Format value
            if isinstance(value, bytes):
                value = value.hex()[:16] + "..." if len(value) > 8 else value.hex()
            elif isinstance(value, (int, float)):
                value = str(value)
            elif value is None:
                value = ""
            else:
                value = str(value)
            # Escape pipe characters
            value = value.replace("|", "\\|")
            cells.append(value)
        data_rows.append("| " + " | ".join(cells) + " |")

    return "\n".join([header_row, separator] + data_rows)


def format_prom_info(meta: Dict) -> str:
    """Format PROM metadata as readable text."""
    lines = [
        f"**{meta.get('filename', 'Unknown')}**",
        "",
        f"- **Size:** {meta.get('size', 0):,} bytes",
        f"- **Platform:** {meta.get('platform', 'Unknown')}",
        f"- **Part Number:** {meta.get('part_number', 'N/A')}",
        f"- **Entry Point:** {meta.get('entry_point', '0x00000000')}",
        f"- **Endianness:** {meta.get('endian', 'big')}",
        f"- **SHA256:** `{meta.get('sha256', 'N/A')[:32]}...`",
    ]

    if meta.get('vectors'):
        lines.append("")
        lines.append("**Vectors:**")
        for name, addr in meta['vectors'].items():
            lines.append(f"  - {name}: `0x{addr:08x}`")

    return "\n".join(lines)


def format_disassembly_markdown(
    lines: List[Dict],
    show_bytes: bool = True,
    show_annotations: bool = True
) -> str:
    """Format disassembly as Markdown code block."""
    result = ["```asm"]

    for line in lines:
        addr = line.get('address', 0)
        bytes_hex = line.get('bytes_hex', '')
        mnemonic = line.get('mnemonic', '')
        op_str = line.get('op_str', '')
        annotation = line.get('annotation', '')

        if show_bytes:
            text = f"{addr:08x}:  {bytes_hex:8s}  {mnemonic:8s} {op_str}"
        else:
            text = f"{addr:08x}:  {mnemonic:8s} {op_str}"

        if show_annotations and annotation:
            text = f"{text:50s} {annotation}"

        result.append(text)

    result.append("```")
    return "\n".join(result)


def format_diff_markdown(diff_data: Dict) -> str:
    """Format diff comparison as Markdown."""
    lines = []

    # Header
    prom1 = diff_data.get('prom1', {})
    prom2 = diff_data.get('prom2', {})

    lines.append(f"## Comparison: {prom1.get('filename', '?')} vs {prom2.get('filename', '?')}")
    lines.append("")

    # Summary table
    comparison = diff_data.get('comparison', {})
    lines.append("### Summary")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Identical | {'Yes' if comparison.get('identical') else 'No'} |")
    lines.append(f"| Similarity | {comparison.get('similarity_percent', 0):.2f}% |")
    lines.append(f"| Diff Regions | {comparison.get('diff_regions', 0)} |")
    lines.append(f"| Bytes Changed | {comparison.get('total_diff_bytes', 0):,} |")
    lines.append("")

    # PROM details
    lines.append("### PROM Details")
    lines.append("")
    lines.append("| Property | PROM 1 | PROM 2 |")
    lines.append("|----------|--------|--------|")
    lines.append(f"| Platform | {prom1.get('platform', 'N/A')} | {prom2.get('platform', 'N/A')} |")
    lines.append(f"| Part Number | {prom1.get('part_number', 'N/A')} | {prom2.get('part_number', 'N/A')} |")
    lines.append(f"| Size | {prom1.get('size', 0):,} | {prom2.get('size', 0):,} |")
    lines.append(f"| Entry Point | {prom1.get('entry_point', 'N/A')} | {prom2.get('entry_point', 'N/A')} |")

    return "\n".join(lines)


def format_pattern_matches_markdown(matches: List[Dict], pattern_type: str) -> str:
    """Format pattern matches as Markdown."""
    if not matches:
        return f"*No {pattern_type} patterns found.*"

    lines = [f"### {pattern_type.replace('_', ' ').title()}", ""]

    for match in matches:
        addr = match.get('address', 0)
        offset = match.get('offset', 0)
        desc = match.get('description', '')

        lines.append(f"- `0x{addr:08x}` (+0x{offset:05x}): {desc}")

    return "\n".join(lines)


def format_hexdump_header(filename: str, offset: int, length: int, total_size: int) -> str:
    """Format header for hex dump output."""
    return f"File: {filename} | Offset: 0x{offset:x} | Length: {length} bytes | Total Size: {total_size:,} bytes"


def format_string_list(strings: List[tuple], base_address: int = 0xbfc00000) -> str:
    """Format extracted strings as readable list."""
    if not strings:
        return "*No strings found.*"

    lines = []
    for offset, text in strings:
        addr = base_address + offset
        # Truncate long strings
        display_text = text[:60] + "..." if len(text) > 60 else text
        # Escape special chars for display
        display_text = display_text.replace("\n", "\\n").replace("\t", "\\t")
        lines.append(f"0x{addr:08x}: \"{display_text}\"")

    return "\n".join(lines)


def format_common_code_summary(common_code: List[Dict], top_n: int = 20) -> str:
    """Format common code summary."""
    if not common_code:
        return "*No common code blocks found.*"

    lines = ["### Common Code Blocks", ""]
    lines.append("| Hash (short) | Block Size | Occurrences | Files |")
    lines.append("|--------------|------------|-------------|-------|")

    for entry in common_code[:top_n]:
        hash_short = entry.get('hash', '')[:8]
        length = entry.get('length', 0)
        locations = entry.get('locations', [])
        files = set(loc[0] if isinstance(loc, (list, tuple)) else loc.get('filename', '') for loc in locations)

        lines.append(f"| `{hash_short}` | {length} | {len(locations)} | {len(files)} |")

    if len(common_code) > top_n:
        lines.append(f"| ... | ... | {len(common_code) - top_n} more entries | ... |")

    return "\n".join(lines)


# =============================================================================
# QEMU Debugging Tools - Formatters
# =============================================================================

def format_qemu_log_summary(summary: 'QemuLogSummary', max_entries: int = 50) -> str:
    """Format QemuLogSummary as Markdown."""
    lines = [
        "# QEMU Log Analysis",
        "",
        f"**Total lines:** {summary.total_lines}",
        f"**Hardware accesses:** {summary.hardware_accesses}",
        "",
    ]

    # Device breakdown
    if summary.device_counts:
        lines.append("## Device Access Counts")
        lines.append("")
        lines.append("| Device | Count |")
        lines.append("|--------|-------|")
        for device, count in sorted(summary.device_counts.items(), key=lambda x: -x[1]):
            lines.append(f"| {device} | {count} |")
        lines.append("")

    # Top registers
    if summary.register_counts:
        lines.append("## Top Registers Accessed")
        lines.append("")
        lines.append("| Register | Count |")
        lines.append("|----------|-------|")
        for reg, count in sorted(summary.register_counts.items(), key=lambda x: -x[1])[:15]:
            lines.append(f"| {reg} | {count} |")
        lines.append("")

    # Access log
    lines.append("## Hardware Access Log")
    lines.append("")
    lines.append("| Line | Device | Offset | Op | Value | Annotation |")
    lines.append("|------|--------|--------|-------|-------|------------|")

    for entry in summary.entries[:max_entries]:
        op = "R" if entry.operation.value == "read" else "W"
        value_str = f"0x{entry.value:08x}" if entry.value is not None else "-"
        annotation = entry.annotation[:40] if entry.annotation else "-"
        lines.append(
            f"| {entry.line_number} | {entry.device} | 0x{entry.register_offset:04x} | "
            f"{op} | {value_str} | {annotation} |"
        )

    if len(summary.entries) > max_entries:
        lines.append(f"| ... | | | | | {len(summary.entries) - max_entries} more |")

    # Unrecognized lines
    if summary.unrecognized_lines:
        lines.append("")
        lines.append("## Unrecognized Lines")
        lines.append("")
        lines.append("```")
        for line in summary.unrecognized_lines[:10]:
            lines.append(line[:100])
        lines.append("```")

    return "\n".join(lines)


def format_expected_sequence(sequence: List['ExpectedAccess'], max_entries: int = 100) -> str:
    """Format expected hardware access sequence as Markdown."""
    lines = [
        "# Expected Hardware Access Sequence",
        "",
        f"**Total accesses:** {len(sequence)}",
        "",
        "| # | Code Addr | Device | Register | Op | Expected Value | Source |",
        "|---|-----------|--------|----------|----|----------------|--------|",
    ]

    for exp in sequence[:max_entries]:
        op = "R" if exp.operation.value == "read" else "W"
        value_str = f"0x{exp.expected_value:08x}" if exp.expected_value is not None else "-"
        lines.append(
            f"| {exp.order} | `0x{exp.code_address:08x}` | {exp.device} | "
            f"{exp.register} | {op} | {value_str} | {exp.value_source} |"
        )

    if len(sequence) > max_entries:
        lines.append(f"| ... | | | | | | {len(sequence) - max_entries} more |")

    return "\n".join(lines)


def format_register_value_analysis(analysis: List['RegisterValueAnalysis'], max_entries: int = 100) -> str:
    """Format register value analysis as Markdown."""
    lines = [
        "# Register Value Analysis",
        "",
        f"**Total accesses:** {len(analysis)}",
        "",
    ]

    # Count polling loops
    polling_count = sum(1 for a in analysis if a.is_polling_loop)
    if polling_count:
        lines.append(f"**Polling loops detected:** {polling_count}")
        lines.append("")

    lines.extend([
        "| Code Addr | Device | Register | Op | Value | Confidence | Polling |",
        "|-----------|--------|----------|----|----- -|------------|---------|",
    ])

    for item in analysis[:max_entries]:
        op = "R" if item.operation.value == "read" else "W"
        value_str = f"0x{item.value:08x}" if item.value is not None else "-"
        polling = "Yes" if item.is_polling_loop else ""
        lines.append(
            f"| `0x{item.code_address:08x}` | {item.device} | {item.register} | "
            f"{op} | {value_str} | {item.value_confidence} | {polling} |"
        )

    if len(analysis) > max_entries:
        lines.append(f"| ... | | | | | | {len(analysis) - max_entries} more |")

    # Show instruction sequences for first few writes
    writes_with_seq = [a for a in analysis if a.instruction_sequence and a.operation.value == "write"][:5]
    if writes_with_seq:
        lines.append("")
        lines.append("## Value Construction Examples")
        lines.append("")
        for item in writes_with_seq:
            lines.append(f"**0x{item.code_address:08x}** writes to {item.device}.{item.register}:")
            lines.append("```asm")
            for instr in item.instruction_sequence:
                lines.append(f"  {instr}")
            lines.append(f"  ; value = 0x{item.value:08x}" if item.value else "  ; value = unknown")
            lines.append("```")
            lines.append("")

    return "\n".join(lines)


def format_execution_comparison(comparison: 'ExecutionComparison') -> str:
    """Format execution comparison as Markdown."""
    lines = [
        "# Execution Comparison",
        "",
        f"**PROM:** {comparison.prom_name or 'N/A'}",
        f"**Log file:** {comparison.log_file or 'N/A'}",
        "",
        "## Summary",
        "",
        f"- **Expected accesses:** {comparison.expected_count}",
        f"- **Actual accesses:** {comparison.actual_count}",
        f"- **Matched:** {comparison.match_count}",
        f"- **Divergences:** {len(comparison.divergences)}",
        "",
        comparison.summary,
        "",
    ]

    if comparison.divergences:
        lines.append("## Divergences")
        lines.append("")
        lines.append("| Type | Severity | Device | Register | Details |")
        lines.append("|------|----------|--------|----------|---------|")

        for div in comparison.divergences[:50]:
            if div.expected:
                device = div.expected.device
                register = div.expected.register
            elif div.actual:
                device = div.actual.device
                register = f"0x{div.actual.register_offset:04x}"
            else:
                device = "-"
                register = "-"

            details = div.suggestion[:50] if div.suggestion else "-"
            lines.append(
                f"| {div.divergence_type} | {div.severity} | {device} | {register} | {details} |"
            )

        if len(comparison.divergences) > 50:
            lines.append(f"| ... | | | | {len(comparison.divergences) - 50} more |")

    if comparison.recommendations:
        lines.append("")
        lines.append("## Recommendations")
        lines.append("")
        for rec in comparison.recommendations:
            lines.append(f"- {rec}")

    return "\n".join(lines)


# =============================================================================
# SCSI Trace Formatters
# =============================================================================

def format_scsi_log_summary(summary: 'SCSILogSummary', max_entries: int = 100) -> str:
    """Format SCSILogSummary as Markdown."""
    lines = [
        "# SCSI Command Trace",
        "",
        "## Summary",
        "",
        f"| Metric | Count |",
        f"|--------|-------|",
        f"| Total commands | {summary.total_commands} |",
        f"| Successful | {summary.successful_commands} |",
        f"| Failed (CHECK_CONDITION) | {summary.failed_commands} |",
        f"| Timeouts | {summary.timeouts} |",
        "",
    ]

    # Command frequency table
    if summary.command_counts:
        lines.append("## Command Frequency")
        lines.append("")
        lines.append("| Opcode | Count |")
        lines.append("|--------|-------|")
        for name, count in sorted(summary.command_counts.items(), key=lambda x: -x[1]):
            lines.append(f"| {name} | {count} |")
        lines.append("")

    # Per-target activity
    if summary.target_activity:
        lines.append("## Target Activity")
        lines.append("")
        lines.append("| Target ID | Commands |")
        lines.append("|-----------|----------|")
        for tid, count in sorted(summary.target_activity.items()):
            lines.append(f"| {tid} | {count} |")
        lines.append("")

    # MODE_SENSE page status
    if summary.mode_sense_pages:
        lines.append("## MODE_SENSE Page Status")
        lines.append("")
        lines.append("| Page | Status |")
        lines.append("|------|--------|")
        for page, status in sorted(summary.mode_sense_pages.items()):
            status_icon = "ok" if status == "ok" else "FAILED"
            lines.append(f"| 0x{page:02x} | {status_icon} |")
        lines.append("")

    # Error details
    if summary.error_counts:
        lines.append("## Error Summary")
        lines.append("")
        lines.append("| Error | Count |")
        lines.append("|-------|-------|")
        for desc, count in sorted(summary.error_counts.items(), key=lambda x: -x[1]):
            lines.append(f"| {desc} | {count} |")
        lines.append("")

    # Error command details
    if summary.errors:
        lines.append("## Error Details")
        lines.append("")
        lines.append("| Line | Target | Command | Sense | Description |")
        lines.append("|------|--------|---------|-------|-------------|")
        for cmd in summary.errors[:max_entries]:
            if cmd.status == "check_condition":
                sense = (f"{cmd.sense_key}/{cmd.sense_asc}/{cmd.sense_ascq}"
                         if cmd.sense_key is not None else "-")
                desc = cmd.sense_desc or cmd.error_detail or "-"
            else:
                sense = "-"
                desc = cmd.status or "-"
            lines.append(
                f"| {cmd.line_number} | {cmd.target_id} | "
                f"{cmd.opcode_name} | {sense} | {desc} |"
            )
        if len(summary.errors) > max_entries:
            lines.append(f"| ... | | | | {len(summary.errors) - max_entries} more |")
        lines.append("")

    # Command timeline
    if summary.commands:
        lines.append("## Command Timeline")
        lines.append("")
        lines.append("| Line | Target | Command | CDB | Status | Data Len |")
        lines.append("|------|--------|---------|-----|--------|----------|")
        for cmd in summary.commands[:max_entries]:
            cdb_short = cmd.cdb[:23] + "..." if len(cmd.cdb) > 23 else cmd.cdb
            status = cmd.status or "ok"
            if status == "check_condition" and cmd.sense_desc:
                status = f"FAIL: {cmd.sense_desc[:30]}"
            data_len = str(cmd.data_len) if cmd.data_len is not None else "-"
            lines.append(
                f"| {cmd.line_number} | {cmd.target_id} | "
                f"{cmd.opcode_name} | `{cdb_short}` | {status} | {data_len} |"
            )
        if len(summary.commands) > max_entries:
            lines.append(f"| ... | | | | | {len(summary.commands) - max_entries} more |")

    return "\n".join(lines)


# =============================================================================
# Boot Milestone Formatters
# =============================================================================

def format_boot_report(report: 'BootReport') -> str:
    """Format BootReport as Markdown."""
    lines = [
        "# Boot Progress Report",
        "",
        f"**Milestones reached:** {report.milestones_reached} of {report.milestones_total}",
        f"**Stop reason:** {report.stop_reason}",
        f"**Elapsed:** {report.elapsed_seconds:.1f}s",
        "",
    ]

    # Milestone timeline
    if report.milestones:
        lines.append("## Milestone Timeline")
        lines.append("")
        lines.append("| Time | Phase | Milestone | Status |")
        lines.append("|------|-------|-----------|--------|")
        for ms in report.milestones:
            if ms.reached:
                time_str = f"{ms.timestamp:.1f}s" if ms.timestamp is not None else "?"
                status = "reached"
            else:
                time_str = "-"
                status = "not reached"
            lines.append(
                f"| {time_str} | {ms.phase} | {ms.name} | {status} |"
            )
        lines.append("")

    # SCSI error summary (if any)
    if report.scsi_error_summary:
        lines.append("## SCSI Errors During Boot")
        lines.append("")
        lines.append(report.scsi_error_summary)
        lines.append("")

    # Last output context
    if report.last_output:
        lines.append("## Last Serial Output")
        lines.append("")
        lines.append("```")
        # Show last 30 lines
        output_lines = report.last_output.strip().split('\n')
        if len(output_lines) > 30:
            lines.extend(output_lines[-30:])
        else:
            lines.extend(output_lines)
        lines.append("```")

    return "\n".join(lines)
