"""Regression coverage for mixed builds and fail-closed native voice selection."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common
import manage
import voice


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.commit = common.stable_release_pin()['upstream_commit']
        self.source = 'a' * 40

    def resources(self):
        package = self.root / 'package'
        root = package / 'codex-resources/voice'
        (root / 'bin').mkdir(parents=True)
        (root / 'bin/codex-voice-host').write_bytes(b'fixed helper bytes')
        (root / 'runtime.json').write_text(json.dumps({'schemaVersion': 1,
            'sourceCommit': self.commit, 'target': common.TARGET, 'developmentOnly': False}))
        (root / 'sources.json').write_text('{}')
        hashes = {str(p.relative_to(package)): common.sha256(p)
                  for p in root.rglob('*') if p.is_file()}
        hashes['bin/codex'] = 'f' * 64  # Official CLI is intentionally replaced.
        (root / 'manifest.json').write_text(json.dumps({'schemaVersion': 1,
            'buildCommit': self.commit, 'voiceTarget': common.TARGET, 'sha256': hashes}))
        return package

    def report(self):
        return {'schema_version': 1, 'source_commit': self.source,
                'voice_build_commit': self.commit, 'checks': sorted(voice.VOICE_CHECKS),
                'audio_streams_opened': False, 'network_session_started': False}

    def test_complete_runtime_identity_and_resource_hashes(self):
        package = self.resources()
        result = voice.verify_voice_resources(package, self.commit)
        self.assertEqual(result, {'voice_build_commit': self.commit, 'resource_count': 3})
        with self.assertRaisesRegex(RuntimeError, 'pinned stable native'):
            voice.verify_voice_resources(package, self.source)
        (package / 'codex-resources/voice/bin/codex-voice-host').write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
            voice.verify_voice_resources(package, self.commit)

    def test_voice_resource_cannot_escape_the_package(self):
        package = self.resources()
        helper = package / 'codex-resources/voice/bin/codex-voice-host'
        outside = self.root / 'outside';outside.write_bytes(helper.read_bytes())
        helper.unlink();helper.symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, 'outside'):
            voice.verify_voice_resources(package, self.commit)

    def test_custom_helper_assembly_preserves_official_native_receipts(self):
        package = self.resources()
        root = package / 'codex-resources/voice'
        receipts = {name: (root / name).read_bytes() for name in ('runtime.json', 'sources.json')}
        (package / 'bin').mkdir();(package / 'bin/codex').write_bytes(b'custom CLI')
        (package / 'codex-package.json').write_text(json.dumps({'target': common.TARGET,
            'version': '0.167.0+romil.' + self.source[:12]}))
        helper = root / 'bin/codex-voice-host';helper.write_bytes(b'custom same-source helper')
        result = subprocess.CompletedProcess([], 0, self.source + '\n', '')
        with patch.object(voice.subprocess, 'run', return_value=result):
            voice.assemble_voice_resources(package, self.source, self.commit)
        manifest = json.loads((root / 'manifest.json').read_text())
        self.assertEqual(manifest['buildCommit'], self.source)
        self.assertEqual(manifest['nativeSourceCommit'], self.commit)
        self.assertEqual(manifest['sha256']['bin/codex'], common.sha256(package / 'bin/codex'))
        self.assertEqual(manifest['sha256']['codex-resources/voice/bin/codex-voice-host'], common.sha256(helper))
        for name, original in receipts.items():
            self.assertEqual((root / name).read_bytes(), original)
        self.assertEqual(voice.verify_voice_resources(package, self.source, self.commit)['resource_count'], 3)
        wrong = subprocess.CompletedProcess([], 0, self.commit + '\n', '')
        with patch.object(voice.subprocess, 'run', return_value=wrong):
            with self.assertRaisesRegex(RuntimeError, 'different source identity'):
                voice.assemble_voice_resources(package, self.source, self.commit)

    def test_native_check_uses_the_actual_cli_and_both_identities(self):
        result = subprocess.CompletedProcess([], 0, json.dumps(self.report()), '')
        with patch.object(voice.subprocess, 'run', return_value=result) as run:
            self.assertEqual(voice.check_voice_runtime(self.root, self.source, self.commit), self.report())
        run.assert_called_once_with([str(self.root / 'bin/codex'), 'debug', 'voice'],
                                    capture_output=True, text=True, timeout=45, check=True)
        for field, value in (('source_commit', 'b' * 40), ('voice_build_commit', 'b' * 40),
                             ('checks', []), ('checks', None), ('audio_streams_opened', True),
                             ('network_session_started', True)):
            report = dict(self.report(), **{field: value})
            result = subprocess.CompletedProcess([], 0, json.dumps(report), '')
            with self.subTest(field=field), patch.object(voice.subprocess, 'run', return_value=result):
                with self.assertRaisesRegex(RuntimeError, 'did not prove'):
                    voice.check_voice_runtime(self.root, self.source, self.commit)

    def test_native_helper_failure_and_timeout_are_rejected(self):
        errors = [subprocess.CalledProcessError(1, [], stderr='voice helper handshake failed'),
                  subprocess.TimeoutExpired([], 45)]
        for error in errors:
            with self.subTest(error=type(error).__name__), patch.object(voice.subprocess, 'run', side_effect=error):
                with self.assertRaisesRegex(RuntimeError, 'installed package is unchanged'):
                    voice.check_voice_runtime(self.root, self.source, self.commit)

    def test_failed_voice_check_preserves_the_selected_package(self):
        packages = self.root / 'packages';packages.mkdir()
        old = packages / 'old';old.mkdir()
        current = packages / 'current';current.symlink_to(old)
        manifest = {'source_commit': self.source, 'voice_build_commit': self.commit,
                    'required_local_checks': ['voice-runtime']}
        with patch.object(manage, 'PACKAGES', packages), patch.object(manage, 'verify_package'), \
             patch.object(manage, 'check_voice_runtime', side_effect=RuntimeError('helper failed')):
            with self.assertRaisesRegex(RuntimeError, 'helper failed'):
                manage.select_package(self.root / 'candidate', manifest)
        self.assertEqual(current.resolve(), old)
        self.assertFalse((packages / 'releases').exists())


if __name__ == '__main__':unittest.main()
