# Recovered interfaces: cursor positioning + /dev/gfx board attach

Recovered by Ghidra decompilation of `xsgi.bin` (with `$gp` resolved). Source
decompiles in `../decomp/interface_surface.json`. Cross-checked against the shipped
`sys/gfx.h` (`software_library/irix-655-source/f/root/usr/include/sys/gfx.h`).

## 1. Cursor positioning — the real protocol (`simpleSetPointer`, `coreSetCursorPosition`)

Xsgi does **not** write VC2 `CURSOR_X/Y`, and does **not** use `GFX_POSCURSOR`. It
moves the cursor by issuing a **STREAMS `I_STR`-wrapped `QIOCSETCPOS`** ioctl on the
`/dev/shmiq` fd (`shmiqfds`). The kernel `shmiq.o` STREAMS module is what actually
drives the VC2 hardware cursor.

```c
// 0x5308 == STREAMS I_STR == (('S'<<8)|010). Generic "send an internal ioctl
// down a stream" wrapper. Payload is struct strioctl:
struct strioctl {            // <stropts.h>
    int   ic_cmd;            // the real ioctl command
    int   ic_timout;         // 0
    int   ic_len;            // payload length
    void *ic_dp;             // -> payload
};

// simpleSetPointer(screen_ptr, short x, short y):
struct shmiqsetcpos { short x; short y; };   // 4-byte payload  <-- RECOVERED
struct strioctl s = {
    .ic_cmd   = 0xc004510a,   // _IOWR('Q',10,sizeof(short[2])) == QIOCSETCPOS
    .ic_timout= 0,
    .ic_len   = 4,
    .ic_dp    = &(struct shmiqsetcpos){x, y},
};
ioctl(shmiqfds, I_STR /*0x5308*/, &s);   // on error: "Warning: QIOCSETCPOS ioctl failed"

// If an idev pointer device is attached (screen->dev->something@+0x18 > 0),
// it ALSO forwards the move to the idev layer with a second I_STR:
//   ic_cmd = 0x80085107 = _IOW('Q',7,8)  == QIOCIISTR  (indirect idev mux)
//   inner indirect cmd  = 0xc0086924 = _IOWR('i',36,8) == IDEVSETPTR
```

**Decoded constants**
| value | decode | name |
|---|---|---|
| `0x5308` | `('S'<<8)|010` | STREAMS `I_STR` (the outer transport on /dev/shmiq) |
| `0xc004510a` | `_IOWR('Q',10,4)` | `QIOCSETCPOS` (payload `struct shmiqsetcpos{short x,y}`) |
| `0x80085107` | `_IOW('Q',7,8)` | `QIOCIISTR` (indirect idev ioctl mux) |
| `0xc0086924` | `_IOWR('i',36,8)` | `IDEVSETPTR` (inner cmd carried by QIOCIISTR) |

**Implication for IP54/pvfb:** the cursor will move once the kernel `/dev/shmiq`
STREAMS path handles `I_STR(QIOCSETCPOS)` (move the VC2 sprite) and the
`I_STR(QIOCIISTR→IDEVSETPTR)` forward. No CURSOR_X/Y register write is ever issued by
Xsgi — so any QEMU/pvfb cursor logic keyed on VC2 CURSOR_X/Y writes will never fire.

## 2. /dev/gfx + /dev/opengl board attach (`irixKernInit`)

```c
// gfx.h: GFX_BASE=100; GFX_ATTACH_BOARD = GFX_BASE+3 = 103 = 0x67
// arg is struct gfx_attach_board_args { unsigned int board; void *vaddr; }  (CONFIRMED vs gfx.h:96)
int gl = open("/dev/opengl", O_RDWR|0x800 /*0x802*/);  fcntl(gl, F_SETFD, 5);
struct gfx_attach_board_args ab;
ab.board = screen;
ab.vaddr = (void *)(screen * RRMBOARDSIZE + RRMBOARDBASE);  // per-board mmap window
ioctl(gl, GFX_ATTACH_BOARD /*0x67*/, &ab);              // err: "GFX_ATTACH_BOARD ioctl failed"
int gfx = open("/dev/gfx", 0x802);  fcntl(gfx, F_SETFD, 5);
ioctl(gl, 1000, &(int[2]){0xffffffff, 0});              // board-info / private query (cmd 1000)
// ... (MAPALL etc. follow further down irixKernInit)
```

So: board attach is `GFX_ATTACH_BOARD(103)` on **/dev/opengl** (not /dev/gfx) with the
2-field `gfx_attach_board_args {board, vaddr}` struct (vaddr = the per-board RRM mmap
window, `sys/rrm.h`); `/dev/gfx` is opened as a separate fd. (Earlier this doc showed a
bare `&screen` -- corrected: it's the {board,vaddr} struct.)

## 3. Board enumeration + GFX_GETBOARDINFO (`ddxFindAvailableBoards..PDH`)

```c
int gl = open("/dev/opengl", O_RDWR);
int n  = ioctl(gl, 0x65, 0);                 // 0x65=101=GFX_GETNUM_BOARDS  err:"Couldn't get number of boards"
if (n > 0x10) n = 0x10;                       // max 16 boards
struct gfx_getboardinfo_args {                // CONFIRMED vs gfx.h:81
    unsigned int board;                       // +0x00  board index
    void        *buf;                         // +0x04  -> reply buffer
    unsigned int len;                         // +0x08  = 0x28 (40): reply buffer length
} gbi = { board_idx, infobuf, 0x28 };
ioctl(gl, 0x66, &gbi);                        // 0x66=102=GFX_GETBOARDINFO  err:"Get board info (short) failed"
// 0x28=40 is the REQUESTED reply length (Xsgi's "short" request); the reply's content
// is board-specific (Newport -> a prefix of struct ng1_info, layout TBD), NOT a fixed
// 40-byte struct per se. A "long" form also exists ("Get board info (long) failed").
// rex3Probe reads a board-type byte at boardinfo+0xc.
```

`GFX_GETNUM_BOARDS = GFX_BASE+1 = 101 = 0x65`, `GFX_GETBOARDINFO = GFX_BASE+2 = 102 =
0x66` — both confirmed against gfx.h. The full 40-byte board-info (`struct ng1_info`)
field layout (width/height/depth offsets) is read by the screen-open consumers
(`ddxOpenScreens`/`rex3KernInit`) — next drill target for the pvfb `gfx_info.length`
fix; offset +0xc is the board-type byte.

## How to reproduce / extend
Re-decompile any function with $gp resolved (turns `unaff_gp_lo+N` into named globals):
```
docker compose exec -T dev sh /workspace/_ghidra_decomp_gp.sh \
  0x105498ec "fn1,fn2,..." /workspace/out.json
```
`0x105498ec` = `_gp` = `.got(0x105418fc) + 0x7ff0`. See ../README.md "Ghidra recipe".
