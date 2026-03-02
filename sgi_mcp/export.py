# SGI PROM Comparative Analysis - Symbol Export
"""
Export functions for Ghidra, IDA, and JSON formats.
"""

import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime

from .analysis import FunctionDatabase, Function, HardwareAccess, BootSequenceStep


def export_ghidra_symbols(db: FunctionDatabase, output_path: str) -> str:
    """
    Export symbols in Ghidra .sym format.

    Format: ADDRESS LABEL
    One symbol per line, address in hex without 0x prefix.

    Args:
        db: Function database to export
        output_path: Path for output file

    Returns:
        Path to exported file
    """
    lines = [
        f"; Ghidra symbol file for {db.prom_name}",
        f"; Generated: {datetime.now().isoformat()}",
        f"; Functions: {len(db.functions)}",
        "",
    ]

    # Export functions
    for addr, func in sorted(db.functions.items()):
        name = func.name if func.name else func.suggested_name()
        # Ghidra format: ADDRESS NAME
        lines.append(f"{addr:08x} {name}")

    # Export strings as labels
    for addr, value in sorted(db.strings.items()):
        # Create a safe label from string
        safe_name = ''.join(c if c.isalnum() else '_' for c in value[:20])
        if safe_name:
            lines.append(f"{addr:08x} str_{safe_name}")

    content = "\n".join(lines)
    Path(output_path).write_text(content)
    return output_path


def export_ida_idc(db: FunctionDatabase, output_path: str) -> str:
    """
    Export symbols as IDA IDC script.

    Creates a script that sets function names and comments.

    Args:
        db: Function database to export
        output_path: Path for output file

    Returns:
        Path to exported file
    """
    lines = [
        "// IDA IDC script for SGI PROM analysis",
        f"// Generated: {datetime.now().isoformat()}",
        f"// PROM: {db.prom_name}",
        f"// Functions: {len(db.functions)}",
        "",
        "#include <idc.idc>",
        "",
        "static main()",
        "{",
    ]

    # Set function names
    for addr, func in sorted(db.functions.items()):
        name = func.name if func.name else func.suggested_name()
        lines.append(f'    MakeName(0x{addr:08x}, "{name}");')

        # Add function comment with details
        if func.hardware_accesses:
            devices = set(ha.device for ha in func.hardware_accesses)
            comment = f"Accesses: {', '.join(devices)}"
            lines.append(f'    SetFunctionCmt(0x{addr:08x}, "{comment}", 0);')

        if func.string_refs:
            first_str = func.string_refs[0].string_value[:40].replace('"', '\\"')
            lines.append(f'    SetFunctionCmt(0x{addr:08x}, "Uses: \\"{first_str}\\"", 1);')

    # Mark strings
    lines.append("")
    lines.append("    // String labels")
    for addr, value in sorted(db.strings.items()):
        safe_name = ''.join(c if c.isalnum() else '_' for c in value[:20])
        if safe_name:
            lines.append(f'    MakeName(0x{addr:08x}, "str_{safe_name}");')

    lines.append("}")

    content = "\n".join(lines)
    Path(output_path).write_text(content)
    return output_path


def export_function_json(db: FunctionDatabase, output_path: str) -> str:
    """
    Export function database as JSON.

    Args:
        db: Function database to export
        output_path: Path for output file

    Returns:
        Path to exported file
    """
    data = db.to_dict()
    data["exported"] = datetime.now().isoformat()

    content = json.dumps(data, indent=2)
    Path(output_path).write_text(content)
    return output_path


def export_hardware_sequence_json(
    steps: List[BootSequenceStep],
    output_path: str,
    prom_name: str = ""
) -> str:
    """
    Export boot hardware access sequence as JSON.

    Args:
        steps: Boot sequence steps
        output_path: Path for output file
        prom_name: PROM name for metadata

    Returns:
        Path to exported file
    """
    # Filter to only hardware access steps
    hw_steps = [s for s in steps if s.hardware_access is not None]

    data = {
        "prom_name": prom_name,
        "exported": datetime.now().isoformat(),
        "total_steps": len(steps),
        "hardware_access_count": len(hw_steps),
        "hardware_sequence": [
            {
                "order": step.order,
                "code_address": f"0x{step.code_address:08x}",
                "function": step.function_name,
                "device": step.hardware_access.device,
                "register": step.hardware_access.register,
                "full_address": f"0x{step.hardware_access.full_address:08x}",
                "operation": step.hardware_access.operation.value,
                "description": step.hardware_access.description,
            }
            for step in hw_steps
        ]
    }

    content = json.dumps(data, indent=2)
    Path(output_path).write_text(content)
    return output_path


