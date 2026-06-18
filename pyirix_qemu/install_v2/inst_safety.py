"""Targeted patches applied to the legacy installer at runtime.

`apply()` is called from phases/install.py before importing the legacy
installer. Patches are idempotent — multiple `apply()` calls are safe.

Currently a no-op. The legacy `_is_core_package` + `_select_conflict_option`
already protect `eoe.sw.base`, `desktop_eoe.*`, `4Dwm.*`, etc. from
deselection. If we discover additional packages that need core-status
protection during installs, add them to `_EXTRA_CORE_PACKAGES` below and
the legacy frozenset will be unioned with them at apply() time.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


# Add packages here if the install verifier shows them silently dropped
# by the legacy harness. Anything starting with one of these names will be
# refused for deselection during conflict resolution.
_EXTRA_CORE_PACKAGES: frozenset[str] = frozenset({
    # ── Empty for now; populate from observed install gaps ───────────
    # "ProDev", "ProDev.sw",
})


_applied = False


def apply() -> None:
    """Apply runtime patches. Idempotent — safe to call multiple times."""
    global _applied
    if _applied:
        return

    if _EXTRA_CORE_PACKAGES:
        try:
            import pyirix_qemu.install.irix as installer
            existing = installer._CORE_PACKAGES
            installer._CORE_PACKAGES = frozenset(existing | _EXTRA_CORE_PACKAGES)
            log.info("inst_safety: extended _CORE_PACKAGES with %d extra "
                     "package(s): %s",
                     len(_EXTRA_CORE_PACKAGES),
                     ", ".join(sorted(_EXTRA_CORE_PACKAGES)))
        except (ImportError, AttributeError) as e:
            log.warning("inst_safety: could not extend _CORE_PACKAGES: %s", e)

    _applied = True
