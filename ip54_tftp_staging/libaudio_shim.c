/*
 * libaudio_shim.c - minimal SGI Audio Library (AL) shim for emulated IP54.
 *
 * The real /usr/lib32/libaudio.so.1 drives an /dev/hdsp kernel driver (master + per-resource
 * nodes, a specific ioctl set, an mmap'd sample ring) that IP54 does not have, so ALopenport()
 * fails ("could not open the necessary audio ports") and AL apps like amesh never start.
 *
 * IP54 *does* have a simple write-based pvaudio device (/hw/pvaudio: ioctl rate/chans/bits + a
 * blocking write into a 64KB ring drained at real time by the QEMU AudioBackend). This shim
 * exports exactly the 14 AL symbols amesh imports and lowers them onto pvaudio:
 *   - ALopenport opens /hw/pvaudio and configures it -> the port "opens" (amesh proceeds).
 *   - ALwritesamps does a BLOCKING write into the pvaudio ring -> amesh self-paces to real time
 *     (the ring drains at the sample rate) AND real audio is produced (captured to the wav).
 *   - alGetFrameNumber/alGetFrameTime return the cumulative frame count -> amesh's mesh animation
 *     clock advances in step with playback.
 * libaudio depends only on libc and (other than AL apps) nothing imports it, so replacing it is
 * safe: libdmedia/libaudiofile do NOT use AL.
 *
 * Build on irix-devel (full MIPSpro + crt):  cc -n32 -mips3 -shared -o libaudio.so.1 \
 *     libaudio_shim.c -Wl,-soname,libaudio.so.1 -lc
 */
typedef unsigned long long stamp_t;

extern int open(const char *, int, ...);
extern int ioctl(int, int, void *);
extern long write(int, const void *, unsigned long);
extern int close(int);
extern void *malloc(unsigned long);
extern void free(void *);
extern int sprintf(char *, const char *, ...);

/* pvaudio ioctls (from audiotest.c, verified end-to-end) */
#define PV_RATE  0x6000
#define PV_CHANS 0x6001
#define PV_BITS  0x6002
#define O_WRONLY   1
#define O_CREAT    0x100
#define O_APPEND   0x8

/* --- diagnostic log to /tmp/shimlog (no stdio; raw open/write) --------------
 * Each AL entry point logs a one-line marker so we can see the LAST call amesh
 * made before it hangs/crashes (entry-without-exit = stuck there). */
