/*
 * gwxq - in-guest X11 introspection + window-control helper for the IRIX
 * desktop "eyes" tooling. Compiled n32 on the irix-devel build host
 * (cc -n32 -O -o gwxq gwxq.c -lX11), shipped as gwxq.n32, pushed into a
 * running desktop guest via the gwagent channel, and driven over DISPLAY=:0
 * (the golden has DisplayManager._0.authorize:false, so a local root client
 * connects with no xauth -- but ONLY post-login; xdm grabs the server during
 * the clogin face-picker, so query gwxq only once 4Dwm is up).
 *
 * Modes (argv[1]):
 *   tree                          -> JSON array of every window: id,name,class,
 *                                    x,y,w,h (root-relative), mapped, wm_state,
 *                                    managed, depth, parent
 *   moveresize <win> <x> <y> <w> <h>  XMoveResizeWindow (protocol reconfigure;
 *                                    4Dwm honors it -- this is the reliable
 *                                    resize path, since interactive handle-drag
 *                                    does NOT engage via synthetic Newport input)
 *   move   <win> <x> <y>
 *   resize <win> <w> <h>
 *
 * <win> is a hex window id (0x...). JSON goes to stdout; the gwagent RUN
 * command returns it to the host (<=64KB; a desktop is well under that).
 */
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/Xatom.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static Display *dpy;
static Window root;
static Atom A_WM_STATE;

/* JSON-escape a string to stdout */
static void jstr(const char *s)
{
    putchar('"');
    if (s) {
        for (; *s; s++) {
            unsigned char c = (unsigned char)*s;
            if (c == '"' || c == '\\') { putchar('\\'); putchar(c); }
            else if (c == '\n') { putchar('\\'); putchar('n'); }
            else if (c == '\t') { putchar('\\'); putchar('t'); }
            else if (c < 0x20) printf("\\u%04x", c);
            else putchar(c);
        }
    }
    putchar('"');
}

/* WM_STATE: returns 0=none, 1=Normal(Withdrawn), 1+state otherwise.
 * state value per ICCCM: 0=Withdrawn,1=Normal,3=Iconic. -1 = no WM_STATE. */
static int wm_state(Window w)
{
    Atom type; int fmt; unsigned long n, after; unsigned char *p = 0;
    long st = -1;
    if (XGetWindowProperty(dpy, w, A_WM_STATE, 0, 2, False, A_WM_STATE,
            &type, &fmt, &n, &after, &p) == Success && p) {
        if (n >= 1) st = *(long *)p;
        XFree(p);
    }
    return (int)st;
}

static int first = 1;
static void emit(Window w, Window parent, int depth)
{
    XWindowAttributes a;
    int rx = 0, ry = 0; Window child;
    char *name = 0; XClassHint ch; int st;
    if (!XGetWindowAttributes(dpy, w, &a)) return;
    XTranslateCoordinates(dpy, w, root, 0, 0, &rx, &ry, &child);
    XFetchName(dpy, w, &name);
    ch.res_name = ch.res_class = 0;
    XGetClassHint(dpy, w, &ch);
    st = wm_state(w);

    if (!first) printf(",\n");
    first = 0;
    printf("{\"id\":\"0x%lx\",\"name\":", (unsigned long)w);
    jstr(name);
    printf(",\"inst\":"); jstr(ch.res_name);
    printf(",\"class\":"); jstr(ch.res_class);
    printf(",\"x\":%d,\"y\":%d,\"w\":%d,\"h\":%d", rx, ry, a.width, a.height);
    printf(",\"mapped\":%s", a.map_state == IsViewable ? "true" : "false");
    printf(",\"wm_state\":%d,\"managed\":%s", st, st >= 0 ? "true" : "false");
    printf(",\"depth\":%d,\"parent\":\"0x%lx\"}",
           depth, (unsigned long)parent);

    if (name) XFree(name);
    if (ch.res_name) XFree(ch.res_name);
    if (ch.res_class) XFree(ch.res_class);
}

static void walk(Window w, Window parent, int depth)
{
    Window r, p, *kids = 0; unsigned int n = 0, i;
    emit(w, parent, depth);
    if (XQueryTree(dpy, w, &r, &p, &kids, &n)) {
        for (i = 0; i < n; i++) walk(kids[i], w, depth + 1);
        if (kids) XFree(kids);
    }
}

int main(int argc, char **argv)
{
    const char *mode = argc > 1 ? argv[1] : "tree";
    dpy = XOpenDisplay(":0");
    if (!dpy) { fprintf(stderr, "cannot open :0\n"); return 2; }
    root = DefaultRootWindow(dpy);
    A_WM_STATE = XInternAtom(dpy, "WM_STATE", False);

    if (!strcmp(mode, "tree")) {
        printf("[\n");
        walk(root, 0, 0);
        printf("\n]\n");
    } else if (!strcmp(mode, "moveresize") && argc >= 7) {
        Window w = (Window)strtoul(argv[2], 0, 0);
        XMoveResizeWindow(dpy, w, atoi(argv[3]), atoi(argv[4]),
                          (unsigned)atoi(argv[5]), (unsigned)atoi(argv[6]));
        XSync(dpy, False); printf("{\"ok\":true}\n");
    } else if (!strcmp(mode, "move") && argc >= 5) {
        Window w = (Window)strtoul(argv[2], 0, 0);
        XMoveWindow(dpy, w, atoi(argv[3]), atoi(argv[4]));
        XSync(dpy, False); printf("{\"ok\":true}\n");
    } else if (!strcmp(mode, "resize") && argc >= 5) {
        Window w = (Window)strtoul(argv[2], 0, 0);
        XResizeWindow(dpy, w, (unsigned)atoi(argv[3]), (unsigned)atoi(argv[4]));
        XSync(dpy, False); printf("{\"ok\":true}\n");
    } else {
        fprintf(stderr, "usage: gwxq tree | moveresize w x y w h | move w x y | resize w w h\n");
        return 1;
    }
    XCloseDisplay(dpy);
    return 0;
}
