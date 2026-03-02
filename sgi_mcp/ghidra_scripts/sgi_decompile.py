# SGI PROM decompilation script for Ghidra headless mode
# Decompiles function(s) and outputs C pseudocode as JSON.
# @category SGI
# @author SGI PROM MCP Server

import json

from ghidra.app.decompiler import DecompInterface, DecompileOptions
from ghidra.util.task import ConsoleTaskMonitor

args = getScriptArgs()
output_path = args[0] if len(args) > 0 else "/tmp/sgi_decompile_result.json"
target_addr = args[1] if len(args) > 1 else "all"
max_functions = int(args[2]) if len(args) > 2 else 10

program = currentProgram
fm = program.getFunctionManager()
space = program.getAddressFactory().getDefaultAddressSpace()
monitor = ConsoleTaskMonitor()

# Set up decompiler
decomp = DecompInterface()
opts = DecompileOptions()
decomp.setOptions(opts)
decomp.openProgram(program)

result = {"functions": [], "errors": []}

try:
    if target_addr == "all":
        funcs = list(fm.getFunctions(True))[:max_functions]
    else:
        # Parse hex address
        addr_val = int(target_addr.replace("0x", ""), 16)
        addr = space.getAddress(addr_val)
        func = fm.getFunctionContaining(addr)
        if func is None:
            result["errors"].append("No function at address %s" % target_addr)
            funcs = []
        else:
            funcs = [func]

    for func in funcs:
        try:
            decomp_result = decomp.decompileFunction(func, 30, monitor)
            if decomp_result.decompileCompleted():
                c_code = decomp_result.getDecompiledFunction().getC()
                sig = decomp_result.getDecompiledFunction().getSignature()
                result["functions"].append({
                    "address": "0x%08x" % func.getEntryPoint().getOffset(),
                    "name": func.getName(),
                    "signature": sig if sig else "",
                    "c_code": c_code if c_code else "",
                    "size": func.getBody().getNumAddresses(),
                })
            else:
                result["errors"].append(
                    "Decompile failed for %s at 0x%08x: %s" % (
                        func.getName(),
                        func.getEntryPoint().getOffset(),
                        decomp_result.getErrorMessage() or "unknown error"
                    )
                )
        except Exception as e:
            result["errors"].append(
                "Error decompiling %s: %s" % (func.getName(), str(e))
            )
finally:
    decomp.dispose()

with open(output_path, "w") as f:
    json.dump(result, f, indent=2)
