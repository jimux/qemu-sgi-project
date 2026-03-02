"""SGI PROM and firmware image analyzer.

Analyzes SGI firmware images to extract version information,
detect system type, and find notable strings.

Supports multiple firmware formats:
1. MIPS Exception Vector Table format (IP4-IP30)
2. SHDR header format (IP32/O2)
3. SN0/SN1 Container format (IP27/IP35, IO6)
4. Graphics microcode (KONA, Impact, VPro, Voyager)
5. System controller firmware (L1/L2, pbay, MMSC)
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from ..utils.endian import read_u32_be
from ..utils.hexdump import find_strings
from .firmware_types import (
    FirmwareType,
    detect_firmware_type,
    get_type_name,
    get_type_description,
)
from .version import (
    PROMVersion,
    extract_cpu_type,
    extract_ip_board,
    find_version_strings,
    parse_version,
)


@dataclass
class PROMInfo:
    """Analyzed PROM image information."""
    filepath: str
    size: int
    ip_board: Optional[str] = None          # "IP22", "IP24", "IP32", etc.
    system_name: Optional[str] = None       # "Indy", "Indigo 2", "O2", etc.
    version: Optional[PROMVersion] = None   # Parsed version info
    format_type: str = "unknown"            # "exception_vector", "shdr", "unknown"
    endian: str = "big"                     # "big" or "little"
    entry_point: Optional[int] = None       # Reset vector target address
    shdr_info: Optional[Dict] = None        # SHDR-specific fields
    notable_strings: List[Tuple[int, str]] = field(default_factory=list)


@dataclass
class FirmwareInfo:
    """Unified firmware analysis result."""
    filepath: str
    size: int
    firmware_type: FirmwareType
    type_name: str                      # Human-readable type name
    type_description: str               # Short description
    specific_info: Any = None           # Type-specific analysis result
    notable_strings: List[Tuple[int, str]] = field(default_factory=list)


class SGIFirmwareAnalyzer:
    """Unified SGI firmware analyzer.

    This class detects the firmware type and dispatches to the
    appropriate specialized analyzer.
    """

    # Common PROM sizes
    KNOWN_SIZES: Dict[int, str] = {
        5888: '5.7 KB',
        20480: '20 KB',
        32340: '31.6 KB',
        32768: '32 KB',
        65536: '64 KB',
        85700: '83.7 KB',
        127889: '124.9 KB',
        131072: '128 KB',
        262144: '256 KB',
        365880: '357.3 KB',
        524288: '512 KB',
        712704: '696 KB',
        912760: '891.4 KB',
        1023075: '999.1 KB',
        1048576: '1 MB',
        1477560: '1.4 MB',
        2879048: '2.7 MB',
        3723916: '3.6 MB',
    }

    def __init__(self, filepath: str):
        """Initialize firmware analyzer.

        Args:
            filepath: Path to firmware binary file
        """
        self.filepath = filepath
        self.data: bytes = b''
        self._load_file()

    def _load_file(self) -> None:
        """Load firmware file into memory."""
        with open(self.filepath, 'rb') as f:
            self.data = f.read()

    @classmethod
    def from_file(cls, filepath: str) -> 'SGIFirmwareAnalyzer':
        """Create analyzer from file path."""
        return cls(filepath)

    def detect_type(self) -> FirmwareType:
        """Detect firmware type.

        Returns:
            FirmwareType enum value
        """
        filename = os.path.basename(self.filepath)
        return detect_firmware_type(self.data, filename)

    def analyze(self) -> FirmwareInfo:
        """Perform complete firmware analysis.

        Detects the firmware type and dispatches to the appropriate
        specialized analyzer.

        Returns:
            FirmwareInfo with all extracted information
        """
        fw_type = self.detect_type()
        filename = os.path.basename(self.filepath)

        info = FirmwareInfo(
            filepath=self.filepath,
            size=len(self.data),
            firmware_type=fw_type,
            type_name=get_type_name(fw_type),
            type_description=get_type_description(fw_type),
        )

        # Dispatch to specialized analyzer
        if fw_type == FirmwareType.SN0_CONTAINER or fw_type == FirmwareType.SN1_CONTAINER:
            info.specific_info = self._analyze_sn_container()
        elif fw_type == FirmwareType.VOYAGER_X86:
            info.specific_info = self._analyze_voyager()
        elif fw_type == FirmwareType.KONA_ARM:
            info.specific_info = self._analyze_kona()
        elif fw_type == FirmwareType.MIPS_ELF:
            info.specific_info = self._analyze_elf()
        elif fw_type == FirmwareType.IMPACT_MICROCODE:
            info.specific_info = self._analyze_impact()
        elif fw_type == FirmwareType.VPRO_BUZZ:
            info.specific_info = self._analyze_vpro()
        elif fw_type == FirmwareType.SYSCO_68K:
            info.specific_info = self._analyze_sysco()
        elif fw_type == FirmwareType.PBAY_MCU:
            info.specific_info = self._analyze_pbay()
        elif fw_type == FirmwareType.MMSC_X86:
            info.specific_info = self._analyze_mmsc()
        elif fw_type == FirmwareType.SHDR:
            info.specific_info = self._analyze_shdr()
        elif fw_type == FirmwareType.MIPS_EXCEPTION_VECTOR:
            info.specific_info = self._analyze_mips_prom()
        else:
            info.specific_info = self._analyze_unknown()

        # Get notable strings
        info.notable_strings = self._find_notable_strings()

        return info

    def _analyze_sn_container(self) -> Any:
        """Analyze SN0/SN1 container."""
        from .sn_container import analyze_sn_container
        return analyze_sn_container(self.data)

    def _analyze_voyager(self) -> Any:
        """Analyze Voyager/ATI VGA BIOS."""
        from .graphics_microcode import parse_voyager_bios
        return parse_voyager_bios(self.data)

    def _analyze_kona(self) -> Any:
        """Analyze KONA ARM firmware."""
        from .graphics_microcode import parse_kona_firmware
        return parse_kona_firmware(self.data)

    def _analyze_elf(self) -> Any:
        """Analyze MIPS ELF."""
        from .graphics_microcode import parse_elf_header
        return parse_elf_header(self.data)

    def _analyze_impact(self) -> Any:
        """Analyze Impact microcode."""
        from .graphics_microcode import parse_impact_microcode
        filename = os.path.basename(self.filepath)
        return parse_impact_microcode(self.data, filename)

    def _analyze_vpro(self) -> Any:
        """Analyze VPro Buzz microcode."""
        from .graphics_microcode import parse_vpro_buzz
        return parse_vpro_buzz(self.data)

    def _analyze_sysco(self) -> Any:
        """Analyze ESTFBINR system controller."""
        from .sysco_firmware import parse_estfbinr
        filename = os.path.basename(self.filepath)
        return parse_estfbinr(self.data, filename)

    def _analyze_pbay(self) -> Any:
        """Analyze power bay controller."""
        from .sysco_firmware import parse_pbay_firmware
        return parse_pbay_firmware(self.data)

    def _analyze_mmsc(self) -> Any:
        """Analyze MMSC controller."""
        from .sysco_firmware import parse_mmsc_firmware
        return parse_mmsc_firmware(self.data)

    def _analyze_shdr(self) -> Dict:
        """Analyze SHDR format PROM."""
        return self._extract_shdr_info()

    def _analyze_mips_prom(self) -> Dict:
        """Analyze traditional MIPS PROM."""
        version = self._extract_version()
        ip_board = self._detect_ip_board()
        entry_point = self._extract_entry_point()

        return {
            'version': version,
            'ip_board': ip_board,
            'entry_point': entry_point,
            'endian': self._detect_endian(),
        }

    def _analyze_unknown(self) -> Any:
        """Analyze unknown firmware using heuristics."""
        from .heuristics import analyze_unknown_firmware
        return analyze_unknown_firmware(self.data)

    def _extract_shdr_info(self) -> Optional[Dict]:
        """Extract SHDR header information."""
        if len(self.data) < 64 or self.data[8:12] != b'SHDR':
            return None

        load_addr = read_u32_be(self.data, 0x0C)

        # Read module name (at offset 0x14, null-terminated)
        module_name = ""
        offset = 0x14
        while offset < 0x30 and offset < len(self.data):
            if self.data[offset] == 0:
                break
            if 0x20 <= self.data[offset] <= 0x7e:
                module_name += chr(self.data[offset])
            offset += 1

        # Read version string (at offset 0x30)
        version_str = ""
        offset = 0x30
        while offset < 0x40 and offset < len(self.data):
            if self.data[offset] == 0:
                break
            if 0x20 <= self.data[offset] <= 0x7e:
                version_str += chr(self.data[offset])
            offset += 1

        return {
            'load_address': load_addr,
            'module_name': module_name,
            'version_string': version_str,
        }

    def _detect_ip_board(self) -> Optional[str]:
        """Detect IP board from version string or filename."""
        IP_BOARDS = {
            'IP4': 'Professional IRIS',
            'IP6': '4D/20',
            'IP12': '4D/35 / Indigo',
            'IP15': '4D/420',
            'IP17': 'Crimson',
            'IP20': 'Indigo',
            'IP22': 'Indigo 2',
            'IP24': 'Indy',
            'IP26': 'Indigo 2 R8000',
            'IP28': 'Indigo 2 R10000',
            'IP30': 'Octane',
            'IP32': 'O2',
        }

        # First try from filename
        filename = os.path.basename(self.filepath).upper()
        for ip_board in IP_BOARDS.keys():
            if ip_board in filename:
                return ip_board

        # Search in version strings
        version_strings = find_version_strings(self.data)
        for _, s in version_strings:
            ip = extract_ip_board(s)
            if ip:
                return ip

        return None

    def _extract_version(self) -> Optional[PROMVersion]:
        """Find and extract version information."""
        version_strings = find_version_strings(self.data)

        for _, s in version_strings:
            version = parse_version(s)
            if version:
                return version

        return None

    def _extract_entry_point(self) -> Optional[int]:
        """Extract reset vector / entry point address."""
        if len(self.data) < 4:
            return None

        first_word = read_u32_be(self.data, 0)
        opcode = (first_word >> 26) & 0x3F

        if opcode == 0x02:  # J instruction
            target = (first_word & 0x03FFFFFF) << 2
            return target

        if opcode == 0x04:  # B instruction (BEQ)
            offset = first_word & 0xFFFF
            if offset & 0x8000:
                offset = offset - 0x10000
            return 4 + (offset << 2)

        return None

    def _detect_endian(self) -> str:
        """Detect byte order of PROM."""
        if len(self.data) < 4:
            return "big"

        be_word = read_u32_be(self.data, 0)
        le_word = int.from_bytes(self.data[0:4], 'little')

        be_opcode = (be_word >> 26) & 0x3F
        le_opcode = (le_word >> 26) & 0x3F

        valid_opcodes = {0x01, 0x02, 0x04, 0x05}

        if be_opcode in valid_opcodes:
            return "big"
        elif le_opcode in valid_opcodes:
            return "little"

        return "big"

    def _find_notable_strings(self, limit: int = 30) -> List[Tuple[int, str]]:
        """Find notable firmware strings."""
        NOTABLE_PATTERNS = [
            'bootp()', 'Integral', 'Enet', 'SCSI', 'Error',
            'Microcode', 'Firmware', 'Copyright', 'Silicon Graphics',
            'SGI', 'Version', 'version', 'PROM',
        ]

        all_strings = find_strings(self.data, min_length=8)
        notable = []

        for offset, s in all_strings:
            for pattern in NOTABLE_PATTERNS:
                if pattern.lower() in s.lower():
                    notable.append((offset, s))
                    break

            if len(notable) >= limit:
                break

        return notable

    def format_report(self) -> str:
        """Generate human-readable analysis report.

        Returns:
            Formatted report string
        """
        info = self.analyze()
        lines = []

        lines.append("=== SGI Firmware Analysis ===")
        lines.append("")

        # File info
        filename = os.path.basename(info.filepath)
        size_str = self.KNOWN_SIZES.get(info.size, f"{info.size} bytes")
        lines.append(f"File: {filename}")
        lines.append(f"Size: {size_str}")
        lines.append(f"Type: {info.type_name}")
        lines.append("")

        # Type-specific report
        specific_report = self._format_specific_report(info)
        if specific_report:
            lines.append(specific_report)

        # Notable strings (only if not already shown by specific report)
        if info.notable_strings and info.firmware_type == FirmwareType.UNKNOWN:
            lines.append("")
            lines.append("Notable Strings:")
            for offset, s in info.notable_strings[:15]:
                display_str = s[:60] + "..." if len(s) > 60 else s
                lines.append(f"  0x{offset:06x}: {display_str}")

        return "\n".join(lines)

    def _format_specific_report(self, info: FirmwareInfo) -> str:
        """Format type-specific part of report."""
        if info.specific_info is None:
            return ""

        fw_type = info.firmware_type

        if fw_type in (FirmwareType.SN0_CONTAINER, FirmwareType.SN1_CONTAINER):
            from .sn_container import format_sn_container_report
            if info.specific_info:
                return format_sn_container_report(info.specific_info)

        elif fw_type == FirmwareType.VOYAGER_X86:
            from .graphics_microcode import format_voyager_report
            return format_voyager_report(info.specific_info)

        elif fw_type == FirmwareType.KONA_ARM:
            from .graphics_microcode import format_kona_report
            return format_kona_report(info.specific_info)

        elif fw_type == FirmwareType.MIPS_ELF:
            from .graphics_microcode import format_elf_report
            return format_elf_report(info.specific_info)

        elif fw_type == FirmwareType.IMPACT_MICROCODE:
            from .graphics_microcode import format_impact_report
            return format_impact_report(info.specific_info)

        elif fw_type == FirmwareType.VPRO_BUZZ:
            from .graphics_microcode import format_vpro_report
            return format_vpro_report(info.specific_info)

        elif fw_type == FirmwareType.SYSCO_68K:
            from .sysco_firmware import format_estfbinr_report
            return format_estfbinr_report(info.specific_info)

        elif fw_type == FirmwareType.PBAY_MCU:
            from .sysco_firmware import format_pbay_report
            return format_pbay_report(info.specific_info)

        elif fw_type == FirmwareType.MMSC_X86:
            from .sysco_firmware import format_mmsc_report
            return format_mmsc_report(info.specific_info)

        elif fw_type == FirmwareType.SHDR:
            return self._format_shdr_report(info.specific_info)

        elif fw_type == FirmwareType.MIPS_EXCEPTION_VECTOR:
            return self._format_mips_prom_report(info.specific_info)

        elif fw_type == FirmwareType.UNKNOWN:
            from .heuristics import format_heuristic_report
            return format_heuristic_report(info.specific_info)

        return ""

    def _format_shdr_report(self, shdr_info: Dict) -> str:
        """Format SHDR-specific report section."""
        lines = []
        lines.append("SHDR Header:")
        lines.append(f"  Module:          {shdr_info.get('module_name', 'N/A')}")
        lines.append(f"  Load Address:    0x{shdr_info.get('load_address', 0):08x}")
        if shdr_info.get('version_string'):
            lines.append(f"  Version:         {shdr_info['version_string']}")
        return "\n".join(lines)

    def _format_mips_prom_report(self, mips_info: Dict) -> str:
        """Format MIPS PROM report section."""
        lines = []

        if mips_info.get('ip_board'):
            IP_BOARDS = {
                'IP4': 'Professional IRIS', 'IP6': '4D/20',
                'IP12': '4D/35 / Indigo', 'IP15': '4D/420',
                'IP17': 'Crimson', 'IP20': 'Indigo',
                'IP22': 'Indigo 2', 'IP24': 'Indy',
                'IP26': 'Indigo 2 R8000', 'IP28': 'Indigo 2 R10000',
                'IP30': 'Octane', 'IP32': 'O2',
            }
            ip = mips_info['ip_board']
            system = IP_BOARDS.get(ip, 'Unknown')
            lines.append("System Information:")
            lines.append(f"  IP Board:        {ip}")
            lines.append(f"  System:          {system}")
            lines.append(f"  Endianness:      {mips_info.get('endian', 'big').capitalize()}-endian")

        if mips_info.get('entry_point') is not None:
            lines.append("")
            lines.append("Entry Points:")
            lines.append(f"  Reset Vector:    0x{mips_info['entry_point']:08x}")

        version = mips_info.get('version')
        if version:
            lines.append("")
            lines.append("Version:")
            lines.append(f"  Full String:     {version.raw_string}")
            if version.major:
                lines.append(f"  Major:           {version.major}")
            if version.minor:
                lines.append(f"  Revision:        {version.minor}")
            if version.cpu_type:
                lines.append(f"  CPU Support:     {version.cpu_type}")
            if version.build_date:
                lines.append(f"  Build Date:      {version.build_date}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Convert analysis to dictionary for JSON output.

        Returns:
            Dictionary representation of firmware info
        """
        info = self.analyze()

        result = {
            'file': os.path.basename(info.filepath),
            'path': info.filepath,
            'size': info.size,
            'size_formatted': self.KNOWN_SIZES.get(info.size, f"{info.size} bytes"),
            'firmware_type': info.firmware_type.name,
            'type_name': info.type_name,
            'type_description': info.type_description,
        }

        # Add type-specific data
        result['details'] = self._specific_info_to_dict(info)

        if info.notable_strings:
            result['notable_strings'] = [
                {'offset': offset, 'string': s}
                for offset, s in info.notable_strings[:20]
            ]

        return result

    def _specific_info_to_dict(self, info: FirmwareInfo) -> dict:
        """Convert type-specific info to dictionary."""
        if info.specific_info is None:
            return {}

        fw_type = info.firmware_type

        if fw_type in (FirmwareType.SN0_CONTAINER, FirmwareType.SN1_CONTAINER):
            from .sn_container import sn_container_to_dict
            if info.specific_info:
                return sn_container_to_dict(info.specific_info)

        elif fw_type == FirmwareType.VOYAGER_X86:
            from .graphics_microcode import VoyagerBIOSInfo
            v = info.specific_info
            if isinstance(v, VoyagerBIOSInfo):
                return {
                    'card_name': v.card_name,
                    'chip_name': v.chip_name,
                    'part_number': v.part_number,
                    'version': v.version,
                    'build_date': v.build_date,
                    'copyright': v.copyright,
                }

        elif fw_type == FirmwareType.SYSCO_68K:
            from .sysco_firmware import ESTFBINRInfo
            e = info.specific_info
            if isinstance(e, ESTFBINRInfo):
                return {
                    'controller_type': e.controller_type,
                    'architecture': e.architecture,
                    'version_string': e.version_string,
                    'boot_loader': e.boot_loader,
                }

        elif fw_type == FirmwareType.PBAY_MCU:
            from .sysco_firmware import PBayInfo
            p = info.specific_info
            if isinstance(p, PBayInfo):
                return {
                    'author': p.author,
                    'architecture': p.architecture,
                }

        elif fw_type == FirmwareType.SHDR:
            return info.specific_info if isinstance(info.specific_info, dict) else {}

        elif fw_type == FirmwareType.MIPS_EXCEPTION_VECTOR:
            mips_info = info.specific_info
            if isinstance(mips_info, dict):
                result = {
                    'ip_board': mips_info.get('ip_board'),
                    'endian': mips_info.get('endian'),
                    'entry_point': mips_info.get('entry_point'),
                }
                if mips_info.get('version'):
                    v = mips_info['version']
                    result['version'] = {
                        'raw': v.raw_string,
                        'major': v.major,
                        'minor': v.minor,
                        'build_date': v.build_date,
                    }
                return result

        elif fw_type == FirmwareType.UNKNOWN:
            from .heuristics import HeuristicResult
            h = info.specific_info
            if isinstance(h, HeuristicResult):
                result = {
                    'entropy': h.entropy,
                    'entropy_description': h.entropy_description,
                }
                if h.architecture:
                    result['architecture'] = {
                        'name': h.architecture.name,
                        'confidence': h.architecture.confidence,
                        'endian': h.architecture.endian,
                    }
                if h.strings:
                    result['detected_strings'] = [
                        {'offset': s.offset, 'text': s.text, 'category': s.category}
                        for s in h.strings[:10]
                    ]
                return result

        return {}


