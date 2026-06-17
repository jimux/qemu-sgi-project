# IP54 Deterministic Record/Replay + Reverse Debugging

Status: **WORKING** (validated 2026-06-12). This is the flagship of the Gen-2
debug-tooling effort: record a boot deterministically, replay it byte-for-byte,
and step *backwards* through it in gdb. The pvclock path — feared to block this —
turned out to be **already replay-safe under single-vCPU icount**, so the whole
capability landed with a one-line QEMU comment and no functional QEMU change.

## TL;DR recipe

Run inside the docker dev container, on a disposable overlay (never golden):

```sh
# overlay backed by golden so golden is never written
qemu-img create -f qcow2 -F qcow2 -b vm_instances/ip54-test/disk.qcow2.golden scratch.qcow2

# RECORD (boots to login, deterministic)
qemu-system-mips64 -M sgi-ip54 -bios PROM_library/bins/cpu/ip54/ip54.bin -m 256M \
  -drive if=mtd,file=scratch.qcow2,format=qcow2,cache=writeback,file.locking=off \
  -icount shift=7,sleep=off,rr=record,rrfile=rec.bin,rrsnapshot=rrstart -net none ...

# REPLAY + reverse-debug (SAME overlay; load_snapshot reverts to record start)
qemu-system-mips64 ... -icount shift=7,sleep=off,rr=replay,rrfile=rec.bin,rrsnapshot=rrstart \
  -net none -gdb tcp::1234 -S
# then: gdb-multiarch -> set arch mips:isa64; set mips abi n64; set endian big;
#       target remote :1234; stepi...; reverse-stepi   (PC walks backward)
```

Driver scripts (repo root): `run_ip54_icount_probe.py` (boot/record/replay/net
modes), `run_ip54_reverse_probe.py` (record+replay+reverse-stepi end-to-end),
`run_ip54_watch_test.py` / `run_ip54_watch_replay.py` (watchpoint probes).
Invoke: `docker compose exec -T dev python3 /workspace/run_ip54_*.py`.

## What was validated

| Step | Result |
|------|--------|
| A0 boot under `-icount shift=7,sleep=off` | clean boot to login, 8s wall |
| A1 record vs replay serial transcripts | **byte-identical** (same SHA1) |
| A2 filter-replay networking | identical with `-nic user,id=n0` + filter-replay |
| A3 `reverse-stepi` in replay | PC `0x..50 -> 0x..4c -> 0x..48` (walks back) |
| Live `hbreak` (GuestGDB.catch) at `splx` | fires, full reg+stack dump |

## Gotchas discovered (each cost real time)

1. **`-icount shift=auto` THROTTLES to realtime** — boot stalls >6 min in the
   PROM kernel-load loop. Use a fixed `shift=7,sleep=off` (full-tilt). icount is
   otherwise fully compatible — no QEMU_CLOCK_REALTIME/HOST in any active IP54
   device.
2. **Don't pipe QEMU stdout to an undrained subprocess.PIPE** — it fills (~64KB)
   and QEMU blocks mid-boot (a deterministic-looking hang). Redirect to a file.
3. **filter-replay needs `-nic user,id=n0` not `-netdev`** — a bare `-netdev`
   leaves the sysbus pvnet NIC with "no peer". `-nic` binds the pair; `id=n0`
   makes the netdev filter-able.
4. **Reverse-exec REQUIRES explicit `rrsnapshot`** — the auto `start_debugging`
   path calls `save_snapshot(overwrite=true)` on gdb-attach, which aborts QEMU
   with `bdrv_snapshot_delete: Assertion bs->quiesce_counter > 0`
   (replay/replay-debugging.c:325). With `rrsnapshot` set, that hook is skipped.
5. **qcow2 internal-snapshot hazard**: `rrsnapshot` uses save/load_snapshot — the
   mechanism CLAUDE.md warns caused data loss. Always on a disposable overlay,
   one pinned QEMU build per session.

## GDB capability matrix on this build

- **Live breakpoints (hbreak / GuestGDB.catch)**: WORK.
- **Replay `reverse-stepi`**: WORKS.
- **Hardware watchpoints (watch/rwatch/awatch)**: PLANT but **never fire** — live
  AND replay, even on `lbolt` (written 100Hz). Cause: kernel data is in MIPS
  KSEG0/KSEG1 (unmapped/direct-mapped) and this QEMU's TCG watchpoint check does
  not cover those accesses. **Use a TCG memory plugin** (task A6) for
  "trap write to phys X", or reverse-debug from the corrupt state.
- **Replay continue-to-breakpoint**: unconfirmed (hbreak at `splx` timed out in a
  120s replay continue — likely replay-continue is very slow). reverse-stepi is
  the reliable reverse primitive; reverse-continue is suspect.

## Code changes (durable)

- `qemu-sgi-repo/hw/mips/sgi_ip54pv.c` — comment at the pvclock block explaining
  WHY it's replay-safe and to NOT route the CP0_Cause write through a replay BH
  (would reintroduce the mtc0 race). No functional change.
- `pyirix_qemu/guest_gdb.py` — `_preamble`/`_run` helpers; `watch/rwatch/awatch`,
  `catch_if` (conditional bp), `script()` (compose gdb cmds), `reverse_step`/
  `reverse_continue` (guarded to replay=True), `load_symbols` via `kernel_elf`,
  dynamic `is_kernel_text` bounds from the symbol DB.
- `sgi_mcp/server.py` — `_build_qemu_launch` consumes `icount_shift`, `rr_mode`,
  `rrfile`, `rrsnapshot`, `gdb_port`, `start_stopped`, plus auto `-nic`→filter-
  replay rewrite when recording; `qemu_session_start` schema exposes them.

## memwatch TCG plugin (A6) — WORKS, the wild-write catcher

`qemu-sgi-repo/contrib/plugins/memwatch.c` traps guest-CPU writes (or reads) to a
physical address range and logs the writing PC + value + size. This is the durable
replacement for gdb watchpoints (which don't fire here).

```sh
qemu-system-mips64 ... -d plugin -D /workspace/plugin.log \
  -plugin <build>/contrib/plugins/libmemwatch.so,addr=0x0829edc0,len=4,rw=w
```

**CRITICAL:** plugin output goes through `qemu_log_mask(CPU_LOG_PLUGIN,...)`
(plugins/api.c:391) — it is INVISIBLE without `-d plugin`.  `-D <file>` directs
it to a file.  (This, not a code bug, was why every plugin first showed 0 output
— hotpages/hotblocks too.)  Validated: catches both PROM (pc=0xbfc...) and kernel
(pc=0x88...) writes, cached KSEG0 and uncached KSEG1.  Build:
`ninja contrib/plugins/libmemwatch.so` (added to contrib/plugins/meson.build).
Test driver: `run_ip54_memwatch_test.py`.  Caveat: it sees GUEST-CPU writes only
— a device-issued `cpu_physical_memory_write` (as the pvclock does) is invisible.

## Remaining / follow-ups

- Full network-RX determinism test (TFTP transfer) once interactive replay
  driving is wired (needs socket-serial in the driver, or the MCP session tools).
- The payoff: record a desktop session and reverse-debug a real crash to its
  first cause.
