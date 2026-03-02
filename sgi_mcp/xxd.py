# SGI PROM Comparative Analysis - xxd-style hex dump
"""
Full xxd-compatible hex dump with all options.
"""

from typing import Optional
from .config import PROM_BASE, prom_offset_to_addr


def xxd(
    data: bytes,
    seek: int = 0,
    length: int = 0,
    cols: int = 16,
    groupsize: int = 2,
    little_endian: bool = False,
    binary: bool = False,
    c_include: bool = False,
    plain: bool = False,
    uppercase: bool = False,
    base_address: int = PROM_BASE,
    show_prom_address: bool = True
) -> str:
    """
    Generate xxd-style hex dump.

    Args:
        data: Binary data to dump
        seek: Start offset (-s)
        length: Number of bytes to dump, 0 = all (-l)
        cols: Bytes per line, default 16 (-c)
        groupsize: Byte grouping 1, 2, 4, or 8 (-g)
        little_endian: Little-endian byte order (-e)
        binary: Binary dump mode (-b)
        c_include: C include format (-i)
        plain: Plain hex dump, no address/ASCII (-p)
        uppercase: Uppercase hex (-u)
        base_address: Base address for PROM offset display
        show_prom_address: Show PROM virtual address alongside offset

    Returns:
        Formatted hex dump string
    """
    # Apply seek
    if seek > 0:
        if seek >= len(data):
            return ""
        data = data[seek:]

    # Apply length
    if length > 0:
        data = data[:length]

    if len(data) == 0:
        return ""

    # Format selection
    if plain:
        return _plain_dump(data, uppercase)
    elif binary:
        return _binary_dump(data, cols, seek, base_address, show_prom_address)
    elif c_include:
        return _c_include_dump(data, uppercase)
    else:
        return _standard_dump(data, cols, groupsize, little_endian, uppercase,
                             seek, base_address, show_prom_address)


def _standard_dump(
    data: bytes,
    cols: int,
    groupsize: int,
    little_endian: bool,
    uppercase: bool,
    offset: int,
    base_address: int,
    show_prom_address: bool
) -> str:
    """Standard xxd hex dump format."""
    lines = []
    fmt = "%02X" if uppercase else "%02x"

    for i in range(0, len(data), cols):
        row = data[i:i + cols]
        addr = offset + i
        prom_addr = base_address + addr

        # Address column
        if show_prom_address:
            line = f"{prom_addr:08x}: "
        else:
            line = f"{addr:08x}: "

        # Hex column with grouping
        hex_parts = []
        for j in range(0, cols, groupsize):
            group = row[j:j + groupsize]
            if len(group) == 0:
                break

            if little_endian and len(group) > 1:
                group = bytes(reversed(group))

            group_hex = "".join(fmt % b for b in group)
            hex_parts.append(group_hex)

        # Pad hex column
        expected_groups = (cols + groupsize - 1) // groupsize
        while len(hex_parts) < expected_groups:
            hex_parts.append(" " * (groupsize * 2))

        hex_col = " ".join(hex_parts)

        # Pad if row is short
        expected_hex_len = expected_groups * (groupsize * 2) + (expected_groups - 1)
        hex_col = hex_col.ljust(expected_hex_len)

        # ASCII column
        ascii_col = ""
        for b in row:
            if 0x20 <= b < 0x7f:
                ascii_col += chr(b)
            else:
                ascii_col += "."

        line += f"{hex_col}  {ascii_col}"
        lines.append(line)

    return "\n".join(lines)


def _plain_dump(data: bytes, uppercase: bool) -> str:
    """Plain hex dump without addresses or ASCII."""
    fmt = "%02X" if uppercase else "%02x"
    return "".join(fmt % b for b in data)


