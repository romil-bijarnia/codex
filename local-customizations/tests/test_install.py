import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("local_install", ROOT/"install.py")
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)

class InstallTests(unittest.TestCase):
    def test_adds_only_idle_setting(self):
        text='model = "example"\n[features]\nexample = true\n'
        result=install.shared_config(text)
        self.assertEqual(tomllib.loads(result)["thread_unload_delay_secs"], 0)
        self.assertTrue(result.endswith(text))

    def test_replaces_existing_setting_without_duplicates(self):
        text='thread_unload_delay_secs = 60 # old value\nmodel = "example"\n'
        result=install.shared_config(text)
        self.assertEqual(result.count("thread_unload_delay_secs"), 1)
        self.assertEqual(tomllib.loads(result)["thread_unload_delay_secs"], 0)

    def test_install_keeps_private_files_and_backups(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            home=root/"home"
            home.mkdir()
            config='model = "example"\n'
            (home/"config.toml").write_text(config)
            (home/"AGENTS.md").write_text("Keep this personal file.")
            upstream=root/"official.cjs"
            upstream.write_text("process.stdout.write(JSON.stringify(process.argv.slice(2)));\n")
            bin_dir=root/"bin"
            install.main(["--home",str(home),"--bin-dir",str(bin_dir),"--upstream",str(upstream)])
            self.assertTrue((bin_dir/"codex").is_symlink())
            self.assertEqual((home/"AGENTS.md").read_text(),"Keep this personal file.")
            self.assertEqual(tomllib.loads((home/"config.toml").read_text())["model"],"example")
            self.assertEqual(next((home/"backups").glob("*/config.toml")).read_text(),config)
            for arguments in (
                ["debug", "prompt-input"],
                ["debug", "prompt-input", "-c", 'model_instructions_file="custom.md"'],
            ):
                result=subprocess.run(
                    ["node", str(bin_dir/"codex"), *arguments],
                    capture_output=True, text=True, check=True,
                )
                self.assertEqual(
                    json.loads(result.stdout),
                    ["-c", "thread_unload_delay_secs=0", *arguments],
                )
            self.assertNotIn("selectThreadReleaseFix",(bin_dir/"codex").resolve().read_text())

if __name__=="__main__":
    unittest.main()
