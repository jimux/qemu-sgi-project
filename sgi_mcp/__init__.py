# SGI PROM Comparative Analysis MCP Server
"""
MCP server for comparative analysis of SGI PROM binaries (IP4-IP32).
Provides tools for disassembly, pattern detection, cross-PROM comparison,
hardware annotation, call graph analysis, and function identification.

New in 0.2.0:
- Call graph extraction (build_call_graph)
- Boot sequence tracing with hardware access timeline
- ARCS callback identification
- Function database building with automatic naming
- Symbol export (Ghidra, IDA, JSON, DOT formats)
- Enhanced hardware access tracking with full address reconstruction
"""

__version__ = "0.2.0"
__author__ = "SGI PROM Analysis Tools"

# Core modules
from .config import PROM_BASE, PLATFORMS, detect_platform
from .prom_loader import load_prom, get_prom_metadata, extract_strings
from .hardware_defs import annotate_address, list_devices, get_device_info
from .disassembler import disassemble_prom, format_disassembly
from .pattern_detector import (
    # Original pattern detection
    find_hardware_probes,
    find_exception_vectors,
    find_graphics_init,
    find_memory_detection,
    find_device_detection,
    find_jump_tables,
    # New analysis functions
    build_call_graph,
    find_function_boundaries,
    track_hardware_accesses,
    trace_boot_sequence,
    find_string_references,
    identify_arcs_callbacks,
    analyze_function,
    build_function_database,
)
from .comparator import diff_binary, find_common_code, signature_search

# Analysis data structures
from .analysis import (
    Function,
    FunctionDatabase,
    CallGraph,
    HardwareAccess,
    AccessType,
    BootSequenceStep,
    StringReference,
    ARCS_CALLBACKS,
)

# Export utilities
from .export import (
    export_ghidra_symbols,
    export_ida_idc,
    export_function_json,
    export_hardware_sequence_json,
    export_hardware_sequence_markdown,
    export_arcs_callbacks_json,
    export_call_graph_dot,
)

__all__ = [
    # Config
    "PROM_BASE",
    "PLATFORMS",
    "detect_platform",
    # Loader
    "load_prom",
    "get_prom_metadata",
    "extract_strings",
    # Hardware
    "annotate_address",
    "list_devices",
    "get_device_info",
    # Disassembly
    "disassemble_prom",
    "format_disassembly",
    # Pattern detection
    "find_hardware_probes",
    "find_exception_vectors",
    "find_graphics_init",
    "find_memory_detection",
    "find_device_detection",
    "find_jump_tables",
    # New analysis
    "build_call_graph",
    "find_function_boundaries",
    "track_hardware_accesses",
    "trace_boot_sequence",
    "find_string_references",
    "identify_arcs_callbacks",
    "analyze_function",
    "build_function_database",
    # Comparison
    "diff_binary",
    "find_common_code",
    "signature_search",
    # Data structures
    "Function",
    "FunctionDatabase",
    "CallGraph",
    "HardwareAccess",
    "AccessType",
    "BootSequenceStep",
    "StringReference",
    "ARCS_CALLBACKS",
    # Export
    "export_ghidra_symbols",
    "export_ida_idc",
    "export_function_json",
    "export_hardware_sequence_json",
    "export_hardware_sequence_markdown",
    "export_arcs_callbacks_json",
    "export_call_graph_dot",
]
