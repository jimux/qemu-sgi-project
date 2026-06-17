# Recovered interface: NG1_PIXELDMA (rex3DrawImage bulk-image path)

Recovered from Ghidra decompile of `rex3DrawImage` (`../decomp/interface_surface.json`,
$gp-resolved). `sys/ng1.h` is **not shipped** in any source tree, so these constants
exist only in the binary — this is the real interface behind the task-#16 PIO binary
patch.

## The PIO vs DMA decision (the patch site)

```c
// param_1 = short rect[4] {x0,y0,x1,y1};  param_2 = pixel data;
// param_3 = src stride (0 => packed);     param_4 = ??? (<<28 into REX3 cmd)
uint w = rect[2]-rect[0];          // uStack_d0
uint h = rect[3]-rect[1];          // uVar17
if (w==0 || h==0) return;
...
if (0x3fff < w*h) {                // <-- DMA when w*h >= 0x4000 (16384 px); else PIO
    // program REX3 draw regs through the mapped FIFO (puVar4):
    //   [0x54]=xy_start  [0x55]=xy_end  [0x88]=color  [0]=cmd|drawmode  [1]=0x46  [0x48]=0
    // then DMA the pixels in chunks:
}
```

`0x10056ee4: sltiu $v1,$a3,0x4000` is this `w*h < 0x4000` test (`$a3` = w*h). The
binary patch forces the result so the DMA branch is never taken (pure PIO).

## NG1_PIXELDMA ioctl + struct (recovered, CORRECTED 2026-06-13)

⚠️ The field NAMES below were corrected against independent ground truth: the standalone
NEWPORT `vdma(struct ng1_pixeldma_args *a)` in
`software_library/irix-657m-source/stand/arcs/.../NEWPORT/minigl3.c`, which fills/reads
the SAME struct (`sagfx.h`: `typedef struct ng1_pixeldma_args pixdma_t`). Layout
*offsets* are from the `rex3DrawImage` decompile; *names/semantics* from `vdma`.

```c
#define NG1_PIXELDMA  0x520a   // ioctl on the gfx fd; err: "rex3DrawImage: NG1_PIXELDMA failed"
#define NG1_WRITE     0x1      // host -> gfx (image draw). clear = gfx -> host (read)
#define NG1_STRIDE    0x2      // strided source (use pmstride), else packed

struct ng1_pixeldma_args {     // &uStack_d0 in rex3DrawImage; vdma field names:
    uint   xlen;      // +0x00  image width in pixels         (vdma a->xlen)
    uint   ylen;      // +0x04  rows in THIS chunk            (vdma a->ylen)
    uint   flags;     // +0x08  NG1_WRITE|NG1_STRIDE: 1=packed write, 3=strided write
    uint   pmstride;  // +0x0c  pixmode stride (0 if packed)  (vdma a->pmstride)
    uint   _f10;      // +0x10  = 0xa30 constant -- purpose UNCONFIRMED (pixel type/mode?)
    void  *buf;       // +0x14  host pixel buffer, advances per chunk (vdma a->buf)
    uint   _f18;      // +0x18  rex3 sets = rows*stride (byte count this chunk)
    /* vdma also references a->gfxaddr (dest gfx addr) and a->yzoom -- not seen in the
       rex3DrawImage fill, so the kernel likely derives gfxaddr from current REX regs
       and yzoom defaults; full struct may have more/longer fields. */
};
// gfx fd used: *(int*)(*(int*)(*(int*)(devpriv+0x10)+0x74)+0x64)
```

**Confirmed by name+offset+semantics:** xlen, ylen, flags (NG1_WRITE=1/NG1_STRIDE=2),
pmstride, buf. **Unconfirmed:** +0x10 (`0xa30`) and +0x18 names; `gfxaddr`/`yzoom`
offsets. The earlier version of this doc had wrong names (width/height/mode/stride/data)
and swapped `flags`(+0x08) with the `0xa30` field(+0x10) -- corrected here.

## Chunking

Max **0x180000 bytes (1.5 MB) per NG1_PIXELDMA call**. rows-per-chunk =
`0x180000 / bytes_per_row`; loop issuing NG1_PIXELDMA, advancing `data` by
`chunkbytes` and decrementing `height` until the remainder (< one chunk) is sent in a
final call. Packed transfers (param_3==0 or ==w) use mode 1; strided use mode 3 with
`stride=param_3`.

## Why it matters
This is the real bulk-image upload path. The pvrex3/pvfb side must implement the
`NG1_PIXELDMA` (0x520a) gfx ioctl with this struct + chunking to retire the
force-PIO binary patch (task #16) and get correct/fast image draws. PIO covers small
images (`w*h < 0x4000`) and already works.
