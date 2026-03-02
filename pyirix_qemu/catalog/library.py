"""External software library scanner with format detection and SQLite index.

Scans external directories (e.g. NAS, SMB mounts) containing SGI software
collections and builds a persistent SQLite index for fast search without
re-scanning.  Supports disc images (EFS, ISO), tardist packages, tarballs,
and community package repositories (Nekoware, tgcware).

Two-phase scanning:
  1. Fast walk — stat + filename heuristics, no file I/O
  2. Deep probe — read magic bytes, EFS superblock, ISO PVD (optional)

Usage:
    from pyirix_qemu.catalog.library import LibraryScanner

    scanner = LibraryScanner("/Volumes/Library/software/IRIX")
    scanner.scan()
    results = scanner.search("MIPSpro")
    scanner.stage(results[0], "/workspace/staging/mipspro.img")
"""

import hashlib
import os
import re
import shutil
import sqlite3
import struct
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ── Format types ──────────────────────────────────────────────────────

class FileFormat(Enum):
    """Detected format of a library entry."""
    EFS_IMAGE = "efs_image"         # SGI EFS disc image (.efs.img, .img)
    ISO9660 = "iso9660"             # ISO 9660 CD image (.iso)
    TARDIST = "tardist"             # SGI tardist package (.tardist)
    TARBALL = "tarball"             # tar / tar.gz / tar.bz2
    RAW_IMAGE = "raw_image"         # Unrecognized .img (could be XFS, etc.)
    NEKOWARE_ISO = "nekoware_iso"   # Nekoware distribution ISO
    DIRECTORY = "directory"         # Directory containing dist/ or similar
    UNKNOWN = "unknown"


# ── Category types ────────────────────────────────────────────────────
# Reuse compatible naming from image_catalog.py

CATEGORY_OS = "os"
CATEGORY_DEV = "dev"
CATEGORY_FREEWARE = "freeware"
CATEGORY_NEKOWARE = "nekoware"
CATEGORY_TGCWARE = "tgcware"
CATEGORY_APPLICATION = "application"
CATEGORY_NETWORKING = "networking"
CATEGORY_GRAPHICS = "graphics"
CATEGORY_MULTIMEDIA = "multimedia"
CATEGORY_DEMO = "demo"
CATEGORY_PATCHES = "patches"
CATEGORY_MISC = "misc"


# ── Result dataclass ──────────────────────────────────────────────────

@dataclass
class LibraryEntry:
    """A single item in the external software library."""
    path: str                       # Absolute path on the external volume
    filename: str                   # Base filename
    format: str                     # FileFormat value
    category: str                   # Category string
    size_bytes: int = 0             # File size
    mtime: float = 0.0             # Modification time (epoch)
    display_name: str = ""          # Human-readable name
    version: str = ""               # Detected version string
    part_number: str = ""           # SGI part number (812-xxxx-xxx)
    deep_probed: bool = False       # Whether magic-byte detection was done
    notes: str = ""                 # Additional info from deep probe

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)

    @property
    def size_display(self) -> str:
        if self.size_bytes >= 1024 * 1024 * 1024:
            return f"{self.size_bytes / (1024**3):.1f} GB"
        elif self.size_bytes >= 1024 * 1024:
            return f"{self.size_bytes / (1024**2):.1f} MB"
        elif self.size_bytes >= 1024:
            return f"{self.size_bytes / 1024:.1f} KB"
        return f"{self.size_bytes} B"


# ── Format detection ──────────────────────────────────────────────────

# SGI volume header magic
_VH_MAGIC = 0x0BE5A941
# EFS superblock magic values
_EFS_MAGIC = 0x072959
_EFS_MAGIC_NEW = 0x07295A
# ISO 9660 signature at offset 32769
_ISO_SIGNATURE = b'CD001'
_ISO_SIG_OFFSET = 32769


