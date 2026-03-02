"""PROM and firmware image analysis modules for SGI systems.

This package provides tools for analyzing SGI firmware images,
including version detection, format identification, and string extraction.

Supports multiple generations of SGI systems and firmware types:

CPU PROMs:
- Professional IRIS (IP4)
- 4D series (IP6, IP12, IP15)
- Indigo/Indy/Indigo2 (IP20, IP22, IP24, IP26, IP28)
- Crimson (IP17)
- Octane (IP30)
- O2 (IP32)
- Origin 2000 (IP27)
- Origin 3000 (IP35)
- IO6 Base I/O Controller

Graphics Microcode:
- InfiniteReality KONA transport processor (ARM)
- Venice VS2 (MIPS ELF)
- Impact GE11/HQ3 (raw microcode)
- VPro Buzz (Odyssey transform engine)
- Voyager SG2 (O2 ATI BIOS)

System Controllers:
- L1/L2 Controllers (Origin 3000)
- Power Bay Controller
- MMSC (Multi-Module System Controller)
"""

from .firmware_types import (
    FirmwareType,
    detect_firmware_type,
    get_type_name,
    get_type_description,
)
from .prom_image import (
    FirmwareInfo,
    PROMInfo,
    SGIFirmwareAnalyzer,
    SGIPROMImage,
)
from .version import (
    PROMVersion,
    extract_cpu_type,
    extract_ip_board,
    find_version_strings,
    parse_version,
)

__all__ = [
    # Firmware types
    'FirmwareType',
    'detect_firmware_type',
    'get_type_name',
    'get_type_description',
    # Analyzers
    'FirmwareInfo',
    'PROMInfo',
    'PROMVersion',
    'SGIFirmwareAnalyzer',
    'SGIPROMImage',
    # Version utilities
    'extract_cpu_type',
    'extract_ip_board',
    'find_version_strings',
    'parse_version',
]
