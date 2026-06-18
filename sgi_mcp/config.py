# SGI PROM analysis - Configuration (Ghidra/MCP + PROM-general re-export)
"""
Ghidra integration constants for the MCP server, plus a re-export of the
PROM-general configuration (memory map, KSEG address math, platform
definitions) which now lives in pyirix.prom.config so pyirix can stand alone.

Existing imports keep working: PROM_BASE, PROM_DIR, PLATFORMS, detect_platform,
get_cpu_mode, prom_offset_to_addr, addr_to_prom_offset, ... all resolve via the
re-export below; GHIDRA_* and get_ghidra_language are defined here.
"""

from pathlib import Path

from pyirix.prom.config import *  # noqa: F401,F403


# Ghidra integration constants (MCP-specific; intentionally NOT in pyirix.prom)
GHIDRA_HOME = Path("/home/dev/ghidra")
GHIDRA_ANALYZE_HEADLESS = GHIDRA_HOME / "Ghidra" / "RuntimeScripts" / "Linux" / "support" / "analyzeHeadless"
GHIDRA_PROJECT_DIR = Path(__file__).parent.parent / "ghidra_projects"
GHIDRA_SCRIPT_DIR = Path(__file__).parent / "ghidra_scripts"

# Map cpu_arch to Ghidra language ID
GHIDRA_LANGUAGE_MAP = {
    "mips1": "MIPS:BE:32:default",
    "mips2": "MIPS:BE:32:default",
    "mips3": "MIPS:BE:32:default",
    "mips4": "MIPS:BE:64:default",
    "mips64": "MIPS:BE:64:default",
}


def get_ghidra_language(platform_id: str) -> str:
    """Get Ghidra language ID for a platform."""
    if platform_id not in PLATFORMS:  # noqa: F405  (PLATFORMS via re-export)
        return "MIPS:BE:32:default"
    arch = PLATFORMS[platform_id].cpu_arch  # noqa: F405
    return GHIDRA_LANGUAGE_MAP.get(arch, "MIPS:BE:32:default")
