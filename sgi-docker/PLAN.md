# Docker IRIX Appliance Container

## Context

Create a Docker container that wraps the QEMU SGI Indy emulation so that
`docker exec` commands transparently execute inside the emulated IRIX 6.5
guest. The goal is a seamless UX where the container *appears* to be running
IRIX natively:

```bash
docker exec irix uname -a     # → "IRIX64 IRIS 6.5 ..."
docker exec -it irix bash      # → IRIX login session
docker exec irix hinv          # → SGI hardware inventory
```

This is achieved through a BusyBox-style dispatcher that intercepts all
`docker exec` commands and routes them to IRIX via telnet (interactive) or
rsh (non-interactive). Boot-state awareness provides clear error messages
if the guest isn't ready yet.

---

## File Layout

All new files go in a `docker/` directory. Existing files are not modified
except `tools/install_irix.py` (Phase 5 addition).

```
docker/
  Dockerfile.irix             # Multi-stage, multi-arch
  entrypoint.sh               # QEMU lifecycle + boot monitor + network config
  dispatcher.sh               # BusyBox-style catch-all for docker exec
  healthcheck.sh              # Docker HEALTHCHECK
  docker-compose.irix.yml     # Easy usage
  README.md                   # Usage documentation
```

---

## 1. Dockerfile.irix (multi-stage, multi-arch)

### Stage 1: `builder` — compile QEMU

- Base: `ubuntu:24.04`
- Install build deps: libglib2.0-dev, libpixman-1-dev, libfdt-dev, meson,
  ninja-build, python3 (same as existing Dockerfile lines 6-19)
- `COPY qemu/ /src/qemu/`
- Configure: `--target-list=mips64-softmmu --disable-fuse --disable-fuse-lseek
  --disable-docs --disable-gtk --disable-sdl --disable-opengl --enable-slirp
  --prefix=/opt/qemu`
- Build: `ninja -j$(nproc) && ninja install`
- Multi-arch works automatically via `docker buildx` — each platform compiles
  a native binary

### Stage 2: `runtime` — minimal appliance image

- Base: `ubuntu:24.04`
- Runtime deps only: `libglib2.0-0 libpixman-1-0 libfdt1 socat procps`
- Hidden Linux tools at `/opt/linux/bin/`: `socat`, `busybox` (with symlinks
  for sh, cat, echo, sleep, grep, nc, test, timeout, basename, mktemp, printf,
  kill, wc, dd, rm, date, seq). These are NOT on `$PATH` — the dispatcher
  uses absolute paths so they don't shadow IRIX commands
- `COPY --from=builder /opt/qemu/ /opt/qemu/`
- `COPY PROM_library/bins/cpu/ip24/Indy_ip24prom.070-9101-011.bin /opt/irix/prom.bin`
- `COPY docker/entrypoint.sh docker/dispatcher.sh docker/healthcheck.sh`
- Symlink `bash` and `sh` in `/usr/local/bin/` → dispatcher (catches all
  `docker exec` invocations)
- `VOLUME /data` for disk.qcow2 + nvram.bin
- Optional `IRIX_DISK` build arg to bake in a disk image
- `HEALTHCHECK --start-period=120s` using healthcheck.sh
- `ENTRYPOINT ["/opt/irix/entrypoint.sh"]`

---

## 2. entrypoint.sh — QEMU lifecycle manager (PID 1)

Boot state machine: `starting` → `prom` → `booting` → `configuring` → `ready`
(or `error` at any point). State written to `/var/run/irix/boot_state`.

### Startup sequence:

1. **Validate disk** — check `/data/disk.qcow2` exists, write `error` state
   with helpful message if missing, `sleep infinity` to keep container up
2. **Launch QEMU** in background with:
   - `-M indy -m ${IRIX_RAM:-64}M -bios /opt/irix/prom.bin`
   - `-display none` (headless)
   - `-chardev socket,id=ser0,path=/tmp/serial.sock,server=on,wait=off`
   - `-serial chardev:ser0 -monitor unix:/tmp/monitor.sock,server,nowait`
   - `-drive if=scsi,bus=0,unit=1,file=/data/disk.qcow2,format=qcow2,cache=writethrough,file.locking=off`
   - `-global sgi-hpc3.nvram-file=/data/nvram.bin -global sgi-hpc3.autoload=true`
   - `-icount shift=0,sleep=off` (critical for kernel performance)
   - `-nic user,model=sgi-hpc3,hostfwd=tcp:127.0.0.1:2323-10.0.2.15:23,hostfwd=tcp:127.0.0.1:5140-10.0.2.15:514`
   - `-loadvm $IRIX_SNAPSHOT` if `IRIX_SNAPSHOT` env var set (~5s boot vs ~90s cold)
   - Source: `sgi_prom_mcp/server.py:277` (`_build_qemu_launch`)
