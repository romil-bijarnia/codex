"""Backend rollout reconciliation without a real daemon or service mutation."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend
from common import sha256


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.current = self.home/'packages/standalone/current'
        self.current.parent.mkdir(parents=True)
        self.package, self.manifest = self.candidate('selected', 'a' * 40)
        self.select(self.package)
        self.running = dict(pid=123, executable='/old/bin/codex', sha256='0' * 64,
                            source_commit='b' * 40)
        self.now = 0
        self.calls = []
        self.addCleanup(patch.stopall)
        patch.object(backend, 'running_backend', side_effect=lambda home: dict(self.running)).start()
        patch.object(backend, 'active_threads', return_value=[]).start()
        patch.object(backend.subprocess, 'run', side_effect=self.command).start()
        patch.object(backend.time, 'monotonic', side_effect=lambda: self.now).start()
        patch.object(backend.time, 'sleep', side_effect=self.sleep).start()

    def candidate(self, name, source):
        package = self.current.parent/'releases'/name
        (package/'bin').mkdir(parents=True)
        (package/'bin/codex').write_text('verified executable ' + source)
        manifest = {'source_commit': source, 'binary_sha256': {'codex': sha256(package/'bin/codex')}}
        (package/'custom-build.json').write_text(json.dumps(manifest))
        return package, manifest

    def select(self, package):
        self.current.unlink(missing_ok=True)
        self.current.symlink_to(package)

    def sleep(self, seconds):
        self.now += seconds

    def command(self, args, **kwargs):
        self.calls.append((self.now, args))
        if 'update' in args:
            self.running['sha256'] = sha256(Path(args[0]))
            self.running['source_commit'] = json.loads((Path(args[0]).parent.parent/'custom-build.json').read_text())['source_commit']
        return subprocess.CompletedProcess(args, 0, json.dumps({'socketPath': '/unused.sock'}), '')

    def report(self):
        return json.loads((self.home/'state/codex-customizations/backend.json').read_text())

    def updates(self):
        return [(when, args) for when, args in self.calls if 'update' in args]

    def test_matching_loaded_hash_does_not_reinstall_or_observe_idle(self):
        self.running['sha256'] = self.manifest['binary_sha256']['codex']
        self.running['source_commit'] = self.manifest['source_commit']
        self.assertEqual(backend.rollout(self.package, self.home), self.package)
        backend.active_threads.assert_not_called()
        self.assertFalse(self.updates())
        self.assertEqual(self.report()['status'], 'complete')
        self.assertEqual(self.report()['running_source_commit'], 'a' * 40)

    def test_mismatch_updates_only_after_fifteen_seconds_of_observed_idle(self):
        backend.rollout(self.package, self.home)
        self.assertEqual(self.updates(), [(15, [str(self.package/'bin/codex'),
                                               'app-server', 'daemon', 'update', '--from-cli', '--yes'])])
        self.assertEqual(self.report()['status'], 'complete')

    def test_active_chat_resets_idle_observation(self):
        backend.active_threads.side_effect = [[], [], ['busy'], [], [], [], []]
        backend.rollout(self.package, self.home)
        self.assertEqual(self.updates()[0][0], 30)

    def test_incomplete_observation_resets_idle_and_does_not_infer_safety(self):
        backend.active_threads.side_effect = [[], RuntimeError('incomplete observation'), [], [], [], []]
        backend.rollout(self.package, self.home)
        self.assertEqual(self.updates()[0][0], 25)

    def test_worker_follows_new_selection_and_restarts_idle_timer(self):
        new, _ = self.candidate('newer', 'c' * 40)
        def select_new(seconds):
            self.sleep(seconds)
            if self.now == 5:
                self.select(new)
        backend.time.sleep.side_effect = select_new
        backend.rollout(self.package, self.home)
        self.assertEqual(self.updates()[0][0], 20)
        self.assertEqual(self.updates()[0][1][0], str(new/'bin/codex'))
        self.assertEqual(self.report()['source_commit'], 'c' * 40)

    def test_selection_changed_during_last_idle_probe_is_not_installed(self):
        new, _ = self.candidate('newer', 'c' * 40)
        def active(socket_path):
            if self.now == 15:
                self.select(new)
            return []
        backend.active_threads.side_effect = active
        backend.rollout(self.package, self.home)
        self.assertEqual(self.updates()[0][0], 30)
        self.assertEqual(self.updates()[0][1][0], str(new/'bin/codex'))

    def test_tampered_candidate_is_not_installed(self):
        def tamper(seconds):
            self.sleep(seconds)
            if self.now == 10:
                (self.package/'bin/codex').write_text('replaced')
        backend.time.sleep.side_effect = tamper
        with self.assertRaisesRegex(RuntimeError, 'changed while waiting'):
            backend.rollout(self.package, self.home)
        self.assertFalse(self.updates())

    def test_failed_daemon_update_is_not_reported_complete(self):
        def fail(args, **kwargs):
            if 'update' in args:
                return subprocess.CompletedProcess(args, 1, '', 'update failed')
            return self.command(args, **kwargs)
        backend.subprocess.run.side_effect = fail
        with self.assertRaises(SystemExit):
            backend.rollout(self.package, self.home)
        self.assertEqual(self.report()['status'], 'failed')

    def test_wrong_loaded_hash_after_update_is_not_reported_complete(self):
        backend.subprocess.run.side_effect = lambda args, **kwargs: subprocess.CompletedProcess(
            args, 0, json.dumps({'socketPath': '/unused.sock'}), '')
        with self.assertRaises(SystemExit):
            backend.rollout(self.package, self.home)
        self.assertEqual(self.report()['status'], 'failed')

    def test_inspect_does_not_write_state_or_change_service(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            backend.rollout(self.package, self.home, inspect=True)
        self.assertFalse(self.updates())
        self.assertFalse((self.home/'state').exists())
        self.assertFalse(json.loads(output.getvalue())['service_changed'])

    def test_status_distinguishes_selected_and_running_sources(self):
        status = backend.backend_status(self.home, self.package)
        self.assertEqual(status['selected_source_commit'], 'a' * 40)
        self.assertEqual(status['running']['source_commit'], 'b' * 40)
        self.assertFalse(status['matches_selected'])

    def test_unobservable_backend_is_not_reported_as_matching(self):
        backend.running_backend.side_effect = FileNotFoundError('daemon not running')
        status = backend.backend_status(self.home, self.package)
        self.assertIsNone(status['matches_selected'])
        self.assertIn('daemon not running', status['error'])

    def test_direct_second_worker_exits_without_daemon_commands(self):
        with backend.acquire_rollout_lock(self.home), \
             patch.object(sys, 'argv', ['backend.py', str(self.package)]), \
             patch.dict(os.environ, CODEX_HOME=str(self.home)):
            backend.main()
        backend.subprocess.run.assert_not_called()

    def test_selection_changed_while_worker_exits_is_reconciled(self):
        new, _ = self.candidate('newer', 'c' * 40)
        with patch.object(backend, 'rollout') as rollout, \
             patch.object(sys, 'argv', ['backend.py', str(self.package)]), \
             patch.dict(os.environ, CODEX_HOME=str(self.home)):
            # The real rollout follows current; the fake second call does too.
            def complete(package, home):
                if rollout.call_count == 1:
                    self.select(new)
                    return self.package
                return new
            rollout.side_effect = complete
            backend.main()
        self.assertEqual(rollout.call_count, 2)
        with backend.acquire_rollout_lock(self.home):
            pass


class RunningBackendTests(unittest.TestCase):
    def test_loaded_executable_is_hashed_and_source_requires_matching_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home/'app-server-daemon').mkdir()
            fields = Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')', 1)[1].split()
            record = {'pid': os.getpid(), 'processIdentity': {
                'bootId': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'startTicks': int(fields[19])}}
            (home/'app-server-daemon/daemon.pid').write_text(json.dumps(record))
            package = home/'release';(package/'bin').mkdir(parents=True)
            digest = sha256(Path(f'/proc/{os.getpid()}/exe'))
            manifest = {'source_commit': 'a' * 40, 'binary_sha256': {'codex': digest}}
            (package/'custom-build.json').write_text(json.dumps(manifest))
            with patch.object(backend.os, 'readlink', return_value=str(package/'bin/codex')):
                self.assertEqual(backend.running_backend(home)['source_commit'], 'a' * 40)
                manifest['binary_sha256']['codex'] = '0' * 64
                (package/'custom-build.json').write_text(json.dumps(manifest))
                result = backend.running_backend(home)
            self.assertEqual(result['sha256'], digest)
            self.assertIsNone(result['source_commit'])

    def test_reused_pid_with_stale_native_identity_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory);(home/'app-server-daemon').mkdir()
            record = {'pid': os.getpid(), 'processIdentity': {
                'bootId': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'startTicks': -1}}
            (home/'app-server-daemon/daemon.pid').write_text(json.dumps(record))
            with self.assertRaisesRegex(RuntimeError, 'stale'):
                backend.running_backend(home)


if __name__ == '__main__':
    unittest.main()
