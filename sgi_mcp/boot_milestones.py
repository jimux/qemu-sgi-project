"""Boot milestone detection from QEMU serial and debug output.

Detects structured boot progress milestones from serial console output
and optional SCSI debug logs, producing a concise progress report.
"""

import re
import time
from dataclasses import dataclass, field
from typing import List, Optional


# =============================================================================
# Milestone Definitions
# =============================================================================

@dataclass
class MilestoneDef:
    """Definition of a boot milestone to detect."""
    name: str
    pattern: str       # Regex pattern to match in serial output
    phase: str         # prom, loader, kernel, miniroot, error
    is_error: bool = False


# Ordered list of milestones — detection is cumulative
BOOT_MILESTONES = [
    MilestoneDef("PROM POST start",
                 r"Running power-on diagnostics", "prom"),
    MilestoneDef("Memory detected",
                 r"Memory size:", "prom"),
    MilestoneDef("SCSI probe",
                 r"scsi\(\d+\)", "prom"),
    MilestoneDef("Escape countdown",
                 r"Press Esc", "prom"),
    MilestoneDef("System Maintenance Menu",
                 r"System Maintenance Menu", "prom"),
    MilestoneDef("sashARCS loaded",
                 r"Obtaining.*from", "loader"),
    MilestoneDef("Kernel banner",
                 r"IRIX Release", "kernel"),
    MilestoneDef("Init running",
                 r"INIT:", "kernel"),
    MilestoneDef("Creating devices",
                 r"Creating miniroot devices", "miniroot"),
    MilestoneDef("Installer prompt",
                 r"Inst>", "miniroot"),
    # Error milestones
    MilestoneDef("SCSI error",
                 r"check_condition|MODE_SENSE unsupported", "error",
                 is_error=True),
    MilestoneDef("Panic",
                 r"PANIC|panic:|Kernel panic", "error",
                 is_error=True),
]


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class MilestoneResult:
    """Result of checking a single milestone."""
    name: str
    phase: str
    pattern: str
    reached: bool
    timestamp: Optional[float] = None  # Seconds since boot start
    is_error: bool = False


@dataclass
class BootReport:
    """Complete boot progress report."""
    milestones: List[MilestoneResult]
    milestones_reached: int
    milestones_total: int
    stop_reason: str              # "timeout", "panic", "scsi_error_loop", "success"
    elapsed_seconds: float
    scsi_error_summary: Optional[str] = None
    last_output: Optional[str] = None


# =============================================================================
# Detection
# =============================================================================

def detect_milestones(serial_output: str,
                      start_time: Optional[float] = None,
                      debug_log: Optional[str] = None) -> BootReport:
    """Detect boot milestones from serial output.

    Args:
        serial_output: Full serial console transcript
        start_time: Wall-clock time when boot started (for timestamps)
        debug_log: Optional QEMU debug log content (for SCSI error detection)

    Returns:
        BootReport with milestone timeline and stop analysis
    """
    now = time.time()
    elapsed = now - start_time if start_time else 0.0

    results: List[MilestoneResult] = []
    milestones_reached = 0

    # Check serial output for milestones
    # We scan line by line to get approximate position-based ordering
    lines = serial_output.split('\n')

    for mdef in BOOT_MILESTONES:
        compiled = re.compile(mdef.pattern)
        found = False
        # Find first occurrence
        for i, line in enumerate(lines):
            if compiled.search(line):
                found = True
                # Estimate timestamp based on line position (rough)
                if start_time and len(lines) > 0:
                    # Proportional estimate within elapsed time
                    frac = i / max(len(lines), 1)
                    ts = frac * elapsed
                else:
                    ts = None
                results.append(MilestoneResult(
                    name=mdef.name,
                    phase=mdef.phase,
                    pattern=mdef.pattern,
                    reached=True,
                    timestamp=ts,
                    is_error=mdef.is_error,
                ))
                if not mdef.is_error:
                    milestones_reached += 1
                break
        if not found:
            results.append(MilestoneResult(
                name=mdef.name,
                phase=mdef.phase,
                pattern=mdef.pattern,
                reached=False,
                is_error=mdef.is_error,
            ))

    # Also check debug log for SCSI errors if not found in serial
    scsi_error_summary = None
    if debug_log:
        scsi_error_lines = []
        for line in debug_log.split('\n'):
            if 'check_condition' in line or 'MODE_SENSE unsupported' in line:
                scsi_error_lines.append(line.strip())
        if scsi_error_lines:
            # Mark SCSI error milestone as reached if not already
            for r in results:
                if r.name == "SCSI error" and not r.reached:
                    r.reached = True
                    r.timestamp = elapsed  # Error seen in debug log
                    break
            # Count unique errors
            unique_errors: dict = {}
            for line in scsi_error_lines:
                key = line[:80]  # Group by first 80 chars
                unique_errors[key] = unique_errors.get(key, 0) + 1
            summary_lines = []
            for msg, count in sorted(unique_errors.items(), key=lambda x: -x[1])[:10]:
                summary_lines.append(f"- ({count}x) `{msg}`")
            scsi_error_summary = "\n".join(summary_lines)

    # Determine stop reason
    stop_reason = _determine_stop_reason(results, serial_output)

    # Non-error milestones total
    total = sum(1 for m in BOOT_MILESTONES if not m.is_error)

    # Last output for context
    last_output = '\n'.join(lines[-50:]) if lines else ""

    return BootReport(
        milestones=results,
        milestones_reached=milestones_reached,
        milestones_total=total,
        stop_reason=stop_reason,
        elapsed_seconds=elapsed,
        scsi_error_summary=scsi_error_summary,
        last_output=last_output,
    )


def _determine_stop_reason(results: List[MilestoneResult],
                           serial_output: str) -> str:
    """Determine why the boot stopped."""
    # Check for panic
    for r in results:
        if r.name == "Panic" and r.reached:
            return "panic"

    # Check for SCSI error loop
    for r in results:
        if r.name == "SCSI error" and r.reached:
            # If we got to "Creating devices" but not "Installer prompt",
            # the SCSI error is likely blocking progress
            creating = any(r.name == "Creating devices" and r.reached
                           for r in results)
            installer = any(r.name == "Installer prompt" and r.reached
                            for r in results)
            if creating and not installer:
                return "scsi_error_loop"

    # Check for success
    for r in results:
        if r.name == "Installer prompt" and r.reached:
            return "success"

    return "timeout"
