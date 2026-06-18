/*
 * dgld_shim.c — a window-local, GL-forwarding replacement for IRIX /usr/etc/dgld.
 *
 * VirGL roadmap Step 5. Architecture (see progress_notes/irisgl_re/{dgl_protocol.md,
 * virgl_roadmap.md} + memory gpu_accel_irisgl):
 *
 *   guest IRIS GL app
 *     -> libgl (DGL client) -> connect localhost:5232
 *        -> inetd spawns THIS shim with the DGL socket as stdin/stdout
 *           (replaces the stock dgld in /etc/inetd.conf: "sgi-dgl ... /usr/etc/dgld")
 *
 *   This shim:
 *     1. speaks the DGL handshake to libgl (login/version) — keeps the app happy;
 *     2. on winopen, creates a REAL X window on the LOCAL Xsgi display (:0) via libX11
 *        (so the window is desktop-integrated and there is NO remote-X hang), returns
 *        the WID to libgl, and reports the window's screen geometry to the host renderer;
 *     3. FORWARDS the GL command stream to the host renderer (10.0.2.100:<port> via slirp),
 *        which renders on the host GPU and streams frames to QEMU's pvrex3 gl-listen
 *        socket -> composited into the window's screen region.
 *
 * Build on the irix-devel dev image (machine=indy):
 *     cc -n32 -O -o dgld_shim dgld_shim.c -lX11 -lc
 * Install: back up /usr/etc/dgld, drop this in, keep the inetd "sgi-dgl" line.
 *
 * STATUS: structural skeleton. The exact DGL handshake reply bytes + the variable-length
 * opcode arg layouts are confirmed by the live DGL capture (#55, still pending the X-forward
 * fix); points that depend on it are marked  TODO(capture).  The opcode->length table is
 * generated in dgl_oplen.h.
 */
#include <X11/Xlib.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#include "dgl_oplen.h"

/* DGL opcodes (dgl_opcodes_server.json / dgl_protocol.md) */
#define OP_GVERSION      0x0001
#define OP_WINOPEN       0x0132
#define OP_SWINOPEN      0x01a3
#define OP_SETDISPLAY    0x01ca
#define OP_DGLVERSION    0x10007
#define OP_DGLLOGINX     0x10010
#define OP_DGLXAUTHORITY 0x10013

/* shim -> host-renderer control protocol: [type:1][len:u32 BE][payload] */
#define MSG_WININFO 'W'   /* payload: wid,x,y,w,h (5 x u32 BE) — window screen rect */
#define MSG_GLDATA  'G'   /* payload: raw DGL u32 stream bytes */

static int   g_render_fd = -1;   /* socket to the host renderer */
static Display *g_dpy = NULL;    /* local Xsgi display :0 */
static int   g_next_wid = 1;

/*
 * File logging — inetd runs us as a "nowait" service with the client socket dup'd onto
 * fd 0/1/2, so anything on stderr would corrupt the DGL byte stream. Log to a file instead.
 * MIPSpro cc is pre-C99 (no variadic macros), so use a stdarg varargs function.
 */
static FILE *g_log = NULL;

static void slog(const char *fmt, ...)
{
    va_list ap;
    if (!g_log) {
        return;
    }
    va_start(ap, fmt);
    vfprintf(g_log, fmt, ap);
    va_end(ap);
    fflush(g_log);
}

/* ---- DGL socket (stdin/stdout) word I/O (big-endian u32) ---- */
static int read_word(unsigned int *out)
{
    unsigned char b[4];
    int got = 0, n;
    while (got < 4) {
        n = read(0, b + got, 4 - got);
        if (n <= 0) {
            return -1;
        }
        got += n;
    }
    *out = ((unsigned)b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3];
    return 0;
}

static void write_word(unsigned int w)
{
    unsigned char b[4];
    b[0] = w >> 24; b[1] = w >> 16; b[2] = w >> 8; b[3] = w;
    write(1, b, 4);                 /* reply to libgl on the DGL socket */
}

