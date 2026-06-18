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
 *   4. open a 2nd "control" socket to the renderer ("PVWN" magic) and push a (wid,x,y,w,h,obscured)
 *      record whenever the window moves/resizes/occludes (X ConfigureNotify/VisibilityNotify), so
 *      the host overlay tracks the real desktop window — dynamic windowing (#68).
 *   5. select() loop over {libgl fd0, renderer rfd, X connection}: pipe libgl<->renderer raw, and
 *      drain X events into PVWN updates, until EOF.
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
static int g_cp_dbg;     /* #80 diagnostic: log the first N compute_pieces() stacking results */

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

/* Current absolute screen rect of our window (4Dwm reparents us, so ConfigureNotify x/y are parent-
 * relative — always re-resolve via XTranslateCoordinates to the root). */
static void query_geom(Display *dpy, Window win, Window root,
                       int *sx, int *sy, int *w, int *h)
{
    XWindowAttributes attr;
    Window child;
    if (XGetWindowAttributes(dpy, win, &attr)) {
        XTranslateCoordinates(dpy, win, root, 0, 0, sx, sy, &child);
        *w = attr.width;
        *h = attr.height;
    }
}

#define MAXP 16          /* cap on visible clip pieces (#80) */

typedef struct {
    int x, y, w, h;
} Rect;

#define RMIN(a, b) ((a) < (b) ? (a) : (b))
#define RMAX(a, b) ((a) > (b) ? (a) : (b))

/* Subtract occluder o from rect r, appending the (up to 4) visible bands to out[] (capacity max).
 * Returns the new count. If they don't overlap, r is appended whole. */
static int rect_subtract(Rect r, Rect o, Rect *out, int n, int max)
{
    int ix0 = RMAX(r.x, o.x), iy0 = RMAX(r.y, o.y);
    int ix1 = RMIN(r.x + r.w, o.x + o.w), iy1 = RMIN(r.y + r.h, o.y + o.h);
    if (ix0 >= ix1 || iy0 >= iy1) {                 /* no overlap: r fully visible */
        if (n < max) { out[n].x = r.x; out[n].y = r.y; out[n].w = r.w; out[n].h = r.h; n++; }
        return n;
    }
    if (iy0 > r.y && n < max) {                      /* top band */
        out[n].x = r.x; out[n].y = r.y; out[n].w = r.w; out[n].h = iy0 - r.y; n++;
    }
    if (iy1 < r.y + r.h && n < max) {                /* bottom band */
        out[n].x = r.x; out[n].y = iy1; out[n].w = r.w; out[n].h = (r.y + r.h) - iy1; n++;
    }
    if (ix0 > r.x && n < max) {                      /* left band (between the horizontal cuts) */
        out[n].x = r.x; out[n].y = iy0; out[n].w = ix0 - r.x; out[n].h = iy1 - iy0; n++;
    }
    if (ix1 < r.x + r.w && n < max) {                /* right band */
        out[n].x = ix1; out[n].y = iy0; out[n].w = (r.x + r.w) - ix1; out[n].h = iy1 - iy0; n++;
    }
    return n;
}

/* Compute the GL window's visible region: start from its screen rect R, subtract every window
 * stacked ABOVE it (root's children after it in bottom-to-top order) that is mapped and overlaps.
 * Result pieces (screen coords) go in pieces[]; *np is the count (0 == fully obscured). #80. */
static void compute_pieces(Display *dpy, Window win, Window root,
                           int x, int y, int w, int h, Rect *pieces, int *np)
{
    Rect cur[MAXP], nxt[MAXP], R;
    Window r2, parent, *children = 0, top = win, p = win;
    unsigned int nch = 0, i;
    int ncur, my_index = -1;

    R.x = x; R.y = y; R.w = w; R.h = h;
    cur[0] = R; ncur = 1;

    /* find the top-level ancestor (child of root) of win, for stacking lookup */
    for (;;) {
        Window rr, pp, *cc = 0;
        unsigned int nn = 0;
        if (!XQueryTree(dpy, p, &rr, &pp, &cc, &nn)) {
            break;
        }
        if (cc) {
            XFree(cc);
        }
        if (pp == root || pp == 0) {
            top = p;
            break;
        }
        p = pp;
    }

    if (!XQueryTree(dpy, root, &r2, &parent, &children, &nch) || !children) {
        *np = ncur;
        for (i = 0; i < (unsigned)ncur; i++) pieces[i] = cur[i];
        return;
    }
    for (i = 0; i < nch; i++) {
        if (children[i] == top) { my_index = (int)i; break; }
    }
    if (++g_cp_dbg <= 4) {     /* bounded one-line trace of the stacking lookup */
        slog("cp: win=0x%lx top=0x%lx nch=%u my_index=%d\n",
             (unsigned long)win, (unsigned long)top, nch, my_index);
    }
    for (i = (unsigned)(my_index + 1); my_index >= 0 && i < nch; i++) {
        XWindowAttributes a;
        Window ch;
        Rect o;
        int ox = 0, oy = 0, j, k;
        if (!XGetWindowAttributes(dpy, children[i], &a) || a.map_state != IsViewable) {
            continue;
        }
        XTranslateCoordinates(dpy, children[i], root, 0, 0, &ox, &oy, &ch);
        o.x = ox; o.y = oy;
        o.w = a.width + 2 * a.border_width;
        o.h = a.height + 2 * a.border_width;
        k = 0;
        for (j = 0; j < ncur; j++) {
            k = rect_subtract(cur[j], o, nxt, k, MAXP);
        }
        for (j = 0; j < k; j++) cur[j] = nxt[j];
        ncur = k;
        if (ncur == 0) {
            break;                                   /* fully obscured */
        }
    }
    XFree(children);
    *np = ncur;
    for (i = 0; i < (unsigned)ncur; i++) pieces[i] = cur[i];
}

/* Push one window-update record to the PVWN control connection:
 *   [wid][x][y][w][h][obscured][numpieces]  then numpieces * [px][py][pw][ph]   (all BE int32). */
static void send_pvwn(int cfd, int wid, int x, int y, int w, int h, int obscured,
                      Rect *pieces, int np)
{
    unsigned char rec[28 + MAXP * 16];
    int i, off;
    if (cfd < 0) {
        return;
    }
    if (np > MAXP) np = MAXP;
    put_be(rec + 0,  (unsigned int)wid);
    put_be(rec + 4,  (unsigned int)x);
    put_be(rec + 8,  (unsigned int)y);
    put_be(rec + 12, (unsigned int)w);
    put_be(rec + 16, (unsigned int)h);
    put_be(rec + 20, (unsigned int)obscured);
    put_be(rec + 24, (unsigned int)np);
    off = 28;
    for (i = 0; i < np; i++) {
        put_be(rec + off + 0,  (unsigned int)pieces[i].x);
        put_be(rec + off + 4,  (unsigned int)pieces[i].y);
        put_be(rec + off + 8,  (unsigned int)pieces[i].w);
        put_be(rec + off + 12, (unsigned int)pieces[i].h);
        off += 16;
    }
    write(cfd, rec, off);
}

int main(int argc, char **argv)
{
    const char *rhost = "10.0.2.2";
    int rport = 6053;
    const char *env = getenv("DGLSHIM_RENDERER");
    char hostbuf[64];
    Display *dpy;
    int rfd, cfd = -1, screen, sx = 0, sy = 0, w = 640, h = 480;
    int xfd = -1, obscured = 0, wid = 1, np = 0;
    Window win, root, child;
    XWindowAttributes attr;
    unsigned char hdr[24], buf[8192];
    Rect pieces[MAXP];
    fd_set rfds;
    struct timeval tv, now, last_poll;
    int last_sx, last_sy, last_w, last_h, last_obsc, sel;
    int maxfd, n;

    wid = (int)(getpid() & 0x7fffffff);          /* unique per proxy -> multi-window (#81) */

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
        /* track move/resize (StructureNotify) + occlusion (VisibilityChange) so the overlay follows
         * the desktop window and hides when fully covered. #68. */
        XSelectInput(dpy, win, StructureNotifyMask | VisibilityChangeMask);
        XMapWindow(dpy, win);
        XSync(dpy, False);
        if (XGetWindowAttributes(dpy, win, &attr)) {
            XTranslateCoordinates(dpy, win, root, 0, 0, &sx, &sy, &child);
            w = attr.width; h = attr.height;
        }
        xfd = ConnectionNumber(dpy);
        slog("window mapped at screen (%d,%d) %dx%d xfd=%d\n", sx, sy, w, h, xfd);
    }

    rfd = render_connect(rhost, rport);
    slog("render_connect -> fd=%d\n", rfd);
    if (rfd < 0) {
        return 1;
    }

    /* control header: "PVSH" + wininfo(wid, x, y, w, h) */
    memcpy(hdr, "PVSH", 4);
    put_be(hdr + 4, (unsigned int)wid);
    put_be(hdr + 8, (unsigned int)sx);
    put_be(hdr + 12, (unsigned int)sy);
    put_be(hdr + 16, (unsigned int)w);
    put_be(hdr + 20, (unsigned int)h);
    write(rfd, hdr, 24);

    /* 2nd connection: window-tracking control channel ("PVWN" magic + variable records). Kept
     * separate from the raw DGL byte pipe so we never interleave control into the GL stream. */
    cfd = render_connect(rhost, rport);
    if (cfd >= 0) {
        write(cfd, "PVWN", 4);
        if (dpy) {
            compute_pieces(dpy, win, root, sx, sy, w, h, pieces, &np);
        }
        send_pvwn(cfd, wid, sx, sy, w, h, (np == 0), pieces, np);
        slog("control cfd=%d wid=%d initial PVWN (%d,%d) %dx%d np=%d\n",
             cfd, wid, sx, sy, w, h, np);
    }

    maxfd = rfd;
    if (cfd > maxfd) maxfd = cfd;
    if (xfd > maxfd) maxfd = xfd;
    maxfd += 1;
    last_sx = sx; last_sy = sy; last_w = w; last_h = h; last_obsc = obscured;
    gettimeofday(&last_poll, (struct timezone *)0);
    for (;;) {
        FD_ZERO(&rfds);
        FD_SET(0, &rfds);
        FD_SET(rfd, &rfds);
        if (xfd >= 0) {
            FD_SET(xfd, &rfds);
        }
        tv.tv_sec = 0; tv.tv_usec = 200000;      /* wake at least 5x/sec to poll geometry */
        sel = select(maxfd, &rfds, (fd_set *)0, (fd_set *)0, &tv);
        if (sel < 0) {
            break;
        }
        if (FD_ISSET(0, &rfds)) {            /* libgl -> renderer */
            n = read(0, buf, sizeof buf);
            if (n <= 0) {
                break;
            }
            write(rfd, buf, n);
        }
        if (FD_ISSET(rfd, &rfds)) {          /* renderer -> libgl */
            n = read(rfd, buf, sizeof buf);
            if (n <= 0) {
                break;
            }
            write(1, buf, n);
        }
        /* Drain X events when readable (else select spins). Geometry + occlusion are derived from
         * the poll below (XQueryTree stacking), so events are just consumed here. */
        if (xfd >= 0 && FD_ISSET(xfd, &rfds)) {
            while (XPending(dpy)) {
                XEvent ev;
                XNextEvent(dpy, &ev);
                (void)ev;
            }
        }
        /* Poll absolute screen geometry + visible clip pieces on a ~200ms throttle, decoupled from
         * select wakeups: a reparenting WM (4Dwm) moves our FRAME on a title-bar drag without
         * ConfigureNotify-ing the client, and continuous GL streaming keeps fd0 hot so select rarely
         * times out. Visible pieces = window rect minus windows stacked above it. #68/#80. */
        if (dpy) {
            long ms;
            gettimeofday(&now, (struct timezone *)0);
            ms = (now.tv_sec - last_poll.tv_sec) * 1000
                 + (now.tv_usec - last_poll.tv_usec) / 1000;
            if (ms >= 200) {
                last_poll = now;
                query_geom(dpy, win, root, &sx, &sy, &w, &h);
                compute_pieces(dpy, win, root, sx, sy, w, h, pieces, &np);
                obscured = (np == 0);
                /* Send every poll (idempotent on the renderer; never misses a change). */
                send_pvwn(cfd, wid, sx, sy, w, h, obscured, pieces, np);
                if (sx != last_sx || sy != last_sy || w != last_w || h != last_h
                        || obscured != last_obsc) {
                    slog("PVWN change (%d,%d) %dx%d obsc=%d np=%d\n", sx, sy, w, h, obscured, np);
                    last_sx = sx; last_sy = sy; last_w = w; last_h = h; last_obsc = obscured;
                }
            }
        }
    }
    slog("=== proxy end ===\n");
    if (dpy) {
        XCloseDisplay(dpy);
    }
    if (cfd >= 0) {
        close(cfd);
    }
    close(rfd);
    return 0;
}
