# SGI PROM Ghidra Integration Bridge
"""
Manages Ghidra headless analysis projects and script execution.
Provides async subprocess management for analyzeHeadless invocations.
"""

import asyncio
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

from .config import (
    GHIDRA_ANALYZE_HEADLESS,
    GHIDRA_HOME,
    GHIDRA_PROJECT_DIR,
    GHIDRA_SCRIPT_DIR,
    PROM_BASE,
    detect_platform,
    get_ghidra_language,
)
from .prom_loader import load_prom, get_prom_metadata, normalize_data
from .pattern_detector import build_function_database

# Check if Ghidra and PyGhidra are available at import time
GHIDRA_AVAILABLE = GHIDRA_ANALYZE_HEADLESS.exists()
try:
    import importlib
    _pyghidra_spec = importlib.util.find_spec("pyghidra")
    PYGHIDRA_AVAILABLE = _pyghidra_spec is not None
except Exception:
    PYGHIDRA_AVAILABLE = False


def _sanitize_project_name(filename: str) -> str:
    """Convert PROM filename to a safe Ghidra project name."""
    name = Path(filename).stem
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


def _project_exists(project_name: str) -> bool:
    """Check if a Ghidra project already exists."""
    gpr_path = GHIDRA_PROJECT_DIR / f"{project_name}.gpr"
    return gpr_path.exists()


def _get_program_name(project_name: str) -> str:
    """Get the program name inside a Ghidra project by reading the .prp metadata."""
    prp_path = GHIDRA_PROJECT_DIR / f"{project_name}.rep" / "idata" / "00" / "00000000.prp"
    if prp_path.exists():
        import xml.etree.ElementTree as ET
        try:
            tree = ET.parse(prp_path)
            for state in tree.iter("STATE"):
                if state.get("NAME") == "NAME":
                    return state.get("VALUE", project_name)
        except Exception:
            pass
    return project_name


def _resolve_prom_path(filename: str) -> Optional[Path]:
    """Resolve a PROM filename to an absolute path."""
    # Try as-is (absolute or relative)
    p = Path(filename)
    if p.exists():
        return p.resolve()

    # Try in PROM_library/bins/
    from .config import PROM_DIR
    for bins_dir in [
        PROM_DIR / "PROM_library" / "bins",
        PROM_DIR / "PROM_library",
    ]:
        if bins_dir.exists():
            for match in bins_dir.rglob(filename):
                return match.resolve()

    return None


async def _run_analyze_headless(
    cmd_args: list,
    timeout: int = 300,
) -> tuple[int, str, str]:
    """Run analyzeHeadless with given arguments, return (returncode, stdout, stderr).

    Uses PyGhidra launcher if available (required for Python script support
    in Ghidra 12.x), falls back to standard analyzeHeadless otherwise.
    """
    if PYGHIDRA_AVAILABLE:
        import sys
        cmd = [
            sys.executable, "-m", "pyghidra.ghidra_launch",
            "--install-dir", str(GHIDRA_HOME),
            "ghidra.app.util.headless.AnalyzeHeadless",
        ] + cmd_args
    else:
        cmd = [str(GHIDRA_ANALYZE_HEADLESS)] + cmd_args

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(
            f"Ghidra analyzeHeadless timed out after {timeout}s"
        )

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    return proc.returncode, stdout, stderr


