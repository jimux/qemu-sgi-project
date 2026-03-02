"""
Pytest configuration and shared fixtures for SGI emulation tests.
"""

import os
import pytest


def pytest_addoption(parser):
    parser.addoption("--save-reference", action="store_true", default=False,
                     help="Save framebuffer captures as reference images")

# Base paths
WORKSPACE = "/workspace"
QEMU_DIR = os.path.join(WORKSPACE, "qemu")
QEMU_BUILD = os.path.join(QEMU_DIR, "build")
QEMU_BIN = os.path.join(QEMU_BUILD, "qemu-system-mips64")

# Source files under test
HPC3_SOURCE = os.path.join(QEMU_DIR, "hw", "misc", "sgi_hpc3.c")
HPC3_HEADER = os.path.join(QEMU_DIR, "include", "hw", "misc", "sgi_hpc3.h")
CP0_TIMER_SOURCE = os.path.join(QEMU_DIR, "target", "mips", "system", "cp0_timer.c")
INDY_MACHINE_SOURCE = os.path.join(QEMU_DIR, "hw", "mips", "sgi_indy.c")
CPU_SOURCE = os.path.join(QEMU_DIR, "target", "mips", "cpu.c")
MC_SOURCE = os.path.join(QEMU_DIR, "hw", "misc", "sgi_mc.c")
MC_HEADER = os.path.join(QEMU_DIR, "include", "hw", "misc", "sgi_mc.h")
NEWPORT_SOURCE = os.path.join(QEMU_DIR, "hw", "display", "sgi_newport.c")
NEWPORT_HEADER = os.path.join(QEMU_DIR, "include", "hw", "display", "sgi_newport.h")
MIPS_KCONFIG = os.path.join(QEMU_DIR, "hw", "mips", "Kconfig")
MIPS_MESON = os.path.join(QEMU_DIR, "hw", "mips", "meson.build")
DISPLAY_KCONFIG = os.path.join(QEMU_DIR, "hw", "display", "Kconfig")
WD33C93_SOURCE = os.path.join(QEMU_DIR, "hw", "scsi", "wd33c93.c")
WD33C93_HEADER = os.path.join(QEMU_DIR, "include", "hw", "scsi", "wd33c93.h")
ARCS_SOURCE = os.path.join(QEMU_DIR, "hw", "misc", "sgi_arcs.c")
ARCS_HEADER = os.path.join(QEMU_DIR, "include", "hw", "misc", "sgi_arcs.h")
ICOUNT_SOURCE = os.path.join(QEMU_DIR, "accel", "tcg", "icount-common.c")
EXCEPTION_SOURCE = os.path.join(QEMU_DIR, "target", "mips", "tcg", "exception.c")
SCSI_DISK_SOURCE = os.path.join(QEMU_DIR, "hw", "scsi", "scsi-disk.c")
SCSI_BUS_SOURCE = os.path.join(QEMU_DIR, "hw", "scsi", "scsi-bus.c")
SCSI_CONSTANTS_HEADER = os.path.join(QEMU_DIR, "include", "scsi", "constants.h")

# Trace event files
MISC_TRACE_EVENTS = os.path.join(QEMU_DIR, "hw", "misc", "trace-events")
DISPLAY_TRACE_EVENTS = os.path.join(QEMU_DIR, "hw", "display", "trace-events")

# NVRAM files
NVRAM_FILE = os.path.join(WORKSPACE, "sgi_indy_nvram.bin")
NVRAM_BUILD = os.path.join(QEMU_BUILD, "sgi_indy_nvram.bin")

# Trace log files (written by QEMU during boot)
CP0_TIMER_TRACE = "/tmp/cp0_timer_trace.log"
MAP_MASK_TRACE = "/tmp/map_mask_raw.log"
SCC_TX_TIMER_TRACE = "/tmp/scc_tx_timer_trace.log"
SCC_WR1_TRACE = "/tmp/scc_wr1_trace.log"

