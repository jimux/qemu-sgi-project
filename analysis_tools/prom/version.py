"""SGI PROM version string detection and parsing.

Supports multiple version string formats spanning SGI's history:
- Early (1987-89): "SAIO Version 4D1-X.X PROM IPn"
- Middle (1993-96): "SGI Version X.X Rev Xn R4X00/R5000 IPnn"
- Late (2000+): "SGI Version X.X Rev X.X IPnn"
- O2/IP32: "VERSION X.XX" in SHDR format
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class PROMVersion:
    """Parsed PROM version information."""
    raw_string: str
    vendor: str                      # "SGI" or "Silicon Graphics"
    format_version: Optional[str]    # "SAIO" for early, "4D1" for middle
    major: Optional[str]             # Major version (e.g., "5.3", "6.5")
    minor: Optional[str]             # Minor/revision (e.g., "B10", "4.9")
    ip_board: Optional[str]          # "IP22", "IP24", etc.
    cpu_type: Optional[str]          # "R4X00", "R5000", "R10000", etc.
    build_date: Optional[str]        # Build date string


# Version pattern matchers - ordered from most specific to least
VERSION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # Early SAIO format with date: "Version 4D1-3.1 PROM IP4 OPT Thu Dec 8 16:12:10 PST 1988 SGI"
    (re.compile(
        r'Version\s+(4D1-[\d.]+)\s+PROM\s+(IP\d+)\s+\w*\s*'
        r'(\w{3}\s+\w{3}\s+\d+\s+[\d:]+\s+\w+\s+\d{4})\s*SGI',
        re.IGNORECASE
    ), 'saio'),

    # Early IP5 format: "Version 4.0 IP5 OPT Fri Apr 26 17:12:22 PDT 1991 SGI"
    (re.compile(
        r'Version\s+([\d.]+)\s+(IP\d+)\s+\w*\s*'
        r'(\w{3}\s+\w{3}\s+\d+\s+[\d:]+\s+\w+\s+\d{4})\s*SGI',
        re.IGNORECASE
    ), 'early_ip'),

    # Middle format with CPU: "SGI Version 5.3 Rev B10 R4X00/R5000 IP24 Feb 12, 1996"
    (re.compile(
        r'SGI\s+Version\s+([\d.]+)\s+Rev\s+(\w+)\s+'
        r'(R\w+(?:/R\w+)?)\s+(IP\d+)\s+(.+?\d{4})',
        re.IGNORECASE
    ), 'middle_cpu'),

    # Middle format without CPU: "SGI Version 5.1 Rev B IP22 Sep 16, 1993"
    (re.compile(
        r'SGI\s+Version\s+([\d.]+)\s+Rev\s+(\w+)\s+'
        r'(IP\d+)\s+(.+?\d{4})',
        re.IGNORECASE
    ), 'middle'),

    # Middle format with Rev before IP: "SGI Version 4.0.3 Rev  IP17,  Apr 30, 1992"
    (re.compile(
        r'SGI\s+Version\s+([\d.]+[A-Z]?)\s+Rev\s+\w*\s*'
        r'(IP\d+),?\s+(.+?\d{4})',
        re.IGNORECASE
    ), 'middle_rev_ip'),

    # Late format: "SGI Version 6.5 Rev 4.9 IP30 May 22, 2003"
    (re.compile(
        r'SGI\s+Version\s+([\d.]+)\s+Rev\s+([\d.]+)\s+'
        r'(IP\d+)\s+(.+?\d{4})',
        re.IGNORECASE
    ), 'late'),

    # Graphics variant: "SGI Version 4.0.1 Rev C GR1/GR2/LG1,  Feb 14, 1992"
    (re.compile(
        r'SGI\s+Version\s+([\d.]+)\s+Rev\s+(\w+)\s+'
        r'([GLR][A-Z0-9/]+),?\s*(.+?\d{4})',
        re.IGNORECASE
    ), 'graphics'),

    # SAIO header format: "$Header$ Silicon Graphics, Inc. SAIO Version 4D1-3.1 PROM IP4"
    (re.compile(
        r'SAIO\s+Version\s+(4D1-[\d.]+)\s+PROM\s+(IP\d+)',
        re.IGNORECASE
    ), 'saio_header'),

    # SAIO Rev format: "SAIO Version 4D1-3.1 Rev C"
    (re.compile(
        r'SAIO\s+Version\s+(4D1-[\d.]+)\s+Rev\s+(\w+)',
        re.IGNORECASE
    ), 'saio_rev'),

    # O2/IP32 simple format: "VERSION 4.18"
    (re.compile(
        r'VERSION\s+([\d.]+)',
        re.IGNORECASE
    ), 'ip32_simple'),
]

# Pattern to extract IP board from any string
IP_BOARD_PATTERN = re.compile(r'\b(IP\d+)\b', re.IGNORECASE)

# Pattern to extract CPU type
CPU_PATTERN = re.compile(r'\b(R[0-9]+[A-Z]*(?:/R[0-9]+[A-Z]*)?)\b', re.IGNORECASE)


def parse_version(version_string: str) -> Optional[PROMVersion]:
    """Parse a PROM version string into structured data.

    Args:
        version_string: Raw version string from PROM

    Returns:
        PROMVersion object if parsing succeeds, None otherwise
    """
    for pattern, pattern_type in VERSION_PATTERNS:
        match = pattern.search(version_string)
        if match:
            return _parse_match(version_string, match, pattern_type)

    return None


def _parse_match(raw: str, match: re.Match, pattern_type: str) -> PROMVersion:
    """Parse a regex match into a PROMVersion based on pattern type."""
    groups = match.groups()

    if pattern_type == 'saio':
        # Groups: version (4D1-X.X), ip_board, build_date
        version_parts = groups[0].split('-')
        return PROMVersion(
            raw_string=raw,
            vendor='SGI',
            format_version='SAIO',
            major=version_parts[1] if len(version_parts) > 1 else groups[0],
            minor=None,
            ip_board=groups[1].upper(),
            cpu_type=None,
            build_date=groups[2].strip() if len(groups) > 2 else None
        )

    elif pattern_type == 'middle_cpu':
        # Groups: major, rev, cpu, ip_board, build_date
        return PROMVersion(
            raw_string=raw,
            vendor='SGI',
            format_version=None,
            major=groups[0],
            minor=groups[1],
            ip_board=groups[3].upper(),
            cpu_type=groups[2].upper(),
            build_date=groups[4].strip()
        )

    elif pattern_type == 'middle':
        # Groups: major, rev, ip_board, build_date
        return PROMVersion(
            raw_string=raw,
            vendor='SGI',
            format_version=None,
            major=groups[0],
            minor=groups[1],
            ip_board=groups[2].upper(),
            cpu_type=None,
            build_date=groups[3].strip()
        )

    elif pattern_type == 'late':
        # Groups: major, rev (numeric), ip_board, build_date
        return PROMVersion(
            raw_string=raw,
            vendor='SGI',
            format_version=None,
            major=groups[0],
            minor=groups[1],
            ip_board=groups[2].upper(),
            cpu_type=None,
            build_date=groups[3].strip()
        )

    elif pattern_type == 'early_ip':
        # Groups: major, ip_board, build_date
        return PROMVersion(
            raw_string=raw,
            vendor='SGI',
            format_version=None,
            major=groups[0],
            minor=None,
            ip_board=groups[1].upper(),
            cpu_type=None,
            build_date=groups[2].strip() if len(groups) > 2 else None
        )

    elif pattern_type == 'middle_rev_ip':
        # Groups: major, ip_board, build_date
        return PROMVersion(
            raw_string=raw,
            vendor='SGI',
            format_version=None,
            major=groups[0],
            minor=None,
            ip_board=groups[1].upper(),
            cpu_type=None,
            build_date=groups[2].strip() if len(groups) > 2 else None
        )

    elif pattern_type == 'graphics':
        # Groups: major, rev, graphics_type, build_date
        return PROMVersion(
            raw_string=raw,
            vendor='SGI',
            format_version=None,
            major=groups[0],
            minor=groups[1],
            ip_board=None,  # IP board not in version string
            cpu_type=None,
            build_date=groups[3].strip() if len(groups) > 3 else None
        )

    elif pattern_type == 'saio_header':
        # Groups: version (4D1-X.X), ip_board
        version_parts = groups[0].split('-')
        return PROMVersion(
            raw_string=raw,
            vendor='SGI',
            format_version='SAIO',
            major=version_parts[1] if len(version_parts) > 1 else groups[0],
            minor=None,
            ip_board=groups[1].upper(),
            cpu_type=None,
            build_date=None
        )

    elif pattern_type == 'saio_rev':
        # Groups: version (4D1-X.X), revision
        version_parts = groups[0].split('-')
        return PROMVersion(
            raw_string=raw,
            vendor='SGI',
            format_version='SAIO',
            major=version_parts[1] if len(version_parts) > 1 else groups[0],
            minor=groups[1] if len(groups) > 1 else None,
            ip_board=None,  # IP board not in this format
            cpu_type=None,
            build_date=None
        )

    elif pattern_type == 'ip32_simple':
        # Groups: version only
        return PROMVersion(
            raw_string=raw,
            vendor='SGI',
            format_version='SHDR',
            major=groups[0],
            minor=None,
            ip_board='IP32',  # Inferred from format
            cpu_type=None,
            build_date=None
        )

    # Fallback
    return PROMVersion(
        raw_string=raw,
        vendor='SGI',
        format_version=None,
        major=None,
        minor=None,
        ip_board=None,
        cpu_type=None,
        build_date=None
    )


def extract_ip_board(text: str) -> Optional[str]:
    """Extract IP board identifier from text.

    Args:
        text: String that may contain IP board reference

    Returns:
        IP board string (e.g., "IP24") or None
    """
    match = IP_BOARD_PATTERN.search(text)
    if match:
        return match.group(1).upper()
    return None


def extract_cpu_type(text: str) -> Optional[str]:
    """Extract CPU type from text.

    Args:
        text: String that may contain CPU reference

    Returns:
        CPU type string (e.g., "R4600", "R5000") or None
    """
    match = CPU_PATTERN.search(text)
    if match:
        return match.group(1).upper()
    return None


def find_version_strings(data: bytes, min_length: int = 20) -> List[Tuple[int, str]]:
    """Scan binary data for potential version strings.

    Looks for strings containing version-related keywords.

    Args:
        data: Binary data to scan
        min_length: Minimum string length to consider

    Returns:
        List of (offset, string) tuples
    """
    results = []
    keywords = [b'Version', b'VERSION', b'SGI', b'SAIO', b'IP2', b'IP3', b'IP4']

    # Find all printable strings
    current_string = []
    start_offset = 0

    for i, byte in enumerate(data):
        if 0x20 <= byte <= 0x7e:
            if not current_string:
                start_offset = i
            current_string.append(chr(byte))
        else:
            if len(current_string) >= min_length:
                s = ''.join(current_string)
                # Check if it contains version-related keywords
                s_bytes = s.encode('ascii', errors='ignore')
                for keyword in keywords:
                    if keyword in s_bytes:
                        results.append((start_offset, s))
                        break
            current_string = []

    # Handle string at end of data
    if len(current_string) >= min_length:
        s = ''.join(current_string)
        s_bytes = s.encode('ascii', errors='ignore')
        for keyword in keywords:
            if keyword in s_bytes:
                results.append((start_offset, s))
                break

    return results
