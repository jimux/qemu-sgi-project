"""inst_session — clean expect wrapper around the IRIX `inst` shell.

The legacy harness has the `inst` interaction smeared across ~1500 lines
of intertwined `session.send`/`session.wait_for`/regex-soup. This module
replaces it with a small state machine that names the prompts `inst`
actually produces and exposes one verb per legitimate transition.

States the driver tracks:

    AT_INST      — `Inst> ` prompt; ready for a top-level command
    AT_SHELL     — inside the `sh` escape; expecting `# ` prompt
    AT_CONFLICT  — `inst` printed a conflicts list; waiting for our choice
    AT_PAGER     — paged output; needs a space/q to continue
    AT_BUSY      — `inst` is doing work; no prompt yet
    AT_DONE      — quit completed; session exited

The driver knows:
  * how to send a command and wait for AT_INST again
  * how to enter and exit the shell escape
  * how to parse one or more conflicts from inst's output
  * how to send an option-letter selection for a conflict

It does NOT know:
  * how to RESOLVE conflicts — that's `phases/conflicts.py` against the
    declarative policy
  * which packages to install — that's `phases/select.py` against the
    profile

Keeping policy out of the driver is the whole point.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Iterable

log = logging.getLogger(__name__)


# ── Prompt grammar ───────────────────────────────────────────────────────────
# These regexes are tight. If `inst` adds a banner line or a release notes
# blurb that bumps the prompt off-screen, refresh by sending a bare `\r`
# and re-matching. Don't try to match _everything inst could possibly say_;
# match the small set of known prompts and let everything else be "noise".

RE_INST_PROMPT    = re.compile(r"\nInst>\s?$")
RE_SH_PROMPT      = re.compile(r"\n#\s?$")
RE_PAGER          = re.compile(r"--More--|\(end\)")
RE_CONFLICT_BANNER = re.compile(r"^Conflicts:\s*$", re.MULTILINE)
RE_CONFLICT_NUM   = re.compile(r"^\s*(\d+)\.\s", re.MULTILINE)
RE_CONFLICT_OPT   = re.compile(r"^\s*([a-z])\.\s+(.*)$", re.MULTILINE)
RE_DONE           = re.compile(r"Installation\s+(complete|finished)|Quit complete")
RE_INTERRUPT      = re.compile(r"Interrupt>")


class State(Enum):
    AT_INST = auto()
    AT_SHELL = auto()
    AT_CONFLICT = auto()
    AT_PAGER = auto()
    AT_BUSY = auto()
    AT_DONE = auto()


@dataclass
class Conflict:
    """One conflict parsed out of inst's `Conflicts:` block."""
    number: int                       # 1-based conflict number
    description: list[str]            # description text lines (multi-line)
    options: list[tuple[str, str]]    # [(letter, text), ...]

    @property
    def text(self) -> str:
        """Joined description for regex matching against policy rules."""
        return "\n".join(self.description)

    def __str__(self) -> str:
        opts = "\n".join(f"    {l}. {t}" for l, t in self.options)
        return f"Conflict {self.number}:\n  {self.text}\n{opts}"


@dataclass
class WaitResult:
    """Result of a wait-for-prompt call."""
    state: State
    output: str = ""          # bytes seen since last prompt
    conflicts: list[Conflict] = field(default_factory=list)


# ── The session ──────────────────────────────────────────────────────────────


