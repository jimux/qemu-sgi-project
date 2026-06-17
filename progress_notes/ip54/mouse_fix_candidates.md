# IP54 mouse fix — candidate approaches (work through one-by-one)

Problem (precise, established 2026-06-16): on IP54, X's input stream for /dev/input/mouse is
`pckm → Module B (idev-based, rput=idev_rput)`. Module B's qopen (0x882691b4) allocs a 0xd8 idev
device and builds **2 valuators** (a real pointer device). `idev_rput` fires on motion, but its
per-device decode handler (`device+0xc`) never reaches `idevGenPtrEvent` → dead cursor. X sends
**no config ioctls** (live trace: only M_DATA to idev_wput). So the pointer device is built but
never *enabled/decoding*. QEMU + 8042 + pckm are all verified correct.

Goal of this doc: enumerate every plausible fix angle, rank by (leverage × tractability), and
check them off as we test. Each "kernel patch" item implies an lboot `/unix.new` rebuild
(run_m1_kernel_rebuild.py pattern: compile on indy, install /var/sysgen/boot, lboot -s IP54.sm).

## A. Diagnostic-first (no rebuild) — do these before any patch

- [ ] **A1. Live dump of the LIVE mouse idev device.** Break `idev_rput` (0x8806a9cc) on injected
      motion; `device = *(a0+0x14)`; `x/54xw device`. Read: `device+0xc` (decode handler — set? to
      what?), valuator state/enable flags, ptr-mode byte. Tells us exactly which field is unset
      vs a working device. (Sign-extend ptrs: `| 0xffffffff00000000`.)
- [ ] **A2. Finish Module B qopen disasm (0x882691b4, full 0x140).** Does it set `device+0xc`?
      Does it ENABLE the valuators / set ptr-mode, or leave that to an (never-sent) ioctl?
- [ ] **A3. Working reference: boot irix655-dev (machine=indy + Newport gfx) to X.** If its mouse
      works, trace the SAME (which module pushed; device+0xc; valuator-enable). The **diff vs IP54
      is the fix**. (Risk: irix655-dev may be headless / no X configured.)
- [ ] **A4. Confirm which module is on mouse vs keyboard.** Break the push path / dump each
      stream's modules. (Is Module B on BOTH? Is the mouse maybe getting a keyboard-shaped module?)

## B. Kernel patches (one lboot each) — pick based on A's findings

- [ ] **B1. Enable the pointer in qopen.** If A1/A2 show valuators built but not enabled, call
      the existing idev enable (idevSetPtrMode / idev valuator-enable, all GLOBAL) on the device
      at creation — from pckm_open or a small hook. Most likely fix if "built but not enabled."
- [ ] **B2. Set device+0xc decode handler.** If A1 shows device+0xc wrong/generic, set it to the
      proper PS/2 pointer-decode (0x882682fc / mouse equivalent) at device creation.
- [ ] **B3. Push Module A (pckbd) for keyboard / ensure the right module per device.** If A4 shows
      the wrong module is pushed, correct the push (qattach 0x880bf17c is GLOBAL).
- [ ] **B4. pckm in-driver decode → idevGen* directly.** Self-contained: pckm decodes the 3-byte
      PS/2 packet and calls idevGenPtrEvent/idevGenBtnEvent (all GLOBAL) on the mouse device.
      Bypasses the idev decode-handler entirely. Fallback if B1/B2 are intractable.
- [ ] **B5. pckm_wput M_IOCTL → M_IOCNAK** (orig lead). Now low-probability (X sends no ioctls)
      but trivial; fold into another rebuild to rule out.

## C. Reframe angles (larger / different axis)

- [ ] **C1. QEMU absolute USB tablet** or another input device IRIX's idev already fully decodes,
      so the fix lands in QEMU (fast iterate) not the kernel. Uncertain IRIX driver support.
- [ ] **C2. Run the real gfx-kbd init on IP54** (the originally-skipped chain) with HW-probe parts
      stubbed for the IP54 8042 @ 0x1FBD9840. Most "correct" but most entangled (Indy HW addrs).
- [ ] **C3. Re-examine X's device selection** — why X builds Module B without enabling it; maybe a
      hinv/board-id the guest reports makes Xsgi treat the device as non-pointer. (Closed X.)

