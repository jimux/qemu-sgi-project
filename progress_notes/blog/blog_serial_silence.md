# The Three-Bit Mask: How a Serial Controller Issue Hid Behind an Interrupt Storm

## The Silence

The IRIX 6.5 miniroot kernel was booting well. SCSI commands completed, the XFS root filesystem mounted, device creation began. Then the serial console printed:

```
audio: AES receiver not responding.
Creating miniroot devices, please wait...
```

And went silent. Not a crash — the CPU was alive, 61.5% idle according to my PC sampling. It wasn't hung either. Watching the kernel's memory, things were happening. But nothing appeared on the serial console, no matter how long I waited.

This began a debugging odyssey that touched three distinct problems, each masking the symptoms of the next, before arriving at a fix that changed exactly three characters in one line of code.

## First Suspect: Where Do Characters Go?

IRIX has two paths for serial output. The polled path (`du_putchar`) writes directly to the Z85C30 SCC's transmit data register with no interrupts, no buffering, just spin until the TX buffer is empty and shove a byte in. The interrupt-driven path (`du_wput` → `du_save` → `mips_du_start_tx`) is the STREAMS-based path. It queues data, enables the TX interrupt via WR1 bit 1, and lets the interrupt handler drain the queue.

During early boot, the kernel uses `du_putchar` for all console output. This is the polled path. Every character I'd seen so far: the boot messages, the SCSI probe output, the "Creating miniroot devices" line came through this path. The trace data confirmed it: 1,713 TX bytes total, 1,706 with WR1=0x00 (polled mode), 7 with WR1=0x11 (RX interrupts enabled, but TX interrupt still disabled).

The STREAMS TX path had never been used. Not once. WR1 bit 1 (TX_INT_ENBL) was never set during the entire boot.

This wasn't the bug, it was expected behavior. But it meant I couldn't blame the STREAMS path for the silence. Something else was preventing the polled path from running.

## Second Suspect: The Parallel Port That Didn't Exist

When I dumped the INT3 interrupt controller registers I found something alarming:

```
local0_stat = 0x02
local0_mask = 0xa3
```

Bit 1 of `local0_stat` was stuck high. Bit 1 is `LIO_CENTR` — the Centronics parallel port interrupt, from the PI1 parallel port controller. We don't emulate the PI1. Nothing in the code sets this bit. But there it was, asserting an interrupt that could never be acknowledged.

The IRIX kernel's `lcl_stray()` handler is called when an interrupt fires on a LOCAL0 line that has no registered driver. It increments a counter and returns. The problem is that returning from `lcl_stray()` doesn't clear the interrupt source — the hardware is still asserting. So the interrupt fires again immediately:

```
spl0() → IP2 fires → lcl0_intr() → lcl_stray() → return
→ IP2 still pending → fires again → lcl_stray() → return
→ IP2 still pending → fires again → ...
```

Five hundred times per second, the CPU entered the interrupt handler, counted a stray, and returned. This wasn't enough to completely starve the system, the idle loop still ran between interrupt storms, but it was consuming significant CPU time and interfering with thread scheduling.

### The Side Discovery: IRIX Uses the R4000 Timer, Not the PIT

While investigating the interrupt storm, I needed to verify that the scheduling clock was still running. This led to an important discovery: IRIX on Indy/Indigo2 boards does NOT use the 8254 PIT for its scheduling clock. It uses the R4000's internal CP0 Count/Compare timer, on hardware interrupt IP7.

```c
// timer.c init_timer()
if (is_ioc1()) {       // true for IOC2 boards (is_ioc1_flag = 2)
    __startrtclock = startrtclock_r4000;  // Uses CP0 Count/Compare
} else {
    __startrtclock = startrtclock_8254;   // Uses PIT (only on old boards)
}
```

The PIT is only used by the PROM for delay loops. The kernel ignores it entirely.

### Fixing the Storm

The fix was straightforward: mask the INT3 `local0_stat` register to only include bits corresponding to hardware we actually emulate:

```c
s->int3_local0_stat &= (INT3_LOCAL0_SCSI0 | INT3_LOCAL0_SCSI1 |
                         INT3_LOCAL0_MAPPABLE0);
```

