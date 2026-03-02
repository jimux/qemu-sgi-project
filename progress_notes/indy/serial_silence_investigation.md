# Serial Silence Investigation

## Problem

After the IRIX 6.5 miniroot kernel prints "audio: AES receiver not responding."
and "Creating miniroot devices, please wait...", serial output goes completely
silent for 540+ seconds. SCSI logs show all commands succeed. The CPU is 61.5%
idle (from PC sampling). We need to understand why no further output appears.

## Key Finding: WR1 TX_INT_ENBL Never Set

SCC TX trace analysis shows:
- 1713 total TX bytes during boot
- 1706 bytes with WR1=0x00 (polled via du_putchar)
- 7 bytes with WR1=0x11 (also polled, RX interrupts enabled but NOT TX)
- **WR1 bit 1 (TX_INT_ENBL) is NEVER set**

This means the STREAMS-based TX path (`du_wput → du_save → mips_du_start_tx`)
is never activated. All serial output comes through the polled path
(`du_putchar` / `ducons_write`).

## Investigation: Why Is the STREAMS TX Path Never Used?

### Theory 1: /dev/console is a STREAMS device (DEBUNKED)

If `/dev/console` were registered as a STREAMS device, writes would go through
`strwrite → cn_put(putnext)` which doesn't connect to the DUART stream.

**Finding:** `/dev/console` is NOT a STREAMS device.

From `master.d/cn`:
```
orcsm   cn   58
```

Flags: `o`=ONCE, `r`=REQ, `c`=CHAR, `s`=SOFT, `m`=semaphore. No `f` (FUNDRV)
flag, so `cdevsw[58].d_str = NULL`. The SSTREAM flag is never set on cn vnodes.

The du (DUART) driver IS a STREAMS device (`master.d/sduart`: `sfr du 260`),
so writes to `/dev/ttyd*` go through strwrite.

### Actual Write Path for /dev/console

```
write(fd, buf, len)              // user syscall
  → spec_cs_write_vop()          // specfs
  → SSTREAM not set on cn        // cn is NOT STREAMS
  → cdwrite(cdevsw[58], ...)     // traditional cdevsw path
  → cnwrite()                    // cn.c line 172
  → VOP_WRITE(cn.cn_rvp, ...)   // write to DUART vnode
  → spec_cs_write_vop()          // specfs again, for DUART
  → SSTREAM IS set on du         // du IS STREAMS
  → strwrite()                   // STREAMS path
  → stty_ld module               // line discipline
  → du_wput()                    // DUART driver
  → du_save() → mips_du_start_tx() // enable TX interrupt (WR1 bit 1)
```

This path is architecturally sound. If any process writes to /dev/console,
it should reach du_wput and set WR1 TX_INT_ENBL.

### Theory 2: du_open fails (DEBUNKED)

- WR1=0x11 persists after du_open (not reset by du_zap error path)
- No "no file for console" error in klogmsgs
- stty_ld push succeeded (part of du_open completion)

### Theory 3: DCD blocks du_open (DEBUNKED)

- IP22 DCD_XOR_MASK = 0x00 (active-high)
- RR0 = 0x2c has bit 3 set → DCD IS asserted
- du_open does not block

### Theory 4: /sbin/sh doesn't exist (DEBUNKED)

Miniroot filesystem inspection shows `/sbin/sh` exists (510 KB).

## Miniroot Filesystem Analysis

The miniroot is an XFS filesystem extracted from the CD volume header "mr" entry
(17.8 MB). Key contents:

### Device Nodes (only 5 pre-created)
```
crw------- console (58,0)   - Console device
crw-rw-rw- null    (1,2)    - Null device
crw------- syscon  (58,0)   - System console
crw------- systty  (58,0)   - System TTY
crw-rw-rw- zero    (37,0)   - Zero device
```

### Boot Sequence (from /etc/inittab)
1. **sysinit:** `/etc/bcheckrc` — fsck, grow XFS, run MAKEDEV
2. **sysinit:** `/etc/brc` — fstab/mtab, swap, mount /proc and /hw
3. **bootwait:** `mrinitrc` → `mrlogrc` → `mrcustomrc` → etc.
4. **wait:** `mrinstrc` — launches inst> installer

### The "Creating miniroot devices" Message

From `/etc/bcheckrc`:
```sh
if [ `/bin/ls /dev|wc -l` -lt 10 ]; then
    echo Creating miniroot devices, please wait...\\c
    cd /dev; ./MAKEDEV MAXPTY=10 MAXGRO=4 MAXGRI=4 mindevs scsi > /dev/null
    echo ''
fi
```

MAKEDEV targets: `mindevs` = `generic disks pty ttys flash`, plus `scsi`.

### Hardware Graph Dependency

IRIX 6.5 MAKEDEV creates symlinks from `/dev/` into `/hw/` (hardware graph
filesystem = hwgfs). If hwgfs isn't mounted or populated, symlinks have no
targets but creation doesn't error — devices just don't work.

The `/etc/brc` script runs `/etc/mnthwgfs` to mount hwgfs on `/hw/`.

## Resolution

The silence was resolved by two subsequent fixes:

1. **Z85C30 WR0 register pointer masking** — WR0 bits [2:0] select the
   register pointer, not [3:0]. The old `val & 0x0f` mask leaked command
   bits into register selection, corrupting STREAMS TX setup (WR5/WR11/WR14).
   Fix: `val & 0x07`.

2. **Longer boot timeouts with `-icount shift=0,sleep=off`** — The MAKEDEV
   execution and device enumeration simply needed more virtual time. With
   icount, the kernel completes init and reaches the login prompt.

The STREAMS TX path analysis above remains accurate — the polled path
(`du_putchar`) is used during early boot, and STREAMS-based TX activates
later once the serial STREAMS stack is fully configured.

IRIX 6.5 now boots fully to multi-user login and 4Dwm desktop.
