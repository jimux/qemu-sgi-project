"""SGI PROM environment variable parser.

SGI systems store environment variables in battery-backed RAM. The format
varies by system but generally uses fixed offsets for known variables
and null-terminated strings for values.

This module handles the IP22/IP24 (Indy/Indigo2) NVRAM format.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
import re

from ..utils.endian import read_cstring
from ..utils.hexdump import format_mac_address, find_strings


@dataclass
class SGIEnvironment:
    """Decoded SGI PROM environment variables."""
    eaddr: Optional[str] = None          # Ethernet MAC address
    netaddr: Optional[str] = None        # IP address
    dbaud: Optional[str] = None          # Debug serial baud rate
    timezone: Optional[str] = None       # TimeZone (e.g., "PST8PDT")
    console: Optional[str] = None        # Console device
    bootfile: Optional[str] = None       # Default boot file
    osloader: Optional[str] = None       # OS Loader path
    system_partition: Optional[str] = None  # SystemPartition
    os_load_partition: Optional[str] = None  # OSLoadPartition
    autoload: Optional[str] = None       # AutoLoad (Yes/No)
    cpufreq: Optional[str] = None        # CPU frequency
    gfx: Optional[str] = None            # Graphics type
    monitor: Optional[str] = None        # Monitor type

    # Raw strings found in NVRAM
    raw_strings: List[Tuple[int, str]] = field(default_factory=list)

    # Unknown/additional variables
    extra: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = {}
        for key, value in self.__dict__.items():
            if value is not None and key not in ('raw_strings', 'extra'):
                result[key] = value
        result.update(self.extra)
        return result


class SGIPROMEnvironment:
    """Parser for SGI PROM environment variables in NVRAM.

    The IP22/IP24 NVRAM layout has environment data stored at fixed offsets
    within the DS1386 user RAM area. The user RAM starts at offset 0x0E.

    Known fixed offsets (relative to start of file):
        0x40-0x41: Checksum
        0x50+:     Environment strings region 1
        0xB4:      Baud rate (null-terminated string)
        0xB9:      Timezone (may have leading digit, e.g., "0PST8PDT")
        0x13A:     MAC address (6 bytes, raw binary)
    """

    # Fixed offset for MAC address (relative to file start)
    # The MAC address is at offset 0x13A (not 0x138)
    MAC_OFFSET = 0x13A

    # Environment strings region
    ENV_REGION_START = 0x50
    ENV_REGION_END = 0x140

    # Known variable patterns to search for
    VARIABLE_PATTERNS = {
        'dbaud': re.compile(r'^(9600|19200|38400|57600|115200)$'),
        # Timezone format: e.g., PST8PDT, EST5EDT, or with leading digit like 0PST8PDT
        'timezone': re.compile(r'^\d?[A-Z]{3,4}\d?[A-Z]{0,4}$'),
        'autoload': re.compile(r'^(Yes|No|yes|no|YES|NO)$'),
        'console': re.compile(r'^(graphics|d|d1|d2)$'),
    }

    def __init__(self, data: bytes, user_ram_offset: int = 0x0E):
        """Initialize with raw NVRAM data.

        Args:
            data: Complete NVRAM file contents
            user_ram_offset: Offset where user RAM starts (DS1386 = 0x0E)
        """
        self.data = data
        self.user_ram_offset = user_ram_offset

    @classmethod
    def from_file(cls, filepath: str) -> 'SGIPROMEnvironment':
        """Load from a file."""
        with open(filepath, 'rb') as f:
            data = f.read()
        return cls(data)

    def get_mac_address(self) -> Optional[str]:
        """Extract the MAC address from the known fixed offset."""
        if len(self.data) >= self.MAC_OFFSET + 6:
            mac_bytes = self.data[self.MAC_OFFSET:self.MAC_OFFSET + 6]
            # Validate - SGI MAC addresses start with 08:00:69
            if mac_bytes[0] == 0x08 and mac_bytes[1] == 0x00 and mac_bytes[2] == 0x69:
                return format_mac_address(mac_bytes)
            # Also check if it's a valid non-zero MAC
            if any(b != 0 for b in mac_bytes):
                return format_mac_address(mac_bytes)
        return None

    def find_environment_strings(self) -> List[Tuple[int, str]]:
        """Find all readable strings in the environment region."""
        region = self.data[self.ENV_REGION_START:self.ENV_REGION_END]
        strings = find_strings(region, min_length=2)
        # Adjust offsets to be relative to file start
        return [(offset + self.ENV_REGION_START, s) for offset, s in strings]

    def extract_baud_rate(self) -> Optional[str]:
        """Extract baud rate from known offset 0xB4."""
        if len(self.data) > 0xB8:
            # Read null-terminated string starting at 0xB4
            s, _ = read_cstring(self.data, 0xB4, max_len=8)
            if s.isdigit() and len(s) >= 4:
                return s
        return None

    def extract_timezone(self) -> Optional[str]:
        """Extract timezone from the environment region."""
        # Look for timezone pattern after baud rate
        if len(self.data) > 0xC0:
            # Timezone often follows baud rate
            for offset in range(0xB8, 0xC8):
                if offset < len(self.data):
                    s, _ = read_cstring(self.data, offset, max_len=16)
                    if s and self.VARIABLE_PATTERNS['timezone'].match(s):
                        return s
        return None

    def parse(self) -> SGIEnvironment:
        """Parse all environment variables."""
        env = SGIEnvironment()

        # Extract known fixed-location values
        env.eaddr = self.get_mac_address()
        env.dbaud = self.extract_baud_rate()
        env.timezone = self.extract_timezone()

        # Find all strings in the environment region
        env.raw_strings = self.find_environment_strings()

        # Try to identify variables by content patterns
        for offset, s in env.raw_strings:
            if self.VARIABLE_PATTERNS['autoload'].match(s):
                env.autoload = s
            elif s.startswith('dksc') or s.startswith('scsi('):
                if 'partition' not in s.lower():
                    # Likely a boot device pattern
                    if not env.system_partition:
                        env.extra['boot_pattern'] = s

        return env

    def format_report(self) -> str:
        """Generate a human-readable report."""
        env = self.parse()
        lines = []
        lines.append("=== SGI PROM Environment ===")
        lines.append("")

        lines.append("Decoded Variables:")
        if env.eaddr:
            lines.append(f"  eaddr (MAC):    {env.eaddr}")
        if env.dbaud:
            lines.append(f"  dbaud:          {env.dbaud}")
        if env.timezone:
            lines.append(f"  TimeZone:       {env.timezone}")
        if env.autoload:
            lines.append(f"  AutoLoad:       {env.autoload}")
        if env.console:
            lines.append(f"  console:        {env.console}")

        if env.extra:
            lines.append("")
            lines.append("Additional Values:")
            for key, value in env.extra.items():
                lines.append(f"  {key}: {value}")

        lines.append("")
        lines.append("Raw Strings Found:")
        for offset, s in env.raw_strings:
            if len(s) >= 3:  # Only show meaningful strings
                lines.append(f"  0x{offset:04x}: \"{s}\"")

        return "\n".join(lines)


class SGINVRAMAnalyzer:
    """Combined analyzer for SGI NVRAM files.

    Combines DS1386 RTC decoding with environment variable parsing.
    """

    def __init__(self, data: bytes):
        """Initialize with raw NVRAM data."""
        self.data = data
        # Import here to avoid circular imports
        from .ds1386 import DS1386
        self.rtc = DS1386(data)
        self.env = SGIPROMEnvironment(data)

    @classmethod
    def from_file(cls, filepath: str) -> 'SGINVRAMAnalyzer':
        """Load from a file."""
        with open(filepath, 'rb') as f:
            data = f.read()
        return cls(data)

    def analyze(self) -> Dict[str, Any]:
        """Perform complete analysis."""
        return {
            'rtc': self.rtc.analyze(),
            'environment': self.env.parse().to_dict(),
            'file_size': len(self.data),
        }

    def format_report(self) -> str:
        """Generate a complete human-readable report."""
        lines = []

        lines.append("=" * 50)
        lines.append("SGI NVRAM Analysis")
        lines.append("=" * 50)
        lines.append("")

        lines.append(self.rtc.format_report())
        lines.append("")
        lines.append(self.env.format_report())

        return "\n".join(lines)