class InstSession:
    """Wraps a QEMUSession in `inst`-aware semantics.

    Caller provides:
        session   — a pyirix_qemu.boot_harness.QEMUSession (or compatible
                    object exposing `.send(s)` and `.wait_for(regex,
                    timeout, max_wait) -> obj with .output`)

    The session is assumed to be at the `Inst>` prompt when the InstSession
    is constructed. The orchestrator's miniroot phase is responsible for
    getting it there.
    """

    def __init__(self, session, *, default_timeout: float = 5.0,
                 default_max_wait: float = 120.0):
        self._s = session
        self._timeout = default_timeout
        self._max_wait = default_max_wait
        self._state = State.AT_INST   # caller ensures we start here
        self._tail = ""               # accumulated unmatched output (kept short)

    # ── Low-level send + match ──────────────────────────────────────────

    def _send(self, line: str) -> None:
        """Send a line to inst (we always append \\r — `inst` is line-buffered).
        No state change here; caller is responsible for calling `_wait_for`."""
        if not line.endswith("\r") and not line.endswith("\n"):
            line = line + "\r"
        log.debug("INST << %r", line.strip())
        self._s.send(line)

    def _wait_for(self, *, max_wait: float | None = None) -> WaitResult:
        """Wait until we recognize a known prompt and return it.

        Matches the FIRST prompt found scanning new output:
            Inst>       → AT_INST
            #           → AT_SHELL
            Conflicts:  → AT_CONFLICT (parses the conflict block)
            --More--    → AT_PAGER
            Interrupt>  → AT_BUSY (signals user-aborted operation)
            "Quit complete" → AT_DONE

        Returns a WaitResult carrying the new state, the raw output seen,
        and any parsed conflicts.
        """
        max_wait = max_wait or self._max_wait
        deadline = time.time() + max_wait
        buf = ""
        # Read in short timeout windows so any of the prompt regexes can fire.
        while time.time() < deadline:
            remaining = deadline - time.time()
            wait = min(self._timeout, remaining)
            try:
                r = self._s.wait_for(
                    r"Inst>|^#\s|Conflicts:|--More--|\(end\)|Interrupt>|"
                    r"Quit complete|Installation (?:complete|finished)",
                    timeout=wait, max_wait=wait + 1)
            except Exception as e:
                log.warning("wait_for raised: %s", e)
                continue
            out = getattr(r, "output", "") or ""
            buf += out

            # Match in priority order — `Conflicts:` BEFORE `Inst>` because
            # the conflict block ends with an `Inst>` prompt.
            if RE_CONFLICT_BANNER.search(buf):
                conflicts = _parse_conflicts(buf)
                self._state = State.AT_CONFLICT
                log.debug("INST -> AT_CONFLICT (%d conflicts)", len(conflicts))
                return WaitResult(state=State.AT_CONFLICT,
                                  output=buf, conflicts=conflicts)
            if RE_DONE.search(buf):
                self._state = State.AT_DONE
                log.debug("INST -> AT_DONE")
                return WaitResult(state=State.AT_DONE, output=buf)
            if RE_PAGER.search(buf):
                self._state = State.AT_PAGER
                log.debug("INST -> AT_PAGER")
                return WaitResult(state=State.AT_PAGER, output=buf)
            if RE_SH_PROMPT.search(buf):
                self._state = State.AT_SHELL
                log.debug("INST -> AT_SHELL")
                return WaitResult(state=State.AT_SHELL, output=buf)
            if RE_INTERRUPT.search(buf):
                self._state = State.AT_BUSY
                log.debug("INST -> AT_BUSY (Interrupt>)")
                return WaitResult(state=State.AT_BUSY, output=buf)
            if RE_INST_PROMPT.search(buf):
                self._state = State.AT_INST
                log.debug("INST -> AT_INST")
                return WaitResult(state=State.AT_INST, output=buf)

        # Timed out without a recognized prompt.
        log.warning("INST wait timed out (%ds); last tail=%r",
                    max_wait, buf[-200:])
        return WaitResult(state=self._state, output=buf)

    # ── Public API ──────────────────────────────────────────────────────

    @property
    def state(self) -> State:
        return self._state

    def cmd(self, line: str, *, max_wait: float | None = None) -> WaitResult:
        """Send a top-level `inst` command, wait for return to Inst>.

        Caller must be at AT_INST. Raises if we're at a different prompt
        — that's a state-machine bug, not a runtime condition.
        """
        if self._state != State.AT_INST:
            raise RuntimeError(
                f"cmd() requires AT_INST, currently {self._state.name}")
        self._send(line)
        r = self._wait_for(max_wait=max_wait)
        return r

    def enter_shell(self, *, max_wait: float | None = None) -> WaitResult:
        """`sh` escape from `Inst>` — leaves us at `#`.

        IRIX inst's `sh` escape uses the user's login shell — on stock
        IRIX 6.5.5 that's /bin/csh. Callers should use csh-compatible
        syntax: `$status` (not `$?`) for exit codes; `set var = value`
        (not `var=value`) for variables. `2>/dev/null` and `; ` work in
        both shells. DO NOT `exec /bin/sh` inside — when you later exit,
        inst's state machine misbehaves and lands at the post-quit
        restart prompt instead of Inst>.
        """
        if self._state != State.AT_INST:
            raise RuntimeError(
                f"enter_shell requires AT_INST, currently {self._state.name}")
        self._send("sh")
        r = self._wait_for(max_wait=max_wait)
        if r.state != State.AT_SHELL:
            raise RuntimeError(
                f"enter_shell: expected AT_SHELL, got {r.state.name}")
        return r

    def exit_shell(self, *, max_wait: float | None = None) -> WaitResult:
        """Leave the `sh` escape — back at `Inst>`."""
        if self._state != State.AT_SHELL:
            raise RuntimeError(
                f"exit_shell requires AT_SHELL, currently {self._state.name}")
        self._send("exit")
        r = self._wait_for(max_wait=max_wait)
        if r.state != State.AT_INST:
            raise RuntimeError(
                f"exit_shell: expected AT_INST, got {r.state.name}")
        return r

    def shell(self, cmd: str, *, max_wait: float | None = None) -> str:
        """Run one shell command inside the escape. Caller must be AT_SHELL."""
        if self._state != State.AT_SHELL:
            raise RuntimeError(
                f"shell() requires AT_SHELL, currently {self._state.name}")
        self._send(cmd)
        r = self._wait_for(max_wait=max_wait)
        if r.state != State.AT_SHELL:
            raise RuntimeError(
                f"shell(): unexpected state {r.state.name}; out={r.output[-200:]!r}")
        return r.output

    def page_through(self, *, max_pages: int = 50) -> WaitResult:
        """Press space until the pager exits; returns the post-pager state."""
        for i in range(max_pages):
            if self._state != State.AT_PAGER:
                return WaitResult(state=self._state)
            self._send(" ")
            r = self._wait_for(max_wait=10)
            if r.state != State.AT_PAGER:
                return r
        raise RuntimeError(f"page_through: still paging after {max_pages} pages")

    def select_conflict_option(self, conflict_number: int, option_letter: str,
                               *, max_wait: float | None = None) -> WaitResult:
        """Tell inst: for conflict N, choose option L.

        Format inst expects: `N L` (e.g. `1 a`).
        """
        if self._state != State.AT_CONFLICT:
            raise RuntimeError(
                f"select_conflict_option requires AT_CONFLICT, "
                f"currently {self._state.name}")
        self._send(f"{conflict_number} {option_letter}")
        return self._wait_for(max_wait=max_wait)