def detect_format_from_filename(filename: str) -> FileFormat:
    """Fast format detection using only the filename."""
    lower = filename.lower()

    if lower.endswith('.efs.img'):
        return FileFormat.EFS_IMAGE
    if lower.endswith('.iso'):
        if 'nekoware' in lower:
            return FileFormat.NEKOWARE_ISO
        return FileFormat.ISO9660
    if lower.endswith('.tardist'):
        return FileFormat.TARDIST
    if lower.endswith('.tar.gz') or lower.endswith('.tgz'):
        return FileFormat.TARBALL
    if lower.endswith('.tar.bz2') or lower.endswith('.tar.xz'):
        return FileFormat.TARBALL
    if lower.endswith('.tar') or lower.endswith('.tar.z'):
        return FileFormat.TARBALL
    if lower.endswith('.img'):
        return FileFormat.RAW_IMAGE

    return FileFormat.UNKNOWN


def detect_format_deep(filepath: str) -> Tuple[FileFormat, str]:
    """Deep format detection by reading magic bytes.

    Returns (format, notes) where notes may contain info like
    "EFS partition at offset 0x2000" or "ISO volume: LABEL".
    """
    try:
        with open(filepath, 'rb') as f:
            # Read first 64KB for magic detection
            header = f.read(65536)
    except (OSError, PermissionError):
        return FileFormat.UNKNOWN, "unreadable"

    if len(header) < 512:
        return FileFormat.UNKNOWN, "too small"

    notes_parts = []

    # Check for SGI volume header at offset 0
    magic = struct.unpack('>I', header[0:4])[0]
    if magic == _VH_MAGIC:
        # Has SGI volume header — look for EFS partition
        notes_parts.append("SGI volume header")
        # Parse partition table (16 entries at offset 72, each 16 bytes)
        for i in range(16):
            off = 72 + i * 16
            if off + 16 > len(header):
                break
            nblks, first, ptype = struct.unpack('>III', header[off:off+12])
            if ptype == 7:  # EFS
                notes_parts.append(f"EFS partition at sector {first}")
                # Check EFS superblock
                sb_off = first * 512 + 512  # superblock is block 1
                if sb_off + 12 <= len(header):
                    sb_magic = struct.unpack('>I', header[sb_off+4:sb_off+8])[0]
                    if sb_magic in (_EFS_MAGIC, _EFS_MAGIC_NEW):
                        return FileFormat.EFS_IMAGE, "; ".join(notes_parts)
                # Even without readable superblock, VH+EFS type is enough
                return FileFormat.EFS_IMAGE, "; ".join(notes_parts)
            elif ptype == 10:  # XFS
                notes_parts.append(f"XFS partition at sector {first}")

        if notes_parts:
            return FileFormat.RAW_IMAGE, "; ".join(notes_parts)

    # Check for ISO 9660 at sector 16 (offset 32768)
    if len(header) > _ISO_SIG_OFFSET + 5:
        if header[_ISO_SIG_OFFSET:_ISO_SIG_OFFSET+5] == _ISO_SIGNATURE:
            # Read volume ID from PVD (32 bytes at offset 32808)
            vol_id = header[32808:32840].decode('ascii', errors='replace').strip()
            if vol_id:
                notes_parts.append(f"volume: {vol_id}")
            return FileFormat.ISO9660, "; ".join(notes_parts)

    # Check for tar magic at offset 257
    if len(header) > 265:
        tar_magic = header[257:262]
        if tar_magic == b'ustar':
            return FileFormat.TARBALL, "POSIX tar"

    # Check for gzip magic
    if header[:2] == b'\x1f\x8b':
        return FileFormat.TARBALL, "gzip compressed"

    return FileFormat.UNKNOWN, ""


# ── Categorization ────────────────────────────────────────────────────

# SGI part number pattern: 812-XXXX-XXX
_PART_NUMBER_RE = re.compile(r'(812-\d{4}-\d{3})')

# Nekoware/tgcware tardist naming: neko_<name>-<version>.tardist
_NEKO_RE = re.compile(r'^neko_(.+?)[-_](\d[\d.]*\w*)\.tardist$', re.I)
_TGCWARE_RE = re.compile(r'^tgcware_(.+?)[-_](\d[\d.]*\w*)\.tardist$', re.I)
# Freeware tardist: fw_<name>-<version>.tardist
_FW_TARDIST_RE = re.compile(r'^fw_(.+?)[-_](\d[\d.]*\w*)\.tardist$', re.I)

