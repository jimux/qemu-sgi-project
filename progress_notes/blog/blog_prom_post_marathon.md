# First Indy PROM boot in QEMU

## Starting Off

The MAME emulation of Indy was a fantastic project that faithfully emulates a full SGI Indy. It's excellent for preservation, and follows MAME's strict cycle-for-cycle emulation. I personally get a little nervous every time I power on one of my old SGIs, so it's great to have something I can play around with. But what I really wanted was that old IRIX feel, but with modern performance.

So I set out to boot a 1994 SGI Indy workstation inside QEMU. There is an nine year old `qemu-irix` project, but it doesn't actually boot IRIX. It does userland emulation of binaries, and as I undestand it was really only created to assist with Nintendo 64 emulation efforts. It was never intended for merge back into QEMU, and is many years out of date. So we needed to start largely from scratch with the current QEMU.

The machine definition file existed but was nearly empty: a MIPS R4000 CPU, some RAM, and a serial port. No memory controller. No interrupt controller. No SCSI. No graphics. No timers. Just a CPU that could fetch instructions from the PROM and immediately crash. Thankfully, between the IRIX 6.5.5 source and the MAME implementation, I had the essential data necessary to string this together. 

Many tribulations later, the PROM had booted and I was staring at the System Maintenance Menu on a serial console, and it was responding to keyboard input. Getting there required implementing eight separate hardware subsystems, each one discovered when the PROM tried to access it and got a bus error.

## The Approach: Let the PROM Tell You What It Needs

QEMU has a beautifully useful feature for hardware bring-up: the `-d unimp` debug flag. When guest code accesses a memory address that isn't mapped to any device, QEMU logs a message instead of crashing. The PROM becomes its own specification document. It tells you exactly what hardware it expects, in what order, at which addresses.

The cycle was simple:

1. Boot QEMU with `-d unimp`
2. Watch what address the PROM accesses
3. Look it up in the SGI memory map
4. Find the corresponding MAME implementation
5. Write a QEMU version
6. Rebuild, reboot, see what's next

Some were trivial stubs (return a constant). Others required figuring out hardware protocols and interrupt cascades. The PROM probes hardware in dependency order. It needs the memory controller before it can size memory, needs interrupts before it can handle serial I/O, needs SCSI before it can look for boot devices, etc.

## The Memory Controller: Where Every Address Begins

The first device the PROM touches after the reset vector is the MC (Memory Controller) at `0x1fa00000`. It's the heart of the Indy: it maps RAM, controls DMA, handles ECC, and provides system identification. MAME's `mc.cpp` has a beautifully clean implementation.

The critical register is `SYSID`. The PROM reads it to determine which machine it's running on. Different SGI systems (Indy, Indigo2, Challenge S) have different SYSID values that control which hardware drivers the PROM initializes. While these systems fundamentally share the same architecture, the differences are important. Get this wrong and the PROM initializes the wrong hardware or panics.

But the register that nearly stopped me was `MEMCFG`. The PROM doesn't just read memory configuration, it actively probes memory by writing test patterns and reading them back. The MEMCFG registers tell the MC which address ranges contain valid RAM:

```c
/* Each 16-bit bank config:
 * Base[7:0]  = physical_addr >> 22
 * Size[12:8] = (megabytes / 4) - 1
 * Valid[13]  = bank populated
 */
```

The PROM writes a test pattern, reads it back, and if the values match, marks the bank as valid. But there's a subtlety: for the test to work correctly, reads from *unpopulated* memory regions must return something other than the test pattern. In real hardware, accessing missing memory generates a bus error caught by the MC. In QEMU, we needed "unimplemented device" regions that return zero on reads — allowing the pattern mismatch that tells the PROM "no RAM here."

Without this, the PROM thought it had memory everywhere, corrupted its own stack, and crashed in memory sizing.

## The Interrupt Controller: A Three-Level Cascade

The SGI Indy uses a somewhat unusual interrupt architecture. The INT3 ASIC inside the HPC3 provides two levels of local interrupt registers (LOCAL0 and LOCAL1), each with status and mask registers. Individual device interrupts (SCSI, serial, timers, graphics) map into these local registers, which then feed into the MIPS CPU's hardware interrupt pins (IP2 for LOCAL0, IP3 for LOCAL1).

