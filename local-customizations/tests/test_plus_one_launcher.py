"""Checks for the separately pinned codex+1 launcher."""
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import tempfile
import tomllib
import unittest


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

    def test_command_parser_skips_option_values(self):
        launcher = load_launcher()
        self.assertEqual(launcher.command(["-m", "example-model", "resume"]), "resume")


if __name__ == "__main__":
    unittest.main()
