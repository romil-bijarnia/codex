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


def stable_release_pin():
    """The declared stable source/resource base, never the newest development branch."""
    tag = CONFIG.get('upstream_release', '')
    match = re.fullmatch(r'rust-v(\d+\.\d+\.\d+)', tag)
    commit = CONFIG.get('upstream_commit', '')
    digest = CONFIG.get('resource_sha256', '')
    if (CONFIG.get('upstream_channel') != 'stable' or match is None
            or not re.fullmatch(r'[0-9a-f]{40}', commit)
            or not re.fullmatch(r'[0-9a-f]{64}', digest)):
        raise RuntimeError('The source manifest must pin a stable release tag, commit and resource checksum.')
    return {'upstream_channel': 'stable', 'upstream_release': tag,
            'upstream_commit': commit, 'compiled_version': match.group(1),
            'official_resources_sha256': digest}


def stable_package_version(commit):
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise RuntimeError('Stable package identity requires a complete personal source commit SHA.')
    return stable_release_pin()['compiled_version'] + '+romil.' + commit[:12]


def validate_stable_manifest(manifest):
    """Reject a differently based or merely relabelled download before selection."""
    pin = stable_release_pin()
    if any(manifest.get(key) != value for key, value in pin.items()):
        raise RuntimeError('The published build does not match the pinned stable release base.')
    if (manifest.get('official_resources_tag') != pin['upstream_release']
            or manifest.get('version') != stable_package_version(manifest.get('source_commit'))):
        raise RuntimeError('The published stable version or resource identity is inconsistent.')
    if manifest.get('voice_build_commit') != manifest.get('source_commit'):
        raise RuntimeError('The voice helper was not built from the same source as the custom CLI.')
    if ('voice-runtime' not in manifest.get('check_suites', [])
            or 'voice-runtime' not in manifest.get('required_local_checks', [])):
        raise RuntimeError('The stable package has no mandatory native voice startup check.')


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
