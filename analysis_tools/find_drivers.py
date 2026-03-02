#!/usr/bin/env python3
"""
IRIX Driver Discovery and Documentation Tool

Walks through the extracted IRIX directory tree, identifies driver files
(kernel modules, shared libraries, configuration files), classifies them
by hardware type, and builds an organized inventory with documentation.
"""

import os
import sys
import hashlib
import shutil
import re
import json
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional, Tuple

# Base paths
SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR / "extracted"
OUTPUT_DIR = SCRIPT_DIR.parent / "docs"
DRIVERS_OUTPUT_DIR = SCRIPT_DIR.parent / "drivers"

# Driver categories with filename patterns
DRIVER_CATEGORIES = {
    "network": {
        "patterns": [r"^if_.*\.o$", r"^.*eth.*\.o$", r"^.*net.*\.o$"],
        "keywords": ["if_", "ethernet", "network", "ppp", "slip", "fddi", "atm", "isdn"],
        "description": "Network interface drivers"
    },
    "graphics": {
        "patterns": [r".*gfx.*\.o$", r".*_dd\.o$", r".*gl.*\.so", r".*_gr.*\.o$"],
        "keywords": ["gfx", "_dd", "gl", "newport", "impact", "crime", "mgras", "xbow"],
        "description": "Graphics and display drivers"
    },
    "audio": {
        "patterns": [r".*midi.*\.o$", r".*dsp.*\.o$", r".*audio.*\.(o|so)$", r"^hdsp.*\.o$", r"^hal2.*\.o$"],
        "keywords": ["midi", "dsp", "audio", "hal2", "hdsp", "sound"],
        "description": "Audio and MIDI drivers"
    },
    "storage": {
        "patterns": [r"^scsi.*\.o$", r"^wd93.*\.o$", r"^jag.*\.o$", r"^dksc.*\.o$", r"^tpsc.*\.o$", r"^smfd.*\.o$", r"^xlv.*\.o$"],
        "keywords": ["scsi", "wd93", "jag", "disk", "tape", "floppy", "raid", "xlv", "dksc", "tpsc"],
        "description": "Storage and SCSI drivers"
    },
    "input": {
        "patterns": [r"^kbd.*\.o$", r".*mouse.*\.o$", r"^pckm.*\.o$", r"^dial.*\.o$", r"^tablet.*\.o$", r"^wacom.*\.o$", r"^sball.*\.o$", r"^magellan.*\.o$"],
        "keywords": ["kbd", "keyboard", "mouse", "pckm", "dial", "tablet", "wacom", "sball", "magellan", "spaceball", "input"],
        "description": "Input device drivers"
    },
    "serial": {
        "patterns": [r"^alp.*\.o$", r".*tty.*\.o$", r"^cdsio.*\.o$", r"^gentty.*\.o$", r".*duart.*\.o$", r".*uart.*\.o$"],
        "keywords": ["serial", "tty", "uart", "duart", "alp", "cdsio"],
        "description": "Serial port drivers"
    },
    "video": {
        "patterns": [r"^vino.*\.o$", r".*video.*\.(o|so)$", r".*vl.*\.(o|so)$", r".*dmedia.*\.so$"],
        "keywords": ["vino", "video", "indycam", "dmedia", "mvp"],
        "description": "Video input/output drivers"
    },
    "system": {
        "patterns": [r"^hpc.*\.o$", r"^ioc.*\.o$", r"^heart.*\.o$", r"^giobr.*\.o$", r"^gioio.*\.o$", r"^mc.*\.o$", r"^bridge.*\.o$", r"^xbow.*\.o$"],
        "keywords": ["hpc", "ioc", "heart", "giobr", "gioio", "mc", "bridge", "xbow", "hub", "pci"],
        "description": "System controller drivers"
    },
    "memory": {
        "patterns": [r"^mem.*\.o$", r"^vm.*\.o$", r".*tlb.*\.o$"],
        "keywords": ["mem", "memory", "vm", "tlb", "cache"],
        "description": "Memory and cache drivers"
    },
    "filesystem": {
        "patterns": [r"^efs.*\.o$", r"^xfs.*\.o$", r"^nfs.*\.o$", r"^proc.*\.o$", r"^fd.*\.o$"],
        "keywords": ["efs", "xfs", "nfs", "filesystem", "vfs", "proc"],
        "description": "Filesystem drivers"
    }
}