def _binary_dump(
    data: bytes,
    cols: int,
    offset: int,
    base_address: int,
    show_prom_address: bool
) -> str:
    """Binary dump format (bits instead of hex)."""
    lines = []
    bytes_per_line = min(cols, 6)  # Binary takes more space

    for i in range(0, len(data), bytes_per_line):
        row = data[i:i + bytes_per_line]
        addr = offset + i
        prom_addr = base_address + addr

        if show_prom_address:
            line = f"{prom_addr:08x}: "
        else:
            line = f"{addr:08x}: "

        # Binary representation
        bin_parts = []
        for b in row:
            bin_parts.append(f"{b:08b}")

        line += " ".join(bin_parts)

        # Pad if short
        while len(bin_parts) < bytes_per_line:
            bin_parts.append(" " * 8)

        # ASCII column
        ascii_col = ""
        for b in row:
            if 0x20 <= b < 0x7f:
                ascii_col += chr(b)
            else:
                ascii_col += "."

        # Pad ASCII
        ascii_col = ascii_col.ljust(bytes_per_line)

        bin_col = " ".join(bin_parts)
        line = f"{line}  {ascii_col}"
        lines.append(line)

    return "\n".join(lines)


def _c_include_dump(data: bytes, uppercase: bool) -> str:
    """C include format."""
    fmt = "0x%02X" if uppercase else "0x%02x"
    lines = []
    lines.append("unsigned char data[] = {")

    for i in range(0, len(data), 12):
        row = data[i:i + 12]
        hex_vals = ", ".join(fmt % b for b in row)
        if i + 12 < len(data):
            hex_vals += ","
        lines.append(f"  {hex_vals}")

    lines.append("};")
    lines.append(f"unsigned int data_len = {len(data)};")

    return "\n".join(lines)


def reverse_xxd(hex_string: str) -> bytes:
    """
    Convert hex dump back to binary (xxd -r).

    Handles:
    - Standard xxd format with addresses
    - Plain hex format
    - C include format

    Args:
        hex_string: Hex dump string

    Returns:
        Binary data
    """
    import re

    # Remove C include wrapper
    hex_string = re.sub(r'unsigned\s+\w+\s+\w+\s*\[\s*\]\s*=\s*\{', '', hex_string)
    hex_string = re.sub(r'\}\s*;', '', hex_string)
    hex_string = re.sub(r'unsigned\s+int\s+\w+\s*=\s*\d+\s*;', '', hex_string)

    result = bytearray()
    lines = hex_string.strip().split("\n")

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Try to detect format

        # Standard xxd format: "address: hex  ascii"
        if ":" in line:
            # Remove address prefix
            parts = line.split(":", 1)
            if len(parts) == 2:
                line = parts[1].strip()

            # Remove ASCII suffix (after double space)
            if "  " in line:
                line = line.split("  ")[0]

        # Remove 0x prefixes (C include format)
        line = re.sub(r'0x', '', line, flags=re.IGNORECASE)

        # Remove commas, spaces, and non-hex chars
        hex_only = re.sub(r'[^0-9a-fA-F]', '', line)

        # Convert pairs to bytes
        for i in range(0, len(hex_only) - 1, 2):
            try:
                result.append(int(hex_only[i:i + 2], 16))
            except ValueError:
                pass

    return bytes(result)


def xxd_prom(
    filename: str,
    seek: int = 0,
    length: int = 256,
    cols: int = 16,
    groupsize: int = 4,
    little_endian: bool = False,
    binary: bool = False,
    c_include: bool = False,
    plain: bool = False,
    uppercase: bool = False
) -> str:
    """
    Generate xxd dump for a PROM file.

    Args:
        filename: PROM filename
        seek: Start offset (-s)
        length: Number of bytes to dump (-l)
        cols: Bytes per line (-c)
        groupsize: Byte grouping (-g)
        little_endian: Little-endian mode (-e)
        binary: Binary dump (-b)
        c_include: C include format (-i)
        plain: Plain hex (-p)
        uppercase: Uppercase (-u)

    Returns:
        Formatted hex dump
    """
    from .prom_loader import load_prom

    data = load_prom(filename)
    if not data:
        return f"Error: Could not load {filename}"

    return xxd(
        data,
        seek=seek,
        length=length,
        cols=cols,
        groupsize=groupsize,
        little_endian=little_endian,
        binary=binary,
        c_include=c_include,
        plain=plain,
        uppercase=uppercase,
        base_address=PROM_BASE,
        show_prom_address=True
    )
