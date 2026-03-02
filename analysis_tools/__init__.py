"""SGI Binary Analysis Toolkit.

A toolkit for analyzing and decoding SGI (Silicon Graphics) binary data formats,
including NVRAM dumps, PROM images, disk structures, and MAME emulator save files.

Usage as CLI:
    python -m analysis_tools nvram <file>

Usage as library:
    from analysis_tools.nvram import SGINVRAMAnalyzer
    analyzer = SGINVRAMAnalyzer.from_file("path/to/rtc")
    print(analyzer.format_report())
"""

__version__ = "0.1.0"

from .nvram.prom_env import SGINVRAMAnalyzer, SGIEnvironment, SGIPROMEnvironment
from .nvram.ds1386 import DS1386, DS1386Time

__all__ = [
    'SGINVRAMAnalyzer',
    'SGIEnvironment',
    'SGIPROMEnvironment',
    'DS1386',
    'DS1386Time',
]