# SGI system board mappings
BOARD_TYPES = {
    "IP4": {"name": "4D Series", "description": "Professional IRIS 4D/60, 4D/70, 4D/80"},
    "IP5": {"name": "4D Series", "description": "Professional IRIS 4D/120, 4D/210"},
    "IP6": {"name": "4D Series", "description": "Professional IRIS 4D/20"},
    "IP12": {"name": "Personal IRIS", "description": "4D/30, 4D/35"},
    "IP17": {"name": "Crimson", "description": "POWER Series"},
    "IP19": {"name": "Challenge/Onyx", "description": "POWER Challenge, Onyx (R4400)"},
    "IP20": {"name": "Indigo", "description": "Entry workstation"},
    "IP21": {"name": "Challenge/Onyx", "description": "POWER Challenge, Onyx (R8000)"},
    "IP22": {"name": "Indy/Indigo2", "description": "Desktop workstations"},
    "IP25": {"name": "Challenge/Onyx", "description": "POWER Challenge XL"},
    "IP26": {"name": "Indigo2 IMPACT", "description": "Indigo2 with R8000"},
    "IP27": {"name": "Origin 2000", "description": "NUMA servers"},
    "IP28": {"name": "Indigo2 IMPACT", "description": "Indigo2 with R10000"},
    "IP30": {"name": "Octane", "description": "High-end workstation"},
    "IP32": {"name": "O2", "description": "Multimedia workstation"},
    "IP35": {"name": "Origin 300/3000", "description": "Modular servers"},
}


@dataclass
class DriverFile:
    """Represents a discovered driver file."""
    path: Path
    filename: str
    category: str
    board_type: Optional[str]
    irix_version: Optional[str]
    size: int
    md5: str
    file_type: str  # 'kernel', 'library', 'config', 'graphics'
    source_distribution: str

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "filename": self.filename,
            "category": self.category,
            "board_type": self.board_type,
            "irix_version": self.irix_version,
            "size": self.size,
            "md5": self.md5,
            "file_type": self.file_type,
            "source_distribution": self.source_distribution
        }


def compute_md5(filepath: Path) -> str:
    """Compute MD5 hash of a file."""
    hash_md5 = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except (IOError, OSError):
        return "unknown"


def extract_board_type(filepath: Path) -> Optional[str]:
    """Extract IP board type from file path."""
    path_str = str(filepath)
    # Look for IPxxboot pattern
    match = re.search(r"IP(\d+)boot", path_str)
    if match:
        return f"IP{match.group(1)}"
    # Look for arch/IPxx pattern
    match = re.search(r"arch/IP(\d+)", path_str)
    if match:
        return f"IP{match.group(1)}"
    return None


