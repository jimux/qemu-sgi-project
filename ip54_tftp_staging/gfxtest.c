/* Ultra-minimal gfx test - zero includes, raw syscalls */
typedef unsigned int uint;

/* syscall numbers from sys/syssgi.h / sys/syscall.h */
extern int open(const char *, int);
extern int close(int);
extern int write(int, const void *, uint);
extern int ioctl(int, int, ...);
extern void _exit(int);

/* O_RDWR */
#define O_RDWR 2

/* GFX ioctl numbers */
#define GFX_BASE 100
#define GFX_GETBOARDINFO (GFX_BASE+6)
#define GFX_ATTACH_BOARD (GFX_BASE+1)
#define GFX_MAPALL       (GFX_BASE+2)

static void msg(const char *s) {
    const char *p = s;
    while (*p) p++;
    write(2, s, p - s);
}

int main() {
    int fd, r;

    msg("S1:open\n");
    fd = open("/dev/graphics", O_RDWR);
    if (fd < 0) { msg("FAIL:open\n"); return 1; }
    msg("S2:ok\n");

    msg("S3:info\n");
    {
        char buf[64];
        r = ioctl(fd, GFX_GETBOARDINFO, buf);
        if (r < 0) msg("FAIL:info\n");
        else { msg("OK:info name="); msg(buf); msg("\n"); }
    }

    msg("S4:attach\n");
    r = ioctl(fd, GFX_ATTACH_BOARD, 0);
    if (r < 0) msg("FAIL:attach\n");
    else msg("OK:attach\n");

    msg("S5:mapall\n");
    r = ioctl(fd, GFX_MAPALL, 0);
    if (r < 0) msg("FAIL:mapall\n");
    else msg("OK:mapall\n");

    msg("S6:close\n");
    close(fd);
    msg("DONE\n");
    return 0;
}
