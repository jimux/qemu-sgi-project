"""Phase 5: conflicts — resolve pending inst conflicts via the policy.

This is the heart of the resilience improvement. The legacy harness's
"blindly pick 1" caused 47+ packages to silently skip; this module
maps each conflict against the policy's rule list and chooses
deliberately. When NO rule matches, the policy's `fallback: abort`
makes us stop loudly with a JSON dump rather than silently degrade.

API:

    resolve_all(inst, policy, log_dir=None) -> ResolutionReport

The function loops:
    1. inst is currently AT_CONFLICT (caller's responsibility)
    2. parse conflicts from the prior wait_for output
    3. for each conflict: choose an option letter via the policy
    4. send `<number> <letter>` to inst
    5. wait for next prompt
    6. if AT_CONFLICT again (cascading), repeat
    7. else exit with ResolutionReport
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..inst_session import Conflict, InstSession, State
from ..context import ConflictPolicy

log = logging.getLogger(__name__)


@dataclass
class Decision:
    conflict_number: int
    chosen_letter: str
    chosen_text: str
    rule: str                  # the rule.match that fired (or "fallback")


@dataclass
class ResolutionReport:
    decisions: list[Decision] = field(default_factory=list)
    unresolved: list[Conflict] = field(default_factory=list)
    rounds: int = 0


# ── Decision logic ──────────────────────────────────────────────────────


def _pick_option(conflict: Conflict, policy: ConflictPolicy) -> Decision | None:
    """Apply policy rules to ONE conflict; return a Decision or None.

    None means the policy did not match — caller should handle the
    fallback (abort or as_offered)."""
    # Try each rule in order; the FIRST option that matches a rule wins.
    for rule in policy.rules:
        pattern = rule["match"]
        action = rule["choose"]
        # Find candidate options matching the rule's pattern.
        candidates: list[tuple[str, str]] = []
        for letter, text in conflict.options:
            if re.search(pattern, text):
                candidates.append((letter, text))
        if not candidates:
            continue

        # Action handlers — each returns a chosen (letter, text) or None.
        chosen = _apply_action(action, candidates, conflict)
        if chosen is None:
            continue
        return Decision(conflict_number=conflict.number,
                        chosen_letter=chosen[0],
                        chosen_text=chosen[1],
                        rule=pattern)
    return None


def _apply_action(action: str, candidates: list[tuple[str, str]],
                  conflict: Conflict) -> tuple[str, str] | None:
    """Implement the policy actions. `candidates` are options that the
    rule's `match` matched; `conflict.options` is the full list."""
    if action in ("choose_install", "choose_as_offered"):
        # Prefer options whose text starts with "Install"; fall back to the
        # first candidate.
        for letter, text in candidates:
            if text.lower().startswith("install") or text.lower().startswith("also install"):
                return (letter, text)
        return candidates[0]

    if action == "choose_keep":
        for letter, text in candidates:
            if "keep" in text.lower():
                return (letter, text)
        return candidates[0]

    if action == "choose_higher_version":
        # Prefer the option naming overlay / newer / patched / supersedes.
        prio = re.compile(r"(?i)overlay|newer|supersedes|patched|patch")
        scored = sorted(candidates,
                        key=lambda lt: 0 if prio.search(lt[1]) else 1)
        return scored[0]

    if action == "refuse_drop":
        # The MATCH was a "do-not-install" option. Pick the OTHER option
        # (any non-candidate).
        for letter, text in conflict.options:
            if (letter, text) not in candidates:
                return (letter, text)
        return None    # only options were "drop" — caller must abort

    # Explicit letter: `choose: a` etc.
    if len(action) == 1 and action.isalpha():
        for letter, text in conflict.options:
            if letter == action:
                return (letter, text)
        return None

    log.warning("unknown action %r — ignoring rule", action)
    return None


def _fallback(conflict: Conflict, policy: ConflictPolicy) -> Decision | None:
    """Apply the policy's fallback when no rule matched."""
    if policy.fallback == "abort":
        return None
    if policy.fallback == "as_offered":
        # Use the first option as inst's default.
        if not conflict.options:
            return None
        letter, text = conflict.options[0]
        return Decision(conflict_number=conflict.number,
                        chosen_letter=letter, chosen_text=text,
                        rule="fallback:as_offered")
    log.warning("unknown fallback %r — aborting", policy.fallback)
    return None


# ── Main entry point ────────────────────────────────────────────────────


def resolve_all(inst: InstSession, conflicts: list[Conflict],
                policy: ConflictPolicy, *,
                log_dir: str | Path | None = None,
                max_rounds: int = 20) -> ResolutionReport:
    """Apply the policy to the given conflicts; keep resolving cascades
    until inst is back at AT_INST or we hit max_rounds.

    If any conflict has no matching rule and the policy's fallback is
    `abort`, the function returns immediately with `unresolved` populated
    — the orchestrator persists this to JSON and exits."""
    report = ResolutionReport()

    while conflicts and report.rounds < max_rounds:
        report.rounds += 1
        log.info("conflicts: round %d, %d conflict(s)",
                 report.rounds, len(conflicts))

        # Decide for every conflict in this round.
        decisions_this_round: list[Decision] = []
        for c in conflicts:
            d = _pick_option(c, policy)
            if d is None:
                d = _fallback(c, policy)
            if d is None:
                # Unresolved — abort.
                log.warning("conflicts: no rule + fallback=abort for "
                            "conflict %d:\n%s", c.number, c)
                report.unresolved.append(c)
        if report.unresolved:
            _dump_unresolved(report.unresolved, log_dir)
            return report

        for c in conflicts:
            d = _pick_option(c, policy) or _fallback(c, policy)
            assert d is not None, "should have aborted above"
            decisions_this_round.append(d)

        # Send each decision to inst.
        last_wait = None
        for d in decisions_this_round:
            log.info("conflicts: choose %d %s  (%s)",
                     d.conflict_number, d.chosen_letter, d.chosen_text)
            last_wait = inst.select_conflict_option(d.conflict_number,
                                                   d.chosen_letter)
            report.decisions.append(d)

        # If inst is back at Inst>, we're done. If it presented a new
        # conflict cascade, loop.
        if last_wait and last_wait.state == State.AT_CONFLICT:
            conflicts = last_wait.conflicts
        else:
            break

    return report


def _dump_unresolved(unresolved: list[Conflict],
                     log_dir: str | Path | None) -> Path | None:
    if log_dir is None:
        return None
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    out = log_dir / "unresolved_conflicts.json"
    data = [
        {
            "number": c.number,
            "description": c.description,
            "options": [{"letter": l, "text": t} for l, t in c.options],
        }
        for c in unresolved
    ]
    out.write_text(json.dumps(data, indent=2))
    log.info("conflicts: wrote %d unresolved conflict(s) to %s",
             len(unresolved), out)
    return out
