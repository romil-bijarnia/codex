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

    def fixture(self, overlapping=False, sibling_upstream=False, renamed=False, workspace_lock=False):
        root = self.home / 'repo';root.mkdir()
        self.git(root, 'init', '-q', '-b', 'main')
        self.git(root, 'config', 'user.name', 'Fixture')
        self.git(root, 'config', 'user.email', 'fixture@example.invalid')
        lines = [f'line {i}\n' for i in range(40)]
        (root / 'native.rs').write_text(''.join(lines))
        if workspace_lock:
            (root / 'codex-rs').mkdir()
            (root / updater.WORKSPACE_LOCK).write_text('version = 1\n')
        common_base = self.commit(root, 'Common ancestor')
        old = list(lines)
        if sibling_upstream:
            old[15] = 'old release only\n'
            (root / 'native.rs').write_text(''.join(old))
            base = self.commit(root, 'Upstream baseline')
        else:
            base = common_base
        nested = root / 'local-customizations/distribution';nested.mkdir(parents=True)
        manifest = dict(common.CONFIG, upstream_commit=base)
        (nested / 'manifest.json').write_text(json.dumps(manifest))
        (root / 'custom_tool.rs').write_text('additional interface\n')
        local_lines = list(old);local_lines[3] = 'custom permission behaviour\n'
        (root / 'native.rs').write_text(''.join(local_lines))
        if workspace_lock:
            (root / updater.WORKSPACE_LOCK).write_text('version = 2\n')
        custom = self.commit(root, 'Custom harness')
        self.git(root, 'checkout', '-q', '--detach', common_base if sibling_upstream else base)
        upstream_lines = list(lines)
        upstream_lines[3 if overlapping else 30] = 'new upstream feature\n'
        (root / 'native.rs').write_text(''.join(upstream_lines))
        if renamed:
            self.git(root, 'mv', 'native.rs', 'moved.rs')
        if workspace_lock:
            (root / updater.WORKSPACE_LOCK).write_text('version = 3\n')
        upstream = self.commit(root, 'New upstream release')
        self.git(root, 'tag', self.release['tag_name'])
        self.git(root, 'checkout', '-q', 'main')
        return root, custom, upstream

    def prepare(self, root, custom, folder, continuation=False, release=None):
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
            return updater.prepare_candidate(root, custom, release or self.release, folder, continuation=continuation)

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

    def test_verification_only_commit_does_not_force_native_rebuild(self):
        root, native, _ = self.fixture()
        check = root / 'local-customizations/distribution/checks/owner.py'
        check.parent.mkdir();check.write_text('correct owner test environment\n')
        verified = self.commit(root, 'Fix only verification setup')
        installed = {'source_commit': native, 'verification_source_commit': verified}
        self.assertTrue(updater.installed_source_matches(root, installed, verified))
        code = root / 'codex-rs/native.rs';code.parent.mkdir();code.write_text('changed native code\n')
        changed = self.commit(root, 'Native behaviour change')
        installed['verification_source_commit'] = changed
        self.assertFalse(updater.installed_source_matches(root, installed, changed))

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

    def test_uses_the_recorded_base_even_when_official_releases_diverged(self):
        root, custom, upstream = self.fixture(sibling_upstream=True)
        source, commit = self.prepare(root, custom, self.home / 'update')
        content = (source / 'native.rs').read_text()
        self.assertIn('custom permission behaviour', content)
        self.assertIn('new upstream feature', content)
        self.assertNotIn('old release only', content)
        self.assertEqual(self.git(source, 'rev-list', '--parents', '-n', '1', commit).split(),
                         [commit, custom, upstream])

    def test_follows_an_upstream_rename_while_preserving_the_custom_change(self):
        root, custom, _ = self.fixture(renamed=True)
        source, _ = self.prepare(root, custom, self.home / 'update')
        self.assertFalse((source / 'native.rs').exists())
        self.assertIn('custom permission behaviour', (source / 'moved.rs').read_text())
        self.assertIn('new upstream feature', (source / 'moved.rs').read_text())

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

    def test_staged_resolution_prepares_the_exact_merge_and_reuses_it(self):
        root, custom, upstream = self.fixture(overlapping=True)
        folder = self.home / 'update'
        with self.assertRaisesRegex(RuntimeError, 'Resolve in'):
            self.prepare(root, custom, folder)
        source = folder / 'source'
        self.assertEqual(len(self.git(source, 'ls-files', '-u', '--', 'native.rs').splitlines()), 3)
        self.assertTrue((source / 'custom_tool.rs').is_file(), 'non-conflicting custom work is retained')
        with self.assertRaisesRegex(RuntimeError, 'Resolve and stage all conflicts'):
            self.prepare(root, custom, folder, continuation=True)
        native = source / 'native.rs'
        native.write_text(''.join(f'line {i}\n' for i in range(40)))
        native.write_text(native.read_text().replace('line 3\n', 'custom permissions and new upstream feature\n'))
        with self.assertRaisesRegex(RuntimeError, 'Resolve and stage all conflicts'):
            self.prepare(root, custom, folder, continuation=True)
        self.git(source, 'add', 'native.rs')
        (source / 'unrelated').write_text('not in review')
        with self.assertRaisesRegex(RuntimeError, 'untracked'):
            self.prepare(root, custom, folder, continuation=True)
        (source / 'unrelated').unlink()
        _, candidate = self.prepare(root, custom, folder, continuation=True)
        self.assertEqual(self.git(source, 'rev-list', '--parents', '-n', '1', candidate).split(),
                         [candidate, custom, upstream])
        self.assertEqual(self.prepare(root, custom, folder, continuation=True), (source, candidate))
        self.assertEqual(self.git(root, 'rev-parse', 'HEAD'), custom)
        self.assertEqual(self.git(root, 'status', '--porcelain'), '')
        result = json.loads((folder / 'result.json').read_text())
        self.assertEqual(result['status'], 'prepared')
        self.assertFalse(result['installed'])

    def test_resume_rejects_changed_release_resources_and_moved_tag(self):
        root, custom, _ = self.fixture(overlapping=True)
        folder = self.home / 'update'
        with self.assertRaisesRegex(RuntimeError, 'Resolve in'):
            self.prepare(root, custom, folder)
        source = folder / 'source'
        self.git(source, 'checkout', '--theirs', '--', 'native.rs')
        self.git(source, 'add', 'native.rs')
        changed = copy.deepcopy(self.release)
        changed['assets'][0]['digest'] = 'sha256:' + 'b' * 64
        with self.assertRaisesRegex(RuntimeError, 'pinned release'):
            self.prepare(root, custom, folder, continuation=True, release=changed)
        self.git(root, 'tag', '-f', self.release['tag_name'], custom)
        with self.assertRaisesRegex(RuntimeError, 'upstream tag moved'):
            self.prepare(root, custom, folder, continuation=True)

    def test_semantic_lock_conflict_is_put_in_the_index_and_explicitly_resumed(self):
        root, custom, _ = self.fixture(workspace_lock=True)
        folder = self.home / 'update'
        with patch.object(updater, 'reconcile_workspace_lock',
                          side_effect=updater.WorkspaceLockConflict('Dependencies conflict')):
            with self.assertRaisesRegex(RuntimeError, 'Resolve and stage'):
                self.prepare(root, custom, folder)
        source = folder / 'source'
        self.assertEqual(len(self.git(source, 'ls-files', '-u', '--', updater.WORKSPACE_LOCK).splitlines()), 3)
        self.assertEqual(json.loads((folder / 'result.json').read_text())['conflict_kind'],
                         'workspace-dependencies')
        with self.assertRaisesRegex(RuntimeError, 'Resolve and stage all conflicts'):
            self.prepare(root, custom, folder, continuation=True)
        (source / updater.WORKSPACE_LOCK).write_text('version = 4\n')
        self.git(source, 'add', updater.WORKSPACE_LOCK)
        with patch.object(updater, 'reconcile_workspace_lock') as lock:
            self.prepare(root, custom, folder, continuation=True)
            self.assertTrue(lock.call_args.kwargs['resolved'])
        self.assertEqual((source / updater.WORKSPACE_LOCK).read_text(), 'version = 4\n')

    def test_resume_uses_pinned_tag_even_when_a_newer_release_exists(self):
        custom = 'a' * 40
        (self.home / 'updates').mkdir()
        for tag, state in ((self.release['tag_name'], 'prepared'), ('rust-v2.0.1-alpha.3', 'source_conflict')):
            folder = self.home / 'updates' / f'{tag}-{custom[:12]}'
            folder.mkdir()
            (folder / 'result.json').write_text(json.dumps(dict(
                status=state, custom_source=custom, upstream_release=tag, customization_policy='personal-fork-only')))
        with self.assertRaisesRegex(RuntimeError, 'Specify codex update --continue RELEASE'):
            updater.pending_release(self.home, custom, '', 'latest')
        with patch.object(updater, 'api', return_value=self.release) as api:
            self.assertEqual(updater.pending_release(self.home, custom, self.release['tag_name'], 'latest'),
                             self.release)
            api.assert_called_once_with(f"repos/{common.CONFIG['upstream_repository']}/releases/tags/{self.release['tag_name']}")

    def test_same_release_prepare_only_does_not_request_backend_handover(self):
        import manage
        root, custom, _ = self.fixture()
        home = self.home / 'profile'
        package = home / 'packages/standalone/releases/old'
        package.mkdir(parents=True)
        (package.parent.parent / 'current').symlink_to(package)
        (package / 'custom-build.json').write_text(json.dumps({
            'upstream_release': self.release['tag_name'], 'source_commit': custom,
            'binary_sha256': {name: 'c' for name in ('codex', 'codex-exec-server', 'codex-code-mode-host')}}))
        with patch.dict('os.environ', CODEX_HOME=str(home)), \
             patch.object(updater, 'latest_release', return_value=self.release), \
             patch.object(updater, 'source_root', return_value=root), \
             patch.object(updater, 'custom_revision', return_value=custom), \
             patch.object(updater, 'sha256', return_value='c'), \
             patch.object(updater, 'prepare_candidate') as prepare, \
             patch.object(manage, 'start_backend_rollout') as backend, \
             contextlib.redirect_stdout(io.StringIO()):
            updater.update(SimpleNamespace(channel='latest', check=False, prepare_only=True))
        backend.assert_not_called()
        prepare.assert_not_called()

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
