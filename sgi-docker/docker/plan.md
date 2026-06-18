# Configurability Plan

## Changes

### entrypoint.sh
- Add IRIX_VNC, IRIX_VNC_PORT, IRIX_MACHINE, IRIX_EXTRA_ARGS env vars
- When IRIX_VNC=1, add `-vnc :N,to=99` to QEMU command
- Use IRIX_MACHINE in -M flag
- Append IRIX_EXTRA_ARGS to QEMU command

### Dockerfile.irix
- Add ENV declarations for all config vars
- EXPOSE 5900

### docker-compose.irix.yml
- Add VNC port mapping and env var examples