3. **Monitor serial output** via socat → Unix socket, scan for boot milestones:
   - `"Running power-on diagnostics"` → state `prom`
   - `"IRIX Release"` → state `booting`
   - `"login:"` → state `configuring`, break out to configure network
   - `"PANIC"` → state `error`
   - Patterns from: `sgi_prom_mcp/boot_milestones.py`
4. **Configure IRIX networking** via serial console (fallback if not baked into
   disk — see section 5):
   - Login as root, send `ifconfig ec0 inet 10.0.2.15 netmask 255.255.255.0 up`,
     `route add default 10.0.2.2`, write `/etc/hosts.equiv`
   - Verify connectivity with TCP probe to telnet port
5. **Write state `ready`** — dispatcher can now route commands
6. **`wait $QEMU_PID`** — keep container alive until QEMU exits

### Environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `IRIX_RAM` | `64` | Guest RAM in MB (minimum 64) |
| `IRIX_SNAPSHOT` | (none) | Snapshot name for fast boot (e.g., `irix65_booted`) |
| `IRIX_BOOT_TIMEOUT` | `300` | Max seconds to wait for login prompt |

---

## 3. dispatcher.sh — BusyBox-style exec interceptor

Installed at `/usr/local/bin/irix-dispatch` with symlinks from
`/usr/local/bin/bash` and `/usr/local/bin/sh`. All internal operations
use `/opt/linux/bin/` absolute paths.

### Invocation detection:

| How called | What happens |
|------------|-------------|
| `sh -c "COMMAND"` | Non-interactive `docker exec irix COMMAND` → rsh to IRIX |
| `bash` (no args) | Interactive `docker exec -it irix bash` → telnet to IRIX |
| `irix-dispatch CMD` | Direct invocation → route based on TTY |
| `$0` is any other name | BusyBox-style: `basename $0` becomes the command (e.g., `/bin/ls` → `ls`) |

### Boot state checks (before every command):

```
ready       → proceed
error       → print error details, exit 1
starting/prom/booting/configuring → print "still booting" + suggest `wait`, exit 2
```

### Pseudo-commands (intercepted, not sent to IRIX):

| Command | Action |
|---------|--------|
| `wait [timeout]` | Block until state=ready, print progress every 5s |
| `status` | Print boot state, QEMU PID, serial log size |
| `logs` | Print boot log |
| `serial` | Raw socat attach to serial console (advanced) |

### Command routing:

- **Interactive** (TTY detected + shell command): `socat -,raw,echo=0 TCP:127.0.0.1:2323`
  (connects to IRIX telnet — proper login, terminal handling, job control)
- **Non-interactive**: Implement rsh wire protocol with printf/socat:
  send `"0\0root\0root\0COMMAND\0"` to TCP port 5140, strip leading
  null byte from response. Exit code propagated.

### Symlink population:

Generate symlinks for ~60 common Unix commands (ls, cat, cp, mv, rm, mkdir,
ps, kill, df, du, uname, hostname, date, who, id, vi, more, less, head,
tail, grep, find, awk, sed, tr, wc, sort, cut, ifconfig, ping, netstat,
mount, sh, bash, csh, ksh, login, hinv, versions, chkconfig, etc.)
all pointing to `/usr/local/bin/irix-dispatch`.

---

## 4. healthcheck.sh

Reads `/var/run/irix/boot_state`:
- `ready` → exit 0 (healthy)
- `error` → exit 1 (unhealthy)
- anything else → exit 1 (still booting)

`HEALTHCHECK --interval=10s --timeout=5s --start-period=120s --retries=3`

---

## 5. IRIX guest preparation — persistent network config

**Modify: `tools/install_irix.py`** (after line ~1160, after xdm fix in
`phase_verify_boot`)

Add commands via serial console to bake networking into the IRIX disk so
it auto-configures on every boot (including snapshot restore):

```python
# 1. Set hostname → IP mapping in /etc/hosts
q.send('echo "10.0.2.15 IRIS" >> /etc/hosts && echo HOSTS_OK\r')

# 2. Persistent interface config (IRIX reads ifconfig-1.options at boot,
#    address comes from hostname lookup in /etc/hosts via /etc/init.d/network)
q.send('echo "netmask 255.255.255.0" > /etc/config/ifconfig-1.options '
       '&& echo IFCFG_OK\r')

# 3. Static default route (sourced by /etc/init.d/network, $ROUTE is preset)
q.send('echo \'$ROUTE $QUIET add default 10.0.2.2\' > /etc/config/static-route.options '
       '&& echo ROUTE_OK\r')

# 4. Enable networking
q.send('chkconfig network on && echo NET_ON\r')

# 5. rsh trust (+ root = allow from any host as root)
q.send('echo "localhost root" > /etc/hosts.equiv '
       '&& echo "+ root" >> /etc/hosts.equiv '
       '&& echo RSH_OK\r')

# 6. Ensure inetd enabled (should be default)
q.send('chkconfig inetd on && echo INETD_OK\r')
```