def export_hardware_sequence_markdown(
    steps: List[BootSequenceStep],
    output_path: str,
    prom_name: str = ""
) -> str:
    """
    Export boot hardware access sequence as Markdown documentation.

    Args:
        steps: Boot sequence steps
        output_path: Path for output file
        prom_name: PROM name for metadata

    Returns:
        Path to exported file
    """
    lines = [
        f"# Boot Hardware Sequence: {prom_name}",
        "",
        f"Generated: {datetime.now().isoformat()}",
        "",
        "## Summary",
        "",
    ]

    # Collect statistics
    hw_steps = [s for s in steps if s.hardware_access is not None]
    devices = {}
    for step in hw_steps:
        dev = step.hardware_access.device
        if dev not in devices:
            devices[dev] = {"reads": 0, "writes": 0}
        if step.hardware_access.operation.value == "read":
            devices[dev]["reads"] += 1
        else:
            devices[dev]["writes"] += 1

    lines.append(f"- Total steps traced: {len(steps)}")
    lines.append(f"- Hardware accesses: {len(hw_steps)}")
    lines.append("")

    lines.append("### Device Access Summary")
    lines.append("")
    lines.append("| Device | Reads | Writes | Total |")
    lines.append("|--------|-------|--------|-------|")
    for dev, counts in sorted(devices.items()):
        total = counts["reads"] + counts["writes"]
        lines.append(f"| {dev} | {counts['reads']} | {counts['writes']} | {total} |")
    lines.append("")

    # Detailed sequence
    lines.append("## Hardware Access Timeline")
    lines.append("")
    lines.append("| # | Address | Function | Device | Register | Op | Description |")
    lines.append("|---|---------|----------|--------|----------|----|-------------|")

    for step in hw_steps:
        ha = step.hardware_access
        op = "R" if ha.operation.value == "read" else "W"
        lines.append(
            f"| {step.order} | `0x{step.code_address:08x}` | "
            f"{step.function_name} | {ha.device} | {ha.register} | "
            f"{op} | {ha.description[:40]} |"
        )

    content = "\n".join(lines)
    Path(output_path).write_text(content)
    return output_path


def export_arcs_callbacks_json(
    callbacks: List[Tuple[int, str, int]],
    output_path: str,
    prom_name: str = ""
) -> str:
    """
    Export ARCS callback table as JSON.

    Args:
        callbacks: List of (index, name, address) tuples
        output_path: Path for output file
        prom_name: PROM name for metadata

    Returns:
        Path to exported file
    """
    data = {
        "prom_name": prom_name,
        "exported": datetime.now().isoformat(),
        "callback_count": len(callbacks),
        "callbacks": [
            {
                "index": idx,
                "name": name,
                "address": f"0x{addr:08x}",
            }
            for idx, name, addr in callbacks
        ]
    }

    content = json.dumps(data, indent=2)
    Path(output_path).write_text(content)
    return output_path


def format_call_graph_dot(db: FunctionDatabase) -> str:
    """
    Export call graph in DOT format for Graphviz visualization.

    Args:
        db: Function database

    Returns:
        DOT format string
    """
    lines = [
        f"digraph call_graph {{",
        f'    label="{db.prom_name} Call Graph";',
        f"    rankdir=TB;",
        f"    node [shape=box, fontname=monospace];",
        "",
    ]

    # Add nodes with labels
    for addr, func in db.functions.items():
        name = func.name if func.name else func.suggested_name()
        color = "lightblue"
        if addr in db.call_graph.entry_points:
            color = "lightgreen"
        elif addr in db.call_graph.orphans:
            color = "lightgray"

        lines.append(f'    n{addr:x} [label="{name}\\n0x{addr:08x}", fillcolor={color}, style=filled];')

    lines.append("")

    # Add edges
    for caller, callees in db.call_graph.callees.items():
        for callee in callees:
            if callee in db.functions:
                lines.append(f"    n{caller:x} -> n{callee:x};")

    lines.append("}")

    return "\n".join(lines)


def export_call_graph_dot(db: FunctionDatabase, output_path: str) -> str:
    """
    Export call graph as DOT file.

    Args:
        db: Function database
        output_path: Path for output file

    Returns:
        Path to exported file
    """
    content = format_call_graph_dot(db)
    Path(output_path).write_text(content)
    return output_path
