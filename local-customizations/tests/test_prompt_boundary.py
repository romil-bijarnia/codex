"""Removing the bundled system prompt must not remove user-owned instructions."""
import importlib.util
from pathlib import Path
import tempfile
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PromptBoundaryTests(unittest.TestCase):
    def test_custom_model_base_and_injector_are_not_bundled(self):
        for name in ("compact-base.md", "install.py", "customize.py", "origin.json"):
            self.assertFalse((ROOT / "prompts" / name).exists(), name)

    def test_fresh_launcher_install_does_not_select_or_generate_system_prompt(self):
        install = load("fresh_launcher_install", ROOT / "install.py")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            upstream = root / "official.cjs"
            upstream.write_text("process.exit(0);\n")
            install.main(["--home", str(home), "--bin-dir", str(root / "bin"),
                          "--upstream", str(upstream)])
            config = tomllib.loads((home / "config.toml").read_text())
            self.assertEqual(config, {"thread_unload_delay_secs": 0})
            self.assertFalse((home / "prompts/sara-system.md").exists())

    def test_existing_user_instructions_and_memory_settings_are_retained(self):
        install = load("existing_launcher_install", ROOT / "install.py")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            (home / "prompts").mkdir(parents=True)
            (home / "memories").mkdir()
            files = {
                "AGENTS.md": "Personal AGENTS instructions.\n",
                "MACHINE.md": "Personal machine instructions.\n",
                "prompts/private.md": "An explicitly configured user prompt.\n",
                "memories/note.md": "A personal memory.\n",
            }
            for name, text in files.items():
                (home / name).write_text(text)
            config = ('model = "example"\nmodel_instructions_file = "prompts/private.md"\n'
                      'developer_instructions = "User-owned instruction"\n'
                      '[memories]\nuse_memories = true\n')
            (home / "config.toml").write_text(config)
            upstream = root / "official.cjs"
            upstream.write_text("process.exit(0);\n")
            install.main(["--home", str(home), "--bin-dir", str(root / "bin"),
                          "--upstream", str(upstream)])
            expected = tomllib.loads(config)
            expected["thread_unload_delay_secs"] = 0
            self.assertEqual(tomllib.loads((home / "config.toml").read_text()), expected)
            for name, text in files.items():
                self.assertEqual((home / name).read_text(), text)
            self.assertFalse((home / "prompts/sara-system.md").exists())

    def test_backend_idle_checker_can_still_import_its_rpc_client(self):
        rpc = load("retained_rpc_helper", ROOT / "prompts/refresh-loaded-chats.py")
        self.assertTrue(callable(rpc.Client))


if __name__ == "__main__":
    unittest.main()
