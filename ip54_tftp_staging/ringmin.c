/* ringmin.c - minimal downstream proof, simplified to survive the flaky MIPSpro be backend
 * (no decode loop / modulo / multiply — just read the ring counters at the end).
 * Replicates X's path: open /dev/shmiq, QIOCATTACH a ring, open /dev/input/mouse with
 * I_PUSH("mouse")+IDEVINIT+ENABLE, I_LINK under shmiq, poll. If ring tail advances from 0 while
 * mouse motion is injected -> idev events ARE reaching the shmiq ring => downstream works (X reads
 * the same ring) => the only gap is that X doesn't push the module. Run with xdm stopped. */
extern int open(const char *, int, int);
extern int ioctl(int, int, void *);
extern int write(int, const void *, int);
extern unsigned int sleep(unsigned int);
extern char *valloc(unsigned int);
extern int errno;

#define O_RDWR              2
#define I_PUSH              0x5302
#define I_LINK              0x530c
#define QIOCATTACH          0x80085101
#define IDEVINITDEVICE      0x80046933
#define IDEVENABLEBUTTONS   0x8040690a
#define IDEVENABLEVALUATORS 0x8040690b
#define NEV 128

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
    int sfd, mfd, r, i;
    int req[2];
    int *ring;
    unsigned char bits[64];
    for(i=0;i<64;i++) bits[i]=0xff;
    ring = (int *)valloc(NEV*12 + 64);
    ring[0]=0; ring[1]=0; ring[2]=0;
    s("RMIN START\n");
    sfd = open("/dev/shmiq", O_RDWR, 0);
    if(sfd<0){ s("shmiq open FAIL errno="); d(errno); s("\n"); return 1; }
    req[0] = (int)ring; req[1] = NEV;
    r = ioctl(sfd, QIOCATTACH, req);  s("ATTACH="); d(r); if(r<0){s(" e=");d(errno);} s("\n");
    mfd = open("/dev/input/mouse", O_RDWR, 0);
    if(mfd<0){ s("mouse open FAIL errno="); d(errno); s("\n"); return 1; }
    r = ioctl(mfd, I_PUSH, (void*)"mouse");      s("PUSH="); d(r); s("\n");
    r = ioctl(mfd, IDEVINITDEVICE, bits);         s("INIT="); d(r); s("\n");
    r = ioctl(mfd, IDEVENABLEBUTTONS, bits);      s("ENB=");  d(r); s("\n");
    r = ioctl(mfd, IDEVENABLEVALUATORS, bits);    s("ENV=");  d(r); s("\n");
    r = ioctl(sfd, I_LINK, (void*)mfd);           s("LINK="); d(r); if(r<0){s(" e=");d(errno);} s("\n");
    s("POLL 25s (inject motion now)\n");
    for(i=0;i<25;i++) sleep(1);
    s("RESULT head="); d(ring[0]); s(" tail="); d(ring[1]); s(" flags="); d(ring[2]); s("\n");
    s("RMIN DONE\n");
    return 0;
}
