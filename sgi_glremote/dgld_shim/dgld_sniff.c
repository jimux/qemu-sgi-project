/*
 * dgld_sniff.c (v2) — transparent DGL handshake sniffer.
 *
 * Installed as /usr/etc/dgld (inetd spawns it with the libgl socket on fd 0/1/2). It spawns the
 * REAL dgld (/usr/etc/dgld.orig) on a real loopback TCP connection and proxies every byte between
 * libgl and the real dgld, logging BOTH directions (hex) to /tmp/dgld_sniff.log. This reveals the
 * exact connection handshake — in particular what dgld replies to libgl's first word 0x1234.
 *
 * v2 fixes vs v1 (which crashed gltri):
 *   - real AF_INET loopback socket to dgld (not an AF_UNIX socketpair) so dgld's TCP socket calls
 *     (getpeername / setsockopt) behave;
 *   - the child redirects fd 2 (stderr) to /dev/null — v1 left it pointing at libgl's socket, so
 *     dgld's stderr corrupted the DGL stream and faulted gltri.
 *
 * Build on irix-devel:  cc -n32 -O -o dgld_sniff dgld_sniff.c -lc
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <sys/time.h>

static FILE *g_log;

static void dump(const char *dir, const unsigned char *b, int n)
{
    int i;
    fprintf(g_log, "%s %d:", dir, n);
    for (i = 0; i < n && i < 128; i++) {
        fprintf(g_log, " %02x", b[i]);
    }
    fprintf(g_log, "\n");
    fflush(g_log);
}

int main(int argc, char **argv)
{
    int lsock, dgld_fd, devnull, port;
    struct sockaddr_in a;
    int alen;
    pid_t pid;
    unsigned char buf[8192];
    fd_set rfds;
    int maxfd, n;

    g_log = fopen("/tmp/dgld_sniff.log", "a");
    fprintf(g_log, "\n=== dgld_sniff v2 start pid=%d ===\n", (int)getpid());
    fflush(g_log);

    /* listening socket on 127.0.0.1:<ephemeral> for the real dgld to connect back to */
    lsock = socket(AF_INET, SOCK_STREAM, 0);
    memset(&a, 0, sizeof(a));
    a.sin_family = AF_INET;
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    a.sin_port = 0;
    if (bind(lsock, (struct sockaddr *)&a, sizeof(a)) < 0 || listen(lsock, 1) < 0) {
        fprintf(g_log, "bind/listen failed\n"); fflush(g_log); return 1;
    }
    alen = sizeof(a);
    getsockname(lsock, (struct sockaddr *)&a, &alen);
    port = ntohs(a.sin_port);
    fprintf(g_log, "dgld back-connect port=%d\n", port); fflush(g_log);

    pid = fork();
    if (pid == 0) {
        /* child = real dgld; its socket is a fresh loopback TCP connection back to lsock */
        int cs = socket(AF_INET, SOCK_STREAM, 0);
        struct sockaddr_in ca;
        memset(&ca, 0, sizeof(ca));
        ca.sin_family = AF_INET;
        ca.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        ca.sin_port = htons(port);
        if (connect(cs, (struct sockaddr *)&ca, sizeof(ca)) < 0) {
            _exit(126);
        }
        devnull = open("/dev/null", O_WRONLY);
        dup2(cs, 0);
        dup2(cs, 1);
        if (devnull >= 0) {
            dup2(devnull, 2);        /* keep dgld's stderr OFF the proxied stream */
        }
        close(cs);
        close(lsock);
        execl("/usr/etc/dgld.orig", "dgld", "-IM", "-tDGLTSOCKET", (char *)0);
        _exit(127);
    }

    dgld_fd = accept(lsock, (struct sockaddr *)0, (int *)0);
    close(lsock);
    if (dgld_fd < 0) {
        fprintf(g_log, "accept failed\n"); fflush(g_log); return 1;
    }
    fprintf(g_log, "dgld connected (fd=%d)\n", dgld_fd); fflush(g_log);

    maxfd = (dgld_fd > 0 ? dgld_fd : 0) + 1;
    for (;;) {
        FD_ZERO(&rfds);
        FD_SET(0, &rfds);
        FD_SET(dgld_fd, &rfds);
        if (select(maxfd, &rfds, (fd_set *)0, (fd_set *)0, (struct timeval *)0) < 0) {
            break;
        }
        if (FD_ISSET(0, &rfds)) {            /* libgl -> dgld */
            n = read(0, buf, sizeof buf);
            if (n <= 0) {
                break;
            }
            write(dgld_fd, buf, n);
            dump("C->S", buf, n);
        }
        if (FD_ISSET(dgld_fd, &rfds)) {      /* dgld -> libgl */
            n = read(dgld_fd, buf, sizeof buf);
            if (n <= 0) {
                break;
            }
            write(1, buf, n);
            dump("S->C", buf, n);
        }
    }
    fprintf(g_log, "=== sniff end ===\n");
    fclose(g_log);
    return 0;
}
