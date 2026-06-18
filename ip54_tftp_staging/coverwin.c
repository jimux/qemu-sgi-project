/*
 * coverwin.c — map a plain raised window at a given screen rect and keep it up, to stage occlusion
 * for the #80 piece-level clipping test. Being mapped-raised after the GL window, it sits ABOVE it
 * in the stacking order, so the proxy's compute_pieces() subtracts it from the GL window's visible
 * region.
 *
 *   coverwin <x> <y> <w> <h> [seconds]
 *
 * Build on irix-devel:  cc -n32 -O -o coverwin coverwin.c -lX11 -lc
 */
#include <X11/Xlib.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    Display *d;
    Window root, win, rr, parent, *ch = 0;
    unsigned int nch = 0;
    XSetWindowAttributes swa;
    int s, x, y, w, h, secs;

    if (argc < 5) {
        fprintf(stderr, "usage: coverwin <x> <y> <w> <h> [seconds]\n");
        return 1;
    }
    d = XOpenDisplay(":0");
    if (!d) {
        fprintf(stderr, "coverwin: cannot open :0\n");
        return 1;
    }
    x = atoi(argv[1]); y = atoi(argv[2]); w = atoi(argv[3]); h = atoi(argv[4]);
    secs = (argc > 5) ? atoi(argv[5]) : 30;
    root = DefaultRootWindow(d);
    s = DefaultScreen(d);
    /* override_redirect: bypass the WM, map directly as a raised child of root so it reliably sits
     * ABOVE the GL window in the stacking order (a controlled occluder for the #80 test). */
    swa.override_redirect = True;
    swa.background_pixel = WhitePixel(d, s);
    win = XCreateWindow(d, root, x, y, (unsigned)w, (unsigned)h, 0, CopyFromParent,
                        InputOutput, (Visual *)CopyFromParent,
                        CWOverrideRedirect | CWBackPixel, &swa);
    XStoreName(d, win, "COVER");
    XMapRaised(d, win);
    XSync(d, False);
    XQueryTree(d, win, &rr, &parent, &ch, &nch);
    if (ch) {
        XFree(ch);
    }
    printf("coverwin: 0x%lx at %d,%d %dx%d parent=0x%lx root=0x%lx for %ds\n",
           (unsigned long)win, x, y, w, h, (unsigned long)parent, (unsigned long)root, secs);
    fflush(stdout);
    sleep(secs);
    XCloseDisplay(d);
    return 0;
}
