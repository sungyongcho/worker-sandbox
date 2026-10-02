"""Materialize a deterministic seed commit in independent Git object databases."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess


def seed_repository(root: Path, expected: str | None = None) -> str:
    if (root / '.git').exists():
        raise ValueError('Seed requires a new independent repository')
    environment = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
                   'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                   'GIT_AUTHOR_NAME': 'Benchmark Seed', 'GIT_AUTHOR_EMAIL': 'seed@invalid.example',
                   'GIT_COMMITTER_NAME': 'Benchmark Seed', 'GIT_COMMITTER_EMAIL': 'seed@invalid.example',
                   'GIT_AUTHOR_DATE': '2000-01-01T00:00:00+00:00', 'GIT_COMMITTER_DATE': '2000-01-01T00:00:00+00:00'}
    def git(*args):
        return subprocess.check_output(['/usr/bin/git', *args], cwd=root, env=environment, stderr=subprocess.PIPE).decode().strip()
    git('init', '--quiet', '--initial-branch=main', '--object-format=sha1')
    git('add', '--all', '--force')
    tree = git('write-tree')
    commit = git('commit-tree', tree, '-m', 'Public benchmark seed')
    git('update-ref', 'refs/heads/main', commit)
    if expected is not None and commit != expected:
        raise ValueError('Independent seed commit differs from frozen source')
    return commit
