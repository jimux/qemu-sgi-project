# IRIX Appliance Container

Run IRIX 6.5 transparently inside a Docker container. `docker exec` commands
execute inside the emulated SGI Indy (QEMU), making the container appear to
run IRIX natively.

```bash
docker exec irix uname -a       # → "IRIX64 IRIS 6.5 ..."
docker exec irix hinv           # → SGI hardware inventory
docker exec irix ls /            # → IRIX filesystem
docker exec -it irix bash       # → Interactive IRIX login session
```

## Quick Start

### 1. Build the image

From the project root:

```bash
docker build -f sgi-docker/docker/Dockerfile.irix -t irix .
```

### 2. Prepare the data volume

You need an IRIX disk image (qcow2 format) with a booted snapshot:

```bash
mkdir -p sgi-docker/docker/data
cp irix_disk.qcow2 sgi-docker/docker/data/disk.qcow2
cp sgi_indy_nvram.bin sgi-docker/docker/data/nvram.bin  # optional
```

To create a fresh disk from scratch, use the installer:
```python
harness_install(version="6.5", disk="/workspace/irix_disk.qcow2")
```

### 3. Run the container

```bash
# With snapshot (fast boot ~5s)
docker run -d --name irix \
    -v $(pwd)/sgi-docker/docker/data:/data \
    -e IRIX_SNAPSHOT=irix65_booted \
    irix

# Cold boot (no snapshot, ~90-120s)
docker run -d --name irix \
    -v $(pwd)/sgi-docker/docker/data:/data \
    irix
```

### 4. Wait for boot and use

```bash
docker exec irix wait           # Block until IRIX is ready
docker exec irix uname -a       # Run commands
docker exec -it irix bash       # Interactive session
```

## Using Docker Compose

```bash
cd sgi-docker/docker
docker compose -f docker-compose.irix.yml up -d
docker exec irix wait
docker exec irix hinv
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `IRIX_RAM` | `64` | Guest RAM in MB (minimum 64) |
| `IRIX_SNAPSHOT` | *(none)* | VM snapshot name for fast boot (e.g., `irix65_booted`) |
| `IRIX_BOOT_TIMEOUT` | `300` | Max seconds to wait for login prompt |

## Built-in Commands

These commands are intercepted locally (not sent to IRIX):

| Command | Description |
|---------|-------------|
| `wait [timeout]` | Block until IRIX is ready, print progress |
| `status` | Show boot state, QEMU PID, log info |
| `logs` | Print the boot log |
| `serial` | Attach to raw serial console (advanced) |

## How It Works

The container uses a BusyBox-style dispatcher that intercepts all `docker exec`
invocations:

1. **Interactive** (`docker exec -it irix bash`): Opens a telnet session to
   the IRIX guest with full terminal support.

2. **Non-interactive** (`docker exec irix uname -a`): Sends the command via
   rsh (remote shell) protocol to IRIX and returns the output.

3. **Boot awareness**: Commands return informative errors if IRIX hasn't
   finished booting yet, with suggestions to use `wait`.

The QEMU SGI Indy emulator runs as PID 1 inside the container, with SLIRP
networking providing telnet (port 23) and rsh (port 514) connectivity between
the Linux host and the IRIX guest.

## Multi-Architecture

The image builds natively on both amd64 and arm64:

```bash
docker buildx build -f sgi-docker/docker/Dockerfile.irix \
    --platform linux/amd64,linux/arm64 -t irix .
```

QEMU compiles from source in the builder stage, so each platform gets a native
binary.

## Limitations

| Issue | Detail |
|-------|--------|
| `docker cp` | Copies to the Linux container filesystem, not IRIX |
| Binary output | rsh is 8-bit clean in principle but fragile for large binary data |
| Signals | Ctrl-C kills the local connection, not the IRIX process |
| Unknown commands | Commands without a symlink return "not found" — mitigated by a large symlink set |
| Boot time | Cold boot ~90-120s; use `IRIX_SNAPSHOT` for ~5s |

## Troubleshooting

**Container starts but commands return "still booting":**
Run `docker exec irix wait` to block until ready, or check `docker exec irix status`
for the current boot state.

**Container shows "error" state:**
Run `docker exec irix logs` to see the boot log. Common causes:
- Missing `/data/disk.qcow2` (mount the data volume)
- Corrupt disk image (re-run `harness_install`)
- Wrong snapshot name (check with `qemu-img snapshot -l disk.qcow2`)

**Interactive session closes immediately:**
Ensure you're using `-it` flag: `docker exec -it irix bash`

**Commands hang or timeout:**
IRIX networking may not be configured. If the disk wasn't prepared with
`install_irix.py`, the entrypoint falls back to serial-based network
configuration, which is less reliable. Re-install with the latest
`install_irix.py` to bake in persistent networking.
