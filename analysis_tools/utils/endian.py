"""Big-endian helpers for SGI binary data parsing.

SGI systems use big-endian byte order. This module provides utilities
for reading and writing big-endian values from binary data.
"""

import struct
from typing import Tuple


def read_u8(data: bytes, offset: int) -> int:
    """Read an unsigned 8-bit value."""
    return data[offset]


def read_u16_be(data: bytes, offset: int) -> int:
    """Read an unsigned 16-bit big-endian value."""
    return struct.unpack_from('>H', data, offset)[0]


def read_u32_be(data: bytes, offset: int) -> int:
    """Read an unsigned 32-bit big-endian value."""
    return struct.unpack_from('>I', data, offset)[0]


def read_u64_be(data: bytes, offset: int) -> int:
    """Read an unsigned 64-bit big-endian value."""
    return struct.unpack_from('>Q', data, offset)[0]


def read_s16_be(data: bytes, offset: int) -> int:
    """Read a signed 16-bit big-endian value."""
    return struct.unpack_from('>h', data, offset)[0]


def read_s32_be(data: bytes, offset: int) -> int:
    """Read a signed 32-bit big-endian value."""
    return struct.unpack_from('>i', data, offset)[0]


def write_u16_be(value: int) -> bytes:
    """Pack an unsigned 16-bit value as big-endian bytes."""
    return struct.pack('>H', value)


def write_u32_be(value: int) -> bytes:
    """Pack an unsigned 32-bit value as big-endian bytes."""
    return struct.pack('>I', value)


def read_cstring(data: bytes, offset: int, max_len: int = 256) -> Tuple[str, int]:
    """Read a null-terminated string from data.

    Returns:
        Tuple of (string, bytes_consumed including null terminator)
    """
    end = offset
    while end < len(data) and end < offset + max_len and data[end] != 0:
        end += 1

    try:
        s = data[offset:end].decode('ascii', errors='replace')
    except Exception:
        s = data[offset:end].decode('latin-1', errors='replace')

    # Include the null terminator in bytes consumed if present
    bytes_consumed = end - offset
    if end < len(data) and data[end] == 0:
        bytes_consumed += 1

    return s, bytes_consumed


def bcd_to_int(bcd: int, strict: bool = False) -> int:
    """Convert a BCD (Binary-Coded Decimal) byte to integer.

    BCD encodes each decimal digit in 4 bits.
    For example: 0x59 -> 59

    Args:
        bcd: The BCD-encoded byte value
        strict: If True, raise ValueError for invalid BCD digits (A-F)

    Returns:
        The decoded integer value. For invalid BCD (nibbles > 9),
        returns the raw byte value unless strict=True.
    """
    high = (bcd >> 4) & 0x0F
    low = bcd & 0x0F

    # Check for invalid BCD digits (A-F in either nibble)
    if high > 9 or low > 9:
        if strict:
            raise ValueError(f"Invalid BCD value: 0x{bcd:02x}")
        # Return the raw value for invalid BCD
        return bcd

    return high * 10 + low


def int_to_bcd(value: int) -> int:
    """Convert an integer (0-99) to BCD format."""
    if value < 0 or value > 99:
        raise ValueError(f"Value {value} out of BCD range (0-99)")
    return ((value // 10) << 4) | (value % 10)
