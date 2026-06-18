"""Phase 4: select — apply the profile's selectors against `inst`, with
policy-driven conflict resolution.

This is where v2 actively diverges from legacy. Legacy's
`_install_from_combined()` does "install standard / install prereqs /
auto-resolve(1,1,1...) / go" — that's the 54+ packages skipped path.

v2's approach:

    1. Shell-escape inst, mount the combined dist image, return to Inst>.
    2. Open the dist (or sub-dist subdirectories) via `from <path>`.
    3. For each selector in profile.select, send it. After each, send
       `conflicts` to flush inst's pending conflict list. If conflicts
       returned, hand them to phases.conflicts.resolve_all() against the
       v2 policy. Repeat until inst reports zero conflicts.
    4. Send `go` to commit. Watch the install run, tail output for
       errors, return when `Inst>` reappears.
    5. Parse the post-install summary for skipped/installed counts.

The CRUCIAL difference from legacy: every conflict is resolved by an
explicit rule, NOT a blind "pick option 1" — and an unmatched conflict
fails the install loudly via the policy's `fallback: abort`.

Inputs:
    ctx.live['inst']             — InstSession at AT_INST
    ctx.live['combined_image']   — host path to combined dist image
    ctx.profile.select           — list of inst selectors
    ctx.policy                   — ConflictPolicy for conflict resolution

Outputs:
    ctx.findings['select'] = {
        rounds, selectors_applied, conflict_decisions, skipped_packages
    }
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)


def _mount_and_discover(inst) -> list[str]:
    """Enter the shell escape ONCE: mount the combined image AND list its
    distribution subdirs. Returns the ordered list of `from`-able paths,
    or [] on mount failure.

    inst's shell escape doesn't like being re-entered cleanly (the second
    `sh` after we've exec'd /bin/sh confuses inst's state machine), so we
    do everything in one shell session and exit cleanly.
    """
    log.info("select: mounting combined dist + discovering distributions")
    inst.enter_shell()
    try:
        inst.shell("mkdir -p /mnt >& /dev/null")
        # csh syntax: $status for exit code (NOT $?). Quote the marker so
        # echoed-back command text doesn't false-match.
        out = inst.shell(
            "mount -r /dev/dsk/dks0d2s7 /mnt ; echo \"MOUNTRC_=$status\"",
            max_wait=30)
        if "MOUNTRC_=0" not in out:
            log.warning("select: mount failed; output=%s", out[-400:])
            return []
        out = inst.shell(
            "ls -A /mnt | wc -l ; echo \"LSRC_=$status\"", max_wait=15)
        if "LSRC_=0" not in out:
            log.warning("select: post-mount ls failed; output=%s",
                        out[-400:])
            return []
        log.info("select: mount OK (entries: %s)",
                 out.strip().splitlines()[-3:])

        # Detect layout: single /mnt/dist vs per-subdir. csh `test -d`
        # via /usr/bin/test; csh's `if -e` works too but `test` is safer.
        out = inst.shell("/usr/bin/test -d /mnt/dist ; echo \"LAYOUT_=$status\"")
        if "LAYOUT_=0" in out:
            log.info("select: single-dist layout (/mnt/dist)")
            return ["/mnt/dist"]

        # Per-subdir layout — list the subdirs.
        out = inst.shell("ls -1 /mnt >& /dev/null ; ls -1 /mnt")
        names: list[str] = []
        for line in out.splitlines():
            s = line.strip()
            if (not s or s.startswith("ls ") or s in ("#", "lost+found")
                    or s.endswith(":")):
                continue
            names.append(s)

        # Order: foundation/overlay → mipspro → dev → apps (empirical).
        def _prio(n: str) -> tuple[int, str]:
            l = n.lower()
            if "foundation" in l: return (0, l)
            if "overlay" in l or "install-tools" in l or "installation_tools" in l:
                return (1, l)
            if "mipspro" in l: return (2, l)
            if "dev" in l: return (3, l)
            if "prodev" in l: return (4, l)
            return (5, l)
        names.sort(key=_prio)
        paths = [f"/mnt/{n}" for n in names]
        log.info("select: per-subdir layout — %d distribution(s):", len(paths))
        for p in paths:
            log.info("    %s", p)
        return paths
    finally:
        inst.exit_shell()


def _from_dist(inst, dist_path: str) -> bool:
    """Open `dist_path` as an inst distribution. Returns True on success."""
    r = inst.cmd(f"from {dist_path}", max_wait=120)
    # `from` either lands back at Inst> or paginates a CD-name banner.
    if r.state.name == "AT_PAGER":
        r = inst.page_through()
    if r.state.name != "AT_INST":
        log.warning("select: `from %s` ended in %s state",
                    dist_path, r.state.name)
        return False
    if "Error" in r.output or "cannot" in r.output.lower():
        log.warning("select: `from %s` reported error: %s",
                    dist_path, r.output[-200:])
        return False
    return True


def _apply_selectors_and_resolve(inst, selectors: list[str], policy,
                                 log_dir: str | Path | None) -> dict:
    """Send each selector; after each, drain conflicts via the policy."""
    from .conflicts import resolve_all
    findings = {
        "selectors_applied": 0,
        "conflict_rounds": 0,
        "conflict_decisions": 0,
        "unresolved": 0,
    }
    for selector in selectors:
        log.info("select: applying %r", selector)
        r = inst.cmd(selector, max_wait=180)
        findings["selectors_applied"] += 1
        # Some selectors trigger pager.
        if r.state.name == "AT_PAGER":
            r = inst.page_through()
        # If the selector immediately produced conflicts, resolve them.
        if r.state.name == "AT_CONFLICT":
            report = resolve_all(inst, r.conflicts, policy, log_dir=log_dir)
            findings["conflict_rounds"] += report.rounds
            findings["conflict_decisions"] += len(report.decisions)
            if report.unresolved:
                findings["unresolved"] += len(report.unresolved)
                log.error("select: unresolved conflict — aborting "
                          "selector loop (selector=%r)", selector)
                return findings
        # Otherwise we should be back at Inst>.
    return findings


def _drain_conflicts(inst, policy, log_dir, findings):
    """After all selectors are applied, explicitly send `conflicts` to
    surface ANY remaining unresolved conflicts. Resolve via policy."""
    from .conflicts import resolve_all
    log.info("select: draining residual conflicts (`conflicts` cmd)")
    r = inst.cmd("conflicts", max_wait=60)
    if r.state.name == "AT_CONFLICT" and r.conflicts:
        report = resolve_all(inst, r.conflicts, policy, log_dir=log_dir)
        findings["conflict_rounds"] += report.rounds
        findings["conflict_decisions"] += len(report.decisions)
        if report.unresolved:
            findings["unresolved"] += len(report.unresolved)
            return False
    return True


def _run_go_and_watch(inst, max_wait: float = 3600) -> dict:
    """Send `go` to commit. Watch inst's output until it returns to
    `Inst>` (success), reports an error, or times out.

    Returns parsed install stats."""
    log.info("select: sending `go` (commit, max_wait=%ds)", max_wait)
    t0 = time.time()
    r = inst.cmd("go", max_wait=max_wait)
    elapsed = time.time() - t0
    log.info("select: go returned after %.0fs, state=%s",
             elapsed, r.state.name)

    # Parse the post-install summary if present.
    output = r.output or ""
    summary = {
        "elapsed_s": int(elapsed),
        "state": r.state.name,
        "skipped": _count_pattern(output, r"\d+\s+packages?\s+skipped"),
        "installed": _count_pattern(output, r"\d+\s+packages?\s+installed"),
        "errors": _count_pattern(output, r"(?i)error|failed"),
    }
    return summary


def _count_pattern(text: str, pattern: str) -> int:
    m = re.search(pattern, text)
    if not m:
        return 0
    digits = re.search(r"\d+", m.group(0))
    return int(digits.group(0)) if digits else 0


# ── Phase entry point ────────────────────────────────────────────────────


def run(ctx) -> object:
    inst = ctx.live.get("inst")
    if inst is None:
        raise RuntimeError("select: no InstSession — miniroot must run first")
    profile = ctx.profile
    policy = ctx.policy
    if not profile.select:
        log.warning("select: profile has no selectors — skipping")
        ctx.findings["select"] = {"skipped": True}
        ctx.mark_done("select")
        return ctx

    log_dir = ctx.log_dir or None
    findings: dict = {}

    # Steps 1 + 2 combined: mount the combined image AND discover dist
    # subdirs in a single shell-escape (inst's state machine doesn't like
    # being re-entered into shell mode multiple times).
    if not ctx.live.get("combined_image"):
        raise RuntimeError(
            "select: no combined image in profile; per-CD path not yet "
            "implemented in v2")
    dist_paths = _mount_and_discover(inst)
    if not dist_paths:
        raise RuntimeError(
            "select: combined image mount or discovery failed; per-CD "
            "fallback not yet implemented in v2")

    # Step 3: open each dist subdirectory in inst via `from <path>`.
    log.info("select: opening %d distribution(s)", len(dist_paths))
    for p in dist_paths:
        if not _from_dist(inst, p):
            log.warning("select: failed to open dist %s — continuing", p)

    # Step 3: apply selectors + resolve conflicts inline.
    findings.update(_apply_selectors_and_resolve(inst, profile.select,
                                                 policy, log_dir))
    if findings.get("unresolved"):
        ctx.findings["select"] = findings
        raise RuntimeError(
            f"select: {findings['unresolved']} unresolved conflict(s); "
            f"see {log_dir}/unresolved_conflicts.json")

    # Step 4: drain any conflicts that selectors didn't immediately surface.
    if not _drain_conflicts(inst, policy, log_dir, findings):
        ctx.findings["select"] = findings
        raise RuntimeError("select: unresolved conflicts during drain pass")

    # Step 5: GO. This is where inst actually installs everything.
    summary = _run_go_and_watch(inst)
    findings.update(summary)

    log.info("select: install commit complete — installed=%d skipped=%d "
             "errors=%d elapsed=%ds",
             summary["installed"], summary["skipped"],
             summary["errors"], summary["elapsed_s"])

    ctx.findings["select"] = findings
    ctx.mark_done("select")
    return ctx
