# IP54 Interrupt Wiring Progress (2026-03-17)

## Goal
Interrupt-driven RX for pvnet to reduce packet latency from ~10ms (polling) to <1ms.

## QEMU Side — COMPLETE AND TESTED

### Changes (`qemu-sgi-repo/hw/mips/sgi_ip54pv.c`)

1. **HEART shim IRQ interposition**: PV devices connect through `heart_irqs[]` GPIO
   inputs instead of directly to CPU pins.
   - `ip54_heart_irq_handler()` — GPIO input from devices → ISR bits 20-22
   - `ip54_heart_update_irq()` — evaluates ISR & IMR[0] LEVEL1 bits, raises/lowers CPU IP4
   - **Edge detection** (`irq_level` field) — only calls `qemu_irq_raise/lower` on
     state transitions, preventing interference with pvclock's SW2 mechanism

2. **CLR_ISR gpio_level re-assertion**: When kernel writes CLR_ISR, any GPIO-held
   bits are immediately re-asserted (handles heart_intr's pre-handler CLR_ISR pattern)

3. **IMR0 write triggers update_irq**: Needed for `heart_intr_preconn` enabling

### Testing
- Golden kernel boots and pings successfully with the HEART shim rewiring
- Edge detection fix eliminates pvclock disruption (was causing lost clock ticks)
- Ping: 4/5 packets, ~9.6ms RTT (matches 10ms poll interval)

### Critical Bug Found & Fixed
- **IRQ bounce**: Without edge detection, every received packet triggered
  `qemu_irq_raise` then `qemu_irq_lower` on IP4, even with IMR[0]=0. The lower
  call entered `cpu_mips_irq_request(4,0)` which could clear `CPU_INTERRUPT_HARD`
  set by the pvclock, losing clock ticks. This caused boot instability and ping
  failures. Fixed with `irq_level` state tracking.

### pvuart debug traces (can be removed)
- `sgi_pvuart.c` has fprintf debug traces for RX/LSR — remove before release

## Kernel Side — COMPILED, NOT YET VERIFIED

### Changes

**IP54.c** (`ip54_tftp_staging/IP54.c`):
- `ip54_intr_init()` — currently a stub (see blockers below)
- Called from `if_pvnetedtinit()` during device init

**if_pvnet.c** (`ip54_tftp_staging/if_pvnet.c`):
- `pvnet_intr(intr_arg_t)` — interrupt handler for RX/TX completion
- `PVNET_INTR_MASK = PVNET_INTR_RX_DONE` in `pvnet_init()`
- `PVNET_POLL_TICKS` changed to 100 (1 second fallback); was tested at 2 (20ms)
- `ip54_intr_init()` called from `if_pvnetedtinit()`

### Blockers

1. **heart_ivec struct size mismatch**: `heart_ivec_t` is defined in `heart_vec.h`
   which includes `kthread.h` and `ksys/xthread.h` — deep header dependencies not
   available on the compilation environment. Using a local struct with wrong size
   causes array offset miscalculation → crash at BadAddr 0x39.

   **Fix needed**: Either upload `heart_vec.h` + dependencies, or determine the
   correct struct size (likely ~32 bytes on N32) and use a correctly-sized local typedef.

2. **Kernel layout sensitivity**: Any change to .o file sizes shifts the kernel
   binary layout, causing the PROM's runtime patches to target wrong addresses.
   Even with a stub `ip54_intr_init`, the different if_pvnet.o size triggers crashes
   during device init (PC 0x880d6e5c, unrelated to our code).

   **Fix needed**: The new if_pvnet.o must match the golden if_pvnet.o's size exactly,
   or the PROM patches need to be more robust (pattern-based rather than offset-based).

3. **RAW_HDRPAD macro**: Our stub `net/raw.h` initially used `RAW_HDRPAD(x) = (x)`
   instead of the correct `(((x) + 7) & ~7)`. This caused a 2-byte etherbufhead
   misalignment, corrupting all received packets. Fixed in the stub header.

## Operational Lessons

- **S23autoconfig must be disabled**: The init-time lboot (via `/etc/rc2.d/S23autoconfig`)
  rebuilds /unix.new from `irix.sm` (which uses `sduart`, not `pvuart_cn`), overwriting
  the IP54 kernel. Move S23autoconfig to `disabled/`.

- **lboot creates unix.new in CWD**: Always run `cd / && lboot ...` to ensure
  /unix.new lands at the root.

- **Future-date /unix.new**: `touch -t 203001010000 /unix.new` prevents lboot from
  thinking the kernel needs rebuilding (its timestamp check compares .o mtimes).

- **RC script crashes**: Many N32 userland binaries crash on IP54 kernel. Disable:
  S50*, S58*, S60*, S62*, S63*, S75*, S76*, S88*, S90*, S95-S99* in `/etc/rc2.d/`.

- **cc_wrapper must be persistent**: Place at `/var/sysgen/boot/cc_wrapper` (not
  `/tmp/cc` which is cleared on reboot). Update CC: line in both IP54.sm and irix.sm.

- **Serial upload truncation**: Long lines with complex syntax (nested `if/else`,
  backtick expressions) get truncated during `qemu_serial_write_file`. Use simple
  multi-line formatting with one statement per line.

## Next Steps

1. Determine correct `heart_ivec_t` struct size (read from golden kernel binary
   or check `sizeof` on IRIX with full headers)
2. Fix ip54_intr_init to use correctly-sized array access
3. Alternatively: have the PROM wire the interrupt (PROM can write heart_ivec
   directly since it runs before the kernel)
4. Test interrupt-driven ping latency (should drop from ~10ms to ~1ms)

---

## 2026-06-12 — interrupt wiring is BLOCKED by an intr()-return crash

While pursuing interrupt-driven keyboard/mouse (to fix the dead mouse pointer /
flaky keyboard — see mouse_input_investigation.md), found the real blocker.

**The pvnet IP4 c0vec trampoline (Patch 36 in ip54_stubs.c) is DISABLED**
(`if (0 && ...)`), with the comment:
> DISABLED: IP4 dispatch trampoline crashes in intr() return path.
> Root cause TBD — TLB fault in unsema during interrupt return.
> For now, pvnet uses fast polling (PVNET_POLL_TICKS=2, ~20ms).

So **every** c0vec_tbl trampoline interrupt on IP54 crashes on return — this is
the shared blocker for ALL interrupt-driven devices (pvnet RX and the 8042
keyboard/mouse). The kernel/QEMU input path is otherwise verified correct:
- 8042 delivers correct PS/2 bytes; pckm drains 32/poll → /dev/input/mouse OK.
- The mouse stack (pckm/shmiq/Xsgi) is identical to a real Indy, where it works;
  the ONLY input-relevant emulation difference is poll-vs-interrupt context.

### Why this is newly tractable
The prior attempt had **no live kernel debugger** (root cause was "TBD"). This
session built **working guest GDB** (`set mips abi n64`, see memory
guest_gdb_ip54.md) — hardware breakpoints fire, live KSEG0 memory/disasm work.
So the "TLB fault in unsema during interrupt return" can now be caught and
root-caused: re-enable Patch 36 (`if (0 &&` → `if (`), rebuild PROM, boot under
`-gdb`, hbreak the fault path, inspect the intr() return / unsema state.

### Caveat (doubly-uncertain payoff)
Even after fixing the intr()-return crash, whether interrupt-driven delivery
fixes the MOUSE depends on the unproven hypothesis that the closed shmiq→Xsgi
pointer-event delivery requires interrupt (not callout) context. Keyboard
reliability is the more likely win.

### Plan if pursued
1. Re-enable Patch 36; rebuild PROM (→ PROM_library/bins/cpu/ip54/ip54.bin).
2. Boot ip54-test under `-gdb tcp::PORT`; let pvnet RX fire the IP4 trampoline.
3. GDB-catch the TLB fault in the intr() return / unsema path; root-cause
   (eframe corruption? spl/nesting? bad $ra? KSEG3 page-table miss in the
   return path like the zone_shake fault?).
4. Fix, verify pvnet interrupt-driven, then wire the 8042 IRQ (QEMU: ioc2-kbd
   IRQ → a HEART ISR bit; PROM: c0vec trampoline → pckm_intr) and retest mouse.

---

## 2026-06-12 (cont.) — GDB debug: the "crash" is stale; context isn't the mouse issue

Re-enabled Patch 36 (pvnet IP4 trampoline), rebuilt PROM, booted under guest GDB.
Two findings that reshape this work:

1. **The documented intr()-return crash was wrong-handler dispatch, already fixed.**
   The QEMU HEART `ip54_heart_update_irq()` comment (sgi_ip54pv.c ~L305) records:
   routing pvnet to IP4 → `c0vec_tbl[5]` (the CLOCK handler) is what "crashes in
   semaphore code"; the fix was to route pvnet to **IP3 → `c0vec_tbl[4]`** (safe
   INT2/INT3 slot). So the crash = the pvnet interrupt being dispatched to the
   clock handler, not a generic intr()-return bug. The `ip54_stubs.c`
   "DISABLED: crashes in intr() return path" comment is STALE (predates the IP3 fix).