# ── Conflict parsing ─────────────────────────────────────────────────────────


def _parse_conflicts(text: str) -> list[Conflict]:
    """Extract conflicts from a Conflicts: block in inst's output.

    Conflict blocks look like:

        Conflicts:
          1. eoe.sw.base from foundation requires motif_eoe.sw.base
             from foundation but motif_eoe.sw.base is not selected.
              a. Install motif_eoe.sw.base from foundation
              b. Do not install eoe.sw.base

          2. ...

    Algorithm: find the `Conflicts:` header, then split into numbered
    items. Each item starts with `<N>.` at any indent; everything before
    the first `<letter>.` line is the description; everything after is
    option lines until the next numbered item or end-of-block.
    """
    m = RE_CONFLICT_BANNER.search(text)
    if not m:
        return []
    body = text[m.end():]

    # Find numbered conflict starts.
    starts = [(int(nm.group(1)), nm.start()) for nm in RE_CONFLICT_NUM.finditer(body)]
    if not starts:
        return []

    conflicts: list[Conflict] = []
    for i, (number, pos) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(body)
        chunk = body[pos:end]
        # Stop the chunk at the next Inst> prompt — that terminates the
        # conflicts block, and anything after it isn't part of this
        # conflict's description.
        m_end = re.search(r"^Inst>\s*$", chunk, re.MULTILINE)
        if m_end:
            chunk = chunk[:m_end.start()]

        # Split chunk into desc lines + option lines. Option lines are
        # `<letter>.` at any indent; description is everything else.
        desc_lines: list[str] = []
        options: list[tuple[str, str]] = []
        for line in chunk.splitlines():
            opt_m = re.match(r"\s*([a-z])\.\s+(.*)", line)
            if opt_m:
                options.append((opt_m.group(1), opt_m.group(2).strip()))
            else:
                desc_lines.append(line.strip())

        # Trim leading "N." from the first non-empty description line.
        for j, line in enumerate(desc_lines):
            if line:
                desc_lines[j] = re.sub(r"^\d+\.\s*", "", line)
                break
        conflicts.append(Conflict(number=number,
                                  description=[l for l in desc_lines if l],
                                  options=options))
    return conflicts


__all__ = [
    "InstSession", "State", "Conflict", "WaitResult",
    # Convenience exports for tests:
    "_parse_conflicts",
]
