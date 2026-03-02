"""Shared NVRAM constants, checksum, and read/write helpers for SGI HPC3.

Mirrors the NVRAM layout from qemu/hw/misc/sgi_hpc3.c so that Python tools
(vm_instances, server.py nvram_dump/nvram_set, install_irix.py) can create,
read, and modify NVRAM files without duplicating the layout or checksum logic.
"""

import os
import random
from pathlib import Path

# DS1386 NVRAM geometry
NVRAM_SIZE = 8192         # Full DS1386 chip size
NVRAM_TABLE_BASE = 0x40   # NVRAM variable table starts here
NVRAM_TABLE_SIZE = 256    # Size of the variable table

# Variable definitions: name -> (offset_within_table, max_length, description)
NVRAM_VARS = {
    "checksum":   (0,   1, "Checksum (auto-computed)"),
    "revision":   (1,   1, "NVRAM revision (8=IP22/IP24, 9=IP26/IP28)"),
    "console":    (2,   2, "Console device (d=serial, g=graphics, d1/d2/g1/g2)"),
    "syspart":    (4,  48, "System partition"),
    "osloader":   (52, 18, "OS loader path"),
    "osfile":     (70, 28, "OS kernel file"),
    "osopts":     (98, 12, "OS boot options"),
    "dbaud":      (116, 5, "Serial baud rate"),
    "diskless":   (121, 1, "Diskless boot (0/1)"),
    "timezone":   (122, 8, "Timezone string"),
    "ospart":     (130, 48, "OS partition"),
    "autoload":   (178, 1, "Auto-load on boot (Y/N)"),
    "netaddr":    (181, 4, "Network address (binary IP)"),
    "nokbd":      (185, 1, "No keyboard mode (0/1)"),
    "volume":     (232, 3, "Audio volume"),
    "scsihostid": (235, 1, "SCSI host ID (0-7)"),
    "sgilogo":    (236, 1, "Show SGI logo (y/n)"),
    "nogui":      (237, 1, "No GUI mode (0/1)"),
    "autopower":  (239, 1, "Auto power on (0/1)"),
    "monitor":    (240, 1, "Monitor type"),
    "enet":       (250, 6, "Ethernet MAC (read-only)"),
}

# Machine type -> default NVRAM revision
MACHINE_NVRAM_REV = {
    "indy": 8,
    "indigo2": 8,
    "indigo2-r10k": 9,
    "indigo2-r8k": 9,
}


def nvram_checksum(table: bytes) -> int:
    """Compute the NVRAM table checksum (matches sgi_hpc3_nvram_checksum in C).

    Args:
        table: 256-byte NVRAM variable table (starting from NVRAM_TABLE_BASE).
               The checksum byte at offset 0 is skipped during computation.

    Returns:
        Computed checksum byte (0-255).
    """
    checksum = 0xa5 & 0xff
    for i in range(len(table)):
        if i != 0:
            checksum ^= table[i]
            checksum &= 0xff
        if i & 1:
            checksum = ((checksum << 1) | (checksum >> 7)) & 0xff
    return checksum


def nvram_read(path: str | Path) -> dict:
    """Read all NVRAM variables from a file.

    Args:
        path: Path to the NVRAM binary file.

    Returns:
        Dict mapping variable names to their decoded values.
        Returns empty dict if file doesn't exist or is too small.
    """
    path = Path(path)
    if not path.exists():
        return {}

    data = path.read_bytes()
    if len(data) < NVRAM_TABLE_BASE + NVRAM_TABLE_SIZE:
        return {}

    table = data[NVRAM_TABLE_BASE:NVRAM_TABLE_BASE + NVRAM_TABLE_SIZE]
    result = {}

    for var_name, (offset, length, _desc) in NVRAM_VARS.items():
        raw = table[offset:offset + length]
        if var_name == "checksum":
            result[var_name] = f"0x{raw[0]:02x}"
        elif var_name == "revision":
            result[var_name] = str(raw[0])
        elif var_name == "enet":
            result[var_name] = ":".join(f"{b:02x}" for b in raw)
        elif var_name == "netaddr":
            result[var_name] = ".".join(str(b) for b in raw)
        elif var_name in ("scsihostid", "diskless", "nokbd", "nogui", "autopower"):
            result[var_name] = str(raw[0])
        elif var_name == "volume":
            result[var_name] = str(int.from_bytes(raw[:3], 'big') if any(raw[:3]) else 0)
        else:
            # String variable - read until null
            try:
                null_idx = raw.index(0)
                val = raw[:null_idx].decode('ascii', errors='replace')
            except ValueError:
                val = raw.decode('ascii', errors='replace')
            result[var_name] = val if val else ""
    return result


