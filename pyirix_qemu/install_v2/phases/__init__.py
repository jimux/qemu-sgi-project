"""Phases of an IRIX install. Each module exports a single `run(ctx)`
function that takes the InstallContext, performs its phase, mutates ctx
in-place, and returns ctx. Phases never own state — all state lives on
InstallContext so the orchestrator can checkpoint between phases.

Pipeline:

    prepare    — create VM instance dir, fresh disk, NVRAM cleanup
    partition  — boot sash from boot CD, run fx
    miniroot   — boot miniroot, mkfs the new partition, get to Inst>
    select     — apply profile selectors
    conflicts  — resolve outstanding conflicts via policy (loop until stable)
    kernel     — quit, autoconfig+lboot, reboot from disk
    verify     — boot from disk, run completeness check against the manifest

Each phase reads `ctx.profile`, `ctx.disk_path`, `ctx.live['session']`,
etc. and writes results into `ctx.findings[<phase_name>]`.
"""
