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
import tempfile
import time
import tomllib

from common import CONFIG, REPO, api, atomic_write, output, run, sha256, source_root


RELEASE = re.compile(r'rust-v(\d+)\.(\d+)\.(\d+)(?:-alpha\.(\d+(?:\.\d+)*))?')
MANIFEST = 'local-customizations/distribution/manifest.json'
WORKSPACE_LOCK = 'codex-rs/Cargo.lock'
RESUME_FORMAT = 'explicit-base-merge-v1'


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


class WorkspaceLockConflict(RuntimeError):
    pass


def merge_status(source):
    return output(['git', '-C', source, 'diff', '--name-only', '--diff-filter=U']).splitlines()


def git_tree(source, revision):
    return output(['git', '-C', source, 'rev-parse', f'{revision}^{{tree}}'])


def manifest_at(source, custom):
    return json.loads(output(['git', '-C', source, 'show', f'{custom}:{MANIFEST}']))


def fetch_target(source, release):
    run(['git', '-C', source, 'fetch', '--quiet', '--no-tags',
         'https://github.com/' + CONFIG['upstream_repository'] + '.git',
         'refs/tags/' + release['tag_name']])
    return output(['git', '-C', source, 'rev-parse', 'FETCH_HEAD^{commit}'])


def commit_tree(source, tree, parents, message, custom):
    # Stable identity lets later attempts reuse the same verified candidate.
    stamp = output(['git', '-C', source, 'show', '-s', '--format=%aI', custom])
    env = dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
    args = [arg for parent in parents for arg in ('-p', parent)]
    return output(['git', '-C', source, 'commit-tree', tree, *args],
                  input=message + '\n', env=env)


def lock_entry(source, revision):
    raw = subprocess.check_output(['git', '-C', str(source), 'ls-tree', '-z',
                                   revision, '--', WORKSPACE_LOCK])
    if not raw:
        return None
    mode, _kind, oid = raw.split(b'\t', 1)[0].decode('ascii').split()
    return mode, oid


def normalized_custom_tree(source, base, custom, folder):
    # Use a private index: only workspace lock stamps are excluded here.
    # Real dependency deltas are reconciled separately after the source merge.
    with tempfile.TemporaryDirectory(prefix='index-', dir=folder) as temp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(temp) / 'index'))
        run(['git', '-C', source, 'read-tree', custom], env=env)
        old = lock_entry(source, base)
        if old:
            mode, oid = old
            run(['git', '-C', source, 'update-index', '--add',
                 '--cacheinfo', f'{mode},{oid},{WORKSPACE_LOCK}'], env=env)
        else:
            run(['git', '-C', source, 'update-index', '--force-remove', '--', WORKSPACE_LOCK], env=env)
        return output(['git', '-C', source, 'write-tree'], env=env)


def mark_lock_conflict(source, base, upstream, custom):
    # A semantic Cargo conflict has the same explicit staging contract as a
    # Git conflict; taking either original lock requires a deliberate git add.
    lines = [f'0 {"0" * len(upstream)}\t{WORKSPACE_LOCK}']
    for stage, revision in enumerate((base, upstream, custom), 1):
        entry = lock_entry(source, revision)
        if entry:
            mode, oid = entry
            lines.append(f'{mode} {oid} {stage}\t{WORKSPACE_LOCK}')
    run(['git', '-C', source, 'update-index', '--index-info'],
        input='\n'.join(lines) + '\n', text=True)


