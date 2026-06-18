"""pyirix_qemu.install_v2 — IRIX install harness, rewrite.

Replacement for the 4161-line install/irix.py monolith. See README.md for
the design notes. Modules:

    context       — InstallContext dataclass; threads state through phases
    inst_session  — clean expect wrapper around the `inst` shell
    orchestrator  — top-level driver (drives phases, manages checkpoints)
    phases/       — one module per install phase
    profiles/     — declarative YAML install configs
    policies/     — declarative conflict-resolution rules
    completeness/ — manifest + checker; honest "did this install succeed?"

This module is built side-by-side with the legacy install/ module. Once
proven (2-3 successful clean installs), the legacy module is deleted and
install_v2 is moved to install/.
"""
