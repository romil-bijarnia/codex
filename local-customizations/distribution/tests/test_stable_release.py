"""Offline checks for the exact stable source, resource and version boundary."""
import copy
import contextlib
import io
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build
import common
import manage


class StableReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.commit = 'a' * 40
        self.pin = common.stable_release_pin()
        self.resource_name = 'codex-package-' + common.CONFIG['resource_target'] + '.tar.gz'

    def release(self, digest):
        return {'tag_name': self.pin['upstream_release'], 'draft': False,
                'prerelease': False, 'assets': [{'name': self.resource_name,
                                               'digest': 'sha256:' + digest}]}

    def manifest(self):
        return dict(build.plan(self.commit), repository=common.REPO,
                    version=common.stable_package_version(self.commit),
                    official_resources_tag=self.pin['upstream_release'],
                    check_suites=['voice-runtime'], required_local_checks=['voice-runtime'],
                    asset='codex-custom-' + common.TARGET + '.tar.gz')

    def test_plan_uses_pinned_stable_version_not_newest_release(self):
        with patch.object(build, 'api') as api:
            plan = build.plan(self.commit)
            self.assertEqual(plan['upstream_release'], 'rust-v0.167.2')
            self.assertEqual(plan['upstream_commit'], 'ce21072c90d32c40af7a55b60b2fb35c84a43a6a')
            self.assertEqual(plan['compiled_version'], '0.167.2')
            self.assertEqual(common.stable_package_version(self.commit), '0.167.2+romil.aaaaaaaaaaaa')
            api.assert_not_called()
        for change in ({'upstream_channel': 'alpha'},
                       {'upstream_release': 'rust-v0.169.0-alpha.6'},
                       {'upstream_commit': 'main'}, {'resource_sha256': ''}):
            with self.subTest(change=change), patch.dict(common.CONFIG, change):
                with self.assertRaises(RuntimeError):common.stable_release_pin()

    def test_upstream_base_verifies_only_the_exact_tag_commit(self):
        with patch.object(build, 'run') as run, \
             patch.object(build, 'output', return_value=self.pin['upstream_commit']) as output:
            self.assertEqual(build.upstream_base(self.root), self.pin['upstream_commit'])
        run.assert_called_once_with([
            'git', '-C', self.root, 'fetch', '--quiet', '--no-tags',
            'https://github.com/openai/codex.git', 'refs/tags/rust-v0.167.2'])
        output.assert_called_once_with(['git', '-C', self.root, 'rev-parse', 'FETCH_HEAD^{commit}'])
        with patch.object(build, 'run'), patch.object(build, 'output', return_value='f' * 40):
            with self.assertRaisesRegex(RuntimeError, 'pinned commit'):
                build.upstream_base(self.root)

    def test_resources_download_exact_release_and_verify_pinned_digest(self):
        content = b'official stable resource package fixture'
        digest = hashlib.sha256(content).hexdigest()
        release = self.release(digest)
        def download(args):
            self.assertEqual(args[3], 'rust-v0.167.2')
            self.assertEqual(args[args.index('--pattern') + 1], self.resource_name)
            (self.root / self.resource_name).write_bytes(content)
        with patch.dict(common.CONFIG, resource_sha256=digest), \
             patch.object(build, 'api', return_value=release) as api, \
             patch.object(build, 'run', side_effect=download):
            actual, path = build.official_resources(self.root)
        self.assertEqual(actual, release)
        self.assertEqual(path.read_bytes(), content)
        api.assert_called_once_with('repos/openai/codex/releases/tags/rust-v0.167.2')

    def test_wrong_release_metadata_is_rejected_before_download(self):
        good = self.release(self.pin['official_resources_sha256'])
        variants = []
        for field, value in (('prerelease', True), ('draft', True),
                             ('tag_name', 'rust-v0.169.0-alpha.6'),
                             ('tag_name', 'rust-v0.168.0')):
            item = copy.deepcopy(good);item[field] = value;variants.append(item)
        item = copy.deepcopy(good);item['assets'][0]['digest'] = 'sha256:' + 'f' * 64;variants.append(item)
        item = copy.deepcopy(good);item['assets'] = [];variants.append(item)
        item = copy.deepcopy(good);item['assets'] *= 2;variants.append(item)
        for release in variants:
            with self.subTest(release=release), patch.object(build, 'api', return_value=release), \
                 patch.object(build, 'run') as run:
                with self.assertRaises(RuntimeError):build.official_resources(self.root)
                run.assert_not_called()

    def test_bad_resource_bytes_are_rejected(self):
        release = self.release(self.pin['official_resources_sha256'])
        with patch.object(build, 'api', return_value=release), \
             patch.object(build, 'run', side_effect=lambda args: (self.root / self.resource_name).write_bytes(b'wrong')):
            with self.assertRaisesRegex(RuntimeError, 'pinned checksum'):
                build.official_resources(self.root)

    def test_development_workspace_cannot_be_relabelled_as_stable(self):
        path = self.root / 'codex-rs/Cargo.toml';path.parent.mkdir()
        for version in ('0.167.2', '0.167.0', '0.0.0', '0.169.0-dev'):
            original = '[workspace.package]\nversion = "' + version + '"\n'
            path.write_text(original)
            with self.subTest(version=version):
                if version == '0.167.2':build.verify_workspace_version(self.root, '0.167.2')
                else:
                    with self.assertRaisesRegex(RuntimeError, 'refusing to relabel'):
                        build.verify_workspace_version(self.root, '0.167.2')
                self.assertEqual(path.read_text(), original)

    def test_stable_package_provenance_rejects_inconsistent_version_or_base(self):
        good = self.manifest()
        common.validate_stable_manifest(good)
        for field, value in (('compiled_version', '0.169.0-dev'),
                             ('upstream_commit', 'f' * 40),
                             ('voice_build_commit', 'f' * 40),
                             ('official_resources_tag', 'rust-v0.169.0-alpha.6'),
                             ('upstream_channel', 'development'),
                             ('version', '0.167.0-dev'),
                             ('source_commit', None), ('check_suites', []),
                             ('required_local_checks', [])):
            with self.subTest(field=field):
                bad = dict(good);bad[field] = value
                with self.assertRaises(RuntimeError):common.validate_stable_manifest(bad)

    def test_downloader_refuses_dev_manifest_before_downloading_executable(self):
        manifest = self.manifest();manifest['compiled_version'] = '0.169.0-dev'
        def download(args):
            self.assertEqual(args[args.index('--pattern') + 1], 'release-manifest.json')
            (self.root / 'release-manifest.json').write_text(json.dumps(manifest))
        with patch.object(manage, 'run', side_effect=download) as run:
            with self.assertRaisesRegex(RuntimeError, 'pinned stable'):
                manage.download_release(manifest['release_tag'], self.root, self.commit)
        self.assertEqual(run.call_count, 1)
        self.assertFalse((self.root / manifest['asset']).exists())

    def test_downloaded_stable_manifest_and_archive_are_verified(self):
        content = b'custom source package fixture'
        manifest = self.manifest();manifest['archive_sha256'] = hashlib.sha256(content).hexdigest()
        def download(args):
            name = args[args.index('--pattern') + 1]
            if name == 'release-manifest.json':
                (self.root / name).write_text(json.dumps(manifest))
            else:
                self.assertEqual(name, manifest['asset']);(self.root / name).write_bytes(content)
        with patch.object(manage, 'run', side_effect=download) as run:
            actual, archive = manage.download_release(manifest['release_tag'], self.root, self.commit)
        self.assertEqual(actual, manifest)
        self.assertEqual(archive.read_bytes(), content)
        self.assertEqual(run.call_count, 2)

    def test_status_separates_stable_target_from_legacy_installed_version(self):
        captured = io.StringIO()
        with patch.object(manage, 'read_state', return_value={'version': '0.169.0-dev'}), \
             patch.object(manage, 'source_root', return_value=self.root), \
             patch.object(manage, 'output', return_value=self.commit), \
             contextlib.redirect_stdout(captured):
            manage.status()
        status = json.loads(captured.getvalue())
        self.assertEqual(status['channel'], 'stable')
        self.assertEqual(status['upstream_release'], 'rust-v0.167.2')
        self.assertEqual(status['installed']['version'], '0.169.0-dev')

    def test_stable_binary_version_remains_separate_from_custom_package_identity(self):
        package = self.root / 'package';(package / 'bin').mkdir(parents=True)
        manifest = self.manifest()
        (package / 'codex-package.json').write_text(json.dumps({'target': common.TARGET,
                                                               'version': manifest['version']}))
        for name in ('codex', 'codex-exec-server', 'codex-code-mode-host'):
            binary = package / 'bin' / name
            binary.write_text('#!/bin/sh\nprintf "codex-cli 0.167.2\\n"\n');binary.chmod(0o755)
        manifest['binary_sha256'] = {name: common.sha256(package / 'bin' / name)
                                   for name in ('codex', 'codex-exec-server', 'codex-code-mode-host')}
        with patch.object(manage, 'verify_voice_resources'):
            manage.verify_package(package, manifest)
        binary = package / 'bin/codex';binary.write_text('#!/bin/sh\nprintf "codex-cli 0.169.0-dev\\n"\n')
        manifest['binary_sha256']['codex'] = common.sha256(binary)
        with patch.object(manage, 'verify_voice_resources'), self.assertRaisesRegex(RuntimeError, 'unexpected version'):
            manage.verify_package(package, manifest)


if __name__ == '__main__':unittest.main()
