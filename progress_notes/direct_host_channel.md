# Direct host↔guest channel for IRIX (no serial, no TFTP)

Exploration of ways to drive the IRIX guest and move files directly from the host, bypassing the serial console and TFTP, on top of the minimal single-user root from `pyirix_qemu/build_minimal_root.py`. Tested on `sgi-ip54` (IRIX 6.5 IP54) booted to a `#` shell.

## TL;DR — what works today

The **QEMU gdbstub is a complete, reliable, serial-free / TFTP-free side channel** into the guest: the host can read and write arbitrary guest memory (and CPU state). Packaged as `pyirix_qemu/host_channel.py`:

```python
import pyirix_qemu.host_channel as hc
hc.write_guest_mem(0xAC000000, b"...bytes...")   # host -> guest RAM
data = hc.read_guest_mem(0xAC000000, n)           # guest RAM -> host
hc.read_word(0x882a0d88)                          # read a kernel global
```

Boot the guest with the gdbstub (`qemu_session_start ... gdb_port=1234`). Verified: a 59-byte binary payload written from the host and read back **matches exactly**, and live kernel globals (e.g. `putbufsz`, `putbufndx`) read correctly — no serial, no TFTP. Addresses are KSEG0/KSEG1 (`0x8xxxxxxx`/`0xAxxxxxxx`), sign-extended to the n64 form; QEMU/TCG has no cache model so KSEG0/KSEG1 alias the same DRAM.

This is the host half. It gives direct memory-level fetch/add/edit. Turning raw RAM access into clean *in-guest file and interactive-console* operations needs a tiny in-guest agent — see below.

## Architecture notes (for any in-guest agent)

- **Console = `sgi_pvuart`** (`hw/char/sgi_pvuart.c`): two polled byte regs at PA `0x1F620178` (off 3 = THR/RBR, off 6 = LSR). It IS the serial device — anything using it uses serial. Backed by `serial_hd(0)`.
- **PV bank** at PA `0x1F480000` (`hw/mips/sgi_ip54pv.c`); free MMIO slots ~`0x1F480700`. New sysbus PV devices map here and raise IRQs through the HEART shim GPIO (`heart_irqs[]`). QEMU device code can DMA to/from guest RAM via `dma_memory_read/write(&address_space_memory, …)`.
- **Kernel patch-at-load** (`prom-building/src/fw/ip54_stubs.c` `Execute()`): resolves any global kernel symbol via `kern_sym()` (from the ELF `.symtab`), synthesizes MIPS with `mips_lui/addiu/j/jal`, and writes trampolines into dead code (e.g. the stubbed `wd93_earlyinit` body). This is how every `[IP54] Patched …` boot message is produced — and the injection point for a new hook, **with no kernel recompile**.
- **`du_poll`** (`ip54_tftp_staging/pvuart_cn.c`) is a global, called every ~20 ms from the callout table — the ideal periodic hook point. It already polls the pvuart RX and the 8042 keyboard.

## What was tested and what blocks each path

| Approach | Result |
|----------|--------|
| gdbstub guest-memory read/write (host side) | **Works.** Proven roundtrip + kernel-global reads. |
| gdbstub reading kernel message buffer `putbuf` | Reads the buffer/metadata fine; content is sparse (putbuf is kernel `cmn_err`/printf, not interactive TTY output). |
| IRIX `/dev/mem` + `dd` (userland bridge) | **Blocked.** `dd` read-to-skips on the char device (no lseek) and the first read hits non-RAM at offset 0 → ENXIO ("Read error during skip"). Also the minimal root needed `/sbin/dd`, `/sbin/cat`, and `/usr/lib32 -> ../lib32` added before `dd` would even load. |
| `/dev/kmem` + `dd` | **Blocked**, same read-to-skip issue (kernel-virtual 0 unmapped). |

Pitfall hit along the way: physical `0x08000000` (KSEG0 `0x88000000`) is exactly where the **kernel loads** — writing a payload there via gdb corrupts the kernel and panics. Pick high RAM well above the kernel (e.g. KSEG1 `0xAC000000`).

## IMPLEMENTED + TESTED: a kernel clock-hook mailbox (drives the console from the host)

A real "add something to the kernel" mechanism, built and verified end-to-end:

