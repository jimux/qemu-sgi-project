/* ringtest.c (v2) - does the shmiq RING receive mouse events?
 * Flat int[] ring buffer (avoids MIPSpro be-backend struct-layout crash).
 * Ring layout (big-endian, matches struct sharedMemoryInputQueue):
 *   int[0]=head  int[1]=tail  int[2]=flags  then events, each 3 ints:
 *     ev[0]=time/id  ev[1]=(device<<24|which<<16|type<<8|flags)  ev[2]=un(value)
 * QIOCATTACH a ring, push+init+enable the mouse, I_LINK it under shmiq, poll. */

extern int open(const char *, int, int);
extern int ioctl(int, int, void *);
extern int close(int);
extern int write(int, const void *, int);
extern unsigned int sleep(unsigned int);
extern char *valloc(unsigned int);
extern int errno;

#define O_RDWR   2
#define I_PUSH              0x5302
#define I_LINK              0x530c
#define QIOCATTACH          0x80085101
#define IDEVINITDEVICE      0x80046933
#define IDEVENABLEBUTTONS   0x8040690a
#define IDEVENABLEVALUATORS 0x8040690b

#define NEV  128

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

int main(){
    int sfd, mfd, r, i, drained, head;
    int req[2];
    int *ring;
    unsigned char bits[64];
    for(i=0;i<64;i++) bits[i]=0xff;
    /* valloc returns page-aligned memory (required by QIOCATTACH) */
    ring = (int *)valloc(NEV*12 + 64);
    ring[0]=0; ring[1]=0; ring[2]=0;
    s("RINGTEST_NOEN START (X-exact: no enable ioctls)\n");
    sfd = open("/dev/shmiq", O_RDWR, 0);
    if(sfd<0){ s("open shmiq FAIL errno="); d(errno); s("\n"); return 1; }
    req[0] = (int)ring;      /* user_vaddr */
    req[1] = NEV;            /* arg = #events */
    r = ioctl(sfd, QIOCATTACH, req);
    s("QIOCATTACH = "); d(r); if(r<0){s(" errno=");d(errno);} s("\n");
    mfd = open("/dev/input/mouse", O_RDWR, 0);
    if(mfd<0){ s("open mouse FAIL errno="); d(errno); s("\n"); return 1; }
    r = ioctl(mfd, I_PUSH, (void*)"mouse");      s("I_PUSH=");  d(r); s("\n");
    r = ioctl(mfd, IDEVINITDEVICE, bits);         s("INIT=");    d(r); s("\n");
    r = ioctl(sfd, I_LINK, (void*)mfd);           s("I_LINK=");  d(r); if(r<0){s(" errno=");d(errno);} s("\n");
    s("POLLING RING 25s (inject motion now)...\n");
    drained = 0; head = 0;
    for(i=0;i<25;i++){
        while(head != ring[1]){          /* head != tail */
            int base = 3 + (head % NEV) * 3;
            int w1 = ring[base+1];
            int un = ring[base+2];
            s("EV type="); d((w1>>8)&0xff);
            s(" flags="); d(w1&0xff);
            s(" dev="); d((w1>>24)&0xff);
            s(" un="); d(un); s("\n");
            head++; ring[0]=head;
            drained++;
            if(drained>=40) break;
        }
        if(drained>=40) break;
        sleep(1);
    }
    s("TOTAL drained="); d(drained); s(" tail="); d(ring[1]); s(" flags="); d(ring[2]); s("\n");
    s("RINGTEST DONE\n");
    close(mfd); close(sfd);
    return 0;
}
