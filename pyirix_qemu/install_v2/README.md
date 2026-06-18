# pyirix_qemu/install_v2 — IRIX install harness, second cut

Replacement for `pyirix_qemu/install/irix.py` (4161-line monolith).

## Why a rewrite

The original harness shipped enough to boot, but produced **incomplete**
installs — packages that ship by default on a real IRIX system end up
missing here. Symptoms we hit:

- `clogin` falls back to plain `xlogin` because `/usr/local/lib/faces/` is empty.
- `desktop_eoe.sw.*` subpackages randomly missing.
- A trail of one-off "install X afterwards" patches that compound.

Root causes traced through the old harness:

1. Package selection is `install *` with cascading prereq failures. Code
   comments openly say "47+ skipped packages". When `inst` skips a package,
   the original harness logs a warning and moves on.
2. Conflict resolution is ~700 lines of ad-hoc state machine that auto-picks
   `1` for most conflicts — sometimes that deselects packages we wanted.
3. "Success" is defined as `uname` working after reboot. No check that
   `/usr/lib/faces/`, `/usr/Cadmin/`, `/usr/lib/X11/iconlib/`, etc. are
   actually populated.
4. No way to add a profile without editing the giant `VERSIONS` dict and
   threading it through five layers of switching logic.

## Design principles

- **Declarative profiles.** A profile is a YAML file naming the install
  media (resolved via the existing `catalog/images.py`), the package
  selectors, and the conflict policy. Adding a profile = adding a YAML file.
- **Honest verification.** Every install ends with a completeness check
  against a manifest of files/dirs/devices that MUST exist. Missing
  `/usr/local/lib/faces/root` is a FAILURE, not a warning.
- **Phases as separate modules.** `prepare → partition → miniroot → inst →
  conflicts → kernel → verify`. Each has a single clear contract.
- **Checkpoint + resume.** After each phase, snapshot the VM. Resuming
  from "kernel" doesn't re-run `inst`.
- **One inst strategy.** Combined-image only. The old CD-swap path is dead
  code — we always have a combined image (or we generate one). Drop the
  fallback chain.
- **No global state.** All harness state passes through an `InstallContext`
  object: profile, paths, session handle, log dir.

## Layout

```
install_v2/
├── README.md                     ← this file
├── __init__.py
├── orchestrator.py               ← drives phases, manages checkpoints
├── context.py                    ← InstallContext dataclass
├── inst_session.py               ← cleaner expect wrapper around `inst`
├── phases/
│   ├── prepare.py                ← VM instance + fresh disk
│   ├── partition.py              ← fx
│   ├── miniroot.py               ← boot miniroot + mkfs
│   ├── select.py                 ← apply profile package selectors
│   ├── conflicts.py              ← apply conflict policy
│   ├── kernel.py                 ← autoconfig + reboot
│   └── verify.py                 ← completeness check
├── profiles/
│   ├── irix_6_5_5_dev.yaml       ← full desktop + MIPSpro + dev + demos
│   └── irix_6_5_5_minimal.yaml   ← base OS only
├── policies/
│   └── default_conflicts.yaml    ← global conflict rules (prefer overlay over base, etc.)
└── completeness/
    ├── manifest_6_5_5.yaml       ← what MUST exist for a complete install
    └── check.py                  ← walk manifest, telnet-into-guest verify
```

## What a profile looks like

```yaml
# profiles/irix_6_5_5_dev.yaml
version: "6.5.5"
machine: indy
ram_mb: 256
disk_size_mb: 4096

# Where to find install media. Resolved by catalog/images.py from
# software_library/. Order is install precedence.
media:
  - category: combined
    image: IRIX_6.5.5_full_with_MIPSpro_and_demos_patched.img
  # If combined is unavailable, fall back to individual CDs:
  - category: foundation
    image: "IRIX 6.5 Foundation 1.img"
  - category: foundation
    image: "IRIX 6.5 Foundation 2.img"
  - category: overlay
    image: "IRIX 6.5.5 Installation Tools and Overlays (1 of 2) - 812-0818-005.efs.img"
  - category: overlay
    image: "IRIX 6.5.5 Overlays (2 of 2) - 812-0819-005.efs.img"
  - category: applications
    image: "IRIX 6.5 Applications August 1999 - 812-0877-004.efs.img"
  - category: networking
    image: "ONC3 NFS Version 3 for IRIX 6.2, 6.3, 6.4, and 6.5 - 812-0774-002.efs.img"

# What to install. `inst` selector expressions, applied in order.
select:
  - keep *           # baseline
  - install standard # SGI's "Standard" preset (the right default for a workstation)
  - install desktop_eoe.*        # full Indigo Magic Desktop, INCLUDING the faces
  - install x_eoe.*              # full X11
  - install nfs.*                # NFS client+server
  - install motif_eoe motif_dev
  - install gl_dev x_dev          # dev headers
  - install dev.*                # development tools
  - install c++_eoe c++_dev       # C++ runtime + dev
  - install compiler_eoe          # MIPSpro
  - install c_dev c_fe c_solo     # MIPSpro C frontend
  - install ProDev.*              # ProDev WorkShop

# Conflict policy. See policies/default_conflicts.yaml for the global rules.
conflict_policy: default

# Post-install commands run via inst's "sh" escape before reboot.
post_inst_sh:
  - "echo /usr/local/lib/faces snapshot:"
  - "ls /usr/local/lib/faces 2>/dev/null || echo MISSING"
```

