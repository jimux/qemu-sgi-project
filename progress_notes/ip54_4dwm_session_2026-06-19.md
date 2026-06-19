# IP54 4Dwm work — session checkpoint 2026-06-19

## Where we landed

The MO_UNALN QEMU fix from 2026-06-18 (committed in
`qemu-sgi-repo/target/mips/tcg/translate.c`) is the **major
breakthrough**: the IRIS clogin "Welcome to IRIS — IRIX 6.5" Motif
dialog now renders correctly, accepts root login + empty password,
and Xsession kicks off. Cores from `iaf/scheme` (the previous primary
SIGBUS culprit) are gone.

But a **second wave** of crashes prevents reaching the Indigo Magic
4Dwm Desktop. Captured this session:

| Core file (mtime)   | Binary                    | EPC at fault   | Failure   |
|---------------------|---------------------------|----------------|-----------|
| `/core`             | csh (via telnetd inetd)   | 0x0e01aa88     | `lb t0, 0(a1)` with a1=NULL — strchr-style routine called with NULL needle |
| `/var/tmp/core`     | sh -c X (xdm's wrapper)   | 0x00000000     | jump-to-NULL instruction fetch (cause AdEL) |
| /core (yesterday)   | xrdb                      | unknown        | crashes during X resource setup |
| /var/tmp/core (yest)| sh -c xkbcomp             | unknown        | XKB compiler invocation dies |

xdm-errors confirms the symptom from the X server side:
```
X Error of failed request:  BadAlloc (insufficient resources for operation)
  Major opcode of failed request:  45 (X_OpenFont)
Error: XtAppCreateShell requires non-NULL widget class
xdm error (pid 219): /usr/bin/X11/X[10]: 261 Memory fault(coredump)
xdm error (pid 219): /usr/bin/X11/X[10]: 264 Memory fault(coredump)
xdm error (pid 219): /usr/bin/X11/X[10]: 266 Memory fault(coredump)
```

So PIDs 261/264/266 (spawned by `/usr/bin/X11/X` shell wrapper)
all SIGSEGV. Xt also gets a NULL pointer.

## What this looks like at the kernel-trap level

Recall the kernel emits ALERT messages via the `0x881bb4f8` trap-path
(found via `li a0, 1757` for the `0x6dd` tag). I planted a hardware
breakpoint there during this session — it did NOT fire for the
csh/sh crashes. They go through the **normal psig() path** (signal
not held/ignored) and produce cores cleanly, without triggering the
"signal held" cmn_err path.

So they're real userspace SIGSEGV events being delivered cleanly.

## Pattern across all the second-wave crashes

All of them are **process-startup-time failures** in static or
statically-linked-against-libc binaries:

- csh: static, statically links libc → crashes inside csh's own text
- sh: static, statically links libc → crashes jumping to NULL
- xrdb: dynamic — links libX11, libXmu, libXt → crashes during Xt init
- xkbcomp-via-sh: same sh pattern as above

The common thread: process startup pulls in a chain of init code
(`__rld_init`, `__libc_init`, libC++ static initializers, Xt
`XtAppCreateShell`, etc.) and SOMETHING in that chain hits a NULL
pointer.

Most likely root cause classes (educated guesses, NOT verified):

1. **`usynccntl` / `prctl` / shared-arena syscalls** — the
   `pvdisk_read_fragility_fix` memory note explicitly says
   "MIPSpro `be` backend — crashes in libCsup.so/libC.so.2 static
   initializers (require IRIX-specific usynccntl/prctl/shared
   arena)". These syscalls may be returning wrong values on IP54,
   causing static initializers to set up global state with NULL
   pointers.
2. **argv/envp setup at execve** — if the kernel pushes the
   user-stack auxv/argv/envp incorrectly, every process sees garbage
   for `getenv()` and arg parsing, leading to NULL chains.
3. **Signal-handler frame layout** — if the kernel writes the signal
   frame to a slightly wrong offset, sigreturn restores garbage
   registers.

## Operational state

The disk has been telnet-edited so `visuallogin=off` and `desktop=off`
(then re-set `desktop=on`). Reverting to the indigo_magic_dialog
backup restores the original state. The MO_UNALN qemu binary at
`qemu-sgi-repo/build-linux/qemu-system-mips64` has the fix baked in
(md5 `6dbc31bcf20f6e3fcd5820fc4e6b7488` as of this checkpoint).

## What needs to happen next

To get to 4Dwm + Toolchest, the second-wave NULL-pointer crashes
need to be diagnosed and fixed. Concrete next steps:

1. **Get the xrdb crash PC**: extract the core, parse the IRIX
   sigcontext (section type 0x1, offset 0x438 in coreout) for the
   EPC value. Disasm xrdb at that PC.
2. **Compare csh, sh, xrdb crash points** to find a common library
   function or syscall they share.
3. **Trace the kernel-side syscall(s) involved** — likely
   `usynccntl`, `prctl`, `sigprocmask`, or `_sproc`. If those are
   stubs in our IP54 kernel build, that explains the bug.
4. **Test the hypothesis** by running a static binary that doesn't
   use any of those (e.g., a hand-coded MIPS assembly hello-world)
   on IP54 to see if pure exec/argv setup works. If hello-world
   succeeds and csh fails, the bug is in libc init, not process
   setup.

The MO_UNALN fix is committed and is the right baseline; the
remaining work is investigating the second-wave crashes.

## Important artifacts saved

- `/tmp/cur_core` (csh) and `/tmp/cur_var_tmp_core` (sh) — fresh
  cores from this session
- `/tmp/ip54_sbin_csh`, `/tmp/ip54_sbin_sh`, `/tmp/ip54_xsgi`,
  `/tmp/ip54_lib32_libc.so.1`, `/tmp/ip54_lib32_rld` — extracted
  binaries for disasm
- `qemu-sgi-repo/target/mips/tcg/translate.c` — committed MO_UNALN fix
- `progress_notes/ip54_mo_unaln_breakthrough_2026-06-18.md` — the
  prior session's writeup of the MO_UNALN fix
- `progress_notes/ip54_userspace_sigbus_2026-06-18.md` — original
  diagnosis writeup

## Going forward this session

Per the user's broader goal, pivoting to task #26 — investigate
**decoupling IRIX system time from QEMU CPU clock**. The 4Dwm chase
will resume once the userspace second-wave crashes have been pinned
to a specific kernel syscall.
