/*
 * winmove.c — tiny X helper to move (and optionally resize) a top-level window by name, for the
 * #68 dynamic-windowing test. Finds the window whose WM_NAME contains argv[1] (searching root's
 * children and one level into WM reparent frames), then XMoveWindow / XMoveResizeWindow it. The
 * dgld_proxy polls absolute screen geometry, so this deterministically exercises the overlay-follow
 * path without fiddly synthetic title-bar drags.
 *
 *   winmove "IRIS GL" <x> <y> [w h]
 *
 * Build on irix-devel:  cc -n32 -O -o winmove winmove.c -lX11 -lc
 */
#include <X11/Xlib.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static Window find_named(Display *d, Window w, const char *name, int depth)
{
    Window r, parent, *ch = 0, found = 0;
    unsigned int n = 0, i;
    char *wn = 0;

    if (XFetchName(d, w, &wn) && wn) {
        if (strstr(wn, name)) {
            found = w;
        }
        XFree(wn);
    }
    if (found || depth <= 0) {
        return found;
    }
    if (XQueryTree(d, w, &r, &parent, &ch, &n) && ch) {
        for (i = 0; i < n && !found; i++) {
            found = find_named(d, ch[i], name, depth - 1);
        }
        XFree(ch);
    }
    return found;
}

int main(int argc, char **argv)
{
    Display *d;
    Window root, w;

    if (argc < 3) {
        fprintf(stderr, "usage: winmove <name> <x> <y> [w h] | <name> lower\n");
        return 1;
    }
    d = XOpenDisplay(":0");
    if (!d) {
        fprintf(stderr, "winmove: cannot open :0\n");
        return 1;
    }
    root = DefaultRootWindow(d);
    /* "winmove <name> lower|raise" — restack the matching root-child window so it occludes (raise)
     * or is occluded by (lower) others, exercising piece-level clipping (#80). Scan root's direct
     * children explicitly (and log each) — robust in the minimal-WM xdm environment. */
    if (argc == 3 && (strcmp(argv[2], "lower") == 0 || strcmp(argv[2], "raise") == 0)) {
        Window rr, parent, *ch = 0, hit = 0;
        unsigned int nch = 0, i;
        int do_raise = (strcmp(argv[2], "raise") == 0);
        if (XQueryTree(d, root, &rr, &parent, &ch, &nch) && ch) {
            for (i = 0; i < nch; i++) {
                char *wn = 0;
                if (XFetchName(d, ch[i], &wn) && wn) {
                    printf("winmove: root child 0x%lx name='%s'\n", (unsigned long)ch[i], wn);
                    if (strstr(wn, argv[1])) {
                        hit = ch[i];
                    }
                    XFree(wn);
                }
            }
            XFree(ch);
        }
        if (!hit) {
            fprintf(stderr, "winmove: '%s' not among root children\n", argv[1]);
            XCloseDisplay(d);
            return 2;
        }
        if (do_raise) {
            XRaiseWindow(d, hit);
        } else {
            XLowerWindow(d, hit);
        }
        XSync(d, False);
        printf("winmove: %s 0x%lx\n", do_raise ? "raised" : "lowered", (unsigned long)hit);
        XCloseDisplay(d);
        return 0;
    }
    w = find_named(d, root, argv[1], 3);
    if (!w) {
        fprintf(stderr, "winmove: window '%s' not found\n", argv[1]);
        return 2;
    }
    if (argc < 4) {
        fprintf(stderr, "usage: winmove <name> <x> <y> [w h] | <name> lower\n");
        XCloseDisplay(d);
        return 1;
    }
    /* Move the WM FRAME (the client's parent), like a real title-bar drag — this changes the
     * client's absolute screen position unambiguously, which the proxy catches by polling
     * XTranslateCoordinates. Falls back to the client itself if not reparented. */
    {
        Window rr, parent = 0, *ch = 0;
        unsigned int nch = 0;
        Window target = w;
        if (XQueryTree(d, w, &rr, &parent, &ch, &nch)) {
            if (ch) {
                XFree(ch);
            }
            if (parent != 0 && parent != root) {
                target = parent;
            }
        }
        printf("winmove: client=0x%lx frame=0x%lx -> moving 0x%lx to %s,%s\n",
               (unsigned long)w, (unsigned long)parent, (unsigned long)target, argv[2], argv[3]);
        fflush(stdout);
        XMoveWindow(d, target, atoi(argv[2]), atoi(argv[3]));
        XSync(d, False);
    }
    XCloseDisplay(d);
    return 0;
}
