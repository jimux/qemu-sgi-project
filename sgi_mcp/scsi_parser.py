"""SCSI log parser for QEMU debug output.

Parses SCSI command traces from QEMU's -d unimp output (wd33c93.c and
scsi-disk.c log lines) into structured data for analysis.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# =============================================================================
# SCSI Opcode and Sense Code Reference Tables
# =============================================================================

SCSI_OPCODE_NAMES = {
    0x00: "TEST_UNIT_READY",
    0x01: "REZERO_UNIT",
    0x03: "REQUEST_SENSE",
    0x04: "FORMAT_UNIT",
    0x07: "REASSIGN_BLOCKS",
    0x08: "READ_6",
    0x0a: "WRITE_6",
    0x0b: "SEEK_6",
    0x12: "INQUIRY",
    0x15: "MODE_SELECT",
    0x1a: "MODE_SENSE",
    0x1b: "START_STOP",
    0x1c: "RECEIVE_DIAGNOSTIC",
    0x1d: "SEND_DIAGNOSTIC",
    0x1e: "PREVENT_ALLOW_MEDIUM_REMOVAL",
    0x25: "READ_CAPACITY",
    0x28: "READ_10",
    0x2a: "WRITE_10",
    0x2b: "SEEK_10",
    0x2e: "WRITE_AND_VERIFY",
    0x2f: "VERIFY_10",
    0x35: "SYNCHRONIZE_CACHE",
    0x37: "READ_DEFECT_DATA",
    0x3b: "WRITE_BUFFER",
    0x3c: "READ_BUFFER",
    0x43: "READ_TOC",
    0x46: "GET_CONFIGURATION",
    0x4d: "LOG_SENSE",
    0x55: "MODE_SELECT_10",
    0x5a: "MODE_SENSE_10",
    0xa0: "REPORT_LUNS",
    0xa8: "READ_12",
    0xaa: "WRITE_12",
}

# Sense code descriptions: (key, asc, ascq) -> description
# Note: QEMU logs sense codes in DECIMAL, so ASC 36 = 0x24
SENSE_DESCRIPTIONS = {
    (0, 0, 0): "NO SENSE",
    (1, 0, 0): "RECOVERED ERROR",
    (2, 4, 1): "NOT READY - BECOMING READY",
    (2, 4, 2): "NOT READY - INITIALIZING",
    (2, 0x3a, 0): "NOT READY - MEDIUM NOT PRESENT",
    (3, 0, 0): "MEDIUM ERROR",
    (3, 0x11, 0): "UNRECOVERED READ ERROR",
    (4, 0, 0): "HARDWARE ERROR",
    (5, 0x20, 0): "INVALID COMMAND OPERATION CODE",
    (5, 0x24, 0): "INVALID FIELD IN CDB",
    (5, 0x25, 0): "LOGICAL UNIT NOT SUPPORTED",
    (5, 0x26, 0): "INVALID FIELD IN PARAMETER LIST",
    (5, 0x39, 0): "SAVING PARAMETERS NOT SUPPORTED",
    (6, 0x28, 0): "NOT READY TO READY CHANGE",
    (6, 0x29, 0): "POWER ON OR RESET",
    (7, 0x27, 0): "WRITE PROTECTED",
    (0xb, 0, 0): "ABORTED COMMAND",
}


def opcode_name(opcode: int) -> str:
    """Get human-readable name for a SCSI opcode."""
    return SCSI_OPCODE_NAMES.get(opcode, f"UNKNOWN_0x{opcode:02x}")


def sense_description(key: int, asc: int, ascq: int) -> str:
    """Get human-readable description for a sense code triplet."""
    desc = SENSE_DESCRIPTIONS.get((key, asc, ascq))
    if desc:
        return desc
    # Try without ascq
    desc = SENSE_DESCRIPTIONS.get((key, asc, 0))
    if desc:
        return f"{desc} (ascq={ascq})"
    # Try just the key
    desc = SENSE_DESCRIPTIONS.get((key, 0, 0))
    if desc:
        return f"{desc} (asc=0x{asc:02x}/ascq={ascq})"
    return f"KEY={key} ASC=0x{asc:02x} ASCQ={ascq}"


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class SCSICommand:
    """A single SCSI command parsed from the log."""
    line_number: int
    target_id: int
    lun: int
    opcode: int
    opcode_name: str
    cdb: str                          # Hex CDB string
    transfer_count: int
    data_len: Optional[int] = None    # From "SCSI response datalen="
    status: Optional[str] = None      # "ok", "check_condition", "timeout"
    sense_key: Optional[int] = None
    sense_asc: Optional[int] = None
    sense_ascq: Optional[int] = None
    sense_desc: Optional[str] = None  # "INVALID FIELD IN CDB"
    error_detail: Optional[str] = None
    raw_lines: List[str] = field(default_factory=list)


@dataclass
class SCSILogSummary:
    """Summary of all SCSI activity parsed from a log."""
    total_commands: int
    successful_commands: int
    failed_commands: int
    timeouts: int
    commands: List[SCSICommand]
    command_counts: Dict[str, int]     # opcode_name -> count
    error_counts: Dict[str, int]       # error description -> count
    target_activity: Dict[int, int]    # target_id -> command count
    mode_sense_pages: Dict[int, str]   # page number -> "ok"/"failed"
    errors: List[SCSICommand]


# =============================================================================
# Log Line Regexes
# =============================================================================

# wd33c93: SELECT_XFER target=1 CDB[6]={1a 00 3f 00 fc 00 }tc=252
# Also matches enhanced format with cmd= prefix:
# wd33c93: SELECT_XFER target=1 cmd=MODE_SENSE(0x1a) CDB[6]={1a 00 3f 00 fc 00 }tc=252
RE_SELECT_XFER = re.compile(
    r'wd33c93: SELECT_XFER target=(\d+)\s+'
    r'(?:cmd=\w+\(0x[0-9a-fA-F]+\)\s+)?'
    r'CDB\[(\d+)\]=\{([0-9a-fA-F ]+)\}'
    r'tc=(\d+)'
)

# wd33c93: SCSI response datalen=36
RE_RESPONSE_DATALEN = re.compile(
    r'wd33c93: SCSI response datalen=(-?\d+)'
)

# scsi-disk: check_condition cmd=MODE_SENSE (0x1a) sense=5/36/0
# Note: sense codes are in DECIMAL in QEMU output
RE_CHECK_CONDITION = re.compile(
    r'scsi-disk: check_condition cmd=(\S+)\s+\(0x([0-9a-fA-F]+)\)\s+'
    r'sense=(\d+)/(\d+)/(\d+)'
)

# scsi-disk: MODE_SENSE unsupported page 0x3f (page_control=0, dbd=0, dev_type=0)
RE_MODE_SENSE_UNSUPPORTED = re.compile(
    r'scsi-disk: MODE_SENSE unsupported page 0x([0-9a-fA-F]+)\s+'
    r'\(page_control=(\d+),\s*dbd=(\d+),\s*dev_type=(\d+)\)'
)

# wd33c93: SCSI CMD FAILED target=1 status=2 CDB[6]={1a 00 3f 00 fc 00 }
RE_CMD_FAILED = re.compile(
    r'wd33c93: SCSI CMD FAILED target=(\d+)\s+status=(\d+)\s+'
    r'CDB\[(\d+)\]=\{([0-9a-fA-F ]+)\}'
)

# wd33c93: SELECT: no device at target=2 lun=0 (WD_DPRINTF, only if WD_DEBUG=1)
RE_SELECT_NO_DEVICE = re.compile(
    r'wd33c93: SELECT: no device at target=(\d+)\s+lun=(\d+)'
)


# =============================================================================
# Parser
# =============================================================================

def _parse_cdb_opcode(cdb_hex: str) -> int:
    """Extract opcode byte from CDB hex string like '1a 00 3f 00 fc 00'."""
    parts = cdb_hex.strip().split()
    if parts:
        return int(parts[0], 16)
    return 0


def parse_scsi_log(log_content: str, max_commands: int = 0,
                   errors_only: bool = False,
                   target_filter: Optional[int] = None,
                   opcode_filter: Optional[str] = None) -> SCSILogSummary:
    """Parse SCSI command traces from QEMU -d unimp log output.

    Uses a state machine approach:
    1. Scan for SELECT_XFER -> create SCSICommand with partial data
    2. Next 'SCSI response datalen=' -> attach to pending command
    3. If check_condition or CMD FAILED follows -> attach error info
    4. If MODE_SENSE unsupported follows -> attach detail
    5. Non-SCSI lines between events are tolerated

    Args:
        log_content: Raw QEMU log content (from -d unimp or -D file)
        max_commands: Maximum commands to return (0 = unlimited)
        errors_only: Only include failed commands in the command list
        target_filter: Only include commands to this SCSI target ID
        opcode_filter: Only include commands matching this opcode name or hex

    Returns:
        SCSILogSummary with parsed commands and statistics
    """
    commands: List[SCSICommand] = []
    pending: Optional[SCSICommand] = None  # Command awaiting completion info
    command_counts: Dict[str, int] = {}
    error_counts: Dict[str, int] = {}
    target_activity: Dict[int, int] = {}
    mode_sense_pages: Dict[int, str] = {}

    total = 0
    successful = 0
    failed = 0
    timeouts = 0

    # Resolve opcode filter to an opcode number if it's a name
    filter_opcode_num: Optional[int] = None
    if opcode_filter:
        opcode_upper = opcode_filter.upper()
        # Check if it's a hex value
        if opcode_upper.startswith("0X"):
            filter_opcode_num = int(opcode_upper, 16)
        else:
            # Search by name
            for code, name in SCSI_OPCODE_NAMES.items():
                if name == opcode_upper:
                    filter_opcode_num = code
                    break

    lines = log_content.split('\n')

    for line_num, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue

        # Check for SELECT_XFER (new command start)
        m = RE_SELECT_XFER.search(line)
        if m:
            # Finalize any pending command
            if pending is not None:
                _finalize_command(pending, commands, command_counts,
                                 error_counts, target_activity,
                                 mode_sense_pages, errors_only,
                                 target_filter, filter_opcode_num)
                total += 1
                if pending.status == "check_condition":
                    failed += 1
                elif pending.status == "timeout":
                    timeouts += 1
                else:
                    successful += 1

            target_id = int(m.group(1))
            cdb_len = int(m.group(2))
            cdb_hex = m.group(3).strip()
            tc = int(m.group(4))
            opcode = _parse_cdb_opcode(cdb_hex)

            pending = SCSICommand(
                line_number=line_num,
                target_id=target_id,
                lun=0,  # LUN extracted from CDB if needed
                opcode=opcode,
                opcode_name=opcode_name(opcode),
                cdb=cdb_hex,
                transfer_count=tc,
                status="ok",  # Assume ok until we see an error
                raw_lines=[line],
            )
            continue

        # Check for response datalen (attaches to pending)
        m = RE_RESPONSE_DATALEN.search(line)
        if m and pending is not None:
            pending.data_len = int(m.group(1))
            pending.raw_lines.append(line)
            continue

        # Check for check_condition (error)
        m = RE_CHECK_CONDITION.search(line)
        if m:
            cmd_name = m.group(1)
            cmd_opcode = int(m.group(2), 16)
            sense_key = int(m.group(3))
            sense_asc = int(m.group(4))
            sense_ascq = int(m.group(5))
            desc = sense_description(sense_key, sense_asc, sense_ascq)

            if pending is not None and pending.opcode == cmd_opcode:
                pending.status = "check_condition"
                pending.sense_key = sense_key
                pending.sense_asc = sense_asc
                pending.sense_ascq = sense_ascq
                pending.sense_desc = desc
                pending.raw_lines.append(line)
            else:
                # Standalone check_condition (no matching SELECT_XFER seen)
                standalone = SCSICommand(
                    line_number=line_num,
                    target_id=pending.target_id if pending else 0,
                    lun=0,
                    opcode=cmd_opcode,
                    opcode_name=opcode_name(cmd_opcode),
                    cdb="",
                    transfer_count=0,
                    status="check_condition",
                    sense_key=sense_key,
                    sense_asc=sense_asc,
                    sense_ascq=sense_ascq,
                    sense_desc=desc,
                    raw_lines=[line],
                )
                _finalize_command(standalone, commands, command_counts,
                                 error_counts, target_activity,
                                 mode_sense_pages, errors_only,
                                 target_filter, filter_opcode_num)
                total += 1
                failed += 1
            continue

        # Check for MODE_SENSE unsupported page detail
        m = RE_MODE_SENSE_UNSUPPORTED.search(line)
        if m:
            page = int(m.group(1), 16)
            if pending is not None:
                pending.error_detail = (
                    f"MODE_SENSE unsupported page 0x{page:02x} "
                    f"(pc={m.group(2)}, dbd={m.group(3)}, type={m.group(4)})"
                )
                pending.raw_lines.append(line)
            mode_sense_pages[page] = "failed"
            continue

        # Check for CMD FAILED
        m = RE_CMD_FAILED.search(line)
        if m:
            target_id = int(m.group(1))
            status_code = int(m.group(2))
            cdb_hex = m.group(4).strip()
            opcode = _parse_cdb_opcode(cdb_hex)

            if pending is not None and pending.target_id == target_id:
                if pending.status == "ok":
                    pending.status = "check_condition"
                pending.raw_lines.append(line)
            continue

        # Check for selection timeout / no device (WD_DEBUG only)
        m = RE_SELECT_NO_DEVICE.search(line)
        if m:
            target_id = int(m.group(1))
            lun = int(m.group(2))
            timeout_cmd = SCSICommand(
                line_number=line_num,
                target_id=target_id,
                lun=lun,
                opcode=0,
                opcode_name="(selection)",
                cdb="",
                transfer_count=0,
                status="timeout",
                raw_lines=[line],
            )
            _finalize_command(timeout_cmd, commands, command_counts,
                             error_counts, target_activity,
                             mode_sense_pages, errors_only,
                             target_filter, filter_opcode_num)
            total += 1
            timeouts += 1
            continue

    # Finalize last pending command
    if pending is not None:
        _finalize_command(pending, commands, command_counts,
                         error_counts, target_activity,
                         mode_sense_pages, errors_only,
                         target_filter, filter_opcode_num)
        total += 1
        if pending.status == "check_condition":
            failed += 1
        elif pending.status == "timeout":
            timeouts += 1
        else:
            successful += 1

    # Track MODE_SENSE pages that succeeded (seen in commands but not in failed list)
    for cmd in commands:
        if cmd.opcode == 0x1a and cmd.status == "ok":
            # Extract page from CDB byte 2
            cdb_parts = cmd.cdb.split()
            if len(cdb_parts) >= 3:
                page = int(cdb_parts[2], 16) & 0x3f
                if page not in mode_sense_pages:
                    mode_sense_pages[page] = "ok"

    # Apply max_commands limit
    if max_commands > 0:
        commands = commands[:max_commands]

    errors = [c for c in commands if c.status in ("check_condition", "timeout")]

    return SCSILogSummary(
        total_commands=total,
        successful_commands=successful,
        failed_commands=failed,
        timeouts=timeouts,
        commands=commands,
        command_counts=command_counts,
        error_counts=error_counts,
        target_activity=target_activity,
        mode_sense_pages=mode_sense_pages,
        errors=errors,
    )


def _finalize_command(cmd: SCSICommand,
                      commands: List[SCSICommand],
                      command_counts: Dict[str, int],
                      error_counts: Dict[str, int],
                      target_activity: Dict[int, int],
                      mode_sense_pages: Dict[int, str],
                      errors_only: bool,
                      target_filter: Optional[int],
                      opcode_filter: Optional[int]) -> None:
    """Finalize a command: update counters and optionally add to the list."""
    # Update counters (always, regardless of filters)
    command_counts[cmd.opcode_name] = command_counts.get(cmd.opcode_name, 0) + 1
    target_activity[cmd.target_id] = target_activity.get(cmd.target_id, 0) + 1

    if cmd.status == "check_condition" and cmd.sense_desc:
        error_counts[cmd.sense_desc] = error_counts.get(cmd.sense_desc, 0) + 1

    # Track MODE_SENSE page status
    if cmd.opcode == 0x1a and cmd.cdb:
        cdb_parts = cmd.cdb.split()
        if len(cdb_parts) >= 3:
            page = int(cdb_parts[2], 16) & 0x3f
            if cmd.status == "check_condition":
                mode_sense_pages[page] = "failed"
            elif page not in mode_sense_pages:
                mode_sense_pages[page] = "ok"

    # Apply filters
    if target_filter is not None and cmd.target_id != target_filter:
        return
    if opcode_filter is not None and cmd.opcode != opcode_filter:
        return
    if errors_only and cmd.status not in ("check_condition", "timeout"):
        return

    commands.append(cmd)
