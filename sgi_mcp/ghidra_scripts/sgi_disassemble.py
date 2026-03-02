# SGI PROM disassembly script for Ghidra headless mode
# Returns Ghidra's disassembly with labels, comments, and function boundaries.
# @category SGI
# @author SGI PROM MCP Server

import json

args = getScriptArgs()
output_path = args[0] if len(args) > 0 else "/tmp/sgi_disassemble_result.json"
target_addr_str = args[1] if len(args) > 1 else "0xbfc00000"
count = int(args[2]) if len(args) > 2 else 50

program = currentProgram
listing = program.getListing()
fm = program.getFunctionManager()
space = program.getAddressFactory().getDefaultAddressSpace()

addr_val = int(target_addr_str.replace("0x", ""), 16)
addr = space.getAddress(addr_val)

result = {"address": target_addr_str, "instructions": [], "errors": []}

func = fm.getFunctionContaining(addr)
if func:
    result["function"] = {
        "name": func.getName(),
        "entry": "0x%08x" % func.getEntryPoint().getOffset(),
        "size": func.getBody().getNumAddresses(),
    }

insn = listing.getInstructionAt(addr)
if insn is None:
    # Try to find the nearest instruction
    insn = listing.getInstructionAfter(addr)
    if insn is None:
        result["errors"].append("No instruction at or after %s" % target_addr_str)

i = 0
while insn is not None and i < count:
    insn_addr = insn.getAddress()

    # Check for function boundary
    insn_func = fm.getFunctionContaining(insn_addr)
    func_name = None
    if insn_func and insn_addr.equals(insn_func.getEntryPoint()):
        func_name = insn_func.getName()

    # Get any pre-comment or plate comment
    pre_comment = insn.getComment(0)  # PRE_COMMENT
    plate_comment = listing.getComment(2, insn_addr)  # PLATE_COMMENT

    # Get label(s) at this address
    symbols = program.getSymbolTable().getSymbols(insn_addr)
    labels = []
    for sym in symbols:
        if not sym.isDynamic():
            labels.append(sym.getName())

    insn_info = {
        "address": "0x%08x" % insn_addr.getOffset(),
        "bytes": " ".join(["%02x" % (insn.getByte(j) & 0xff) for j in range(insn.getLength())]),
        "mnemonic": insn.getMnemonicString(),
        "operands": insn.getDefaultOperandRepresentation(0),
    }

    # Add second operand if present
    if insn.getNumOperands() > 1:
        op1 = insn.getDefaultOperandRepresentation(1)
        if op1:
            insn_info["operands"] += ", " + op1
    if insn.getNumOperands() > 2:
        op2 = insn.getDefaultOperandRepresentation(2)
        if op2:
            insn_info["operands"] += ", " + op2

    if func_name:
        insn_info["function_entry"] = func_name
    if labels:
        insn_info["labels"] = labels
    if pre_comment:
        insn_info["comment"] = pre_comment

    result["instructions"].append(insn_info)
    insn = insn.getNext()
    i += 1

with open(output_path, "w") as f:
    json.dump(result, f, indent=2)