/* ---- host-renderer control channel ---- */
static int render_connect(const char *host, int port)
{
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in a;
    if (fd < 0) {
        return -1;
    }
    memset(&a, 0, sizeof(a));
    a.sin_family = AF_INET;
    a.sin_port = htons(port);
    a.sin_addr.s_addr = inet_addr(host);
    if (connect(fd, (struct sockaddr *)&a, sizeof(a)) < 0) {
        close(fd);
        return -1;
    }
    return fd;
}

static void render_msg(char type, const unsigned char *payload, unsigned int len)
{
    unsigned char hdr[5];
    if (g_render_fd < 0) {
        return;
    }
    hdr[0] = type;
    hdr[1] = len >> 24; hdr[2] = len >> 16; hdr[3] = len >> 8; hdr[4] = len;
    write(g_render_fd, hdr, 5);
    if (len) {
        write(g_render_fd, payload, len);
    }
}

static void render_wininfo(int wid, int x, int y, int w, int h)
{
    unsigned char p[20];
    int vals[5], i;
    vals[0] = wid; vals[1] = x; vals[2] = y; vals[3] = w; vals[4] = h;
    for (i = 0; i < 5; i++) {
        p[i * 4]     = vals[i] >> 24; p[i * 4 + 1] = vals[i] >> 16;
        p[i * 4 + 2] = vals[i] >> 8;  p[i * 4 + 3] = vals[i];
    }
    render_msg(MSG_WININFO, p, 20);
}

/* forward a command's raw words (opcode + args already read) to the renderer */
static void render_glwords(const unsigned int *words, int n)
{
    unsigned char *buf = malloc(n * 4);
    int i;
    for (i = 0; i < n; i++) {
        buf[i * 4]     = words[i] >> 24; buf[i * 4 + 1] = words[i] >> 16;
        buf[i * 4 + 2] = words[i] >> 8;  buf[i * 4 + 3] = words[i];
    }
    render_msg(MSG_GLDATA, buf, n * 4);
    free(buf);
}

/* opcode -> total word count (incl. opcode); 0 = variable (caller special-cases) */
static int oplen(unsigned int op)
{
    int i;
    for (i = 0; i < DGL_OPLEN_N; i++) {
        if (dgl_oplen[i].op == op) {
            return dgl_oplen[i].words;
        }
    }
    return -1;                       /* unknown */
}

/* ---- winopen: create a local X window, return WID, report geometry ---- */
static int handle_winopen(unsigned int op)
{
    unsigned int namelen_w, w, nwords, i;
    unsigned int words[512];
    char name[256];
    Window win, root, child;
    XWindowAttributes attr;
    int sx = 0, sy = 0, wid;

    words[0] = op;
    if (read_word(&namelen_w)) {     /* word[1] = padded byte length of the name */
        return -1;
    }
    words[1] = namelen_w;
    nwords = (namelen_w + 3) / 4;
    if (nwords > 500) {
        nwords = 500;
    }
    for (i = 0; i < nwords; i++) {
        if (read_word(&words[2 + i])) {
            return -1;
        }
    }
    /* unpack the window name (big-endian packed) */
    for (i = 0; i < nwords && i * 4 < (int)sizeof(name) - 4; i++) {
        name[i * 4]     = words[2 + i] >> 24; name[i * 4 + 1] = words[2 + i] >> 16;
        name[i * 4 + 2] = words[2 + i] >> 8;  name[i * 4 + 3] = words[2 + i];
    }
    name[namelen_w < sizeof(name) ? namelen_w : sizeof(name) - 1] = '\0';

    wid = g_next_wid++;
    if (g_dpy) {
        int screen = DefaultScreen(g_dpy);
        root = RootWindow(g_dpy, screen);
        /* default 640x480; the app resizes via prefsize/reshape (TODO(capture): honor it) */
        win = XCreateSimpleWindow(g_dpy, root, 100, 100, 640, 480, 0,
                                  BlackPixel(g_dpy, screen), BlackPixel(g_dpy, screen));
        XStoreName(g_dpy, win, name);
        XMapWindow(g_dpy, win);
        XSync(g_dpy, False);
        if (XGetWindowAttributes(g_dpy, win, &attr)) {
            /* absolute screen position of the window origin */
            XTranslateCoordinates(g_dpy, win, root, 0, 0, &sx, &sy, &child);
            w = attr.width;
            render_wininfo(wid, sx, sy, attr.width, attr.height);
        }
    }
    slog("winopen name='%s' wid=%d screen=(%d,%d) dpy=%s\n",
         name, wid, sx, sy, g_dpy ? "ok" : "null");
    /* tell the renderer to open a matching GL context for this WID */
    render_glwords(words, 2 + nwords);
    /* reply the WID to libgl (it does (conn<<8)|byte; the low byte is ours) */
    write_word((unsigned int)(wid & 0xff));
    return 0;
}

