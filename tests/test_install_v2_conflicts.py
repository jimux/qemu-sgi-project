"""Tests for inst_session conflict parser + policy-rule matching.

These run without an IRIX guest — they exercise the pure parsing/policy
layers. Real install drives are gated on harness slow tests.
"""

import pytest
import sys
from pathlib import Path

# Make sure pyirix_qemu is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyirix_qemu.install_v2.inst_session import _parse_conflicts, Conflict


# ── Sample inst outputs ──────────────────────────────────────────────────────

SAMPLE_BASIC = """
Conflicts:

  1. eoe.sw.base from foundation requires motif_eoe.sw.base
     from foundation but motif_eoe.sw.base is not selected.
      a. Install motif_eoe.sw.base from foundation
      b. Do not install eoe.sw.base

  2. desktop_eoe.sw.toolchest conflicts with an installed version.
      a. Install desktop_eoe.sw.toolchest from overlay (newer version)
      b. Keep installed version

Inst>
"""

SAMPLE_TIGHT_INDENT = """
Conflicts:
1. pkg.a requires pkg.b
 a. Install pkg.b
 b. Do not install pkg.a
Inst>
"""

SAMPLE_EMPTY_BLOCK = """
Some preamble.
Conflicts:

Inst>
"""

SAMPLE_NO_CONFLICTS = """
Pre-installation check: no conflicts.
Inst>
"""

SAMPLE_MULTI_OPTION = """
Conflicts:
  1. Three-way conflict.
      a. Option alpha
      b. Option beta
      c. Option gamma
      d. Option delta
Inst>
"""


def test_basic_two_conflicts():
    c = _parse_conflicts(SAMPLE_BASIC)
    assert len(c) == 2
    # First conflict
    assert c[0].number == 1
    assert "motif_eoe.sw.base" in c[0].text
    assert "1." not in c[0].description[0]   # the leading number stripped
    assert len(c[0].options) == 2
    assert c[0].options[0] == ("a", "Install motif_eoe.sw.base from foundation")
    # Second conflict
    assert c[1].number == 2
    assert "overlay" in c[1].text or "toolchest" in c[1].text
    assert len(c[1].options) == 2


def test_tight_indentation():
    c = _parse_conflicts(SAMPLE_TIGHT_INDENT)
    assert len(c) == 1
    assert c[0].number == 1
    assert len(c[0].options) == 2
    assert c[0].options == [("a", "Install pkg.b"),
                            ("b", "Do not install pkg.a")]


def test_no_conflicts_returns_empty():
    """A 'no conflicts' message must NOT produce ghost conflicts."""
    assert _parse_conflicts(SAMPLE_NO_CONFLICTS) == []


def test_empty_block_returns_empty():
    """A `Conflicts:` header with no numbered items returns []."""
    assert _parse_conflicts(SAMPLE_EMPTY_BLOCK) == []


def test_multi_option_conflict():
    c = _parse_conflicts(SAMPLE_MULTI_OPTION)
    assert len(c) == 1
    letters = [opt[0] for opt in c[0].options]
    assert letters == ["a", "b", "c", "d"]


def test_inst_prompt_not_absorbed_into_description():
    """Regression: the trailing `Inst>` line must NOT appear in the
    last conflict's description (we hit this in initial dev)."""
    c = _parse_conflicts(SAMPLE_BASIC)
    for conflict in c:
        for line in conflict.description:
            assert "Inst>" not in line, \
                f"Inst> leaked into description: {line!r}"


# ── Policy matching ──────────────────────────────────────────────────────────

from pyirix_qemu.install_v2.context import load_policy


def test_default_policy_loads():
    pol = load_policy("default")
    assert pol.name == "default"
    assert len(pol.rules) >= 3
    assert pol.fallback == "abort"


def test_resolution_picks_install_over_drop():
    """The headline behavior change: when inst offers 'install X' vs
    'do not install Y', the new harness picks INSTALL — not the silent
    deselect path the legacy harness took."""
    from pyirix_qemu.install_v2.phases.conflicts import _pick_option, _fallback

    pol = load_policy("default")
    sample = """
Conflicts:
  1. eoe.sw.base requires motif_eoe.sw.base but motif_eoe is not selected.
      a. Also install motif_eoe.sw.base from foundation
      b. Do not install eoe.sw.base
Inst>
"""
    conflict = _parse_conflicts(sample)[0]
    d = _pick_option(conflict, pol) or _fallback(conflict, pol)
    assert d is not None
    assert d.chosen_letter == "a"
    assert "install" in d.chosen_text.lower()
    assert "do not install" not in d.chosen_text.lower()


def test_resolution_prefers_overlay_over_foundation():
    """Overlay packages supersede foundation packages — we want the newer."""
    from pyirix_qemu.install_v2.phases.conflicts import _pick_option, _fallback

    pol = load_policy("default")
    sample = """
Conflicts:
  1. desktop_eoe.sw.toolchest conflicts with installed version.
      a. Install desktop_eoe.sw.toolchest from overlay (newer version)
      b. Keep foundation version
Inst>
"""
    conflict = _parse_conflicts(sample)[0]
    d = _pick_option(conflict, pol) or _fallback(conflict, pol)
    assert d is not None
    assert d.chosen_letter == "a"
    assert "overlay" in d.chosen_text.lower()


def test_unmatched_conflict_falls_back_to_abort():
    """When NOTHING matches and fallback is 'abort', return None — the
    orchestrator's job is then to dump JSON and exit non-zero."""
    from pyirix_qemu.install_v2.phases.conflicts import _pick_option, _fallback
    from pyirix_qemu.install_v2.context import ConflictPolicy

    pol = ConflictPolicy(name="strict", rules=[
        # Only a useless rule that won't match anything.
        {"match": "this-will-never-match", "choose": "choose_install"},
    ], fallback="abort")

    sample = """
Conflicts:
  1. mystery_pkg conflicts with nothing.
      a. Embrace chaos
      b. Continue with chaos
Inst>
"""
    conflict = _parse_conflicts(sample)[0]
    d = _pick_option(conflict, pol)
    assert d is None
    d = _fallback(conflict, pol)
    assert d is None   # abort returns None — orchestrator detects + bails


def test_default_policy_handles_known_cases():
    """Sanity-check that the rule regexes match the conflict patterns
    we expect to see from inst."""
    import re
    pol = load_policy("default")

    def match_first(text: str) -> tuple[str, str] | None:
        for rule in pol.rules:
            if re.search(rule["match"], text):
                return rule["match"], rule["choose"]
        return None

    # Cross-CD prereq
    assert match_first("a. Also install motif_eoe.sw.base from foundation") \
        == (r"(?i)also install", "choose_install")

    # Newer overlay version
    assert match_first("a. Install desktop_eoe.sw.toolchest from overlay (newer version)") \
        == (r"(?i)overlay|patch.*foundation|supersedes|newer version",
            "choose_higher_version")

    # Refuse-drop
    assert match_first("b. Do not install eoe.sw.base") \
        == (r"(?i)do not install|deselect|drop|remove from install",
            "refuse_drop")
