# Step 6 — Milestone 0 integration: findings so far

Goal: guest IRIS GL app → libgl → DGL → our dgld-shim → host renderer (OSMesa) → frame channel
→ pvrex3 composite → triangle in a desktop window. This note records what the integration runs
established (the pipeline is being lit up link-by-link).

## Confirmed facts (this session)

1. **DGL invocation path** (recon, `run_m0_investigate.py`): `/etc/inetd.conf` has
   `sgi-dgl stream tcp nowait root/rcv /usr/etc/dgld dgld -IM -tDGLTSOCKET`, `sgi-dgl = 5232/tcp`.
   So **inetd spawns `/usr/etc/dgld`** (the connected socket on stdin/stdout) on any `:5232`
   connection. **Install = replace `/usr/etc/dgld` with our shim, keep the inetd line.** The stock
   dgld is 533892 bytes; `/usr/etc/dgld.orig` is our backup.
2. **Xsgi + xdm already run** at boot (pid Xsgi ~220) — there is a live `:0` display to put the
   shim's window on.
3. **inetd actually spawns dgld** on a `:5232` connect (`run_m0_probe.py` Probe B saw
   `dgld -IM -tDGLTSOCKET` as a child of inetd).
4. **Renderer transport works** (`run_m0_probe.py` Probe A): the guest connecting to
   **`10.0.2.2:6053`** reaches a container-side listener (slirp host-loopback; the CONN arrives
   from `127.0.0.1` translated). So the shim's `render_connect("10.0.2.2", 6053)` will reach the
   host `sgi_glremote.shim_renderer`.
5. **Both guest binaries build + extract**: `gltri` (IRIS GL, `cc -n32 -lgl -lX11 -lm`) and
   `dgld_shim` (`cc -n32 -lX11`) compile clean on irix-devel and are pulled into TFTP staging via
   `IRIXTelnet.get_file` (uuencode). Valid ELF N32 MIPS.

## Shim changes driven by the recon

- **Renderer address must be fixed, not argv**: inetd passes argv `dgld -IM -tDGLTSOCKET`, so the
  shim can't read the renderer host from `argv[1]`. Now hardcoded `10.0.2.2:6053` with a
  `DGLSHIM_RENDERER=host:port` env override.
- **File logging** (`/tmp/dgld_shim.log`): inetd is a `nowait` service → stdin/stdout/stderr are
  the client socket, so stderr would corrupt the DGL stream. Log to a file. MIPSpro `cc` is
  pre-C99 → no variadic macros; used a `stdarg` `slog()` function.

## Gotchas found (and the harness fixes)

- **Deployment persistence**: killing QEMU without an IRIX `sync`/clean shutdown loses the XFS
  writes (the `cp` to `/usr/etc/dgld`). Either deploy **in the same boot** as the test, or `sync`
  + clean-shutdown to persist. `/tmp` is tmpfs (cleared each boot) — redeploy `gltri` per session.
- **`sync` is slow on a fresh boot** — it blew a 12s `run()` timeout and desynced the telnet
  channel (a timed-out command leaves stale output that corrupts the next). Fix: `IRIXTelnet.run`
  now `_drain()`s stale bytes before each command (resync); avoid `sync` in single-session tests.
- **Rapid successive boots race on the hostfwd port** (2324): a lingering QEMU can hold the telnet
  forward so the next boot's telnetd is unreachable (`wait_for_login` then times out). Always
  `qemu_session_cleanup` and let it settle between boots.

## Open question — the libgl DGL path (DISPLAY value)

