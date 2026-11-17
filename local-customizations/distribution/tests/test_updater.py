"""Focused filesystem checks for installation safety and CLI argument forwarding."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import manage
import common
import build
import backend
import shutil
from common import sha256


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.profile = self.home / '.codex'
        self.packages = self.profile / 'packages/standalone'
        old = self.packages / 'releases/old';old.mkdir(parents=True)
        self.packages.joinpath('current').symlink_to(old)
        self.old = old
        self.profile.joinpath('config.toml').write_text('personal = "unchanged"\n')
        self.profile.joinpath('sessions').mkdir()
        self.profile.joinpath('sessions/history.jsonl').write_text('existing conversation\n')
        state = self.profile / 'state/codex-customizations'
        replacements = dict(HOME=self.home, CODEX_HOME=self.profile, PACKAGES=self.packages, STATE=state, STATE_FILE=state / 'installed.json')
        self.addCleanup(patch.stopall)
        patch.multiple(manage, **replacements).start()

    def candidate(self):
        package = self.home / 'candidate';(package / 'bin').mkdir(parents=True)
        for name in ('codex', 'codex-exec-server', 'codex-code-mode-host'):
            p = package / 'bin' / name;p.write_text('#!/bin/sh\nprintf "codex-cli 2.3.4\\n"\n');p.chmod(0o755)
        (package / 'codex-package.json').write_text(json.dumps({'target':manage.TARGET,'version':'2.3.4'}))
        manifest = {'target':manage.TARGET,'version':'2.3.4','release_tag':'test-package','binary_sha256':{n:sha256(package / 'bin' / n) for n in ('codex','codex-exec-server','codex-code-mode-host')}}
        return package, manifest

    def source_repository(self):
        root = self.home / 'source-repo';root.mkdir()
        subprocess.run(['git', 'init', '-q', '-b', 'development', str(root)], check=True)
        subprocess.run(['git', '-C', str(root), 'config', 'user.name', 'Test'], check=True)
        subprocess.run(['git', '-C', str(root), 'config', 'user.email', 'test@example.invalid'], check=True)
        (root / 'native.rs').write_text('upstream source\n')
        self.commit_source(root, 'Initial upstream')
        base = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
        nested = root / 'local-customizations/distribution';nested.mkdir(parents=True)
        (nested / 'manifest.json').write_text('{"schema":2}\n')
        (nested / 'build.py').write_text('committed build recipe\n')
        (root / '.gitignore').write_text('__pycache__/\n')
        (root / 'native.rs').write_text('personal source\n')
        self.commit_source(root, 'Personal source and recipe')
        return root, base

    def commit_source(self, root, message):
        subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(root), 'commit', '-qm', message], check=True)
        return subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()

    def test_success_preserves_profile_and_history(self):
        package, manifest = self.candidate()
        selected = manage.select_package(package, manifest)
        self.assertEqual((self.packages / 'current').resolve(), selected)
        self.assertEqual((self.profile / 'config.toml').read_text(), 'personal = "unchanged"\n')
        self.assertEqual((self.profile / 'sessions/history.jsonl').read_text(), 'existing conversation\n')
        self.assertTrue(self.old.is_dir())

    def test_invalid_download_does_not_change_working_selection(self):
        package, manifest = self.candidate();manifest['binary_sha256']['codex'] = '0' * 64
        with self.assertRaises(RuntimeError):manage.select_package(package, manifest)
        self.assertEqual((self.packages / 'current').resolve(), self.old)
        self.assertFalse(manage.STATE_FILE.exists())

    def test_stale_update_receipt_does_not_hide_replaced_native_files(self):
        package, manifest = self.candidate()
        self.assertFalse(manage.installed_matches(manifest))
        selected = manage.select_package(package, manifest)
        self.assertTrue(manage.installed_matches(manifest))
        (selected / 'bin/codex').write_text('a replaced native executable')
        self.assertFalse(manage.installed_matches(manifest))

    def test_already_installed_update_still_reconciles_backend(self):
        commit = 'a' * 40
        tag = common.release_tag(commit)
        manage.STATE.mkdir(parents=True)
        manage.STATE_FILE.write_text(json.dumps({'release_tag': tag}))
        args = SimpleNamespace(source_refreshed=True, revision=commit, check=False)
        with patch.object(manage, 'platform_check'), \
             patch.object(manage, 'published', return_value={}), \
             patch.object(manage, 'installed_matches', return_value=True), \
             patch.object(manage, 'start_backend_rollout') as rollout, \
             patch.object(manage, 'run') as remote_build, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            manage.update(args)
        rollout.assert_called_once_with(self.old)
        remote_build.assert_not_called()
        self.assertIn('selected Codex package is already current', output.getvalue())

    def test_check_reports_backend_mismatch_without_starting_rollout(self):
        commit = 'a' * 40
        manage.STATE.mkdir(parents=True)
        manage.STATE_FILE.write_text(json.dumps({'release_tag': common.release_tag(commit)}))
        args = SimpleNamespace(source_refreshed=True, revision=commit, check=True)
        mismatch = {'selected_source_commit': commit, 'running': {'source_commit': 'b' * 40},
                    'matches_selected': False}
        with patch.object(manage, 'platform_check'), \
             patch.object(manage, 'published', return_value={}), \
             patch.object(manage, 'installed_matches', return_value=True), \
             patch.object(manage, 'backend_status', return_value=mismatch), \
             patch.object(manage, 'start_backend_rollout') as rollout, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            manage.update(args)
        rollout.assert_not_called()
        info = json.loads(output.getvalue())
        self.assertTrue(info['already_installed'])
        self.assertEqual(info['backend'], mismatch)

    def test_rollout_launcher_does_not_spawn_when_worker_owns_lock(self):
        with backend.acquire_rollout_lock(self.profile):
            with patch.object(manage.subprocess, 'Popen') as popen:
                self.assertFalse(manage.start_backend_rollout(self.old))
            popen.assert_not_called()

    def test_rollout_launcher_passes_exclusive_lock_to_child(self):
        with patch.object(manage.subprocess, 'Popen') as popen:
            def inherited_lock(args, **kwargs):
                descriptor = int(args[args.index('--lock-fd') + 1])
                self.assertEqual(kwargs['pass_fds'], (descriptor,))
                self.assertIsNone(backend.acquire_rollout_lock(self.profile))
            popen.side_effect = inherited_lock
            self.assertTrue(manage.start_backend_rollout(self.old))
        # A failed/exited launch must not leave a permanent lock behind.
        with backend.acquire_rollout_lock(self.profile):
            pass

    def test_spawned_child_holds_rollout_lock_after_launcher_returns(self):
        root = self.home / 'test-worker';root.mkdir()
        (root / 'backend.py').write_text(
            'import os, sys, time\n'
            'from pathlib import Path\n'
            'os.fstat(int(sys.argv[sys.argv.index("--lock-fd") + 1]))\n'
            'Path(sys.argv[1], "worker-ready").touch()\n'
            'time.sleep(20)\n')
        children = []
        real_popen = subprocess.Popen
        def spawn(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child
        try:
            with patch.object(manage, 'ROOT', root), \
                 patch.object(manage.subprocess, 'Popen', side_effect=spawn):
                self.assertTrue(manage.start_backend_rollout(self.old))
                deadline = time.monotonic() + 3
                while not (self.old / 'worker-ready').exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue((self.old / 'worker-ready').exists())
                self.assertFalse(manage.start_backend_rollout(self.old))
                self.assertEqual(len(children), 1)
        finally:
            for child in children:
                child.terminate()
                child.wait(timeout=3)
        with backend.acquire_rollout_lock(self.profile):
            pass

    def test_source_identity_includes_committed_build_recipe(self):
        root, _ = self.source_repository()
        before = common.output(['git', '-C', root, 'rev-parse', 'HEAD'])
        nested = root / 'local-customizations/distribution'
        cache = nested / '__pycache__';cache.mkdir()
        (cache / 'helper.pyc').write_bytes(b'generated bytecode')
        self.assertEqual(common.output(['git', '-C', root, 'status', '--porcelain']), '')
        (nested / 'build.py').write_text('changed build recipe\n')
        after = self.commit_source(root, 'Update recipe')
        self.assertNotEqual(common.release_tag(before), common.release_tag(after))

    def test_default_revision_resolves_personal_branch_not_upstream(self):
        commit = 'a' * 40
        with patch.object(common, 'api', return_value={'sha': commit}) as api:
            self.assertEqual(build.plan()['source_commit'], commit)
        api.assert_called_once_with(f"repos/{common.REPO}/commits/{common.CONFIG['source_branch']}")

    def test_explicit_revision_is_exact_and_does_not_resolve_remote(self):
        with patch.object(common, 'api') as api:
            self.assertEqual(build.plan('b' * 40)['source_commit'], 'b' * 40)
            for invalid in ('main', 'HEAD', 'abcdef', 'a' * 39, '$(touch nope)'):
                with self.assertRaises(RuntimeError):build.plan(invalid)
            api.assert_not_called()

    def test_prepare_uses_committed_source_not_uncommitted_worktree(self):
        root, _ = self.source_repository()
        commit = common.output(['git', '-C', root, 'rev-parse', 'HEAD'])
        (root / 'native.rs').write_text('uncommitted changes must stay local\n')
        destination = self.home / 'build/source'
        with patch.object(build, 'source_root', return_value=root):
            build.prepare(destination, commit)
        self.assertEqual((destination / 'native.rs').read_text(), 'personal source\n')
        self.assertEqual(common.output(['git', '-C', destination, 'rev-parse', 'HEAD']), commit)
        self.assertEqual((root / 'native.rs').read_text(), 'uncommitted changes must stay local\n')

    def test_prepare_rejects_bare_upstream_revision(self):
        root, base = self.source_repository()
        with patch.object(build, 'source_root', return_value=root):
            with self.assertRaisesRegex(RuntimeError, 'bare upstream'):
                build.prepare(self.home / 'bare-build/source', base)

    def test_refresh_discovers_nested_linked_worktree_and_fast_forwards_full_history(self):
        root, base = self.source_repository()
        remote = self.home / 'origin.git'
        subprocess.run(['git', 'init', '-q', '--bare', str(remote)], check=True)
        subprocess.run(['git', '-C', str(root), 'remote', 'add', 'origin', str(remote)], check=True)
        subprocess.run(['git', '-C', str(root), 'push', '-q', 'origin', f"HEAD:{common.CONFIG['source_branch']}"], check=True)
        worktree = self.home / 'personal-worktree'
        subprocess.run(['git', '-C', str(root), 'worktree', 'add', '-q', '-b', common.CONFIG['source_branch'], str(worktree)], check=True)
        self.assertTrue((worktree / '.git').is_file())
        (root / 'native.rs').write_text('next personal source\n')
        expected = self.commit_source(root, 'Next personal source')
        subprocess.run(['git', '-C', str(root), 'push', '-q', 'origin', f"HEAD:{common.CONFIG['source_branch']}"], check=True)
        with patch.object(common, 'ROOT', worktree / 'local-customizations/distribution'):
            self.assertEqual(common.source_root(), worktree)
            self.assertTrue(manage.refresh_source())
            self.assertFalse(manage.refresh_source())
        self.assertEqual(common.output(['git', '-C', worktree, 'rev-parse', 'HEAD']), expected)
        self.assertEqual(common.output(['git', '-C', worktree, 'rev-parse', '--is-shallow-repository']), 'false')
        subprocess.run(['git', '-C', str(worktree), 'cat-file', '-e', base], check=True)

    def test_refresh_refuses_dirty_source_before_fetching(self):
        with patch.object(manage, 'source_root', return_value=self.home), \
             patch.object(manage, 'output', side_effect=[common.CONFIG['source_branch'], ' M native.rs']), \
             patch.object(manage, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'uncommitted'):
                manage.refresh_source()
            run.assert_not_called()

    def test_legacy_receipt_cannot_match_source_identity(self):
        package, manifest = self.candidate()
        manage.select_package(package, manifest)
        self.assertTrue(manage.installed_matches(manifest))
        self.assertFalse(manage.installed_matches(manifest, 'c' * 40))
        manifest['source_commit'] = 'c' * 40
        self.assertTrue(manage.installed_matches(manifest, 'c' * 40))
        self.assertFalse(manage.installed_matches(manifest, 'd' * 40))

    def test_missing_local_renderer_keeps_the_working_package(self):
        package, manifest = self.candidate()
        helper = package / 'customizations/visualize-cli/visualize.py'
        helper.parent.mkdir(parents=True)
        shutil.copy2(manage.source_root() / 'local-customizations/visualize-cli/visualize.py',helper)
        manifest['required_local_checks'] = ['bundled-visualization-renderer']
        with self.assertRaises(RuntimeError):manage.select_package(package,manifest)
        self.assertEqual((self.packages / 'current').resolve(),self.old)

    def test_cached_source_times_are_reused_only_for_identical_content(self):
        source = self.home / 'source';source.mkdir()
        file = source / 'input.rs';file.write_text('original source')
        subprocess.run(['git','init','-q',str(source)],check=True)
        subprocess.run(['git','-C',str(source),'add','.'],check=True)
        stamp = file.stat().st_mtime_ns
        with patch.object(build.Path,'home',return_value=self.home):
            build.source_times(source)
            os.utime(file,ns=(stamp+1000000000,stamp+1000000000))
            build.source_times(source,restore=True)
            self.assertEqual(file.stat().st_mtime_ns,stamp)
            file.write_text('changed source')
            os.utime(file,ns=(stamp+2000000000,stamp+2000000000))
            build.source_times(source,restore=True)
            self.assertEqual(file.stat().st_mtime_ns,stamp+2000000000)

    def test_compiled_series_version_is_distinct_from_package_provenance(self):
        package, manifest = self.candidate()
        version = '2.3.4-dev.20261109+romil.abcdef012345.01234567'
        (package / 'codex-package.json').write_text(json.dumps({'target':manage.TARGET,'version':version}))
        binary = package / 'bin/codex'
        binary.write_text('#!/bin/sh\nprintf "codex-cli 2.3.4-dev\\n"\n')
        manifest.update(version=version,compiled_version='2.3.4-dev',build_kind='remote-source-build')
        manifest['binary_sha256']['codex'] = sha256(binary)
        manage.verify_package(package,manifest)
        del manifest['compiled_version']
        manage.verify_package(package,manifest)
        binary.write_text('#!/bin/sh\nprintf "codex-cli 2.3.4-alpha\\n"\n')
        manifest['binary_sha256']['codex'] = sha256(binary)
        with self.assertRaises(RuntimeError):manage.verify_package(package,manifest)

    def test_archive_path_escape_is_rejected(self):
        archive = self.home / 'bad.tar.gz'
        with tarfile.open(archive,'w:gz') as f:
            item=tarfile.TarInfo('../outside');item.size=4;f.addfile(item,io.BytesIO(b'nope'))
        with self.assertRaises((tarfile.TarError, OSError)):
            manage.extract(archive,self.home / 'extract')
        self.assertFalse((self.home / 'outside').exists())

    def test_launcher_preserves_native_invocation(self):
        package, _ = self.candidate();(self.packages / 'current').unlink();(self.packages / 'current').symlink_to(package)
        env=dict(os.environ,CODEX_HOME=str(self.profile))
        result=subprocess.run([str(manage.ROOT / 'bin/codex'),'--version'],env=env,text=True,capture_output=True,check=True)
        self.assertEqual(result.stdout.strip(),'codex-cli 2.3.4')

    def test_unified_dispatcher_selects_fork_behaviour_without_using_legacy_binary(self):
        package, _ = self.candidate()
        (package / 'custom-build.json').write_text(json.dumps({'launcher_mode': 'personal-harness'}))
        (self.packages / 'current').unlink()
        (self.packages / 'current').symlink_to(package)
        helpers = self.home / 'helpers';helpers.mkdir()
        bubblewrap = helpers / 'bwrap'
        bubblewrap.write_text('#!/usr/bin/env python3\nimport json,os,sys\n'
                             'print(json.dumps({"arguments":sys.argv[1:],"owner":os.environ.get("CODEX_PLUS_ONE")}))\n')
        bubblewrap.chmod(0o755)
        env = dict(os.environ, HOME=str(self.home), CODEX_HOME=str(self.profile),
                   PATH=str(helpers) + os.pathsep + os.environ['PATH'])
        result = subprocess.run([str(manage.ROOT / 'bin/codex'), '--version'],
                                env=env, text=True, capture_output=True, check=True)
        observed = json.loads(result.stdout)
        self.assertEqual(observed['owner'], '1')
        self.assertIn(str(self.packages / 'current/bin/codex'), observed['arguments'])
        self.assertIn('instructions=""', observed['arguments'])
        self.assertNotIn(str(self.home / '.local/share/codex-plus-one/current/bin/codex'), observed['arguments'])

    def test_unified_package_without_owner_checks_cannot_replace_working_install(self):
        package, manifest = self.candidate()
        manifest['launcher_mode'] = 'personal-harness'
        with self.assertRaisesRegex(RuntimeError, 'owner-mode checks'):
            manage.select_package(package, manifest)
        self.assertEqual((self.packages / 'current').resolve(), self.old)

    def test_retirement_removes_only_known_old_command_and_preserves_settings(self):
        old = self.home / '.local/share/codex-plus-one'
        (old / 'bin').mkdir(parents=True)
        native = old / 'bin/codex+1';native.write_text('legacy launcher\n')
        settings = old / 'home/config.toml';settings.parent.mkdir()
        settings.write_text('model = "saved-choice"\n')
        command = self.home / '.local/bin/codex+1';command.parent.mkdir(parents=True)
        command.symlink_to(native)
        self.assertTrue(manage.retire_legacy_launcher())
        self.assertFalse(command.is_symlink())
        self.assertEqual(settings.read_text(), 'model = "saved-choice"\n')
        self.assertEqual(native.read_text(), 'legacy launcher\n')
        command.write_text('unrelated user command\n')
        self.assertFalse(manage.retire_legacy_launcher())
        self.assertEqual(command.read_text(), 'unrelated user command\n')

    def test_launcher_install_is_idempotent_and_preserves_profile(self):
        package, _ = self.candidate();(self.packages / 'current').unlink();(self.packages / 'current').symlink_to(package)
        before = (self.profile / 'config.toml').read_bytes()
        with patch.object(manage, 'platform_check'), contextlib.redirect_stdout(io.StringIO()):
            manage.install_launcher()
            manage.install_launcher()
        self.assertEqual((self.home / '.local/bin/codex').resolve(), (manage.ROOT / 'bin/codex').resolve())
        self.assertEqual((self.profile / 'config.toml').read_bytes(), before)

    def test_retirement_removes_only_old_command_and_preserves_fork_data(self):
        old = self.home / '.local/share/codex-plus-one'
        (old / 'bin').mkdir(parents=True)
        native = old / 'bin/codex+1';native.write_text('legacy helper\n')
        (old / 'home').mkdir()
        settings = old / 'home/config.toml';settings.write_text('model = "saved"\n')
        command = self.home / '.local/bin/codex+1'
        command.parent.mkdir(parents=True);command.symlink_to(native)
        self.assertTrue(manage.retire_legacy_launcher())
        self.assertFalse(command.is_symlink())
        self.assertEqual(native.read_text(), 'legacy helper\n')
        self.assertEqual(settings.read_text(), 'model = "saved"\n')

    def test_retirement_preserves_unrecognised_command(self):
        command = self.home / '.local/bin/codex+1'
        command.parent.mkdir(parents=True);command.write_text('unrelated command\n')
        self.assertFalse(manage.retire_legacy_launcher())
        self.assertEqual(command.read_text(), 'unrelated command\n')

    def test_launcher_replaces_only_explicitly_verified_legacy_launcher(self):
        package, _ = self.candidate();(self.packages / 'current').unlink();(self.packages / 'current').symlink_to(package)
        legacy = self.home / 'Work/codex-customizations';(legacy / 'bin').mkdir(parents=True)
        subprocess.run(['git', 'init', '-q', str(legacy)], check=True)
        subprocess.run(['git', '-C', str(legacy), 'remote', 'add', 'origin', f'https://github.com/{manage.LEGACY_REPO}.git'], check=True)
        legacy_script = legacy / 'bin/codex';shutil.copy2(manage.ROOT / 'bin/codex', legacy_script)
        launcher = self.home / '.local/bin/codex';launcher.parent.mkdir(parents=True);launcher.symlink_to(legacy_script)
        with patch.object(manage, 'platform_check'), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):manage.install_launcher()
            self.assertEqual(launcher.resolve(), legacy_script)
            legacy_script.write_text('unrelated script\n')
            with self.assertRaises(RuntimeError):manage.install_launcher(replace_legacy=True)
            self.assertEqual(launcher.resolve(), legacy_script)
            shutil.copy2(manage.ROOT / 'bin/codex', legacy_script)
            manage.install_launcher(replace_legacy=True)
        self.assertEqual(launcher.resolve(), (manage.ROOT / 'bin/codex').resolve())


if __name__ == '__main__':unittest.main()
