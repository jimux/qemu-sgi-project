FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# ── System packages: QEMU build deps + cross-compiler deps + Node.js repo ──
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash bc bison bzip2 ca-certificates ccache curl findutils flex tini \
        gcc g++ git libc6-dev libfdt-dev libffi-dev libglib2.0-dev \
        libpixman-1-dev libslirp-dev libcap-ng-dev libseccomp-dev libgtk-3-dev libsdl2-dev locales make meson ninja-build pkgconf \
        python3 python3-venv python3-pip sed tar sudo \
        xz-utils texinfo libgmp-dev libmpfr-dev libmpc-dev zlib1g-dev \
        gnupg gdb gdb-multiarch \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── Build MIPS bare-metal cross-compiler (binutils 2.43 + GCC 14.2.0) ──
# Installed to /opt so it's available to all users
ENV CROSS_PREFIX=/opt/cross/mips-elf
ENV PATH="${CROSS_PREFIX}/bin:${PATH}"

RUN set -eux; \
    BUILDDIR=/tmp/mips-toolchain-build; \
    BINUTILS_VER=2.43; \
    GCC_VER=14.2.0; \
    NJOBS=$(nproc); \
    mkdir -p "$BUILDDIR" "$CROSS_PREFIX"; \
    \
    # Download sources \
    curl -L -o "$BUILDDIR/binutils-${BINUTILS_VER}.tar.xz" \
        "https://ftp.gnu.org/gnu/binutils/binutils-${BINUTILS_VER}.tar.xz"; \
    curl -L -o "$BUILDDIR/gcc-${GCC_VER}.tar.xz" \
        "https://ftp.gnu.org/gnu/gcc/gcc-${GCC_VER}/gcc-${GCC_VER}.tar.xz"; \
    \
    # Extract \
    tar xf "$BUILDDIR/binutils-${BINUTILS_VER}.tar.xz" -C "$BUILDDIR"; \
    tar xf "$BUILDDIR/gcc-${GCC_VER}.tar.xz" -C "$BUILDDIR"; \
    \
    # Build binutils \
    mkdir -p "$BUILDDIR/build-binutils"; \
    cd "$BUILDDIR/build-binutils"; \
    MAKEINFO=true "$BUILDDIR/binutils-${BINUTILS_VER}/configure" \
        --target=mips-elf \
        --prefix="$CROSS_PREFIX" \
        --disable-nls \
        --disable-werror \
        --with-system-zlib; \
    make -j"$NJOBS" MAKEINFO=true; \
    make install MAKEINFO=true; \
    \
    # Build GCC (C only, freestanding) \
    mkdir -p "$BUILDDIR/build-gcc"; \
    cd "$BUILDDIR/build-gcc"; \
    MAKEINFO=true "$BUILDDIR/gcc-${GCC_VER}/configure" \
        --target=mips-elf \
        --prefix="$CROSS_PREFIX" \
        --enable-languages=c \
        --without-headers \
        --with-newlib \
        --disable-shared \
        --disable-threads \
        --disable-libssp \
        --disable-libgomp \
        --disable-libquadmath \
        --disable-libatomic \
        --disable-nls \
        --disable-multilib \
        --with-arch=mips3 \
        --with-abi=32 \
        --with-float=soft \
        --with-system-zlib; \
    make -j"$NJOBS" MAKEINFO=true all-gcc all-target-libgcc; \
    make install-gcc install-target-libgcc MAKEINFO=true; \
    \
    # Verify \
    mips-elf-gcc --version; \
    \
    # Clean up build artifacts \
    rm -rf "$BUILDDIR"

# ── Miniconda installer (cached in image, installed to bind mount on first run) ──
RUN ARCH=$(uname -m) \
    && curl -fsSL -o /tmp/miniconda.sh \
        "https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-${ARCH}.sh" \
    && chmod +x /tmp/miniconda.sh

ENV CONDA_PREFIX=/workspace/linuxminiconda
ENV PATH="${CONDA_PREFIX}/bin:${PATH}"

# ── Docker CLI (for managing sibling containers via mounted socket) ──
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu noble stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

# ── Python packages (system python, for tools that run outside conda) ──
RUN pip3 install --no-cache-dir --break-system-packages \
        capstone>=5.0.0 mcp>=1.0.0 "meson>=1.5"

# ── Host graphics: Xvfb (remote X for the DGL capture) + OSMesa (renderer backend) ──
# Used by sgi_glremote (the IRIS GL host renderer) and the live DGL capture: guest IRIS GL
# apps render against Xvfb (X side) + the DGL host server (GL side) via slirp guestfwd.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb xauth libosmesa6 libosmesa6-dev libgl1-mesa-dri mesa-utils \
    && rm -rf /var/lib/apt/lists/*

# ── Claude Code CLI ──
RUN npm install -g @anthropic-ai/claude-code

# ── Non-root user ──
RUN useradd -m -s /bin/bash -G sudo dev \
    && echo 'dev ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers \
    && ln -s /opt/cross /home/dev/cross

# ── Bashrc: conda init on exec ──
RUN echo '\n\
if [ -f "$CONDA_PREFIX/bin/conda" ]; then\n\
    eval "$("$CONDA_PREFIX/bin/conda" shell.bash hook)"\n\
elif [ -f /tmp/miniconda.sh ]; then\n\
    echo "Run: /tmp/miniconda.sh -b -p $CONDA_PREFIX && conda init bash"\n\
fi' >> /home/dev/.bashrc

USER dev
WORKDIR /workspace
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sleep", "infinity"]