There's also a set of "mappable" interrupts — a third register that collects miscellaneous sources (keyboard, DMA, parallel port) and feeds them back through LOCAL0 bit 7. It's an interrupt cascade: device → mappable → LOCAL0 → CPU.

The PROM needs interrupts working for the serial console. Without them, it can initialize hardware, but the moment it tries to print a character and wait for the UART to be ready, it hangs. Getting the interrupt wiring right (specifically, which INT3 bits correspond to which hardware, and the cascade from mappable through LOCAL0 to the CPU) was essential before I could see any output.

The MAME code was invaluable here. SGI's interrupt routing is documented in the hardware reference, but the actual bit assignments and cascade logic are much clearer in MAME's `ioc2.cpp` than in any data sheet. I'm certain that team spent considerable time getting things right. To be *very* clear, this effort would have been *impossible* without their work.

## Serial: The First Sign of Life

The Z85C30 SCC (Serial Communications Controller) is the Indy's serial interface. It has two channels. One is for the console. QEMU has a generic Z85C30 implementation, but SGI wires it through the HPC3's peripheral space with specific register offsets and interrupt routing.

Celebraionts were had with the first character appearing on the terminal. After implementing the MC, basic INT3 interrupt routing, and the SCC register interface:

```
SGI Version 5.3 rev B10 R4X00 IP24, 64 MB
```

That single line meant the CPU was running, the PROM had sized memory correctly, and serial TX was working.

## SCSI: The Slow Dance

The PROM spends most of its boot time on SCSI. The WD33C93B controller needs careful initialization. The PROM programs its configuration registers, then scans all 8 SCSI IDs looking for devices. Each empty ID takes several seconds to time out (the controller has to wait for the selection timeout period).

The WD33C93 implementation was the most complex single device. It's a state machine that processes SCSI commands in phases:

1. **Selection**: The controller selects a target device on the bus.
2. **Command**: Sends the SCSI CDB (Command Descriptor Block).
3. **Data Transfer**: DMA to/from the HPC3's descriptor chains.
4. **Status**: The target reports success/failure.
5. **Completion**: The controller generates an interrupt.

Each phase transition generates a specific status code that the PROM's interrupt handler checks. Getting any of these wrong (wrong status code, wrong interrupt timing, not clearing the busy flag) would stall the SCSI scan and I'd be sitting there tapping my fingers.

With no SCSI disks attached, the PROM scans all IDs, finds nothing, and falls through to the "no boot device" path. And that's fine for a start. My first goal is to get to the System Maintenance Menu.

## The RPSS Counter: Time Itself

One of the subtler requirements was the RPSS (Real-time Periodic Sample and Status) counter. The PROM uses it for delay loops. "Wait N microseconds" is implemented as "read RPSS, spin until it advances by N ticks." The RPSS counts at 1 MHz, driven by the same clock that drives the MC.

Without a ticking RPSS counter, the PROM's delay loops become infinite loops. The machine hangs during the first hardware probe that needs a timeout. Implementing it was simple enough. A QEMU timer that increments a counter. But *finding* the hang was harder. The `-d unimp` output didn't help because RPSS was mapped; it just never changed. The clue was that the PROM was reading the same address repeatedly, millions of times, getting the same value back.

## The PIT: Three Timers, Two That Matter

The 8254 PIT (Programmable Interval Timer) provides three independent timer channels. Timer 0 feeds INT3 LOCAL0 (mapped to CPU IP4), Timer 1 feeds INT3 LOCAL0 (CPU IP5), and Timer 2 is used for speaker and delay calibration.

The PROM uses Timer 2 for calibration delay loops during POST. It doesn't program Timers 0 or 1. Those are for the OS. But Timer 2 needs to count correctly, or the PROM's calibration produces nonsensical values and it prints warnings about clock drift.

I implemented all three channels, with the correct IRQ routing to INT3. Later, I'd discover that the IRIX kernel doesn't use Timers 0 or 1 at all, but rather it uses the R4000's internal Count/Compare timer for scheduling. But the PROM needs Timer 2, and that was enough for now.

## Graphics: Just Enough to Not Crash

The PROM probes the GIO64 graphics slot looking for a Newport (XL) graphics board. If it finds one, it initializes it and uses it for the textport display. If it doesn't find one, it falls back to serial-only console mode.