# Backwards compatibility: keep SGIPROMImage as alias
class SGIPROMImage(SGIFirmwareAnalyzer):
    """Legacy class for backwards compatibility.

    Use SGIFirmwareAnalyzer for new code.
    """

    # Known IP board to system name mapping
    IP_BOARDS: Dict[str, str] = {
        'IP4': 'Professional IRIS',
        'IP6': '4D/20',
        'IP12': '4D/35 / Indigo',
        'IP15': '4D/420',
        'IP17': 'Crimson',
        'IP20': 'Indigo',
        'IP22': 'Indigo 2',
        'IP24': 'Indy',
        'IP26': 'Indigo 2 R8000',
        'IP28': 'Indigo 2 R10000',
        'IP30': 'Octane',
        'IP32': 'O2',
    }

    # SHDR magic value
    SHDR_MAGIC = b'SHDR'

    def detect_format(self) -> str:
        """Detect PROM format type (legacy method)."""
        fw_type = self.detect_type()
        if fw_type == FirmwareType.SHDR:
            return "shdr"
        elif fw_type == FirmwareType.MIPS_EXCEPTION_VECTOR:
            return "exception_vector"
        elif fw_type in (FirmwareType.SN0_CONTAINER, FirmwareType.SN1_CONTAINER):
            return "sn_container"
        else:
            return "unknown"

    def detect_endian(self) -> str:
        """Detect byte order of PROM."""
        return self._detect_endian()

    def detect_ip_board(self) -> Optional[str]:
        """Detect IP board from version string or filename."""
        return self._detect_ip_board()

    def extract_version(self) -> Optional[PROMVersion]:
        """Find and extract version information."""
        return self._extract_version()

    def extract_shdr_info(self) -> Optional[Dict]:
        """Extract SHDR header information."""
        return self._extract_shdr_info()

    def extract_entry_point(self) -> Optional[int]:
        """Extract reset vector / entry point address."""
        return self._extract_entry_point()

    def find_notable_strings(self, limit: int = 50) -> List[Tuple[int, str]]:
        """Find notable firmware strings."""
        return self._find_notable_strings(limit)

    def _format_type_name(self, format_type: str) -> str:
        """Convert format type to display name."""
        names = {
            'exception_vector': 'MIPS Exception Vector Table',
            'shdr': 'SHDR Header (O2/IP32)',
            'sn_container': 'SN0/SN1 Container',
            'unknown': 'Unknown',
        }
        return names.get(format_type, format_type)
