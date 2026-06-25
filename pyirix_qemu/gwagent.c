/* gwagent.c - host<->guest gateway agent for IRIX (portable: Indy, IP54, ...).
 *
 * Driven entirely over the QEMU gdbstub. The host reads/writes the shared page
 * `gw` (kept TLB-resident by the spin loop so the gdbstub can always translate
 * its user VA) and uses the mailbox to run commands and transfer files. No
 * serial, no TFTP, no QEMU device, no kernel patch -> runs on any SGI machine
 * that boots this IRIX userland.
 *
 * Protocol: the host writes the command block + sets gw.cmd; the agent does the
 * work and writes gw.status (1 ok, <0 error), then clears gw.cmd to 0. gw.magic
 * ('GWAY') lets the host confirm the agent's address space is the current CPU
 * context before trusting a read (in multi-user another process may be current
 * at the gdb stop; the host retries until magic reads back correctly).
 *
 * Transfers chunk through gw.data (<= one page) so we never depend on more than
 * one TLB entry: OPEN_W, repeated WRITE, CLOSE (push); OPEN_R, repeated READ,
 * CLOSE (pull). RUN popen()s a command and returns its stdout in gw.data.
 *
 * Build on IRIX:  cc -O -o gwagent gwagent.c        (n32 or o32; static is fine)
 * Host needs &gw_region (from `nm`/ELF) -> it rounds up to the page boundary.
 */

#define GW_MAGIC 0x47574159u   /* 'GWAY' */
/* DATA_SZ is bounded by the MIPS software TLB. The gdbstub reads the agent's
 * user VA ONLY via TLB entries currently resident (no page-table walk), so the
 * spin loop must keep EVERY page of the struct resident (see the touch loop).
 * This works because the agent is a busy-loop that never yields: its whole
 * working set (struct pages + code/stack) stays TLB-resident with no eviction,
 * as long as it fits the 48-entry R4000/R4600 TLB. 64 KB (16 pages) is safe;
 * 128 KB+ risks eviction -> flaky reads; 5 MB is impossible. Must equal
 * Gateway.DATA_SZ in host_channel.py. */
#define DATA_SZ  65536
#define PAGE     4096

/* command codes */
#define C_PING   1
#define C_RUN    2
#define C_OPEN_W 3
#define C_WRITE  4
#define C_CLOSE  5
#define C_OPEN_R 6
#define C_READ   7

#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>

struct gw {
    volatile unsigned int  magic;   /* agent sets GW_MAGIC */
    volatile unsigned int  seq;     /* heartbeat, ++ every spin */
    volatile unsigned int  cmd;     /* host -> agent (0 = idle) */
    volatile unsigned int  arg;     /* length in/out */
    volatile int           status;  /* agent -> host: 0 busy, 1 ok, <0 error */
    volatile unsigned int  rsv;
    char path[256];
    char data[DATA_SZ];
};

/* BSS for the struct; it starts at the first page boundary inside this region.
 * The spin loop keeps ALL of its pages resident + TLB-mapped (it now spans
 * ceil((280 + DATA_SZ)/PAGE) pages, not one). */
static char gw_region[DATA_SZ + 2 * PAGE];

int main(int argc, char **argv)
{
    struct gw *g = (struct gw *)
        (((unsigned long)gw_region + PAGE - 1) & ~(unsigned long)(PAGE - 1));
    int fd = -1;
    volatile char touch;

    g->cmd = 0; g->status = 0; g->seq = 0; g->arg = 0;
    g->magic = GW_MAGIC;

    /* Publish the runtime address of the shared page so the host knows where to
     * drive the mailbox (more reliable than ELF symbols: reflects actual load). */
    {
        FILE *af = fopen("/tmp/gwaddr", "w");
        if (af) { fprintf(af, "0x%lx\n", (unsigned long)g); fclose(af); }
    }

    for (;;) {
        unsigned int c;
        unsigned int o;
        g->seq++;               /* heartbeat */
        /* Keep EVERY page of the struct resident/in-TLB so the gdbstub can read
         * the whole (multi-page) data buffer, not just its first page. popen()
         * in C_RUN context-switches and can evict these, so re-touch each spin. */
        for (o = 0; o < sizeof(struct gw); o += PAGE)
            touch = ((volatile char *)g)[o];
        c = g->cmd;
        if (c == 0) continue;

        g->status = 0;          /* busy */
        if (c == C_PING) {
            g->arg = g->seq;
            g->status = 1;
        } else if (c == C_RUN) {
            FILE *p;
            int n = 0;
            g->data[g->arg < DATA_SZ ? g->arg : DATA_SZ - 1] = 0;
            p = popen(g->data, "r");
            if (p) {
                n = fread(g->data, 1, DATA_SZ - 1, p);
                pclose(p);
                if (n < 0) n = 0;
                g->data[n] = 0;
                g->arg = (unsigned int)n;
                g->status = 1;
            } else {
                g->status = -1;
            }
        } else if (c == C_OPEN_W) {
            if (fd >= 0) close(fd);
            fd = open(g->path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
            g->status = (fd >= 0) ? 1 : -1;
        } else if (c == C_WRITE) {
            int n = (fd >= 0) ? (int)write(fd, (void *)g->data, g->arg) : -1;
            g->status = (n == (int)g->arg) ? 1 : -1;
        } else if (c == C_OPEN_R) {
            if (fd >= 0) close(fd);
            fd = open(g->path, O_RDONLY);
            g->status = (fd >= 0) ? 1 : -1;
        } else if (c == C_READ) {
            int n = (fd >= 0) ? (int)read(fd, (void *)g->data, DATA_SZ) : -1;
            g->arg = (n > 0) ? (unsigned int)n : 0;
            g->status = (n >= 0) ? 1 : -1;
        } else if (c == C_CLOSE) {
            if (fd >= 0) { close(fd); fd = -1; }
            g->status = 1;
        } else {
            g->status = -1;
        }
        g->cmd = 0;             /* done */
    }
    return 0;
}