def reconcile_workspace_lock(source, base, custom, folder, *, upstream, resolved=False):
    """Merge real lock changes separately from generated workspace version stamps."""
    from lockfile import LockfileMergeConflict, merge_lockfile
    path = 'codex-rs/Cargo.lock'
    manifest = source / 'codex-rs/Cargo.toml'
    if not manifest.is_file() or not (source / path).is_file():
        return
    if resolved:
        tomllib.loads((source / path).read_text())  # Refuse an unfinished resolution.
    toolchain = tomllib.loads((source / 'codex-rs/rust-toolchain.toml').read_text())['toolchain']['channel']
    run(['rustup', 'toolchain', 'install', toolchain, '--profile', 'minimal'])
    cargo = output(['rustup', 'which', '--toolchain', toolchain, 'cargo'])
    env = dict(os.environ, RUSTUP_TOOLCHAIN=toolchain,
               RUSTC=output(['rustup', 'which', '--toolchain', toolchain, 'rustc']))
    env['PATH'] = str(Path(cargo).parent) + os.pathsep + env['PATH']
    if not resolved:
        base_text = output(['git', '-C', source, 'show', f'{base}:{path}']) + '\n'
        custom_text = output(['git', '-C', source, 'show', f'{custom}:{path}']) + '\n'
        custom_manifest = tomllib.loads(output(['git', '-C', source, 'show', f'{custom}:codex-rs/Cargo.toml']))
        metadata = json.loads(output([cargo, 'metadata', '--offline', '--no-deps', '--format-version', '1'],
                                     cwd=manifest.parent, env=env))
        members = set(metadata['workspace_members'])
        names = {p['name'] for p in metadata['packages'] if p['id'] in members}
        try:
            merged = merge_lockfile(base_text, custom_text, output(['git', '-C', source, 'show', f'{upstream}:{path}']) + '\n',
                                    custom_manifest['workspace']['package']['version'], names)
        except LockfileMergeConflict as error:
            (source / path).write_text(error.merged_text)
            raise WorkspaceLockConflict(f'Genuine custom dependency conflict in {path}; no package was installed.') from error
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


def receipt_matches(report, custom, release):
    return (report.get('custom_source') == custom
            and report.get('upstream_release') == release['tag_name']
            and report.get('customization_policy') == 'personal-fork-only')


def write_receipt(folder, report):
    atomic_write(folder / 'result.json', json.dumps(report, indent=2) + '\n')


def resume_candidate(source, folder, custom, release, report):
    if report.get('merge_format') != RESUME_FORMAT:
        raise RuntimeError('This older conflict has no recorded merge index; it cannot be continued.')
    merge_head = subprocess.run(['git', '-C', str(source), 'rev-parse', '--verify', 'MERGE_HEAD'],
                                capture_output=True, text=True, check=False)
    if (report.get('checkout') != str(source)
            or report.get('resource_sha256') != resource_digest(release)
            or report.get('custom_base') != manifest_at(source, custom)['upstream_commit']
            or output(['git', '-C', source, 'rev-parse', 'HEAD']) != report.get('staging_head')
            or merge_head.returncode or merge_head.stdout.strip() != report.get('staging_merge_head')):
        raise RuntimeError('The prepared merge or its pinned release no longer matches its receipt. Nothing was installed.')
    if merge_status(source):
        raise RuntimeError(f'Resolve and stage all conflicts in {source} before continuing.')
    if (output(['git', '-C', source, 'diff', '--name-only'])
            or output(['git', '-C', source, 'ls-files', '--others', '--exclude-standard'])):
        raise RuntimeError(f'Stage the intended resolutions and remove unrelated untracked files in {source} before continuing.')
    checked = subprocess.run(['git', '-C', str(source), 'diff', '--cached', '--check'],
                             env=dict(os.environ, LC_ALL='C'), capture_output=True, text=True, check=False)
    if 'leftover conflict marker' in checked.stdout + checked.stderr:
        raise RuntimeError(f'Staged files in {source} still contain conflict markers.')
    if fetch_target(source, release) != report.get('upstream_commit'):
        raise RuntimeError('The recorded upstream tag moved. The checkout was not overwritten.')
    return finish_candidate(source, folder, custom, release, report)