## What a completeness manifest looks like

```yaml
# completeness/manifest_6_5_5.yaml
# Things that MUST exist on a complete IRIX 6.5.5 desktop install.
# Each entry: a path glob + a description of why we care.
must_exist:
  # Default user-face icons (the original blocker).
  - { path: "/usr/local/lib/faces/root", why: "default root face for clogin" }
  - { path: "/usr/local/lib/faces/guest", why: "default guest face" }
  - { path: "/usr/local/lib/faces/EZsetup", why: "EZsetup face" }

  # Core desktop binaries.
  - { path: "/usr/Cadmin/bin/clogin", why: "Indigo Magic visual login" }
  - { path: "/usr/sbin/4Dwm", why: "window manager" }
  - { path: "/usr/sbin/toolchest", why: "desktop launcher" }

  # MIPSpro (we use it to rebuild the kernel).
  - { path: "/usr/cpu/sysgen/root/usr/bin/cc", why: "MIPSpro C compiler" }
  - { path: "/usr/include/sys/types.h", why: "system headers" }
  - { path: "/usr/include/gl/gl.h", why: "IRIS GL development" }

  # X11 + Motif.
  - { path: "/usr/lib/X11/iconlib", why: "default icon library", kind: dir }
  - { path: "/usr/lib/libXm.so", why: "Motif runtime" }
  - { path: "/usr/include/Xm/Xm.h", why: "Motif development headers" }

  # Kernel build host requirements.
  - { path: "/var/sysgen/master.d", why: "kernel sysgen tree", kind: dir }
  - { path: "/var/sysgen/system/irix.sm", why: "default kernel master config" }

must_be_running:  # processes that should be up after multi-user
  - { name: "init", why: "process 1" }
  - { name: "inetd", why: "service launcher" }
  - { name: "xdm", why: "X display manager — required for clogin", optional: true }
```

The completeness check walks the manifest via the telnet harness against a
booted guest and reports per-entry pass/fail. The orchestrator treats
manifest failure as install failure — no silent passes.

## Conflict policy

Conflicts in `inst` typically ask "do you want the foundation or the overlay
version of X?" The original harness picks `1` (foundation) blindly, which is
wrong when an overlay is meant to supersede a foundation package.

```yaml
# policies/default_conflicts.yaml
rules:
  # Always prefer the higher version (overlays supersede foundations).
  - match: "supersedes|newer|overlay"
    choose: higher_version

  # Keep the SGI-recommended default for "Standard|Default|All" choices.
  - match: "Standard|Default"
    choose: as_offered

  # For "also install X" prompts, accept (we want a complete install).
  - match: "also install"
    choose: yes

  # For dropping packages — refuse. Cascade deselect is what produced
  # the 47-skipped-packages problem.
  - match: "neither.*install|skip"
    choose: refuse_drop
    fallback: choose_keep

fallback: as_offered  # if no rule matches, accept the inst default
```

## Migration plan

1. Build `install_v2/` as a separate module, leaving the old one in place.
2. Run a complete fresh 6.5.5 dev install through `install_v2` → end with a
   verified-complete `prebuilt_disks/irix-6.5.5-complete.qcow2`.
3. Use that disk as the new base for `irix655-dev` and `ip54-test`.
4. Once the new module has proven itself across 2–3 successful installs,
   delete `install/irix.py` and move `install_v2/` to `install/`.

## Open questions (tracked, not blocking)

- Do we want a profile *inherits* mechanism (minimal → dev) or stay flat YAML?
- Should completeness checks run inside the guest (telnet) or against the
  mounted XFS image from the host (fs_ls)? Both. Host-side is faster for the
  obvious file-existence checks; guest-side is required for `must_be_running`.
- The combined-image generator (extracting dist/ from individual CDs) lives
  in `pyirix/catalog/` — do we move it into install_v2 or keep it as a
  prepare step?
