"""Read-only views of a session's working folder: git changes and files.

Every path is resolved and checked against the session folder and the allowed
roots, so symlinks and `..` cannot escape them. Git runs without a shell, with
a timeout and an output cap.
"""

import fnmatch
import os
import subprocess
from pathlib import Path

GIT_TIMEOUT = 10
DIFF_MAX_BYTES = 400 * 1024
LIST_MAX_ENTRIES = 500


class WorkspaceError(Exception):
    pass


def _git(cwd, *args):
    try:
        proc = subprocess.run(
            ['git', '-C', str(cwd), '--no-pager', *args],
            capture_output=True, timeout=GIT_TIMEOUT, check=False,
            env={'GIT_OPTIONAL_LOCKS': '0', 'LANG': 'C', 'HOME': os.environ.get('HOME', ''), 'PATH': os.environ.get('PATH', '/usr/bin:/bin')},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkspaceError(f'git failed: {exc}') from exc
    return proc.returncode, proc.stdout[:DIFF_MAX_BYTES].decode('utf-8', 'replace')


def git_changes(cwd):
    """Status and diff of the git repository containing `cwd`, if any."""
    code, top = _git(cwd, 'rev-parse', '--show-toplevel')
    if code != 0:
        return {'repo': False}
    _, branch = _git(cwd, 'branch', '--show-current')
    _, status = _git(cwd, 'status', '--porcelain=v1', '--untracked-files=normal')
    _, diff = _git(cwd, 'diff', 'HEAD', '--no-color', '--no-ext-diff', '--stat=200', '--patch')
    files = []
    for line in status.splitlines():
        if len(line) > 3:
            files.append({'status': line[:2].strip() or '?', 'path': line[3:]})
    return {
        'repo': True,
        'root': top.strip(),
        'branch': branch.strip(),
        'files': files,
        'diff': diff,
        'truncated': len(diff.encode()) >= DIFF_MAX_BYTES,
    }


class FileViewer:
    def __init__(self, config):
        self.config = config

    def _resolve(self, base, rel):
        base = Path(base).resolve()
        if not self.config.is_allowed_path(base):
            raise WorkspaceError('This folder is outside the allowed roots.')
        target = (base / (rel or '.')).resolve()
        if target != base and base not in target.parents:
            raise WorkspaceError('This path is outside the session folder.')
        return base, target

    def _denied(self, path):
        return any(fnmatch.fnmatch(part, pattern) for part in path.parts for pattern in self.config.file_denylist)

    def list(self, base, rel=''):
        base, target = self._resolve(base, rel)
        if not target.is_dir():
            raise WorkspaceError('Not a folder.')
        entries = []
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name in ('.git', 'node_modules', '__pycache__', '.venv'):
                continue
            entries.append({
                'name': child.name,
                'path': str(child.relative_to(base)),
                'dir': child.is_dir(),
                'denied': self._denied(child.relative_to(base)),
            })
            if len(entries) >= LIST_MAX_ENTRIES:
                break
        return {'path': str(target.relative_to(base)) if target != base else '', 'entries': entries}

    def read(self, base, rel):
        base, target = self._resolve(base, rel)
        if self._denied(target.relative_to(base)):
            raise WorkspaceError('This file matches the deny list and cannot be shown.')
        if not target.is_file():
            raise WorkspaceError('Not a file.')
        size = target.stat().st_size
        limit = self.config.file_max_kb * 1024
        with open(target, 'rb') as fh:
            data = fh.read(limit)
        if b'\x00' in data[:8192]:
            return {'path': rel, 'binary': True, 'size': size}
        return {
            'path': rel,
            'binary': False,
            'size': size,
            'truncated': size > limit,
            'content': data.decode('utf-8', 'replace'),
        }
