/* mouseprobe.c - confirm WHY Xsgi's "mouse" input device fails.
 * Replicates sgiCheckDevices' probe: open /dev/input/<dev>, I_PUSH(<dev>),
 * then query the idev device descriptor. Compare keyboard (works) vs mouse.
 * Self-contained: no stdio; inline ioctl constants; libc only for syscalls.
 * Run with X (xdm) STOPPED so the devices are free (pckm disallows 2nd open). */

extern int open(const char *, int, int);
extern int ioctl(int, int, void *);
extern int close(int);
extern int write(int, const void *, int);
extern int errno;

#define O_RDWR        2
#define I_PUSH        0x5302
#define I_POP         0x5303
/* IDEVGETDEVICEDESC = _IOWR('i',0, idevDesc[44]) */
#define IDEVGETDEVICEDESC  0xc02c6900

typedef struct {
    char  devName[16];
    char  devType[16];
    unsigned short nButtons, nValuators, nLEDs, nStrDpys, nIntDpys;
    unsigned char  nBells, flags;
} idevDesc;

static void s(const char *p){ int n=0; while(p[n]) n++; write(1,p,n); }
static void d(int v){
    char o[20];
    char t[16];
    int oi, neg, ti;
    oi = 0; neg = 0; ti = 0;
    if(v<0){ neg=1; v=-v; }
    if(v==0){ o[oi++]='0'; }
    else {
        while(v){ t[ti++]='0'+(v%10); v/=10; }
        while(ti) o[oi++]=t[--ti];
    }
    if(neg) write(1,"-",1);
    write(1,o,oi);
}

static void probe(const char *path, const char *mod){
    int fd, r;
    idevDesc desc;
    s("== "); s(path); s(" ==\n");
    fd = open(path, O_RDWR, 0);
    if(fd < 0){ s("  open FAILED errno="); d(errno); s("\n"); return; }
    s("  open ok fd="); d(fd); s("\n");
    r = ioctl(fd, I_PUSH, (void*)mod);
    s("  I_PUSH(\""); s(mod); s("\") = "); d(r);
    if(r < 0){ s("  errno="); d(errno); }
    s("\n");
    r = ioctl(fd, IDEVGETDEVICEDESC, &desc);
    s("  IDEVGETDEVICEDESC = "); d(r);
    if(r == 0){
        s("  devType='"); s(desc.devType); s("' nButtons="); d(desc.nButtons);
        s(" nValuators="); d(desc.nValuators); s(" flags="); d(desc.flags);
    } else { s("  errno="); d(errno); }
    s("\n");
    close(fd);
}

int main(){
    s("MOUSEPROBE START\n");
    probe("/dev/input/keyboard", "keyboard");
    probe("/dev/input/mouse", "mouse");
    s("MOUSEPROBE DONE\n");
    return 0;
}
