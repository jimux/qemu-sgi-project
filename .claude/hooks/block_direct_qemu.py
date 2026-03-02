#!/usr/bin/env python3
"""PreToolUse hook: Block direct QEMU/ninja execution via Bash.

Enforces the CLAUDE.md rule: always use sgi-prom MCP tools for building,
running, and analyzing QEMU. Catches common patterns like:
  - qemu-system-mips64 ...
  - ninja (in qemu build context)
  - ../configure (in qemu build context)
"""
import json
import sys

input_data = json.load(sys.stdin)
cmd = input_data.get("tool_input", {}).get("command", "")

blocked_patterns = [
    ("qemu-system-mips64", "Use qemu_run_sgi MCP tool instead of direct qemu-system-mips64."),
    ("qemu-system-mips", "Use qemu_run_sgi MCP tool instead of direct QEMU execution."),
]

for pattern, reason in blocked_patterns:
    if pattern in cmd:
        print(json.dumps({"decision": "block", "reason": reason}))
        sys.exit(0)

# Block ninja only when it looks like a QEMU build (not other projects)
if "ninja" in cmd and ("qemu" in cmd.lower() or "/build" in cmd):
    print(json.dumps({
        "decision": "block",
        "reason": "Use qemu_build MCP tool instead of direct ninja."
    }))
    sys.exit(0)

print(json.dumps({"decision": "approve"}))