Unimplemented hardware — parallel port, ethernet (not yet implemented), GIO graphics interrupts — can't set bits in `local0_stat`, so they can't cause stray interrupt storms. The CPU went quiet. The interrupt storm was gone.

But the serial console was still silent.

## The Real Issue: Three Bits vs Four

With the interrupt storm fixed, I could focus on why serial output stopped. The MAKEDEV script was running (creating hundreds of device nodes), processes were scheduling, the kernel was alive, but `du_putchar` wasn't being called.

I went deeper into the Z85C30 register traces. Something strange: the STREAMS TX setup sequence was corrupting registers. The kernel would write a value intended for WR5 (transmit parameters) and it would end up modifying WR13 (baud rate high byte). WR11 (clock mode) writes were hitting WR3 (receive parameters).

The Z85C30 SCC uses an indirect register addressing scheme. To write to any register other than WR0, you first write the register number to WR0 (as the register pointer), then write the data to the same address. The register pointer auto-resets to 0 after each data write.

But WR0 has a dual purpose. Its lower bits select the register pointer, and its upper bits encode a command (null, reset status, channel reset, etc.):

```
WR0 bit layout:
  [2:0] = Register pointer (0-7)
  [5:3] = Command (null, point high, reset ext/status, ...)
  [7:6] = CRC reset code
```

My code was extracting the register pointer with `val & 0x0f`, a four-bit mask. It should have been `val & 0x07` — a three-bit mask.

The Z85C30 has only 16 registers (0-15). To access registers 8-15, you write a "point high" command to WR0 first, which sets an internal flag, and then the three-bit pointer is OR'd with 8. The pointer field is always three bits.

With the four-bit mask, any WR0 write that included a command in bits [5:3] would leak the command bits into the register pointer. For example, the kernel writing `0x28` to WR0 intended: register pointer = 0 (bits [2:0] = 000), command = "reset TX interrupt pending" (bits [5:3] = 101). But the code extracted `0x28 & 0x0f = 0x08`, selecting register 8 instead of register 0. The next data write went to WR8 instead of the intended register.

The cascade of corruption was devastating. The STREAMS TX initialization sequence writes to WR5, WR11, WR14, and WR1 in rapid succession, each preceded by a WR0 write to set the register pointer. With the four-bit mask, every pointer write that included a non-null command directed the subsequent data write to the wrong register:

```
Intended: WR0←5, WR5←0xEA    →  Actual: WR0←(5|cmd), WR13←0xEA
Intended: WR0←1, WR1←0x17    →  Actual: WR0←(1|cmd), WR9←0x17
```

WR5 never got its transmit-enable bits. WR1 never got its interrupt-enable bits. The STREAMS TX path was dead. Not because the software was wrong, but because the hardware emulation was sending data to the wrong registers.

## The Fix

```c
// Before:
s->wr0_reg_ptr = val & 0x0f;

// After:
s->wr0_reg_ptr = val & 0x07;
```

Three characters changed: `0x0f` → `0x07`. One bit less in the mask.

## The Unmasking

With both fixes applied (the INT3 spurious interrupt mask and the WR0 register pointer) I booted again:

```
IRIX Release 6.5 IP22
...
login:
```

The serial console was alive. MAKEDEV completed. Init ran through its runlevels. The login prompt appeared. Success!

## Reflections

The hardest part of this investigation wasn't finding either issue's cause, it was recognizing that there were *two* issues working hand-in-hand.

What saved me was trace data. Just logging every register write and reading the logs carefully. The WR0 corruption was visible in the traces all along, but I didn't recognize it until after the interrupt storm was gone and I could focus on why STREAMS TX wasn't activating.

The Z85C30 WR0 register pointer mask is documented in the Zilog datasheet. The register pointer field is explicitly described as bits [2:0]. I simply hadn't read that part carefully enough. The `0x0f` mask "looked right" for a nibble extraction, and the registers worked fine for the polled path (which only uses WR0 and WR8, never needing the pointer for higher registers with commands).

This is a common pattern in emulation: the easy path works, and the hard path is broken, and you don't discover the breakage until the guest software takes the hard path. PROM used polled serial, which worked fine. Kernel early boot used polled serial, which worked fine. Kernel STREAMS TX setup used the register pointer with commands and silently corrupted.

That was a lot for three characters.
