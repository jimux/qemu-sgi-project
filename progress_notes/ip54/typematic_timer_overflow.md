# Typematic timer int64 overflow — QEMU main-loop wedge on keypress

Date: 2026-06-11. Found while verifying Phase A (Xsession.dt desktop).

## Symptom

Within ~30s of typing into xlogin via `newport_sendkey`, the entire QEMU
front end went dark: serial console silent, monitor socket EAGAIN,
screendump impossible, the per-second `[IP54-DIAG]` stderr heartbeat
stopped — while the QEMU process burned 210% CPU. The guest vCPU was
fine (idle loop); the **main loop thread never returned from
`timerlist_run_timers()`**.

Because the first two occurrences happened minutes after a desktop
login, it masqueraded as "the .dt session crashes QEMU". It had nothing
to do with the session.

## Root cause

`hw/misc/sgi_hpc3.c` (`sgi-ps2-kbd` subtype):

```c
s->typematic_timer = timer_new_ms(QEMU_CLOCK_REALTIME, ...);   /* scale = 1e6 */
...
timer_mod(s->typematic_timer,
          qemu_clock_get_ns(QEMU_CLOCK_REALTIME) +              /* nanoseconds! */
          (int64_t)s->typematic_delay_ms * SCALE_MS);
```

`timer_mod()` multiplies its argument by the timer's scale
(`timer_mod_ns(ts, expire_time * ts->scale)`). An absolute nanosecond
time × 1e6 overflows int64 once host `CLOCK_MONOTONIC` exceeds
2^63/1e12 ≈ 2.56 hours.

The wrapped value's **sign flips every 2^63/1e6 ns ≈ 2h34m of host
uptime**:

- **Positive window**: expiry lands absurdly far in the future. The
  typematic timer simply never fires. Typing works (make/break codes
  are emitted directly by the event handler) — this is why M1/M2 and
  many login tests passed and the repeat feature was never missed.
- **Negative window**: expiry is permanently in the past. The first
  keydown arms the timer; `timerlist_run_timers()` pops it, the
  callback re-arms it with another negative expiry, and the loop never
  exits. Every other REALTIME timer (serial chardev, monitor, GUI
  update, DIAG heartbeat) is starved forever.

Verified live: gdb showed `timer_mod_ns(ts, expire_time=-2.7e18)` from
the typematic callback, and the arithmetic reproduces the value from
the host's monotonic clock. The fix window boundary also explains a
success at 22:36 and identical-config wedges at 22:46/22:53.

## Fix

One line: `timer_new_ms` → `timer_new_ns` (arm sites already compute
nanosecond expiry). After this fix the typematic repeat actually works
for the first time — REALTIME 500 ms delay / 91 ms period.

## Debugging recipe that cracked it

1. `pgrep`/`top` inside container: process alive, 2 threads ~100%
   **usertime** (not futex) — main loop busy, not blocked.
2. `/tmp/qemu_session_*/qemu_stderr.txt`: the per-second DIAG heartbeat
   timestamp pinpointed the freeze second exactly.
3. ptrace is blocked for normal `docker compose exec`, **but
   `docker exec --privileged -u root <container> gdb -p <pid>` works**
   (after `apt-get install gdb`). `thread apply all bt` named the exact
   callback; repeated sampling proved it was a loop, and one sample
   caught the negative `expire_time` argument.

## Lessons

- **Never pass `qemu_clock_get_ns()`-based expiry to a scaled timer.**
  `timer_new_ms` arms must use `qemu_clock_get_ms()`; mixed units don't
  fail loudly — they overflow silently hours later.
- A bug that only manifests in alternating 2.5-hour host-uptime windows
  looks exactly like "flaky after X minutes of activity". When QEMU
  serial+monitor+display die together but CPU spins, suspect a starved
  main loop and go straight for a thread backtrace.
- Keep a once-per-second stderr heartbeat in dev builds — DIAG's last
  timestamp localized the freeze to the second.
