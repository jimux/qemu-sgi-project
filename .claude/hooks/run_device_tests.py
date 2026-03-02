#!/usr/bin/env python3
"""PostToolUse hook: Auto-run relevant fast tests after editing SGI device files.

Maps edited C/H files to their corresponding test files and runs them.
Only triggers for SGI device files, not for test files or other code.
"""
import json
import os
import subprocess
import sys

input_data = json.load(sys.stdin)
path = input_data.get("tool_input", {}).get("file_path", "")
base = os.path.basename(path)

# Map device source/header files to their test files
TEST_MAP = {
    "sgi_mc.c":       ["test_mc_source.py"],
    "sgi_mc.h":       ["test_mc_source.py"],
    "sgi_hpc3.c":     ["test_hpc3_source.py", "test_hpc3_subsystems.py"],
    "sgi_hpc3.h":     ["test_hpc3_source.py", "test_hpc3_subsystems.py"],
    "sgi_newport.c":  ["test_newport_source.py", "test_newport_drawing.py"],
    "sgi_newport.h":  ["test_newport_source.py"],
    "sgi_indy.c":     ["test_machine_wiring.py", "test_machine_stubs.py"],
    "sgi_arcs.c":     ["test_machine_stubs.py"],
    "sgi_arcs.h":     ["test_machine_stubs.py"],
    "wd33c93.c":      ["test_scsi_source.py"],
    "wd33c93.h":      ["test_scsi_source.py"],
}

tests = TEST_MAP.get(base)
if tests:
    # Find the project root (directory containing tests/)
    project_root = path
    for _ in range(10):
        project_root = os.path.dirname(project_root)
        if os.path.isdir(os.path.join(project_root, "tests")):
            break
    else:
        print(json.dumps({"decision": "approve"}))
        sys.exit(0)

    test_paths = [os.path.join(project_root, "tests", t) for t in tests]
    existing = [t for t in test_paths if os.path.exists(t)]

    if existing:
        result = subprocess.run(
            ["python3", "-m", "pytest"] + existing + ["-x", "-q", "--tb=short"],
            capture_output=True, text=True, timeout=30,
            cwd=project_root,
        )
        # Print test output to stderr so it's visible but doesn't interfere
        output = result.stdout.strip()
        if output:
            sys.stderr.write(f"\n--- Device tests for {base} ---\n")
            sys.stderr.write(output + "\n")
            sys.stderr.write("---\n\n")
        if result.returncode != 0 and result.stderr.strip():
            sys.stderr.write(result.stderr.strip() + "\n")

print(json.dumps({"decision": "approve"}))