# Category rules by path components
_PATH_CATEGORY_RULES: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'/(\d+\.\d+)_os/', re.I), CATEGORY_OS),
    (re.compile(r'/development/', re.I), CATEGORY_DEV),
    (re.compile(r'/freeware/', re.I), CATEGORY_FREEWARE),
    (re.compile(r'/nekoware/', re.I), CATEGORY_NEKOWARE),
    (re.compile(r'/tgcware/', re.I), CATEGORY_TGCWARE),
    (re.compile(r'/nfs/|/networker/', re.I), CATEGORY_NETWORKING),
    (re.compile(r'/cosmo/|/open_inventor/', re.I), CATEGORY_GRAPHICS),
    (re.compile(r'/demos?/', re.I), CATEGORY_DEMO),
    (re.compile(r'/patches/', re.I), CATEGORY_PATCHES),
    (re.compile(r'/impressario/', re.I), CATEGORY_MULTIMEDIA),
    (re.compile(r'/indizone/', re.I), CATEGORY_APPLICATION),
    (re.compile(r'/hot_mix/', re.I), CATEGORY_APPLICATION),
    (re.compile(r'/drivers/', re.I), CATEGORY_MISC),
    (re.compile(r'/snmp/', re.I), CATEGORY_NETWORKING),
    (re.compile(r'/webforce/', re.I), CATEGORY_APPLICATION),
    (re.compile(r'/performance.co.pilot/', re.I), CATEGORY_DEV),
    (re.compile(r'/trusted_irix/', re.I), CATEGORY_OS),
]

# Filename-based category rules (fallback)
_FILENAME_CATEGORY_RULES: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'IRIX\s+\d', re.I), CATEGORY_OS),
    (re.compile(r'MIPSpro|compiler|IDO\b', re.I), CATEGORY_DEV),
    (re.compile(r'ProDev|Developer', re.I), CATEGORY_DEV),
    (re.compile(r'Freeware', re.I), CATEGORY_FREEWARE),
    (re.compile(r'Nekoware', re.I), CATEGORY_NEKOWARE),
    (re.compile(r'neko_', re.I), CATEGORY_NEKOWARE),
    (re.compile(r'tgcware', re.I), CATEGORY_TGCWARE),
    (re.compile(r'fw_\w+\.tardist', re.I), CATEGORY_FREEWARE),
    (re.compile(r'NFS|ONC3', re.I), CATEGORY_NETWORKING),
    (re.compile(r'Maya|Alias|Softimage|Houdini|RenderMan', re.I), CATEGORY_GRAPHICS),
    (re.compile(r'Cosmo|Inventor', re.I), CATEGORY_GRAPHICS),
    (re.compile(r'Demo', re.I), CATEGORY_DEMO),
    (re.compile(r'patch', re.I), CATEGORY_PATCHES),
    (re.compile(r'Quake|quake', re.I), CATEGORY_APPLICATION),
    (re.compile(r'Netscape|Mozilla', re.I), CATEGORY_APPLICATION),
]

# Shared with pyirix_qemu.catalog.images — import to avoid duplication
from pyirix_qemu.catalog.images import _VERSION_PATTERNS  # noqa: E402


def categorize_entry(filepath: str) -> Tuple[str, str, str]:
    """Categorize a file by path and filename.

    Returns (category, version, part_number).
    """
    filename = os.path.basename(filepath)

    # Extract SGI part number
    m = _PART_NUMBER_RE.search(filename)
    part_number = m.group(1) if m else ""

    # Try path-based rules first
    category = ""
    for pattern, cat in _PATH_CATEGORY_RULES:
        if pattern.search(filepath):
            category = cat
            break

    # Fall back to filename rules
    if not category:
        for pattern, cat in _FILENAME_CATEGORY_RULES:
            if pattern.search(filename):
                category = cat
                break

    if not category:
        category = CATEGORY_MISC

    # Extract version
    version = ""
    for pattern in _VERSION_PATTERNS:
        m = pattern.search(filename)
        if m:
            version = m.group(1)
            break

    return category, version, part_number


