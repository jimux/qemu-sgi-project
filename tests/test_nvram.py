"""
NVRAM binary assertions.

Tests the actual NVRAM file (/workspace/sgi_indy_nvram.bin) to verify
that the DS1386 BBRAM contents match expected defaults for serial console
operation.

These tests are FAST (no QEMU boot needed).
"""

import os
import pytest
from helpers.nvram import NVRAMParser, NVRAM_TABLE_BASE, NVRAM_TABLE_SIZE
from conftest import NVRAM_FILE, NVRAM_BUILD


def get_nvram_path():
    """Find an existing NVRAM file."""
    for path in (NVRAM_FILE, NVRAM_BUILD):
        if os.path.exists(path):
            return path
    return None


@pytest.fixture
def parser():
    path = get_nvram_path()
    if not path:
        pytest.skip("No NVRAM binary file found")
    return NVRAMParser(path)


class TestNVRAMBasics:
    """Basic NVRAM file format tests."""

    def test_nvram_size(self, parser):
        """NVRAM file should be 8192 bytes (DS1386 8KB BBRAM)."""
        assert parser.size == 8192, (
            f"NVRAM file is {parser.size} bytes, expected 8192 (DS1386)"
        )

    def test_nvram_table_base(self, parser):
        """Table should start at offset 0x40 in the BBRAM."""
        # Verify the table region is not all zeros (it should have data)
        assert any(b != 0 for b in parser.table), (
            "NVRAM table at offset 0x40 is all zeros"
        )


class TestNVRAMConsole:
    """Console and serial settings."""

    def test_console_is_serial(self, parser):
        """NVRAM console variable should be 'd' (serial console).

        This is critical: if console != 'd', the IRIX kernel routes
        output to the graphics console instead of serial.
        """
        console = parser.get_console()
        assert console == "d", (
            f"NVRAM console is '{console}', expected 'd' (serial). "
            f"Raw byte at table[{0x02}] = 0x{parser.table[0x02]:02x}"
        )

    def test_dbaud_is_9600(self, parser):
        """Serial baud rate should be '9600'."""
        dbaud = parser.get_dbaud()
        assert dbaud == "9600", (
            f"NVRAM dbaud is '{dbaud}', expected '9600'"
        )


class TestNVRAMChecksum:
    """Checksum validation."""

    def test_nvram_checksum_valid(self, parser):
        """Computed checksum should match stored checksum byte.

        Algorithm: XOR+rotate per dallas.c:nvchecksum(), seed 0xa5.
        A bad checksum causes the PROM to print an error and reset defaults.
        """
        stored = parser.table[0]
        computed = parser.compute_checksum()
        assert parser.verify_checksum(), (
            f"NVRAM checksum mismatch: stored=0x{stored:02x}, "
            f"computed=0x{computed:02x}"
        )


class TestNVRAMContent:
    """Other NVRAM variable content tests."""

    def test_eaddr_present(self, parser):
        """Ethernet MAC address should be set (SGI OUI 08:00:69)."""
        mac = parser.get_eaddr()
        assert mac[0] == 0x08 and mac[1] == 0x00 and mac[2] == 0x69, (
            f"MAC OUI is {mac[0]:02x}:{mac[1]:02x}:{mac[2]:02x}, "
            f"expected 08:00:69 (SGI)"
        )
        # At least one of the random bytes should be non-zero
        assert any(b != 0 for b in mac[3:6]), (
            "MAC random bytes are all zero"
        )

    def test_timezone_present(self, parser):
        """TimeZone string should be set."""
        tz = parser.get_timezone()
        assert len(tz) > 0, "TimeZone is empty"
        assert "PST" in tz or "GMT" in tz or len(tz) >= 3, (
            f"TimeZone '{tz}' doesn't look like a valid timezone"
        )

    def test_revision_set(self, parser):
        """NVRAM revision should be 8 (IP22/IP24) or 9 (IP28)."""
        rev = parser.get_revision()
        assert rev in (8, 9), (
            f"NVRAM revision is {rev}, expected 8 (IP22/IP24) or 9 (IP28)"
        )
