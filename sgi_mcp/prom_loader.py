# SGI PROM analysis - PROM Loader (shim)
"""
PROM loading with caching, platform detection, and metadata extraction.

The implementation now lives in pyirix.prom.prom_loader (so pyirix can stand
alone). This module re-exports it for backward compatibility with existing
sgi_mcp imports (load_prom, get_prom_metadata, normalize_data, extract_strings,
list_prom_files, PromMetadata, ...).
"""

from pyirix.prom.prom_loader import *  # noqa: F401,F403
