"""Command-line interface for SGI binary analysis toolkit.

Usage:
    python -m analysis_tools nvram <file>       Analyze NVRAM/RTC file
    python -m analysis_tools nvram --json <file> Output as JSON
    python -m analysis_tools prom <file>        Analyze PROM/firmware image
    python -m analysis_tools prom --all <dir>   Analyze all firmware in directory
    python -m analysis_tools hexdump <file>     Hex dump a file
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, List


def cmd_nvram(args: argparse.Namespace) -> int:
    """Analyze SGI NVRAM file."""
    from .nvram.prom_env import SGINVRAMAnalyzer

    filepath = Path(args.file)
    if not filepath.exists():
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        return 1

    try:
        analyzer = SGINVRAMAnalyzer.from_file(str(filepath))

        if args.json:
            # JSON output
            result = analyzer.analyze()
            # Convert non-serializable objects
            if 'rtc' in result and 'time' in result['rtc']:
                result['rtc']['time'] = str(result['rtc']['time'])
            print(json.dumps(result, indent=2))
        else:
            # Human-readable output
            print(analyzer.format_report())

        return 0

    except Exception as e:
        print(f"Error analyzing file: {e}", file=sys.stderr)
        if args.debug:
            import traceback
            traceback.print_exc()
        return 1


def cmd_prom(args: argparse.Namespace) -> int:
    """Analyze SGI PROM/firmware image."""
    from .prom.prom_image import SGIFirmwareAnalyzer

    # Handle --all option for directory scanning
    if args.all:
        return cmd_prom_batch(args)

    filepath = Path(args.file)
    if not filepath.exists():
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        return 1

    try:
        analyzer = SGIFirmwareAnalyzer.from_file(str(filepath))

        if args.json:
            # JSON output
            result = analyzer.to_dict()
            print(json.dumps(result, indent=2))
        elif args.strings:
            # Show all strings found
            from .utils.hexdump import find_strings
            with open(filepath, 'rb') as f:
                data = f.read()
            strings = find_strings(data, min_length=args.min_length or 6)
            for offset, s in strings:
                print(f"0x{offset:06x}: {s}")
        else:
            # Human-readable output
            print(analyzer.format_report())

        return 0

    except Exception as e:
        print(f"Error analyzing file: {e}", file=sys.stderr)
        if args.debug:
            import traceback
            traceback.print_exc()
        return 1


def cmd_prom_batch(args: argparse.Namespace) -> int:
    """Analyze all firmware files in a directory."""
    from .prom.prom_image import SGIFirmwareAnalyzer

    dirpath = Path(args.file)
    if not dirpath.is_dir():
        print(f"Error: Not a directory: {dirpath}", file=sys.stderr)
        return 1

    # Find all firmware files
    firmware_files = find_firmware_files(dirpath)

    if not firmware_files:
        print(f"No firmware files found in {dirpath}", file=sys.stderr)
        return 1

    results = []
    errors = []

    for filepath in firmware_files:
        try:
            analyzer = SGIFirmwareAnalyzer.from_file(str(filepath))
            result = analyzer.to_dict()
            results.append(result)

            if not args.json:
                # Print summary for each file
                rel_path = filepath.relative_to(dirpath)
                fw_type = result.get('type_name', 'Unknown')
                size = result.get('size_formatted', f"{result.get('size', 0)} bytes")
                print(f"{rel_path}: {fw_type} ({size})")

        except Exception as e:
            rel_path = filepath.relative_to(dirpath)
            errors.append({'file': str(rel_path), 'error': str(e)})
            if not args.json:
                print(f"{rel_path}: Error - {e}", file=sys.stderr)

    if args.json:
        output = {
            'directory': str(dirpath),
            'firmware_count': len(results),
            'error_count': len(errors),
            'firmware': results,
            'errors': errors,
        }
        print(json.dumps(output, indent=2))
    else:
        print()
        print(f"Analyzed {len(results)} firmware files, {len(errors)} errors")

    return 0 if not errors else 1


def find_firmware_files(directory: Path) -> List[Path]:
    """Find all firmware files in a directory.

    Args:
        directory: Directory to search

    Returns:
        List of firmware file paths
    """
    firmware_files = []

    # Common firmware extensions and patterns
    patterns = [
        '*.bin', '*.img', '*.image', '*.rom', '*.prom',
        '*prom*', '*prom', 'vs2prom',
    ]

    # Walk the directory tree
    for pattern in patterns:
        for filepath in directory.rglob(pattern):
            if filepath.is_file() and filepath not in firmware_files:
                # Skip common non-firmware files
                if filepath.suffix.lower() in ('.txt', '.md', '.json', '.gz', '.zip'):
                    continue
                if filepath.name.startswith('.'):
                    continue
                firmware_files.append(filepath)

    # Sort by path
    firmware_files.sort()

    return firmware_files


def cmd_hexdump(args: argparse.Namespace) -> int:
    """Hex dump a file."""
    from .utils.hexdump import hexdump

    filepath = Path(args.file)
    if not filepath.exists():
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        return 1

    try:
        with open(filepath, 'rb') as f:
            data = f.read()

        # Apply offset and length limits
        start = args.offset or 0
        length = args.length or len(data) - start
        data = data[start:start + length]

        print(hexdump(data, start_offset=start, bytes_per_line=16, show_ascii=True))
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_strings(args: argparse.Namespace) -> int:
    """Find strings in a file."""
    from .utils.hexdump import find_strings

    filepath = Path(args.file)
    if not filepath.exists():
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        return 1

    try:
        with open(filepath, 'rb') as f:
            data = f.read()

        min_len = args.min_length or 4
        strings = find_strings(data, min_length=min_len)

        for offset, s in strings:
            print(f"0x{offset:08x}: {s}")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_mame(args: argparse.Namespace) -> int:
    """Analyze a MAME nvram directory for an SGI machine."""
    from .nvram.prom_env import SGINVRAMAnalyzer

    dirpath = Path(args.directory)
    if not dirpath.is_dir():
        print(f"Error: Not a directory: {dirpath}", file=sys.stderr)
        return 1

    # Look for common SGI NVRAM files
    rtc_file = dirpath / "rtc"
    eeprom_file = dirpath / "eeprom"

    print(f"=== MAME SGI Machine Analysis ===")
    print(f"Directory: {dirpath}")
    print()

    if rtc_file.exists():
        print(f"Found RTC file: {rtc_file}")
        print(f"Size: {rtc_file.stat().st_size} bytes")
        print()

        try:
            analyzer = SGINVRAMAnalyzer.from_file(str(rtc_file))
            print(analyzer.format_report())
        except Exception as e:
            print(f"Error analyzing RTC: {e}")
    else:
        print("No RTC file found")

    if eeprom_file.exists():
        print()
        print(f"Found EEPROM file: {eeprom_file}")
        print(f"Size: {eeprom_file.stat().st_size} bytes")
        # Could add EEPROM analysis here

    return 0


def cmd_types(args: argparse.Namespace) -> int:
    """List all supported firmware types."""
    from .prom.firmware_types import FirmwareType, get_type_name, get_type_description

    print("=== Supported Firmware Types ===")
    print()

    for fw_type in FirmwareType:
        if fw_type == FirmwareType.UNKNOWN:
            continue
        name = get_type_name(fw_type)
        desc = get_type_description(fw_type)
        print(f"{fw_type.name:25} {name}")
        print(f"{'':25} {desc}")
        print()

    return 0


def main(argv: Optional[list] = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog='sgi-analyze',
        description='SGI Binary Analysis Toolkit',
    )
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug output')

    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # nvram subcommand
    nvram_parser = subparsers.add_parser('nvram', help='Analyze NVRAM/RTC file')
    nvram_parser.add_argument('file', help='Path to NVRAM file')
    nvram_parser.add_argument('--json', action='store_true',
                              help='Output as JSON')
    nvram_parser.set_defaults(func=cmd_nvram)

    # prom subcommand
    prom_parser = subparsers.add_parser('prom', help='Analyze SGI PROM/firmware image')
    prom_parser.add_argument('file', help='Path to firmware file or directory (with --all)')
    prom_parser.add_argument('--json', action='store_true',
                             help='Output as JSON')
    prom_parser.add_argument('--strings', action='store_true',
                             help='Show all strings found')
    prom_parser.add_argument('--min-length', '-m', type=int, default=6,
                             help='Minimum string length (with --strings)')
    prom_parser.add_argument('--all', '-a', action='store_true',
                             help='Analyze all firmware files in directory')
    prom_parser.set_defaults(func=cmd_prom)

    # hexdump subcommand
    hex_parser = subparsers.add_parser('hexdump', help='Hex dump a file')
    hex_parser.add_argument('file', help='Path to file')
    hex_parser.add_argument('--offset', '-o', type=int, default=0,
                            help='Start offset')
    hex_parser.add_argument('--length', '-n', type=int,
                            help='Number of bytes to display')
    hex_parser.set_defaults(func=cmd_hexdump)

    # strings subcommand
    strings_parser = subparsers.add_parser('strings', help='Find strings in file')
    strings_parser.add_argument('file', help='Path to file')
    strings_parser.add_argument('--min-length', '-m', type=int, default=4,
                                help='Minimum string length')
    strings_parser.set_defaults(func=cmd_strings)

    # mame subcommand
    mame_parser = subparsers.add_parser('mame', help='Analyze MAME nvram directory')
    mame_parser.add_argument('directory', help='Path to MAME nvram/<machine> directory')
    mame_parser.set_defaults(func=cmd_mame)

    # types subcommand
    types_parser = subparsers.add_parser('types', help='List supported firmware types')
    types_parser.set_defaults(func=cmd_types)

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
