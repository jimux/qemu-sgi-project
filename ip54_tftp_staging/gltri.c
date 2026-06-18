/*
 * gltri.c — minimal IRIS GL test program for the accelerated-graphics work.
 *
 * Dual purpose: live DGL-capture source (#55) + Milestone-0 target (#60) — a red triangle
 * on black inside its window.
 *
 * NO standard headers: the dev disks lack the base C headers; we declare every entry point
 * extern (IRIS GL calls are real libgl.so functions; sleep is libc). Only crt1.o (C startup,
 * from dev.sw.lib) is needed to link main(). Build on the INDY machine session:
 *     cc -n32 -O -o gltri gltri.c -lgl -lX11 -lm
 * Types match the DGL opcode layouts: Coord=float, RGBvalue=short, Int32=int.
 */
extern void foreground(void);
extern void prefsize(int x, int y);
extern int  winopen(char *name);
extern void RGBmode(void);
extern void gconfig(void);
extern void ortho2(float left, float right, float bottom, float top);
extern void RGBcolor(short r, short g, short b);
extern void clear(void);
extern void bgnpolygon(void);
extern void endpolygon(void);
extern void v2f(float v[2]);
extern void gflush(void);
extern void gexit(void);
extern unsigned int sleep(unsigned int);

int main(int argc, char **argv)
{
    int win;
    float v0[2];
    float v1[2];
    float v2[2];

    v0[0] = 100.0f; v0[1] = 100.0f;
    v1[0] = 500.0f; v1[1] = 100.0f;
    v2[0] = 300.0f; v2[1] = 400.0f;

    foreground();
    prefsize(640, 480);
    win = winopen("gltri");       /* DGL winopen -> WID; triggers the handshake */

    RGBmode();
    gconfig();
    ortho2(0.0f, 640.0f, 0.0f, 480.0f);

    RGBcolor(0, 0, 0);
    clear();

    RGBcolor(255, 0, 0);          /* red */
    bgnpolygon();
        v2f(v0);
        v2f(v1);
        v2f(v2);
    endpolygon();

    gflush();                     /* flush the command FIFO to the server */
    sleep(8);                     /* keep the window up long enough to screendump */

    gexit();
    return 0;
}