async def ensure_project(
    filename: str,
    force: bool = False,
    timeout: int = 300,
) -> tuple[str, str, str]:
    """
    Ensure a Ghidra project exists for the given PROM file.
    Creates and analyzes if needed. Returns (project_dir, project_name, message).
    """
    if not GHIDRA_AVAILABLE:
        raise RuntimeError(
            "Ghidra not available. Expected analyzeHeadless at: "
            f"{GHIDRA_ANALYZE_HEADLESS}"
        )

    project_name = _sanitize_project_name(filename)
    project_dir = str(GHIDRA_PROJECT_DIR)

    # Ensure project directory exists
    GHIDRA_PROJECT_DIR.mkdir(parents=True, exist_ok=True)

    if _project_exists(project_name) and not force:
        return project_dir, project_name, "Using existing project"

    # Delete existing project if force
    if force and _project_exists(project_name):
        gpr = GHIDRA_PROJECT_DIR / f"{project_name}.gpr"
        rep = GHIDRA_PROJECT_DIR / f"{project_name}.rep"
        if gpr.exists():
            gpr.unlink()
        if rep.exists():
            shutil.rmtree(rep, ignore_errors=True)

    # Resolve PROM path
    prom_path = _resolve_prom_path(filename)
    if prom_path is None:
        raise FileNotFoundError(f"PROM file not found: {filename}")

    # Detect platform and get language
    platform = detect_platform(filename)
    language = get_ghidra_language(platform) if platform else "MIPS:BE:32:default"

    # Build our function database and export as JSON for the import script
    symbols_json_path = None
    try:
        data = load_prom(filename)
        if not data and prom_path:
            data = prom_path.read_bytes()
        if data:
            meta = get_prom_metadata(filename)
            if meta and meta.endian != "big":
                data = normalize_data(data, meta.endian)
            db = build_function_database(data, PROM_BASE, filename)
            symbols_json_path = tempfile.mktemp(suffix=".json", prefix="sgi_symbols_")
            # Export in a format the import script expects
            symbols_data = {"functions": {}}
            for addr, func in db.functions.items():
                name = func.name if func.name else func.suggested_name()
                hw_accesses = []
                for acc in func.hardware_accesses:
                    hw_accesses.append({
                        "device": getattr(acc, "device", ""),
                        "register": getattr(acc, "register", ""),
                    })
                symbols_data["functions"][str(addr)] = {
                    "name": name,
                    "hardware_accesses": hw_accesses,
                }
            with open(symbols_json_path, "w") as f:
                json.dump(symbols_data, f)
    except Exception:
        # Non-fatal - we can still import without our symbols
        symbols_json_path = None

    # Build analyzeHeadless import command
    # Format: analyzeHeadless <project_dir> <project_name>
    #   -import <file> -loader BinaryLoader
    #   -loader-baseAddr <addr> -loader-blockName PROM
    #   -processor <language>
    #   -scriptPath <path> -postScript <script> <args>
    cmd_args = [
        project_dir,
        project_name,
        "-import", str(prom_path),
        "-loader", "BinaryLoader",
        "-loader-baseAddr", hex(PROM_BASE),
        "-loader-blockName", "PROM",
        "-processor", language,
        "-scriptPath", str(GHIDRA_SCRIPT_DIR),
        "-postScript", "sgi_analyze.py", tempfile.mktemp(suffix=".json"),
    ]

    # Add symbol import as a second post-script if we have symbols
    if symbols_json_path:
        import_result_path = tempfile.mktemp(suffix=".json", prefix="sgi_import_result_")
        cmd_args.extend([
            "-postScript", "sgi_import_symbols.py",
            import_result_path, symbols_json_path,
        ])

    start_time = time.monotonic()
    returncode, stdout, stderr = await _run_analyze_headless(cmd_args, timeout=timeout)
    elapsed = time.monotonic() - start_time

    # Clean up temp files
    if symbols_json_path and os.path.exists(symbols_json_path):
        os.unlink(symbols_json_path)

    if returncode != 0:
        # Include last 30 lines of output for diagnostics
        output_tail = "\n".join(stdout.split("\n")[-30:])
        stderr_tail = "\n".join(stderr.split("\n")[-10:])
        raise RuntimeError(
            f"Ghidra import failed (exit code {returncode}).\n"
            f"stdout tail:\n{output_tail}\n"
            f"stderr tail:\n{stderr_tail}"
        )

    msg = f"Project created and analyzed in {elapsed:.1f}s"
    return project_dir, project_name, msg


async def run_script(
    filename: str,
    script_name: str,
    script_args: list,
    timeout: int = 120,
    ensure: bool = True,
) -> dict:
    """
    Run a Ghidra script on an existing project.
    Returns parsed JSON result from the script's output file.
    """
    if not GHIDRA_AVAILABLE:
        raise RuntimeError(
            "Ghidra not available. Expected analyzeHeadless at: "
            f"{GHIDRA_ANALYZE_HEADLESS}"
        )

    project_name = _sanitize_project_name(filename)
    project_dir = str(GHIDRA_PROJECT_DIR)

    # Auto-create project if needed
    if ensure and not _project_exists(project_name):
        await ensure_project(filename, timeout=timeout)

    if not _project_exists(project_name):
        raise FileNotFoundError(
            f"No Ghidra project for '{filename}'. "
            "Run ghidra_analyze first."
        )

    # Get the actual program name inside the project (may differ from project name)
    program_name = _get_program_name(project_name)

    # Create temp file for results
    result_fd, result_path = tempfile.mkstemp(suffix=".json", prefix="ghidra_result_")
    os.close(result_fd)

    try:
        # Build command: process existing project, run script, read-only
        cmd_args = [
            project_dir,
            project_name,
            "-process", program_name,
            "-noanalysis",
            "-readOnly",
            "-scriptPath", str(GHIDRA_SCRIPT_DIR),
            "-postScript", script_name,
            result_path,
        ] + script_args

        returncode, stdout, stderr = await _run_analyze_headless(
            cmd_args, timeout=timeout
        )

        if returncode != 0:
            output_tail = "\n".join(stdout.split("\n")[-20:])
            stderr_tail = "\n".join(stderr.split("\n")[-10:])
            raise RuntimeError(
                f"Ghidra script '{script_name}' failed (exit code {returncode}).\n"
                f"stdout tail:\n{output_tail}\n"
                f"stderr tail:\n{stderr_tail}"
            )

        # Read JSON result
        if os.path.exists(result_path) and os.path.getsize(result_path) > 0:
            with open(result_path, "r") as f:
                return json.load(f)
        else:
            return {"error": "Script produced no output", "stdout": stdout[-500:]}

    finally:
        if os.path.exists(result_path):
            os.unlink(result_path)