# NVRAM layout constants (must match sgi_hpc3.c)
NVRAM_TABLE_BASE = 0x40
NVRAM_TABLE_SIZE = 256
NVOFF_CHECKSUM = 0
NVOFF_REVISION = 1
NVOFF_CONSOLE = 2
NVOFF_LBAUD = 116
NVOFF_DISKLESS = 121
NVOFF_TIMEZONE = 122
NVOFF_AUTOLOAD = 178
NVOFF_VOLUME = 232
NVOFF_SCSIHOSTID = 235
NVOFF_SGILOGO = 236
NVOFF_AUTOPOWER = 239
NVOFF_MONITOR = 240
NVOFF_ENET = 250


@pytest.fixture
def nvram_data():
    """Load the NVRAM binary file."""
    path = NVRAM_FILE
    if not os.path.exists(path):
        path = NVRAM_BUILD
    if not os.path.exists(path):
        pytest.skip("No NVRAM binary file found")
    with open(path, "rb") as f:
        return f.read()


@pytest.fixture
def nvram_table(nvram_data):
    """Extract the 256-byte NVRAM variable table."""
    return nvram_data[NVRAM_TABLE_BASE:NVRAM_TABLE_BASE + NVRAM_TABLE_SIZE]


@pytest.fixture
def hpc3_source():
    """Load the HPC3 C source file."""
    if not os.path.exists(HPC3_SOURCE):
        pytest.skip(f"HPC3 source not found: {HPC3_SOURCE}")
    with open(HPC3_SOURCE) as f:
        return f.read()


@pytest.fixture
def cp0_timer_source():
    """Load the CP0 timer C source file."""
    if not os.path.exists(CP0_TIMER_SOURCE):
        pytest.skip(f"CP0 timer source not found: {CP0_TIMER_SOURCE}")
    with open(CP0_TIMER_SOURCE) as f:
        return f.read()


@pytest.fixture
def indy_machine_source():
    """Load the SGI Indy machine C source file."""
    if not os.path.exists(INDY_MACHINE_SOURCE):
        pytest.skip(f"Indy machine source not found: {INDY_MACHINE_SOURCE}")
    with open(INDY_MACHINE_SOURCE) as f:
        return f.read()


@pytest.fixture
def cpu_source():
    """Load the MIPS CPU C source file."""
    if not os.path.exists(CPU_SOURCE):
        pytest.skip(f"CPU source not found: {CPU_SOURCE}")
    with open(CPU_SOURCE) as f:
        return f.read()


@pytest.fixture
def mc_source():
    """Load the MC (Memory Controller) C source file."""
    if not os.path.exists(MC_SOURCE):
        pytest.skip(f"MC source not found: {MC_SOURCE}")
    with open(MC_SOURCE) as f:
        return f.read()


@pytest.fixture
def mc_header():
    """Load the MC header file."""
    if not os.path.exists(MC_HEADER):
        pytest.skip(f"MC header not found: {MC_HEADER}")
    with open(MC_HEADER) as f:
        return f.read()


@pytest.fixture
def newport_source():
    """Load the Newport graphics C source file."""
    if not os.path.exists(NEWPORT_SOURCE):
        pytest.skip(f"Newport source not found: {NEWPORT_SOURCE}")
    with open(NEWPORT_SOURCE) as f:
        return f.read()


@pytest.fixture
def newport_header():
    """Load the Newport header file."""
    if not os.path.exists(NEWPORT_HEADER):
        pytest.skip(f"Newport header not found: {NEWPORT_HEADER}")
    with open(NEWPORT_HEADER) as f:
        return f.read()


@pytest.fixture
def hpc3_header():
    """Load the HPC3 header file."""
    if not os.path.exists(HPC3_HEADER):
        pytest.skip(f"HPC3 header not found: {HPC3_HEADER}")
    with open(HPC3_HEADER) as f:
        return f.read()


@pytest.fixture
def mips_kconfig():
    """Load the MIPS Kconfig file."""
    if not os.path.exists(MIPS_KCONFIG):
        pytest.skip(f"MIPS Kconfig not found: {MIPS_KCONFIG}")
    with open(MIPS_KCONFIG) as f:
        return f.read()