def make_display_name(filename: str) -> str:
    """Create a human-readable display name from a filename."""
    name = filename
    # Strip common extensions
    for ext in ['.efs.img', '.tardist', '.tar.gz', '.tar.bz2',
                '.tar.xz', '.tar.z', '.tgz', '.tar', '.iso',
                '.img', '.zip', '.rar', '.gz']:
        if name.lower().endswith(ext):
            name = name[:-len(ext)]
            break

    # Parse nekoware/tgcware naming
    m = _NEKO_RE.match(filename)
    if m:
        return f"nekoware: {m.group(1)} {m.group(2)}"
    m = _TGCWARE_RE.match(filename)
    if m:
        return f"tgcware: {m.group(1)} {m.group(2)}"
    m = _FW_TARDIST_RE.match(filename)
    if m:
        return f"freeware: {m.group(1)} {m.group(2)}"

    # Clean up underscores/hyphens for readability
    return name.replace('_', ' ').strip()


def parse_nekoware_name(filename: str) -> Optional[Tuple[str, str]]:
    """Parse a nekoware tardist filename into (package_name, version).

    Returns None if the filename doesn't match nekoware naming.
    """
    m = _NEKO_RE.match(filename)
    if m:
        return m.group(1), m.group(2)
    return None


# ── SQLite Index ──────────────────────────────────────────────────────

_SCHEMA_VERSION = 1

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    filename TEXT NOT NULL,
    format TEXT NOT NULL,
    category TEXT NOT NULL,
    size_bytes INTEGER DEFAULT 0,
    mtime REAL DEFAULT 0,
    display_name TEXT DEFAULT '',
    version TEXT DEFAULT '',
    part_number TEXT DEFAULT '',
    deep_probed INTEGER DEFAULT 0,
    notes TEXT DEFAULT '',
    scan_time REAL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_entries_category ON entries(category);
CREATE INDEX IF NOT EXISTS idx_entries_format ON entries(format);
CREATE INDEX IF NOT EXISTS idx_entries_filename ON entries(filename);
"""

_FTS_TABLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    filename, display_name, category, notes, path,
    content='entries',
    content_rowid='id'
);
"""

_FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
    INSERT INTO entries_fts(rowid, filename, display_name, category, notes, path)
    VALUES (new.id, new.filename, new.display_name, new.category, new.notes, new.path);
END;

CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, filename, display_name, category, notes, path)
    VALUES ('delete', old.id, old.filename, old.display_name, old.category, old.notes, old.path);
END;

CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, filename, display_name, category, notes, path)
    VALUES ('delete', old.id, old.filename, old.display_name, old.category, old.notes, old.path);
    INSERT INTO entries_fts(rowid, filename, display_name, category, notes, path)
    VALUES (new.id, new.filename, new.display_name, new.category, new.notes, new.path);