async def ghidra_analyze(
    filename: str,
    force: bool = False,
    timeout: int = 300,
) -> str:
    """Import PROM into Ghidra, run auto-analysis, import our symbols."""
    project_dir, project_name, msg = await ensure_project(
        filename, force=force, timeout=timeout
    )

    # Get function count from the project
    result = await run_script(
        filename, "sgi_list_functions.py", [], timeout=60, ensure=False
    )
    func_count = result.get("total_count", "?")

    lines = [
        f"# Ghidra Analysis: {filename}",
        "",
        f"**Project:** {project_name}",
        f"**Location:** {project_dir}/{project_name}.gpr",
        f"**Functions detected:** {func_count}",
        f"**Status:** {msg}",
    ]
    return "\n".join(lines)


async def ghidra_decompile(
    filename: str,
    address: str,
    max_functions: int = 10,
    timeout: int = 120,
) -> str:
    """Decompile function(s) and return C pseudocode."""
    result = await run_script(
        filename,
        "sgi_decompile.py",
        [address, str(max_functions)],
        timeout=timeout,
    )

    lines = []
    for func in result.get("functions", []):
        lines.append(f"### {func['name']} (`{func['address']}`, {func['size']} bytes)")
        lines.append("")
        lines.append("```c")
        lines.append(func.get("c_code", "// decompilation unavailable"))
        lines.append("```")
        lines.append("")

    for err in result.get("errors", []):
        lines.append(f"**Error:** {err}")

    if not lines:
        return "No functions found to decompile."

    return "\n".join(lines)


async def ghidra_functions(
    filename: str,
    filter_str: str = "",
    timeout: int = 60,
) -> str:
    """List all Ghidra-detected functions."""
    script_args = [filter_str] if filter_str else []
    result = await run_script(
        filename, "sgi_list_functions.py", script_args, timeout=timeout
    )

    total = result.get("total_count", 0)
    matched = result.get("matched_count", 0)
    functions = result.get("functions", [])

    lines = [
        f"# Ghidra Functions: {filename}",
        "",
        f"**Total:** {total} | **Matched:** {matched}",
        "",
        "| Address | Name | Size | Callers | Callees | Stack |",
        "|---------|------|------|---------|---------|-------|",
    ]

    for func in functions:
        thunk = " (thunk)" if func.get("is_thunk") else ""
        lines.append(
            f"| `{func['address']}` | {func['name']}{thunk} | "
            f"{func['size']} | {func['callers']} | {func['callees']} | "
            f"{func['stack_frame_size']} |"
        )

    return "\n".join(lines)


async def ghidra_xrefs(
    filename: str,
    address: str,
    direction: str = "both",
    timeout: int = 60,
) -> str:
    """Get cross-references to/from an address."""
    result = await run_script(
        filename,
        "sgi_xrefs.py",
        [address, direction],
        timeout=timeout,
    )

    lines = [f"# Cross-References: {result.get('address', address)}", ""]

    refs_to = result.get("refs_to", [])
    refs_from = result.get("refs_from", [])

    if refs_to:
        lines.append(f"## References TO ({len(refs_to)})")
        lines.append("")
        lines.append("| From | Function | Type |")
        lines.append("|------|----------|------|")
        for ref in refs_to:
            func_name = ref.get("from_function") or "-"
            lines.append(
                f"| `{ref['from_address']}` | {func_name} | {ref['ref_type']} |"
            )
        lines.append("")

    if refs_from:
        lines.append(f"## References FROM ({len(refs_from)})")
        lines.append("")
        lines.append("| To | Function | Type |")
        lines.append("|----|----------|------|")
        for ref in refs_from:
            func_name = ref.get("to_function") or "-"
            lines.append(
                f"| `{ref['to_address']}` | {func_name} | {ref['ref_type']} |"
            )
        lines.append("")

    if not refs_to and not refs_from:
        lines.append("No cross-references found.")

    return "\n".join(lines)