static int slen(const char *s) { int n = 0; while (s[n]) n++; return n; }
static void slog(const char *s)
{
    int fd = open("/tmp/shimlog", O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (fd >= 0) { write(fd, s, (unsigned long)slen(s)); close(fd); }
}

/* Set to 1 to ACTUALLY play audio via pvaudio (blocking). 0 = decouple: discard
 * samples so amesh's render loop never blocks on the audio device (goal = render
 * the mesh; real audio is secondary). */
#define SHIM_PLAY_AUDIO 0

/* AL_SAMPLE_8/16/24 tokens -> bits */
static long width_bits(long w) { return (w == 1) ? 8 : (w == 3) ? 24 : 16; }

typedef struct { long bits; long channels; long qsize; } shim_config;
typedef struct { int fd; long bits; long channels; stamp_t frames; } shim_port;

static long g_rate = 44100;   /* updated from ALsetparams if a plausible rate appears */

void *ALnewconfig(void)
{
    shim_config *c = (shim_config *)malloc(sizeof(shim_config));
    slog("ALnewconfig\n");
    if (c) { c->bits = 16; c->channels = 2; c->qsize = 8192; }
    return c;
}

void ALsetwidth(void *cfg, long w)      { slog("ALsetwidth\n"); if (cfg) ((shim_config *)cfg)->bits = width_bits(w); }
void ALsetchannels(void *cfg, long n)   { slog("ALsetchannels\n"); if (cfg && n > 0) ((shim_config *)cfg)->channels = n; }
void ALsetqueuesize(void *cfg, long n)  { slog("ALsetqueuesize\n"); if (cfg && n > 0) ((shim_config *)cfg)->qsize = n; }
void ALseterrorhandler(void *fn)        { slog("ALseterrorhandler\n"); (void)fn; }

/* ALsetparams(device, pvbuf, nvals): pvbuf is (token,value) longs. Grab a plausible sample rate. */
long ALsetparams(long dev, long *pvbuf, long n)
{
    long i;
    char lb[96];
    (void)dev;
    sprintf(lb, "ALsetparams dev=%lx n=%ld tok0=%lx v0=%lx\n", dev, n,
            (pvbuf && n > 0) ? pvbuf[0] : -1L, (pvbuf && n > 1) ? pvbuf[1] : -1L);
    slog(lb);
    if (pvbuf) {
        for (i = 0; i + 1 < n; i += 2) {
            long v = pvbuf[i + 1];
            if (v >= 4000 && v <= 48000) g_rate = v;
        }
    }
    return 0;
}

/* ALgetparams: report our rate back for any (token,value) pair the app reads. */
long ALgetparams(long dev, long *pvbuf, long n)
{
    long i;
    char lb[96];
    (void)dev;
    sprintf(lb, "ALgetparams dev=%lx n=%ld tok0=%lx\n", dev, n,
            (pvbuf && n > 0) ? pvbuf[0] : -1L);
    slog(lb);
    if (pvbuf)
        for (i = 0; i + 1 < n; i += 2) pvbuf[i + 1] = g_rate;
    return 0;
}

void *ALopenport(char *name, char *dir, void *cfg)
{
    shim_config *c = (shim_config *)cfg;
    shim_port *p;
    long rate = g_rate, chans = c ? c->channels : 2, bits = c ? c->bits : 16;
    int fd = -1;
    char lb[96];
    (void)name; (void)dir;
    sprintf(lb, "ALopenport name=%s dir=%s\n", name ? name : "?", dir ? dir : "?");
    slog(lb);
    if (SHIM_PLAY_AUDIO) {
        fd = open("/hw/pvaudio", O_WRONLY);
        if (fd >= 0) {
            ioctl(fd, PV_RATE, &rate);
            ioctl(fd, PV_CHANS, &chans);
            ioctl(fd, PV_BITS, &bits);
        }
    }
    p = (shim_port *)malloc(sizeof(shim_port));
    if (!p) { if (fd >= 0) close(fd); return 0; }
    p->fd = fd; p->bits = bits; p->channels = chans; p->frames = 0;
    return p;                                   /* non-NULL -> the port "opened" */
}

/* ALwritesamps(port, buf, count): count = total samples. With SHIM_PLAY_AUDIO=0 we DISCARD the
 * samples (no blocking device write) so amesh's render loop is never gated on pvaudio draining. */
long ALwritesamps(void *port, void *buf, long count)
{
    shim_port *p = (shim_port *)port;
    slog("ALwritesamps\n");
    if (!p) return -1;
    if (SHIM_PLAY_AUDIO && p->fd >= 0 && count > 0)
        write(p->fd, buf, (unsigned long)(count * (p->bits / 8)));
    if (p->channels > 0) p->frames += (stamp_t)(count / p->channels);
    return 0;
}

long ALreadsamps(void *port, void *buf, long count)
{
    shim_port *p = (shim_port *)port;
    long i, bytes = count * (p ? p->bits / 8 : 2);
    char *b = (char *)buf;
    slog("ALreadsamps\n");
    if (b) for (i = 0; i < bytes; i++) b[i] = 0;     /* no input device -> silence */
    if (p && p->channels > 0) p->frames += (stamp_t)(count / p->channels);
    return 0;
}

long ALgetfilled(void *port) { (void)port; slog("ALgetfilled\n"); return 0; }  /* room -> keep writing */

void ALcloseport(void *port)
{
    shim_port *p = (shim_port *)port;
    slog("ALcloseport\n");
    if (p) { if (p->fd >= 0) close(p->fd); free(p); }
}

/* alGetFrameNumber/alGetFrameTime: a visualizer may poll these to pace itself to "playback
 * time".  Since we discard audio, advance a synthetic clock on EVERY call (≈ one 60 Hz frame's
 * worth of samples) so any "wait for time to pass" loop makes progress and terminates. */
static stamp_t g_clock;

int alGetFrameNumber(void *port, stamp_t *fnum)
{
    (void)port;
    slog("alGetFrameNumber\n");
    g_clock += (stamp_t)(g_rate / 60);
    if (fnum) *fnum = g_clock;
    return 0;
}

int alGetFrameTime(void *port, stamp_t *fnum, stamp_t *t)
{
    (void)port;
    slog("alGetFrameTime\n");
    g_clock += (stamp_t)(g_rate / 60);
    if (fnum) *fnum = g_clock;
    if (t)    *t = g_clock;
    return 0;
}
