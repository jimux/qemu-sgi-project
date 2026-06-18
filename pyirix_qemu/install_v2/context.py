"""InstallContext — the single piece of state threaded through phases.

Phases never touch globals. They take an `InstallContext`, mutate fields
on it (e.g. attach the QEMUSession after prepare(), set the partition
table after partition(), etc.), and return it.

Resumability: a phase can serialize the context to JSON between runs.
Anything non-serializable (open session handles, raw file objects) is
held under `.live` and reconstructed on resume.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# ── Profile / policy schema ──────────────────────────────────────────────────


@dataclass
class Profile:
    """Loaded install profile (one YAML in profiles/)."""
    name: str
    version: str
    description: str = ""
    machine: str = "indy"
    ram_mb: int = 256
    disk_size_mb: int = 4096
    fs_type: str = "xfs"
    media: dict = field(default_factory=dict)
    partitioning: dict = field(default_factory=dict)
    select: list[str] = field(default_factory=list)
    conflict_policy: str = "default"
    manifest: str = ""
    post_inst_sh: list[str] = field(default_factory=list)
    output_disk: str = ""
    snapshot_after_install: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        # Allow missing optional fields; tolerate extras (forward-compat).
        keep = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**keep)


@dataclass
class ConflictPolicy:
    """Loaded conflict-resolution policy (one YAML in policies/)."""
    name: str
    rules: list[dict] = field(default_factory=list)
    fallback: str = "abort"   # action to take when no rule matches

    @classmethod
    def from_dict(cls, d: dict) -> "ConflictPolicy":
        keep = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**keep)


# ── Loaders ──────────────────────────────────────────────────────────────────


def _load_yaml(path: str | Path) -> dict:
    try:
        import yaml
    except ImportError as e:
        raise RuntimeError("PyYAML required; pip install pyyaml") from e
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_profile(profile_name_or_path: str) -> Profile:
    """Accept either a bare name (looks under install_v2/profiles/) or a
    full path."""
    p = Path(profile_name_or_path)
    if not p.exists():
        # Try as a name under the bundled profiles dir.
        base = Path(__file__).parent / "profiles"
        candidate = base / f"{profile_name_or_path}.yaml"
        if candidate.exists():
            p = candidate
        else:
            raise FileNotFoundError(
                f"Profile not found: {profile_name_or_path} "
                f"(tried {p}, {candidate})")
    return Profile.from_dict(_load_yaml(p))


def load_policy(name_or_path: str) -> ConflictPolicy:
    p = Path(name_or_path)
    if not p.exists():
        base = Path(__file__).parent / "policies"
        candidate = base / f"{name_or_path}_conflicts.yaml"
        if candidate.exists():
            p = candidate
        else:
            raise FileNotFoundError(
                f"Policy not found: {name_or_path}")
    return ConflictPolicy.from_dict(_load_yaml(p))


# ── Runtime context ──────────────────────────────────────────────────────────


@dataclass
class InstallContext:
    """State threaded through every install phase.

    Persistent fields are JSON-serializable. Live-only state (session
    handles, open files) lives under `.live` and is not serialized.
    """
    # Inputs
    profile: Profile = field(default_factory=lambda: Profile(name="", version=""))
    policy: ConflictPolicy = field(
        default_factory=lambda: ConflictPolicy(name="default"))
    disk_path: str = ""
    instance: str = ""           # vm_instances/<instance>/ — optional
    log_dir: str = ""            # where to write per-phase logs

    # Phase progress (updated as phases run; used for resume).
    completed_phases: list[str] = field(default_factory=list)
    last_snapshot: str = ""

    # Phase results (per-phase findings).
    findings: dict[str, Any] = field(default_factory=dict)

    # Non-serializable runtime handles (session, open disk, etc.).
    live: dict[str, Any] = field(default_factory=dict, repr=False)

    def mark_done(self, phase: str, snapshot: str = "") -> None:
        if phase not in self.completed_phases:
            self.completed_phases.append(phase)
        if snapshot:
            self.last_snapshot = snapshot

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict. Do NOT use asdict() — it
        deep-copies every field including ctx.live, which holds thread
        locks (QEMUSession's socket, etc.) that can't be pickled."""
        return {
            "profile": asdict(self.profile),
            "policy": asdict(self.policy),
            "disk_path": self.disk_path,
            "instance": self.instance,
            "log_dir": self.log_dir,
            "completed_phases": list(self.completed_phases),
            "last_snapshot": self.last_snapshot,
            # findings should already be JSON-friendly (phase modules
            # construct them from plain dicts/strings/ints).
            "findings": dict(self.findings),
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2,
                                         default=str))

    @classmethod
    def load(cls, path: str | Path,
             profiles_dir: str | Path | None = None) -> "InstallContext":
        data = json.loads(Path(path).read_text())
        prof = Profile.from_dict(data.get("profile", {}))
        pol = ConflictPolicy.from_dict(data.get("policy", {}))
        return cls(
            profile=prof,
            policy=pol,
            disk_path=data.get("disk_path", ""),
            instance=data.get("instance", ""),
            log_dir=data.get("log_dir", ""),
            completed_phases=data.get("completed_phases", []),
            last_snapshot=data.get("last_snapshot", ""),
            findings=data.get("findings", {}),
        )