async def ghidra_import_symbols(
    filename: str,
    timeout: int = 120,
) -> str:
    """Re-import our MCP function names into an existing Ghidra project."""
    if not GHIDRA_AVAILABLE:
        raise RuntimeError("Ghidra not available")

    project_name = _sanitize_project_name(filename)
    if not _project_exists(project_name):
        raise FileNotFoundError(
            f"No Ghidra project for '{filename}'. Run ghidra_analyze first."
        )

    # Build function database and export
    data = load_prom(filename)
    if not data:
        # Fallback: try resolving path directly
        prom_path = _resolve_prom_path(filename)
        if prom_path:
            data = prom_path.read_bytes()
        else:
            raise FileNotFoundError(f"Could not load PROM: {filename}")

    meta = get_prom_metadata(filename)
    if meta and meta.endian != "big":
        data = normalize_data(data, meta.endian)

    db = build_function_database(data, PROM_BASE, filename)

    symbols_json_path = tempfile.mktemp(suffix=".json", prefix="sgi_symbols_")
    try:
        symbols_data = {"functions": {}}
        for addr, func in db.functions.items():
            name = func.name if func.name else func.suggested_name()
            hw_accesses = []
            for acc in func.hardware_accesses:
                hw_accesses.append({
                    "device": getattr(acc, "device", ""),
                    "register": getattr(acc, "register", ""),
                })
            symbols_data["functions"][str(addr)] = {
                "name": name,
                "hardware_accesses": hw_accesses,
            }
        with open(symbols_json_path, "w") as f:
            json.dump(symbols_data, f)

        # Run import script (NOT read-only - we need to write symbols)
        project_dir = str(GHIDRA_PROJECT_DIR)
        program_name = _get_program_name(project_name)
        result_fd, result_path = tempfile.mkstemp(
            suffix=".json", prefix="ghidra_import_"
        )
        os.close(result_fd)

        try:
            cmd_args = [
                project_dir,
                project_name,
                "-process", program_name,
                "-noanalysis",
                "-scriptPath", str(GHIDRA_SCRIPT_DIR),
                "-postScript", "sgi_import_symbols.py",
                result_path, symbols_json_path,
            ]

            returncode, stdout, stderr = await _run_analyze_headless(
                cmd_args, timeout=timeout
            )

            if returncode != 0:
                output_tail = "\n".join(stdout.split("\n")[-20:])
                raise RuntimeError(f"Symbol import failed:\n{output_tail}")

            if os.path.exists(result_path) and os.path.getsize(result_path) > 0:
                with open(result_path, "r") as f:
                    result = json.load(f)
            else:
                result = {"imported": "?", "errors": ["No output from script"]}
        finally:
            if os.path.exists(result_path):
                os.unlink(result_path)

    finally:
        if os.path.exists(symbols_json_path):
            os.unlink(symbols_json_path)

    imported = result.get("imported", 0)
    skipped = result.get("skipped", 0)
    errors = result.get("errors", [])

    lines = [
        f"# Symbol Import: {filename}",
        "",
        f"**Imported:** {imported}",
        f"**Skipped:** {skipped}",
    ]
    if errors:
        lines.append(f"**Errors:** {len(errors)}")
        for err in errors[:10]:
            lines.append(f"  - {err}")

    return "\n".join(lines)


async def ghidra_disassemble(
    filename: str,
    address: str,
    count: int = 50,
    timeout: int = 60,
) -> str:
    """Get Ghidra's disassembly with labels and comments."""
    result = await run_script(
        filename,
        "sgi_disassemble.py",
        [address, str(count)],
        timeout=timeout,
    )

    lines = []

    func_info = result.get("function")
    if func_info:
        lines.append(
            f"**Function:** {func_info['name']} "
            f"(entry: `{func_info['entry']}`, {func_info['size']} bytes)"
        )
        lines.append("")

    lines.append("```")
    for insn in result.get("instructions", []):
        prefix = ""
        if insn.get("function_entry"):
            lines.append("")
            lines.append(f"; ---- {insn['function_entry']} ----")
        if insn.get("labels"):
            for label in insn["labels"]:
                lines.append(f"{label}:")

        addr_str = insn["address"]
        mnemonic = insn["mnemonic"]
        operands = insn.get("operands", "")
        comment = ""
        if insn.get("comment"):
            comment = f"  ; {insn['comment']}"

        lines.append(f"  {addr_str}  {mnemonic:10s} {operands}{comment}")
    lines.append("```")

    for err in result.get("errors", []):
        lines.append(f"**Error:** {err}")

    return "\n".join(lines)