This uses IRIX's native `/etc/init.d/network` script which reads
`/etc/config/ifconfig-1.options` and `/etc/config/static-route.options`
(verified in IRIX 6.5.5 source: `software_library/irix-655-source/m/eoe/cmd/initpkg/init.d/network`).

The entrypoint.sh still has serial-based fallback networking for disk images
that don't have this configuration pre-applied.

---

## 6. docker-compose.irix.yml

```yaml
services:
  irix:
    build:
      context: ..
      dockerfile: docker/Dockerfile.irix
    container_name: irix
    volumes:
      - ./data:/data              # disk.qcow2 + nvram.bin
    environment:
      - IRIX_SNAPSHOT=irix65_booted   # Fast boot (~5s)
    restart: unless-stopped
```

---

## 7. Usage

```bash
# Build
docker build -f docker/Dockerfile.irix -t irix .

# Multi-arch build
docker buildx build -f docker/Dockerfile.irix \
    --platform linux/amd64,linux/arm64 -t irix .

# Run (volume-mount disk)
docker run -d --name irix \
    -v $(pwd)/irix65_disk.qcow2:/data/disk.qcow2 \
    -v $(pwd)/sgi_indy_nvram.bin:/data/nvram.bin \
    -e IRIX_SNAPSHOT=irix65_booted \
    irix

# Wait for boot
docker exec irix wait

# Use it
docker exec irix uname -a           # IRIX64 IRIS 6.5 ...
docker exec irix hinv               # Hardware inventory
docker exec irix ls /                # IRIX filesystem
docker exec -it irix bash           # Interactive IRIX session
docker exec irix status             # Boot state info
```

---

## Known Limitations

| Issue | Detail |
|-------|--------|
| `docker cp` | Copies to the Linux container filesystem, not IRIX |
| Binary output | rsh is 8-bit clean in principle but fragile for binary data |
| Signals | Ctrl-C kills the local socat/telnet, not the remote IRIX process cleanly |
| Unknown commands | If a command has no symlink, Docker returns "not found" — mitigated by large symlink set |
| Boot time | Cold boot ~90-120s; use `IRIX_SNAPSHOT` for ~5s |

---

## Files to Create

| File | Purpose |
|------|---------|
| `docker/Dockerfile.irix` | Multi-stage build |
| `docker/entrypoint.sh` | QEMU lifecycle + boot monitor |
| `docker/dispatcher.sh` | exec interceptor |
| `docker/healthcheck.sh` | Boot state check |
| `docker/docker-compose.irix.yml` | Compose file |
| `docker/README.md` | Usage docs |

## Files to Modify

| File | Change |
|------|--------|
| `tools/install_irix.py` | Add networking/rsh config to Phase 5 (~line 1160) |

## Files to Reference (read-only)

| File | Why |
|------|-----|
| `sgi_prom_mcp/server.py:277` | `_build_qemu_launch()` — QEMU command line |
| `sgi_prom_mcp/boot_milestones.py` | Boot milestone patterns |
| `tools/boot_harness.py` | Serial console interaction patterns |
| `Dockerfile` | Build dependencies reference |
| `software_library/irix-655-source/m/eoe/cmd/initpkg/init.d/network` | IRIX network init |
| `software_library/irix-655-source/m/eoe/cmd/initpkg/init.d/static-route.options` | Route config format |

---

## Verification

1. **Build the image**: `docker build -f docker/Dockerfile.irix -t irix .`
2. **Prepare disk**: Copy an existing `irix65_disk.qcow2` with `irix65_booted`
   snapshot (or run `harness_install` to create one with networking pre-configured)
3. **Start container**: `docker run -d --name irix -v ...:/data -e IRIX_SNAPSHOT=irix65_booted irix`
4. **Test wait**: `docker exec irix wait` — should block then print "IRIX is ready."
5. **Test non-interactive**: `docker exec irix uname -a` — should return IRIX version
6. **Test interactive**: `docker exec -it irix bash` — should open IRIX login session
7. **Test boot awareness**: Start without snapshot, immediately `docker exec irix ls`
   — should print "IRIX is still booting" with current state
8. **Test missing disk**: Start without mounting disk — should print clear error
9. **Test concurrent**: Run multiple `docker exec irix date` in parallel — all should work
10. **Test healthcheck**: `docker inspect --format='{{.State.Health.Status}}' irix`
