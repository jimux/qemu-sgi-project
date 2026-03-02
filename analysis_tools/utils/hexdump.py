"""Pretty hex dump output utilities."""

from typing import Optional, List, Tuple


def hexdump(data: bytes, start_offset: int = 0, bytes_per_line: int = 16,
            show_ascii: bool = True, annotations: Optional[dict] = None) -> str:
    """Generate a formatted hex dump of binary data.

    Args:
        data: The binary data to dump
        start_offset: The offset to display (for labeling purposes)
        bytes_per_line: Number of bytes per line (default 16)
        show_ascii: Whether to show ASCII representation
        annotations: Optional dict mapping offset ranges to descriptions
                    Format: {(start, end): "description"}

    Returns:
        Formatted hex dump string
    """
    lines = []
    annotations = annotations or {}

    for i in range(0, len(data), bytes_per_line):
        offset = start_offset + i
        chunk = data[i:i + bytes_per_line]

        # Offset column
        line = f"{offset:08x}  "

        # Hex bytes with spacing in the middle
        hex_parts = []
        for j, byte in enumerate(chunk):
            hex_parts.append(f"{byte:02x}")
        # Pad if less than bytes_per_line
        while len(hex_parts) < bytes_per_line:
            hex_parts.append("  ")

        # Add space in middle for readability
        mid = bytes_per_line // 2
        line += " ".join(hex_parts[:mid]) + "  " + " ".join(hex_parts[mid:])

        # ASCII column
        if show_ascii:
            line += "  |"
            for byte in chunk:
                if 0x20 <= byte <= 0x7e:
                    line += chr(byte)
                else:
                    line += "."
            line += "|"

        # Check for annotations
        for (ann_start, ann_end), desc in annotations.items():
            if ann_start <= offset < ann_end or ann_start < offset + bytes_per_line <= ann_end:
                line += f"  <- {desc}"
                break

        lines.append(line)

    return "\n".join(lines)


def hexdump_compact(data: bytes, max_bytes: int = 64) -> str:
    """Generate a compact single-line hex representation.

    Args:
        data: The binary data to dump
        max_bytes: Maximum bytes to show before truncating

    Returns:
        Compact hex string like "00 11 22 33 ... (128 bytes)"
    """
    if len(data) <= max_bytes:
        return " ".join(f"{b:02x}" for b in data)
    else:
        shown = " ".join(f"{b:02x}" for b in data[:max_bytes])
        return f"{shown} ... ({len(data)} bytes total)"


def format_mac_address(data: bytes) -> str:
    """Format 6 bytes as a MAC address string."""
    if len(data) < 6:
        return "INVALID"
    return ":".join(f"{b:02x}" for b in data[:6])


def format_bytes_annotated(data: bytes, regions: List[Tuple[int, int, str]]) -> str:
    """Format binary data with annotated regions.

    Args:
        data: The binary data
        regions: List of (start_offset, length, description) tuples

    Returns:
        Multi-line formatted output with annotations
    """
    lines = []
    for start, length, desc in regions:
        if start + length <= len(data):
            chunk = data[start:start + length]
            hex_str = " ".join(f"{b:02x}" for b in chunk)
            lines.append(f"  {start:04x}-{start + length - 1:04x}: {hex_str}")
            lines.append(f"           {desc}")
    return "\n".join(lines)


def find_strings(data: bytes, min_length: int = 4) -> List[Tuple[int, str]]:
    """Find printable ASCII strings in binary data.

    Args:
        data: The binary data to search
        min_length: Minimum string length to report

    Returns:
        List of (offset, string) tuples
    """
    results = []
    current_string = []
    start_offset = 0

    for i, byte in enumerate(data):
        if 0x20 <= byte <= 0x7e:
            if not current_string:
                start_offset = i
            current_string.append(chr(byte))
        else:
            if len(current_string) >= min_length:
                results.append((start_offset, "".join(current_string)))
            current_string = []

    # Handle string at end of data
    if len(current_string) >= min_length:
        results.append((start_offset, "".join(current_string)))

    return results