@pytest.fixture
def mips_meson_build():
    """Load the MIPS meson.build file."""
    if not os.path.exists(MIPS_MESON):
        pytest.skip(f"MIPS meson.build not found: {MIPS_MESON}")
    with open(MIPS_MESON) as f:
        return f.read()


@pytest.fixture
def display_kconfig():
    """Load the display Kconfig file."""
    if not os.path.exists(DISPLAY_KCONFIG):
        pytest.skip(f"Display Kconfig not found: {DISPLAY_KCONFIG}")
    with open(DISPLAY_KCONFIG) as f:
        return f.read()


@pytest.fixture
def wd33c93_source():
    """Load the WD33C93 SCSI controller C source file."""
    if not os.path.exists(WD33C93_SOURCE):
        pytest.skip(f"WD33C93 source not found: {WD33C93_SOURCE}")
    with open(WD33C93_SOURCE) as f:
        return f.read()


@pytest.fixture
def wd33c93_header():
    """Load the WD33C93 SCSI controller header file."""
    if not os.path.exists(WD33C93_HEADER):
        pytest.skip(f"WD33C93 header not found: {WD33C93_HEADER}")
    with open(WD33C93_HEADER) as f:
        return f.read()


@pytest.fixture
def arcs_source():
    """Load the SGI ARCS firmware C source file."""
    if not os.path.exists(ARCS_SOURCE):
        pytest.skip(f"ARCS source not found: {ARCS_SOURCE}")
    with open(ARCS_SOURCE) as f:
        return f.read()


@pytest.fixture
def arcs_header():
    """Load the SGI ARCS firmware header file."""
    if not os.path.exists(ARCS_HEADER):
        pytest.skip(f"ARCS header not found: {ARCS_HEADER}")
    with open(ARCS_HEADER) as f:
        return f.read()


@pytest.fixture
def icount_source():
    """Load the icount-common.c source file."""
    if not os.path.exists(ICOUNT_SOURCE):
        pytest.skip(f"icount source not found: {ICOUNT_SOURCE}")
    with open(ICOUNT_SOURCE) as f:
        return f.read()


@pytest.fixture
def exception_source():
    """Load the MIPS exception.c source file."""
    if not os.path.exists(EXCEPTION_SOURCE):
        pytest.skip(f"exception source not found: {EXCEPTION_SOURCE}")
    with open(EXCEPTION_SOURCE) as f:
        return f.read()


@pytest.fixture
def scsi_disk_source():
    """Load the QEMU scsi-disk.c source file."""
    if not os.path.exists(SCSI_DISK_SOURCE):
        pytest.skip(f"scsi-disk source not found: {SCSI_DISK_SOURCE}")
    with open(SCSI_DISK_SOURCE) as f:
        return f.read()


@pytest.fixture
def scsi_bus_source():
    """Load the QEMU scsi-bus.c source file."""
    if not os.path.exists(SCSI_BUS_SOURCE):
        pytest.skip(f"scsi-bus source not found: {SCSI_BUS_SOURCE}")
    with open(SCSI_BUS_SOURCE) as f:
        return f.read()


@pytest.fixture
def scsi_constants_header():
    """Load the SCSI constants header file."""
    if not os.path.exists(SCSI_CONSTANTS_HEADER):
        pytest.skip(f"SCSI constants header not found: {SCSI_CONSTANTS_HEADER}")
    with open(SCSI_CONSTANTS_HEADER) as f:
        return f.read()


@pytest.fixture
def misc_trace_events():
    """Load the hw/misc/trace-events file."""
    if not os.path.exists(MISC_TRACE_EVENTS):
        pytest.skip(f"Trace events not found: {MISC_TRACE_EVENTS}")
    with open(MISC_TRACE_EVENTS) as f:
        return f.read()


@pytest.fixture
def display_trace_events():
    """Load the hw/display/trace-events file."""
    if not os.path.exists(DISPLAY_TRACE_EVENTS):
        pytest.skip(f"Trace events not found: {DISPLAY_TRACE_EVENTS}")
    with open(DISPLAY_TRACE_EVENTS) as f:
        return f.read()
