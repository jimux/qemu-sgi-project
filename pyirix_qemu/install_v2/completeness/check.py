"""Verify a manifest against an installed IRIX system.

Two backends:
    - HostBackend: reads the disk image via pyirix.sgi_fs (XFS reader),
      no booting required — fast for iterating during development.
    - GuestBackend: runs `ls -ld <path>` over telnet against a booted
      guest — required for `must_be_running` checks.

Each path entry is checked for existence + kind (file/dir/exec). The
`sanity:` section uses backend.list_dir to enforce minimum entry counts.
`must_be_running:` is checked via backend.list_processes.

CLI:
    python3 -m pyirix_qemu.install_v2.completeness.check \\
        --backend host --disk vm_instances/ip54-test/disk.qcow2 \\
        --manifest pyirix_qemu/install_v2/completeness/manifest_6_5_5.yaml

    python3 -m pyirix_qemu.install_v2.completeness.check \\
        --backend guest --host localhost --port 2323 \\
        --manifest pyirix_qemu/install_v2/completeness/manifest_6_5_5.yaml

Returns exit 0 on full pass, exit 1 on any required failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
except ImportError:
    yaml = None


# ── Result types ─────────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    """One verified manifest entry."""
    path: str
    why: str
    kind: str = "file"           # "file" | "dir" | "exec" | "process" | "sanity"
    optional: bool = False
    ok: bool = False
    detail: str = ""             # human-readable reason for failure (or "")

    @property
    def passed(self) -> bool:
        return self.ok or self.optional

    def __str__(self) -> str:
        sym = "OK" if self.ok else ("--" if self.optional else "FAIL")
        opt = " (optional)" if self.optional else ""
        detail = f"  [{self.detail}]" if self.detail else ""
        return f"  [{sym}] {self.kind:<7} {self.path:<60}{opt} — {self.why}{detail}"


@dataclass
class Report:
    """Aggregate verification result."""
    manifest_path: str
    backend: str
    results: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def required_failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.ok and not r.optional]

    def summary(self) -> str:
        total = len(self.results)
        ok = sum(1 for r in self.results if r.ok)
        opt_missing = sum(1 for r in self.results if not r.ok and r.optional)
        fail = sum(1 for r in self.results if not r.ok and not r.optional)
        verdict = "COMPLETE" if self.passed else "INCOMPLETE"
        return (f"{verdict}: {ok}/{total} OK, {opt_missing} optional missing, "
                f"{fail} required FAIL")

    def print(self, file=sys.stdout) -> None:
        print(f"Completeness check ({self.backend}): {self.manifest_path}",
              file=file)
        print("-" * 72, file=file)
        for r in self.results:
            print(str(r), file=file)
        print("-" * 72, file=file)
        print(self.summary(), file=file)


# ── Backends ─────────────────────────────────────────────────────────────────


class Backend:
    """Interface every backend must implement."""

    def exists(self, path: str) -> tuple[bool, str]:
        """Return (exists, kind) where kind is one of:
        'file' | 'dir' | 'symlink' | 'missing'."""
        raise NotImplementedError

    def is_executable(self, path: str) -> bool:
        raise NotImplementedError

    def list_dir(self, path: str) -> list[str]:
        """List entries in a directory (empty list if missing/not-a-dir)."""
        raise NotImplementedError

    def list_processes(self) -> list[str]:
        """Return process command names (NOT full argv, just the basename)."""
        raise NotImplementedError


class HostBackend(Backend):
    """Read directly from the disk image via sgi_mcp.sgi_fs (no guest boot).

    Opens the disk once (qcow2 → temp-raw conversion) and reuses the open
    file for every lookup. The disk closes when the backend is GC'd or
    .close() is called.
    """

    def __init__(self, disk_path: str, partition: int | None = None):
        sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
        from sgi_mcp import sgi_fs as _sgi_fs                          # noqa: E402
        self._sgi_fs = _sgi_fs
        self._disk = disk_path
        # open_disk_image is a contextmanager. Drive it manually so the
        # raw-converted temp file stays open for the lifetime of the backend.
        self._cm = _sgi_fs.open_disk_image(disk_path)
        self._f = self._cm.__enter__()
        part = _sgi_fs.find_xfs_partition(self._f)
        if not part:
            self._cm.__exit__(None, None, None)
            raise RuntimeError(f"No XFS partition found in {disk_path}")
        self._part_offset = part[0]
        self._sb = _sgi_fs.xfs_read_superblock(self._f, self._part_offset)
        if not self._sb:
            self._cm.__exit__(None, None, None)
            raise RuntimeError(f"Could not read XFS superblock from {disk_path}")
        # Cache resolved inodes (path → (ino, inode_dict)) to avoid redundant
        # path walks for the same dir-tree prefix.
        self._cache: dict[str, tuple[int, dict] | None] = {}

    def close(self) -> None:
        try:
            self._cm.__exit__(None, None, None)
        except Exception:
            pass

    def __del__(self):
        self.close()

    # ── primitives ──────────────────────────────────────────────────────

    def _resolve(self, path: str) -> tuple[int, dict] | None:
        if path in self._cache:
            return self._cache[path]
        ino = self._sgi_fs._xfs_resolve_path(self._f, self._part_offset,
                                             self._sb, path)
        if ino is None:
            self._cache[path] = None
            return None
        inode = self._sgi_fs.xfs_read_inode(self._f, self._part_offset,
                                            self._sb, ino)
        if not inode:
            self._cache[path] = None
            return None
        self._cache[path] = (ino, inode)
        return ino, inode

    @staticmethod
    def _kind_of(inode: dict) -> str:
        mode = inode.get("di_mode", 0)
        ftype = mode & 0o170000
        if ftype == 0o040000:
            return "dir"
        if ftype == 0o120000:
            return "symlink"
        if ftype == 0o100000:
            return "file"
        # block/char/fifo/sock — we don't distinguish; "file" is closest
        return "file"

    # ── Backend interface ───────────────────────────────────────────────

    def exists(self, path: str) -> tuple[bool, str]:
        r = self._resolve(path)
        if r is None:
            return False, "missing"
        _, inode = r
        return True, self._kind_of(inode)

    def is_executable(self, path: str) -> bool:
        r = self._resolve(path)
        if r is None:
            return False
        _, inode = r
        return bool(inode.get("di_mode", 0) & 0o111)

    def list_dir(self, path: str) -> list[str]:
        r = self._resolve(path)
        if r is None:
            return []
        _, inode = r
        if self._kind_of(inode) != "dir":
            return []
        entries = self._sgi_fs.xfs_read_dir_entries(
            self._f, self._part_offset, self._sb, inode)
        # entries is [(name, child_ino), ...]; skip '.' and '..'
        return [name for name, _ino in entries
                if name not in (".", "..")]

    def list_processes(self) -> list[str]:
        # Host backend can't see a running guest.
        return []


class GuestBackend(Backend):
    """Drive checks via telnet against a booted IRIX guest."""

    def __init__(self, host: str = "localhost", port: int = 2323,
                 user: str = "root"):
        sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
        from pyirix_qemu.irix_telnet import IRIXTelnet  # type: ignore
        self._t = IRIXTelnet(host=host, port=port, user=user)
        self._t.login()
        # Use Bourne shell — csh has weird quoting around heredocs.
        self._t.send("exec /bin/sh")

    def _q(self, cmd: str) -> str:
        out = self._t.run(cmd, timeout=20)
        return out

    def exists(self, path: str) -> tuple[bool, str]:
        # ls -ld returns kind in the mode bits; check exit status separately
        # via a marker.
        out = self._q(f"ls -ld '{path}' 2>/dev/null; echo X_$?_X")
        if "X_0_X" not in out:
            return False, "missing"
        # Parse the leading char of the long listing for kind:
        # -=file d=dir l=symlink
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("X_"):
                continue
            ch = line[0]
            if ch == "d":
                return True, "dir"
            if ch == "l":
                return True, "symlink"
            if ch == "-":
                return True, "file"
        return False, "missing"

    def is_executable(self, path: str) -> bool:
        out = self._q(f"test -x '{path}' && echo X_EXEC")
        return "X_EXEC" in out

    def list_dir(self, path: str) -> list[str]:
        out = self._q(f"ls -A1 '{path}' 2>/dev/null; echo X_DONE")
        entries: list[str] = []
        for line in out.splitlines():
            line = line.strip()
            if not line or line == "X_DONE" or line.startswith("X_"):
                continue
            # Skip command echo lines
            if line.startswith("ls "):
                continue
            entries.append(line)
        return entries

    def list_processes(self) -> list[str]:
        # IRIX ps -e -o comm; fall back to BSD ps.
        out = self._q("ps -e -o comm 2>/dev/null || ps ax")
        names: list[str] = []
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("COMMAND") or line.startswith("PID"):
                continue
            # Last whitespace-delimited token is the command name
            tok = line.split()[-1]
            tok = tok.split("/")[-1]   # strip leading path
            tok = tok.lstrip("-")      # strip "-bash" → "bash"
            names.append(tok)
        return names


# ── Manifest loader + driver ─────────────────────────────────────────────────


def load_manifest(path: str | os.PathLike) -> dict:
    if yaml is None:
        raise RuntimeError("PyYAML required; pip install pyyaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _check_path_entry(backend: Backend, entry: dict) -> CheckResult:
    path = entry["path"]
    why = entry.get("why", "")
    kind = entry.get("kind", "file")
    optional = bool(entry.get("optional", False))
    r = CheckResult(path=path, why=why, kind=kind, optional=optional)

    exists, actual_kind = backend.exists(path)
    if not exists:
        r.detail = "not present"
        return r
    if kind == "dir" and actual_kind not in ("dir", "symlink"):
        # Symlink-to-dir is the IRIX norm (e.g. /var/sysgen/boot → ../boot).
        # We accept symlinks where a dir is expected and trust the manifest
        # author meant "thing you can ls into".
        r.detail = f"expected dir, got {actual_kind}"
        return r
    if kind in ("file", "exec") and actual_kind not in ("file", "symlink"):
        r.detail = f"expected file, got {actual_kind}"
        return r
    if kind == "exec" and not backend.is_executable(path):
        r.detail = "not executable"
        return r
    r.ok = True
    return r


def _check_process_entry(backend: Backend, entry: dict,
                         procs: list[str]) -> CheckResult:
    name = entry["name"]
    why = entry.get("why", "")
    optional = bool(entry.get("optional", False))
    r = CheckResult(path=name, why=why, kind="process", optional=optional)
    if any(p == name or p.startswith(name) for p in procs):
        r.ok = True
    else:
        r.detail = "not running"
    return r


def _check_sanity_entry(backend: Backend, entry: dict) -> CheckResult:
    path = entry["path"]
    why = entry.get("why", "")
    min_entries = int(entry.get("min_entries", 1))
    r = CheckResult(path=path, why=why, kind="sanity",
                    optional=bool(entry.get("optional", False)))
    entries = backend.list_dir(path)
    if len(entries) < min_entries:
        r.detail = f"only {len(entries)} entries (need ≥{min_entries})"
        return r
    r.ok = True
    return r


def verify(backend: Backend, manifest_path: str) -> Report:
    manifest = load_manifest(manifest_path)
    report = Report(manifest_path=str(manifest_path),
                    backend=backend.__class__.__name__)

    for entry in manifest.get("must_exist", []):
        report.results.append(_check_path_entry(backend, entry))

    if manifest.get("must_be_running"):
        procs = backend.list_processes()
        # If we got no proc list back (e.g. HostBackend), mark all process
        # checks as "skipped — backend can't see processes".
        if not procs and isinstance(backend, HostBackend):
            for entry in manifest["must_be_running"]:
                r = CheckResult(path=entry["name"],
                                why=entry.get("why", ""),
                                kind="process",
                                optional=True,
                                detail="(host backend — process check skipped)")
                report.results.append(r)
        else:
            for entry in manifest["must_be_running"]:
                report.results.append(_check_process_entry(backend, entry, procs))

    for entry in manifest.get("sanity", []):
        report.results.append(_check_sanity_entry(backend, entry))

    return report


# ── CLI ──────────────────────────────────────────────────────────────────────


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify IRIX install completeness.")
    ap.add_argument("--manifest", required=True,
                    help="Path to manifest YAML.")
    ap.add_argument("--backend", choices=("host", "guest"), required=True)
    ap.add_argument("--disk", help="(host backend) qcow2/raw disk image path")
    ap.add_argument("--partition", type=int, default=0,
                    help="(host backend) partition index (default: auto)")
    ap.add_argument("--host", default="localhost",
                    help="(guest backend) telnet hostname")
    ap.add_argument("--port", type=int, default=2323,
                    help="(guest backend) telnet port")
    ap.add_argument("--user", default="root",
                    help="(guest backend) login user")
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON instead of text")
    args = ap.parse_args(argv)

    if args.backend == "host":
        if not args.disk:
            ap.error("--disk is required with --backend host")
        backend: Backend = HostBackend(args.disk, partition=args.partition)
    else:
        backend = GuestBackend(host=args.host, port=args.port, user=args.user)

    report = verify(backend, args.manifest)

    if args.json:
        out = {
            "manifest": report.manifest_path,
            "backend": report.backend,
            "passed": report.passed,
            "summary": report.summary(),
            "results": [
                {
                    "path": r.path,
                    "why": r.why,
                    "kind": r.kind,
                    "optional": r.optional,
                    "ok": r.ok,
                    "detail": r.detail,
                }
                for r in report.results
            ],
        }
        json.dump(out, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        report.print()

    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(_main())