- **PROM patch** (`prom-building/src/fw/ip54_stubs.c`, "Host-channel mailbox hook"): patches `clock()` (resolved via `kern_sym`) with a trampoline in the `wd93_earlyinit` dead body. Each 100 Hz tick the hook increments a heartbeat word and, if a command byte is set, writes that byte to the console and clears it. No kernel recompile — installed at PROM load (boot prints `[IP54] Host-channel hook installed: clock->mailbox @0x88054d40`).
- **Mailbox** in guest RAM: `0x88054d40` byte = console-out request, `+4` word = heartbeat.
- **Host driver** (`pyirix_qemu/host_channel.py`): `hook_heartbeat()`, `hook_putc()`, `hook_puts()` drive the mailbox via the gdbstub.

Verified on a booted minimal root: the heartbeat advanced `6725 → 7031` over 2 s (≈150/s, the clock rate) read purely via gdb — proving the kernel hook runs and the host observes it with **no serial**. Then `hook_puts("<<HOST-DROVE-THIS-VIA-KERNEL-HOOK>>\n")` (host → mailbox via gdb → kernel hook → console) **appeared on the console** — the host drove console output without using the serial input path or TFTP.

Build/install: build the bare-metal toolchain once (`cd prom-building && make CROSS=... toolchain`, or the default `make toolchain` — gcc 14.2.0 / binutils 2.43 to `~/cross/mips-elf`), then `prom_build` and copy `build/ip54.bin` to `PROM_library/bins/cpu/ip54/ip54.bin` (a `.prehook` backup is kept). The hook is inert unless the host sets the mailbox, so it is safe to leave installed.

## IMPLEMENTED + TESTED: file transfer to the guest fs (no serial, no TFTP)

A small QEMU change makes a complete file-transfer channel, avoiding the kernel
file-op complexity by committing through the shell (which is already in process
context, where `dd`/`cat` can sleep on disk I/O):

- **QEMU pvuart RX-inject** (`hw/char/sgi_pvuart.c` + `.h`): the UART now has an
  8 KB RX FIFO and a write-only **RX-inject register at offset 4** (PA
  `0x1F62017C`, KSEG1 `0xBF62017C`). A write there pushes a byte into the RX FIFO
  as if received. The **host writes it via the gdbstub** — a binary-clean console
  *input* channel with no serial backend. (gdb MMIO writes dispatch to the device.)
