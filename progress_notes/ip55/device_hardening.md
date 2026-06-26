# Virtuix host-backed device hardening

Per the device doctrine, disk/net/console/input stay emulated (they already ride host backends — qcow2, slirp, host serial) and are **hardened with tests** rather than paravirtualized. This note tracks that hardening, beyond the boot smoke test.

## Coverage map

- `tests/test_virtuix_boot.py` (slow) — the smoke test: boots `-M virtuix -kernel`, asserts SMP CPU count, ec0 up + slirp ping, a console computed round-trip, a small disk write→sync→read-back, and SCSI/disk in `hinv`.
- `tests/test_virtuix_hardening.py` (slow) — the **hardening** test (added 2026-06-26). Targets the properties that have cost the most time, above all **data durability** (disk corruption from broken write-ordering has been the #1 time-sink — see CLAUDE.md "VM Lifecycle & Disk Safety").

## Robustness fix found while hardening: guest reboot/shutdown under `-kernel`

The guest-reboot durability test surfaced a real gap: under `-M virtuix -kernel` boot the ARCS firmware **Halt/PowerDown/Restart/Reboot** vectors were **no-ops** (they logged "stopping QEMU" but did nothing), so `init 0` and `init 6` just hung/looped — the guest's serial log showed `ARCS: Reboot called, stopping QEMU` repeating. Fixed in `qemu-sgi-repo/hw/misc/sgi_arcs.c`:
- **Halt/PowerDown → `qemu_system_shutdown_request`** (clean QEMU exit; supports the disk-safety "graceful `init 0`" stop workflow).
- **Restart/Reboot → `qemu_system_reset_request`** — the guest now actually reboots. It works with no PROM because the kernel ELF + `write_kernel_trampoline` blob are registered ROMs (`rom_add_blob_fixed`/`rom_add_elf_program`), which QEMU re-applies on every system reset, so the CPU restarts at the trampoline and boots the kernel fresh. (sgi_arcs is the `-kernel` ARCS stub — virtuix-only; `machine=indy` uses the real PROM, unaffected.)

This makes `init 6` / `init 0` behave correctly inside the guest, which is both a usability win (reboot the desktop without restarting QEMU) and what enables the guest-reboot durability test below.

## What `test_virtuix_hardening.py` proves that the smoke test does not

1. **Disk durability across a full QEMU restart** — the centerpiece. Boot 1 writes a 2 MB file (`dd if=/unix`, real varied data) + a marker, `sync`, then the VM is shut down. Boot 2 is a **fresh QEMU process on the same overlay** and re-checksums: the `cksum` and byte size must be **identical**, and the marker text must survive. This is the crash-consistency property `cache=writethrough` + XFS journaling is supposed to deliver, exercised end-to-end through the WD33C93 + HPC3 SCSI-DMA path. A checksum mismatch here = silent data corruption, which is exactly the failure class that has burned days before.
2. **Large-file write/read integrity** — 2 MB, not a tiny marker; a real DMA-sized transfer with an exact-size assertion.
3. **Seeq ec0 net health under traffic** — drives 5 pings then parses `netstat -in` for the `ec0` line and asserts **Ierrs == 0 and Oerrs == 0** (the interface isn't silently dropping/erroring frames), a stronger check than "ping replied".
4. **Z85C30 console bulk-output integrity** — emits 120 copies of a fixed marker line over the serial, bounded by a sentinel, and asserts every line arrives intact (no dropped/corrupted bytes under a burst).
5. **Disk durability across a clean guest reboot (`init 6`)** — a second test function: writes a payload, `sync`, drives `init 6`, waits for the system to come back to login, and re-checksums. This additionally exercises XFS journal replay on remount (the QEMU-restart test only covers qcow2 persistence). Made possible by the ARCS reboot fix above.
6. **Multi-target SCSI enumeration** — a third test attaches a blank unit-2 disk and asserts the guest's `hinv` enumerates BOTH drives, exercising the WD33C93/HPC3 controller's multi-target scan + selection (the other tests only ever touch unit 1).
7. **Net file-transfer integrity** — a fourth test TFTP-fetches a 64 KB payload from the slirp server over Seeq ec0 and asserts the in-guest `cksum` matches the host's exactly. Exercises the Seeq receive-DMA path with a sustained, verified transfer (vs. the small ICMP of ping). The reliable IRIX tftp recipe is **interactive `tftp` + `mode octet`** (slirp rejects netascii; the client reads commands from the tty, so pace each line) — fetching ~64 KB takes ~1.3 s.

Both tests are `@pytest.mark.slow` (full IRIX boots) and excluded from the default `-m "not slow"` fast suite. Run:

```
python3 -m pytest tests/test_virtuix_hardening.py -v
```

## Test-infrastructure fix found while validating input coverage

`tests/helpers/qemu_runner.py` (`SGIQemuRunner`) still pointed `_BUILD_DIR` at the **legacy `qemu/build`** directory, removed in the `qemu/` → `qemu-sgi-repo/build-linux` migration. So **every `SGIQemuRunner`-based slow test was silently broken** (`FileNotFoundError` on the QEMU binary) — `test_prom_boot`, `test_scsi_prom_irix`, `test_scsi_timing`, `test_cpu_timing`, `test_miniroot_boot`, `test_newport_framebuffer`, `test_scsi_benchmarks`, `test_input_integration`. One-line fix: prefer `qemu-sgi-repo/build-linux`, fall back to the legacy path. Verified `test_prom_boot.py` (4/4) green again.

`tests/test_input_integration.py` was also conceptually wrong: it injected **PS/2 `sendkey`** but ran a **serial-console** PROM, which reads the serial line, not PS/2 — so it could never pass. Retargeted it to validate the **Z85C30 serial-RX → PROM** input path (feed a byte at the PROM's 'press any key' block, assert it advances to the maintenance menu). PS/2 keyboard input drives the *graphical* console and is covered by the desktop-eyes tooling for virtuix.

## Notes / gotchas baked in

- Commands are **csh-safe** (the desktop golden's root shell is csh — no `2>`/`2>&1`; see memory `shell_default_differs_by_disk`).
- Durability is tested **both** ways: a QEMU restart on the same overlay (qcow2 persistence + the SCSI write path) and a guest `init 6` reboot (adds XFS journal replay on remount). The latter needed the ARCS reboot fix above.
- `netstat -in` column parse counts from the **end** of the line (the last 5 numbers are Ipkts/Ierrs/Opkts/Oerrs/Coll) so the IP-address digits earlier in the line don't confuse it.

## Next hardening candidates (not yet done)

- **Second-disk write/read integrity** — the multi-target test only checks *enumeration*. A follow-up could `fx`-label + `mkfs`/mount unit 2 and round-trip data, but it's **low marginal value** (the 2nd target uses the *same* read/write DMA path as unit 1, already heavily exercised by the durability tests) and needs fragile in-guest disk admin (a blank disk's `dks0d2vol` has no usable space until labelled; only `dks0d2vh`/`dks0d2vol` nodes exist). Probably not worth it.
- **PS/2 input depth** — `test_input_integration.py` exists; could add keyboard/mouse burst + focus-tracking robustness (needs a display + injection, so heavier than the headless tests here).