2. **The trampoline (now installed at c0vec_tbl[4]=0x8805524c → pvnet_intr
   0x8802b8c4) never fires.** GDB hbreak on it: 0 hits across 30 pings; RTT stayed
   ~9.6ms (poll latency). Cause: the golden kernel runs pvnet in POLLING mode and
   never sets `PVNET_INTR_MASK=RX_DONE`, so pvnet never asserts its QEMU IRQ/GPIO →
   no HEART ISR bit 20 → no IP3 → trampoline dormant. (pvnet is a bad debug vehicle:
   needs a kernel change to fire.)

3. **Interrupt wiring probably won't fix the MOUSE.** Keyboard reaches Xsgi (xlogin
   logins succeed ~50-66%) through the SAME poll→pckm_intr→shmiq→Xsgi path the mouse
   uses. If poll context delivers keyboard events to X, poll context is sufficient
   for shmiq→X — so the dead pointer is mouse-specific in the closed shmiq/Xsgi
   pointer path, NOT a poll-vs-interrupt-context problem. The interrupt-context
   hypothesis for the mouse is largely refuted.

### Implication
The 8042 IS interrupt-ready (guest set cmd_byte=0x03 KBD_INT|MOUSE_INT) but its QEMU
IRQ is unconnected. Wiring it (→ HEART ISR bit → IP3 → extend the c0vec_tbl[4]
trampoline to call pckm_intr) WOULD give interrupt-driven input (latency / keyboard-
reliability / HW-accuracy wins) and is now de-risked (IP3 routing avoids the clock-
handler crash). But it is UNLIKELY to fix the mouse pointer. The mouse fix lives in
the closed shmiq/Xsgi pointer-event path (see mouse_input_investigation.md).

