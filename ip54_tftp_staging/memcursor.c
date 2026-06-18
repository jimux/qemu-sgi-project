/* memcursor.c - verify the DCB->VC2 cursor-write sequence from userland by
 * mapping the pvrex3 REX3 registers (PA 0x1F490000) via /dev/mem and issuing:
 *   DCBMODE(0x238)=0x3            (slave=VC2(0), reg=0, dw=3 combined)
 *   DCBDATA0(0x240)=(2<<24)|(x<<8)   -> vc2_reg[2]=CURSOR_X
 *   DCBDATA0(0x240)=(3<<24)|(y<<8)   -> vc2_reg[3]=CURSOR_Y
 * Host then checks VC2 CURSOR_X/Y. If they move, the sequence is correct and the
 * kernel pvfb_gf_PositionCursor fix is just this sequence. */

extern int open(const char *, int, int);
extern int write(int, const void *, int);
extern int close(int);
extern char *mmap(void *, int, int, int, int, int);
extern int errno;

#define O_RDWR 2
#define PROT_READ  1
#define PROT_WRITE 2
#define MAP_SHARED 1
#define REX3_PA    0x1f490000
#define REX3_SIZE  0x2000

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
    int fd, x, y;
    char *map;
    volatile unsigned int *rex3;
    x = 600; y = 500;
    s("MEMCURSOR START\n");
    fd = open("/dev/mem", O_RDWR, 0);
    if(fd < 0){ s("open /dev/mem FAIL errno="); d(errno); s("\n"); return 1; }
    map = mmap((void*)0, REX3_SIZE, PROT_READ|PROT_WRITE, MAP_SHARED, fd, REX3_PA);
    if(map == (char*)-1 || map == (char*)0){ s("mmap FAIL errno="); d(errno); s("\n"); return 1; }
    s("mmap ok\n");
    rex3 = (volatile unsigned int *)map;
    rex3[0x238/4] = 0x00000003;                       /* DCBMODE */
    rex3[0x240/4] = (2 << 24) | ((x & 0xffff) << 8);  /* CURSOR_X = 600 */
    rex3[0x240/4] = (3 << 24) | ((y & 0xffff) << 8);  /* CURSOR_Y = 500 */
    s("wrote VC2 CURSOR_X=600 CURSOR_Y=500\n");
    s("MEMCURSOR DONE (check VC2)\n");
    close(fd);
    return 0;
}
