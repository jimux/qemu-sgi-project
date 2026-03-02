"""Parser for SGI SN0/SN1 container format (JFKSWCSM).

This container format is used by Origin 2000 (SN0/IP27), Origin 3000 (SN1/IP35),
and IO6 base I/O controller PROMs. The format features a distinctive "JFKSWCSM"
magic signature at offset 0x40.

Header Structure:
    0x00-0x3F: Padding (zeros)
    0x3F:      Format version byte
    0x40-0x47: "JFKSWCSM" magic
    0x48-0x4F: Flags (64-bit)
    0x50-0x57: Total size (64-bit)
    0x60-0x7F: Reserved
    0x80-0x8F: Module name (null-terminated)
    0x90-0x97: Header size offset
    0x98-0x9F: Code start offset
    0xA0-0xAF: Load address (64-bit)
    0xB0-0xBF: Code size and other fields
    0x1000+:   MIPS code begins
"""

import re
from dataclasses import dataclass
from typing import Optional, List, Tuple

from ..utils.endian import read_u32_be, read_u64_be, read_cstring
from ..utils.hexdump import find_strings


# Magic signature for SN containers
JFKSWCSM_MAGIC = b'JFKSWCSM'
JFKSWCSM_OFFSET = 0x40


@dataclass
class SNContainerInfo:
    """Parsed SN0/SN1 container header information."""
    magic: str                          # "JFKSWCSM"
    format_version: int                 # Version byte at 0x3F
    flags: int                          # 64-bit flags at 0x48
    total_size: int                     # Total file size from header
    module_name: str                    # e.g., "ip27prom", "io6prom"
    header_size: int                    # Header/code offset info
    code_offset: int                    # Where MIPS code begins
    load_address: int                   # 64-bit load address
    load_address_str: str               # Formatted load address
    entry_point: Optional[int] = None   # Entry point if different
    code_size: int = 0                  # Size of code section
    platform: str = ""                  # "SN0", "SN1", etc.
    ip_board: str = ""                  # "IP27", "IP35", etc.
    version_string: Optional[str] = None  # Full version string
    version_number: Optional[str] = None  # Just the version (e.g., "6.150")
    build_date: Optional[str] = None    # Build date extracted


@dataclass
class SNContainerAnalysis:
    """Complete analysis of an SN container firmware."""
    header: SNContainerInfo
    notable_strings: List[Tuple[int, str]]


def is_sn_container(data: bytes) -> bool:
    """Check if data is an SN0/SN1 container.

    Args:
        data: Binary data to check

    Returns:
        True if JFKSWCSM magic is present at offset 0x40
    """
    if len(data) < JFKSWCSM_OFFSET + len(JFKSWCSM_MAGIC):
        return False
    return data[JFKSWCSM_OFFSET:JFKSWCSM_OFFSET + len(JFKSWCSM_MAGIC)] == JFKSWCSM_MAGIC


def parse_sn_container_header(data: bytes) -> Optional[SNContainerInfo]:
    """Parse SN container header.

    Args:
        data: Binary firmware data

    Returns:
        SNContainerInfo or None if not a valid SN container
    """
    if not is_sn_container(data):
        return None

    if len(data) < 0x100:
        return None

    # Read format version byte at 0x3F
    format_version = data[0x3F]

    # Read 64-bit fields (big-endian)
    flags = read_u64_be(data, 0x48)
    total_size = read_u64_be(data, 0x50)

    # Read module name at 0x80 (null-terminated)
    module_name, _ = read_cstring(data, 0x80, max_len=16)

    # Read header/code offsets
    header_size = read_u64_be(data, 0x90)
    code_offset = read_u64_be(data, 0x98)

    # Read 64-bit load address
    load_address = read_u64_be(data, 0xA0)
    load_address_str = format_64bit_address(load_address)

    # Read code size
    code_size = read_u64_be(data, 0xB0)

    # Determine platform and IP board from module name
    platform, ip_board = classify_sn_module(module_name, data)

    # Search for version string
    version_string, version_number, build_date = find_sn_version_string(data)

    return SNContainerInfo(
        magic=JFKSWCSM_MAGIC.decode('ascii'),
        format_version=format_version,
        flags=flags,
        total_size=total_size,
        module_name=module_name,
        header_size=header_size,
        code_offset=code_offset,
        load_address=load_address,
        load_address_str=load_address_str,
        code_size=code_size,
        platform=platform,
        ip_board=ip_board,
        version_string=version_string,
        version_number=version_number,
        build_date=build_date,
    )


def format_64bit_address(addr: int) -> str:
    """Format a 64-bit address in SGI style.

    Args:
        addr: 64-bit address value

    Returns:
        Formatted string like "0xc0000000_1fc00000"
    """
    high = (addr >> 32) & 0xFFFFFFFF
    low = addr & 0xFFFFFFFF
    return f"0x{high:08x}_{low:08x}"


def classify_sn_module(module_name: str, data: bytes) -> Tuple[str, str]:
    """Classify the SN platform and IP board from module name.

    Args:
        module_name: Module name from header (e.g., "ip27prom")
        data: Full binary data for string searches

    Returns:
        Tuple of (platform, ip_board) e.g., ("SN0", "IP27")
    """
    name_lower = module_name.lower()

    # Direct IP board detection from module name
    if 'ip27' in name_lower:
        return ("SN0", "IP27")
    elif 'ip35' in name_lower:
        return ("SN1", "IP35")
    elif 'io6' in name_lower:
        return ("SN0", "IO6")

    # Search in strings for platform indicators
    try:
        # Check first 8KB of data for platform strings
        sample = data[:8192].decode('ascii', errors='ignore')

        if 'SN1' in sample or 'IP35' in sample:
            return ("SN1", "IP35")
        elif 'SN0' in sample or 'IP27' in sample:
            return ("SN0", "IP27")
    except Exception:
        pass

    return ("Unknown", "Unknown")