- **Host driver** (`pyirix_qemu/host_channel.py`): `inject_input(bytes)` drives
  console input; `push_text_file(content, path)` injects `dd of=path bs=1 count=N`
  then the bytes — `dd` exits after exactly N bytes (no EOF/signal needed, which
  matters: the minimal single-user tty has VEOF/VINTR disabled, so `cat`+`^D`
  can't be ended).

Verified end-to-end: `inject_input("echo …\n")` made the shell run a command with
**no serial input**, and `push_text_file(131-byte content, "/tmp/xfer3.txt")`
landed a byte-exact file (`131+0 records in/out`) **committed to the XFS root** —
no TFTP, no serial. The data path is the gdb→MMIO RX-inject channel; the shell's
`dd` does the real filesystem write.

**Binary works too, byte-exact** (`host_channel.push_file`): `stty raw` is NOT
8-bit clean on this pvuart console (high bytes get mangled — a 256-value blob
came back with the right *size* but wrong CRC), so binary is sent 7-bit-safe:
uuencode host-side → push the text over the channel → `/usr/bsd/uudecode` in the
guest. Verified: a 256-byte all-values blob round-tripped with `cksum` =
`1313719201 256` on **both** host and guest (identical). The minimal root now
ships `/sbin/{dd,cat,stty}`, `/usr/bin/cksum`, `/usr/bsd/uudecode`.

Caveats: text uses cooked-tty line buffering; binary uses the uuencode path.
**Guest→host pull** today is `cat file` to the console (serial-out) or reading
RAM via gdb; a clean non-serial pull is a small further step. The earlier
vn_rdwr kernel hook (interrupt-context) is unnecessary — the shell is the
committer. The **cleanest no-tty design** remains the RAM-buffer + spinning
userland agent (host writes the agent's gdb-reachable BSS buffer + a mailbox,
agent write()s the file): binary-clean by construction and far faster than the
byte-at-a-time RX-inject. Worth building if throughput matters.

## IMPLEMENTED + TESTED: the portable userland-agent gateway (RECOMMENDED)

This is the clean, fast, **machine-independent** realization of everything above
— no serial, no TFTP, no QEMU device, no kernel patch. Built + verified on **both
Indy (IP22) and IP54** from the *same* agent binary, 2026-06-18.

**Why an agent (not the pvuart bulk-DMA):** the gdb memory channel is
machine-independent (it reads/writes guest RAM on any QEMU target), but the
pvuart bulk-DMA idea is IP54-only (Indy has no pvuart). A tiny IRIX userland
daemon driven over the gdb channel runs identically on every SGI machine.

- **Agent** (`pyirix_qemu/gwagent.c`, compiled `gwagent.n32` — ELF N32 mips-3
  dynamic, runs on IP22 and IP54): spins on a one-page mailbox+buffer (`struct
  gw`) that it keeps **TLB-resident** by touching it every iteration — this is
  the crux, because QEMU's MIPS gdbstub can only translate a user VA that is in
  the TLB. It publishes the page's runtime address to `/tmp/gwaddr` at startup
  (reliably `0x10013000` — n32 loads at fixed VAs). Commands: PING, RUN
  (`popen` a shell command, return its stdout), OPEN_W/WRITE/CLOSE (push),
  OPEN_R/READ/CLOSE (pull). It sets `magic`='GWAY' so the host can confirm the
  agent's address space is the *current* CPU context before trusting a read
  (in multi-user another process may be current at the gdb stop — the host
  retries until magic reads back).
- **Host driver** (`pyirix_qemu/host_channel.py` `Gateway`): `Gateway.attach(port)`
  self-bootstraps (probes for the magic); then `gw.ping()`, `gw.run(cmd)` ->
  `(status, stdout)`, `gw.push_file(bytes, path)`, `gw.pull_file(path)` -> bytes.
  Transfers chunk through the resident page (`DATA_SZ`=2048), so they never
  depend on more than one TLB entry.

Verified end-to-end on **Indy** and **IP54** (separate gdb ports, same code):
`run('uname -a; id')` returned the exact stdout (`IP22`/`IP54`, `uid=0`); a
multi-KB binary blob (all 256 byte values, NULs + high bytes) **round-tripped
byte-exact** in both directions (md5 + `cksum` match host and guest); the
gateway even **pulled its own binary** off the guest byte-exact (cksum
`3363291531 16748`). Deploy: build once on the Indy dev image (`cc -O -n32 -o
gwagent gwagent.c`), then TFTP/serial it onto any target and run it.

`run()` also subsumes the **introspection** idea for free: `gw.run('ps -ef')`,
`gw.run('netstat -rn')`, `gw.run('cat /proc/...')` — any diagnostic, captured
structured, with no console scraping.

Build/run quickstart:
```python
import pyirix_qemu.host_channel as hc
gw = hc.Gateway.attach(port=1234)          # finds the agent's page by its magic
st, out = gw.run("hinv; uname -a")         # run a command, get stdout
gw.push_file(open("x","rb").read(), "/tmp/x")   # host -> guest, binary-exact
data = gw.pull_file("/var/adm/SYSLOG")          # guest -> host, binary-exact
```

## Design notes for extending to files (in-guest agent)

The robust answer for moving files — no new QEMU device needed, because the host drives it via the gdbstub:

1. **A `du_poll` trampoline** (added as a new patch in `ip54_stubs.c`, using the existing trampoline pattern) that each tick reads a **mailbox struct at a fixed guest-RAM address** (a reserved BSS slot or the `wd93_earlyinit` dead space).
2. **The host writes commands / reads results in that mailbox via `host_channel.py`** (gdbstub) — no serial, no TFTP, no QEMU change.
3. Hook command handlers, using resolved kernel symbols:
   - **console out**: hook copies the mailbox text to the console (or, to bypass serial entirely, the host just reads kernel console state via gdb).
   - **console in**: hook feeds mailbox bytes into the console STREAMS read path (the same path `du_poll`→`du_rsrv` uses) — the fiddly part (mblk alloc + `putnext`).
   - **file read/write**: hook calls `vn_open`/`vn_rdwr`/`vn_close` (all `kern_sym`-resolvable) against the mailbox buffer. Constraint: `du_poll` runs in callout context (no sleeping) — stage through a kernel thread or use non-blocking VOPs.

This was designed and de-risked (the trampoline-writes-to-console pattern already exists in `ip54_stubs.c`'s `#if 0` diagnostics) but not yet built; it requires a PROM rebuild cycle and hand-written MIPS. It is the recommended next step for a production-quality channel.

## Minimal-root additions made for this work

`build_minimal_root.py` now also installs `/dev/mem`, `/dev/kmem`, `/sbin/dd`, `/sbin/cat`, and `/usr/lib32 -> ../lib32` (so dynamically-linked tools whose interpreter is `/usr/lib32/libc.so.1` load). Useful regardless of the channel mechanism.