def nvram_write_var(path: str | Path, variable: str, value: str) -> str:
    """Set one NVRAM variable and recompute the checksum.

    Args:
        path: Path to the NVRAM binary file (must exist).
        variable: Variable name (must be in NVRAM_VARS).
        value: New value as a string.

    Returns:
        Status message string.

    Raises:
        FileNotFoundError: If the NVRAM file doesn't exist.
        ValueError: If the variable is unknown, read-only, or value is invalid.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"NVRAM file not found: {path}")

    if variable not in NVRAM_VARS:
        raise ValueError(f"Unknown variable: {variable}\n"
                         f"Valid variables: {', '.join(sorted(NVRAM_VARS.keys()))}")

    if variable in ("checksum", "enet"):
        raise ValueError(f"Cannot modify {variable} (auto-computed or read-only)")

    offset, max_length, desc = NVRAM_VARS[variable]

    data = bytearray(path.read_bytes())
    if len(data) < NVRAM_TABLE_BASE + NVRAM_TABLE_SIZE:
        raise ValueError(f"NVRAM file too small: {len(data)} bytes")

    table_start = NVRAM_TABLE_BASE

    # Encode value
    if variable in ("scsihostid", "diskless", "nokbd", "nogui", "autopower", "revision"):
        encoded = bytes([int(value) & 0xff])
    elif variable == "netaddr":
        parts = value.split(".")
        if len(parts) != 4:
            raise ValueError("netaddr must be in dotted-quad format (e.g., 192.168.1.1)")
        encoded = bytes(int(p) & 0xff for p in parts)
    elif variable == "volume":
        v = int(value) & 0xffffff
        encoded = v.to_bytes(3, 'big')
    else:
        # String variable
        encoded = value.encode('ascii')
        if len(encoded) > max_length:
            raise ValueError(f"Value too long: {len(encoded)} bytes (max {max_length})")
        # Pad with nulls
        encoded = encoded + b'\x00' * (max_length - len(encoded))

    # Write value
    abs_offset = table_start + offset
    data[abs_offset:abs_offset + len(encoded)] = encoded

    # Recompute checksum
    table = data[table_start:table_start + NVRAM_TABLE_SIZE]
    new_cksum = nvram_checksum(table)
    data[table_start] = new_cksum

    # Write back
    path.write_bytes(bytes(data))

    return (f"Set {variable} = {value} in {path}\n"
            f"Checksum: 0x{new_cksum:02x}")


def nvram_create_defaults(path: str | Path, revision: int = 8,
                          autoload: bool = False) -> Path:
    """Create a fresh NVRAM file with sane defaults.

    Mirrors sgi_hpc3_nvram_init_defaults() from the C code so that
    the NVRAM exists before the first QEMU boot.

    Args:
        path: Output file path.
        revision: NVRAM revision (8 for IP22/IP24, 9 for IP26/IP28).
        autoload: Whether to set autoload=Y (default N for fresh instances).

    Returns:
        The path written to.
    """
    path = Path(path)
    data = bytearray(NVRAM_SIZE)
    # Write directly into data using absolute offsets (NVRAM_TABLE_BASE + offset)
    # because slicing a bytearray creates a copy, not a view.
    B = NVRAM_TABLE_BASE

    # Revision
    data[B + 1] = revision

    # console = "d" (serial)
    data[B + 2] = ord('d')

    # dbaud = "9600"
    data[B + 116:B + 120] = b'9600'

    # monitor = "h" (1280x1024@60Hz)
    data[B + 240] = ord('h')

    # timezone = "PST8PDT"
    data[B + 122:B + 129] = b'PST8PDT'

    # diskless = "0"
    data[B + 121] = ord('0')

    # volume = "80"
    data[B + 232] = ord('8')
    data[B + 233] = ord('0')

    # sgilogo = "y"
    data[B + 236] = ord('y')

    # autopower = "y"
    data[B + 239] = ord('y')

    # scsihostid = "0"
    data[B + 235] = ord('0')

    # autoload
    data[B + 178] = ord('Y') if autoload else ord('N')

    # Ethernet MAC: SGI OUI 08:00:69 + 3 random bytes
    data[B + 250] = 0x08
    data[B + 251] = 0x00
    data[B + 252] = 0x69
    data[B + 253] = random.randint(0, 255)
    data[B + 254] = random.randint(0, 255)
    data[B + 255] = random.randint(0, 255)

    # Compute and set checksum
    cksum = nvram_checksum(bytes(data[B:B + NVRAM_TABLE_SIZE]))
    data[B] = cksum

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(data))
    return path
