/* mouseread.c - does the kernel produce mouse EVENTS on motion?
 * Open /dev/input/mouse, push "mouse", IDEVINITDEVICE, enable buttons+valuators,
 * then non-blocking read loop. Host injects motion meanwhile. If read() returns
 * data -> kernel path (8042->pckm->mouse/idev) works -> bug is Xsgi/shmiq.
 * Run with xdm STOPPED. */

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
static void hx(unsigned char *b, int n){
    static char H[16] = "0123456789abcdef";
    char o[3]; int i;
    o[2]=' ';
    for(i=0;i<n;i++){ o[0]=H[(b[i]>>4)&15]; o[1]=H[b[i]&15]; write(1,o,3); }
}

int main(){
    int fd, r, i, zero;
    unsigned char bits[64];
    unsigned char buf[512];
    int total;
    zero=0; total=0;
    for(i=0;i<64;i++) bits[i]=0xff;
    s("READER START\n");
    fd = open("/dev/input/mouse", O_RDWR|O_NDELAY, 0);
    if(fd<0){ s("open FAILED errno="); d(errno); s("\n"); return 1; }
    s("open ok fd="); d(fd); s("\n");
    r = ioctl(fd, I_PUSH, (void*)"mouse");        s("I_PUSH=");  d(r); s("\n");
    r = ioctl(fd, IDEVINITDEVICE, &zero);          s("INIT=");    d(r); if(r<0){s(" errno=");d(errno);} s("\n");
    r = ioctl(fd, IDEVENABLEBUTTONS, bits);        s("ENBTN=");   d(r); if(r<0){s(" errno=");d(errno);} s("\n");
    r = ioctl(fd, IDEVENABLEVALUATORS, bits);      s("ENVAL=");   d(r); if(r<0){s(" errno=");d(errno);} s("\n");
    s("READING 30s (inject motion now)...\n");
    for(i=0;i<30;i++){
        r = read(fd, buf, sizeof(buf));
        if(r>0){ total+=r; s("READ "); d(r); s("b: "); hx(buf, r<48?r:48); s("\n"); }
        sleep(1);
    }
    s("TOTAL bytes read="); d(total); s("\n");
    s("READER DONE\n");
    close(fd);
    return 0;
}
