/*
 * glscene.c — a small, independent IRIS GL demo for the accelerated-graphics pipeline.
 *
 * A spinning, depth-buffered, 6-colored cube on a dark background. Purpose: prove the DGL ->
 * host-renderer -> composite pipeline handles an INDEPENDENT IRIS GL program (not atlantis), with
 * a clean small lib closure (libgl/libX11/libc/libm) and no input/audio dependencies. Auto-
 * animates forever (the harness screendumps + kills it).
 *
 * Header-free (the dev disks lack base C headers): every IRIS GL / libc entry point is extern.
 * Build on the INDY machine (cc segfaults on sgi-ip54 'be'):
 *     cc -n32 -O -o glscene glscene.c -lgl -lX11 -lm
 */
extern void foreground(void);
extern void prefsize(int x, int y);
extern int  winopen(char *name);
extern void RGBmode(void);
extern void doublebuffer(void);
extern void gconfig(void);
extern void zbuffer(int bool);
extern void mmode(short m);
extern void perspective(short fovy, float aspect, float n, float f);
extern void translate(float x, float y, float z);
extern void rotate(short ang, char axis);
extern void pushmatrix(void);
extern void popmatrix(void);
extern void cpack(unsigned long c);
extern void czclear(unsigned long cval, int zval);
extern void bgnpolygon(void);
extern void endpolygon(void);
extern void v3f(float v[3]);
extern void swapbuffers(void);
extern void gexit(void);

#define MPROJECTION 1
#define MVIEWING    2
#define ZMAX        0x7fffff

static float cube[8][3] = {
    {-1.0f, -1.0f, -1.0f}, { 1.0f, -1.0f, -1.0f}, { 1.0f,  1.0f, -1.0f}, {-1.0f,  1.0f, -1.0f},
    {-1.0f, -1.0f,  1.0f}, { 1.0f, -1.0f,  1.0f}, { 1.0f,  1.0f,  1.0f}, {-1.0f,  1.0f,  1.0f}
};
static int face[6][4] = {
    {0, 3, 2, 1},   /* back  (-z) */
    {4, 5, 6, 7},   /* front (+z) */
    {0, 4, 7, 3},   /* left  (-x) */
    {1, 2, 6, 5},   /* right (+x) */
    {3, 7, 6, 2},   /* top   (+y) */
    {0, 1, 5, 4}    /* bottom(-y) */
};
/* cpack is 0xAABBGGRR: red, green, blue, yellow, cyan, magenta */
static unsigned long col[6] = {
    0xff0000ffUL, 0xff00ff00UL, 0xffff0000UL, 0xff00ffffUL, 0xffffff00UL, 0xffff00ffUL
};

int main(int argc, char **argv)
{
    int win, i;
    short ang = 0;

    foreground();
    prefsize(500, 500);
    win = winopen("glscene");
    RGBmode();
    doublebuffer();
    gconfig();
    zbuffer(1);

    mmode(MPROJECTION);
    perspective(450, 1.0f, 1.0f, 100.0f);   /* 45.0 deg */
    mmode(MVIEWING);

    for (;;) {
        czclear(0x00303030UL, ZMAX);         /* dark grey bg + clear depth */
        pushmatrix();
            translate(0.0f, 0.0f, -6.0f);
            rotate(ang, 'y');
            rotate((short)((ang * 7) / 13), 'x');
            for (i = 0; i < 6; i++) {
                cpack(col[i]);
                bgnpolygon();
                    v3f(cube[face[i][0]]);
                    v3f(cube[face[i][1]]);
                    v3f(cube[face[i][2]]);
                    v3f(cube[face[i][3]]);
                endpolygon();
            }
        popmatrix();
        swapbuffers();

        ang += 80;                            /* 8.0 deg/frame */
        if (ang >= 3600) ang -= 3600;
    }

    gexit();
    return 0;
}
