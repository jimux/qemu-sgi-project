# SGI PROM analysis - Hardware definitions (shim)
"""
Register definitions ported from MAME and NetBSD sources; hardware
annotations for memory-mapped I/O addresses.

The implementation now lives in pyirix.prom.hardware_defs (so pyirix can stand
alone). This module re-exports it for backward compatibility with existing
sgi_mcp imports (annotate_address, format_annotation, get_lui_annotation,
get_device_info, list_devices, ...).
"""

from pyirix.prom.hardware_defs import *  # noqa: F401,F403