END;
"""


class LibraryIndex:
    """Persistent SQLite index for the external library."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def initialize(self) -> None:
        """Create tables if they don't exist."""
        conn = self._connect()
        conn.executescript(_CREATE_TABLES)
        try:
            conn.executescript(_FTS_TABLE)
            conn.executescript(_FTS_TRIGGERS)
        except sqlite3.OperationalError:
            pass  # FTS5 not available — search will fall back to LIKE
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("schema_version", str(_SCHEMA_VERSION))
        )
        conn.commit()

    def has_fts(self) -> bool:
        """Check if FTS5 is available."""
        conn = self._connect()
        try:
            conn.execute("SELECT * FROM entries_fts LIMIT 0")
            return True
        except sqlite3.OperationalError:
            return False

    def upsert(self, entry: LibraryEntry) -> None:
        """Insert or update an entry."""
        conn = self._connect()
        conn.execute(
            """INSERT INTO entries
               (path, filename, format, category, size_bytes, mtime,
                display_name, version, part_number, deep_probed, notes,
                scan_time)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                 filename=excluded.filename,
                 format=excluded.format,
                 category=excluded.category,
                 size_bytes=excluded.size_bytes,
                 mtime=excluded.mtime,
                 display_name=excluded.display_name,
                 version=excluded.version,
                 part_number=excluded.part_number,
                 deep_probed=excluded.deep_probed,
                 notes=excluded.notes,
                 scan_time=excluded.scan_time""",
            (entry.path, entry.filename, entry.format, entry.category,
             entry.size_bytes, entry.mtime, entry.display_name,
             entry.version, entry.part_number,
             1 if entry.deep_probed else 0, entry.notes, time.time())
        )

    def bulk_upsert(self, entries: List[LibraryEntry]) -> None:
        """Insert or update multiple entries in a single transaction."""
        conn = self._connect()
        with conn:
            for entry in entries:
                self.upsert(entry)

    def search(self, query: str, category: Optional[str] = None,
               fmt: Optional[str] = None, limit: int = 50
               ) -> List[LibraryEntry]:
        """Search the index.

        Uses FTS5 if available, falls back to LIKE queries.
        """
        conn = self._connect()
        params: list = []
        conditions: list = []

        if category:
            conditions.append("e.category = ?")
            params.append(category)
        if fmt:
            conditions.append("e.format = ?")
            params.append(fmt)

        if query and self.has_fts():
            # FTS5 search — escape special chars
            fts_query = query.replace('"', '""')
            base = (
                "SELECT e.* FROM entries e "
                "JOIN entries_fts f ON e.id = f.rowid "
                f"WHERE entries_fts MATCH ?"
            )
            params.insert(0, f'"{fts_query}"')
            if conditions:
                base += " AND " + " AND ".join(conditions)
            base += f" LIMIT {limit}"
        elif query:
            # LIKE fallback
            like = f"%{query}%"
            conditions.append(
                "(e.filename LIKE ? OR e.display_name LIKE ? OR "
                "e.notes LIKE ? OR e.path LIKE ?)"
            )
            params.extend([like, like, like, like])
            base = "SELECT e.* FROM entries e WHERE " + " AND ".join(conditions)
            base += f" LIMIT {limit}"
        else:
            base = "SELECT e.* FROM entries e"
            if conditions:
                base += " WHERE " + " AND ".join(conditions)
            base += f" LIMIT {limit}"

        rows = conn.execute(base, params).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def get_stats(self) -> Dict[str, int]:
        """Get index statistics."""
        conn = self._connect()
        stats: Dict[str, int] = {}

        row = conn.execute("SELECT COUNT(*) FROM entries").fetchone()
        stats["total"] = row[0]

        for row in conn.execute(
            "SELECT category, COUNT(*) FROM entries GROUP BY category "
            "ORDER BY COUNT(*) DESC"
        ):
            stats[f"cat:{row[0]}"] = row[1]

        for row in conn.execute(
            "SELECT format, COUNT(*) FROM entries GROUP BY format "
            "ORDER BY COUNT(*) DESC"
        ):
            stats[f"fmt:{row[0]}"] = row[1]

        return stats

    def get_all(self, category: Optional[str] = None) -> List[LibraryEntry]:
        """Get all entries, optionally filtered by category."""
        conn = self._connect()
        if category:
            rows = conn.execute(
                "SELECT * FROM entries WHERE category = ? ORDER BY filename",
                (category,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM entries ORDER BY filename"
            ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def needs_rescan(self, path: str, mtime: float) -> bool:
        """Check if a file needs rescanning (new or modified)."""
        conn = self._connect()
        row = conn.execute(
            "SELECT mtime FROM entries WHERE path = ?", (path,)
        ).fetchone()
        if row is None:
            return True
        return abs(row[0] - mtime) > 1.0  # 1 second tolerance

    def remove_missing(self, existing_paths: set) -> int:
        """Remove entries whose paths no longer exist.

        Returns the number of entries removed.
        """
        conn = self._connect()
        all_rows = conn.execute("SELECT id, path FROM entries").fetchall()
        to_delete = [row[0] for row in all_rows if row[1] not in existing_paths]
        if to_delete:
            placeholders = ",".join("?" * len(to_delete))
            conn.execute(
                f"DELETE FROM entries WHERE id IN ({placeholders})", to_delete
            )
            conn.commit()
        return len(to_delete)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> LibraryEntry:
        return LibraryEntry(
            path=row["path"],
            filename=row["filename"],
            format=row["format"],
            category=row["category"],
            size_bytes=row["size_bytes"],
            mtime=row["mtime"],
            display_name=row["display_name"],
            version=row["version"],
            part_number=row["part_number"],
            deep_probed=bool(row["deep_probed"]),
            notes=row["notes"],
        )


# ── Main Scanner ──────────────────────────────────────────────────────

# File extensions we index
_INSTALLABLE_EXTENSIONS = {
    '.img', '.efs.img', '.iso', '.tardist',
    '.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tar.xz', '.tar.z',
}

# Extensions/directories to skip entirely
_SKIP_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff',
    '.txt', '.md', '.html', '.htm', '.pdf', '.doc',
    '.py', '.sh', '.pl', '.rb', '.js', '.cgi',
    '.mht', '.json', '.xml', '.csv',
    '.rar', '.zip', '.7z',
    # SGI inst subsystem files (not standalone installable media)
    '.idb', '.sw', '.sw32', '.sw64', '.man', '.books',
    '.data', '.src', '.hdr', '.relnotes', '.help',
    '.redirect', '.iscd',
    # Misc OS/metadata
    '.ds_store',
}

_SKIP_DIRS = {
    '.git', '__pycache__', 'screenshots', 'docs', 'Documents and Media',
    '#recycle',
}


def _should_index(filename: str) -> bool:
    """Check if a file should be indexed based on its extension."""
    lower = filename.lower()

    # Skip hidden files and OS metadata
    if filename.startswith('.') or lower == '.ds_store':
        return False

    # Check compound extensions first
    if lower.endswith('.efs.img'):
        return True

    _, ext = os.path.splitext(lower)

    if ext in _SKIP_EXTENSIONS:
        return False

    if ext in {'.img', '.iso', '.tardist', '.tar', '.tgz'}:
        return True

    # Check compound tar extensions
    for compound in ('.tar.gz', '.tar.bz2', '.tar.xz', '.tar.z'):
        if lower.endswith(compound):
            return True

    # Skip extensionless files — these are typically inst spec files
    # (e.g. fw_PAM, fw_BasiliskII) or other non-media
    if not ext:
        return False

    # Accept anything not explicitly skipped that's > some minimum size
    # (will be filtered later by the scanner if too small)
    return ext not in _SKIP_EXTENSIONS


class LibraryScanner:
    """Scans an external directory tree and builds a searchable index.

    Args:
        library_root: Path to the external software library root
        db_path: Path for the SQLite index file. Defaults to
            <project_root>/software_library/external_library.db
    """

    def __init__(self, library_root: str,
                 db_path: Optional[str] = None):
        self.library_root = os.path.abspath(library_root)

        if db_path is None:
            project_root = Path(__file__).resolve().parent.parent
            db_dir = project_root / "software_library"
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(db_dir / "external_library.db")

        self.index = LibraryIndex(db_path)
        self.index.initialize()

    def scan(self, deep: bool = False,
             progress_callback=None) -> Dict[str, int]:
        """Walk the library tree and index all installable files.

        Args:
            deep: If True, read magic bytes for format detection.
                  If False, use filename heuristics only (much faster
                  over network mounts).
            progress_callback: Optional callable(scanned, total_estimate)
                for progress reporting.

        Returns:
            Dict with scan statistics (new, updated, unchanged, removed,
            total, elapsed_seconds).
        """
        start = time.time()
        stats = {"new": 0, "updated": 0, "unchanged": 0,
                 "removed": 0, "total": 0, "errors": 0}

        # Phase 1: Walk and collect entries
        entries: List[LibraryEntry] = []
        seen_paths: set = set()

        for root, dirs, files in os.walk(self.library_root):
            # Skip unwanted directories
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

            for filename in files:
                if not _should_index(filename):
                    continue

                filepath = os.path.join(root, filename)
                seen_paths.add(filepath)

                try:
                    st = os.stat(filepath)
                except OSError:
                    stats["errors"] += 1
                    continue

                # Skip tiny files (< 1KB) — not installable media
                if st.st_size < 1024:
                    continue

                # Incremental: skip if unchanged
                if not self.index.needs_rescan(filepath, st.st_mtime):
                    stats["unchanged"] += 1
                    continue

                entry = self._build_entry(filepath, filename, st, deep)
                entries.append(entry)

                if progress_callback and len(entries) % 100 == 0:
                    progress_callback(len(entries), 0)

        # Phase 2: Bulk insert
        if entries:
            self.index.bulk_upsert(entries)
            stats["new"] = len(entries)  # Approximation — upsert handles both

        # Phase 3: Remove entries for files that no longer exist
        stats["removed"] = self.index.remove_missing(seen_paths)

        stats["total"] = len(seen_paths)
        stats["elapsed_seconds"] = round(time.time() - start, 1)

        return stats

    def _build_entry(self, filepath: str, filename: str,
                     st: os.stat_result, deep: bool) -> LibraryEntry:
        """Build a LibraryEntry for a single file."""
        # Filename-based detection
        fmt = detect_format_from_filename(filename)
        notes = ""

        # Deep probe if requested
        if deep and fmt in (FileFormat.UNKNOWN, FileFormat.RAW_IMAGE):
            fmt, notes = detect_format_deep(filepath)

        category, version, part_number = categorize_entry(filepath)
        display_name = make_display_name(filename)

        return LibraryEntry(
            path=filepath,
            filename=filename,
            format=fmt.value,
            category=category,
            size_bytes=st.st_size,
            mtime=st.st_mtime,
            display_name=display_name,
            version=version,
            part_number=part_number,
            deep_probed=deep,
            notes=notes,
        )

    def search(self, query: str, category: Optional[str] = None,
               fmt: Optional[str] = None,
               limit: int = 50) -> List[LibraryEntry]:
        """Search the index for matching entries."""
        return self.index.search(query, category=category, fmt=fmt,
                                 limit=limit)

    def get_stats(self) -> Dict[str, int]:
        """Get index statistics."""
        return self.index.get_stats()

    def close(self) -> None:
        """Close the database connection."""
        self.index.close()


# ── Staging Manager ───────────────────────────────────────────────────

DEFAULT_STAGING_DIR = Path(__file__).resolve().parent.parent / "staging"


def stage_file(entry: LibraryEntry,
               dest: Optional[str] = None,
               staging_dir: Optional[str] = None) -> str:
    """Copy a library entry to local staging for use with QEMU.

    Args:
        entry: The LibraryEntry to stage
        dest: Explicit destination path. If None, copies to staging_dir
              with the same filename.
        staging_dir: Staging directory. Defaults to <project>/staging/

    Returns:
        Absolute path to the staged file.

    Raises:
        FileNotFoundError: If source file doesn't exist
        OSError: If copy fails
    """
    src = Path(entry.path)
    if not src.exists():
        raise FileNotFoundError(f"Source not found: {entry.path}")

    if dest:
        dst = Path(dest)
    else:
        stage_dir = Path(staging_dir) if staging_dir else DEFAULT_STAGING_DIR
        stage_dir.mkdir(parents=True, exist_ok=True)
        dst = stage_dir / entry.filename

    # Skip if already staged and same size
    if dst.exists() and dst.stat().st_size == entry.size_bytes:
        return str(dst.resolve())

    shutil.copy2(str(src), str(dst))
    return str(dst.resolve())


def stage_entry_info(entry: LibraryEntry,
                     staging_dir: Optional[str] = None) -> Dict:
    """Check staging status for an entry without copying.

    Returns dict with 'staged' (bool), 'staged_path', 'needs_copy',
    'source_size', 'source_exists'.
    """
    stage_dir = Path(staging_dir) if staging_dir else DEFAULT_STAGING_DIR
    dst = stage_dir / entry.filename

    src_exists = os.path.exists(entry.path)
    staged = dst.exists()
    needs_copy = not staged or (
        staged and dst.stat().st_size != entry.size_bytes
    )

    return {
        "staged": staged,
        "staged_path": str(dst) if staged else str(stage_dir / entry.filename),
        "needs_copy": needs_copy,
        "source_size": entry.size_bytes,
        "source_exists": src_exists,
    }
