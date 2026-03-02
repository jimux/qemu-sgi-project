"""
NVRAM binary parser for SGI DS1386 BBRAM.

The DS1386 has 8KB of battery-backed RAM. The first 16 bytes are RTC registers.
The NVRAM variable table starts at offset 0x40 and is 256 bytes long.

Checksum algorithm matches dallas.c:nvchecksum():
  - XOR each byte with running checksum (seed 0xa5)
  - Rotate left after each odd-indexed byte
  - Byte 0 (the checksum itself) is skipped
"""

import ctypes

NVRAM_TABLE_BASE = 0x40
NVRAM_TABLE_SIZE = 256

# Offsets within the 256-byte table (from IP22nvram.h)
NVOFF_CHECKSUM = 0
NVOFF_REVISION = 1
NVOFF_CONSOLE = 2       # 2 bytes
NVOFF_SYSPART = 4       # 48 bytes
NVOFF_OSLOADER = 52     # 18 bytes
NVOFF_OSFILE = 70       # 28 bytes
NVOFF_OSOPTS = 98       # 12 bytes
NVOFF_LBAUD = 116       # 5 bytes ("dbaud")
NVOFF_DISKLESS = 121    # 1 byte
NVOFF_TIMEZONE = 122    # 8 bytes
NVOFF_OSPART = 130      # 48 bytes
NVOFF_AUTOLOAD = 178    # 1 byte
NVOFF_NETADDR = 181     # 4 bytes (binary IP)
NVOFF_NOKBD = 185       # 1 byte
NVOFF_VOLUME = 232      # 3 bytes
NVOFF_SCSIHOSTID = 235  # 1 byte
NVOFF_SGILOGO = 236     # 1 byte
NVOFF_NOGUI = 237       # 1 byte
NVOFF_AUTOPOWER = 239   # 1 byte
NVOFF_MONITOR = 240     # 1 byte
NVOFF_ENET = 250        # 6 bytes (write-protected MAC)


class NVRAMParser:
    """Parse an SGI NVRAM binary file."""

    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = f.read()
        self.table = self.data[NVRAM_TABLE_BASE:NVRAM_TABLE_BASE + NVRAM_TABLE_SIZE]

    @property
    def size(self):
        return len(self.data)

    def get_table_byte(self, offset):
        """Get a single byte from the NVRAM table at the given offset."""
        return self.table[offset]

    def get_table_string(self, offset, length):
        """Get a null-terminated string from the NVRAM table."""
        raw = self.table[offset:offset + length]
        # Find null terminator
        null_pos = raw.find(b"\x00")
        if null_pos >= 0:
            raw = raw[:null_pos]
        return raw.decode("ascii", errors="replace")

    def get_console(self):
        """Get the console setting ('d' for serial, 'g' for graphics)."""
        return chr(self.table[NVOFF_CONSOLE])

    def get_dbaud(self):
        """Get the serial baud rate string (e.g. '9600')."""
        return self.get_table_string(NVOFF_LBAUD, 5)

    def get_timezone(self):
        """Get the timezone string (e.g. 'PST8PDT')."""
        return self.get_table_string(NVOFF_TIMEZONE, 8)

    def get_eaddr(self):
        """Get the 6-byte Ethernet MAC address."""
        return self.table[NVOFF_ENET:NVOFF_ENET + 6]

    def get_eaddr_string(self):
        """Get the MAC address as a colon-separated hex string."""
        mac = self.get_eaddr()
        return ":".join(f"{b:02x}" for b in mac)

    def compute_checksum(self):
        """Compute the NVRAM checksum per dallas.c:nvchecksum().

        Algorithm: XOR each byte (except byte 0) with running checksum,
        rotate left after each odd-indexed byte. Seed is 0xa5.
        """
        checksum = ctypes.c_int8(0xa5).value

        for i in range(NVRAM_TABLE_SIZE):
            if i != 0:
                checksum ^= ctypes.c_int8(self.table[i]).value
            if i & 1:
                # Rotate left by 1 bit (8-bit)
                unsigned = checksum & 0xFF
                checksum = ctypes.c_int8(((unsigned << 1) | (unsigned >> 7)) & 0xFF).value

        return checksum & 0xFF

    def verify_checksum(self):
        """Verify the stored checksum matches the computed one."""
        stored = self.table[NVOFF_CHECKSUM]
        computed = self.compute_checksum()
        return stored == computed

    def get_revision(self):
        """Get the NVRAM revision byte."""
        return self.table[NVOFF_REVISION]
