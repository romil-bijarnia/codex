#!/usr/bin/env python3
"""Bring the personal harness forward without installing bare upstream."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib

from common import CONFIG, REPO, api, atomic_write, output, run, sha256, source_root


RELEASE = re.compile(r'rust-v(\d+)\.(\d+)\.(\d+)(?:-alpha\.(\d+(?:\.\d+)*))?')


def version_key(tag):
    match = RELEASE.fullmatch(tag)
    if not match:
        raise ValueError(f'Unsupported upstream release: {tag}')
    major, minor, patch, alpha = match.groups()
    return (int(major), int(minor), int(patch), alpha is None,
            tuple(map(int, alpha.split('.'))) if alpha else ())


def latest_release(channel):
    endpoint = f"repos/{CONFIG['upstream_repository']}/releases"
    endpoint += '/latest' if channel == 'stable' else '?per_page=20'
    for attempt in range(3):
        try:
            releases = api(endpoint)
            break
        except subprocess.CalledProcessError:
            if attempt == 2:
                raise
            print('Release lookup failed; retrying without changing your installation.', file=sys.stderr)
            time.sleep(2 ** attempt)
    if isinstance(releases, dict):
        releases = [releases]
    choices = [r for r in releases if not r.get('draft')
               and RELEASE.fullmatch(r.get('tag_name', ''))
               and (channel == 'latest' or not r.get('prerelease'))]
    if not choices:
        raise RuntimeError(f'No published upstream release for channel {channel}.')
    return max(choices, key=lambda r: version_key(r['tag_name']))


def resource_digest(release):
    name = 'codex-package-' + CONFIG['resource_target'] + '.tar.gz'
    assets = [a for a in release.get('assets', []) if a.get('name') == name]
    if len(assets) != 1 or not re.fullmatch(r'sha256:[0-9a-f]{64}', assets[0].get('digest') or ''):
        raise RuntimeError('The upstream release has no unique hash-verified resource package. Nothing was installed.')
    return assets[0]['digest'][7:]


def installed_source_matches(root, installed, custom):
    """A verification-only fix does not require recompiling unchanged native code."""
    built = installed.get('source_commit')
    if built == custom:
        return True
    if not built or installed.get('verification_source_commit') != custom:
        return False
    result = subprocess.run(['git', '-C', str(root), 'diff', '--name-only', built, custom],
                            capture_output=True, text=True)
    if result.returncode:
        return False
    allowed = ('local-customizations/distribution/checks/',
               'local-customizations/distribution/tests/', 'local-customizations/tests/')
    return all(path == 'local-customizations/distribution/upstream_update.py'
               or path.startswith(allowed) for path in result.stdout.splitlines())


def check_downgrade(release, selected):
    receipt = selected / 'custom-build.json'
    if not receipt.is_file():
        raise RuntimeError('The selected package has no custom source receipt; refusing to guess its identity.')
    installed = json.loads(receipt.read_text())
    tag = installed.get('upstream_release')
    if not tag:
        raise RuntimeError('The selected package has no upstream baseline; refusing a possible downgrade.')
    if version_key(release['tag_name']) < version_key(tag):
        raise RuntimeError(f"Refusing to downgrade {tag} to {release['tag_name']}. Your working installation is unchanged.")
    return installed


def custom_revision(root):
    if output(['git', '-C', root, 'branch', '--show-current']) != CONFIG['source_branch']:
        raise RuntimeError(f"Use the configured {CONFIG['source_branch']} source branch; another branch was not published or changed.")
    if output(['git', '-C', root, 'status', '--porcelain']):
        raise RuntimeError(f'Commit your source changes in {root} first; the updater does not overwrite or omit them.')
    branch = CONFIG['source_branch']
    # Fetch refs only. Never reset, stash or merge somebody's working checkout.
    run(['git', '-C', root, 'fetch', '--quiet', 'origin',
         f'refs/heads/{branch}:refs/remotes/origin/{branch}'])
    local = output(['git', '-C', root, 'rev-parse', 'HEAD'])
    remote = output(['git', '-C', root, 'rev-parse', f'origin/{branch}'])
    def ancestor(a, b):
        return subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', a, b],
                              check=False).returncode == 0
    if ancestor(remote, local):
        return local
    if ancestor(local, remote):
        return remote
    raise RuntimeError('Local and published custom source have diverged. Neither was overwritten; reconcile that source conflict first.')


def conflicted_paths(source, diagnostic):
    paths = set(output(['git', '-C', source, 'diff', '--name-only', '--diff-filter=U']).splitlines())
    # Git can roll back the whole application when a deletion cannot be merged.
    # In that case the index has no U entries, but stderr still names the overlaps.
    text = diagnostic.decode(errors='replace')
    paths.update(re.findall(r"Applied patch to '([^']+)' with conflicts\.", text))
    paths.update(re.findall(r'(?m)^error: patch failed: (.+):\d+$', text))
    return sorted(paths)


def reconcile_workspace_lock(source, base, custom, folder):
    """Merge real lock changes separately from generated workspace version stamps."""
    from lockfile import LockfileMergeConflict, merge_lockfile
    path = 'codex-rs/Cargo.lock'
    manifest = source / 'codex-rs/Cargo.toml'
    if not manifest.is_file() or not (source / path).is_file():
        return
    base_text = output(['git', '-C', source, 'show', f'{base}:{path}']) + '\n'
    custom_text = output(['git', '-C', source, 'show', f'{custom}:{path}']) + '\n'
    custom_manifest = tomllib.loads(output(['git', '-C', source, 'show', f'{custom}:codex-rs/Cargo.toml']))
    toolchain = tomllib.loads((source / 'codex-rs/rust-toolchain.toml').read_text())['toolchain']['channel']
    run(['rustup', 'toolchain', 'install', toolchain, '--profile', 'minimal'])
    cargo = output(['rustup', 'which', '--toolchain', toolchain, 'cargo'])
    env = dict(os.environ, RUSTUP_TOOLCHAIN=toolchain,
               RUSTC=output(['rustup', 'which', '--toolchain', toolchain, 'rustc']))
    env['PATH'] = str(Path(cargo).parent) + os.pathsep + env['PATH']
    metadata = json.loads(output([cargo, 'metadata', '--offline', '--no-deps', '--format-version', '1'],
                                 cwd=manifest.parent, env=env))
    members = set(metadata['workspace_members'])
    names = {p['name'] for p in metadata['packages'] if p['id'] in members}
    try:
        merged = merge_lockfile(base_text, custom_text, (source / path).read_text(),
                                custom_manifest['workspace']['package']['version'], names)
    except LockfileMergeConflict as error:
        (source / path).write_text(error.merged_text)
        raise RuntimeError(f'Genuine custom dependency conflict in {path}; no package was installed.') from error
    (source / path).write_text(merged)
    # Only path/workspace versions are refreshed; existing registry and Git pins remain locked.
    command = [cargo, 'update', '--workspace', '--offline']
    result = subprocess.run(command, cwd=manifest.parent, env=env, capture_output=True, text=True)
    if result.returncode:
        # A new release may require an index or source not yet present in the local cache.
        result = subprocess.run(command[:-1], cwd=manifest.parent, env=env, capture_output=True, text=True)
    atomic_write(folder / 'lockfile.log', result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f'Workspace dependency resolution failed; nothing was installed. See {folder / "lockfile.log"}.')
    run(['git', '-C', source, 'add', '--', path])


def prepare_candidate(root, custom, release, folder):
    """Apply the custom delta with its real base, not a blanket ours/theirs rule."""
    source = folder / 'source'
    if source.exists():
        report_path = folder / 'result.json'
        if report_path.is_file():
            report = json.loads(report_path.read_text())
            if (report.get('custom_source') == custom
                    and report.get('upstream_release') == release['tag_name']
                    and report.get('customization_policy') == 'personal-fork-only'):
                if report.get('status') == 'source_conflict':
                    names = '\n'.join(report.get('conflicts', [])[:12])
                    raise RuntimeError(f'The recorded source conflict still needs resolution ({len(report.get("conflicts", []))} files).\n'
                                       f'{names}\nDetails: {report_path}. '
                                       'The working harness is unchanged; no duplicate build was started.')
                commit = report.get('source_commit')
                if (commit and output(['git', '-C', source, 'rev-parse', 'HEAD']) == commit
                        and not output(['git', '-C', source, 'status', '--porcelain'])):
                    return source, commit
        raise RuntimeError(f'An update checkout already exists: {source}. Its contents were not overwritten.')
    folder.mkdir(parents=True, exist_ok=True)
    run(['git', 'clone', '--quiet', '--shared', '--no-checkout', root, source])
    run(['git', '-C', source, 'remote', 'set-url', 'origin', f'https://github.com/{REPO}.git'])
    manifest_path = 'local-customizations/distribution/manifest.json'
    original = json.loads(output(['git', '-C', source, 'show', f'{custom}:{manifest_path}']))
    base = original['upstream_commit']
    run(['git', '-C', source, 'fetch', '--quiet', '--no-tags',
         'https://github.com/' + CONFIG['upstream_repository'] + '.git',
         'refs/tags/' + release['tag_name']])
    upstream = output(['git', '-C', source, 'rev-parse', 'FETCH_HEAD^{commit}'])
    run(['git', '-C', source, '-c', 'advice.detachedHead=false', 'checkout', '--quiet', '--detach', upstream])
    report = {'custom_source': custom, 'custom_base': base, 'upstream_release': release['tag_name'],
              'upstream_commit': upstream, 'checkout': str(source), 'installed': False,
              'customization_policy': 'personal-fork-only'}
    log = b''
    for old_base, revision in [(base, custom)]:
        delta = subprocess.check_output(['git', '-C', str(source), 'diff', '--no-ext-diff', '--binary', old_base, revision, '--', '.', ':(exclude)codex-rs/Cargo.lock'])
        if not delta:
            raise RuntimeError('The custom delta is empty; refusing to substitute bare upstream for the harness.')
        applied = subprocess.run(['git', '-C', str(source), 'apply', '--3way', '--index', '-'],
                                 input=delta, capture_output=True, check=False)
        log += applied.stderr
        atomic_write(folder / 'integration.log', log)
        if applied.returncode:
            conflicts = conflicted_paths(source, applied.stderr)
            report.update(status='source_conflict', conflicts=conflicts, conflict_source=revision)
            atomic_write(folder / 'result.json', json.dumps(report, indent=2) + '\n')
            names = '\n'.join(conflicts[:12]) or applied.stderr.decode(errors='replace')[-1800:]
            raise RuntimeError(f'Upstream overlaps your custom changes. No side was discarded and nothing was installed.\n'
                               f'{names}\nDetails: {folder / "result.json"}\nIntegration log: {folder / "integration.log"}')
    try:
        reconcile_workspace_lock(source, base, custom, folder)
    except RuntimeError as error:
        report.update(status='source_conflict', conflicts=['codex-rs/Cargo.lock'],
                      conflict_kind='workspace-dependencies', error=str(error))
        atomic_write(folder / 'result.json', json.dumps(report, indent=2) + '\n')
        raise
    original.update(upstream_channel='alpha' if release['prerelease'] else 'stable',
                    upstream_release=release['tag_name'], upstream_commit=upstream,
                    resource_sha256=resource_digest(release))
    atomic_write(source / manifest_path, json.dumps(original, indent=2) + '\n')
    run(['git', '-C', source, 'add', '--', manifest_path])
    tree = output(['git', '-C', source, 'write-tree'])
    # A deterministic merge identity permits retries to reuse the same verified build.
    stamp = output(['git', '-C', source, 'show', '-s', '--format=%aI', custom])
    env = dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
    parents = ['-p', custom, '-p', upstream]
    commit = output(['git', '-C', source, 'commit-tree', tree, *parents],
                    input=f"Integrate {release['tag_name']} with the custom harness\n", env=env)
    run(['git', '-C', source, 'reset', '--quiet', '--hard', commit])
    report.update(status='prepared', source_commit=commit)
    atomic_write(folder / 'result.json', json.dumps(report, indent=2) + '\n')
    return source, commit


def update(args):
    import manage
    manage.platform_check()
    home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')).expanduser()
    state = home / 'state/codex-customizations'
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'update.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another codex update is running. Your working version is unchanged.')
        selected = (home / 'packages/standalone/current').resolve(strict=True)
        release = latest_release(args.channel)
        installed = check_downgrade(release, selected)
        digest = resource_digest(release)
        root = source_root()
        if args.check:
            print(json.dumps({'upstream_release': release['tag_name'], 'resource_sha256': digest,
                              'selected_package': str(selected), 'installed': installed,
                              'custom_source': output(['git', '-C', root, 'rev-parse', 'HEAD']),
                              'customization_policy': 'personal-fork-only',
                              'integration_evaluated': False, 'real_model_requests': 0}, indent=2))
            return
        custom = custom_revision(root)
        if (installed.get('upstream_release') == release['tag_name']
                and installed_source_matches(root, installed, custom)):
            checksums = installed.get('binary_sha256', {})
            if (checksums and all(sha256(selected / 'bin' / name) == checksums.get(name)
                                  for name in ('codex', 'codex-exec-server', 'codex-code-mode-host'))):
                manage.start_backend_rollout(selected)
                print(f"Your custom harness is already current with {release['tag_name']}. The idle backend handover is being checked.")
                return
        identity = release['tag_name'] + '-' + custom[:12]
        folder = state / 'updates' / identity
        source, commit = prepare_candidate(root, custom, release, folder)
        if args.prepare_only:
            print(f'Prepared {commit} in {source}. No build was started and nothing was installed.')
            return
        recipe = source / 'local-customizations/distribution'
        env = dict(os.environ, CODEX_BUILD_CACHE=str(Path.home() / '.cache/codex-upstream-update'))
        run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', recipe / 'tests'], env=env)
        plan = folder / 'plan.json'
        # Use the candidate's recipe and source pin, never the old installer's baseline.
        run([sys.executable, '-B', recipe / 'build.py', 'plan', '--revision', commit, '--out', plan], env=env)
        print('Building and testing your custom harness locally. This makes no model requests.', flush=True)
        attempt = len(list(folder.glob('build-*'))) + 1
        work = folder / f'build-{attempt}'
        run([sys.executable, '-B', recipe / 'build.py', 'build', '--plan', plan,
             '--work', work, '--out', folder / 'release'], env=env)
        manifest = folder / 'release/release-manifest.json'
        result = json.loads(manifest.read_text())
        if result.get('source_commit') != commit or result.get('real_model_requests') != 0:
            raise RuntimeError('The build receipt does not match the verified candidate. Nothing was installed.')
        # Publication is a normal fast-forward push; never force-push over new work.
        run(['git', '-C', source, 'push', 'origin', f'{commit}:refs/heads/{CONFIG["source_branch"]}'])
        package = work / 'official'
        metadata = list(package.rglob('codex-package.json'))
        if len(metadata) != 1:
            raise RuntimeError('The verified package layout is ambiguous. Nothing was installed.')
        run([sys.executable, '-B', recipe / 'manage.py', 'install-package',
             '--package', metadata[0].parent, '--manifest', manifest], env=env)
        report = json.loads((folder / 'result.json').read_text())
        report.update(status='installed', installed=True, manifest=str(manifest))
        atomic_write(folder / 'result.json', json.dumps(report, indent=2) + '\n')
        try:
            run(['git', '-C', root, 'fetch', '--quiet', source, commit])
            if (not output(['git', '-C', root, 'status', '--porcelain'])
                    and output(['git', '-C', root, 'branch', '--show-current']) == CONFIG['source_branch']
                    and subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', 'HEAD', commit]).returncode == 0):
                run(['git', '-C', root, 'merge', '--quiet', '--ff-only', commit])
        except (OSError, subprocess.CalledProcessError):
            print(f'The verified harness is installed and published. The separate source checkout was not fast-forwarded: {root}', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(prog='codex update', description=__doc__)
    parser.add_argument('--check', action='store_true', help='Show the latest release and selected custom build; do not integrate, compile or install')
    parser.add_argument('--prepare-only', action='store_true', help='Integrate in a private checkout without compiling or installing')
    parser.add_argument('--channel', choices=('latest', 'stable'), default='latest',
                        help='Latest includes alpha releases; a downgrade is always rejected')
    args = parser.parse_args()
    try:
        update(args)
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f'codex update: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