def finish_candidate(source, folder, custom, release, report):
    original = manifest_at(source, custom)
    base, upstream = report['custom_base'], report['upstream_commit']
    resolved_lock = report.get('conflict_kind') == 'workspace-dependencies'
    try:
        reconcile_workspace_lock(source, base, custom, folder, upstream=upstream, resolved=resolved_lock)
    except WorkspaceLockConflict as error:
        mark_lock_conflict(source, base, upstream, custom)
        report.update(status='source_conflict', conflicts=[WORKSPACE_LOCK],
                      conflict_kind='workspace-dependencies', error=str(error))
        write_receipt(folder, report)
        raise RuntimeError(f'{error} Resolve and stage {source / WORKSPACE_LOCK}, then use codex update --continue.') from error
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        report.update(status='source_conflict', conflicts=[WORKSPACE_LOCK], error=str(error))
        write_receipt(folder, report)
        raise

    original.update(upstream_channel='alpha' if release['prerelease'] else 'stable',
                    upstream_release=release['tag_name'], upstream_commit=upstream,
                    resource_sha256=resource_digest(release))
    atomic_write(source / MANIFEST, json.dumps(original, indent=2) + '\n')
    run(['git', '-C', source, 'add', '--', MANIFEST])
    tree = output(['git', '-C', source, 'write-tree'])
    commit = commit_tree(source, tree, [custom, upstream],
                         f"Integrate {release['tag_name']} with the custom harness", custom)
    run(['git', '-C', source, 'reset', '--quiet', '--hard', commit])
    for key in ('conflicts', 'conflict_kind', 'error'):
        report.pop(key, None)
    report.update(status='prepared', source_commit=commit)
    write_receipt(folder, report)
    return source, commit


def prepare_candidate(root, custom, release, folder, *, continuation=False):
    """Merge the fork changes relative to the recorded release, even on a different upstream lineage."""
    source = folder / 'source'
    report_path = folder / 'result.json'
    if source.exists():
        if report_path.is_file():
            report = json.loads(report_path.read_text())
            if receipt_matches(report, custom, release):
                if report.get('status') == 'source_conflict':
                    if continuation:
                        return resume_candidate(source, folder, custom, release, report)
                    names = '\n'.join(report.get('conflicts', [])[:12])
                    raise RuntimeError(f'The recorded source conflict still needs resolution ({len(report.get("conflicts", []))} files).\n'
                                       f'{names}\nCheckout: {source}\nDetails: {report_path}. '
                                       'Resolve and stage changes there, then run codex update --continue. '
                                       'The working harness is unchanged; no duplicate build was started.')
                commit = report.get('source_commit')
                if (commit and report.get('status') in ('prepared', 'installed')
                        and report.get('resource_sha256', resource_digest(release)) == resource_digest(release)
                        and output(['git', '-C', source, 'rev-parse', 'HEAD']) == commit
                        and not output(['git', '-C', source, 'status', '--porcelain'])):
                    if continuation and fetch_target(source, release) != report.get('upstream_commit'):
                        raise RuntimeError('The recorded upstream tag moved. The checkout was not overwritten.')
                    return source, commit
        raise RuntimeError(f'An update checkout already exists: {source}. Its contents were not overwritten.')
    if continuation:
        raise RuntimeError('There is no recorded update checkout to continue.')

    folder.mkdir(parents=True, exist_ok=True)
    run(['git', 'clone', '--quiet', '--shared', '--no-checkout', root, source])
    run(['git', '-C', source, 'remote', 'set-url', 'origin', f'https://github.com/{REPO}.git'])
    base = manifest_at(source, custom)['upstream_commit']
    upstream = fetch_target(source, release)
    custom_tree = normalized_custom_tree(source, base, custom, folder)
    if custom_tree == git_tree(source, base):
        raise RuntimeError('The custom delta is empty; refusing to substitute bare upstream for the harness.')

    # Git uses exact recorded base B for both sides, not the merge-base that
    # history happens to imply. The final commit still uses the original parents.
    staging_head = commit_tree(source, git_tree(source, upstream), [base], 'Upstream release tree', custom)
    staging_merge_head = commit_tree(source, custom_tree, [base], 'Personal fork delta tree', custom)
    run(['git', '-C', source, '-c', 'advice.detachedHead=false', 'checkout', '--quiet', '--detach', staging_head])
    report = {'custom_source': custom, 'custom_base': base, 'upstream_release': release['tag_name'],
              'upstream_commit': upstream, 'checkout': str(source), 'installed': False,
              'resource_sha256': resource_digest(release), 'merge_format': RESUME_FORMAT,
              'staging_head': staging_head, 'staging_merge_head': staging_merge_head,
              'customization_policy': 'personal-fork-only'}
    merged = subprocess.run(['git', '-C', str(source), '-c', 'rerere.enabled=false',
                             '-c', 'core.hooksPath=/dev/null', '-c', 'merge.conflictStyle=diff3',
                             'merge', '--no-commit', '--no-ff', '--no-edit', staging_merge_head],
                            capture_output=True, check=False)
    atomic_write(folder / 'integration.log', merged.stdout + merged.stderr)
    if merged.returncode:
        conflicts = merge_status(source)
        if not conflicts:
            raise RuntimeError(f'Git could not prepare the merge; see {folder / "integration.log"}. Nothing was installed.')
        report.update(status='source_conflict', conflicts=conflicts, conflict_kind='git')
        write_receipt(folder, report)
        names = '\n'.join(conflicts[:12])
        raise RuntimeError(f'Upstream overlaps your custom changes. No side was discarded and nothing was installed.\n'
                           f'{names}\nResolve in {source}, stage the resolved files, then run codex update --continue.\n'
                           f'Details: {report_path}\nIntegration log: {folder / "integration.log"}')
    return finish_candidate(source, folder, custom, release, report)


