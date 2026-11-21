"""Checks for the separately pinned codex+1 launcher."""
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_launcher():
    path = ROOT / "distribution/bin/codex+1"
    loader = SourceFileLoader("codex_plus_one_launcher", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PlusOneLauncherTests(unittest.TestCase):
    def test_overlay_uses_stock_prompt_without_changing_shared_config(self):
        launcher = load_launcher()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shared = root / "shared"
            shared.mkdir()
            prompt = shared / "private.md"
            prompt.write_text("Private model prompt.\n")
            original = (
                f'model_instructions_file = "{prompt}"\n'
                'model = "example-model"\n'
            )
            (shared / "config.toml").write_text(original)
            (shared / "auth.json").write_text("{}\n")
            (shared / "AGENTS.md").write_text("Personal instructions.\n")

            launcher.ROOT = root / "installation"
            launcher.CONFIG_HOME = launcher.ROOT / "home"
            launcher.shared_home = lambda: shared
            source, config_path = launcher.prepare_config_home()

            self.assertEqual(source, shared)
            self.assertEqual((shared / "config.toml").read_text(), original)
            generated = tomllib.loads(config_path.read_text())
            self.assertNotIn("model_instructions_file", generated)
            self.assertEqual(generated["model"], "example-model")

    def test_generated_overlay_drops_legacy_blank_base_without_editing_primary(self):
        launcher = load_launcher()
        for saved in ('absent', 'generated', 'legacy', 'edited'):
            with self.subTest(saved=saved), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                shared = root / 'shared'
                shared.mkdir()
                primary = shared / 'config.toml'
                original = (
                    "notes = '''\ninstructions = \"\"\n'''\n"
                    'instructions = "" # Old blank model base\n'
                    'model = "example-model"\n'
                    '[fixture]\ninstructions = ""\n'
                )
                primary.write_text(original)
                agents = shared / 'AGENTS.md'
                agents.write_text('Personal instructions.\n')
                launcher.ROOT = root / 'installation'
                launcher.CONFIG_HOME = launcher.ROOT / 'home'
                launcher.shared_home = lambda: shared

                if saved != 'absent':
                    launcher.CONFIG_HOME.mkdir(parents=True)
                    overlay = launcher.CONFIG_HOME / 'config.toml'
                    previous = (
                        '# Generated for codex+1 from the shared config; custom model prompt omitted.\n'
                        + original
                    )
                    overlay.write_text(previous)
                    if saved in ('generated', 'edited'):
                        (launcher.CONFIG_HOME / launcher.GENERATED_HASH).write_text(
                            launcher.hashlib.sha256(previous.encode()).hexdigest() + '\n')
                    if saved == 'edited':
                        overlay.write_text(previous.replace('example-model', 'personal-choice'))

                _, config_path = launcher.prepare_config_home()
                parsed = tomllib.loads(config_path.read_text())
                expected = tomllib.loads(original)
                if saved == 'edited':
                    expected['model'] = 'personal-choice'
                else:
                    del expected['instructions']
                self.assertEqual(parsed, expected)
                self.assertEqual(primary.read_text(), original)
                self.assertEqual((launcher.CONFIG_HOME / 'AGENTS.md').resolve(), agents)
                content = config_path.read_text()
                launcher.prepare_config_home()
                self.assertEqual(config_path.read_text(), content)

    def test_generated_overlay_preserves_nonblank_inline_base(self):
        launcher = load_launcher()
        original = 'instructions = "explicit custom base"\n'
        self.assertEqual(launcher.without_blank_model_base(original), original)

    def test_command_parser_skips_option_values(self):
        launcher = load_launcher()
        self.assertEqual(launcher.command(["-m", "example-model", "resume"]), "resume")
        self.assertEqual(launcher.command(["--local-provider", "ollama", "update"]),
                         "update")

    def test_fork_config_edits_persist_without_changing_primary(self):
        launcher = load_launcher()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shared = root / "shared"
            shared.mkdir()
            (shared / "config.toml").write_text(
                'model_instructions_file = "private.md"\nmodel = "initial"\n')
            (shared / "auth.json").write_text('{}\n')
            (shared / "bin").mkdir()
            (shared / "bin/codex-fork-window").write_text('primary helper\n')
            launcher.ROOT = root / "installation"
            launcher.CONFIG_HOME = launcher.ROOT / "home"
            launcher.shared_home = lambda: shared
            _, config_path = launcher.prepare_config_home()
            self.assertNotIn('model_instructions_file', config_path.read_text())
            self.assertTrue((launcher.CONFIG_HOME / "auth.json").is_symlink())
            self.assertEqual((launcher.CONFIG_HOME / "auth.json").resolve(),
                             (shared / "auth.json").resolve())
            self.assertEqual((launcher.CONFIG_HOME / "bin/codex-fork-window").resolve(),
                             (launcher.ROOT / "bin/codex+1").resolve())
            config_path.write_text(config_path.read_text().replace('initial', 'fork-edited'))
            launcher.prepare_config_home()
            self.assertIn('model = "fork-edited"', config_path.read_text())
            self.assertIn('model = "initial"', (shared / "config.toml").read_text())

    def test_unmodified_fork_config_follows_primary_changes(self):
        launcher = load_launcher()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shared = root / "shared"
            shared.mkdir()
            primary = shared / "config.toml"
            primary.write_text('model_instructions_file = "private.md"\nmodel = "first"\n')
            launcher.ROOT = root / "installation"
            launcher.CONFIG_HOME = launcher.ROOT / "home"
            launcher.shared_home = lambda: shared
            _, config_path = launcher.prepare_config_home()
            primary.write_text('model_instructions_file = "private.md"\nmodel = "second"\n')
            launcher.prepare_config_home()
            self.assertIn('model = "second"', config_path.read_text())
            self.assertNotIn('model_instructions_file =', config_path.read_text())

    def test_primary_command_reuses_saved_fork_settings_and_migrates_fork_helper(self):
        launcher = load_launcher()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shared = root / 'shared';shared.mkdir()
            (shared / 'config.toml').write_text('model = "initial"\n')
            launcher.ROOT = root / 'installation'
            launcher.CONFIG_HOME = launcher.ROOT / 'home'
            launcher.shared_home = lambda: shared
            _, config = launcher.prepare_config_home()
            config.write_text('model = "saved-fork-choice"\n')
            primary = root / '.local/bin/codex'
            launcher.PRIMARY_LAUNCHER = primary
            launcher.prepare_config_home()
            self.assertEqual(config.read_text(), 'model = "saved-fork-choice"\n')
            self.assertEqual((launcher.CONFIG_HOME / 'bin/codex-fork-window').readlink(), primary)

    def test_primary_command_executes_selected_package_with_fork_behaviour(self):
        launcher = load_launcher()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native = root / 'selected/bin/codex'
            native.parent.mkdir(parents=True);native.touch()
            launcher.ROOT = root / 'legacy'
            launcher.CONFIG_HOME = launcher.ROOT / 'home'
            launcher.PRIMARY_EXECUTABLE = native
            with patch.object(launcher, 'prepare_config_home', return_value=(root / 'shared', root / 'config.toml')), \
                 patch.object(launcher.shutil, 'which', return_value='/usr/bin/bwrap'), \
                 patch.object(launcher.os, 'execv') as execv, \
                 patch.object(launcher.sys, 'argv', ['codex']), \
                 patch.dict(launcher.os.environ, {}, clear=True):
                launcher.main(['--version'])
                self.assertEqual(launcher.os.environ['CODEX_PLUS_ONE'], '1')
            args = execv.call_args.args[1]
            self.assertEqual(args[1:6], ['--bind', '/', '/', '--dev', '/dev'])
            self.assertIn(str(native), args)
            self.assertNotIn('instructions=""', execv.call_args.args[1])

    def test_primary_update_with_global_options_routes_to_the_single_updater(self):
        launcher = load_launcher()
        launcher.PRIMARY_LAUNCHER = Path('/fixture/codex')
        with patch.object(launcher.os, 'execv') as execute, \
             patch.object(launcher.sys, 'argv', ['codex']):
            self.assertEqual(launcher.main(['-m', 'update', 'update', '--check']), 0)
        execute.assert_called_once_with('/fixture/codex', ['/fixture/codex', 'update', '--check'])

    def test_remote_launch_does_not_inject_no_daemon(self):
        launcher = load_launcher()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "current/bin/codex"
            executable.parent.mkdir(parents=True)
            executable.touch()
            launcher.ROOT = root
            launcher.CONFIG_HOME = root / "home"
            source = root / "primary"
            source.mkdir()
            config = root / "home/config.toml"
            with patch.object(launcher, 'prepare_config_home', return_value=(source, config)), \
                 patch.object(launcher.shutil, 'which', return_value='/usr/bin/bwrap'), \
                 patch.object(launcher.os, 'execv') as execv, \
                 patch.object(launcher.sys, 'argv', ['codex+1']), \
                 patch.dict(launcher.os.environ, {}, clear=True):
                launcher.main(['--remote=unix:///tmp/fixture.sock', 'resume', 'fixture-thread'])
                self.assertEqual(launcher.os.environ.get('CODEX_PLUS_ONE'), '1')
            args = execv.call_args.args[1]
            self.assertIn('--remote=unix:///tmp/fixture.sock', args)
            self.assertNotIn('--no-daemon', args)
            self.assertNotIn('instructions=""', args)

    def test_fork_helper_bootstrap_preserves_pinned_launcher(self):
        launcher = load_launcher()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            launcher.ROOT = root / 'installation'
            launcher.CONFIG_HOME = launcher.ROOT / 'home'
            shared = root / 'primary'
            shared.mkdir()
            launcher.shared_home = lambda: shared
            class Child:
                returncode = 0

                def poll(self):
                    return 0

            with patch.object(launcher.subprocess, 'Popen', return_value=Child()) as popen, \
                 patch.object(launcher.time, 'sleep'):
                self.assertEqual(launcher.fork_window([
                    '--resume', 'fixture-thread', '--workdir', str(root), '--no-daemon']), 0)
            request = popen.call_args.args[0]
            self.assertEqual(request[1:3], ['--new-window', '--desktopfile'])
            bootstrap = Path(request[-1]).read_text()
            self.assertIn(str(launcher.ROOT / 'bin/codex+1'), bootstrap)
            self.assertIn("'resume', 'fixture-thread'", bootstrap)
            self.assertIn("'CODEX_PLUS_ONE': '1'", bootstrap)
            self.assertNotIn("'/home/romilbijarnia/.local/bin/codex'", bootstrap)


if __name__ == "__main__":
    unittest.main()
