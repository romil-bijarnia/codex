"""Offline checks for same-source helper linking with verified bundled libraries."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voice_build


class VoiceBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        (self.source / 'codex-rs').mkdir(parents=True)
        self.package = self.root / 'package'
        self.lib = self.package / 'codex-resources/voice/lib'
        self.lib.mkdir(parents=True)
        for name in voice_build.NATIVE_LIBRARIES.values():
            (self.lib / ('lib' + name + '.so.0')).write_bytes(name.encode())
        self.env = {'STABLE_GIT_COMMIT': 'a' * 40,
                    'CARGO_TARGET_DIR': str(self.root / 'target'),
                    'RUSTFLAGS': '-C opt-level=3',
                    'PKG_CONFIG_PATH': '/host/older/gstreamer'}
        self.work = self.root / 'work'

    def test_links_exact_bundled_libraries_without_using_host_pkgconfig(self):
        original_env = dict(self.env)
        original_files = {path: path.read_bytes() for path in self.lib.iterdir()}
        with patch.object(voice_build, 'run') as run:
            binary = voice_build.build_voice_host(self.source, self.package, self.env, self.work)
        self.assertEqual(binary, self.root / 'target/release/codex-voice-host')
        self.assertEqual(self.env, original_env)
        self.assertEqual({path: path.read_bytes() for path in self.lib.iterdir()}, original_files)
        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(kwargs['cwd'], self.source / 'codex-rs')
        self.assertEqual(args[0][:9], ['cargo', 'rustc', '--locked', '--release',
                                      '-p', 'codex-voice-host', '--bin',
                                      'codex-voice-host', '--'])
        native_env = kwargs['env']
        self.assertEqual(native_env['STABLE_GIT_COMMIT'], self.env['STABLE_GIT_COMMIT'])
        for key, name in voice_build.NATIVE_LIBRARIES.items():
            with self.subTest(key=key):
                prefix = 'SYSTEM_DEPS_' + key
                self.assertEqual(native_env[prefix + '_NO_PKG_CONFIG'], '1')
                self.assertEqual(native_env[prefix + '_LIB'], name)
                self.assertEqual(native_env[prefix + '_LINK'], 'dynamic')
                aliases = Path(native_env[prefix + '_SEARCH_NATIVE'])
                alias = aliases / ('lib' + name + '.so')
                self.assertTrue(alias.is_symlink())
                self.assertEqual(alias.resolve(), self.lib / ('lib' + name + '.so.0'))
                self.assertFalse(alias.is_relative_to(self.package))

    def test_loader_flags_apply_only_to_the_final_helper(self):
        with patch.object(voice_build, 'run') as run:
            voice_build.build_voice_host(self.source, self.package, self.env, self.work)
        command = run.call_args.args[0]
        self.assertEqual(command[9:], [
            '-C', 'link-arg=-Wl,-rpath,$ORIGIN/../lib',
            '-C', 'link-arg=-Wl,-rpath-link,' + str(self.lib),
        ])
        self.assertEqual(run.call_args.kwargs['env']['RUSTFLAGS'], self.env['RUSTFLAGS'])
        self.assertNotIn('CARGO_ENCODED_RUSTFLAGS', run.call_args.kwargs['env'])

    def test_missing_library_is_rejected_before_building_or_staging_aliases(self):
        (self.lib / 'libgstapp-1.0.so.0').unlink()
        with patch.object(voice_build, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'native voice library is missing'):
                voice_build.build_voice_host(self.source, self.package, self.env, self.work)
        run.assert_not_called()
        self.assertFalse(self.work.exists())

    def test_indirect_native_library_is_rejected(self):
        library = self.lib / 'libgstapp-1.0.so.0'
        outside = self.root / 'outside.so'
        outside.write_bytes(library.read_bytes())
        library.unlink()
        library.symlink_to(outside)
        with patch.object(voice_build, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'native voice library is missing or indirect'):
                voice_build.build_voice_host(self.source, self.package, self.env, self.work)
        run.assert_not_called()

    def test_unstamped_helper_build_is_rejected(self):
        for stamp in ('', 'dev', 'a' * 12):
            with self.subTest(stamp=stamp), patch.object(voice_build, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, 'complete application source commit'):
                    voice_build.build_voice_host(
                        self.source, self.package, dict(self.env, STABLE_GIT_COMMIT=stamp), self.work)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