PROM state: Patch 36 re-enabled (dormant — harmless). Backup at
PROM_library/bins/cpu/ip54/ip54.bin.prepatch36 if revert is wanted.

---

## 2026-06-12 (cont.) — 8042 interrupt wired; trampoline works; handler crashes in IRQ context

Implemented interrupt-driven keyboard/mouse end to end and debugged it with GDB.

### Done
- **QEMU** (sgi_ip54pv.c): allocate a 4th HEART GPIO (`heart_irqs[3]` → ISR bit 23);
  route bits 20|23 → IP3 (exclude 23 from IP4); `sysbus_connect_irq` the
  sgi-ioc2-kbd 8042 → `heart_irqs[3]`. (Harmless when the PROM leaves bit 23
  masked — verified the stable PROM still boots to login with this QEMU build.)
- **PROM** (ip54_stubs.c, Patch 36): rewrote the c0vec_tbl[4] trampoline to
  dispatch the 8042: check 8042 status (KSEG1 0xBFBD9847 & 0x21), then call
  `lcl2vec_tbl[5].isr(arg,0)` (the handler the kernel installs for VECTOR_KBDMS;
  pckm_intr is static so kern_sym can't resolve it — call via the table).
  `kern_sym("lcl2vec_tbl")` resolves (= 0x8827e5b0).  Removed the old block that
  overwrote c0vec_tbl[4] with pvnet_intr directly.

### Bug found & fixed (GDB)
- First boot: `PANIC: Read Address Error, Bad addr 0xa1`.  GDB hbreak on the
  trampoline + disasm of the actual bytes showed `lw t9,160(k0)` — my
  `0x8F5900A0` encoded base **$k0 ($26)** instead of **$t2 ($10)**; `$k0`=1 so
  `0xA0($k0)` = 0xA1.  Fixed to `0x8D5900A0` (the `$a0` load `0x8D4400A4` was
  already correct).  (gdb n64 names $10 "a6" and "$t2"→$14 — that misled an
  earlier read; the disasm `lui a6,0x8827` / `lw a0,164(a6)` confirms $10 is the
  base.)

### Remaining blocker (deep)
After the fix, the trampoline fires and **correctly** loads `$t9 =
lcl2vec_tbl[5].isr` (0x88064854, a valid handler) and `jalr`s it with a0=0,a1=0
— exactly as `du_pckm_poll` does.  But the call **crashes intermittently when
invoked from hard-interrupt (c0vec) context**: GDB-caught panic shows BadVAddr
0x10037fec, backtrace through `pvfbioctl` / `wd93intr` / `icmn_err`.  The local
interrupt handler expects the kernel's local-interrupt dispatch context (spl /
thread / eframe setup), not a direct call from a c0vec trampoline.  `du_pckm_poll`
gets away with the same direct call because it runs in benign callout context.
This is the real interrupt-context incompatibility (and it confirms the earlier
prediction that interrupt-context, not byte-delivery, is the variable — though
it does NOT fix the mouse, since keyboard already works via the poll path).

### State (safe)
- Trampoline code in source is CORRECT but **disabled** (`if (0 && ...)`); bit 23
  IMR0 unmask also disabled.  Re-enable BOTH together to resume.
- Installed PROM = source-consistent stable (boots to login).  Backup of the
  pre-Patch-36 PROM: PROM_library/bins/cpu/ip54/ip54.bin.prepatch36.
- QEMU 8042-IRQ wiring left in place (harmless with bit 23 masked).

### To resume
Make `lcl2vec_tbl[5].isr` safe to call from c0vec context — likely the trampoline
must replicate the kernel's local-interrupt entry setup (spl/eframe/thread), or
invoke the proper local-dispatch path rather than the raw handler.  Inspect how
intr() / the IP22 local-interrupt handler normally sets up before calling
lcl2vec handlers, and mirror that in the trampoline.
