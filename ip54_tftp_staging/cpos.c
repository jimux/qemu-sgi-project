/* cpos.c - test the cursor OUTPUT path directly: I_STR(QIOCSETCPOS{x,y}) on
 * /dev/shmiq (exactly what Xsgi's simpleSetPointer does). Run with X up so the
 * screen/cursor is designated. Host then checks pvrex3 VC2 CURSOR_X/Y.
 * If VC2 moves -> kernel shmiq->VC2 output works (bug is input delivery).
 * If not -> the QIOCSETCPOS->VC2 path is broken on IP54 (fix the output). */

extern int open(const char *, int, int);
extern int ioctl(int, int, void *);
extern int close(int);
extern int write(int, const void *, int);
extern int errno;

#define O_RDWR 2
#define I_STR        0x5308
#define QIOCSETCPOS  0xc004510a
#define QIOCSETSCRN  0x80045106   /* _IOW('Q',6,int) set current screen */

struct strioctl { int ic_cmd; int ic_timout; int ic_len; void *ic_dp; };
struct shmiqsetcpos { short x; short y; };

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

int main(int argc, char **argv){
    int fd, r, scrn;
    struct strioctl st;
    struct shmiqsetcpos p;
    int xx, yy;
    xx = 600; yy = 500;
    s("CPOS START\n");
    fd = open("/dev/shmiq", O_RDWR, 0);
    if(fd < 0){ s("open /dev/shmiq FAILED errno="); d(errno); s("\n"); return 1; }
    s("open /dev/shmiq ok fd="); d(fd); s("\n");
    /* designate screen 0 (best effort) */
    scrn = 0;
    r = ioctl(fd, QIOCSETSCRN, &scrn);
    s("QIOCSETSCRN(0) = "); d(r); if(r<0){ s(" errno="); d(errno); } s("\n");
    /* the cursor-set */
    p.x = (short)xx; p.y = (short)yy;
    st.ic_cmd = QIOCSETCPOS; st.ic_timout = 0; st.ic_len = 4; st.ic_dp = &p;
    r = ioctl(fd, I_STR, &st);
    s("I_STR(QIOCSETCPOS{600,500}) = "); d(r); if(r<0){ s(" errno="); d(errno); } s("\n");
    s("CPOS DONE (check VC2 CURSOR_X/Y)\n");
    close(fd);
    return 0;
}
