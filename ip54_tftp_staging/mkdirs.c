/* mkdirs.c - create needed include subdirectories */
#include <sys/stat.h>
int main(void) {
    mkdir("/tmp/kerninc", 0755);
    mkdir("/tmp/kerninc/sys", 0755);
    mkdir("/tmp/kerninc/sys/arcs", 0755);
    mkdir("/tmp/kerninc/sys/RACER", 0755);
    return 0;
}
