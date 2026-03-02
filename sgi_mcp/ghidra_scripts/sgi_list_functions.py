# SGI PROM function listing script for Ghidra headless mode
# Lists all Ghidra-detected functions with metadata.
# @category SGI
# @author SGI PROM MCP Server

import json

args = getScriptArgs()
output_path = args[0] if len(args) > 0 else "/tmp/sgi_list_functions_result.json"
filter_str = args[1] if len(args) > 1 else ""

program = currentProgram
fm = program.getFunctionManager()
ref_mgr = program.getReferenceManager()

result = {"functions": [], "total_count": fm.getFunctionCount()}

for func in fm.getFunctions(True):
    name = func.getName()
    if filter_str and filter_str.lower() not in name.lower():
        continue

    entry = func.getEntryPoint()
    body = func.getBody()

    # Count incoming references (callers)
    caller_count = 0
    refs = ref_mgr.getReferencesTo(entry)
    for _ in refs:
        caller_count += 1

    # Count called functions
    callee_count = len(func.getCalledFunctions(None))

    func_info = {
        "address": "0x%08x" % entry.getOffset(),
        "name": name,
        "size": body.getNumAddresses(),
        "callers": caller_count,
        "callees": callee_count,
        "stack_frame_size": func.getStackFrame().getFrameSize(),
        "is_thunk": func.isThunk(),
    }
    result["functions"].append(func_info)

result["matched_count"] = len(result["functions"])

with open(output_path, "w") as f:
    json.dump(result, f, indent=2)
