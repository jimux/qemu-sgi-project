#!/usr/bin/env python3
"""PostToolUse hook: Remind to check CLAUDE.md after writing progress notes.

When a new or updated progress note is written, prints a reminder to check
whether the Key Technical Findings or Current Status sections of CLAUDE.md
need updating too.
"""
import json
import sys

input_data = json.load(sys.stdin)
path = input_data.get("tool_input", {}).get("file_path", "")

if "/progress_notes/" in path and path.endswith(".md"):
    sys.stderr.write(
        "\nReminder: Check if CLAUDE.md 'Key Technical Findings' or "
        "'Current Status' sections need updating to reflect this note.\n\n"
    )

print(json.dumps({"decision": "approve"}))
