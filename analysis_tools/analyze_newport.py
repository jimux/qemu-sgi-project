#!/usr/bin/env python3
"""
Newport Graphics Hardware Analysis Script

Parses IRIX header files to extract and document SGI Newport graphics
hardware register definitions, bit fields, and macros.

Sources analyzed:
- ng1hw.h  - REX3 hardware register definitions
- ng1.h    - Newport driver interface
- vc2.h    - VC2 video timing controller
- xmap9.h  - XMAP9 display generator
- ng1_cmap.h - CMAP palette mapper
"""

import re
import os
import sys
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Default path to IRIX headers
DEFAULT_IRIX_PATH = "extracted/dist_contents/812-0279-003_IRIX_5.2_for_Indy_R4600SC-XZ_and_Presenter/usr/include/sys"

@dataclass
class BitField:
    """Represents a bit field within a register"""
    name: str
    value: int
    shift: int = 0
    mask: int = 0
    description: str = ""

@dataclass
class Register:
    """Represents a hardware register"""
    name: str
    offset: int
    size: int = 32
    description: str = ""
    fields: List[BitField] = field(default_factory=list)

@dataclass
class StructMember:
    """Represents a struct member"""
    name: str
    type: str
    offset: int
    comment: str = ""

class NewportAnalyzer:
    """Analyzes Newport graphics hardware from IRIX headers"""

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        self.defines: Dict[str, Tuple[int, str]] = {}  # name -> (value, comment)
        self.structs: Dict[str, List[StructMember]] = {}
        self.macros: Dict[str, str] = {}

    def parse_define(self, line: str) -> Optional[Tuple[str, int, str]]:
        """Parse a #define line, return (name, value, comment) or None"""
        # Match: #define NAME value /* comment */
        # or: #define NAME (expression)
        match = re.match(r'#\s*define\s+(\w+)\s+(.+?)(?:\s*/\*(.+?)\*/)?$', line.strip())
        if not match:
            return None

        name = match.group(1)
        value_str = match.group(2).strip()
        comment = match.group(3).strip() if match.group(3) else ""

        # Try to evaluate the value
        try:
            # Handle common patterns
            value_str = value_str.replace('BIT(', '(1 << ')
            value_str = re.sub(r'\b0x([0-9a-fA-F]+)\b', r'0x\1', value_str)

            # Replace defined constants with their values
            for def_name, (def_val, _) in self.defines.items():
                value_str = re.sub(rf'\b{def_name}\b', str(def_val), value_str)

            value = eval(value_str)
            if isinstance(value, (int, float)):
                return (name, int(value), comment)
        except:
            pass

        return None

    def parse_file(self, filename: str) -> Dict[str, any]:
        """Parse a header file and extract definitions"""
        filepath = self.base_path / filename
        if not filepath.exists():
            print(f"Warning: {filepath} not found")
            return {}

        results = {
            'defines': {},
            'structs': {},
            'macros': {}
        }

        with open(filepath, 'r') as f:
            content = f.read()
            lines = content.split('\n')

        in_struct = False
        struct_name = ""
        struct_members = []
        current_offset = 0

        for i, line in enumerate(lines):
            # Skip empty lines and comments
            stripped = line.strip()
            if not stripped or stripped.startswith('//'):
                continue

            # Parse #define
            if stripped.startswith('#') and 'define' in stripped:
                result = self.parse_define(stripped)
                if result:
                    name, value, comment = result
                    self.defines[name] = (value, comment)
                    results['defines'][name] = {'value': value, 'comment': comment}

            # Parse struct definitions
            if 'typedef struct' in stripped or (stripped.startswith('struct') and '{' in content[content.find(stripped):content.find(stripped)+200]):
                in_struct = True
                struct_match = re.search(r'struct\s+(\w+)', stripped)
                if struct_match:
                    struct_name = struct_match.group(1)
                struct_members = []
                current_offset = 0

            elif in_struct:
                if '}' in stripped:
                    # End of struct
                    typedef_match = re.search(r'}\s*(\w+)', stripped)
                    if typedef_match:
                        struct_name = typedef_match.group(1)
                    if struct_name:
                        self.structs[struct_name] = struct_members
                        results['structs'][struct_name] = struct_members
                    in_struct = False
                elif stripped.startswith('char') and '_pad' in stripped:
                    # Padding - extract size
                    pad_match = re.search(r'\[([^\]]+)\]', stripped)
                    if pad_match:
                        try:
                            pad_expr = pad_match.group(1)
                            pad_expr = pad_expr.replace('sizeof(struct rex3regs)', '0x248')
                            pad_expr = pad_expr.replace('sizeof(struct configregs)', '0x44')
                            pad_size = eval(pad_expr)
                            current_offset += pad_size
                        except:
                            pass
                elif stripped and not stripped.startswith('/*') and not stripped.startswith('*'):
                    # Regular struct member
                    member_match = re.match(r'([\w\s\*]+?)\s+(\w+)\s*;(?:\s*/\*(.+?)\*/)?', stripped)
                    if member_match:
                        member_type = member_match.group(1).strip()
                        member_name = member_match.group(2)
                        member_comment = member_match.group(3).strip() if member_match.group(3) else ""

                        # Extract offset from comment if present
                        offset_match = re.search(r'0x([0-9a-fA-F]+)', member_comment)
                        if offset_match:
                            current_offset = int(offset_match.group(1), 16)

                        member = StructMember(
                            name=member_name,
                            type=member_type,
                            offset=current_offset,
                            comment=member_comment
                        )
                        struct_members.append(member)

                        # Advance offset based on type
                        if 'long' in member_type or 'float_long' in member_type:
                            current_offset += 4
                        elif 'short' in member_type:
                            current_offset += 2
                        elif 'char' in member_type:
                            current_offset += 1

        return results

    def analyze_ng1hw(self) -> Dict:
        """Analyze ng1hw.h - REX3 hardware definitions"""
        results = self.parse_file("ng1hw.h")

        # Categorize defines
        categorized = {
            'drawmode0': {},
            'drawmode1': {},
            'status': {},
            'config': {},
            'dcb': {},
            'clipmode': {},
            'lsmode': {},
            'other': {}
        }

        for name, info in results.get('defines', {}).items():
            value = info['value']
            comment = info['comment']

            if name.startswith('DM0_'):
                categorized['drawmode0'][name] = info
            elif name.startswith('DM1_'):
                categorized['drawmode1'][name] = info
            elif 'STATUS' in name or name in ['GFXBUSY', 'BACKBUSY', 'VRINT', 'VIDEOINT']:
                categorized['status'][name] = info
            elif 'CONFIG' in name or 'TIMEOUT' in name or 'VREFRESH' in name:
                categorized['config'][name] = info
            elif name.startswith('DCB_'):
                categorized['dcb'][name] = info
            elif name.startswith('SMASK') or 'CLIP' in name:
                categorized['clipmode'][name] = info
            elif name.startswith('LS'):
                categorized['lsmode'][name] = info
            else:
                categorized['other'][name] = info

        results['categorized'] = categorized
        return results

    def analyze_vc2(self) -> Dict:
        """Analyze vc2.h - Video Controller definitions"""
        results = self.parse_file("vc2.h")

        categorized = {
            'crs': {},
            'index': {},
            'config': {},
            'dc_control': {},
            'memory_map': {},
            'other': {}
        }

        for name, info in results.get('defines', {}).items():
            if name.startswith('VC2_CRS_'):
                categorized['crs'][name] = info
            elif name.startswith('VC2_') and any(x in name for x in ['ENTRY', 'LOC', 'PTR', 'LEN', 'CTR', 'ADDR', 'FRAME', 'LINE', 'CURSOR', 'CONFIG']):
                if 'TAB' in name or 'ADDR' in name or 'RAM' in name:
                    categorized['memory_map'][name] = info
                else:
                    categorized['index'][name] = info
            elif name.startswith('VC2_ENA_') or 'CURS' in name or 'GENLOCK' in name:
                categorized['dc_control'][name] = info
            elif 'RESET' in name or 'CLOCK' in name or 'ERR' in name or 'REVISION' in name:
                categorized['config'][name] = info
            else:
                categorized['other'][name] = info

        results['categorized'] = categorized
        return results

    def analyze_xmap9(self) -> Dict:
        """Analyze xmap9.h - Display generator definitions"""
        results = self.parse_file("xmap9.h")

        categorized = {
            'crs': {},
            'config': {},
            'mode': {},
            'fifo': {},
            'other': {}
        }

        for name, info in results.get('defines', {}).items():
            if name.startswith('XM9_CRS_'):
                categorized['crs'][name] = info
            elif name.startswith('XM9_FIFO'):
                categorized['fifo'][name] = info
            elif any(x in name for x in ['PUPMODE', 'ODD_PIXEL', 'BITPLANES', 'VIDEO_RGBMAP', 'EXPRESS', 'OPTION']):
                categorized['config'][name] = info
            elif any(x in name for x in ['BUF_SEL', 'GAMMA', 'CMAP', 'PIX', 'VIDEO', 'ALPHA', 'AUX', 'DITHER']):
                categorized['mode'][name] = info
            else:
                categorized['other'][name] = info

        results['categorized'] = categorized
        return results

    def analyze_cmap(self) -> Dict:
        """Analyze ng1_cmap.h - Palette mapper definitions"""
        results = self.parse_file("ng1_cmap.h")

        categorized = {
            'cmap_crs': {},
            'ramdac_crs': {},
            'ramdac_regs': {},
            'other': {}
        }

        for name, info in results.get('defines', {}).items():
            if name.startswith('CMAP_CRS_'):
                categorized['cmap_crs'][name] = info
            elif name.startswith('RDAC_CRS_'):
                categorized['ramdac_crs'][name] = info
            elif name.startswith('RDAC_'):
                categorized['ramdac_regs'][name] = info
            else:
                categorized['other'][name] = info

        results['categorized'] = categorized
        return results

    def generate_report(self) -> str:
        """Generate a comprehensive analysis report"""
        report = []
        report.append("=" * 80)
        report.append("SGI Newport Graphics Hardware Analysis")
        report.append("=" * 80)
        report.append("")

        # REX3
        ng1hw = self.analyze_ng1hw()
        report.append("\n" + "=" * 80)
        report.append("REX3 Raster Engine (ng1hw.h)")
        report.append("=" * 80)

        if 'structs' in ng1hw:
            report.append("\n--- REX3 Register Structure ---")
            for struct_name, members in ng1hw['structs'].items():
                report.append(f"\nStruct: {struct_name}")
                for member in members:
                    report.append(f"  0x{member.offset:04x}: {member.name:20s} ({member.type}) - {member.comment}")

        for category, defines in ng1hw.get('categorized', {}).items():
            if defines:
                report.append(f"\n--- {category.upper()} Definitions ---")
                for name, info in sorted(defines.items(), key=lambda x: x[1]['value']):
                    report.append(f"  {name:40s} = 0x{info['value']:08x}  {info['comment']}")

        # VC2
        vc2 = self.analyze_vc2()
        report.append("\n" + "=" * 80)
        report.append("VC2 Video Timing Controller (vc2.h)")
        report.append("=" * 80)

        for category, defines in vc2.get('categorized', {}).items():
            if defines:
                report.append(f"\n--- {category.upper()} Definitions ---")
                for name, info in sorted(defines.items(), key=lambda x: x[1]['value']):
                    report.append(f"  {name:40s} = 0x{info['value']:08x}  {info['comment']}")

        # XMAP9
        xmap9 = self.analyze_xmap9()
        report.append("\n" + "=" * 80)
        report.append("XMAP9 Display Generator (xmap9.h)")
        report.append("=" * 80)

        for category, defines in xmap9.get('categorized', {}).items():
            if defines:
                report.append(f"\n--- {category.upper()} Definitions ---")
                for name, info in sorted(defines.items(), key=lambda x: x[1]['value']):
                    report.append(f"  {name:40s} = 0x{info['value']:08x}  {info['comment']}")

        # CMAP
        cmap = self.analyze_cmap()
        report.append("\n" + "=" * 80)
        report.append("CMAP Palette Mapper (ng1_cmap.h)")
        report.append("=" * 80)

        for category, defines in cmap.get('categorized', {}).items():
            if defines:
                report.append(f"\n--- {category.upper()} Definitions ---")
                for name, info in sorted(defines.items(), key=lambda x: x[1]['value']):
                    report.append(f"  {name:40s} = 0x{info['value']:08x}  {info['comment']}")

        return "\n".join(report)

    def print_summary(self):
        """Print a summary of all analyzed data"""
        print(self.generate_report())


def main():
    # Determine base path
    script_dir = Path(__file__).parent
    default_path = script_dir / DEFAULT_IRIX_PATH

    if len(sys.argv) > 1:
        base_path = Path(sys.argv[1])
    elif default_path.exists():
        base_path = default_path
    else:
        # Try to find headers in any extracted IRIX
        for path in script_dir.glob("extracted/*/usr/include/sys"):
            if (path / "ng1hw.h").exists():
                base_path = path
                break
        else:
            print("Error: Could not find IRIX header files")
            print(f"Usage: {sys.argv[0]} [path_to_sys_headers]")
            sys.exit(1)

    print(f"Analyzing headers in: {base_path}")
    analyzer = NewportAnalyzer(base_path)
    analyzer.print_summary()


if __name__ == "__main__":
    main()
