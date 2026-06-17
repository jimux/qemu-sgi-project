# IP54 mouse/keyboard fix — concrete recipe (B4)

Root cause (see memory `mouse_root_cause`): the graphics-console keyboard init
(`gfx_earlyinit`/`ng1_earlyinit`) is **skipped on IP54**, so `htp_register_board` never
runs → the `tportpckbd` decoder (`pckbd_rput`) is never installed → raw PS/2 bytes reach
idev/X undecoded → cursor never moves.

## The exact call to replicate (recovered from golden /unix, ng1_earlyinit @0x88262298)
```
htp_register_board(
    a0 = board_handle,                 // lw(board_struct) — registry key
    a1 = &ng1_htp_fncs,                // 0x882c0000-0x1550 = 0x882beab0 (EXISTING table,
                                       //   holds pckbd_rput@0x88268bd0 / pckbd_wput@0x88268b70)
    a2 = *(u16*)(board_struct+0x20),   // keyboard port index
    a3 = *(u16*)(board_struct+0x22));  // mouse port index
```
`htp_register_board` (0x88067c9c) just records {a0,a2,a3} into the registry table at
`0x882d45c8+0x1a18` and gp slots (-0x3a50/-0x3a54/-0x7458) — pure bookkeeping, no HW.
On the real path `board_struct` ($v0) comes from the gfx hwgraph board lookup; the two
u16 ports are the PS/2 keyboard/mouse port indices that `pckm` exposes.

## The COMPLETE fix — TWO calls (both pure-software, no HW probe)
Registration alone is NOT enough. `tp_init` does a second step via `gl_setporthandler`
(recovered): `gl_setporthandler` (0x8802d518) is a 3-instruction stub that just stores
its `a1` into a global handler slot (0x882ac7b8) — it **ignores a0**. `tp_init` calls
`du_keyboard_port()` (DUART, faults on IP54) only to get a port it then discards, then
`gl_setporthandler(port, handler=0x8825fe80)`. The handler `0x8825fe80` is a static
function in the tport keyboard code that links PS/2 input → the pckbd decoder.

So the IP54 fix = a **pvfb init hook** (`pvfbedtinit`, after pckm is up) that does:
```c
htp_register_board(handle, (void*)0x882beab0 /*ng1_htp_fncs*/, kbd_port, mouse_port);
gl_setporthandler(0, (void*)0x8825fe80);   /* a0 ignored; just sets the global handler */
```
Do NOT call `ng1_earlyinit`/`newportInit`/`tp_init`/`keyboard_init`/`du_keyboard_port` —
those touch DUART/GIO and fault on IP54. Both calls above are pure bookkeeping.

Open items to pin during implementation:
1. `board_handle` (a0 of htp_register_board) — non-zero registry key; confirm vs the
   stream-open consumer that reads gp[-0x3a50]/registry@0x882d45c8+0x1a18.
2. `kbd_port`/`mouse_port` — the pckm PS/2 port indices on IP54 (sgi_ioc2_kbd 8042 from M1).
3. Confirm the handler `0x8825fe80` doesn't itself touch DUART when invoked (it's the
   tport port handler; should drive pckm→pckbd). If it does, stub those reads.

## Verify
Rebuild kernel via lboot; boot to the xdm/desktop; inject `newport_mouse`; re-run the
idev probe (`run_a_idevtrace3.py`: breakpoint `pckbd_rput`@0x88268bd0 + `idevGenPtrEvent`)
— both should now FIRE; `newport_screendump` should show the cursor tracking the injected
position. (Restore golden before each boot; `sync; init 0` to shut down.)

## ⚠️ Implementation blocker found (the relink/static-symbol problem)
`htp_register_board`, `gl_setporthandler`, and `ng1_htp_fncs` are global symbols → a pvfb.c
extern decl links cleanly (lboot relink resolves them to their new post-relink addresses).
BUT the port handler **`0x8825fe80` is a STATIC function with no symbol** in /unix → it
can't be referenced by name, and a hardcoded address breaks after lboot relinks the kernel
(symbol addresses shift). Options for the implementation session:
1. **RE the handler @0x8825fe80 and reimplement it in pvfb.c** (most robust — it's the
   tport PS/2→pckbd port handler; disasm + port to C, calling the global pckbd/pckm fns).
2. Find a higher-level *exported* function that installs the keyboard port handler
   (search for a global wrapper around the static handler).
3. Resolve the handler address at runtime from a global table/registry that already holds
   it (if any does post-pckminit).
Until this is resolved, the two-call recipe can't be implemented as a clean kernel-linked
hook.

## Status
Fix mechanism FULLY RECOVERED (both calls + addresses + the table at ng1_htp_fncs+0xc8 =
pckbd_rput). Implementation BLOCKED on the static-handler-symbol problem above — needs one
more RE step (option 1/2) before the pvfb hook + kernel rebuild + graphical verify. This is
the genuinely-heavy, multi-cycle remainder.
