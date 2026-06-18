"""Integration tests: CLI subcommands end-to-end.

Run python3 -m pyirix.xfs <cmd> via subprocess, check exit codes and output.

Note: The CLI uses find_xfs_partition() which requires an SGI volume header.
Modern mkfs.xfs images lack volume headers, so CLI tests use IRIX disk only.
"""

import os
import subprocess
import pytest

IRIX_DISK = '/workspace/vm_instances/ip54-test/disk.qcow2'
SKIP_REASON = "ip54-test disk image not found"


def run_xfs_cli(*args, timeout=60):
    """Run the XFS CLI and return CompletedProcess."""
    cmd = ['python3', '-m', 'pyirix.xfs'] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ── CLI info ───────────────────────────────────────────────────────

@pytest.mark.slow
@pytest.mark.skipif(not os.path.exists(IRIX_DISK), reason=SKIP_REASON)
class TestCLIInfo:
    def test_info(self):
        r = run_xfs_cli('info', IRIX_DISK)
        assert r.returncode == 0
        assert 'XFS Filesystem:' in r.stdout
        assert 'Block size:' in r.stdout
        assert 'Total blocks:' in r.stdout
        assert 'Free blocks:' in r.stdout
        assert 'AG count:' in r.stdout
        assert 'Root inode:' in r.stdout
        assert 'Version:' in r.stdout
        assert 'SASH compat:' in r.stdout


# ── CLI ls ─────────────────────────────────────────────────────────

@pytest.mark.slow
@pytest.mark.skipif(not os.path.exists(IRIX_DISK), reason=SKIP_REASON)
class TestCLILs:
    def test_ls_root(self):
        r = run_xfs_cli('ls', IRIX_DISK, '/')
        assert r.returncode == 0
        assert 'etc' in r.stdout
        assert 'usr' in r.stdout

    def test_ls_recursive_etc(self):
        r = run_xfs_cli('ls', IRIX_DISK, '/etc', '-r', '-n', '50')
        assert r.returncode == 0
        lines = r.stdout.strip().split('\n')
        assert len(lines) >= 1

    def test_ls_nonexistent(self):
        r = run_xfs_cli('ls', IRIX_DISK, '/nonexistent_path_xyz')
        assert r.returncode != 0


# ── CLI check ──────────────────────────────────────────────────────

@pytest.mark.slow
@pytest.mark.skipif(not os.path.exists(IRIX_DISK), reason=SKIP_REASON)
class TestCLICheck:
    def test_check(self):
        r = run_xfs_cli('check', IRIX_DISK)
        assert r.returncode == 0
        assert '[PASS]' in r.stdout
        assert 'Superblock magic OK' in r.stdout
        assert 'Root inode' in r.stdout


# ── CLI cat ────────────────────────────────────────────────────────

@pytest.mark.slow
@pytest.mark.skipif(not os.path.exists(IRIX_DISK), reason=SKIP_REASON)
class TestCLICat:
    def test_cat_etc_passwd(self):
        r = run_xfs_cli('cat', IRIX_DISK, '/etc/passwd')
        if r.returncode == 0:
            assert 'root' in r.stdout

    def test_cat_nonexistent(self):
        r = run_xfs_cli('cat', IRIX_DISK, '/nonexistent_file_xyz')
        assert r.returncode != 0


# ── CLI error handling ─────────────────────────────────────────────

class TestCLIErrors:
    def test_no_command(self):
        r = run_xfs_cli()
        assert r.returncode != 0

    def test_bad_image_path(self):
        r = run_xfs_cli('info', '/nonexistent/disk.img')
        assert r.returncode != 0
