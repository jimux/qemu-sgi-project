# IP35 PROM Binary Analysis Summary

## File: ip35prom.img
- **Size**: 1.4 MB (1,477,560 bytes)
- **SHA256**: 9361066dd30585118c513e7d42fa7350a05839b13a376de1e523db65ebda7e74
- **Format**: SN1 Container (JFKSWCSM magic at offset 0x40)

## Header Information
- **Module**: ip35prom
- **Platform**: SN1 (Bedrock ASIC)
- **IP Board**: IP35
- **Load Address**: 0xc0000000_1fc00000
- **Code Offset**: 0x1000
- **Entry Point**: 0xBFC00400 (confirmed - different from IP27's 0xBFC00800!)
- **Version**: SGI Version 6.170, built Aug 6, 2003
- **Build Flags**: -DIP35 -DSN1 -DSN -DMP -DNUMA_BASE -mips4 -64

## Critical Findings from String Analysis

### L1 Controller (ELSC) - Status: TIMEOUT HANDLING
Boot log shows timeout handling for ELSC communication. It does NOT block indefinitely.

### UART/Serial Console
Uses IOC3/IOC4 UART for console output.

### Bedrock References
Confirmed Bedrock ASIC usage. Revision 1.1+ required.

## Hardware Architecture Confirmation

- Bedrock ASIC: SN1 (Origin 3000)
- Crossbar: PIC (part 0xd100) for Tezro, XXBow (part 0xd000) for Fuel
- Max CPUs: 4 CPUs (2 PI blocks: PI_0 and PI_1)
- Max RAM: 16 GB (8 banks x 2 GB DDR)

## Key Unknowns to Resolve via Disassembly

### 1. KLCONFIG Memory Address
Need to find where PROM writes KLCONFIG structure.

### 2. Bedrock/Register Offsets
Confirm PI_1 base offset and any differences from Hub.

### 3. ARCS64 vs ARCS Structure Layout
Verify SPB structure alignment differences.

## Conclusion

- ✅ SN1/Bedrock platform confirmed
- ✅ Entry point at 0xBFC00400
- ✅ L1 controller has timeout handling
- ⏳ KLCONFIG address unknown (need disassembly)
