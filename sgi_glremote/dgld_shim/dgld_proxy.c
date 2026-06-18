/*
 * dgld_proxy.c — transparent DGL proxy shim (replaces /usr/etc/dgld).
 *
 * The real DGL wire protocol turned out to be the framed buffer protocol (sniffed 2026-06-14, see
 * progress_notes/irisgl_re/dgl_protocol.md), not a raw opcode stream — so instead of parsing it in
 * C, this shim is a DUMB BIDIRECTIONAL BYTE PROXY between libgl and the host renderer, and the
 * renderer (sgi_glremote, Python, fast to iterate) handles the whole protocol via DglFramedConnection.
 *
 * Flow (inetd spawns us with the libgl socket on fd 0/1):
 *   1. XOpenDisplay(:0) + create a 640x480 window on the guest's Xsgi (desktop-integrated; this is
 *      the X server the app's DISPLAY already pointed at, so no extra X hang).
 *   2. connect to the host renderer (10.0.2.2:6053, override DGLSHIM_RENDERER).
 *   3. send one control header  "PVSH" + wininfo(wid,x,y,w,h)  so the renderer knows where to
 *      composite the frame.
 *   4. select() loop: pipe libgl(fd0) -> renderer and renderer -> libgl(fd1), raw, until EOF.
 *
 * Build on irix-devel:  cc -n32 -O -o dgld_proxy dgld_proxy.c -lX11 -lc
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
#include <sys/time.h>

static FILE *g_log;

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

static void put_be(unsigned char *p, unsigned int v)
{
    p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v;
}

int main(int argc, char **argv)
{
    const char *rhost = "10.0.2.2";
    int rport = 6053;
    const char *env = getenv("DGLSHIM_RENDERER");
    char hostbuf[64];
    Display *dpy;
    int rfd, screen, sx = 0, sy = 0, w = 640, h = 480;
    Window win, root, child;
    XWindowAttributes attr;
    unsigned char hdr[24], buf[8192];
    fd_set rfds;
    int maxfd, n;

    (void)argc; (void)argv;
    if (env && *env) {
        const char *colon = strchr(env, ':');
        if (colon && (size_t)(colon - env) < sizeof(hostbuf)) {
            memcpy(hostbuf, env, colon - env);
            hostbuf[colon - env] = '\0';
            rhost = hostbuf;
            rport = atoi(colon + 1);
        }
    }

    g_log = fopen("/tmp/dgld_proxy.log", "a");
    slog("\n=== dgld_proxy start pid=%d renderer=%s:%d ===\n", (int)getpid(), rhost, rport);

    dpy = XOpenDisplay(":0");
    slog("XOpenDisplay(:0) -> %s\n", dpy ? "OK" : "NULL");
    if (dpy) {
        screen = DefaultScreen(dpy);
        root = RootWindow(dpy, screen);
        win = XCreateSimpleWindow(dpy, root, 200, 150, w, h, 0,
                                  BlackPixel(dpy, screen), BlackPixel(dpy, screen));
        XStoreName(dpy, win, "IRIS GL (accelerated)");
        XMapWindow(dpy, win);
        XSync(dpy, False);
        if (XGetWindowAttributes(dpy, win, &attr)) {
            XTranslateCoordinates(dpy, win, root, 0, 0, &sx, &sy, &child);
            w = attr.width; h = attr.height;
        }
        slog("window mapped at screen (%d,%d) %dx%d\n", sx, sy, w, h);
    }

    rfd = render_connect(rhost, rport);
    slog("render_connect -> fd=%d\n", rfd);
    if (rfd < 0) {
        return 1;
    }

    /* control header: "PVSH" + wininfo(wid=1, x, y, w, h) */
    memcpy(hdr, "PVSH", 4);
    put_be(hdr + 4, 1);
    put_be(hdr + 8, (unsigned int)sx);
    put_be(hdr + 12, (unsigned int)sy);
    put_be(hdr + 16, (unsigned int)w);
    put_be(hdr + 20, (unsigned int)h);
    write(rfd, hdr, 24);

    maxfd = (rfd > 0 ? rfd : 0) + 1;
    for (;;) {
        FD_ZERO(&rfds);
        FD_SET(0, &rfds);
        FD_SET(rfd, &rfds);
        if (select(maxfd, &rfds, (fd_set *)0, (fd_set *)0, (struct timeval *)0) < 0) {
            break;
        }
        if (FD_ISSET(0, &rfds)) {            /* libgl -> renderer */
            n = read(0, buf, sizeof buf);
            if (n <= 0) {
                break;
            }
            write(rfd, buf, n);
            slog("C->R %d\n", n);
        }
        if (FD_ISSET(rfd, &rfds)) {          /* renderer -> libgl */
            n = read(rfd, buf, sizeof buf);
            if (n <= 0) {
                break;
            }
            write(1, buf, n);
            slog("R->C %d\n", n);
        }
    }
    slog("=== proxy end ===\n");
    if (dpy) {
        XCloseDisplay(dpy);
    }
    close(rfd);
    return 0;
}