def find_sn_version_string(data: bytes) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Find version string in SN container firmware.

    Looks for "SGI Version X.XXX built ..." format.

    Args:
        data: Binary firmware data

    Returns:
        Tuple of (full_version_string, version_number, build_date)
    """
    # Pattern: "SGI Version 6.150  built 11:59:56 AM Sep 29, 2003"
    version_pattern = re.compile(
        rb'SGI Version\s+([\d.]+)\s+built\s+(.+?\d{4})',
        re.IGNORECASE
    )

    match = version_pattern.search(data)
    if match:
        try:
            full_match = match.group(0).decode('ascii', errors='ignore')
            version_number = match.group(1).decode('ascii', errors='ignore')
            build_date = match.group(2).decode('ascii', errors='ignore').strip()
            return (full_match, version_number, build_date)
        except Exception:
            pass

    return (None, None, None)


def analyze_sn_container(data: bytes) -> Optional[SNContainerAnalysis]:
    """Perform complete analysis of an SN container firmware.

    Args:
        data: Binary firmware data

    Returns:
        SNContainerAnalysis or None if not a valid SN container
    """
    header = parse_sn_container_header(data)
    if not header:
        return None

    # Find notable strings
    notable_strings = find_notable_sn_strings(data)

    return SNContainerAnalysis(
        header=header,
        notable_strings=notable_strings,
    )


def find_notable_sn_strings(data: bytes, limit: int = 30) -> List[Tuple[int, str]]:
    """Find notable strings in SN container firmware.

    Args:
        data: Binary firmware data
        limit: Maximum strings to return

    Returns:
        List of (offset, string) tuples
    """
    notable_patterns = [
        'SGI',
        'Version',
        'PROM',
        'NASID',
        'NODE',
        'BASEIO',
        'Error',
        'Copyright',
        'Origin',
        'Onyx',
        'WARNING',
    ]

    all_strings = find_strings(data, min_length=10)
    results = []

    for offset, s in all_strings:
        if len(results) >= limit:
            break

        for pattern in notable_patterns:
            if pattern.lower() in s.lower():
                results.append((offset, s))
                break

    return results


def format_sn_container_report(analysis: SNContainerAnalysis) -> str:
    """Format SN container analysis as a human-readable report.

    Args:
        analysis: SNContainerAnalysis result

    Returns:
        Formatted report string
    """
    h = analysis.header
    lines = []

    lines.append("=== SGI Firmware Analysis ===")
    lines.append("")
    lines.append(f"Type: SN Container ({h.platform})")
    lines.append("")

    # Header info
    lines.append("Header:")
    lines.append(f"  Magic:           {h.magic}")
    lines.append(f"  Module:          {h.module_name}")
    lines.append(f"  Platform:        {h.platform}")
    if h.ip_board != "Unknown":
        lines.append(f"  IP Board:        {h.ip_board}")
    lines.append(f"  Load Address:    {h.load_address_str}")
    lines.append(f"  Code Offset:     0x{h.code_offset:x}")
    if h.code_size > 0:
        lines.append(f"  Code Size:       {h.code_size} bytes ({h.code_size // 1024} KB)")
    lines.append(f"  Format Version:  0x{h.format_version:02x}")

    # Version info
    if h.version_string:
        lines.append("")
        lines.append("Version:")
        lines.append(f"  String:          {h.version_string}")
        if h.version_number:
            lines.append(f"  Number:          {h.version_number}")
        if h.build_date:
            lines.append(f"  Build Date:      {h.build_date}")

    # Notable strings
    if analysis.notable_strings:
        lines.append("")
        lines.append("Notable Strings:")
        for offset, s in analysis.notable_strings[:15]:
            display_str = s[:60] + "..." if len(s) > 60 else s
            lines.append(f"  0x{offset:06x}: {display_str}")
        if len(analysis.notable_strings) > 15:
            lines.append(f"  ... and {len(analysis.notable_strings) - 15} more")

    return "\n".join(lines)


def sn_container_to_dict(analysis: SNContainerAnalysis) -> dict:
    """Convert SN container analysis to dictionary.

    Args:
        analysis: SNContainerAnalysis result

    Returns:
        Dictionary suitable for JSON serialization
    """
    h = analysis.header
    return {
        'type': 'sn_container',
        'header': {
            'magic': h.magic,
            'format_version': h.format_version,
            'flags': h.flags,
            'total_size': h.total_size,
            'module_name': h.module_name,
            'code_offset': h.code_offset,
            'load_address': h.load_address,
            'load_address_str': h.load_address_str,
            'code_size': h.code_size,
            'platform': h.platform,
            'ip_board': h.ip_board,
        },
        'version': {
            'string': h.version_string,
            'number': h.version_number,
            'build_date': h.build_date,
        } if h.version_string else None,
        'notable_strings': [
            {'offset': offset, 'string': s}
            for offset, s in analysis.notable_strings
        ],
    }
