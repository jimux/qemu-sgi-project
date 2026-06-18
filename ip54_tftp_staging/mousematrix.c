/* mousematrix.c - pin down WHY X's mouse path produces no pointer events.
 * X opens /dev/input/mouse, does IDEVINITDEVICE, then I_LINK to /dev/shmiq, and
 * (per live trace) sends NO enable ioctls. mouseread.c works only because it does
 * I_PUSH("mouse") + IDEVINITDEVICE + IDEVENABLEBUTTONS + IDEVENABLEVALUATORS.
 *
 * Decode put-procs run as data flows up the stream regardless of who reads, so a
 * direct-read test faithfully reports whether decode RUNS for a given config.
 * We test 3 configs on fresh fds, injecting motion during each window:
 *   A) open, NO I_PUSH, IDEVINITDEVICE, read         -> is "mouse" AUTO-pushed at open?
 *   B) open, I_PUSH, IDEVINITDEVICE, NO enable, read -> is the ENABLE required? (==X minus link)
 *   C) open, I_PUSH, IDEVINITDEVICE, ENABLE, read    -> known-good baseline
 * Run with xdm STOPPED. Bytes>0 => decode ran for that config.
 */
extern int open(const char *, int, int);
extern int ioctl(int, int, void *);
extern int close(int);
extern int read(int, void *, int);
extern int write(int, const void *, int);
extern unsigned int sleep(unsigned int);
extern int errno;

#define O_RDWR   2
#define O_NDELAY 4
#define I_PUSH              0x5302
#define IDEVINITDEVICE      0x80046933
#define IDEVENABLEBUTTONS   0x8040690a
#define IDEVENABLEVALUATORS 0x8040690b

static void s(const char *p){ int n=0; while(p[n]) n++; write(1,p,n); }
static void d(int v){
    char o[20]; char t[16]; int oi, neg, ti;
    oi=0; neg=0; ti=0;
    if(v<0){ neg=1; v=-v; }
    if(v==0) o[oi++]='0';
    else { while(v){ t[ti++]='0'+(v%10); v/=10; } while(ti) o[oi++]=t[--ti]; }
    if(neg) write(1,"-",1);
    write(1,o,oi);
}

/* read for `secs` seconds non-blocking; return total bytes read. Caller injects motion. */
static int readwin(int fd, int secs){
    unsigned char buf[512];
    int i, r, total;
    total = 0;
    for(i=0;i<secs;i++){
        r = read(fd, buf, sizeof(buf));
        if(r>0) total += r;
        sleep(1);
    }
    return total;
}

int main(){
    int fd, r, t;
    unsigned char bits[64];
    int zero, i;
    zero=0;
    for(i=0;i<64;i++) bits[i]=0xff;

    /* ---- config A: NO manual push (relies on autopush at open) ---- */
    s("=== A: open + INIT, NO push (autopush test) ===\n");
    fd = open("/dev/input/mouse", O_RDWR|O_NDELAY, 0);
    if(fd<0){ s("A open FAILED errno="); d(errno); s("\n"); }
    else {
        r = ioctl(fd, IDEVINITDEVICE, &zero); s("A INIT="); d(r); s("\n");
        s("A reading 12s -- INJECT MOTION NOW\n");
        t = readwin(fd, 12);
        s("A TOTAL="); d(t); s(t>0?"  <== decode RAN (autopush works)\n":"  <== dead\n");
        close(fd);
    }

    /* ---- config B: push + INIT, NO enable (== X's config minus shmiq link) ---- */
    s("=== B: open + push + INIT, NO enable (X-equivalent) ===\n");
    fd = open("/dev/input/mouse", O_RDWR|O_NDELAY, 0);
    if(fd<0){ s("B open FAILED errno="); d(errno); s("\n"); }
    else {
        r = ioctl(fd, I_PUSH, (void*)"mouse");  s("B I_PUSH="); d(r); s("\n");
        r = ioctl(fd, IDEVINITDEVICE, &zero);   s("B INIT="); d(r); s("\n");
        s("B reading 12s -- INJECT MOTION NOW\n");
        t = readwin(fd, 12);
        s("B TOTAL="); d(t); s(t>0?"  <== decode RAN w/o enable\n":"  <== dead (enable REQUIRED)\n");
        close(fd);
    }

    /* ---- config C: full (known-good baseline) ---- */
    s("=== C: open + push + INIT + ENABLE (baseline) ===\n");
    fd = open("/dev/input/mouse", O_RDWR|O_NDELAY, 0);
    if(fd<0){ s("C open FAILED errno="); d(errno); s("\n"); }
    else {
        r = ioctl(fd, I_PUSH, (void*)"mouse");      s("C I_PUSH="); d(r); s("\n");
        r = ioctl(fd, IDEVINITDEVICE, &zero);        s("C INIT="); d(r); s("\n");
        r = ioctl(fd, IDEVENABLEBUTTONS, bits);      s("C ENBTN="); d(r); s("\n");
        r = ioctl(fd, IDEVENABLEVALUATORS, bits);    s("C ENVAL="); d(r); s("\n");
        s("C reading 12s -- INJECT MOTION NOW\n");
        t = readwin(fd, 12);
        s("C TOTAL="); d(t); s(t>0?"  <== decode RAN (baseline OK)\n":"  <== dead?!\n");
        close(fd);
    }
    s("MATRIX DONE\n");
    return 0;
}
