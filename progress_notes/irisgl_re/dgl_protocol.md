# DGL wire protocol — connection handshake, value-returns, and WID model

RE'd from `dgld.elf` (DgldProj, `$gp=0x1006c9e4`) + the libgl client decompiles
(`decomp/dglopen.json`, `decomp/transport.json`) for the **accelerated-graphics host
renderer** (Phase 0a). This is what a host-side DGL server must implement to satisfy the
guest's `libgl`. Ground-truth byte confirmation comes from the live capture (task #55); the
*structure* below is settled from static RE.

## Transport
- TCP, port **5232** (`sgi-dgl`), selected by `$DISPLAY=<host>:0.0` → `libgl` resolves
  `<host>` via `gethostbyname` and `connect()`s.
- **LIVE-CONFIRMED selection (2026-06-14):** `dglopen(display,4)` compares `gethostbyname(<host>)`
  canonical vs `gethostname()` canonical: **same → local direct render** (dies on NG1-RevA lib
  mismatch); **different → remote/DGL**, and it then `XOpenDisplay(<host>:0)` + `XSGIMiscQueryExtension`
  — proceeds to DGL only if that X server exists AND has SGI-Misc, else error **-13 "not DGL capable".**
  So `DISPLAY=10.0.2.2:0.0` FAILS (no X server in the container). The working setup for the guest-
  side shim is an `/etc/hosts` alias **`127.0.0.1 dglhost`** + `DISPLAY=dglhost:0.0`: canonical
  `dglhost` ≠ hostname → remote; `XOpenDisplay(dglhost:0)` → the guest's own Xsgi (has XSGIMisc);
  DGL socket → `dglhost:5232 = 127.0.0.1:5232 = guest inetd = our shim`. (`10.0.2.15:0` fails —
  slirp won't loop the guest's own IP; `127.0.0.1:0`/real-hostname read as *local* → RevA.)
