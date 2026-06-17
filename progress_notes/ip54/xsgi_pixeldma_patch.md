# Xsgi bulk-image fix — NG1_PIXELDMA threshold binary patch

Date: 2026-06-12. Closes the "granite bands / missing weave & fm icons"
graphics bug (task 16).

## The bug

Xsgi's Newport DDX splits image transfers by size:
- < 16 KB (`sltiu rX, bytecount, 0x4000`): PIO loop writing HOSTRW0@GO
- ≥ 16 KB: `ioctl(gfx_fd, NG1_PIXELDMA=0x520a, &ng1_pixeldma_args)`

On our IP54 franken-board the 0x520a ioctl is swallowed silently
(gfxioctl's dispatch only handles the 'e'/'i'/'g' command families;
0x520a is family 'R'; it reaches NEITHER pvfb's gf_Private NOR the
linked-but-dead Ng1PixelDma — both proven by instrumentation). It
evidently returns "success", so the DDX never prints its
"NG1_PIXELDMA failed, errno = %d" message and never falls back —
large PutImages just vanish. Root tile fills decompose into small
seeds (painted) + huge seeds (lost) + scr2scr replication of unpainted
regions → the infamous three granite bands.

## The fix

Binary-patch the size-threshold compare in the three DrawImage
variants so EVERY transfer takes the PIO path (PIO is fine in TCG —
the X startup solid fill already pushes 1.3 MB through it):

| site | original | patched |
|---|---|---|
| rex3DrawImage+0x100 (0x10056ee4) | `sltiu $v1,$a3,0x4000` (0x2ce34000) | `li $v1,1` (0x24030001) |
| rex3DrawImage24+0xac (0x100576c4) | `sltiu $at,$t4,0x4000` (0x2d814000) | `li $at,1` (0x24010001) |
| rex3DrawImage12+0xfc (0x102562c0) | `sltiu $t2,$a3,0x4000` (0x2cea4000) | `li $t2,1` (0x240a0001) |

Each compare feeds `bnez → PIO path`, so forcing rX=1 always selects
PIO. (rex3ReadImage* variants have the same pattern; XGetImage from
screen will still lose data until they're patched too — cosmetic for
now.)

Artifacts:
- `/workspace/xsgi.bin` — pristine Xsgi (3,871,564 bytes, extracted
  from the guest via in-guest `split -b 204800` + per-chunk offline
  pyirix extraction, all 19 chunks `sum`-verified; multi-extent read
  bug sidestepped because each chunk is single-extent)
- `/workspace/xsgi.patched` — patched copy
- injected at the symlink-chain target `/usr/gfx/arch/IP22NG1/Xsgi`
  (`/usr/bin/X11/Xsgi → /var/arch/X11/Xsgi → ../../../usr/gfx/arch/IP22NG1/Xsgi`)

## Verified

`xsetroot -bitmap granite` now covers the FULL 1280×1024 root
(framebuffers/a_pdma_granite.png) — previously three bands.

## Reverse-engineering trail (for posterity)

`sys/ng1.h` (ioctl number + args struct) was never shipped on any CD.
Evidence chain: pvrex3 draw/reg/DCB trace events proved zero MMIO loss
and gave the exact op plan; `strings Xsgi` named the path
("rex3DrawImage: NG1_PIXELDMA failed"); kernel-side instrumentation
ruled out gf_Private and Ng1PixelDma (PROM stub patch, no behavior
change); the threshold and cmd came from capstone disassembly of the
extracted binary — `.dynsym` gave rex3DrawImage* addresses, the error
string xrefs (lui/addiu pairs) located the ioctl block, and
`addiu $a1, $zero, 0x520a` before `jalr $s4` exposed the command word.
ng1_pixeldma_args semantics (flags/xlen/ylen/pmstride/buf/gfxaddr/yzoom)
recovered from the IDE diagnostics' vdma() in
irix-657m stand/arcs/ide/fforward/graphics/NEWPORT/minigl3.c.
