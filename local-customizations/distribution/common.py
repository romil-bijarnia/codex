"""Shared source-build and installation primitives."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / 'manifest.json').read_text())
REPO = CONFIG['repository']
TARGET = CONFIG['target']


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def output(args, **kwargs):
    return subprocess.check_output([str(x) for x in args], text=True, **kwargs).strip()


def api(path, method='GET', fields=None):
    args = ['gh', 'api', path]
    if method != 'GET':
        args += ['--method', method]
    for key, value in (fields or {}).items():
        args += ['-f', f'{key}={value}']
    return json.loads(output(args))


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def source_root():
    """Find the repository from this nested tool directory, including worktrees."""
    return Path(output(['git', '-C', ROOT, 'rev-parse', '--show-toplevel']))


def source_revision(commit=None):
    commit = commit or api(f"repos/{REPO}/commits/{CONFIG['source_branch']}")['sha']
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise RuntimeError('Source revision must be a complete commit SHA from the personal source branch.')
    return commit


def release_tag(commit):
    return 'source-' + source_revision(commit)


def atomic_write(path, content, mode=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content.encode() if isinstance(content, str) else content)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.chmod(existing_mode if mode is None else mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_link(path, destination):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.new-{os.getpid()}')
    temporary.unlink(missing_ok=True)
    try:
        temporary.symlink_to(destination)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