We didn't need full Newport graphics for the first boot. We need just enough that the PROM's probe didn't crash. That meant returning a valid board ID from the DCB (Display Control Bus) and handling the basic REX3 register reads without faulting.

The Newport stub returned `0xf` for the board revision and zeroes for most everything else. The PROM probe ran, decided it had a graphics board, and then... tried to initialize it. Each initialization step accessed registers I hadn't implemented, producing a cascade of `unimp` warnings but no crashes (because I had set up catch-all handlers that accepted writes and returned zeroes on reads).

The textport rendered nothing visible. Our VRAM was all zeroes and we had no display update path, but the PROM didn't care. It wrote its blue gradient and menu text into VRAM, set up the hardware cursor, and moved on. From the serial console's perspective, the System Maintenance Menu appeared perfectly. From my perspective, there was still much work to be done.

## The 8042 Keyboard Controller: An Unexpected Guest

Late in the boot sequence, the PROM probes for a PS/2 keyboard via the 8042 controller embedded in the IOC2 ASIC. On real hardware, the Indy has a PS/2 keyboard port on the back panel. The 8042 is accessed through HPC3 peripheral space at `0x1fbd9843` (data) and `0x1fbd9847` (status/command).

The PROM sends the `0xAA` self-test command and waits for a response. Without even a stub implementation, the PROM would hang waiting for the keyboard controller. I just implemented basic command handling. Self-test returns `0x55` (pass), disable/enable keyboard, and read command byte. That was enough for the PROM to decide there was no keyboard plugged in and continue to serial-only mode.

## The GR2 Slot Probe: Hardware That Isn't There

One non-obvious requirement: the PROM probes not just the Newport graphics slot but also the GR2 (Extreme/XS) slot at a different GIO64 address. Reading from an unpopulated GIO slot needs to return `0xFFFFFFFF` (all ones) — the value of an unterminated bus. If it returns zero, the PROM thinks there's a device there and tries to initialize it, leading to further accesses to nonexistent hardware.

I created a small "empty slot" device that returns `0xFFFFFFFF` on all reads and ignores all writes. This was one of those ten-minute implementations that would have caused hours of debugging if missed.

## The Moment of Glory

```
System Maintenance Menu

1) Start System
2) Install System Software
3) Run Diagnostics
4) Recover System
5) Enter Command Monitor

Option?
```

Eight new hardware subsystems. A machine running twenty-seven-year-old firmware, responding to my keystrokes on a serial terminal.

The PROM was happy. It had found its memory controller, sized its RAM, configured its interrupt cascade, scanned its empty SCSI bus, initialized its serial port, probed for graphics and keyboards, and presented its menu. From its perspective, it was running on a slightly underequipped Indy. It had no disks, no keyboard, and no network. But an apparent real machine.

## What Came Next

This was only the beginning. The System Maintenance Menu is the firmware UI. IRIX hadn't loaded yet, and how IRIX interacts with the hardware differs significantly. Booting an actual operating system would require:

- A working SCSI disk with a valid volume header
- DMA transfers through the HPC3's descriptor chains
- A disc image with the IRIX installer
- Multi-pass DMA for large kernel loads
- CP0 Count/Compare timer for the scheduling clock
- And so so much more.

But those were for later. Seeing the PROM ask "Option?" was enough.

## Reflections

The most useful tool wasn't any debugger or profiler. It was QEMU's `unimp` logging. Every unimplemented hardware access was a task item. The PROM, by trying to use hardware in dependency order, naturally created a prioritized implementation queue.

The most useful general resource was MAME. SGI's hardware documentation exists but is scattered across technical reference manuals, data sheets, and kernel source comments. MAME's implementation is a working reference when the docs are ambiguous about register bit assignments or interrupt routing. MAME provided ground truth (or at least a tested interpretation, sometimes ambiguity remains).

It seems the approach of "let the firmware drive implementation order" worked remarkably well. You don't need to understand the entire hardware architecture upfront. You need to understand the next thing the PROM is trying to do, implement it, and move on. The PROM is a very patient teacher. It will try the same thing over and over until you get it right, and when you do, it immediately shows you the next lesson. I had fear that getting it to do *anything* at first would require significantly more upfront.
