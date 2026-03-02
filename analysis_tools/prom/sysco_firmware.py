"""Analyzers for SGI system controller firmware.

This module provides parsers for system controller firmware:
- L1/L2 Controllers (Origin 3000, 68K/PowerPC, ESTFBINR format)
- Power Bay Controller (MCU)
- MMSC (Multi-Module System Controller, x86)
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..utils.endian import read_u32_be, read_cstring
from ..utils.hexdump import find_strings


# ============================================================================
# ESTFBINR format (L1/L2 controllers)
# ============================================================================

# Magic signature for ESTFBINR format
ESTFBINR_MAGIC = b'ESTFBINR'


@dataclass
class ESTFBINRInfo:
    """Parsed ESTFBINR header information."""
    magic: str = "ESTFBINR"
    size: int = 0
    architecture: str = ""          # "68K" or "PowerPC"
    controller_type: str = ""       # "L1" or "L2"
    version_string: str = ""        # e.g., "SGI L2 Controller ROM 0.01"
    boot_loader: bool = False       # Contains boot loader
    load_addresses: List[int] = field(default_factory=list)


def is_estfbinr(data: bytes) -> bool:
    """Check if data has ESTFBINR magic.

    Args:
        data: Binary data

    Returns:
        True if ESTFBINR magic present at offset 0
    """
    return len(data) >= 8 and data[0:8] == ESTFBINR_MAGIC


def parse_estfbinr(data: bytes, filename: str = "") -> Optional[ESTFBINRInfo]:
    """Parse ESTFBINR format firmware (L1/L2 controllers).

    ESTFBINR is a container format used for Origin 2000/3000 system
    controller firmware. It contains header info followed by the
    controller code (68K or PowerPC).

    Args:
        data: Binary firmware data
        filename: Filename for hints

    Returns:
        ESTFBINRInfo or None
    """
    if not is_estfbinr(data):
        return None

    info = ESTFBINRInfo()
    info.size = len(data)

    # Detect controller type from filename or strings
    fname_lower = filename.lower()
    if 'l1' in fname_lower:
        info.controller_type = "L1"
    elif 'l2' in fname_lower:
        info.controller_type = "L2"

    # Read potential load addresses from header (at offsets 0x08-0x10)
    if len(data) >= 0x20:
        # These look like address pairs
        addr1 = read_u32_be(data, 0x08)
        addr2 = read_u32_be(data, 0x0C)
        if addr1 != 0:
            info.load_addresses.append(addr1)
        if addr2 != 0 and addr2 != addr1:
            info.load_addresses.append(addr2)

    # Detect architecture from code patterns
    info.architecture = _detect_estfbinr_architecture(data)

    # Search for version string (often at offset 0x30+)
    info.version_string = _find_estfbinr_version(data)

    # Check if this contains boot loader
    if b'Boot Loader' in data or b'boot loader' in data:
        info.boot_loader = True

    return info


def _detect_estfbinr_architecture(data: bytes) -> str:
    """Detect processor architecture in ESTFBINR firmware.

    Args:
        data: Binary firmware data

    Returns:
        "68K", "PowerPC", or "Unknown"
    """
    # Check for 68K patterns
    # 68K RTS (return) = 0x4e75
    # 68K LINK = 0x4e56
    # 68K UNLK = 0x4e5e
    mc68k_patterns = [b'\x4e\x75', b'\x4e\x56', b'\x4e\x5e', b'\x48\xe7']
    mc68k_count = 0
    for pattern in mc68k_patterns:
        if pattern in data[0x100:0x1000]:
            mc68k_count += 1

    # Check for PowerPC patterns
    # PPC mflr r0 = 0x7c0802a6
    # PPC blr = 0x4e800020
    ppc_patterns = [b'\x7c\x08\x02\xa6', b'\x4e\x80\x00\x20', b'\x7d\x40\x01\x24']
    ppc_count = 0
    for pattern in ppc_patterns:
        if pattern in data[0x100:0x1000]:
            ppc_count += 1

    if ppc_count > mc68k_count:
        return "PowerPC"
    elif mc68k_count > 0:
        return "68K"
    else:
        return "Unknown"


def _find_estfbinr_version(data: bytes) -> str:
    """Find version string in ESTFBINR firmware.

    Args:
        data: Binary firmware data

    Returns:
        Version string or empty string
    """
    # Look for "SGI L1/L2 Controller..." pattern
    patterns = [
        rb'SGI L\d Controller [\w\s.]+',
        rb'SGI L\d [\w\s.]+',
    ]

    for pattern in patterns:
        match = re.search(pattern, data[:0x200])
        if match:
            try:
                return match.group(0).decode('ascii', errors='ignore').strip()
            except Exception:
                pass

    # Also check offset 0x34 where version string often appears
    if len(data) >= 0x60:
        try:
            version, _ = read_cstring(data, 0x34, max_len=60)
            if version and 'SGI' in version:
                return version.strip()
        except Exception:
            pass

    return ""


def format_estfbinr_report(info: ESTFBINRInfo) -> str:
    """Format ESTFBINR info as a report."""
    lines = []
    lines.append("=== SGI Firmware Analysis ===")
    lines.append("")
    lines.append(f"Type: System Controller ({info.controller_type})")
    lines.append("")

    lines.append("Header:")
    lines.append(f"  Magic:           {info.magic}")
    lines.append(f"  Size:            {info.size} bytes ({info.size // 1024} KB)")
    lines.append(f"  Controller:      {info.controller_type}")
    lines.append(f"  Architecture:    {info.architecture}")
    if info.boot_loader:
        lines.append(f"  Contains:        Boot Loader")

    if info.version_string:
        lines.append("")
        lines.append("Version:")
        lines.append(f"  Name:            {info.version_string}")

    if info.load_addresses:
        lines.append("")
        lines.append("Load Addresses:")
        for addr in info.load_addresses[:4]:
            lines.append(f"  0x{addr:08x}")

    return "\n".join(lines)


# ============================================================================
# Power Bay Controller (MCU)
# ============================================================================

@dataclass
class PBayInfo:
    """Parsed power bay controller information."""
    size: int = 0
    author: str = ""            # Author attribution
    version_indicator: int = 0  # Version byte
    architecture: str = "MCU"


def parse_pbay_firmware(data: bytes) -> Optional[PBayInfo]:
    """Parse power bay controller firmware.

    The power bay controller is a small MCU-based firmware with
    the author signature "Owen Yah" near the start.

    Args:
        data: Binary firmware data

    Returns:
        PBayInfo or None
    """
    if len(data) < 32:
        return None

    info = PBayInfo()
    info.size = len(data)

    # Look for author signature "<< Author : Owen Yah >>"
    author_match = re.search(rb'<<\s*Author\s*:\s*([^>]+)\s*>>', data[:256])
    if author_match:
        try:
            info.author = author_match.group(1).decode('ascii', errors='ignore').strip()
        except Exception:
            pass

    # Version indicator at offset 0x1C
    if len(data) >= 0x1D:
        info.version_indicator = data[0x1C]

    return info


def is_pbay_firmware(data: bytes, filename: str = "") -> bool:
    """Check if this looks like power bay firmware.

    Args:
        data: Binary firmware data
        filename: Filename for hints

    Returns:
        True if this is likely pbay firmware
    """
    # Filename hint
    if 'pbay' in filename.lower():
        return True

    # Size hint - pbay is small (~6KB)
    if len(data) > 16384:
        return False

    # Author signature
    if b'Owen Yah' in data[:256]:
        return True

    return False


def format_pbay_report(info: PBayInfo) -> str:
    """Format power bay info as a report."""
    lines = []
    lines.append("=== SGI Firmware Analysis ===")
    lines.append("")
    lines.append("Type: Power Bay Controller (MCU)")
    lines.append("")

    lines.append("Firmware:")
    lines.append(f"  Size:            {info.size} bytes ({info.size / 1024:.1f} KB)")
    lines.append(f"  Architecture:    {info.architecture}")
    if info.author:
        lines.append(f"  Author:          {info.author}")
    if info.version_indicator:
        lines.append(f"  Version Byte:    0x{info.version_indicator:02x}")

    return "\n".join(lines)


# ============================================================================
# MMSC (Multi-Module System Controller, x86)
# ============================================================================

# MMSC magic at offset 0x18: 0x5aa5a55a
MMSC_MAGIC = b'\x5a\xa5\xa5\x5a'
MMSC_MAGIC_OFFSET = 0x18


@dataclass
class MMSCInfo:
    """Parsed MMSC firmware information."""
    size: int = 0
    magic_valid: bool = False
    architecture: str = "x86"


def is_mmsc_firmware(data: bytes) -> bool:
    """Check if data has MMSC magic signature.

    Args:
        data: Binary firmware data

    Returns:
        True if MMSC magic present at offset 0x18
    """
    if len(data) < MMSC_MAGIC_OFFSET + 4:
        return False
    return data[MMSC_MAGIC_OFFSET:MMSC_MAGIC_OFFSET + 4] == MMSC_MAGIC


def parse_mmsc_firmware(data: bytes) -> Optional[MMSCInfo]:
    """Parse MMSC (Multi-Module System Controller) firmware.

    MMSC is an x86-based embedded controller used in Origin systems.

    Args:
        data: Binary firmware data

    Returns:
        MMSCInfo or None
    """
    info = MMSCInfo()
    info.size = len(data)
    info.magic_valid = is_mmsc_firmware(data)

    # The firmware is x86 code - we can see CLI, JMP, NOP patterns
    # at offset 0x20+
    return info


def format_mmsc_report(info: MMSCInfo) -> str:
    """Format MMSC info as a report."""
    lines = []
    lines.append("=== SGI Firmware Analysis ===")
    lines.append("")
    lines.append("Type: MMSC Controller (x86)")
    lines.append("")

    lines.append("Firmware:")
    lines.append(f"  Size:            {info.size} bytes ({info.size // 1024} KB)")
    lines.append(f"  Architecture:    {info.architecture}")
    lines.append(f"  Magic Valid:     {'Yes' if info.magic_valid else 'No'}")

    return "\n".join(lines)


# ============================================================================
# Unified analysis
# ============================================================================

@dataclass
class SyscoAnalysis:
    """Unified system controller analysis result."""
    firmware_type: str = ""         # "estfbinr", "pbay", "mmsc"
    specific_info: object = None    # Type-specific info dataclass
    notable_strings: List[Tuple[int, str]] = field(default_factory=list)


def analyze_sysco_firmware(data: bytes, filename: str = "") -> Optional[SyscoAnalysis]:
    """Analyze system controller firmware.

    Args:
        data: Binary firmware data
        filename: Filename for hints

    Returns:
        SyscoAnalysis or None
    """
    # Try ESTFBINR (L1/L2)
    if is_estfbinr(data):
        estfbinr_info = parse_estfbinr(data, filename)
        if estfbinr_info:
            return SyscoAnalysis(
                firmware_type="estfbinr",
                specific_info=estfbinr_info,
                notable_strings=_find_sysco_strings(data),
            )

    # Try MMSC
    if is_mmsc_firmware(data):
        mmsc_info = parse_mmsc_firmware(data)
        if mmsc_info:
            return SyscoAnalysis(
                firmware_type="mmsc",
                specific_info=mmsc_info,
                notable_strings=_find_sysco_strings(data),
            )

    # Try pbay
    if is_pbay_firmware(data, filename):
        pbay_info = parse_pbay_firmware(data)
        if pbay_info:
            return SyscoAnalysis(
                firmware_type="pbay",
                specific_info=pbay_info,
                notable_strings=[],
            )

    return None


def _find_sysco_strings(data: bytes, limit: int = 20) -> List[Tuple[int, str]]:
    """Find notable strings in system controller firmware.

    Args:
        data: Binary firmware data
        limit: Maximum strings to return

    Returns:
        List of (offset, string) tuples
    """
    notable_patterns = [
        'SGI', 'Controller', 'L1', 'L2', 'L3', 'MMSC',
        'Flash', 'Error', 'FATAL', 'Boot', 'ROM',
        'Version', 'version', 'Copyright'
    ]

    all_strings = find_strings(data, min_length=8)
    results = []

    for offset, s in all_strings:
        if len(results) >= limit:
            break

        for pattern in notable_patterns:
            if pattern in s:
                results.append((offset, s))
                break

    return results


def sysco_to_dict(analysis: SyscoAnalysis) -> dict:
    """Convert sysco analysis to dictionary.

    Args:
        analysis: SyscoAnalysis result

    Returns:
        Dictionary for JSON serialization
    """
    result = {
        'firmware_type': analysis.firmware_type,
        'notable_strings': [
            {'offset': o, 'string': s}
            for o, s in analysis.notable_strings[:15]
        ],
    }

    info = analysis.specific_info

    if isinstance(info, ESTFBINRInfo):
        result['estfbinr'] = {
            'size': info.size,
            'architecture': info.architecture,
            'controller_type': info.controller_type,
            'version_string': info.version_string,
            'boot_loader': info.boot_loader,
            'load_addresses': [hex(a) for a in info.load_addresses],
        }
    elif isinstance(info, PBayInfo):
        result['pbay'] = {
            'size': info.size,
            'author': info.author,
            'version_indicator': info.version_indicator,
            'architecture': info.architecture,
        }
    elif isinstance(info, MMSCInfo):
        result['mmsc'] = {
            'size': info.size,
            'magic_valid': info.magic_valid,
            'architecture': info.architecture,
        }

    return result
