# SGI PROM cross-reference script for Ghidra headless mode
# Finds references to/from an address.
# @category SGI
# @author SGI PROM MCP Server

import json

args = getScriptArgs()
output_path = args[0] if len(args) > 0 else "/tmp/sgi_xrefs_result.json"
target_addr_str = args[1] if len(args) > 1 else "0xbfc00000"
direction = args[2] if len(args) > 2 else "both"

program = currentProgram
ref_mgr = program.getReferenceManager()
fm = program.getFunctionManager()
space = program.getAddressFactory().getDefaultAddressSpace()

addr_val = int(target_addr_str.replace("0x", ""), 16)
target_addr = space.getAddress(addr_val)

result = {"address": target_addr_str, "refs_to": [], "refs_from": []}

# References TO this address (who calls/accesses this?)
if direction in ("to", "both"):
    refs = ref_mgr.getReferencesTo(target_addr)
    for ref in refs:
        from_addr = ref.getFromAddress()
        from_func = fm.getFunctionContaining(from_addr)
        result["refs_to"].append({
            "from_address": "0x%08x" % from_addr.getOffset(),
            "from_function": from_func.getName() if from_func else None,
            "ref_type": ref.getReferenceType().getName(),
            "is_call": ref.getReferenceType().isCall(),
        })

# References FROM this address (what does this address reference?)
if direction in ("from", "both"):
    refs = ref_mgr.getReferencesFrom(target_addr)
    for ref in refs:
        to_addr = ref.getToAddress()
        to_func = fm.getFunctionContaining(to_addr)
        result["refs_from"].append({
            "to_address": "0x%08x" % to_addr.getOffset(),
            "to_function": to_func.getName() if to_func else None,
            "ref_type": ref.getReferenceType().getName(),
            "is_call": ref.getReferenceType().isCall(),
        })

with open(output_path, "w") as f:
    json.dump(result, f, indent=2)
