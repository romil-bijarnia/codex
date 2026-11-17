"""Merge custom Cargo dependency changes without replaying generated versions."""
import json
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib


class LockfileMergeConflict(RuntimeError):
    """A real dependency conflict, with text the caller can retain for resolution."""

    def __init__(self, merged_text):
        self.merged_text = merged_text
        super().__init__('Cargo.lock has overlapping custom and upstream dependency changes.')


def _normalize_workspace_versions(base_text, custom_text, custom_workspace_version, workspace_names):
    base_packages = tomllib.loads(base_text).get('package', [])
    base_versions = {}
    for package in base_packages:
        if 'source' not in package:
            name = package['name']
            # Ambiguous names must not cause an arbitrary version to be selected.
            base_versions[name] = None if name in base_versions else package['version']
    workspace_names = set(workspace_names)
    blocks = re.split(r'(?m)(?=^\[\[package\]\][ \t]*\r?$)', custom_text)
    for index, block in enumerate(blocks):
        if not block.startswith('[[package]]'):
            continue
        package = tomllib.loads(block)['package'][0]
        version = base_versions.get(package['name'])
        if ('source' in package or package['name'] not in workspace_names
                or package['version'] != custom_workspace_version or version is None):
            continue
        # Only this package's version field changes. Comments, dependencies and
        # external pins remain part of the real three-way merge.
        blocks[index] = re.sub(
            r'''(?m)^([ \t]*version[ \t]*=[ \t]*)(?:"[^"\r\n]*"|'[^'\r\n]*')([ \t]*(?:#[^\r\n]*)?)(\r?)$''',
            lambda match: match[1] + json.dumps(version) + match[2] + match[3],
            block, count=1,
        )
    return ''.join(blocks)


def merge_lockfile(base_text, custom_text, upstream_text, custom_workspace_version, workspace_names):
    """Return an upstream-based merge, or raise LockfileMergeConflict with markers.

    workspace_names must name actual workspace members, not every source-less
    lock entry. The caller subsequently regenerates workspace versions from its
    new manifests; this function does not run Cargo or change repository files.
    """
    normalized = _normalize_workspace_versions(
        base_text, custom_text, custom_workspace_version, workspace_names,
    )
    tomllib.loads(upstream_text)
    with tempfile.TemporaryDirectory(prefix='codex-lock-merge-') as temporary:
        folder = Path(temporary)
        paths = [folder / name for name in ('upstream', 'base', 'custom')]
        for path, content in zip(paths, (upstream_text, base_text, normalized)):
            path.write_text(content, encoding='utf-8')
        result = subprocess.run(
            ['git', 'merge-file', '-p', '-L', 'upstream', '-L', 'base', '-L', 'custom',
             *map(str, paths)], text=True, capture_output=True, check=False,
        )
    if result.returncode == 0:
        tomllib.loads(result.stdout)
        return result.stdout
    # merge-file returns the number of conflicts, capped at 127, not just 1.
    if 0 < result.returncode < 128:
        raise LockfileMergeConflict(result.stdout)
    raise RuntimeError(f'Git could not merge Cargo.lock: {result.stderr.strip() or result.returncode}')
