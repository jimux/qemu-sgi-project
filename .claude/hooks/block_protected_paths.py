#!/usr/bin/env python3
"""PreToolUse hook: Block edits to read-only reference material and upstream QEMU.

Protects:
  - Upstream QEMU files (only SGI-specific files are editable)
  - MAME source (read-only reference)
  - PROM binaries (original firmware images)
  - IRIX source trees (reference material)
"""
import json
import re
import sys

input_data = json.load(sys.stdin)
path = input_data.get("tool_input", {}).get("file_path", "")

# --- MAME reference (always read-only) ---
if "/mame/" in path:
    print(json.dumps({
        "decision": "block",
        "reason": "MAME source is read-only reference. "
                  "Edit the QEMU implementation in qemu/hw/ instead."
    }))
    sys.exit(0)

# --- PROM binaries and IRIX source (always read-only, except artifacts) ---
if "/PROM_library/bins/" in path:
    print(json.dumps({
        "decision": "block",
        "reason": "Reference material (PROM binaries) is read-only."
    }))
    sys.exit(0)

# --- Upstream QEMU (only SGI-specific files allowed) ---
if "/qemu/" in path:
    allowed_patterns = [
        r"qemu/hw/mips/sgi_",
        r"qemu/hw/misc/sgi_",
        r"qemu/hw/display/sgi_",
        r"qemu/include/hw/misc/sgi_",
        r"qemu/include/hw/display/sgi_",
        r"qemu/include/hw/scsi/wd33c93\.h",
        r"qemu/hw/scsi/wd33c93\.c",
        r"qemu/hw/mips/Kconfig",
        r"qemu/hw/mips/meson\.build",
        r"qemu/hw/display/Kconfig",
        r"qemu/hw/display/meson\.build",
        r"qemu/hw/misc/Kconfig",
        r"qemu/hw/misc/meson\.build",
        r"qemu/hw/scsi/Kconfig",
        r"qemu/hw/scsi/meson\.build",
        r"qemu/hw/display/trace-events",
        r"qemu/hw/misc/trace-events",
        r"qemu/hw/scsi/trace-events",
    ]
    if not any(re.search(p, path) for p in allowed_patterns):
        print(json.dumps({
            "decision": "block",
            "reason": f"Upstream QEMU file: {path}. "
                      "Only edit sgi_*/wd33c93 files and their build configs."
        }))
        sys.exit(0)

# --- Everything else is fine ---
print(json.dumps({"decision": "approve"}))
