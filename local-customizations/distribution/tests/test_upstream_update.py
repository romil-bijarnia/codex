"""Offline integration fixtures for custom sections and upstream changes."""
import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common
import upstream_update as updater


class UpstreamUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.release = {'tag_name': 'rust-v2.0.1-alpha.2', 'prerelease': True, 'draft': False,
                        'assets': [{'name': 'codex-package-' + common.CONFIG['resource_target'] + '.tar.gz',
                                    'digest': 'sha256:' + 'a' * 64}]}

    def git(self, root, *args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True,
                                       stderr=subprocess.PIPE).strip()

    def commit(self, root, message):
        self.git(root, 'add', '.')
        self.git(root, 'commit', '-qm', message)
        return self.git(root, 'rev-parse', 'HEAD')

    def fixture(self, overlapping=False):
        root = self.home / 'repo';root.mkdir()
        self.git(root, 'init', '-q', '-b', 'main')
        self.git(root, 'config', 'user.name', 'Fixture')
        self.git(root, 'config', 'user.email', 'fixture@example.invalid')
        lines = [f'line {i}\n' for i in range(40)]
        (root / 'native.rs').write_text(''.join(lines))
        base = self.commit(root, 'Upstream baseline')
        nested = root / 'local-customizations/distribution';nested.mkdir(parents=True)
        manifest = dict(common.CONFIG, upstream_commit=base)
        (nested / 'manifest.json').write_text(json.dumps(manifest))
        (root / 'custom_tool.rs').write_text('additional interface\n')
        local_lines = list(lines);local_lines[3] = 'custom permission behaviour\n'
        (root / 'native.rs').write_text(''.join(local_lines))
        custom = self.commit(root, 'Custom harness')
        self.git(root, 'checkout', '-q', '--detach', base)
        upstream_lines = list(lines)
        upstream_lines[3 if overlapping else 30] = 'new upstream feature\n'
        (root / 'native.rs').write_text(''.join(upstream_lines))
        upstream = self.commit(root, 'New upstream release')
        self.git(root, 'tag', self.release['tag_name'])
        self.git(root, 'checkout', '-q', 'main')
        return root, custom, upstream

    def prepare(self, root, custom, folder):
        real_run = common.run
        def local_fetch(args, **kwargs):
            args = list(args)
            if 'fetch' in args:
                args[-2] = root
            return real_run(args, **kwargs)
        # All Git operations are real; only the upstream network URL is replaced.
        with patch.object(updater, 'run', side_effect=local_fetch), \
             patch.dict('os.environ', GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
                        GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid'):
            return updater.prepare_candidate(root, custom, self.release, folder)

    def test_installed_other_branch_is_not_used_as_a_second_customization_source(self):
        root, custom, _ = self.fixture()
        home = self.home / 'profile'
        package = home / 'packages/standalone/releases/old'
        package.mkdir(parents=True)
        (package.parent.parent / 'current').symlink_to(package)
        (package / 'custom-build.json').write_text(json.dumps({
            'upstream_release': 'rust-v2.0.0', 'source_commit': 'f' * 40,
            'upstream_commit': 'e' * 40}))
        args = SimpleNamespace(channel='latest', check=False, prepare_only=True)
        with patch.dict('os.environ', CODEX_HOME=str(home)), \
             patch.object(updater, 'latest_release', return_value=self.release), \
             patch.object(updater, 'source_root', return_value=root), \
             patch.object(updater, 'custom_revision', return_value=custom), \
             patch.object(updater, 'prepare_candidate', return_value=(root, custom)) as prepare, \
             contextlib.redirect_stdout(io.StringIO()):
            updater.update(args)
        prepare.assert_called_once_with(root, custom, self.release,
            home / 'state/codex-customizations/updates' / (self.release['tag_name'] + '-' + custom[:12]))

    def test_other_source_branch_is_not_implicitly_published_to_main(self):
        root, _, _ = self.fixture()
        self.git(root, 'checkout', '-q', '-b', 'unrelated')
        with patch.object(updater, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'configured main'):
                updater.custom_revision(root)
            run.assert_not_called()

    def test_latest_is_semantic_not_api_order_or_publication_time(self):
        old = dict(self.release, tag_name='rust-v2.0.1-alpha.1')
        other_series = dict(self.release, tag_name='rust-v1.99.0-alpha.99')
        with patch.object(updater, 'api', return_value=[old, other_series, self.release]):
            self.assertEqual(updater.latest_release('latest'), self.release)
        stable = dict(self.release, tag_name='rust-v2.0.0', prerelease=False)
        with patch.object(updater, 'api', return_value=[self.release, stable]):
            self.assertEqual(updater.latest_release('stable'), stable)
        self.assertGreater(updater.version_key('rust-v2.0.1'), updater.version_key('rust-v2.0.1-alpha.99'))
        self.assertGreater(updater.version_key('rust-v2.0.1-alpha.10'), updater.version_key('rust-v2.0.1-alpha.9'))

    def test_temporary_release_lookup_failure_is_retried_locally(self):
        failure = subprocess.CalledProcessError(1, ['gh', 'api'])
        with patch.object(updater, 'api', side_effect=[failure, [self.release]]) as api, \
             patch.object(updater.time, 'sleep') as sleep, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(updater.latest_release('latest'), self.release)
        self.assertEqual(api.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_failed_atomic_patch_still_reports_its_conflicting_paths(self):
        diagnostic = (b"Applied patch to 'native.rs' with conflicts.\n"
                      b'error: patch failed: removed.rs:1\n')
        with patch.object(updater, 'output', return_value=''):
            self.assertEqual(updater.conflicted_paths(self.home, diagnostic), ['native.rs', 'removed.rs'])

    def test_same_file_keeps_custom_section_and_new_upstream_section(self):
        root, custom, upstream = self.fixture()
        source, commit = self.prepare(root, custom, self.home / 'update')
        content = (source / 'native.rs').read_text()
        self.assertIn('custom permission behaviour', content)
        self.assertIn('new upstream feature', content)
        self.assertEqual((source / 'custom_tool.rs').read_text(), 'additional interface\n')
        self.assertEqual(self.git(root, 'rev-parse', 'HEAD'), custom)
        self.assertEqual(self.git(root, 'status', '--porcelain'), '')
        self.assertEqual(self.git(source, 'rev-list', '--parents', '-n', '1', commit).split(),
                         [commit, custom, upstream])
        manifest = json.loads((source / 'local-customizations/distribution/manifest.json').read_text())
        self.assertEqual(manifest['upstream_commit'], upstream)
        self.assertEqual(manifest['upstream_channel'], 'alpha')
        self.assertEqual(manifest['resource_sha256'], 'a' * 64)

    def test_overlapping_section_reports_conflict_without_choosing_a_side(self):
        root, custom, _ = self.fixture(overlapping=True)
        folder = self.home / 'update'
        with self.assertRaisesRegex(RuntimeError, 'No side was discarded'):
            self.prepare(root, custom, folder)
        result = json.loads((folder / 'result.json').read_text())
        self.assertEqual(result['status'], 'source_conflict')
        self.assertEqual(result['conflicts'], ['native.rs'])
        self.assertFalse(result['installed'])
        self.assertEqual(self.git(root, 'rev-parse', 'HEAD'), custom)
        self.assertIn('custom permission behaviour', (root / 'native.rs').read_text())
        with patch.object(updater, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'recorded source conflict'):
                updater.prepare_candidate(root, custom, self.release, folder)
            run.assert_not_called()

    def test_repeated_preparation_reuses_clean_exact_candidate(self):
        root, custom, _ = self.fixture()
        folder = self.home / 'update'
        first = self.prepare(root, custom, folder)
        with patch.object(updater, 'run') as run:
            self.assertEqual(updater.prepare_candidate(root, custom, self.release, folder), first)
            run.assert_not_called()

    def test_downgrade_and_unknown_provenance_are_rejected(self):
        package = self.home / 'selected';package.mkdir()
        with self.assertRaisesRegex(RuntimeError, 'source receipt'):
            updater.check_downgrade(self.release, package)
        receipt = package / 'custom-build.json'
        receipt.write_text(json.dumps({'upstream_release': 'rust-v3.0.0'}))
        with self.assertRaisesRegex(RuntimeError, 'downgrade'):
            updater.check_downgrade(self.release, package)
        receipt.write_text(json.dumps({'upstream_release': 'rust-v2.0.0'}))
        self.assertEqual(updater.check_downgrade(self.release, package)['upstream_release'], 'rust-v2.0.0')

    def test_resource_without_exact_digest_is_not_accepted(self):
        for assets in ([], [dict(self.release['assets'][0], digest=None)], self.release['assets'] * 2):
            with self.subTest(assets=assets), self.assertRaises(RuntimeError):
                updater.resource_digest(dict(self.release, assets=assets))

    def test_alpha_pin_and_manifest_keep_exact_identity_and_voice_checks(self):
        config = dict(common.CONFIG, upstream_channel='alpha', upstream_release=self.release['tag_name'])
        with patch.dict(common.CONFIG, config):
            pin = common.release_pin()
            manifest = dict(pin, source_commit='b' * 40, voice_build_commit='b' * 40,
                            version=common.package_version('b' * 40),
                            official_resources_tag=self.release['tag_name'],
                            check_suites=['voice-runtime'], required_local_checks=['voice-runtime'])
            common.validate_release_manifest(manifest)
            for key, value in [('upstream_commit', 'f' * 40), ('check_suites', []),
                               ('voice_build_commit', 'c' * 40), ('compiled_version', '2.0.0')]:
                with self.subTest(key=key), self.assertRaises(RuntimeError):
                    common.validate_release_manifest(dict(manifest, **{key: value}))
            with self.assertRaises(RuntimeError):
                common.stable_release_pin()


if __name__ == '__main__':
    unittest.main()
