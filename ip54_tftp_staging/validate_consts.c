/* validate_consts.c — compile & run on IRIX 6.5.5 (cc -o vc validate_consts.c).
 * Prints the ioctl numbers, struct sizes and field offsets we recovered by RE, so
 * the TARGET's own headers/compiler are the judge. Cross-check against
 * progress_notes/xsgi_re/VALIDATION.md.  (No ng1.h here — not shipped.) */
#include <stdio.h>
#include <sys/stropts.h>
#include <sys/shmiq.h>
#include <sys/idev.h>
#include <sys/gfx.h>
#include <stddef.h>

int main(void)
{
    printf("== STREAMS ==\n");
    printf("I_STR              = 0x%08x  (expect 0x5308)\n", (unsigned)I_STR);

    printf("== shmiq ==\n");
    printf("QIOCSETCPOS        = 0x%08x  (expect 0xc004510a)\n", (unsigned)QIOCSETCPOS);
    printf("QIOCIISTR          = 0x%08x  (expect 0x80085107)\n", (unsigned)QIOCIISTR);
    printf("QIOCSETCURS        = 0x%08x\n", (unsigned)QIOCSETCURS);
    printf("QIOCGETINDX        = 0x%08x\n", (unsigned)QIOCGETINDX);
    printf("sizeof(shmiqsetcpos)= %d  (expect 4)\n", (int)sizeof(struct shmiqsetcpos));
    printf("sizeof(muxioctl)    = %d  (expect 8)\n", (int)sizeof(struct muxioctl));
    printf("  muxioctl.index   @ %d\n", (int)offsetof(struct muxioctl, index));
    printf("  muxioctl.realcmd @ %d\n", (int)offsetof(struct muxioctl, realcmd));

    printf("== idev ==\n");
    printf("IDEVSETPTR         = 0x%08x  (expect 0xc0086924)\n", (unsigned)IDEVSETPTR);
    printf("IDEVSETPTRMODE     = 0x%08x\n", (unsigned)IDEVSETPTRMODE);
    printf("IDEVSETPTRBOUNDS   = 0x%08x\n", (unsigned)IDEVSETPTRBOUNDS);

    printf("== gfx ==\n");
    printf("GFX_GETNUM_BOARDS  = %d  (0x%x, expect 101/0x65)\n", GFX_GETNUM_BOARDS, GFX_GETNUM_BOARDS);
    printf("GFX_GETBOARDINFO   = %d  (0x%x, expect 102/0x66)\n", GFX_GETBOARDINFO, GFX_GETBOARDINFO);
    printf("GFX_ATTACH_BOARD   = %d  (0x%x, expect 103/0x67)\n", GFX_ATTACH_BOARD, GFX_ATTACH_BOARD);
    printf("GFX_MAPALL         = %d  (0x%x)\n", GFX_MAPALL, GFX_MAPALL);
    printf("GFX_POSCURSOR      = %d  (proto, not used by cursor path)\n", GFX_POSCURSOR);
    return 0;
}
