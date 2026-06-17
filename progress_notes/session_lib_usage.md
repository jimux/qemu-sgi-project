# `pyirix_qemu/session_lib.py` — the canonical IRIX session driver (Layer 3 / task #19)

Every `run_a_*.py` runner re-implemented the same boot/login/screendump/cleanup boilerplate
against `sgi_mcp.server._handle_tool`, and most leaked QEMU processes on failure. `session_lib`
collapses all of that into one context-manager whose `__exit__` **always** does
`init 0` + session stop + orphan kill, so a crashed test can't leave a wedged QEMU behind.

This file is the template for new runners — prefer it over copying an old `run_a_*.py`.

## Minimal pattern

```python
import sys; sys.path.insert(0, "/workspace")
from pyirix_qemu.session_lib import IRIXSession, prepare_instance

# restore golden + inject files + verify extents, all in one call (offline)
prepare_instance("ip54-test", bank=1, inject={...}, verify=["/unix.new"])

with IRIXSession(machine="sgi-ip54",
                 prom="/workspace/PROM_library/bins/cpu/ip54/ip54.bin",
                 instance="ip54-test", ram_mb=256) as vm:
    vm.boot(boot_cmd="boot dksc(0,1,7)/unix.new")   # PROM menu -> kernel
    vm.await_login(timeout=200)                     # blocks until `login:` or panic
    vm.login("root")
    r = vm.run_until("# ", "hinv\n", timeout=20)     # unified RunResult
    print(r.text, "panic=", r.panicked)
    vm.screendump("/workspace/_state.png")
# __exit__ here: init 0, stop session, kill_orphans() — guaranteed
```

## API surface (validated)

- `IRIXSession(...)` context manager:
  `boot / await_login / login / send / run_until / monitor / sendkey / mouse / screendump /
   set_env / shutdown`. `__exit__` always cleans up.
- `RunResult` — unified result from `run_until`: `.text`, `.matched`, `.panicked`
  (scans `PANIC_MARKERS`), `.halted` (`HALT_MARKERS`).
- `guest_lines(text)` — strip the serial echo / PROM noise from captured output.
- `kill_orphans()` — reap stray `qemu-system-mips64` processes (also run in `__exit__`).
- `prepare_instance(instance, bank, inject, verify)` — golden restore + `fs_inject` +
  `xfs_path` FMT_EXTENTS verify in one offline call (no boot).

## Validation

Drove `validate_trace.py` and `run_a_faultseq.py` end-to-end
(boot → await_login → monitor → `__exit__` cleanup verified, no orphaned QEMU). Imports clean
in the dev container; `IRIXSession` exposes the full method set listed above.

> Safety rules baked in: golden restore before each run, `init 0` (never raw `reboot`, which
> XFS-panics), `/proc/*/exe` qemu detection, orphan reaping on every exit.