- **LIVE connection handshake — CAPTURED 2026-06-14** (sniffed the real dgld via `dgld_sniff.c`;
  raw bytes in `dgl_handshake_capture.txt`). Two phases:
  - **Phase 1 — raw byte-order probe, NO framing:** `C→S 0x1234` then `S→C 0xffffedcc` (== -0x1234,
    two's-complement byte-order confirm). This is THE reply that a bare echo got wrong.
  - **Phase 2 — the BUFFERED protocol:** every message is `[0x10000000 | payload_nbytes][payload]`
    (high nibble 0x1 = buffer marker, low 24 bits = payload byte length). The payload is the opcode
    stream; each value-returning request ends with a `0x10004` reply-sync marker + `0`, then the
    server sends a framed reply. Captured login sequence:

    | request (in buffer) | args | server reply (framed) |
    |---|---|---|
    | `0x10003` dglxdrformat | bytes 0x02, 0x01 | `[0x10000004][0]` |
    | `0x10010` dglloginX | str "IRIS"(node), str "root", str "root", flags 0x80/0xffffffff/0x80 | `[0x1000000c][0,0,0]` |
    | `0x10007` dglversion | 2 | `[0x10000004][0]` |
    | `0x10013` dglxauthority | 16B auth(≈0) + embedded `0x1ca` gl_setdisplay + str ":0.0" | `[0x10000004][0]` |
    | `0x143` gversion | — | `[0x10000004][1]` |
    | `0x1` … | 0x0c,0x0c | (trace ended — real dgld then hit NG1-RevA) |

    Strings are `[len:u32][bytes padded to 4]`. A host DGL server must (1) reply `0xffffedcc` to the
    raw `0x1234`, (2) speak the `[0x10000000|len]`-framed buffer protocol, replying framed words per
    the table; winopen/GL follow in the same framing.
- Wire (after the 0x1234 probe) = the **`[0x10000000|nbytes]`-framed buffer protocol**; payload is
  the big-endian `[opcode][args…]` u32 stream.
- **Value-returning** opcodes: the server writes back reply word(s); the client reads them via
  `gl_comm_read_data(n)`. Fire-and-forget opcodes get no reply. (Same mechanism for handshake
  and for `winopen`/`get*`/`query*`.)

## Two interpreters (KEY state machine)
`dgld` starts in the **login interpreter** (`dgld_login_interpret`) which loops
`dgld_aux_interpret` over the connection. Return convention: `0`=continue, `>0`=login done,
`<0`=fatal (`dgld_error` + `dcom_close` + `exit(2)`). The **login opcodes live in a high range
(0x10000+)**, distinct from the ~0x0–0x248 GL opcodes:

| opcode | call | words | server behaviour |
|---|---|---|---|
| `0x10010` | `dglloginX` | 3 | logs; **sets `dgld_interpreter = dgld_interpret`** (switch login→main loop); returns 0 |
| `0x10007` | `dglversion` | 2 | accepts arg ∈ {1,2} → reply **0** (OK); else reply **0x78** (bad-version) |
| `0x10013` | `dglxauthority` | var | calls `XSetAuthorization(name,len,data,…)` for X11 auth forwarding |

## Handshake sequence (client → server), all on the FIFO
From `dglopen` (`decomp/dglopen.json`), in order after `connect()`:
1. `gl_comm_get_bufsize()` — command-buffer size negotiation (exact wire form: confirm via #55).
2. `dglloginX(0x10010)` — `[0x10010][user][host…]` login. Server switches to the main interpreter.
3. `dglversion(0x10007)` `[…][2]` → **reads back** the status word (0 = accept v2).
4. `dglxauthority(0x10013)` — X auth (→ `XSetAuthorization`).
5. `dglversion(0x10007)` `[…][1]` — secondary negotiate.
6. `gl_setdisplay(0x1ca)` — bind the X display.
7. `gversion(0x1)` (3 words) → **reads back** the server GL version word.

So the handshake is **just DGL opcodes** (some value-returning) on the same stream — our decoder
handles it uniformly; no special parser needed. For each value-returning op the server replies:
`dglversion`→`0`, `gversion`→a version word (1 or 2), `gl_setdisplay`→success.

## winopen / WID model (the routing key)
- `winopen` = opcode **0x132** (variable: `[0x132][padded_namelen][name…]`); `swinopen` = **0x1a3**
  (2 words). Server replies **one word** = the WID.
- Client: `gl_dgl_winopen` reads back 1 word and computes
  **`WID = (connection_slot << 8) | reply_byte`** (`gl_dgl_wid_from_gl`, libgl `transport.json`).
- **The real window creation is in libgl (server-mode), not in `dgld`** — `swinopen`/
  `gl_dgl_wid_from_gl` are PLT/RLD stubs in `dgld.elf`. (The `tgl_*`/`gen_cmd`/`getseed`/
  `irandom` cluster is a *test command generator*, not the dispatch path — ignore it.)
- **Consequence for us:** the **WID byte is ours to assign.** Our renderer returns its own WID
  (e.g. sequential), tracks WID→host-window/context, and the client treats the composite WID as
  an opaque handle. `winset(wid)` opcodes then select the current window per connection; we route
  subsequent GL commands to that window's context, render, read back, and tag frames with the WID
  for the QEMU compositor to place (WID → `RRM_ValidateClip` record → screen rect).

## What still needs the live capture (#55)
- Exact `gl_comm_get_bufsize` wire bytes + reply.
- Exact reply word format for each value-returning op (confirm single u32, byte order).
- Arg/binary layout for the 67 variable-length GL opcodes (the capture decodes them empirically
  against `dgl_opcodes_server.json`).

## Opcode tables (already complete, reuse)
`progress_notes/irisgl_re/dgl_opcodes_server.json` (547, authoritative decoder) +
`dgl_opcodes.json` (606, client encoders w/ `cmd_words`).
