# SGI PROM symbol import script for Ghidra headless mode
# Imports function names and hardware annotations from our MCP analysis.
# @category SGI
# @author SGI PROM MCP Server

import json

from ghidra.program.model.symbol import SourceType

args = getScriptArgs()
output_path = args[0] if len(args) > 0 else "/tmp/sgi_import_symbols_result.json"
symbols_path = args[1] if len(args) > 1 else ""

result = {"imported": 0, "skipped": 0, "errors": []}

if not symbols_path:
    result["errors"].append("No symbols file path provided")
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
    # Exit early - in Ghidra Jython we can't use sys.exit
else:
    try:
        with open(symbols_path, "r") as f:
            symbols_data = json.load(f)
    except Exception as e:
        result["errors"].append("Failed to load symbols: %s" % str(e))
        symbols_data = {"functions": {}}

    program = currentProgram
    fm = program.getFunctionManager()
    space = program.getAddressFactory().getDefaultAddressSpace()
    listing = program.getListing()

    functions = symbols_data.get("functions", {})
    for addr_str, func_data in functions.items():
        try:
            addr_val = int(addr_str)
            addr = space.getAddress(addr_val)

            name = func_data.get("name") or func_data.get("suggested_name", "")
            if not name:
                result["skipped"] += 1
                continue

            func = fm.getFunctionContaining(addr)
            if func is None:
                # Try to create a function at this address
                func = listing.createFunction(name, addr, None, SourceType.USER_DEFINED)
                if func is None:
                    result["skipped"] += 1
                    continue

            func.setName(name, SourceType.USER_DEFINED)

            # Add hardware access summary as comment
            hw_accesses = func_data.get("hardware_accesses", [])
            if hw_accesses:
                devices = set()
                for access in hw_accesses:
                    dev = access.get("device", "")
                    if dev:
                        devices.add(dev)
                if devices:
                    comment = "HW: %s" % ", ".join(sorted(devices))
                    func.setComment(comment)

            result["imported"] += 1
        except Exception as e:
            result["errors"].append("0x%x: %s" % (addr_val, str(e)))

    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