int main(int argc, char **argv)
{
    /*
     * inetd spawns us as `dgld -IM -tDGLTSOCKET` (see /etc/inetd.conf sgi-dgl line),
     * so argv is NOT ours to use for the renderer address. Default to the slirp gateway
     * (10.0.2.2 = the QEMU host / container, where sgi_glremote.shim_renderer listens),
     * port 6053; allow an env override DGLSHIM_RENDERER="host:port" for flexibility.
     */
    const char *rhost = "10.0.2.2";
    int rport = 6053;
    const char *env = getenv("DGLSHIM_RENDERER");
    char hostbuf[64];
    unsigned int op, words[64];
    int len, i;

    if (env && *env) {
        const char *colon = strchr(env, ':');
        if (colon && (size_t)(colon - env) < sizeof(hostbuf)) {
            memcpy(hostbuf, env, colon - env);
            hostbuf[colon - env] = '\0';
            rhost = hostbuf;
            rport = atoi(colon + 1);
        }
    }
    (void)argc; (void)argv;

    g_log = fopen("/tmp/dgld_shim.log", "a");
    slog("\n=== dgld_shim start (pid=%d) renderer=%s:%d ===\n",
         (int)getpid(), rhost, rport);

    g_dpy = XOpenDisplay(":0");          /* LOCAL Xsgi — window mgmt stays in-guest */
    slog("XOpenDisplay(:0) -> %s\n", g_dpy ? "OK" : "NULL");
    g_render_fd = render_connect(rhost, rport);
    slog("render_connect(%s:%d) -> fd=%d\n", rhost, rport, g_render_fd);

    /* DGL command loop: stdin/stdout is the libgl socket (via inetd) */
    while (read_word(&op) == 0) {
        slog("op=0x%x oplen=%d\n", op, oplen(op));
        switch (op) {
        case 0x1234:                     /* XDR/byte-order probe — echo it back so libgl
                                          * confirms same byte order and proceeds (gl_data_
                                          * check_xdr). The first word libgl sends. */
            write_word(0x1234);
            slog("  replied 0x1234 (xdr echo)\n");
            break;
        case OP_WINOPEN:
        case OP_SWINOPEN:
            handle_winopen(op);
            break;
        case OP_DGLVERSION:              /* [op][ver] -> reply 0 (accept) TODO(capture) */
            read_word(&words[1]);
            write_word(0);
            break;
        case OP_GVERSION:                /* reply server GL version TODO(capture) */
            len = oplen(op);
            for (i = 1; i < len; i++) {
                read_word(&words[i]);
            }
            write_word(2);
            break;
        case OP_DGLLOGINX:               /* login: consume args, no reply */
        case OP_DGLXAUTHORITY:
            len = oplen(op);
            for (i = 1; i < (len > 0 ? len : 1); i++) {
                read_word(&words[i]);
            }
            break;
        default:
            /* generic: forward fixed-length commands verbatim to the renderer */
            len = oplen(op);
            if (len <= 0) {
                /* CAPTURE MODE: unknown opcode/length — forward the single word and KEEP
                 * READING (word-by-word) so we capture the full raw DGL stream instead of
                 * bailing. libgl will block at the first value-returning opcode; up to there
                 * we log everything (the live handshake) to /tmp/dgld_shim.log + the renderer. */
                render_glwords(&op, 1);
                break;
            }
            words[0] = op;
            for (i = 1; i < len && i < 64; i++) {
                read_word(&words[i]);
            }
            render_glwords(words, len);
            break;
        }
    }
done:
    if (g_dpy) {
        XCloseDisplay(g_dpy);
    }
    if (g_render_fd >= 0) {
        close(g_render_fd);
    }
    return 0;
}