def extract_irix_version(filepath: Path) -> Optional[str]:
    """Extract IRIX version from file path."""
    path_str = str(filepath)
    # Common version patterns
    patterns = [
        r"IRIX[_-]?(\d+\.\d+(?:\.\d+)?)",
        r"(\d+\.\d+(?:\.\d+)?)[_-]foundation",
        r"irix(\d+\.\d+(?:\.\d+)?)",
        r"effect(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, path_str, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def extract_source_distribution(filepath: Path) -> str:
    """Extract the source distribution name from the path."""
    path_str = str(filepath)
    # Look for dist_contents/XXX or extracted/XXX patterns
    match = re.search(r"(?:dist_contents|extracted)/([^/]+)", path_str)
    if match:
        return match.group(1)
    return "unknown"


def classify_driver(filename: str, filepath: Path) -> str:
    """Classify a driver into a category."""
    filename_lower = filename.lower()

    for category, info in DRIVER_CATEGORIES.items():
        # Check patterns
        for pattern in info["patterns"]:
            if re.match(pattern, filename_lower):
                return category
        # Check keywords
        for keyword in info["keywords"]:
            if keyword in filename_lower:
                return category

    return "misc"


def get_file_type(filepath: Path) -> str:
    """Determine the type of driver file."""
    path_str = str(filepath).lower()
    filename = filepath.name.lower()

    if "sysgen" in path_str and filename.endswith(".o"):
        return "kernel"
    if "master.d" in path_str:
        return "config"
    if "/gfx/" in path_str:
        return "graphics"
    if filename.endswith(".so") or ".so." in filename:
        return "library"
    if filename.endswith(".o"):
        return "kernel"
    return "misc"


def is_driver_file(filepath: Path) -> bool:
    """Determine if a file is a driver file."""
    filename = filepath.name.lower()
    path_str = str(filepath).lower()

    # Skip certain directories
    skip_dirs = ["cpuminer", "jansson", "docs", "man"]
    if any(d in path_str for d in skip_dirs):
        return False

    # Kernel modules in sysgen directories
    if "sysgen" in path_str and filename.endswith(".o"):
        return True

    # master.d configuration files
    if "master.d" in path_str:
        return True

    # Graphics libraries and drivers
    if "/gfx/" in path_str:
        if filename.endswith((".o", ".so")) or ".so." in filename:
            return True

    # Shared libraries that might be hardware-related
    if "/lib/" in path_str and (filename.endswith(".so") or ".so." in filename):
        # Filter for likely hardware-related libraries
        hw_keywords = ["audio", "midi", "vl", "video", "dm", "gl", "cl", "al"]
        if any(kw in filename_lower for kw in hw_keywords for filename_lower in [filename]):
            return True

    return False


def walk_and_find_drivers(base_dir: Path) -> List[DriverFile]:
    """Walk the directory tree and find all driver files."""
    drivers = []

    print(f"Scanning {base_dir} for drivers...")

    for root, dirs, files in os.walk(base_dir):
        # Skip hidden directories
        dirs[:] = [d for d in dirs if not d.startswith(".")]

        for filename in files:
            filepath = Path(root) / filename

            if is_driver_file(filepath):
                try:
                    stat = filepath.stat()
                    driver = DriverFile(
                        path=filepath,
                        filename=filename,
                        category=classify_driver(filename, filepath),
                        board_type=extract_board_type(filepath),
                        irix_version=extract_irix_version(filepath),
                        size=stat.st_size,
                        md5=compute_md5(filepath),
                        file_type=get_file_type(filepath),
                        source_distribution=extract_source_distribution(filepath)
                    )
                    drivers.append(driver)
                except (IOError, OSError) as e:
                    print(f"  Warning: Could not process {filepath}: {e}")

    print(f"Found {len(drivers)} driver files")
    return drivers


def deduplicate_drivers(drivers: List[DriverFile]) -> Tuple[List[DriverFile], Dict[str, List[DriverFile]]]:
    """Remove duplicate drivers based on MD5 hash, keeping one representative."""
    seen_md5 = {}
    unique_drivers = []
    duplicates = defaultdict(list)

    for driver in drivers:
        if driver.md5 not in seen_md5:
            seen_md5[driver.md5] = driver
            unique_drivers.append(driver)
        else:
            duplicates[driver.md5].append(driver)

    print(f"Deduplicated to {len(unique_drivers)} unique drivers ({len(drivers) - len(unique_drivers)} duplicates removed)")
    return unique_drivers, dict(duplicates)


def generate_documentation(drivers: List[DriverFile], duplicates: Dict[str, List[DriverFile]]):
    """Generate documentation files."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "architecture").mkdir(exist_ok=True)
    (OUTPUT_DIR / "drivers").mkdir(exist_ok=True)
    (OUTPUT_DIR / "systems").mkdir(exist_ok=True)

    # Generate main driver inventory
    generate_driver_inventory(drivers, duplicates)

    # Generate category-specific documentation
    generate_category_docs(drivers)

    # Generate system-specific documentation
    generate_system_docs(drivers)

    # Generate architecture documentation
    generate_architecture_docs(drivers)

    # Export JSON inventory for programmatic use
    export_json_inventory(drivers)


def generate_driver_inventory(drivers: List[DriverFile], duplicates: Dict[str, List[DriverFile]]):
    """Generate the main driver inventory document."""
    doc_path = OUTPUT_DIR / "drivers" / "DRIVER_INVENTORY.md"

    with open(doc_path, "w") as f:
        f.write("# IRIX Driver Inventory\n\n")
        f.write("Complete listing of IRIX drivers extracted from installation media.\n\n")

        # Summary statistics
        f.write("## Summary\n\n")
        f.write(f"- **Total unique drivers**: {len(drivers)}\n")
        f.write(f"- **Duplicates found**: {sum(len(d) for d in duplicates.values())}\n")

        # Count by category
        category_counts = defaultdict(int)
        for driver in drivers:
            category_counts[driver.category] += 1

        f.write("\n### By Category\n\n")
        f.write("| Category | Count | Description |\n")
        f.write("|----------|-------|-------------|\n")
        for category, count in sorted(category_counts.items(), key=lambda x: -x[1]):
            desc = DRIVER_CATEGORIES.get(category, {}).get("description", "Miscellaneous")
            f.write(f"| {category} | {count} | {desc} |\n")

        # Count by file type
        type_counts = defaultdict(int)
        for driver in drivers:
            type_counts[driver.file_type] += 1

        f.write("\n### By File Type\n\n")
        f.write("| Type | Count |\n")
        f.write("|------|-------|\n")
        for ftype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            f.write(f"| {ftype} | {count} |\n")

        # Count by board type
        board_counts = defaultdict(int)
        for driver in drivers:
            if driver.board_type:
                board_counts[driver.board_type] += 1

        f.write("\n### By Board Type\n\n")
        f.write("| Board | System | Count |\n")
        f.write("|-------|--------|-------|\n")
        for board, count in sorted(board_counts.items(), key=lambda x: int(x[0][2:]) if x[0][2:].isdigit() else 99):
            info = BOARD_TYPES.get(board, {"name": "Unknown", "description": "Unknown system"})
            f.write(f"| {board} | {info['name']} | {count} |\n")

        # Full driver listing by category
        f.write("\n## Driver Listing\n\n")

        drivers_by_category = defaultdict(list)
        for driver in drivers:
            drivers_by_category[driver.category].append(driver)

        for category in sorted(drivers_by_category.keys()):
            cat_drivers = drivers_by_category[category]
            f.write(f"\n### {category.title()} Drivers ({len(cat_drivers)})\n\n")
            f.write("| Filename | Board | Type | Size | Distribution |\n")
            f.write("|----------|-------|------|------|-------------|\n")

            for driver in sorted(cat_drivers, key=lambda d: d.filename):
                board = driver.board_type or "-"
                size_kb = driver.size // 1024 if driver.size > 1024 else f"{driver.size}B"
                if isinstance(size_kb, int):
                    size_kb = f"{size_kb}KB"
                dist = driver.source_distribution[:30] + "..." if len(driver.source_distribution) > 33 else driver.source_distribution
                f.write(f"| {driver.filename} | {board} | {driver.file_type} | {size_kb} | {dist} |\n")

    print(f"Generated {doc_path}")


def generate_category_docs(drivers: List[DriverFile]):
    """Generate category-specific documentation."""
    drivers_by_category = defaultdict(list)
    for driver in drivers:
        drivers_by_category[driver.category].append(driver)

    category_files = {
        "network": "NETWORK_DRIVERS.md",
        "graphics": "GRAPHICS_DRIVERS.md",
        "audio": "AUDIO_DRIVERS.md",
        "storage": "STORAGE_DRIVERS.md",
        "input": "INPUT_DRIVERS.md",
        "serial": "SERIAL_DRIVERS.md",
        "video": "VIDEO_DRIVERS.md",
        "system": "SYSTEM_DRIVERS.md",
    }

    for category, filename in category_files.items():
        if category not in drivers_by_category:
            continue

        doc_path = OUTPUT_DIR / "drivers" / filename
        cat_drivers = drivers_by_category[category]
        cat_info = DRIVER_CATEGORIES.get(category, {"description": "Miscellaneous drivers"})

        with open(doc_path, "w") as f:
            f.write(f"# {category.title()} Drivers\n\n")
            f.write(f"{cat_info['description']}\n\n")
            f.write(f"**Total drivers in category**: {len(cat_drivers)}\n\n")

            # Group by driver name (without .o extension)
            driver_groups = defaultdict(list)
            for driver in cat_drivers:
                base_name = driver.filename.rsplit(".", 1)[0]
                driver_groups[base_name].append(driver)

            f.write("## Drivers\n\n")

            for name in sorted(driver_groups.keys()):
                group = driver_groups[name]
                f.write(f"### {name}\n\n")

                # List all boards this driver appears on
                boards = set(d.board_type for d in group if d.board_type)
                if boards:
                    f.write(f"**Supported boards**: {', '.join(sorted(boards))}\n\n")

                # List all versions
                versions = set(d.irix_version for d in group if d.irix_version)
                if versions:
                    f.write(f"**IRIX versions**: {', '.join(sorted(versions))}\n\n")

                # Table of variants
                f.write("| Board | Version | Size | Distribution |\n")
                f.write("|-------|---------|------|-------------|\n")
                for driver in sorted(group, key=lambda d: (d.board_type or "", d.irix_version or "")):
                    board = driver.board_type or "-"
                    version = driver.irix_version or "-"
                    size_kb = driver.size // 1024 if driver.size > 1024 else f"{driver.size}B"
                    if isinstance(size_kb, int):
                        size_kb = f"{size_kb}KB"
                    dist = driver.source_distribution[:40]
                    f.write(f"| {board} | {version} | {size_kb} | {dist} |\n")

                f.write("\n")

        print(f"Generated {doc_path}")


def generate_system_docs(drivers: List[DriverFile]):
    """Generate system-specific documentation."""
    # Focus on emulation-relevant systems
    target_systems = {
        "IP20": "INDIGO_IP20.md",
        "IP22": "INDY_INDIGO2_IP22.md",
        "IP26": "INDIGO2_IMPACT_IP26.md",
        "IP27": "ORIGIN_IP27.md",
        "IP28": "INDIGO2_IMPACT_IP28.md",
        "IP30": "OCTANE_IP30.md",
        "IP32": "O2_IP32.md",
    }

    for board, filename in target_systems.items():
        board_drivers = [d for d in drivers if d.board_type == board]

        if not board_drivers:
            continue

        doc_path = OUTPUT_DIR / "systems" / filename
        board_info = BOARD_TYPES.get(board, {"name": board, "description": "Unknown system"})

        with open(doc_path, "w") as f:
            f.write(f"# {board_info['name']} ({board}) Drivers\n\n")
            f.write(f"{board_info['description']}\n\n")
            f.write(f"**Total drivers**: {len(board_drivers)}\n\n")

            # Group by category
            by_category = defaultdict(list)
            for driver in board_drivers:
                by_category[driver.category].append(driver)

            f.write("## Driver Categories\n\n")
            f.write("| Category | Count |\n")
            f.write("|----------|-------|\n")
            for category in sorted(by_category.keys()):
                f.write(f"| {category} | {len(by_category[category])} |\n")

            f.write("\n## Drivers by Category\n\n")

            for category in sorted(by_category.keys()):
                cat_drivers = by_category[category]
                f.write(f"### {category.title()}\n\n")
                f.write("| Driver | Size | IRIX Version | Distribution |\n")
                f.write("|--------|------|--------------|-------------|\n")
                for driver in sorted(cat_drivers, key=lambda d: d.filename):
                    size_kb = driver.size // 1024 if driver.size > 1024 else f"{driver.size}B"
                    if isinstance(size_kb, int):
                        size_kb = f"{size_kb}KB"
                    version = driver.irix_version or "-"
                    dist = driver.source_distribution[:35]
                    f.write(f"| {driver.filename} | {size_kb} | {version} | {dist} |\n")
                f.write("\n")

        print(f"Generated {doc_path}")


def generate_architecture_docs(drivers: List[DriverFile]):
    """Generate architecture documentation."""

    # IRIX Driver Architecture
    doc_path = OUTPUT_DIR / "architecture" / "IRIX_DRIVER_ARCHITECTURE.md"
    with open(doc_path, "w") as f:
        f.write("# IRIX Driver Architecture\n\n")
        f.write("## Overview\n\n")
        f.write("IRIX uses a modular kernel architecture with loadable kernel modules (LKMs).\n")
        f.write("Drivers are compiled as object files (.o) and loaded at boot time or dynamically.\n\n")

        f.write("## Directory Structure\n\n")
        f.write("```\n")
        f.write("/usr/cpu/sysgen/\n")
        f.write("├── IPxxboot/          # Board-specific kernel modules\n")
        f.write("│   ├── if_ec.o        # Ethernet controller\n")
        f.write("│   ├── wd93.o         # SCSI controller\n")
        f.write("│   └── ...            # Other drivers\n")
        f.write("├── root/              # Root filesystem template\n")
        f.write("└── system.gen         # Kernel configuration\n")
        f.write("\n")
        f.write("/var/sysgen/master.d/  # Driver configuration files\n")
        f.write("├── wd93               # SCSI config\n")
        f.write("├── if_ec              # Ethernet config\n")
        f.write("└── ...                # Other configs\n")
        f.write("\n")
        f.write("/usr/gfx/              # Graphics subsystem\n")
        f.write("├── arch/IPxx*/        # Board-specific GL libraries\n")
        f.write("└── ucode/             # Graphics microcode\n")
        f.write("```\n\n")

        f.write("## master.d Configuration Format\n\n")
        f.write("The master.d files define driver properties:\n\n")
        f.write("```\n")
        f.write("*\n")
        f.write("* Driver description\n")
        f.write("*\n")
        f.write("*FLAG   PREFIX          SOFT    #DEV    DEPENDENCIES\n")
        f.write("c       driver_         -       -       dependency1,dependency2\n")
        f.write("$$$\n")
        f.write("```\n\n")

        f.write("**FLAGS**:\n")
        f.write("- `c` - Character device\n")
        f.write("- `b` - Block device\n")
        f.write("- `s` - Software driver\n")
        f.write("- `f` - Filesystem\n")
        f.write("- `n` - Network driver\n\n")

        f.write("## Board-Specific Drivers\n\n")
        f.write("Different SGI systems use different chipsets and require specific drivers:\n\n")
        f.write("| Board | System | Key Drivers |\n")
        f.write("|-------|--------|-------------|\n")
        for board, info in sorted(BOARD_TYPES.items(), key=lambda x: int(x[0][2:]) if x[0][2:].isdigit() else 99):
            board_drivers = [d for d in drivers if d.board_type == board]
            key_drivers = ", ".join(sorted(set(d.filename.rsplit(".", 1)[0] for d in board_drivers[:5])))
            f.write(f"| {board} | {info['name']} | {key_drivers} |\n")

        f.write("\n")

    print(f"Generated {doc_path}")

    # SGI Hardware Overview
    doc_path = OUTPUT_DIR / "architecture" / "SGI_HARDWARE_OVERVIEW.md"
    with open(doc_path, "w") as f:
        f.write("# SGI Hardware Overview\n\n")
        f.write("## System Boards\n\n")
        f.write("| Board | Name | Description | CPU |\n")
        f.write("|-------|------|-------------|-----|\n")

        cpu_info = {
            "IP4": "MIPS R2000/R3000",
            "IP5": "MIPS R3000",
            "IP6": "MIPS R3000",
            "IP12": "MIPS R3000A",
            "IP17": "MIPS R4000",
            "IP19": "MIPS R4400",
            "IP20": "MIPS R4000",
            "IP21": "MIPS R8000",
            "IP22": "MIPS R4000/R4600/R5000",
            "IP25": "MIPS R10000",
            "IP26": "MIPS R8000",
            "IP27": "MIPS R10000/R12000",
            "IP28": "MIPS R10000",
            "IP30": "MIPS R10000/R12000/R14000",
            "IP32": "MIPS R5000/R10000/R12000",
            "IP35": "MIPS R14000/R16000",
        }

        for board, info in sorted(BOARD_TYPES.items(), key=lambda x: int(x[0][2:]) if x[0][2:].isdigit() else 99):
            cpu = cpu_info.get(board, "Unknown")
            f.write(f"| {board} | {info['name']} | {info['description']} | {cpu} |\n")

        f.write("\n## Key Hardware Components\n\n")

        f.write("### Indy/Indigo2 (IP22)\n\n")
        f.write("- **Memory Controller (MC)**: RAM management, DMA, GIO64 arbitration\n")
        f.write("- **HPC3**: Peripheral controller - SCSI, Ethernet, PBUS DMA\n")
        f.write("- **IOC2**: I/O controller - interrupts, keyboard, serial\n")
        f.write("- **HAL2**: Audio controller\n")
        f.write("- **VINO**: Video input (IndyCam)\n")
        f.write("- **Newport**: Graphics (REX3 raster engine)\n\n")

        f.write("### O2 (IP32)\n\n")
        f.write("- **CRIME**: Central memory controller and DMA\n")
        f.write("- **MACE**: Peripheral controller\n")
        f.write("- **GBE**: Graphics backend\n")
        f.write("- **RAD1**: Audio/video\n\n")

        f.write("### Octane (IP30)\n\n")
        f.write("- **HEART**: Crossbar switch, memory controller\n")
        f.write("- **XBOW**: Crossbar interconnect\n")
        f.write("- **BRIDGE**: PCI/GIO bridge\n")
        f.write("- **IOC3**: I/O controller\n")
        f.write("- **Impact/Odyssey**: Graphics\n\n")

    print(f"Generated {doc_path}")


def export_json_inventory(drivers: List[DriverFile]):
    """Export driver inventory as JSON for programmatic use."""
    json_path = OUTPUT_DIR / "driver_inventory.json"

    inventory = {
        "total_drivers": len(drivers),
        "generated": str(Path(__file__).name),
        "drivers": [d.to_dict() for d in drivers]
    }

    with open(json_path, "w") as f:
        json.dump(inventory, f, indent=2)

    print(f"Generated {json_path}")


def copy_drivers_to_output(drivers: List[DriverFile]):
    """Copy unique drivers to organized output directory."""
    # Create output structure
    kernel_dir = DRIVERS_OUTPUT_DIR / "kernel"
    graphics_dir = DRIVERS_OUTPUT_DIR / "graphics"
    config_dir = DRIVERS_OUTPUT_DIR / "config" / "master.d"
    library_dir = DRIVERS_OUTPUT_DIR / "libraries"

    for d in [kernel_dir, graphics_dir, config_dir, library_dir]:
        d.mkdir(parents=True, exist_ok=True)

    copied = 0
    for driver in drivers:
        try:
            if driver.file_type == "kernel":
                # Organize by board type
                board = driver.board_type or "generic"
                dest_dir = kernel_dir / board.lower()
                dest_dir.mkdir(exist_ok=True)
                dest = dest_dir / driver.filename
            elif driver.file_type == "config":
                dest = config_dir / driver.filename
            elif driver.file_type == "graphics":
                dest = graphics_dir / driver.filename
            else:
                dest = library_dir / driver.filename

            if not dest.exists():
                shutil.copy2(driver.path, dest)
                copied += 1
        except (IOError, OSError) as e:
            print(f"  Warning: Could not copy {driver.filename}: {e}")

    print(f"Copied {copied} drivers to {DRIVERS_OUTPUT_DIR}")


def main():
    """Main entry point."""
    print("IRIX Driver Discovery Tool")
    print("=" * 40)

    if not BASE_DIR.exists():
        print(f"Error: Base directory not found: {BASE_DIR}")
        print("Please ensure IRIX installation media is extracted to the 'extracted' subdirectory.")
        sys.exit(1)

    # Find all drivers
    drivers = walk_and_find_drivers(BASE_DIR)

    if not drivers:
        print("No drivers found!")
        sys.exit(1)

    # Deduplicate
    unique_drivers, duplicates = deduplicate_drivers(drivers)

    # Generate documentation
    print("\nGenerating documentation...")
    generate_documentation(unique_drivers, duplicates)

    # Optionally copy drivers to output directory
    if "--copy" in sys.argv:
        print("\nCopying drivers to output directory...")
        copy_drivers_to_output(unique_drivers)

    print("\nDone!")
    print(f"Documentation generated in: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
