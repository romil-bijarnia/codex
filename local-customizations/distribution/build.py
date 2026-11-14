#!/usr/bin/env python3
"""Build and publish an exact committed personal Codex source revision."""
import argparse
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

from common import ROOT, CONFIG, REPO, TARGET, api, atomic_write, output, release_tag, run, sha256, source_revision, source_root, stable_release_pin, stable_package_version
from voice import VOICE_CHECK, assemble_voice_resources, check_voice_runtime, verify_voice_resources
from voice_build import build_voice_host


def plan(commit=None):
    commit = source_revision(commit)
    return {'source_commit': commit, 'voice_build_commit': commit, 'source_branch': CONFIG['source_branch'],
            'release_tag': release_tag(commit), 'target': TARGET, **stable_release_pin()}


def prepare(source, commit):
    commit = source_revision(commit)
    source = Path(source)
    if source.exists():
        raise RuntimeError(f'The disposable source destination already exists: {source}')
    source.parent.mkdir(parents=True, exist_ok=True)
    # Copy committed Git objects, never working-tree changes, and never patch
    # upstream during a build. This also keeps version stamping out of the
    # canonical checkout and works with a linked Git worktree as the input.
    run(['git', 'clone', '--quiet', '--no-checkout', '--no-hardlinks', source_root(), source])
    run(['git', '-C', source, 'remote', 'set-url', 'origin', 'https://github.com/' + REPO + '.git'])
    available = subprocess.run(['git', '-C', str(source), 'cat-file', '-e', commit + '^{commit}'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if available.returncode:
        run(['git', '-C', source, 'fetch', '--quiet', '--no-tags', 'origin', commit])
    run(['git', '-C', source, '-c', 'advice.detachedHead=false', 'checkout', '--quiet', '--detach', commit])
    manifest = source / 'local-customizations/distribution/manifest.json'
    if not manifest.is_file() or json.loads(manifest.read_text()).get('schema') != 2:
        raise RuntimeError('The requested revision is not a personal source build; bare upstream is not installed.')
    return source


def upstream_base(source):
    """Verify the pinned tag; prior development ancestry is not the source base."""
    pin = stable_release_pin()
    run(['git', '-C', source, 'fetch', '--quiet', '--no-tags',
         'https://github.com/' + CONFIG['upstream_repository'] + '.git',
         'refs/tags/' + pin['upstream_release']])
    actual = output(['git', '-C', source, 'rev-parse', 'FETCH_HEAD^{commit}'])
    if actual != pin['upstream_commit']:
        raise RuntimeError('The upstream stable tag does not resolve to the pinned commit.')
    return actual


def official_resources(folder):
    pin = stable_release_pin()
    release = api(f"repos/{CONFIG['upstream_repository']}/releases/tags/{pin['upstream_release']}")
    if (release.get('tag_name') != pin['upstream_release']
            or release.get('prerelease') is not False or release.get('draft') is not False):
        raise RuntimeError('The resource release is not the exact pinned public stable release.')
    name = 'codex-package-' + CONFIG['resource_target'] + '.tar.gz'
    assets = [asset for asset in release['assets'] if asset['name'] == name]
    if len(assets) != 1:
        raise RuntimeError('The pinned stable release has no unique matching resource package.')
    expected = pin['official_resources_sha256']
    if assets[0].get('digest') != 'sha256:' + expected:
        raise RuntimeError('The official stable resource digest differs from the pinned checksum.')
    run(['gh', 'release', 'download', pin['upstream_release'], '--repo',
         CONFIG['upstream_repository'], '--pattern', name, '--dir', folder])
    path = Path(folder) / name
    if sha256(path) != expected:
        raise RuntimeError('Official stable resource package failed its pinned checksum.')
    return release, path


def verify_workspace_version(source, version):
    # Stable tags already carry their release version. Do not turn a development
    # checkout into an apparently stable executable by changing only its label.
    import tomllib
    actual = tomllib.loads((Path(source) / 'codex-rs/Cargo.toml').read_text())['workspace']['package']['version']
    if actual != version:
        raise RuntimeError(f'Source workspace version is {actual}, not stable {version}; refusing to relabel it.')


def archive_package(package, output_dir, manifest):
    output_dir.mkdir(parents=True, exist_ok=True)
    asset = output_dir / f'codex-custom-{TARGET}.tar.gz'
    def public_package(info):
        if info.name == 'package/romil-build.json':
            return None  # Local source/log paths are not release artifacts.
        info.uid = info.gid = 0
        info.uname = info.gname = ''
        return info
    with tarfile.open(asset, 'w:gz', compresslevel=2) as bundle:
        bundle.add(package, arcname='package', filter=public_package)
        if not (package / 'custom-build.json').exists():
            content = (json.dumps(manifest, indent=2) + '\n').encode()
            info = tarfile.TarInfo('package/custom-build.json');info.size = len(content);info.mode = 0o644
            bundle.addfile(info, io.BytesIO(content))
    manifest = dict(manifest, asset=asset.name, archive_sha256=sha256(asset))
    (output_dir / 'release-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def package_manifest(package, build_plan, **extra):
    return dict(build_plan, version=json.loads((package / 'codex-package.json').read_text())['version'],
                binary_sha256={name: sha256(package / 'bin' / name) for name in ('codex', 'codex-exec-server', 'codex-code-mode-host')},
                repository=REPO, created_at=datetime.now(timezone.utc).isoformat(), **extra)


def publish(output_dir, manifest):
    tag = manifest['release_tag']
    body = (f"Source: {REPO}@{manifest['source_commit']}\n\n"
            f"Stable base: {manifest['upstream_release']} (openai/codex@{manifest['upstream_commit']})\n\n"
            "Built from the pinned stable release plus committed personal changes. Profile settings and running sessions are not replaced.\n")
    args = ['gh', 'release', 'create', tag, '--repo', REPO, '--target', manifest['source_commit'],
            '--latest=false', '--title', manifest['version'], '--notes', body,
            output_dir / manifest['asset'], output_dir / 'release-manifest.json']
    run(args)


def source_times(source, restore=False):
    # Cargo uses source mtimes. A fresh checkout must not invalidate every unchanged crate.
    cache = Path.home() / '.cache/codex-customizations/source-fingerprints.json'
    previous = {}
    if restore and cache.exists():
        try:previous = json.loads(cache.read_text())
        except (ValueError, OSError):pass
    records = {}
    raw = subprocess.check_output(['git','-C',str(source),'ls-files','-z'])
    for name in (p.decode() for p in raw.split(b'\0') if p):
        path = source / name
        if not path.is_file() or path.is_symlink():continue
        digest = sha256(path)
        if restore and previous.get(name, {}).get('sha256') == digest:
            stamp = previous[name]['mtime_ns'];os.utime(path, ns=(stamp,stamp))
        records[name] = {'sha256':digest,'mtime_ns':path.stat().st_mtime_ns}
    if not restore:atomic_write(cache,json.dumps(records))


def build(work, build_plan, output_dir):
    commit = source_revision(build_plan['source_commit'])
    pin = stable_release_pin()
    if any(build_plan.get(key) != value for key, value in pin.items()):
        raise RuntimeError("The build plan does not match this source revision's stable release pin.")
    if output(['git', '-C', source_root(), 'rev-parse', 'HEAD']) != commit:
        raise RuntimeError('Check out the requested source commit before building; its own recipe and checks must be used.')
    if output(['git', '-C', source_root(), 'status', '--porcelain']):
        raise RuntimeError('Commit or resolve source changes before building a reproducible package; no files were overwritten.')
    work.mkdir(parents=True, exist_ok=False)
    source = prepare(work / 'source', commit)
    cargo_version = pin['compiled_version']
    verify_workspace_version(source, cargo_version)
    build_plan = dict(build_plan, upstream_commit=upstream_base(source))
    release, resource_archive = official_resources(work)
    unpacked = work / 'official';unpacked.mkdir()
    with tarfile.open(resource_archive) as bundle:bundle.extractall(unpacked, filter='data')
    metadata_files = list(unpacked.rglob('codex-package.json'))
    if len(metadata_files) != 1:raise RuntimeError('Official package layout is ambiguous.')
    package = metadata_files[0].parent
    verify_voice_resources(package, pin['upstream_commit'])
    import tomllib
    toolchain = tomllib.loads((source / 'codex-rs/rust-toolchain.toml').read_text())['toolchain']['channel']
    run(['rustup', 'toolchain', 'install', toolchain, '--profile', 'minimal'])
    env = dict(os.environ, RUSTUP_TOOLCHAIN=toolchain, CODEX_REPO_ROOT=str(source),
               CARGO_PROFILE_RELEASE_DEBUG='0', CARGO_PROFILE_RELEASE_STRIP='symbols',
               CARGO_PROFILE_RELEASE_LTO='false', CARGO_PROFILE_RELEASE_CODEGEN_UNITS='16',
               STABLE_GIT_COMMIT=commit)
    env['CARGO_TARGET_DIR'] = str(Path.home() / '.cache/codex-customizations/target')
    env['PATH'] = str(Path(output(['rustup', 'which', '--toolchain', toolchain, 'cargo'])).parent) + os.pathsep + env['PATH']
    os.environ['CODEX_REPO_ROOT'] = str(source)
    sys.path.insert(0, str(source / 'scripts'))
    from codex_package.targets import TARGET_SPECS
    from codex_package.v8 import resolve_codex_v8_cargo_env
    env.update(resolve_codex_v8_cargo_env(TARGET_SPECS[TARGET], environ=env, cache_root=Path.home() / '.cache/codex-customizations/v8'))
    command = ['cargo', 'build', '--locked', '--release', '--jobs', os.environ.get('CARGO_BUILD_JOBS', '2')]
    for project, binary in [('codex-cli', 'codex'), ('codex-exec-server-cli', 'codex-exec-server'), ('codex-code-mode-host', 'codex-code-mode-host'), ('codex-bwrap', 'bwrap')]:
        command += ['-p', project, '--bin', binary]
    source_times(source, restore=True)
    try:
        run(command, cwd=source / 'codex-rs', env=env)
        helper = build_voice_host(source, package, env, work)
    finally:
        source_times(source)
    target = Path(env['CARGO_TARGET_DIR']) / 'release'
    for name in ('codex', 'codex-exec-server', 'codex-code-mode-host'):
        (package / 'bin' / name).unlink(missing_ok=True);shutil.copy2(target / name, package / 'bin' / name)
    shutil.copy2(helper, package / 'codex-resources/voice/bin/codex-voice-host')
    (package / 'codex-resources/bwrap').unlink(missing_ok=True);shutil.copy2(target / 'bwrap', package / 'codex-resources/bwrap')
    metadata = json.loads(metadata_files[0].read_text())
    metadata.update(target=TARGET, version=stable_package_version(commit))
    metadata_files[0].write_text(json.dumps(metadata, indent=2) + '\n')
    assemble_voice_resources(package, commit, pin['upstream_commit'])
    shutil.copytree(source / 'local-customizations', package / 'customizations')
    qa_home = work / 'qa-home';(qa_home / '.codex').mkdir(parents=True)
    models = json.loads((source / 'codex-rs/models-manager/models.json').read_text())
    if not any(m['slug'] == 'gpt-6.1-sol' for m in models['models']):raise RuntimeError('The upstream model catalog no longer contains the terminal fixture model.')
    (qa_home / '.codex/models_cache.json').write_text(json.dumps(models))
    for origin, destination in [('api-cli/codex-api-chat.py', qa_home / '.local/bin/codex-api-chat'), ('visualize-cli/visualize.py', qa_home / '.codex/local-overrides/visualize-cli/visualize.py')]:
        destination.parent.mkdir(parents=True, exist_ok=True);shutil.copy2(package / 'customizations' / origin, destination)
    python = Path(sys.executable)
    test_env = dict(os.environ, HOME=str(qa_home))
    test_env.pop('CODEX_HOME', None)
    voice_report = check_voice_runtime(package, commit, commit)
    voice_output = work / 'checks/voice-runtime.json';voice_output.parent.mkdir(parents=True, exist_ok=True)
    voice_output.write_text(json.dumps(voice_report, indent=2) + '\n')
    suites = [VOICE_CHECK]
    for name, script, extra in [('defaults','defaults-qa.py',[]),('daybreak-defaults','daybreak-defaults-qa.py',[]),('picker-display','picker-order-qa.py',['--display-only']),('integrations-portable','terminal-integrations-qa.py',['--without-bundled-renderer']),('regressions','cli-regression-qa.py',['--source',str(source)]),('mcp-approvals','mcp-review-qa.py',[]),('steering','steering-qa.py',[]),('fork-window','fork-window-qa.py',[])]:
        run([python, ROOT / 'checks' / script, package / 'bin/codex', work / 'checks' / name, *extra], env=test_env)
        suites.append(name)
    manifest = package_manifest(package, build_plan, check_suites=suites, real_model_requests=0,
                                kernel_activation_verified=False, compiled_version=cargo_version, required_local_checks=[VOICE_CHECK, 'bundled-visualization-renderer'], official_resources_tag=release['tag_name'], build_kind='remote-source-build')
    (package / 'custom-build.json').write_text(json.dumps(manifest, indent=2) + '\n')
    manifest = archive_package(package, output_dir, manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('plan');p.add_argument('--revision');p.add_argument('--out', type=Path, required=True)
    p = commands.add_parser('prepare');p.add_argument('--revision', required=True);p.add_argument('--source', type=Path, required=True)
    p = commands.add_parser('build');p.add_argument('--plan', type=Path, required=True);p.add_argument('--work', type=Path, required=True);p.add_argument('--out', type=Path, required=True);p.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    if args.command == 'plan':
        data = plan(args.revision);args.out.write_text(json.dumps(data, indent=2) + '\n')
        from manage import published
        exists = bool(published(data['release_tag']))
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as f:f.write(f"commit={data['source_commit']}\ntag={data['release_tag']}\nexists={str(exists).lower()}\n")
        print(json.dumps(dict(data, exists=exists), indent=2))
    elif args.command == 'prepare':
        prepare(args.source, args.revision);print('Checked out the exact committed personal source; no patches or merges applied.')
    else:
        manifest = build(args.work, json.loads(args.plan.read_text()), args.out)
        if args.publish:publish(args.out, manifest)
        print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