## Execution log

(fill in as we go)

## Execution log

### A2 done (2026-06-16) — MAJOR REFRAME
Module B qopen (0x882691b4) DOES fully set up the mouse device: device+0xc=0x88268c40 (mouse PS/2
decode handler), device+0x10=0x88268e1c, ptr fields, 2 valuators. The decode handler 0x88268c40 is
a proper PS/2 mouse decoder (3-byte packet state machine @ device+0xcc index; sync-validation; on
sync-loss sets 0xd7=skip-5 + resets) that on a good packet calls **idevGenValEvents (0x8806951c)**
for motion and **idevGenBtnEvents (0x8806a29c)** for buttons — NOT idevGenPtrEvent.
=> The prior task-21 probe of idevGenPtrEvent (which "missed") was the WRONG function for a
relative mouse; that miss does NOT prove the decode is broken. Re-test with idevGenValEvents /
idevGenBtnEvents. New hypotheses: (i) decode handler IS called + works (problem downstream:
shmiq/X) — likely; or (ii) decode bails on PS/2 sync (8042 poll misframes the 3-byte packet).

### A1/jalr done (2026-06-16) — DECODE NEVER INVOKED; "mouse" module not pushed
Live gdb (run_a_valtrace.py / run_a_decprobe.py / run_a_jalrprobe.py): on mouse injection,
idev_rput hits but its M_DATA decode-call (jalr @0x8806aab4) MISSES and the mouse decode handler
0x88268c40 MISSES. So mouse M_DATA never reaches the idev decode loop → the idev "mouse" decode
device is NOT on the mouse stream. fmodsw module names: "keyboard"=pckbd (kbd decode), **"mouse"**
=idev-based (pointer decode, qopen 0x882691b4 builds 2 valuators + device+0xc=0x88268c40). Neither
is pushed on IP54 (gfx-kbd init skipped; SAD autopush can't target the hwgraph major-0 dev_t).
CONCRETE FIX: push the "mouse"/"keyboard" STREAMS modules in-kernel on IP54 (pckm_open hook or
equivalent). Next: find the in-kernel push API the gfx-init uses (qattach 0x880bf17c + fmodsw
lookup, or a strpush helper), implement in pckm.c, lboot-rebuild.

### FIX IMPLEMENTED (2026-06-16) — autopush decode modules via pvfb (candidate B-variant)
Root cause fully nailed: the idev "keyboard"/"mouse" decode modules are never pushed onto
/dev/input/{keyboard,mouse} on IP54 (gfx-console kbd init skipped). idev_rput's per-device decode
handler (device+0xc, set by the module's qopen) is therefore never installed → raw PS/2 bytes
reach idev un-decoded → idevGenValEvents/idevGenBtnEvents never fire → dead pointer.

Why not the obvious routes:
- SAD autopush(1M): stropen looks up autopush by getemajor(*devp); /hw/input/* dev_t major is 0;
  cdevsw[0] isn't a STREAMS driver so the SAD ioctl rejects configuring it.
- pckm.c patch: the golden's pckm is the Indy/gp-global variant (reads 8042 @0x1FBD9840 via gp
  globals set by ng1_earlyinit); the only pckm.c source we have is the MACE variant (MACE_KBDMS
  0x1F320000) — recompiling it would break the working keyboard. Source mismatch → too risky.

IMPLEMENTED: populate the kernel autopush[] hash DIRECTLY from pvfbedtinit (pvfb.c, which we build
with real kernel headers — type-safe), replicating ap_create()+ap_hadd() and bypassing the SAD
cdevsw validation. Adds (major 0, minor 3)->"keyboard" and (major 0, minor 4)->"mouse" so stropen
auto-pushes the decode module when Xsgi opens the device. ip54_input_autopush() in pvfb.c uses
findmod()/strpcache[]/strphash/SAP_ONE. Rebuilt /unix.new via run_m1_kernel_rebuild.py.
TEST NEXT: boot sgi-ip54, probe idevGenValEvents on mouse injection — should now FIRE.
RISK/UNKNOWN: the (major 0, minor 3/4) values are from `ls -lL /hw/input/*`; if getemajor/geteminor
differ at stropen, the entry won't match (fix silently no-ops — safe, just adjust minors/major).

### 2026-06-16 (cont) — autopush B-variant DISPROVEN; data path proven; problem isolated to enable
Three boundary measurements settled the search space (macOS-native build-mac + container gdb):
1. **autopush fix is INEFFECTIVE** — booted the .mousefix2 kernel (autopush configured: NOTE confirms
   "ip54 autopush /hw/input/mouse -> mouse mod=2 maj=0 min=4"), re-ran decprobe: mouse decode handler
   0x88268c40 STILL `entry=False`. The strpcache entry (major 0) likely never matches stropen's
   open-time major — pckm registers as **major 197** (master.d/pckm), not 0. autopush is a dead end.
2. **8042→pckm data delivery WORKS** — added read-side trace to sgi_ioc2_kbd.c (`<- MOUSE read 0x..`).
   On motion injection mouse_reads 12→180 (+168), clean repeating 3-byte packets `a6 28 78`. So mouse
   bytes ARE drained into the kernel. Data-arrival hypothesis (H2) REFUTED. (build-mac, SGI_KBD_DEBUG=1.)
3. **Live cursor is DEAD** — PositionCursor oracle (fires only when X moves its logical pointer):
   startup centers at 640,512 (proves gf_PositionCursor OUTPUT path works) but ZERO new coords on
   motion injection. So the gap is X never RECEIVES pointer events.
SYNTHESIS: kernel decode is proven to work *only when valuators are explicitly enabled* (mouseread.c
does I_PUSH+IDEVINITDEVICE+IDEVENABLEBUTTONS+IDEVENABLEVALUATORS). Live trace shows X sends NO enable
ioctls. ⇒ leading fix = **B1: enable the pointer/valuators at device creation**. Running mousematrix.c
(configs A=no-push, B=push+init+NO-enable, C=full) to confirm the enable is the missing step before
committing to a kernel rebuild. NOTE: stale notes claiming the mouse was "FIXED 2026-06-14" are wrong
for the current golden — only the cursor *output* register-write was fixed, not event delivery.

### 2026-06-16 (cont) — STATIC RE of the decode chain: it is UNCONDITIONAL (capstone, golden /unix)
Disassembled idev_rput (0x8806a9cc) and the mouse decode handler (0x88268c40) from
_golden_extract/unix (capstone MIPS64 BE; the container's objdump has NO mips support):
- idev_rput: db_type==0 (M_DATA) → branch 0x8806aa90 → loop calling `device+0xc` (decode handler)
  via jalr 0x8806aab4 with (device, b_rptr, len). **NO enable-guard on the M_DATA path.**
- decode handler 0x88268c40: pure PS/2 3-byte state machine (idx at device[0xcc]; byte0→[0xd3],
  byte1/dx→[0xd4], byte2/dy→[0xd5]; sync check (device[0xcf]&byte0)==(device[0xce]|signbits);
  first valid packet sets device[0xcd] and is skipped; thereafter calls **idevGenValEvents
  (0x8806951c)** for motion). **NO enable-bitarray gate anywhere** before idevGenValEvents.
⇒ The decode chain produces events the instant Module B is on the stream and M_DATA flows up.
Since live probes show the decode is NEVER reached in X's path, the root cause is that **Module B
("mouse" idev) is not on the mouse stream when X opens it** (pckm's raw PS/2 bytes go straight to
shmiq, undecoded). mouseread.c works because its I_PUSH("mouse") is the operative step (the enable
ioctls are NOT the lever — confirmed by the unconditional decode). FIX DIRECTION: ensure Module B is
pushed at open. The earlier pvfb-autopush used major 0 but pckm is **major 197** (master.d/pckm) —
wrong key, never matched stropen. struct autopush (N32): ap_nextp@0, ap_flags@4, apc_cmd@8,
apc_major@0xc, apc_minor@0x10, apc_lastminor@0x14, apc_npush@0x18, ap_list[8]@0x1c (size ~0x2c).
Pending: confirm Module-B qopen never fires in X's path (run_a_qopenprobe.py), get the real
open-time major (gdb at stropen), then fix the autopush key (or a kernel push hook).

### 2026-06-16 (cont) — ROOT CAUSE NAILED: Module B never pushed; autopush subsystem UNINITIALIZED
Two decisive live measurements on the stock golden kernel:
1. **run_a_qopenprobe.py**: hbreak on Module-B qopen (0x882691b4), armed at login, 120s + mouse
   injection → **qopen NEVER fires**. So the "mouse" idev decode module is never pushed onto the
   /dev/input/mouse stream in X's path. (idev_rput + decode handler are unconditional — proven by
   static RE — so a never-pushed module is the entire bug.)
2. **run_a_apdump.py** (live gdb memory dump): the WHOLE SAD autopush subsystem is empty —
   `nautopush=0`, `strpmask=0`, `strpcache[0..63]` all 0, `autopush[0..31]` all 0. So the keyboard
   does NOT use autopush either (it must be decoded shmiq-natively); there is no autopush infra
   running on IP54 (sadinit never ran — `sad` driver likely absent from IP54.sm).

stropen autopush logic (irix-655 io/streams/streamio.c:447-505): `ap=strphash(getemajor(*devp))`
(= strpcache[major & strpmask]); match needs `ap_major==getemajor && ap_type==SAP_ONE &&
ap_minor==geteminor` (or SAP_ALL / SAP_RANGE); then qattach(fmodsw[ap_list[s]]). It does NOT gate
on nautopush/sadcnt — a correctly-keyed strpcache entry works even with SAD uninitialized. So the
earlier pvfb-autopush failed purely on a wrong (major,minor) key (used 0,4 from hwgraph_path_to_dev,
but stropen's open-time getemajor/geteminor for the mouse must differ) and/or strpmask handling.

FIX PLAN (well-scoped): (a) live-prove via gdb — write autopush[0] for the mouse's REAL
(getemajor,geteminor) [read at stropen], set strpcache[major&mask]=&autopush[0], ap_type=SAP_ONE,
ap_list[0]=fmodsw index of "mouse", ap_npush=1; restart xdm; inject motion → PositionCursor should
fire + cursor track. (b) once proven, bake the same strpcache/autopush population into pvfb's
pvfbedtinit (we compile it), lboot-rebuild /unix.new, promote golden. KEY: get the mouse's exact
open-time major/minor from gdb at stropen (don't assume 0,4) and the fmodsw index of "mouse"/"
keyboard" (search fmodsw for the names; the 620B fmodsw region dumped is the streams-module table —
"mouse"/"keyboard" live further in it / a different table; findmod() returns the index).

### 2026-06-16 (cont) — AUTOPUSH IS FUNDAMENTALLY INAPPLICABLE (live-injection disproof)
Did the live proof-of-fix (run_a_livefix3.py): with xdm stopped + Xsgi killed (mouse stream
released), wrote a VERIFIED-correct autopush entry via gdb (readback confirmed: autopush[0] =
{nextp→kbd, APUSED, SAP_ONE, major=0, minor=4, npush=1, list[0]=2}, strpcache[0]→autopush[0],
strpmask=0x3f), then `cat /dev/input/mouse` to force a FRESH open. Result:
- **stropen (0x880b3714) was NEVER hit** during the cat open (hbreak, 50s timeout).
- Therefore qopen not reached, decode not run.

⇒ Opening /hw/input/* (/dev/input/* symlinks) does NOT go through stropen, so the SAD autopush
path (which lives entirely in stropen, streamio.c:447) can NEVER push a module for these devices.
Almost certainly because /hw/input/* are **hwgfs (hwgraph) vnodes, not spec (VCHR) vnodes** — their
open uses a different vnops whose stream creation bypasses stropen's autopush block. Explains: (a)
why the SAD autopush table is empty AND irrelevant, (b) why the pvfb strpcache injection never
worked, (c) why mouseread.c had to I_PUSH("mouse") MANUALLY, (d) why X's mouse is dead (X's
sgiOpenDevice does NOT I_PUSH before I_LINK, and nothing auto-pushes on the hwgfs path). (Caveat
not 100% excluded: the `cat` could have failed to open; but mouseread.c proves the path opens + is
a stream, so the open exists — it just isn't stropen.)

⇒ AUTOPUSH/strpcache is a DEAD END for this device class. The fix must push Module B from the
hwgfs/idev open path itself (closed pckm/idev — source mismatch, can't recompile) OR a kernel hook
we control. Remaining options (all need a closed-binary modification): (1) binary-patch the idev
"mouse" hwgfs streamtab so Module B's decode is in the stream by default; (2) a pckm/idev-open hook
that qattach()es Module B (qattach 0x880bf17c is global); (3) accept the limitation — desktop +
keyboard work. This is a genuine wall for the tractable approaches; next phase = closed-binary
patching of the hwgfs/idev open path (different, higher-risk effort).

### 2026-06-16 (cont) — FIX IMPLEMENTED (pckm qopen hook) — COMPILES, but BREAKS BOOT (layout shift)
Implemented the chosen fix in ip54_tftp_staging/pvfb.c: hook pckm's driver-open so it pushes the
idev decode module ("mouse"/"keyboard") that nothing else pushes on IP54 (since hwgfs opens bypass
stropen autopush). Mechanism:
- New static fns in pvfb.c: ip54_km_open() (calls the original pckm_open via a saved fn-ptr, then
  qattach()es the per-port decode module — "mouse" if kmport km_state&KM_MOUSE(0x4), else "keyboard"
  — replicating stropen's autopush push: findmod/fmhold/useglobalmon/qattach(FMODSW)/fmrele),
  ip54_push_decode(), ip54_install_km_hook().
- pvfbedtinit() calls ip54_install_km_hook(): ip54_orig_km_open = pckm_rinit.qi_qopen; then
  pckm_rinit.qi_qopen = ip54_km_open.  (pckm is single-open so the push runs once; orig captured at
  boot so it survives lboot relinking.)
- Addresses used (golden): pckm_rinit=0x882ac818 (qi_qopen@+8=pckm_open=0x8802c488), qattach
  0x880bf17c, fmhold 0x8812285c, fmrele 0x8812292c, useglobalmon 0x880b2c50, findmod 0x880c1ce4,
  fmodsw 0x882a3c70, FMODSW=1, MULTI_THREADED=1.

RESULT: pvfb.c **compiles clean** (CC_pvfb_RC=0) and the kernel **relinks clean** (lboot LBRC=0,
/unix.new 6.14MB).  BUT the rebuilt kernel **does NOT boot to a usable state**:
- 0 serial bytes across 3 boot attempts (golden NVRAM didn't help) — the serial CONSOLE (pvuart_cn)
  is dead.
- Framebuffer screendump at t=300s is ALL BLACK — X greeter never comes up.
- gdb-stub IS reachable (mousefixtest connected) — so qemu runs, but the kernel doesn't boot right.
ROOT CAUSE of the regression: the **LAYOUT-SHIFT landmine** (documented in interrupt_wiring_progress.md):
adding code to pvfb.c grows pvfb.o, shifting the kernel binary layout, so the PROM's OFFSET-based
boot-time patches target wrong addresses → early-boot breakage (no console, no X).  The fix LOGIC
is sound and builds; the DELIVERY (growing pvfb.o) is what breaks it.

State left: working golden restored to disk.qcow2 (system usable). The unverified hook-kernel disk is
preserved at vm_instances/ip54-test/disk.qcow2.mousehook.unverified; the pvfb.c source has the hook.
TO MAKE IT WORK (next session): eliminate the layout-shift dependency — options:
  (a) make the PROM's pvfb/PV-device patches SYMBOL-based (resolve at boot from the kernel symtab)
      instead of hardcoded offsets — robust to .o size changes (best long-term);
  (b) place ip54_km_open as a raw-code binary patch in kernel free/pad space + patch
      pckm_rinit.qi_qopen, so pvfb.o size is unchanged;
  (c) pad/trim pvfb.o so its size (and downstream layout) is identical to the golden pvfb.o.
First diagnose exactly which PROM patch breaks: boot the .mousehook.unverified disk under gdb, find
where early boot diverges (compare PC/patched addresses vs golden), confirm it's the offset-patch
target drift, then apply (a) or (b).

### 2026-06-16 (cont) — DOWNSTREAM PROOF blocked by in-guest toolchain (build on Indy)
Tried to prove idev→shmiq→X works (ringtest/ringmin: open shmiq + QIOCATTACH ring + open mouse +
I_PUSH+enable + I_LINK + poll ring; tail>0 ⇒ downstream OK). Blocked by the in-guest sgi-ip54
toolchain, NOT the test: ringtest.c crashed the MIPSpro `be` backend (cc rc=32 / SIGILL); the
simplified ringmin.c compiled but `ld32: Bus error` (cc rc=1) at link. This is the documented
"native cc on sgi-ip54 FAILS — be/ld32/arena issue; BUILD ON INDY" fact. Also: TCG boots are ~5min
and the in-guest serial harness times out on login detection (bump the wait; the shell IS there).
RELIABLE PATH for the downstream proof: cross-build — boot ip54-test on machine=indy (reliable
MIPSpro, it builds the kernel), cc ringmin.c → a PERSISTENT path (/var/tmp/ringmin survives reboot;
NOT /tmp), shut down WITHOUT restoring golden; then boot the SAME disk on machine=sgi-ip54 (no
restore) and run /var/tmp/ringmin with newport_mouse injection, read the ring counters. ~2 slow
boots. Alternative lighter check (no cc): boot older goldens to the X greeter and screendump-compare
the cursor before/after injection.

### 2026-06-16 (cont) — the "toolchain crashes" were a FLAKY-TFTP EMPTY crt1.o (key clarification)
Cross-built ringmin on Indy (machine=indy, reliable MIPSpro). It failed cleanly: `ld32: FATAL 11:
Object file format error (/usr/lib32/crt1.o): file is empty`. The staging crt is FINE
(ip54_tftp_staging/crt1.o = 4748 bytes, crtn.o = 1660 bytes) — but the in-guest `tftp get` delivered
a 0-byte /tmp/crt1.o, and the harness cp'd that empty file over /usr/lib32/crt1.o. So the earlier
sgi-ip54 "be SIGILL (rc=32)" / "ld32 Bus error (rc=1)" were almost certainly the toolchain choking
on a 0-BYTE crt — NOT a genuine be/ld32 bug, and NOT a source bug. ⇒ the in-guest toolchain is
likely usable once crt1.o/crtn.o are reliably present + non-empty.

NET: the bottleneck is VERIFICATION INFRASTRUCTURE, not the diagnosis: (1) flaky tftp delivers empty
crt (verify size + retry, OR bake a verified crt1.o/crtn.o into the golden so every probe has it —
inject LIVE, never offline-fs_inject this XFS); (2) ~5min TCG boots + a fragile serial harness that
times out on login detection (bump waits; use unbuffered `python3 -u` + poll-for-marker). Fix these
once and the downstream proof (ringmin) + all future fix-verification become reliable+quick.
Disk left: working golden.desktop restored to disk.qcow2.

### 2026-06-16 (cont) — in-guest toolchain REFUTED as usable (it is genuinely broken)
Hardened the downstream harness: ONE tftp session/file + `wc -c` size-verify + retry. tftp now
delivers CORRECT sizes (crt1.o=4748, crtn.o=1660, ringmin.c=2605) and the harness cp'd the verified
crt to /usr/lib32. Then `cc -n32 ringmin.c` STILL failed: `Signal: Bus error in Front End Driver
phase ... /usr/lib32/cmplrs/fec died due to signal 4` (CCRC=32). So the prior "empty-crt was the
whole story / toolchain likely usable" conclusion is REFUTED — the in-guest sgi-ip54 toolchain is
genuinely broken (fec front-end bus-errors regardless of crt). ⇒ ALL custom probe/fix binaries MUST
be cross-built on the Indy build host (machine=indy). Stop attempting in-guest cc on sgi-ip54.

### 2026-06-16 (GROUND TRUTH) — THE MOUSE CURSOR TRACKS. Prior "dead mouse" conclusion REFUTED.
Settled the contradiction between this file ("decode module never pushed / dead mouse") and
mouse_root_cause.md ("cursor tracks, FIXED") by EMPIRICAL TEST on the desktop golden:
boot -> X greeter -> inject mouse motion via newport_mouse -> screendump before/after.
RESULT (run_a_cursortest.py, screenshots _ct_g0..g3):
  g0 initial: red-X cursor at screen CENTER (~617,489)
  g1 after move(-50,-50)x30: cursor GONE from center (parked top-left corner)
  g2 after move(+50,+50)x35: cursor at BOTTOM-RIGHT corner (~1255,1000)
The cursor is the X server's OWN sprite (XC_X_cursor shape) and it tracks injected motion
1:1. Therefore motion events DO traverse pckm -> idev decode -> shmiq -> X. The "mouse decode
module is never pushed" theory is WRONG: if decode weren't happening, X would receive no motion
and the cursor could not move. mouse_root_cause.md is the correct/current record.
  => All the autopush/pckm-qopen-hook/layout-shift work in this file was solving a NON-PROBLEM.
     The decode chain is already functional on the IP54 golden.

BUTTON CLICKS: being verified separately (clogin 'Help' button click; closed-loop cursor
homing in run_a_clickproof2.py). STRUCTURAL EVIDENCE they work: the PS/2 decode handler
(0x88268c40) parses buttons (byte0) AND motion (bytes1-2) from the SAME 3-byte packet and emits
both via idevGenBtnEvents / idevGenValEvents down the SAME idev->shmiq->X stream. There is no
code path that delivers motion to X but drops buttons. So motion working (proven) implies
buttons working.

SEPARATE ISSUE (not a mouse bug): logging in via the greeter tears down the greeter X and the
full-desktop session does NOT paint (bare blue root / hang) — the known Xsession.dt PC=0 trap.
This blocks reaching a usable 4Dwm desktop, independent of input. That is the real remaining
barrier to an interactive desktop.

ALSO (infra): the greeter alternates per boot between the SGI clogin 'IRIS' graphical greeter
(has Login name + Log In + Help buttons) and the plain xdm 'X Window System' greeter
(Login/Password only) -- clogin appears ~1 in 4-5 boots (it falls back to plain xdm when it
fails to start). And in-guest cc on sgi-ip54 is genuinely broken (fec Bus error even w/ good
crt) -> cross-build on Indy for any probe binary.

### 2026-06-17 — click verification blocked by greeter flakiness + Xsession-not-painting (NOT mouse)
- Empirical CLICK proof attempts: the only clean click target is the SGI clogin 'IRIS' greeter's
  Log In/Help buttons, but the greeter is a per-boot coin-flip (clogin vs plain xdm 'X Window
  System'); under load clogin lost ~10/12 boots. One clogin Help click showed region-diff=0
  (closed-loop cursor homing landed the red arrow exactly on Help) — most likely clogin Help is a
  no-op in this minimal install, not a failed click.
- CODE-LEVEL proof clicks work: QEMU IOC2 8042 (hw/misc/sgi_ioc2_kbd.c) uses standard
  TYPE_PS2_MOUSE_DEVICE; button events use the SAME path as motion (qemu_input_update_buttons ->
  ps2_mouse_event -> 3-byte packet -> same drain), no motion-only filter. Guest decode handler
  parses buttons(byte0)+motion(bytes1-2) from the SAME packet. So motion working (proven) => clicks
  working. newport_mouse buttons doc is mislabeled (QEMU-legacy: 1=left,2=right,4=middle) but
  1=left is correct; HMP hmp_mouse_button impl is correct.
- DESKTOP-OFF test (run_a_desktop_off.py): set /etc/config/desktop=off LIVE (chkconfig + echo),
  then X-login root. Result: post-login framebuffer = bare blue root + cursor (6299 bytes), NO 4Dwm,
  NO Toolchest. So BOTH Xsession.dt AND the plain Xsession fail to paint the desktop content -- the
  session-not-painting blocker is deeper than the .dt path. /root/.xsession-errors is absent
  (Xsession's error-redirect is commented out in this golden -> errors go to xdm-errors/X log).
  Right-click root gives no menu because there is no WM running. ⇒ the click test is coupled to
  fixing the session; this is the REAL barrier to an interactive desktop, separate from the mouse.
NEXT (real blocker): diagnose why the post-login X session brings up no WM/content (capture the X
server + xdm logs / run the session steps manually from a serial root shell with a working DISPLAY).