IRIS GL only takes the **DGL/remote** path (TCP 5232 → dgld → our shim) for a remote-style
`DISPLAY`; `:0`/`unix:0` use a local transport that bypasses dgld. Need a `DISPLAY` that libgl
treats as remote **and** routes to the guest's own `:5232`:
- `DISPLAY=10.0.2.15:0` (guest's own slirp IP) — slirp may not loop the guest's own IP back; the
  one attempt produced no shim spawn.
- `DISPLAY=127.0.0.1:0` (loopback) — to try; routes to the guest's own inetd, but libgl may
  special-case 127.0.0.1 as local.
The decisive, libgl-independent test is to open `127.0.0.1:5232` directly (no app) once the shim
is installed and read `/tmp/dgld_shim.log` — that proves inetd→shim→`XOpenDisplay(:0)`→
`render_connect` end-to-end; only then bring libgl into it.

## BREAKTHROUGH — full DGL pipeline proven end-to-end (2026-06-14)

The whole transport works. With `DISPLAY=dglhost:0.0` (an `/etc/hosts` alias `127.0.0.1 dglhost`)
and the shim installed at `/usr/etc/dgld`, running gltri produced:
```
=== dgld_shim start (pid=270) renderer=10.0.2.2:6053 ===
XOpenDisplay(:0) -> OK
render_connect(10.0.2.2:6053) -> fd=5
op=0x1234 oplen=-1
capture server: CONN + "G 4 bytes: 1234"   ← shim forwarded libgl's first DGL word to the host
```
i.e. **gltri → libgl (DGL path) → inetd → our shim → local Xsgi window + forward to host renderer.**

### Why `DISPLAY=dglhost:0.0` is the magic (from `decomp/dglopen.json`)
`dglopen(display, 4)`:
- compares `gethostbyname(display_host)` canonical vs `gethostname()` canonical. **Same → local
  direct render** (hits the NG1-RevA lib mismatch — the "early model NG1 / libraries not
  compatible" death). **Different → remote (DGL).**
- For remote it then **`XOpenDisplay(host:0)` + `XSGIMiscQueryExtension`** — only proceeds to DGL if
  that X server exists AND has the SGI-Misc extension; else `uStack_510=0xd` → **"-13 / not DGL
  capable"** (this is why `DISPLAY=10.0.2.2:0.0` fails: the container has no X server).
- So the alias must (a) resolve to a canonical name ≠ hostname (→ remote) and (b) point at an X
  server WITH XSGIMisc = **the guest's own Xsgi**. `dglhost → 127.0.0.1` gives both: XOpenDisplay
  hits the guest Xsgi, and the DGL socket goes to `dglhost:5232 = 127.0.0.1:5232 = guest inetd =
  our shim`. (`10.0.2.15:0` doesn't work — slirp won't loop the guest's own IP; `127.0.0.1:0` and
  the real hostname both read as *local* → RevA.)

### The one remaining unknown — the connection handshake reply
libgl's **first DGL word is `0x1234`** and it then **blocks waiting for a reply** (a value-returning
probe — the `gl_data_check_xdr` byte-order/identity check). Replying with a bare echo `0x1234`
advances the error from "read error" to **"can't talk to dgld on dglhost:0.0"** — so the reply must
be a specific multi-word dgld greeting (dgld identity + version + byte order), not an echo. This is
emitted by `gl_socket_init`/dgld's connection-accept code, which is NOT in the current decomps.

**Login opcodes (from `decomp/dglcmds_all.json`)**: `0x10002` dglcheckxdr (arg 1000), `0x10003`
dglxdrformat (2 bytes), `0x10007` dglversion, `0x10010` dglloginX, `0x10013` dglxauthority — these
come AFTER the `0x1234` connection handshake.

## NEAR-MILESTONE-0 — live pipeline runs, window on the desktop (2026-06-14 late)

Architecture settled: the shim is a **transparent byte proxy** (`dgld_proxy.c`) — it creates the
local Xsgi window, sends `"PVSH"+wininfo(wid,x,y,w,h)`, then pipes libgl↔host raw. ALL DGL protocol
logic lives on the host in `sgi_glremote/`:
- `dgl_framed.py` `DglFramedConnection` — the real framed protocol (0x1234→0xffffedcc, then the
  `[0x10000000|len]` buffer framing), handshake replies **validated against the live capture**
  (`tests/test_dgl_framed.py`).
- `proxy_renderer.py` `ProxyServer` — handshake + decode GL payloads → OSMesa → frame to pvrex3
  `gl-listen`. Offline smoke test renders the red triangle to a mock QEMU (`tests/test_proxy_renderer.py`).
- 12 host tests green.

**Live result** (`run_m0_final.py`): the proxy log shows the FULL login handshake flowing through
our renderer (`0x1234`→reply, dglxdrformat, dglloginX, dglversion, dglxauthority, gversion 0x143)
and **the GL window mapped on the Indigo Magic Desktop** (640-wide black window verified in the
screendump at the reported rect). gltri reaches the LAST login step — `op 0x1` = `gversion(buf)`
(returns scalar + a 12-byte version-string array) — we reply, but **gltri then closes before
`winopen`**: our gversion reply's **array packing isn't what `gl_mem_unpack_array` expects**. That
one format detail is all that stands between here and the triangle drawing.

### The final blocker — the gversion (op 0x1) reply array format
`gl_d_gversion(buf)` (decomp): sends `[op=1][0xc][0xc]`, reads the reply via `gl_comm_read_data(1)`,
then `gl_mem_unpack_array(buf, comm_buffer+8, copy)` and returns `comm_buffer+4` (scalar). So the
reply is `[0x10000000|len][scalar@+4][array@+8]`. We send `[0x10000010][1]["GL4.0"+pad(12B)]` — the
scalar+offsets are right but the **array body must match `gl_mem_pack/unpack_array`'s on-wire format**
(likely a dim/count header before the bytes, not raw). RE `gl_mem_pack_array`/`gl_mem_unpack_array`
(or sniff a real dgld that survives gversion — patch/skip the NG1-RevA check, or use a demo whose
winopen path doesn't crash the real dgld) to get the exact bytes. Then winopen→GL→triangle = M0.

## Next (precise)
1. **Sniff the real handshake** (definitive). FIRST ATTEMPT (`dgld_sniff.c`, `run_m0_sniff.py`):
   socketpair + fork/exec `/usr/etc/dgld.orig`, proxy fd0↔socketpair. **Didn't capture** — gltri
   got `Memory fault(coredump)` and `/tmp/dgld_sniff.log` was never created. Likely the real dgld
   misbehaves/crashes when its socket is an **`AF_UNIX` socketpair** instead of the real inet
   socket inetd hands it (getpeername / socket-option calls), or it segfaults in graphics setup.
   FIXES to try: (a) use an `AF_INET` socketpair / a real loopback TCP pair so dgld's socket calls
   work; (b) leave the sniffer as a plain TCP man-in-the-middle: listen on a temp port, have the
   sniffer connect libgl↔(real dgld spawned via a second inetd-style listener); (c) simplest —
   tcpdump/snoop the loopback `:5232` while the **real** dgld (restore golden, original dgld)
   handles a libgl connection, and read the handshake off the wire.
2. Implement that reply in the shim (or, better, redesign the shim as a pure byte **proxy to the
   host renderer** and put the handshake logic in `sgi_glremote/server.py` — Python, no guest
   rebuilds per iteration).
3. With the handshake passing → capture winopen + the GL commands → feed OSMesa → frame to pvrex3
   `gl-listen` → triangle composited in the desktop window = Milestone 0.

Harness scripts: `run_m0_investigate.py`, `run_m0_buildbins.py`, `run_m0_probe.py`,
`run_m0_shimtest.py`, `run_m0_integrate.py`, `run_m0_dglpath.py` (the working DGL-path driver),
`run_m0_capture.py`.