def pending_release(state, custom, requested, channel):
    if requested:
        version_key(requested)
    matches = []
    for report_path in (state / 'updates').glob(f'*-{custom[:12]}/result.json'):
        report = json.loads(report_path.read_text())
        tag = report.get('upstream_release', '')
        if not RELEASE.fullmatch(tag) or (requested and tag != requested):
            continue
        if channel == 'stable' and '-alpha.' in tag:
            continue
        if (report.get('custom_source') == custom
                and report.get('customization_policy') == 'personal-fork-only'
                and report.get('status') in ('source_conflict', 'prepared')
                and report_path.parent.name == f'{tag}-{custom[:12]}'):
            matches.append(tag)
    if not matches:
        raise RuntimeError('No recorded update matches the selected personal source and release. No update was continued.')
    if len(matches) != 1:
        raise RuntimeError('Multiple recorded updates can be continued: ' + ', '.join(sorted(matches))
                           + '. Specify codex update --continue RELEASE.')
    tag = matches[0]
    release = api(f"repos/{CONFIG['upstream_repository']}/releases/tags/{tag}")
    if release.get('tag_name') != tag or release.get('draft'):
        raise RuntimeError(f'The recorded release {tag} is no longer published.')
    return release


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
        root = source_root()
        continuation = getattr(args, 'continue_update', None)
        if continuation is not None:
            custom = custom_revision(root)
            release = pending_release(state, custom, continuation, args.channel)
        else:
            release = latest_release(args.channel)
        installed = check_downgrade(release, selected)
        digest = resource_digest(release)
        if args.check:
            print(json.dumps({'upstream_release': release['tag_name'], 'resource_sha256': digest,
                              'selected_package': str(selected), 'installed': installed,
                              'custom_source': output(['git', '-C', root, 'rev-parse', 'HEAD']),
                              'customization_policy': 'personal-fork-only',
                              'integration_evaluated': False, 'real_model_requests': 0}, indent=2))
            return
        if continuation is None:
            custom = custom_revision(root)
        if continuation is None and (installed.get('upstream_release') == release['tag_name']
                and installed_source_matches(root, installed, custom)):
            checksums = installed.get('binary_sha256', {})
            if (checksums and all(sha256(selected / 'bin' / name) == checksums.get(name)
                                  for name in ('codex', 'codex-exec-server', 'codex-code-mode-host'))):
                if args.prepare_only:
                    print(f"Your verified custom harness is already current with {release['tag_name']}. No build, install or backend handover was started.")
                else:
                    manage.start_backend_rollout(selected)
                    print(f"Your custom harness is already current with {release['tag_name']}. The idle backend handover is being checked.")
                return
        identity = release['tag_name'] + '-' + custom[:12]
        folder = state / 'updates' / identity
        if continuation is None:
            source, commit = prepare_candidate(root, custom, release, folder)
        else:
            source, commit = prepare_candidate(root, custom, release, folder, continuation=True)
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
    parser.add_argument('--continue', dest='continue_update', nargs='?', const='', metavar='RELEASE',
                        help='Resume a recorded merge or prepared candidate (optionally specify its release tag)')
    parser.add_argument('--channel', choices=('latest', 'stable'), default='latest',
                        help='Latest includes alpha releases; a downgrade is always rejected')
    args = parser.parse_args()
    if args.check and args.continue_update is not None:
        parser.error('--check cannot be combined with --continue')
    try:
        update(args)
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f'codex update: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
