#!/usr/bin/env python3
"""Install verified builds of committed personal Codex source."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

from common import ROOT, CONFIG, REPO, TARGET, api, atomic_link, atomic_write, output, release_tag, run, sha256, source_revision, source_root, stable_release_pin, validate_stable_manifest

HOME = Path.home()
CODEX_HOME = Path(os.environ.get('CODEX_HOME', HOME / '.codex')).expanduser()
PACKAGES = CODEX_HOME / 'packages/standalone'
STATE = CODEX_HOME / 'state/codex-customizations'
STATE_FILE = STATE / 'installed.json'
LEGACY_REPO = 'romil-bijarnia/codex-customizations'


def read_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def platform_check():
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
        raise RuntimeError('This installation targets the current Mac running Linux/ARM64. A native macOS build is not selected automatically.')


def published(tag):
    result = subprocess.run(['gh', 'api', f'repos/{REPO}/releases/tags/{tag}'], capture_output=True, text=True)
    if result.returncode:
        if '404' in result.stderr or 'Not Found' in result.stdout:
            return None
        raise RuntimeError(result.stderr.strip())
    data = json.loads(result.stdout)
    if data.get('draft'):
        return None
    required = {'release-manifest.json', f'codex-custom-{TARGET}.tar.gz'}
    return data if required <= {a['name'] for a in data['assets']} else None


def download_release(tag, folder, commit):
    run(['gh', 'release', 'download', tag, '--repo', REPO, '--pattern', 'release-manifest.json', '--dir', folder])
    manifest = json.loads((folder / 'release-manifest.json').read_text())
    if (manifest['target'] != TARGET or manifest.get('repository') != REPO
            or manifest.get('source_commit') != source_revision(commit)):
        raise RuntimeError('The published build does not match this platform and exact personal source commit.')
    if manifest['release_tag'] != tag:
        raise RuntimeError('Published manifest has the wrong release identity.')
    validate_stable_manifest(manifest)
    asset = f'codex-custom-{TARGET}.tar.gz'
    if manifest['asset'] != asset:
        raise RuntimeError('Unexpected package asset name.')
    run(['gh', 'release', 'download', tag, '--repo', REPO, '--pattern', asset, '--dir', folder])
    archive = folder / asset
    if sha256(archive) != manifest['archive_sha256']:
        raise RuntimeError('Package checksum mismatch; the installed version was not changed.')
    return manifest, archive


def extract(archive, destination):
    with tarfile.open(archive, 'r:gz') as bundle:
        # The stdlib data filter rejects traversal and links outside the package.
        bundle.extractall(destination, filter='data')
    package = destination / 'package'
    if not (package / 'codex-package.json').is_file():
        raise RuntimeError('The download is not a complete Codex package.')
    return package


def verify_package(package, manifest):
    if manifest.get('upstream_channel') == 'stable':
        validate_stable_manifest(manifest)
    metadata = json.loads((package / 'codex-package.json').read_text())
    if metadata['target'] != TARGET or metadata['version'] != manifest['version']:
        raise RuntimeError('Package metadata does not match the published build.')
    for name in ('codex', 'codex-exec-server', 'codex-code-mode-host'):
        if sha256(package / 'bin' / name) != manifest['binary_sha256'][name]:
            raise RuntimeError(f'Package executable checksum mismatch: {name}')
    actual = output([package / 'bin/codex', '--version'])
    expected = manifest.get('compiled_version', manifest['version'])
    if 'compiled_version' not in manifest and manifest.get('build_kind') == 'remote-source-build':
        # Compatibility with the first published development package: Cargo's
        # version is constant within a series; package metadata carries the SHA.
        expected = re.sub(r'-dev\.\d{8}\+romil\.[0-9a-f]+\.[0-9a-f]+$', '-dev', expected)
    if actual != 'codex-cli ' + expected:
        raise RuntimeError(f'The downloaded executable reported an unexpected version: {actual}; expected {expected}')


def verify_local_checks(package, manifest):
    passed = []
    if 'bundled-visualization-renderer' not in manifest.get('required_local_checks', []):
        return passed
    helper = package / 'customizations/visualize-cli/visualize.py'
    before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location('candidate_visualize', helper)
        module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix='codex-render-check-') as temporary:
            root = Path(temporary);fragment = root / 'fragment.html'
            fragment.write_text('<main><h1>Codex update validation</h1></main>')
            result = module.make_viewer(fragment, codex_home=CODEX_HOME, viewer_root=root / 'viewers')
            if sha256(result['viewer']) != result['sha256']:
                raise RuntimeError('The installed visualization renderer failed its content check.')
        passed.append('bundled-visualization-renderer')
    except (OSError, ValueError, ImportError) as error:
        raise RuntimeError(f'The target-machine visualization check failed; the selected CLI is unchanged: {error}') from error
    finally:
        sys.dont_write_bytecode = before
    return passed


def select_package(package, manifest):
    """Check before selection; roll back in memory if committing the install fails."""
    verify_package(package, manifest)
    local_checks = verify_local_checks(package, manifest)
    current = PACKAGES / 'current'
    previous = current.resolve(strict=True)
    destination = PACKAGES / 'releases' / ('custom-' + manifest['release_tag'])
    if destination.exists():
        verify_package(destination, manifest)
    else:
        shutil.move(str(package), destination)
    helper_pairs = [
        ('api-cli/codex-api-chat.py', HOME / '.local/bin/codex-api-chat'),
        ('visualize-cli/visualize.py', CODEX_HOME / 'local-overrides/visualize-cli/visualize.py'),
    ]
    previous_helpers = []
    PACKAGES.mkdir(parents=True, exist_ok=True)
    with (PACKAGES / 'install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if current.resolve() != previous:
            raise RuntimeError('Another installer changed Codex; retry instead of overwriting it.')
        try:
            for relative, target in helper_pairs:
                source = destination / 'customizations' / relative
                if source.is_file():
                    previous_helpers.append((target, target.read_bytes() if target.exists() else None))
                    atomic_write(target, source.read_bytes(), 0o755)
            atomic_link(current, destination)
            installed = dict(manifest, package=str(destination), installed_at=time.time(), local_checks_passed=local_checks)
            atomic_write(STATE_FILE, json.dumps(installed, indent=2) + '\n')
        except BaseException:
            atomic_link(current, previous)
            for target, content in previous_helpers:
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    atomic_write(target, content, 0o755)
            raise
    return destination


def start_backend_rollout(package):
    STATE.mkdir(parents=True, exist_ok=True)
    log = (STATE / 'backend.log').open('a')
    try:
        subprocess.Popen([sys.executable, str(ROOT / 'backend.py'), str(package)],
                         env=dict(os.environ, CODEX_HOME=str(CODEX_HOME)),
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                         start_new_session=True)
    finally:
        log.close()


def refresh_source():
    root = source_root()
    branch = CONFIG['source_branch']
    if output(['git', '-C', root, 'branch', '--show-current']) != branch:
        raise RuntimeError(f'Check out {branch} before updating; a different branch or detached revision was not changed.')
    if output(['git', '-C', root, 'status', '--porcelain']):
        raise RuntimeError(f'Source has uncommitted changes in {root}; they were not overwritten.')
    before = output(['git', '-C', root, 'rev-parse', 'HEAD'])
    run(['git', '-C', root, 'fetch', '--quiet', 'origin',
         f'refs/heads/{branch}:refs/remotes/origin/{branch}'])
    run(['git', '-C', root, 'merge', '--quiet', '--ff-only', f'origin/{branch}'])
    return before != output(['git', '-C', root, 'rev-parse', 'HEAD'])


def status():
    print(json.dumps({'repository': REPO, 'channel': stable_release_pin()['upstream_channel'],
                      'source_branch': CONFIG['source_branch'],
                      'upstream_release': CONFIG['upstream_release'],
                      'source_checkout': str(source_root()),
                      'checkout_commit': output(['git', '-C', source_root(), 'rev-parse', 'HEAD']),
                      'installed': read_state(), 'selected_package': str((PACKAGES / 'current').resolve()),
                      'source_checkout_required': True}, indent=2))


def install_launcher(replace_legacy=False):
    platform_check()
    selected = (PACKAGES / 'current/bin/codex').resolve(strict=True)
    launcher = HOME / '.local/bin/codex'
    own = ROOT / 'bin/codex'
    if launcher.exists() and launcher.resolve() not in (selected, own.resolve()):
        legacy = HOME / 'Work/codex-customizations/bin/codex'
        recognized = False
        if replace_legacy and launcher.is_symlink() and launcher.resolve() == legacy.resolve() and legacy.is_file():
            origin = output(['git', '-C', legacy.parent.parent, 'remote', 'get-url', 'origin'])
            recognized = (origin in (f'https://github.com/{LEGACY_REPO}', f'https://github.com/{LEGACY_REPO}.git',
                                     f'git@github.com:{LEGACY_REPO}.git')
                          and legacy.read_bytes() == own.read_bytes())
        if not recognized:
            raise RuntimeError(f'The existing launcher is not the selected personal Codex: {launcher}. '
                               'Use --replace-legacy only for the verified former package-repository launcher.')
    own.chmod(0o755)
    atomic_link(launcher, own)
    print(f'Installed the codex update launcher; ordinary Codex launches still execute {selected}.')


def installed_matches(state, commit=None):
    if not state:
        return False
    if commit is not None and state.get('source_commit') != source_revision(commit):
        return False
    try:
        verify_package((PACKAGES / 'current').resolve(strict=True), state)
        return True
    except (OSError, RuntimeError, KeyError, ValueError, subprocess.SubprocessError):
        return False


def update(args):
    platform_check()
    if not args.source_refreshed and refresh_source():
        os.execv(sys.executable, [sys.executable, str(ROOT / 'manage.py'), 'update', '--source-refreshed', *sys.argv[2:]])
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'update.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another codex update is running; the working CLI was not changed.')
        commit = source_revision(args.revision)
        tag = release_tag(commit)
        state = read_state()
        release = published(tag)
        info = {'source_commit': commit, 'source_branch': CONFIG['source_branch'], 'release_tag': tag,
                'build_available': bool(release), 'already_installed': state.get('release_tag') == tag and installed_matches(state, commit),
                'source_checkout_required': True, 'upstream_release': CONFIG['upstream_release'],
                'upstream_channel': stable_release_pin()['upstream_channel']}
        if args.check:
            print(json.dumps(info, indent=2))
            return
        if info['already_installed']:
            print('Codex is already current with your committed personal source.')
            return
        if not release:
            print('A matching build is not published yet. Requesting the automated remote build.', flush=True)
            # CLI handles nested JSON input without exposing credentials in arguments.
            run(['gh', 'workflow', 'run', 'personal-build.yml', '--repo', REPO,
                 '--ref', CONFIG['source_branch'], '-f', f'source={commit}'])
            if args.no_wait:
                print(f'Build requested. Run codex update again when ready. https://github.com/{REPO}/actions')
                return
            started = time.monotonic()
            last_message = None
            while not release:
                if time.monotonic() - started > args.timeout:
                    raise RuntimeError(f'Remote build is still pending: https://github.com/{REPO}/actions . Your installation is unchanged.')
                runs = api(f'repos/{REPO}/actions/workflows/personal-build.yml/runs?per_page=10')['workflow_runs']
                matching = [r for r in runs if commit[:12] in r.get('display_title', '')]
                if matching:
                    latest = matching[0]
                    message = latest['status'] + ' ' + latest['html_url']
                    if latest['status'] == 'completed' and latest.get('conclusion') not in ('success', 'skipped'):
                        other_active = any(r['status'] in ('queued', 'pending', 'in_progress', 'waiting') for r in runs)
                        if latest.get('conclusion') == 'cancelled' and other_active:
                            message = f"Request superseded; waiting for the build already running: https://github.com/{REPO}/actions"
                        else:
                            raise RuntimeError(f"The automated build failed ({latest.get('conclusion')}): {latest['html_url']} . Your installation is unchanged.")
                else:
                    message = f'Waiting for GitHub Actions: https://github.com/{REPO}/actions'
                if message != last_message:
                    print(message, flush=True)
                    last_message = message
                time.sleep(15)
                release = published(tag)
        PACKAGES.joinpath('releases').mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.codex-update-', dir=PACKAGES / 'releases') as temp:
            folder = Path(temp)
            print('Downloading the verified source-build package; no local compilation.', flush=True)
            manifest, archive = download_release(tag, folder, commit)
            if manifest['source_commit'] != commit:
                raise RuntimeError('Published source revision does not match the requested update.')
            destination = select_package(extract(archive, folder), manifest)
        start_backend_rollout(destination)
        print(f"Installed {manifest['version']} at {destination}. Existing sessions were not restarted; the shared backend will update after observed idle time.")


def main():
    parser = argparse.ArgumentParser(prog='codex', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    u = commands.add_parser('update', help='Install committed personal source based on its pinned stable upstream release')
    u.add_argument('--check', action='store_true', help='Show availability without requesting a build or installing')
    u.add_argument('--no-wait', action='store_true', help='Request a missing build and return without installing')
    u.add_argument('--revision', help='Exact personal source commit; default follows the personal branch, not bare upstream')
    u.add_argument('--timeout', type=int, default=7200, help='Maximum seconds to wait for a remote build')
    u.add_argument('--source-refreshed', action='store_true', help=argparse.SUPPRESS)
    commands.add_parser('status', help='Show the selected build and update channel')
    launcher = commands.add_parser('install-launcher', help='Enable codex update without changing the selected executable')
    launcher.add_argument('--replace-legacy', action='store_true', help='Replace only the verified launcher from the former package repository')
    args = parser.parse_args()
    try:
        {'update': lambda: update(args), 'status': status,
         'install-launcher': lambda: install_launcher(args.replace_legacy)}[args.command]()
    except (RuntimeError, OSError, subprocess.CalledProcessError, ValueError, KeyError) as error:
        print(f'codex update: {error}', file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
